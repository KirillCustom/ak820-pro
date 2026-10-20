//! One frame on the lock lights, and the restore after it: the executor of
//! `plans/LOCK-LED-CHANNEL-PLAN.md`'s sender design, revisions 2-4 (R2-1/R3-1
//! the restore, R3-6 the phase deadlines, R3-5/R4-5 the snapshot frozen before
//! the first write, R4-1 the user's locks known or not).
//!
//! Platform-neutral and synchronous: it runs on the sender's own thread and
//! is tested on a fake clock and a fake keyboard. Every wait goes through
//! [`Time::sleep_until`], which the thread wraps around its stop flag, and
//! before every write the caller's [`Control::abort`] is asked whether the
//! frame is still wanted (a newer content, a revoked owner, shutdown).

use std::time::{Duration, Instant};

use super::{encode, Profile};

/// The platform's half (Windows: the class driver's IOCTL; macOS:
/// `IOHIDDeviceSetReport`).
pub trait LockLights {
    fn profile(&self) -> Profile;
    /// The least time from one write's completion to the next write.
    fn spacing(&self) -> Duration;
    /// The user's real Num/Caps/Scroll as HID bits, read now; `None` when
    /// they cannot be known (R4-1), which stops the channel.
    fn user(&mut self) -> Option<u8>;
    /// One LED byte; returns once the platform has accepted it.
    fn write(&mut self, leds: u8) -> Result<(), WriteError>;
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum WriteError {
    /// The write failed and is retired: the handle may be reopened.
    Failed(String),
    /// The write did not retire in its deadline: the channel is poisoned for
    /// the life of the process (R3-3).
    Stuck,
}

/// Waiting, interruptibly.
pub trait Time {
    fn now(&self) -> Instant;
    /// Sleep until `t`, or less if stopped; false when stopped.
    fn sleep_until(&self, t: Instant) -> bool;
    /// Sleep until `t` whatever the stop flag says: the shutdown's one
    /// bounded restore (R4-4; codex, implementation review, 8).
    fn sleep_through(&self, t: Instant);
}

/// Whether the frame is still wanted, asked before every write.
pub trait Control {
    fn abort(&self) -> Option<Abort>;
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Abort {
    /// A newer content superseded this one (R3-5): not a failure.
    Superseded,
    /// The channel's owner generation was revoked: USB or the radio (R2-5).
    Revoked,
    /// The daemon is stopping.
    Stop,
}

/// The receiver's limits as the sender's deadlines, with margins (R3-6).
pub const RECOVERY: Duration = Duration::from_millis(600);
pub const QUIET: Duration = Duration::from_millis(300);
pub const PREAMBLE_GAP: Duration = Duration::from_millis(100);
pub const DATA_GAP: Duration = Duration::from_millis(300);
pub const FRAME_LIMIT: Duration = Duration::from_secs(10);
/// Repairs a restore makes before `restore_pending` (R3-1).
pub const RESTORE_TRIES: usize = 3;

/// What the channel carries from one frame to the next.
#[derive(Debug, Default)]
pub struct Line {
    /// When our last write completed; `None` is unknown history, which waits
    /// the full recovery (R3-6).
    pub last_completion: Option<Instant>,
    /// A restore that has not been confirmed: no payload goes until one is
    /// (R3-1, R4-7).
    pub restore_pending: bool,
    /// Writes made, for the status file and the tests.
    pub writes: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Outcome {
    /// Every write of the frame went, and the lights read back the user's.
    Sent,
    /// The frame was given up; the lights were restored (or are pending).
    Abandoned(Why),
    /// Nothing was written: the user's locks are unknown.
    Unknown,
    /// A write is stuck: stop the channel for good.
    Poisoned,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Why {
    Abort(Abort),
    /// The user's locks changed from the frame's baseline.
    UserChanged,
    /// A phase deadline passed (a slow write, a suspension).
    Deadline,
    Write(String),
    /// The restore before the frame did not confirm.
    RestorePending,
}

/// What [`run`] sends, chosen after the warm-up and the quiet (R4-5).
pub struct Payload {
    pub kind: u8,
    pub seq: u8,
    pub bytes: Vec<u8>,
}

fn warm_bit(p: Profile) -> u8 {
    if p == Profile::P0 { 0x01 } else { 0x08 }
}

struct Run<'a, L: LockLights, T: Time, C: Control> {
    lights: &'a mut L,
    time: &'a T,
    control: &'a C,
    line: &'a mut Line,
    /// Stopping: waits ignore the stop flag, for the one bounded restore.
    stopping: bool,
}

impl<L: LockLights, T: Time, C: Control> Run<'_, L, T, C> {
    fn wait(&self, t: Instant) -> Result<(), Why> {
        if self.stopping {
            self.time.sleep_through(t);
        } else if !self.time.sleep_until(t) {
            return Err(Why::Abort(Abort::Stop));
        }
        Ok(())
    }

    /// One write, spaced from the previous completion, its deadline checked
    /// against that saved completion both before submitting and on its own
    /// completion (codex r3, 6). With `guard`, the frame's baseline: whether
    /// the frame is still wanted and the user's locks unchanged are asked
    /// **after** the spacing wait, right before submitting (codex,
    /// implementation review, 3).
    fn write(&mut self, v: u8, limit: Option<Duration>, guard: Option<u8>) -> Result<(), Why> {
        let prev = self.line.last_completion;
        if let Some(p) = prev {
            self.wait(p + self.lights.spacing())?;
            if limit.is_some_and(|l| self.time.now() - p > l) {
                return Err(Why::Deadline);
            }
        }
        if let Some(u) = guard {
            self.checked(u)?;
        }
        match self.lights.write(v) {
            Ok(()) => {}
            Err(WriteError::Failed(e)) => {
                // Retired, but it may have gone out: the recovery wait counts
                // from now, not from the last success (implementation review, 2).
                self.line.last_completion = Some(self.time.now());
                return Err(Why::Write(e));
            }
            Err(WriteError::Stuck) => return Err(Why::Write(STUCK.into())),
        }
        let done = self.time.now();
        self.line.writes += 1;
        self.line.last_completion = Some(done);
        if let (Some(p), Some(l)) = (prev, limit) {
            if done - p > l {
                return Err(Why::Deadline);
            }
        }
        Ok(())
    }

    fn recovery_wait(&self) -> Result<(), Why> {
        let from = self.line.last_completion.unwrap_or_else(|| self.time.now());
        self.wait(from + RECOVERY)
    }

    /// Fresh locks written, read back, at most [`RESTORE_TRIES`] times (R3-1).
    /// True when a read after a write agrees with it.
    fn restore(&mut self) -> Result<bool, Why> {
        for _ in 0..RESTORE_TRIES {
            // The spacing first, then the locks read: what is written is
            // what they are now, not before the wait (verify, 2).
            if let Some(p) = self.line.last_completion {
                self.wait(p + self.lights.spacing())?;
            }
            let Some(u) = self.lights.user() else { return Ok(false) };
            self.write(u, None, None)?;
            if self.lights.user() == Some(u) {
                return Ok(true);
            }
        }
        Ok(false)
    }

    fn checked(&mut self, u: u8) -> Result<(), Why> {
        if let Some(a) = self.control.abort() {
            return Err(Why::Abort(a));
        }
        match self.lights.user() {
            Some(now) if now == u => Ok(()),
            _ => Err(Why::UserChanged),
        }
    }
}

const STUCK: &str = "stuck";

/// One frame: the restore if one is pending, the recovery wait, the warm-up,
/// the quiet, then `pick` chooses what to send (it may return `None`: nothing
/// is due any more), then every write; on any abandon, the recovery wait and
/// a restore.
pub fn run<L: LockLights, T: Time, C: Control>(
    lights: &mut L,
    time: &T,
    control: &C,
    line: &mut Line,
    pick: impl FnOnce() -> Option<Payload>,
) -> Outcome {
    let mut r = Run { lights, time, control, line, stopping: false };
    if r.lights.user().is_none() {
        return Outcome::Unknown;
    }
    match frame(&mut r, pick) {
        Ok(true) => Outcome::Sent,
        // Nothing due once the quiet was done: only the warm-up went, which
        // ended on the user's state.
        Ok(false) => Outcome::Abandoned(Why::Abort(Abort::Superseded)),
        Err(Why::Write(e)) if e == STUCK => Outcome::Poisoned,
        Err(Why::RestorePending) => Outcome::Abandoned(Why::RestorePending),
        Err(why) => {
            if r.lights.user().is_none() {
                // Unknown, not changed: nothing can be restored until known.
                r.line.restore_pending = true;
                return Outcome::Abandoned(why);
            }
            // Shutdown makes one bounded attempt, its waits through the stop
            // flag, within the exit budget the thread's owner enforces (R4-4).
            r.stopping = why == Why::Abort(Abort::Stop);
            let restored = r.recovery_wait().and_then(|_| r.restore());
            match restored {
                Ok(ok) => r.line.restore_pending = !ok,
                Err(Why::Write(e)) if e == STUCK => return Outcome::Poisoned,
                Err(_) => r.line.restore_pending = true,
            }
            Outcome::Abandoned(why)
        }
    }
}

/// A restore alone, for `restore_pending` while no frame is due or the
/// channel is revoked (R3-1; implementation review, 1): the recovery wait,
/// then the repairs. True when confirmed.
pub fn restore_only<L: LockLights, T: Time, C: Control>(
    lights: &mut L,
    time: &T,
    control: &C,
    line: &mut Line,
    stopping: bool,
) -> Result<bool, Outcome> {
    // Stopping: the waits go through the stop flag, the one bounded attempt
    // a shutdown makes (verify, 1).
    let mut r = Run { lights, time, control, line, stopping };
    match r.recovery_wait().and_then(|_| r.restore()) {
        Ok(ok) => {
            r.line.restore_pending = !ok;
            Ok(ok)
        }
        Err(Why::Write(e)) if e == STUCK => Err(Outcome::Poisoned),
        Err(why) => Err(Outcome::Abandoned(why)),
    }
}

fn frame<L: LockLights, T: Time, C: Control>(
    r: &mut Run<L, T, C>,
    pick: impl FnOnce() -> Option<Payload>,
) -> Result<bool, Why> {
    r.recovery_wait()?;
    if r.line.restore_pending {
        if !r.restore()? {
            return Err(Why::RestorePending);
        }
        r.line.restore_pending = false;
        r.recovery_wait()?;
    }
    let Some(u) = r.lights.user() else { return Err(Why::UserChanged) };
    let p = r.lights.profile();

    // The warm-up: one change and back, so the board has the baseline.
    r.write(u ^ warm_bit(p), None, Some(u))?;
    r.write(u, Some(DATA_GAP), Some(u))?;
    // The quiet.
    let quiet_from = r.line.last_completion.expect("just written");
    r.wait(quiet_from + QUIET)?;

    // Now, and not before, what to send (R4-5): frozen for this attempt.
    let Some(pay) = pick() else { return Ok(false) };
    r.checked(u)?;
    let writes = encode(&pay.bytes, pay.kind, pay.seq, p, u).map_err(|e| Why::Write(format!("{e:?}")))?;
    let start = r.time.now();
    for (i, &v) in writes.iter().enumerate() {
        if r.time.now() - start > FRAME_LIMIT {
            return Err(Why::Deadline);
        }
        let limit = match i {
            0 => None, // after the quiet, which is longer by design
            1 | 2 => Some(PREAMBLE_GAP),
            _ => Some(DATA_GAP),
        };
        r.write(v, limit, Some(u))?;
    }
    // The whole frame's limit, on completion too (implementation review, 12).
    if r.time.now() - start > FRAME_LIMIT {
        return Err(Why::Deadline);
    }
    // The trailer ended on `u`; check the user's lights are still theirs.
    if r.lights.user() != Some(u) {
        let restored = r.recovery_wait().and_then(|_| r.restore())?;
        r.line.restore_pending = !restored;
    }
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::{Cell, RefCell};

    struct FakeTime(Cell<Instant>);
    impl Time for FakeTime {
        fn now(&self) -> Instant {
            self.0.get()
        }
        fn sleep_until(&self, t: Instant) -> bool {
            if t > self.0.get() {
                self.0.set(t);
            }
            true
        }
        fn sleep_through(&self, t: Instant) {
            self.sleep_until(t);
        }
    }

    /// A keyboard whose writes take `cost`, whose user locks can change at a
    /// given write, and whose write can fail at a given index.
    struct Fake<'t> {
        time: &'t FakeTime,
        cost: Duration,
        user: u8,
        writes: Vec<(Instant, u8)>,
        change_at: Option<(usize, u8)>,
        fail_at: Option<(usize, WriteError)>,
        slow_at: Option<(usize, Duration)>,
        unknown: bool,
    }
    impl<'t> Fake<'t> {
        fn new(time: &'t FakeTime) -> Self {
            Fake { time, cost: Duration::from_millis(1), user: 0x02, writes: vec![], change_at: None, fail_at: None, slow_at: None, unknown: false }
        }
    }
    impl LockLights for Fake<'_> {
        fn profile(&self) -> Profile {
            Profile::P0
        }
        fn spacing(&self) -> Duration {
            Duration::from_millis(16)
        }
        fn user(&mut self) -> Option<u8> {
            (!self.unknown).then_some(self.user)
        }
        fn write(&mut self, v: u8) -> Result<(), WriteError> {
            let i = self.writes.len();
            if let Some((at, e)) = &self.fail_at {
                if *at == i {
                    // A failure that retires after the IOCTL's deadline, once.
                    let e = e.clone();
                    self.fail_at = None;
                    self.time.0.set(self.time.0.get() + Duration::from_secs(1));
                    return Err(e);
                }
            }
            let cost = match self.slow_at {
                Some((at, d)) if at == i => d,
                _ => self.cost,
            };
            self.time.0.set(self.time.0.get() + cost);
            self.writes.push((self.time.0.get(), v));
            if let Some((at, u)) = self.change_at {
                if at == i {
                    self.user = u;
                }
            }
            Ok(())
        }
    }

    struct Go(RefCell<Option<Abort>>);
    impl Control for Go {
        fn abort(&self) -> Option<Abort> {
            *self.0.borrow()
        }
    }
    fn go() -> Go {
        Go(RefCell::new(None))
    }

    fn payload() -> Option<Payload> {
        Some(Payload { kind: 1, seq: 4, bytes: b"\x01\x05HelloWorld".to_vec() })
    }

    #[test]
    fn a_frame_is_warm_up_quiet_and_the_encoders_writes_spaced_from_completion() {
        let t = FakeTime(Cell::new(Instant::now()));
        let mut k = Fake::new(&t);
        let mut line = Line::default();
        let start = t.now();
        assert_eq!(run(&mut k, &t, &go(), &mut line, payload), Outcome::Sent);
        let expect = encode(b"\x01\x05HelloWorld", 1, 4, Profile::P0, 0x02).unwrap();
        let vals: Vec<u8> = k.writes.iter().map(|w| w.1).collect();
        assert_eq!(vals[..2], [0x03, 0x02], "the warm-up on Num, Caps kept");
        assert_eq!(vals[2..], expect[..]);
        // Unknown history: the full recovery before the first write.
        assert!(k.writes[0].0 - start >= RECOVERY);
        // The quiet after the warm-up.
        assert!(k.writes[2].0 - k.writes[1].0 >= QUIET);
        // Spacing from completion: every later write at least 16 ms + its cost after the previous.
        for w in k.writes[2..].windows(2) {
            assert!(w[1].0 - w[0].0 >= Duration::from_millis(17));
        }
        assert_eq!(line.writes as usize, k.writes.len());
        assert!(!line.restore_pending);
    }

    #[test]
    fn a_user_change_mid_frame_abandons_waits_out_the_receiver_and_restores_the_new_state() {
        let t = FakeTime(Cell::new(Instant::now()));
        let mut k = Fake::new(&t);
        k.change_at = Some((20, 0x06)); // Scroll on, during the payload
        let mut line = Line::default();
        let out = run(&mut k, &t, &go(), &mut line, payload);
        assert_eq!(out, Outcome::Abandoned(Why::UserChanged));
        let n = k.writes.len();
        assert_eq!(n, 22, "no write after the change but the restore");
        assert_eq!(k.writes[n - 1].1, 0x06, "the user's new state");
        assert!(k.writes[n - 1].0 - k.writes[n - 2].0 >= RECOVERY);
        assert!(!line.restore_pending);
    }

    #[test]
    fn a_change_at_every_write_boundary_ends_on_the_users_state() {
        let t0 = Instant::now();
        let len = {
            let t = FakeTime(Cell::new(t0));
            let mut k = Fake::new(&t);
            run(&mut k, &t, &go(), &mut Line::default(), payload);
            k.writes.len()
        };
        for at in 0..len {
            let t = FakeTime(Cell::new(t0));
            let mut k = Fake::new(&t);
            k.change_at = Some((at, 0x07));
            let mut line = Line::default();
            run(&mut k, &t, &go(), &mut line, payload);
            assert_eq!(k.writes.last().unwrap().1, 0x07, "change after write {at}");
            assert!(!line.restore_pending, "change after write {at}");
        }
    }

    #[test]
    fn superseded_and_revoked_abandon_and_restore_stop_restores_once() {
        for a in [Abort::Superseded, Abort::Revoked, Abort::Stop] {
            let t = FakeTime(Cell::new(Instant::now()));
            let mut k = Fake::new(&t);
            let c = go();
            let mut line = Line::default();
            // Abort as soon as the payload is picked.
            let out = run(&mut k, &t, &c, &mut line, || {
                *c.0.borrow_mut() = Some(a);
                payload()
            });
            assert_eq!(out, Outcome::Abandoned(Why::Abort(a)));
            assert_eq!(k.writes.last().unwrap().1, 0x02, "{a:?}");
        }
    }

    #[test]
    fn nothing_to_pick_sends_nothing_after_the_warm_up() {
        let t = FakeTime(Cell::new(Instant::now()));
        let mut k = Fake::new(&t);
        let out = run(&mut k, &t, &go(), &mut Line::default(), || None);
        assert_eq!(out, Outcome::Abandoned(Why::Abort(Abort::Superseded)));
        assert_eq!(k.writes.iter().map(|w| w.1).collect::<Vec<_>>(), [0x03, 0x02]);
    }

    #[test]
    fn unknown_locks_write_nothing() {
        let t = FakeTime(Cell::new(Instant::now()));
        let mut k = Fake::new(&t);
        k.unknown = true;
        let mut line = Line::default();
        assert_eq!(run(&mut k, &t, &go(), &mut line, payload), Outcome::Unknown);
        assert!(k.writes.is_empty());
        assert!(!line.restore_pending, "nothing was written, nothing to restore");
    }

    #[test]
    fn locks_becoming_unknown_mid_frame_leave_a_restore_pending() {
        struct Goes<'t>(Fake<'t>);
        impl LockLights for Goes<'_> {
            fn profile(&self) -> Profile { Profile::P0 }
            fn spacing(&self) -> Duration { Duration::from_millis(16) }
            fn user(&mut self) -> Option<u8> { (self.0.writes.len() < 12).then_some(0x02) }
            fn write(&mut self, v: u8) -> Result<(), WriteError> { self.0.write(v) }
        }
        let t = FakeTime(Cell::new(Instant::now()));
        let mut k = Goes(Fake::new(&t));
        let mut line = Line::default();
        assert_eq!(run(&mut k, &t, &go(), &mut line, payload), Outcome::Abandoned(Why::UserChanged));
        assert_eq!(k.0.writes.len(), 12, "no restore while unknown");
        assert!(line.restore_pending);
    }

    #[test]
    fn a_slow_preamble_write_misses_its_100_ms_and_abandons() {
        let t = FakeTime(Cell::new(Instant::now()));
        let mut k = Fake::new(&t);
        k.slow_at = Some((3, Duration::from_millis(120))); // the second preamble write
        let out = run(&mut k, &t, &go(), &mut Line::default(), payload);
        assert_eq!(out, Outcome::Abandoned(Why::Deadline));
        assert_eq!(k.writes.last().unwrap().1, 0x02);
    }

    #[test]
    fn a_slow_data_write_misses_its_300_ms() {
        let t = FakeTime(Cell::new(Instant::now()));
        let mut k = Fake::new(&t);
        k.slow_at = Some((30, Duration::from_millis(320)));
        assert_eq!(run(&mut k, &t, &go(), &mut Line::default(), payload), Outcome::Abandoned(Why::Deadline));
    }

    #[test]
    fn a_failed_write_waits_the_recovery_from_its_own_end_before_the_restore() {
        let t = FakeTime(Cell::new(Instant::now()));
        let mut k = Fake::new(&t);
        k.fail_at = Some((10, WriteError::Failed("timed out (cancelled)".into())));
        let mut line = Line::default();
        let out = run(&mut k, &t, &go(), &mut line, payload);
        assert_eq!(out, Outcome::Abandoned(Why::Write("timed out (cancelled)".into())));
        let w = &k.writes;
        assert_eq!(w.len(), 11, "ten, then the restore");
        // The failure took 1 s after write 9; the restore waits 600 ms after that.
        assert!(w[10].0 - w[9].0 >= Duration::from_secs(1) + RECOVERY, "{:?}", w[10].0 - w[9].0);
        assert!(!line.restore_pending);
    }

    #[test]
    fn a_change_during_the_spacing_wait_stops_the_next_write() {
        // The user's lock changes while the executor waits out the spacing:
        // the check after the wait sees it and nothing more of the frame goes.
        struct Mid<'t> {
            k: Fake<'t>,
            changed_at: Instant,
        }
        impl LockLights for Mid<'_> {
            fn profile(&self) -> Profile { Profile::P0 }
            fn spacing(&self) -> Duration { Duration::from_millis(16) }
            fn user(&mut self) -> Option<u8> {
                Some(if self.k.time.now() >= self.changed_at { 0x06 } else { 0x02 })
            }
            fn write(&mut self, v: u8) -> Result<(), WriteError> { self.k.write(v) }
        }
        let t = FakeTime(Cell::new(Instant::now()));
        let start = t.now();
        // RECOVERY, warm-up, QUIET, then about 15 payload writes in.
        let changed_at = start + RECOVERY + QUIET + Duration::from_millis(20 + 15 * 17 + 8);
        let mut k = Mid { k: Fake::new(&t), changed_at };
        let out = run(&mut k, &t, &go(), &mut Line::default(), payload);
        assert_eq!(out, Outcome::Abandoned(Why::UserChanged));
        let w = &k.k.writes;
        // Every write submitted after the change is the restore: none of the
        // frame's (verify: the old code wrote one more, 8 ms after it).
        let after: Vec<_> = w.iter().filter(|x| x.0 > changed_at).collect();
        assert_eq!(after.len(), 1, "only the restore after the change: {:?}", after);
        let last_of_frame = w.iter().filter(|x| x.0 <= changed_at).last().unwrap().0;
        assert!(after[0].0 - last_of_frame >= RECOVERY);
        assert_eq!(w.last().unwrap().1, 0x06);
    }

    #[test]
    fn a_failed_write_abandons_and_a_stuck_one_poisons() {
        let t = FakeTime(Cell::new(Instant::now()));
        let mut k = Fake::new(&t);
        k.fail_at = Some((10, WriteError::Failed("gone".into())));
        let mut line = Line::default();
        assert_eq!(run(&mut k, &t, &go(), &mut line, payload), Outcome::Abandoned(Why::Write("gone".into())));

        let t = FakeTime(Cell::new(Instant::now()));
        let mut k = Fake::new(&t);
        k.fail_at = Some((10, WriteError::Stuck));
        assert_eq!(run(&mut k, &t, &go(), &mut Line::default(), payload), Outcome::Poisoned);
        assert_eq!(k.writes.len(), 10, "nothing after the stuck write");
    }

    #[test]
    fn a_pending_restore_goes_first_and_blocks_the_payload_until_it_confirms() {
        let t = FakeTime(Cell::new(Instant::now()));
        let mut k = Fake::new(&t);
        let mut line = Line { restore_pending: true, ..Line::default() };
        assert_eq!(run(&mut k, &t, &go(), &mut line, payload), Outcome::Sent);
        assert_eq!(k.writes[0].1, 0x02, "the restore first");
        assert!(!line.restore_pending);

        // A restore that never reads back (the user keeps changing) stays pending.
        struct Flicker<'t>(Fake<'t>, Cell<u8>);
        impl LockLights for Flicker<'_> {
            fn profile(&self) -> Profile { Profile::P0 }
            fn spacing(&self) -> Duration { Duration::from_millis(16) }
            fn user(&mut self) -> Option<u8> { let v = self.1.get() ^ 0x04; self.1.set(v); Some(v) }
            fn write(&mut self, v: u8) -> Result<(), WriteError> { self.0.write(v) }
        }
        let t = FakeTime(Cell::new(Instant::now()));
        let mut k = Flicker(Fake::new(&t), Cell::new(0));
        let mut line = Line { restore_pending: true, ..Line::default() };
        let out = run(&mut k, &t, &go(), &mut line, payload);
        assert_eq!(out, Outcome::Abandoned(Why::RestorePending));
        assert_eq!(k.0.writes.len(), RESTORE_TRIES, "three repairs, no payload");
        assert!(line.restore_pending);
    }

    #[test]
    fn a_known_last_completion_is_waited_from_not_now() {
        let t = FakeTime(Cell::new(Instant::now()));
        let mut k = Fake::new(&t);
        let mut line = Line { last_completion: Some(t.now() - Duration::from_secs(5)), ..Line::default() };
        let start = t.now();
        run(&mut k, &t, &go(), &mut line, payload);
        assert!(k.writes[0].0 - start < Duration::from_millis(5), "idle time already past counts");
    }
}
