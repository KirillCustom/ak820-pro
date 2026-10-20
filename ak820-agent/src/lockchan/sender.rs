//! The lock-LED channel's sender: a thread of its own, fed by the daemon's
//! loop (`plans/LOCK-LED-CHANNEL-PLAN.md`, the sender design and R2-R4).
//!
//! The loop calls [`Handle::set`] with the content each poll, [`Handle::grant`]
//! and [`Handle::revoke`] as the transports change, [`Handle::force`] for a
//! resend reason, and reads [`Handle::status`]. Everything that waits or
//! writes happens on the thread, in [`Core::step`], which is synchronous and
//! tested on a fake clock: the thread is only its loop.
//!
//! Locks: the slot's mutex is never held across a write or a wait (R3-3); the
//! machine-wide owner lock is taken on a grant and held through frames,
//! restores and `restore_pending`, released only with nothing outstanding, and
//! **kept for the life of the process if a write is stuck**.

use std::any::Any;
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

use super::frame::{self, Abort, Control, Line, LockLights, Outcome, Payload, Time};
use super::policy::{Content, Policy};

/// Past this without a fresh observation the content becomes CLEAR (R3-7).
pub const STALE_AFTER: Duration = Duration::from_secs(30);
/// The handover's wait after the daemon's last raw-HID media request (R4-2).
pub const HANDOVER: Duration = Duration::from_secs(12);
/// Absent keyboard: the list is read again this often (R3-8).
pub const RELIST: Duration = Duration::from_secs(10);
/// The wake guess: armed past this much input idleness (R3-8, R4).
pub const IDLE_ARM: Duration = Duration::from_secs(30);

/// What the thread needs from the platform, beyond the lights themselves.
pub trait Platform {
    type Lights: LockLights;
    /// The board's keyboard: `Ok(None)` when not listed.
    fn open(&mut self) -> Result<Option<Self::Lights>, String>;
    /// The machine-wide owner lock: `Ok(None)` when another process holds it.
    fn lock(&mut self) -> Result<Option<Box<dyn Any + Send>>, String>;
    /// The last input's tick (`GetLastInputInfo`), and the tick now, if knowable.
    fn input(&self) -> Option<(u32, u32)>;
}

/// The loop's side of the slot.
#[derive(Debug, Default, Clone)]
pub struct Status {
    pub state: &'static str,
    pub frames: u64,
    pub failures: u64,
    pub last_sent: Option<Instant>,
    pub last_error: Option<String>,
    pub restore_pending: bool,
    pub writes: u64,
}

#[derive(Default)]
struct Slot {
    content: Option<Content>,
    observed: Option<Instant>,
    granted: bool,
    owner_gen: u64,
    force: bool,
    not_before: Option<Instant>,
    stop: bool,
    status: Status,
}

struct Shared {
    slot: Mutex<Slot>,
    cv: Condvar,
}

/// The daemon loop's handle on the sender.
#[derive(Clone)]
pub struct Handle(Arc<Shared>);

impl Handle {
    pub fn new() -> Handle {
        Handle(Arc::new(Shared { slot: Mutex::new(Slot::default()), cv: Condvar::new() }))
    }

    fn with<R>(&self, f: impl FnOnce(&mut Slot) -> R) -> R {
        let mut s = self.0.slot.lock().unwrap_or_else(|e| e.into_inner());
        let r = f(&mut s);
        self.0.cv.notify_all();
        r
    }

    /// The content now, and when the media source last observed it.
    pub fn set(&self, content: Content, observed: Instant) {
        self.with(|s| {
            s.content = Some(content);
            s.observed = Some(observed);
        });
    }

    /// The channel may send: USB is absent and the radio is not carrying.
    /// `raw_media_last` is the daemon's last raw-HID media completion, if
    /// any; with none in this process (startup), the full wait (R4-2).
    pub fn grant(&self, raw_media_last: Option<Instant>, now: Instant) {
        self.with(|s| {
            if !s.granted {
                s.granted = true;
                s.owner_gen += 1;
                s.force = true;
                s.not_before = Some(raw_media_last.unwrap_or(now) + HANDOVER);
            }
        });
    }

    /// USB or the radio has it: stop between writes (R2-5).
    pub fn revoke(&self) {
        self.with(|s| {
            if s.granted {
                s.granted = false;
                s.owner_gen += 1;
            }
        });
    }

    pub fn force(&self) {
        self.with(|s| s.force = true);
    }

    pub fn stop(&self) {
        self.with(|s| s.stop = true);
    }

    pub fn status(&self) -> Status {
        self.with(|s| s.status.clone())
    }

    pub fn granted(&self) -> bool {
        self.with(|s| s.granted)
    }
}

impl Default for Handle {
    fn default() -> Self {
        Handle::new()
    }
}

/// The thread's state: everything [`Core::step`] carries between calls.
pub struct Core<P: Platform> {
    platform: P,
    lights: Option<P::Lights>,
    guard: Option<Box<dyn Any + Send>>,
    owner_seen: u64,
    policy: Policy,
    line: Line,
    next_open: Option<Instant>,
    armed: Option<u32>,
    poisoned: bool,
    seq: u8,
}

/// What the thread does after a step.
#[derive(Debug, PartialEq, Eq)]
pub enum Next {
    /// Sleep until this instant, or a change in the slot.
    WaitUntil(Instant),
    /// Sleep until the slot changes.
    Wait,
    /// The thread ends.
    Exit,
}

struct Ctl<'a> {
    h: &'a Handle,
    owner: u64,
    gen: u64,
    policy_gen: &'a dyn Fn() -> u64,
}

impl Control for Ctl<'_> {
    fn abort(&self) -> Option<Abort> {
        // The slot's lock is released before policy_gen, which takes it too.
        let (stop, owned) = {
            let s = self.h.0.slot.lock().unwrap_or_else(|e| e.into_inner());
            (s.stop, s.granted && s.owner_gen == self.owner)
        };
        if stop {
            Some(Abort::Stop)
        } else if !owned {
            Some(Abort::Revoked)
        } else if (self.policy_gen)() != self.gen {
            Some(Abort::Superseded)
        } else {
            None
        }
    }
}

impl<P: Platform> Core<P> {
    pub fn new(platform: P) -> Core<P> {
        Core {
            platform,
            lights: None,
            guard: None,
            owner_seen: 0,
            policy: Policy::default(),
            line: Line::default(),
            next_open: None,
            armed: None,
            poisoned: false,
            seq: 0,
        }
    }

    /// The content the slot asks for, made CLEAR when its observation is stale.
    fn current(slot: &Slot, now: Instant) -> Option<Content> {
        let c = slot.content.clone()?;
        match slot.observed {
            Some(o) if now.saturating_duration_since(o) <= STALE_AFTER => Some(c),
            _ => Some((super::TYPE_CLEAR, Vec::new())),
        }
    }

    /// The wake guess: armed after IDLE_ARM of input idleness, fired on any
    /// change of the last input's tick, forwards or backwards.
    fn wake_guess(&mut self) -> bool {
        let Some((last, now)) = self.platform.input() else { return false };
        match self.armed {
            Some(a) if a != last => {
                self.armed = None;
                true
            }
            Some(_) => false,
            None => {
                if Duration::from_millis(now.wrapping_sub(last) as u64) >= IDLE_ARM {
                    self.armed = Some(last);
                }
                false
            }
        }
    }

    /// One pass: update from the slot, and send a frame if one is due.
    pub fn step<T: Time>(&mut self, h: &Handle, time: &T) -> Next {
        if self.poisoned {
            return Next::Exit;
        }
        let now = time.now();
        let woke = self.wake_guess();
        let (granted, owner, stop) = {
            let mut s = h.0.slot.lock().unwrap_or_else(|e| e.into_inner());
            if let Some(c) = Self::current(&s, now) {
                self.policy.set(c, now);
            }
            if std::mem::take(&mut s.force) || woke {
                self.policy.force(now);
            }
            if let Some(nb) = s.not_before.take() {
                self.policy.not_before(nb);
            }
            s.status.restore_pending = self.line.restore_pending;
            s.status.writes = self.line.writes;
            (s.granted, s.owner_gen, s.stop)
        };
        if stop {
            self.release();
            return Next::Exit;
        }
        if !granted {
            // Revoked: nothing outstanding here (frames run inside step), so
            // the lock and the handle go, unless a restore is still owed.
            if !self.line.restore_pending {
                self.release();
            }
            self.set_state(h, "off");
            return Next::Wait;
        }
        if owner != self.owner_seen {
            self.owner_seen = owner;
        }
        let Some(due) = self.policy.due() else {
            self.set_state(h, "idle");
            return Next::Wait;
        };
        if due > now {
            return Next::WaitUntil(due);
        }
        if self.guard.is_none() {
            match self.platform.lock() {
                Ok(Some(g)) => self.guard = Some(g),
                Ok(None) => {
                    self.set_error(h, "another process holds the lock-LED lock".into());
                    return Next::WaitUntil(now + RELIST);
                }
                Err(e) => {
                    self.set_error(h, e);
                    return Next::WaitUntil(now + RELIST);
                }
            }
        }
        if self.lights.is_none() {
            if let Some(t) = self.next_open {
                if t > now {
                    return Next::WaitUntil(t);
                }
            }
            match self.platform.open() {
                Ok(Some(l)) => {
                    self.lights = Some(l);
                    self.next_open = None;
                }
                Ok(None) => {
                    self.set_state(h, "absent");
                    self.next_open = Some(now + RELIST);
                    return Next::WaitUntil(now + RELIST);
                }
                Err(e) => {
                    self.set_error(h, e);
                    self.policy.failed(self.policy.generation(), now);
                    self.next_open = self.policy.due();
                    return self.policy.due().map_or(Next::Wait, Next::WaitUntil);
                }
            }
        }

        let gen = self.policy.generation();
        let Some(content) = self.policy.content(gen).cloned() else { return Next::Wait };
        self.set_state(h, "sending");
        let seq = self.seq;
        let policy_gen = || {
            // A newer content in the slot supersedes this frame (R3-5).
            let s = h.0.slot.lock().unwrap_or_else(|e| e.into_inner());
            let fresh = Self::current(&s, time.now());
            if fresh.as_ref() == Some(&content) { gen } else { gen + 1 }
        };
        let ctl = Ctl { h, owner, gen, policy_gen: &policy_gen };
        let lights = self.lights.as_mut().expect("opened above");
        let out = frame::run(lights, time, &ctl, &mut self.line, || {
            Some(Payload { kind: content.0, seq, bytes: content.1.clone() })
        });
        let done = time.now();
        match out {
            Outcome::Sent => {
                self.seq = self.seq.wrapping_add(1);
                self.policy.sent(gen, done);
                self.with_status(h, |st| {
                    st.frames += 1;
                    st.last_sent = Some(done);
                });
            }
            Outcome::Unknown => {
                self.set_state(h, "locks unknown");
                return Next::WaitUntil(done + Duration::from_secs(1));
            }
            Outcome::Poisoned => {
                // The write's buffers belong to the kernel now, and so does
                // the right to the device: keep the lock and the handle for
                // the life of the process (R3-3).
                self.poisoned = true;
                std::mem::forget(self.lights.take());
                std::mem::forget(self.guard.take());
                self.set_error(h, "a write is stuck: the channel is off until the daemon restarts".into());
                return Next::Exit;
            }
            Outcome::Abandoned(why) => match why {
                frame::Why::Abort(Abort::Superseded) | frame::Why::Abort(Abort::Revoked) => {}
                frame::Why::Abort(Abort::Stop) => {
                    self.release();
                    return Next::Exit;
                }
                frame::Why::Write(e) => {
                    // The handle may be bad: drop it; the next frame reopens.
                    self.lights = None;
                    self.policy.failed(gen, done);
                    self.set_error(h, e);
                }
                other => {
                    self.policy.failed(gen, done);
                    self.set_error(h, format!("{other:?}"));
                }
            },
        }
        Next::WaitUntil(done)
    }

    fn release(&mut self) {
        self.lights = None;
        self.guard = None;
    }

    fn with_status(&self, h: &Handle, f: impl FnOnce(&mut Status)) {
        let mut s = h.0.slot.lock().unwrap_or_else(|e| e.into_inner());
        f(&mut s.status);
    }

    fn set_state(&self, h: &Handle, state: &'static str) {
        self.with_status(h, |st| st.state = state);
    }

    fn set_error(&self, h: &Handle, e: String) {
        self.with_status(h, |st| {
            st.failures += 1;
            st.last_error = Some(e);
        });
    }
}

/// Wall-clock time whose sleeps end early on any change to the slot.
struct Wall<'a>(&'a Handle);

impl Time for Wall<'_> {
    fn now(&self) -> Instant {
        Instant::now()
    }
    fn sleep_until(&self, t: Instant) -> bool {
        let s = self.0 .0.slot.lock().unwrap_or_else(|e| e.into_inner());
        if s.stop {
            return false;
        }
        let wait = t.saturating_duration_since(Instant::now());
        let (s, _) = self.0 .0.cv.wait_timeout_while(s, wait, |s| !s.stop).unwrap_or_else(|e| e.into_inner());
        !s.stop
    }
}

/// Start the sender thread.
pub fn spawn<P: Platform + Send + 'static>(platform: P) -> Result<Handle, String> {
    let h = Handle::new();
    let theirs = h.clone();
    std::thread::Builder::new()
        .name("ak820-lockchan".into())
        .spawn(move || {
            let mut core = Core::new(platform);
            let time = Wall(&theirs);
            loop {
                let next = core.step(&theirs, &time);
                let s = theirs.0.slot.lock().unwrap_or_else(|e| e.into_inner());
                match next {
                    Next::Exit => break,
                    Next::Wait => {
                        // Until something in the slot changes, or a minute.
                        let _ = theirs.0.cv.wait_timeout(s, Duration::from_secs(60));
                    }
                    Next::WaitUntil(t) => {
                        let wait = t.saturating_duration_since(Instant::now());
                        if !wait.is_zero() {
                            let _ = theirs.0.cv.wait_timeout(s, wait);
                        }
                    }
                }
            }
        })
        .map_err(|e| format!("spawning the lock-LED thread: {e}"))?;
    Ok(h)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::lockchan::frame::WriteError;
    use crate::lockchan::Profile;
    use std::cell::Cell;
    use std::rc::Rc;
    use std::sync::atomic::{AtomicUsize, Ordering};

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
    }

    #[derive(Clone)]
    struct Log(Rc<std::cell::RefCell<Vec<u8>>>);

    struct Lights {
        log: Log,
        t: Rc<FakeTime>,
        fail: bool,
    }
    impl LockLights for Lights {
        fn profile(&self) -> Profile {
            Profile::P0
        }
        fn spacing(&self) -> Duration {
            Duration::from_millis(16)
        }
        fn user(&mut self) -> Option<u8> {
            Some(0x00)
        }
        fn write(&mut self, v: u8) -> Result<(), WriteError> {
            if self.fail {
                return Err(WriteError::Failed("gone".into()));
            }
            self.t.0.set(self.t.0.get() + Duration::from_millis(1));
            self.log.0.borrow_mut().push(v);
            Ok(())
        }
    }

    struct Plat {
        log: Log,
        t: Rc<FakeTime>,
        present: bool,
        opens: Rc<AtomicUsize>,
        locks: Rc<AtomicUsize>,
        fail: bool,
    }
    // The fake runs on the test's thread only.
    unsafe impl Send for Plat {}
    impl Platform for Plat {
        type Lights = Lights;
        fn open(&mut self) -> Result<Option<Lights>, String> {
            self.opens.fetch_add(1, Ordering::SeqCst);
            Ok(self.present.then(|| Lights { log: self.log.clone(), t: self.t.clone(), fail: self.fail }))
        }
        fn lock(&mut self) -> Result<Option<Box<dyn Any + Send>>, String> {
            self.locks.fetch_add(1, Ordering::SeqCst);
            Ok(Some(Box::new(())))
        }
        fn input(&self) -> Option<(u32, u32)> {
            None
        }
    }

    fn rig() -> (Core<Plat>, Handle, Rc<FakeTime>, Log) {
        let t = Rc::new(FakeTime(Cell::new(Instant::now())));
        let log = Log(Rc::new(std::cell::RefCell::new(vec![])));
        let p = Plat { log: log.clone(), t: t.clone(), present: true, opens: Rc::new(AtomicUsize::new(0)), locks: Rc::new(AtomicUsize::new(0)), fail: false };
        (Core::new(p), Handle::new(), t, log)
    }

    fn title(s: &str) -> Content {
        let mut p = vec![1u8, s.len() as u8];
        p.extend_from_slice(s.as_bytes());
        (super::super::TYPE_NOW_PLAYING, p)
    }

    /// Run steps until the core waits past `until`, advancing fake time.
    fn run_until(core: &mut Core<Plat>, h: &Handle, t: &FakeTime, until: Instant) {
        for _ in 0..1000 {
            match core.step(h, t) {
                Next::WaitUntil(w) if w <= until => t.sleep_until(w).then_some(()).unwrap(),
                Next::WaitUntil(_) | Next::Wait | Next::Exit => return,
            }
        }
        panic!("no rest");
    }

    #[test]
    fn nothing_goes_until_granted_then_after_the_handover_wait() {
        let (mut core, h, t, log) = rig();
        let t0 = t.now();
        h.set(title("A"), t0);
        assert_eq!(core.step(&h, &*t), Next::Wait);
        assert!(log.0.borrow().is_empty());
        h.grant(None, t0);
        assert_eq!(core.step(&h, &*t), Next::WaitUntil(t0 + HANDOVER), "no history: the full wait");
        h.set(title("A"), t0 + HANDOVER);
        t.0.set(t0 + HANDOVER);
        run_until(&mut core, &h, &t, t0 + HANDOVER + Duration::from_secs(1));
        assert!(!log.0.borrow().is_empty());
        assert_eq!(h.status().frames, 1);
    }

    #[test]
    fn a_known_last_raw_push_waits_12_s_from_it() {
        let (mut core, h, t, _) = rig();
        let t0 = t.now();
        h.set(title("A"), t0);
        h.grant(Some(t0 - Duration::from_secs(10)), t0);
        assert_eq!(core.step(&h, &*t), Next::WaitUntil(t0 + Duration::from_secs(2)));
    }

    #[test]
    fn stale_content_becomes_clear() {
        let (mut core, h, t, log) = rig();
        let t0 = t.now();
        h.set(title("A"), t0 - STALE_AFTER - Duration::from_secs(1));
        h.grant(Some(t0 - HANDOVER), t0);
        run_until(&mut core, &h, &t, t0 + Duration::from_secs(1));
        // A CLEAR frame is short: far fewer writes than a title's.
        let n = log.0.borrow().len();
        assert!(n > 10 && n < 60, "{n} writes");
    }

    #[test]
    fn revoke_releases_the_lock_and_the_handle_and_grant_takes_them_again() {
        let (mut core, h, t, _) = rig();
        let t0 = t.now();
        h.set(title("A"), t0);
        h.grant(Some(t0 - HANDOVER), t0);
        run_until(&mut core, &h, &t, t0 + Duration::from_secs(1));
        assert!(core.guard.is_some() && core.lights.is_some());
        h.revoke();
        assert_eq!(core.step(&h, &*t), Next::Wait);
        assert!(core.guard.is_none() && core.lights.is_none());
    }

    #[test]
    fn absent_relists_every_10_s() {
        let (mut core, h, t, log) = rig();
        core.platform.present = false;
        let t0 = t.now();
        h.set(title("A"), t0);
        h.grant(Some(t0 - HANDOVER), t0);
        assert_eq!(core.step(&h, &*t), Next::WaitUntil(t0 + RELIST));
        assert_eq!(h.status().state, "absent");
        assert_eq!(core.step(&h, &*t), Next::WaitUntil(t0 + RELIST), "no relist before its time");
        assert_eq!(core.platform.opens.load(Ordering::SeqCst), 1);
        assert!(log.0.borrow().is_empty());
    }

    #[test]
    fn a_failed_write_drops_the_handle_and_backs_off() {
        let (mut core, h, t, _) = rig();
        core.platform.fail = true;
        let t0 = t.now();
        h.set(title("A"), t0);
        h.grant(Some(t0 - HANDOVER), t0);
        let next = core.step(&h, &*t);
        assert!(core.lights.is_none());
        assert!(h.status().failures >= 1);
        assert!(matches!(next, Next::WaitUntil(_)));
    }

    #[test]
    fn stop_exits_and_releases() {
        let (mut core, h, t, _) = rig();
        h.stop();
        assert_eq!(core.step(&h, &*t), Next::Exit);
        assert!(core.guard.is_none());
    }
}
