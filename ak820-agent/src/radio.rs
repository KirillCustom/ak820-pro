//! Now-playing over the 2.4G dongle when the cable is out: the daemon's side
//! of raw81 stage 2 (`plans/RAWHID-24G-DAEMON-PLAN.md`, D5-D8 and revisions
//! 2-4).
//!
//! The agent calls [`Radio::tick`] only when the **USB opener** said `Absent`
//! (R2-5) and `--radio` is on, and [`Radio::usb_present`] whenever the board
//! is on USB, which ends the radio's acquisition. The radio keeps its own
//! state, backoffs and status: nothing here touches the USB presence `Watch`.
//!
//! One tick, in order: the backoff; the dongle (opened per tick, as the board
//! is); the machine-wide lock, held for the acquisition (R3-2), which starts
//! a [`Link`] and its quiet; the probe if the session is unqualified; a full
//! text refresh on a new qualification (R2-7); the publisher's text if due;
//! the playback readout. A failed text push leaves the publisher where it
//! was, so the next tick retries, as on USB (D8).

use std::time::{Duration, Instant};

use crate::hid::exchange::Wire;
use crate::hid;
use crate::logfile::Log;
use crate::media::Publisher;
use crate::platform::RadioGuard;
use crate::raw81::link::{Clock, Failure, Link};
use crate::smtc::Snapshot;
use crate::status::RadioStatus;

/// Silence (no answer with the dongle present: a sleeping board, a board on
/// another mode, a build without stage 2) backs off 1 s, 2 s, 4 s ... to this
/// (R2-7); at the cap the acquisition ends (the lock is released, and the
/// next attempt starts a new session with its quiet).
pub const SILENCE_CAP: Duration = Duration::from_secs(30);
/// Another session live on the board (D3).
pub const BUSY_BACKOFF: Duration = Duration::from_secs(30);
/// A command the board refuses (a stage-2 board without text on its
/// allow-list) or a reply that is not an answer: not retried soon.
pub const REFUSED_BACKOFF: Duration = Duration::from_secs(600);
/// A stuck handle, as on USB.
pub const ABANDON_BACKOFF: Duration = crate::agent::ABANDON_BACKOFF;
/// The lock could not even be tried.
pub const LOCK_ERROR_BACKOFF: Duration = Duration::from_secs(60);

/// The radio's state, as the status file and the log name it.
#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum State {
    /// Nothing tried yet, or the board is on USB.
    Off,
    /// No dongle.
    Absent,
    /// Another owner on this machine holds the lock.
    Contended,
    /// A session's quiet, a grace or a rotation.
    Quiet,
    /// The board answers this session.
    Qualified,
    /// No answer.
    Silent,
    /// Another session is live on the board.
    Busy,
    /// The board refused a command, or a reply did not answer it.
    Refused,
    /// The dongle would not open, or a transfer failed.
    Unresponsive,
    /// A stuck handle.
    Abandoned,
    /// The lock file could not be opened.
    LockError,
}

impl State {
    pub fn word(self) -> &'static str {
        match self {
            State::Off => "off",
            State::Absent => "absent",
            State::Contended => "contended",
            State::Quiet => "quiet",
            State::Qualified => "qualified",
            State::Silent => "silent",
            State::Busy => "busy",
            State::Refused => "refused",
            State::Unresponsive => "unresponsive",
            State::Abandoned => "abandoned",
            State::LockError => "lock error",
        }
    }
}

/// Where the log lines go: the daemon's log, or a test's list.
pub trait Sink {
    fn line(&mut self, s: &str);
}

impl Sink for &Log {
    fn line(&mut self, s: &str) {
        Log::line(self, s)
    }
}

impl Sink for Vec<String> {
    fn line(&mut self, s: &str) {
        self.push(s.to_string())
    }
}

/// The radio's side of the daemon.
pub struct Radio {
    link: Option<(Link, RadioGuard)>,
    /// A stuck transfer happened: the lock was leaked on purpose
    /// (`mem::forget`), so only the process's exit releases it, whatever
    /// becomes of this object. The abandoned write may still leave the
    /// kernel, so no other owner may start a session (codex, daemon review,
    /// must-fix 1, and its verify).
    poisoned: bool,
    state: State,
    hold_until: Option<Instant>,
    silence: u32,
    pushed_generation: u64,
    seed: u64,
    pub status: RadioStatus,
    /// Called right before this tick's first media request, so a lower
    /// transport (the lock lights) stops before the radio pushes (codex,
    /// lock-LED implementation review, 4).
    pub before_media: Option<Box<dyn Fn()>>,
    /// When a media request last completed, delivered or not: the lock
    /// lights' handover waits after it (R4-2; implementation review, 5).
    pub last_media_attempt: Option<Instant>,
}

impl Default for Radio {
    fn default() -> Self {
        Radio::new(0x9E37_79B9_7F4A_7C15)
    }
}

impl Radio {
    /// `seed`: any varying number, for the session ids.
    pub fn new(seed: u64) -> Radio {
        Radio {
            link: None,
            poisoned: false,
            state: State::Off,
            hold_until: None,
            silence: 0,
            pushed_generation: 0,
            seed,
            status: RadioStatus { state: State::Off.word().into(), ..RadioStatus::default() },
            before_media: None,
            last_media_attempt: None,
        }
    }

    pub fn state(&self) -> State {
        self.state
    }

    /// Is the radio carrying the media now?
    pub fn active(&self) -> bool {
        self.state == State::Qualified
    }

    fn set(&mut self, next: State, why: Option<&str>, log: &mut impl Sink) {
        if next != self.state {
            log.line(&match why {
                Some(w) => format!("radio: {} -> {} ({w})", self.state.word(), next.word()),
                None => format!("radio: {} -> {}", self.state.word(), next.word()),
            });
            self.state = next;
            self.status.state = next.word().into();
        }
    }

    /// End the acquisition: the session and the lock. A new link counts its
    /// qualifications from 1 again, so the next one refreshes the text.
    fn release(&mut self) {
        self.link = None;
        self.pushed_generation = 0;
        self.status.session = None;
        self.status.qualified = false;
    }

    /// The board is on USB: the radio stands down, releasing the lock
    /// (R2-2), and its backoffs are forgotten (USB presence is new evidence).
    /// A poisoned lock is kept, and the radio stays abandoned.
    pub fn usb_present(&mut self, log: &mut impl Sink) {
        if self.poisoned {
            return;
        }
        self.release();
        self.hold_until = None;
        self.silence = 0;
        self.set(State::Off, Some("the board is on USB"), log);
    }

    /// Does this radio hold an acquisition (the lock and a session)?
    pub fn acquired(&self) -> bool {
        self.link.is_some()
    }

    /// One media tick over the radio, and whether the text went out. See the
    /// module documentation.
    #[allow(clippy::too_many_arguments)]
    pub fn tick<W: Wire>(
        &mut self,
        clock: &impl Clock,
        lock: impl FnOnce() -> Result<Option<RadioGuard>, String>,
        open: impl FnOnce() -> Result<W, hid::Error>,
        publisher: &mut Publisher,
        snapshot: &Snapshot,
        stamp: impl Fn() -> String,
        log: &mut impl Sink,
    ) -> bool {
        if self.poisoned {
            return false;
        }
        let now = clock.now();
        if self.hold_until.is_some_and(|until| now < until) {
            return false;
        }
        self.hold_until = None;

        let dev = match open() {
            Ok(d) => d,
            Err(e) => {
                self.release();
                let next = match e {
                    hid::Error::Absent => State::Absent,
                    hid::Error::Stuck => {
                        self.hold_until = Some(now + ABANDON_BACKOFF);
                        State::Abandoned
                    }
                    _ => State::Unresponsive,
                };
                self.set(next, Some(&e.to_string()), log);
                return false;
            }
        };

        if self.link.is_none() {
            match lock() {
                Ok(Some(guard)) => {
                    self.seed = self.seed.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
                    // The quiet runs from the moment the lock is held, never
                    // from before the open and the lock (codex, must-fix 2).
                    self.link = Some((Link::new(clock.now(), self.seed ^ nanos()), guard));
                    self.set(State::Quiet, Some("lock taken; a new session waits out its quiet"), log);
                }
                Ok(None) => {
                    self.set(State::Contended, Some("another radio owner on this machine"), log);
                    return false;
                }
                Err(e) => {
                    self.hold_until = Some(now + LOCK_ERROR_BACKOFF);
                    self.set(State::LockError, Some(&e), log);
                    return false;
                }
            }
        }
        let (link, _) = self.link.as_mut().expect("just ensured");

        if !link.qualified() {
            if let Err(f) = link.probe(&dev, clock) {
                self.fail(f, now, &stamp, log);
                return false;
            }
            self.silence = 0;
        }
        let link = &mut self.link.as_mut().expect("still held").0;
        self.status.session = link.session();
        self.status.sessions = link.sessions;
        if link.generation() != self.pushed_generation {
            publisher.refresh();
            self.pushed_generation = link.generation();
        }
        self.set(State::Qualified, Some("the board answers"), log);

        let plan = publisher.plan(snapshot, now);
        let mut pushed = false;
        if let Some(f) = &self.before_media {
            f();
        }
        if let Some(text) = &plan.text {
            for (command, body) in text {
                let link = &mut self.link.as_mut().expect("held").0;
                let r = link.push(&dev, clock, *command, body);
                self.last_media_attempt = Some(clock.now());
                if let Err(f) = r {
                    self.fail(f, now, &stamp, log);
                    return false;
                }
            }
            publisher.text_pushed(snapshot, now);
            pushed = true;
            self.status.pushes += 1;
            self.status.last_push = Some(stamp());
            if plan.changed {
                log.line(&format!("radio: {}", Publisher::describe(snapshot)));
            }
        }
        let link = &mut self.link.as_mut().expect("held").0;
        let r = link.push(&dev, clock, plan.playback.0, &plan.playback.1);
        self.last_media_attempt = Some(clock.now());
        if let Err(f) = r {
            self.fail(f, now, &stamp, log);
            return pushed;
        }
        self.status.qualified = self.link.as_ref().is_some_and(|(l, _)| l.qualified());
        pushed
    }

    fn fail(&mut self, f: Failure, now: Instant, stamp: &impl Fn() -> String, log: &mut impl Sink) {
        if let Some((link, _)) = &self.link {
            self.status.session = link.session();
            self.status.sessions = link.sessions;
            self.status.qualified = link.qualified();
        }
        if let Failure::Waiting { until } = f {
            self.hold_until = Some(until);
            self.set(State::Quiet, Some("a session's quiet or grace"), log);
            return;
        }
        // Kept until the next failure, with its time: a success does not
        // erase it (codex, daemon review, 8).
        self.status.failures += 1;
        self.status.last_error = Some(f.to_string());
        self.status.last_error_at = Some(stamp());
        match f {
            Failure::Waiting { .. } => unreachable!(),
            Failure::Unknown => {
                self.status.silent += 1;
                self.silence += 1;
                let wait = Duration::from_secs(1u64 << (self.silence - 1).min(5)).min(SILENCE_CAP);
                self.hold_until = Some(now + wait);
                if wait >= SILENCE_CAP {
                    self.release();
                }
                self.set(State::Silent, Some("no answer on the radio"), log);
            }
            // Retried next tick, the state unchanged: the board is there.
            // Incomplete (the board ran it) is kept apart from Unknown (it
            // may not have), in the log and the counters (R2-4).
            Failure::Incomplete => {
                self.status.incomplete += 1;
                log.line("radio: incomplete (the board ran a command; its reply was lost)");
            }
            Failure::BadCrc => {}
            Failure::Stale => self.set(State::Quiet, Some("the session went stale; rotating"), log),
            Failure::Busy => {
                self.hold_until = Some(now + BUSY_BACKOFF);
                self.set(State::Busy, Some("another session is live on the board"), log);
            }
            Failure::Forbidden | Failure::Mismatch | Failure::TooLong => {
                let why = f.to_string();
                self.release();
                self.hold_until = Some(now + REFUSED_BACKOFF);
                self.set(State::Refused, Some(&why), log);
            }
            Failure::Hid(hid::Error::Stuck) => {
                // The lock is kept for the process's life and the radio
                // disabled: the abandoned write may still leave the kernel
                // (codex, must-fix 1). A cooldown cannot bound it.
                if let Some((_, guard)) = self.link.take() {
                    std::mem::forget(guard);
                }
                self.poisoned = true;
                self.status.session = None;
                self.status.qualified = false;
                self.set(
                    State::Abandoned,
                    Some("a radio transfer never completed; the radio is off until the daemon restarts"),
                    log,
                );
            }
            Failure::Hid(e) => self.set(State::Unresponsive, Some(&e.to_string()), log),
        }
    }
}

fn nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos() as u64)
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::raw81::fake::{Board, Fake, FakeClock};
    use crate::raw81::link::QUIET;
    use crate::raw81::{ST_BUSY, ST_FORBIDDEN, ST_STALE};
    use crate::smtc::Snapshot;
    use std::cell::Cell;

    fn snap() -> Snapshot {
        Snapshot { title: "Radio title".into(), artist: "The Dongle".into(), ..Snapshot::default() }
    }

    struct Rig<'c> {
        c: &'c FakeClock,
        f: Fake<'c>,
        r: Radio,
        p: Publisher,
        log: Vec<String>,
        lock_free: Cell<bool>,
        locks_taken: Cell<u32>,
        dongle: Cell<bool>,
    }

    impl<'c> Rig<'c> {
        fn new(c: &'c FakeClock, script: &[Board]) -> Rig<'c> {
            Rig {
                c,
                f: Fake::new(c, script),
                r: Radio::new(1),
                p: Publisher::new(),
                log: Vec::new(),
                lock_free: Cell::new(true),
                locks_taken: Cell::new(0),
                dongle: Cell::new(true),
            }
        }

        fn tick(&mut self, s: &Snapshot) {
            let lock = || {
                if self.lock_free.get() {
                    self.locks_taken.set(self.locks_taken.get() + 1);
                    Ok(Some(Box::new(()) as RadioGuard))
                } else {
                    Ok(None)
                }
            };
            let f = &self.f;
            let open = || if self.dongle.get() { Ok(f) } else { Err(hid::Error::Absent) };
            self.r.tick(self.c, lock, open, &mut self.p, s, || "now".into(), &mut self.log);
        }

        /// The inners the board received, as (channel, command).
        fn sent(&self) -> Vec<(u8, u8)> {
            self.f.got.borrow().iter().map(|(_, _, i)| (i[1], i[2])).collect()
        }
    }


    #[test]
    fn nothing_is_sent_in_the_first_quiet_then_probe_text_and_playback() {
        let c = FakeClock::new();
        let mut g = Rig::new(&c, &[]);
        g.tick(&snap());
        assert_eq!(g.r.state(), State::Quiet);
        assert!(g.sent().is_empty());
        c.advance(Duration::from_secs(1));
        g.tick(&snap());
        assert!(g.sent().is_empty(), "held until the quiet ends");
        c.advance(QUIET);
        g.tick(&snap());
        assert_eq!(g.r.state(), State::Qualified);
        // The probe (health), the two lines, the playback.
        assert_eq!(g.sent(), [(0x13, 0x01), (0x12, 0x03), (0x12, 0x03), (0x12, 0x04)]);
        assert_eq!(g.r.status.pushes, 1);
        assert!(g.log.iter().any(|l| l.contains("radio:") && l.contains("Radio title")), "{:?}", g.log);
    }

    #[test]
    fn a_steady_track_sends_only_playback_until_the_keepalive() {
        let c = FakeClock::new();
        let mut g = Rig::new(&c, &[]);
        g.tick(&snap());
        c.advance(QUIET);
        g.tick(&snap());
        let n = g.sent().len();
        c.advance(Duration::from_secs(3));
        g.tick(&snap());
        assert_eq!(&g.sent()[n..], [(0x12, 0x04)]);
        assert_eq!(g.locks_taken.get(), 1, "the lock is held across ticks");
    }

    #[test]
    fn contention_sends_nothing_and_takes_the_lock_when_free() {
        let c = FakeClock::new();
        let mut g = Rig::new(&c, &[]);
        g.lock_free.set(false);
        g.tick(&snap());
        assert_eq!(g.r.state(), State::Contended);
        g.lock_free.set(true);
        g.tick(&snap());
        assert_eq!(g.r.state(), State::Quiet);
        assert!(g.sent().is_empty());
    }

    #[test]
    fn silence_backs_off_and_releases_at_the_cap() {
        let c = FakeClock::new();
        let silent = vec![Board::Silent; 64];
        let mut g = Rig::new(&c, &silent);
        g.tick(&snap());
        c.advance(QUIET);
        let mut waits = Vec::new();
        for _ in 0..8 {
            let before = g.f.written.borrow().len();
            g.tick(&snap());
            if g.f.written.borrow().len() > before {
                waits.push(g.r.hold_until.map(|u| u.saturating_duration_since(c.now()).as_secs()));
            }
            c.advance(g.r.hold_until.map_or(Duration::from_secs(3), |u| u.saturating_duration_since(c.now())));
        }
        assert_eq!(g.r.state(), State::Silent);
        // Probes only: never text to a board that does not answer.
        assert!(g.sent().iter().all(|&s| s == (0x13, 0x01)));
        assert!(waits.windows(2).all(|w| w[0] <= w[1]), "{waits:?}");
        assert!(g.locks_taken.get() >= 2, "released at the cap, retaken after");
    }

    #[test]
    fn usb_presence_stands_the_radio_down_and_a_new_acquisition_refreshes_the_text() {
        let c = FakeClock::new();
        let mut g = Rig::new(&c, &[]);
        g.tick(&snap());
        c.advance(QUIET);
        g.tick(&snap());
        g.r.usb_present(&mut g.log);
        assert_eq!(g.r.state(), State::Off);
        // The cable out again: a new lock, a new quiet, then the full text even
        // though the keepalive is not due.
        c.advance(Duration::from_secs(3));
        g.tick(&snap());
        assert_eq!(g.r.state(), State::Quiet);
        let n = g.sent().len();
        c.advance(QUIET);
        g.tick(&snap());
        assert_eq!(&g.sent()[n..], [(0x13, 0x01), (0x12, 0x03), (0x12, 0x03), (0x12, 0x04)]);
        assert_eq!(g.locks_taken.get(), 2);
    }

    #[test]
    fn no_dongle_is_absent_and_releases() {
        let c = FakeClock::new();
        let mut g = Rig::new(&c, &[]);
        g.tick(&snap());
        c.advance(QUIET);
        g.tick(&snap());
        g.dongle.set(false);
        c.advance(Duration::from_secs(3));
        g.tick(&snap());
        assert_eq!(g.r.state(), State::Absent);
        assert!(g.r.link.is_none());
    }

    #[test]
    fn busy_after_admission_backs_off_and_forbidden_refuses() {
        let c = FakeClock::new();
        let script = [Board::Answer { ms: 20 }, Board::Nak(ST_BUSY)];
        let mut g = Rig::new(&c, &script);
        g.tick(&snap());
        c.advance(QUIET);
        g.tick(&snap());
        assert_eq!(g.r.state(), State::Busy);
        assert!(g.r.hold_until.unwrap() >= c.now() + BUSY_BACKOFF - Duration::from_secs(1));

        let c2 = FakeClock::new();
        let mut h = Rig::new(&c2, &[Board::Answer { ms: 20 }, Board::Nak(ST_FORBIDDEN)]);
        h.tick(&snap());
        c2.advance(QUIET);
        h.tick(&snap());
        assert_eq!(h.r.state(), State::Refused);
        assert!(h.r.link.is_none());
    }

    #[test]
    fn before_media_runs_before_the_first_media_request_and_failures_stamp_the_attempt() {
        let c = FakeClock::new();
        // Probe ok, line 0 ok, line 1 incomplete: the media failed after
        // reaching the board, maybe.
        let script = [Board::Answer { ms: 20 }, Board::Answer { ms: 20 }, Board::ReceiptOnly];
        let mut g = Rig::new(&c, &script);
        let calls = std::rc::Rc::new(Cell::new(0usize));
        let seen = std::rc::Rc::new(Cell::new(usize::MAX));
        {
            let (calls, seen) = (calls.clone(), seen.clone());
            let got = g.f.got.as_ptr();
            g.r.before_media = Some(Box::new(move || {
                calls.set(calls.get() + 1);
                // What had gone out when it ran: the probe only.
                seen.set(unsafe { (*got).len() });
            }));
        }
        g.tick(&snap());
        assert_eq!(calls.get(), 0, "nothing before the quiet ends");
        assert!(g.r.last_media_attempt.is_none());
        c.advance(QUIET);
        g.tick(&snap());
        assert_eq!(calls.get(), 1);
        assert_eq!(seen.get(), 1, "after the probe, before any media request");
        assert_eq!(g.r.status.pushes, 0, "the push failed");
        assert!(g.r.last_media_attempt.is_some(), "a failed media request still stamps the attempt");
    }

    #[test]
    fn a_failed_text_push_is_retried_next_tick() {
        let c = FakeClock::new();
        // Probe ok, line 0 ok, line 1 incomplete: the publisher does not move.
        let script = [Board::Answer { ms: 20 }, Board::Answer { ms: 20 }, Board::ReceiptOnly];
        let mut g = Rig::new(&c, &script);
        g.tick(&snap());
        c.advance(QUIET);
        g.tick(&snap());
        assert_eq!(g.r.status.pushes, 0);
        let n = g.sent().len();
        c.advance(Duration::from_secs(3));
        g.tick(&snap());
        assert_eq!(&g.sent()[n..], [(0x12, 0x03), (0x12, 0x03), (0x12, 0x04)]);
        assert_eq!(g.r.status.pushes, 1);
    }

    #[test]
    fn stale_rotates_and_the_next_session_refreshes() {
        let c = FakeClock::new();
        let script = [Board::Answer { ms: 20 }, Board::Nak(ST_STALE)];
        let mut g = Rig::new(&c, &script);
        g.tick(&snap());
        c.advance(QUIET);
        g.tick(&snap());
        assert_eq!(g.r.state(), State::Quiet);
        c.advance(QUIET);
        g.tick(&snap());
        assert_eq!(g.r.state(), State::Qualified);
        assert_eq!(g.r.status.sessions, 2);
        assert_eq!(g.locks_taken.get(), 1, "a rotation keeps the lock");
    }
}
