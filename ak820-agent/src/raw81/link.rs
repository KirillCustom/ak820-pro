//! The radio's session: now-playing over the 2.4G dongle
//! (`plans/RAWHID-24G-DAEMON-PLAN.md`, revisions 1-4).
//!
//! A [`Link`] is one acquisition of the radio: it exists only while the
//! caller holds the machine-wide lock (R3-2), and dropping it is how the
//! session ends. It owns no handle (the daemon opens the dongle per tick, as
//! it opens the board) and nothing persisted: every session starts with
//! [`QUIET`] on the monotonic clock with nothing sent, so no request, reply or
//! receipt of an earlier session, this host's or another's, can still arrive
//! (R4-1), and its id is drawn at random from the safe set.
//!
//! **Text only, by construction** (R2-5): [`Link::push`] puts a command on
//! the text channel and nothing else, and the one other request is the
//! qualification [`Link::probe`] (the health read `PROBE_INNER`, R3-3's
//! stated exception). The clock and the health reads have no way onto the
//! radio.
//!
//! Nothing here blocks on a wait: the quiet, the initial-admission grace and
//! a rotation answer [`Failure::Waiting`] with the instant to come back, and
//! the daemon's next media tick (3 s) does. Every request is inert to a board
//! without stage 2 ([`safe_request`], R3-1), so qualification is an
//! optimisation (no traffic to a board that never answers), not a safety
//! gate.

use std::time::{Duration, Instant};

use super::{safe_request, safe_session, Outcome, Transaction, INNER, PAYLOAD, PROBE_INNER};
use super::{ST_BADCRC, ST_BUSY, ST_FORBIDDEN, ST_STALE};
use crate::hid::exchange::{Queue, Sent, Wire, DRAIN_BUDGET, DRAIN_LIMIT, DRAIN_READ, WRITE_TIMEOUT};
use crate::hid::{Error, OsError};
use crate::proto::{Channel, SET_VALUE};

/// Before a session's first request: a request's delivery (the 1 s lifetime)
/// + the board's response TTL (250 ms) + the response's delivery, rounded up
/// (R4-1).
pub const QUIET: Duration = Duration::from_secs(3);
/// A transaction's read deadline, from the write's completion (R3-3).
pub const DEADLINE: Duration = Duration::from_millis(100);
/// The initial-admission grace: a `BUSY` before this session's first
/// admission is an old request admitted near the end of the quiet, which
/// restarted the board's 1 s idle (R4-1).
pub const GRACE_WAIT: Duration = Duration::from_millis(1100);
pub const GRACE_TRIES: u8 = 3;
/// Seq positions a session may consume, skips included, before it rotates:
/// half the board's 32,767 freshness window.
pub const ROTATE_SPAN: u32 = 16_384;
/// Candidates [`safe_request`] may try for one request (a few in practice).
pub const SEARCH_TRIES: u16 = 256;

/// The monotonic clock, injected so the waits and deadlines are testable.
pub trait Clock {
    fn now(&self) -> Instant;
}

/// `Instant::now()`.
pub struct SystemClock;

impl Clock for SystemClock {
    fn now(&self) -> Instant {
        Instant::now()
    }
}

/// Why a request did not produce its reply.
#[derive(Debug, Clone)]
pub enum Failure {
    /// Not sent: the session's quiet, a grace or a rotation. Come back at or
    /// after `until`.
    Waiting { until: Instant },
    /// A status-0 receipt with no reply: the board ran it, the reply was
    /// lost. Not retried in the same tick (it changed the board).
    Incomplete,
    /// No answer by the deadline, or a corrupt receipt: the board may or may
    /// not have run it. Silence, if it keeps happening.
    Unknown,
    /// The board's CRC check failed (the request was corrupted on the way).
    BadCrc,
    /// Not on the board's allow-list.
    Forbidden,
    /// The session's identity is no longer fresh on the board; it rotates.
    Stale,
    /// Another session is live on the board.
    Busy,
    /// The reply did not echo the command, or said it was unhandled.
    Mismatch,
    /// The command does not fit an envelope (a caller's bug).
    TooLong,
    /// The transport: a dirty queue (not sent), a failed write or read, or a
    /// stuck handle.
    Hid(Error),
}

impl Failure {
    /// Worth one immediate retry on a fresh seq (R2-3: one per request).
    fn retryable(&self) -> bool {
        matches!(self, Failure::Unknown | Failure::BadCrc)
    }
}

impl std::fmt::Display for Failure {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Failure::Waiting { .. } => write!(f, "waiting (the session's quiet or a grace)"),
            Failure::Incomplete => write!(f, "the board ran it; the reply was lost"),
            Failure::Unknown => write!(f, "no answer"),
            Failure::BadCrc => write!(f, "the board's CRC check failed"),
            Failure::Forbidden => write!(f, "not on the board's allow-list"),
            Failure::Stale => write!(f, "the session went stale; rotating"),
            Failure::Busy => write!(f, "another session is live on the board"),
            Failure::Mismatch => write!(f, "the reply did not answer the command"),
            Failure::TooLong => write!(f, "the command does not fit an envelope"),
            Failure::Hid(e) => write!(f, "{e}"),
        }
    }
}

#[derive(Debug, Clone, Copy)]
struct Live {
    id: u8,
    seq: u16,
    issued: u32,
    admitted: bool,
    grace: u8,
}

/// One acquisition of the radio. See the module documentation.
#[derive(Debug)]
pub struct Link {
    live: Option<Live>,
    /// When the pending session's quiet ends.
    quiet_until: Instant,
    /// A grace's wait.
    wait_until: Option<Instant>,
    qualified: bool,
    /// Bumped on every qualification: a new session the board has answered,
    /// after which the panel's text is refreshed whole (R2-7, R3-3).
    generation: u64,
    rng: u64,
    /// Candidates a request's search may try ([`SEARCH_TRIES`]; tests force it).
    search_tries: u16,
    /// Sessions started, for the status file.
    pub sessions: u64,
}

impl Link {
    /// Acquired at `now`, with the lock held: the first session's quiet
    /// starts here. `seed` is any varying number (the clock's nanoseconds).
    pub fn new(now: Instant, seed: u64) -> Link {
        Link {
            live: None,
            quiet_until: now + QUIET,
            wait_until: None,
            qualified: false,
            generation: 0,
            rng: seed | 1,
            search_tries: SEARCH_TRIES,
            sessions: 0,
        }
    }

    /// Has the board answered this session's probe?
    pub fn qualified(&self) -> bool {
        self.qualified
    }

    pub fn generation(&self) -> u64 {
        self.generation
    }

    /// The live session's id, if any.
    pub fn session(&self) -> Option<u8> {
        self.live.map(|l| l.id)
    }

    /// Drop qualification: the next tick probes before any text.
    pub fn disqualify(&mut self) {
        self.qualified = false;
    }

    fn random(&mut self) -> u64 {
        // xorshift64: unpredictable enough for an id the board only needs to
        // tell apart from the last one; the quiet, not the id, is the safety.
        self.rng ^= self.rng << 13;
        self.rng ^= self.rng >> 7;
        self.rng ^= self.rng << 17;
        self.rng
    }

    /// End the session: a quiet, then a new id (R2-3, R4-1).
    fn rotate(&mut self, now: Instant) {
        self.live = None;
        self.qualified = false;
        self.wait_until = None;
        self.quiet_until = now + QUIET;
    }

    /// The session to send in, starting one if the quiet is over.
    fn session_at(&mut self, now: Instant) -> Result<Live, Failure> {
        if let Some(l) = self.live {
            if let Some(until) = self.wait_until {
                if now < until {
                    return Err(Failure::Waiting { until });
                }
                self.wait_until = None;
            }
            return Ok(l);
        }
        if now < self.quiet_until {
            return Err(Failure::Waiting { until: self.quiet_until });
        }
        let ids: Vec<u8> = (1..=255u8).filter(|&s| safe_session(s)).collect();
        let id = ids[(self.random() % ids.len() as u64) as usize];
        let l = Live { id, seq: self.random() as u16, issued: 0, admitted: false, grace: 0 };
        self.live = Some(l);
        self.sessions += 1;
        Ok(l)
    }

    /// The qualification probe (R2-1, R3-3): the health read, the radio's one
    /// non-text request. Answered, the session is qualified.
    pub fn probe(&mut self, wire: &impl Wire, clock: &impl Clock) -> Result<(), Failure> {
        self.request(wire, clock, &PROBE_INNER).map(|_| {
            self.qualified = true;
            self.generation += 1;
        })
    }

    /// One text command (`CLEAR`, `SET_LINE`, `PLAYBACK`), with at most one
    /// retry for a request the board may never have seen.
    pub fn push(&mut self, wire: &impl Wire, clock: &impl Clock, command: u8, body: &[u8]) -> Result<(), Failure> {
        if body.len() + 3 > INNER {
            return Err(Failure::TooLong);
        }
        let mut inner = vec![SET_VALUE, Channel::Text.id(), command];
        inner.extend_from_slice(body);
        match self.request(wire, clock, &inner) {
            Err(f) if f.retryable() => self.request(wire, clock, &inner).map(|_| ()),
            other => other.map(|_| ()),
        }
    }

    /// One transaction: the next inert request, the drain, the write, and
    /// the reply and receipt within [`DEADLINE`].
    fn request(&mut self, wire: &impl Wire, clock: &impl Clock, inner: &[u8]) -> Result<[u8; PAYLOAD], Failure> {
        let now = clock.now();
        let mut l = self.session_at(now)?;
        let Some((seq, payload)) = safe_request(l.id, l.seq.wrapping_add(1), inner, self.search_tries) else {
            self.rotate(now);
            return Err(Failure::Waiting { until: self.quiet_until });
        };
        let used = u32::from(seq.wrapping_sub(l.seq));
        if l.issued + used > ROTATE_SPAN {
            self.rotate(now);
            return Err(Failure::Waiting { until: self.quiet_until });
        }

        // Every input report reaches every open handle: start from an empty
        // queue or not at all (R2-4).
        drain(wire, clock)?;

        l.issued += used;
        l.seq = seq;
        self.live = Some(l);

        // From the write on, a transport failure leaves the board's state
        // unknown: qualification is dropped, so the next tick probes and,
        // answered, refreshes the text whole (codex, daemon review, 6).
        let mut frame = [0u8; PAYLOAD + 1];
        frame[1..].copy_from_slice(&payload);
        match wire.write_report(&frame, WRITE_TIMEOUT) {
            Ok(Sent::Yes) => {}
            Ok(Sent::TimedOut) => {
                self.qualified = false;
                return Err(Failure::Hid(Error::Io {
                    op: "write",
                    source: OsError::new(0x800703E3_u32 as i32 as i64, "the radio write timed out and was cancelled"),
                }));
            }
            Err(e) => {
                self.qualified = false;
                return Err(Failure::Hid(e));
            }
        }

        let mut t = Transaction::new(l.id, seq, inner);
        let deadline = clock.now() + DEADLINE;
        loop {
            let left = deadline.saturating_duration_since(clock.now());
            if left.is_zero() {
                t.timeout();
                break;
            }
            match wire.read_report(left) {
                Ok(None) => {}
                Ok(Some(r)) => {
                    // Checked before feeding: a report that arrives after the
                    // deadline completes nothing (R3-3).
                    if clock.now() >= deadline {
                        t.timeout();
                        break;
                    }
                    if t.feed(&r) {
                        break;
                    }
                }
                Err(e) => {
                    self.qualified = false;
                    return Err(Failure::Hid(e));
                }
            }
        }

        let now = clock.now();
        let outcome = t.outcome().cloned().unwrap_or(Outcome::Unknown);
        match outcome {
            Outcome::Ok(reply) => {
                self.admit();
                if reply[0] == 0xFF || reply[..inner.len()] != *inner {
                    return Err(Failure::Mismatch);
                }
                Ok(reply)
            }
            Outcome::Incomplete => {
                self.admit();
                Err(Failure::Incomplete)
            }
            Outcome::Unknown => {
                self.qualified = false;
                Err(Failure::Unknown)
            }
            Outcome::Nak(rc) => match rc.status {
                ST_BADCRC => Err(Failure::BadCrc),
                ST_FORBIDDEN => Err(Failure::Forbidden),
                ST_STALE => {
                    self.rotate(now);
                    Err(Failure::Stale)
                }
                ST_BUSY => {
                    let l = self.live.as_mut().expect("a live session sent this");
                    if !l.admitted && l.grace < GRACE_TRIES {
                        l.grace += 1;
                        let until = now + GRACE_WAIT;
                        self.wait_until = Some(until);
                        return Err(Failure::Waiting { until });
                    }
                    Err(Failure::Busy)
                }
                _ => Err(Failure::Unknown),
            },
        }
    }

    /// A status-0 receipt: the board admitted this session.
    fn admit(&mut self) {
        if let Some(l) = self.live.as_mut() {
            l.admitted = true;
        }
    }
}

/// The exchange's drain, on the injected clock: empty, or the request is not
/// sent.
fn drain(wire: &impl Wire, clock: &impl Clock) -> Result<(), Failure> {
    let deadline = clock.now() + DRAIN_BUDGET;
    for _ in 0..DRAIN_LIMIT {
        if clock.now() >= deadline {
            return Err(Failure::Hid(Error::Dirty { queue: Queue::OutOfTime, drained: Vec::new() }));
        }
        match wire.read_report(DRAIN_READ) {
            Ok(None) => return Ok(()),
            Ok(Some(_)) => {}
            Err(Error::Stuck) => return Err(Failure::Hid(Error::Stuck)),
            Err(_) => return Err(Failure::Hid(Error::Dirty { queue: Queue::Unreadable, drained: Vec::new() })),
        }
    }
    Err(Failure::Hid(Error::Dirty { queue: Queue::MoreWaiting, drained: Vec::new() }))
}

#[cfg(test)]
mod tests {
    use super::super::fake::{Board, Fake, FakeClock};
    use super::*;

    const LINE: &[u8] = b"\x00\x01Radio title";

    /// A link whose first session is open (its quiet waited out).
    fn opened(clock: &FakeClock) -> Link {
        let l = Link::new(clock.now(), 0x1234_5678);
        clock.advance(QUIET);
        l
    }

    #[test]
    fn the_first_session_waits_out_the_quiet_with_nothing_sent() {
        let c = FakeClock::new();
        let f = Fake::new(&c, &[]);
        let mut l = Link::new(c.now(), 7);
        match l.probe(&f, &c) {
            Err(Failure::Waiting { until }) => assert_eq!(until, c.now() + QUIET),
            other => panic!("{other:?}"),
        }
        c.advance(QUIET - Duration::from_millis(1));
        assert!(matches!(l.probe(&f, &c), Err(Failure::Waiting { .. })));
        assert!(f.written.borrow().is_empty());
        c.advance(Duration::from_millis(1));
        l.probe(&f, &c).unwrap();
        assert!(l.qualified());
        assert_eq!(l.generation(), 1);
        assert_eq!(f.got.borrow()[0].2[..3], PROBE_INNER);
    }

    #[test]
    fn a_push_reaches_the_board_whole_and_the_session_is_safe() {
        let c = FakeClock::new();
        let f = Fake::new(&c, &[]);
        let mut l = opened(&c);
        l.push(&f, &c, 0x03, LINE).unwrap();
        let got = f.got.borrow();
        let (s, _, inner) = &got[0];
        assert!(safe_session(*s));
        assert_eq!(&inner[..3 + LINE.len()], b"\x07\x12\x03\x00\x01Radio title");
    }

    #[test]
    fn seqs_only_move_forward_and_skip_window_bytes() {
        let c = FakeClock::new();
        let f = Fake::new(&c, &[]);
        let mut l = opened(&c);
        for i in 0..=255u8 {
            l.push(&f, &c, 0x04, &[i & 1, 0, i, 0, 0xF0]).unwrap();
        }
        let got = f.got.borrow();
        for w in got.windows(2) {
            assert_eq!(w[0].0, w[1].0, "one session");
            let d = w[1].1.wrapping_sub(w[0].1);
            assert!((1..=SEARCH_TRIES).contains(&d), "{d}");
        }
    }

    #[test]
    fn foreign_reports_are_ignored_and_the_reply_is_ours() {
        let c = FakeClock::new();
        let f = Fake::new(&c, &[Board::ForeignFirst]);
        let mut l = opened(&c);
        l.push(&f, &c, 0x03, LINE).unwrap();
    }

    #[test]
    fn silence_is_unknown_after_one_retry_and_drops_qualification() {
        let c = FakeClock::new();
        let f = Fake::new(&c, &[Board::Answer { ms: 20 }, Board::Silent, Board::Silent]);
        let mut l = opened(&c);
        l.probe(&f, &c).unwrap();
        let t0 = c.now();
        assert!(matches!(l.push(&f, &c, 0x03, LINE), Err(Failure::Unknown)));
        assert_eq!(f.written.borrow().len(), 3, "the probe, the push, one retry");
        assert!(!l.qualified());
        // Each attempt waited its deadline (plus the drain's empty read).
        assert!(c.now() - t0 >= DEADLINE * 2);
    }

    #[test]
    fn a_late_answer_completes_nothing() {
        let c = FakeClock::new();
        let late = DEADLINE.as_millis() as u64 + 5;
        let f = Fake::new(&c, &[Board::Answer { ms: late }, Board::Answer { ms: 20 }]);
        let mut l = opened(&c);
        // The first answer misses its deadline; the retry's drain discards it.
        l.push(&f, &c, 0x03, LINE).unwrap();
        assert_eq!(f.written.borrow().len(), 2);
    }

    #[test]
    fn a_bad_crc_is_retried_once_on_a_new_seq() {
        let c = FakeClock::new();
        let f = Fake::new(&c, &[Board::Nak(ST_BADCRC)]);
        let mut l = opened(&c);
        l.push(&f, &c, 0x03, LINE).unwrap();
        let got = f.got.borrow();
        assert_eq!(got.len(), 2);
        assert_ne!(got[0].1, got[1].1);
        let f2 = Fake::new(&c, &[Board::Nak(ST_BADCRC), Board::Nak(ST_BADCRC)]);
        assert!(matches!(l.push(&f2, &c, 0x03, LINE), Err(Failure::BadCrc)));
        assert_eq!(f2.written.borrow().len(), 2, "one retry in total");
    }

    #[test]
    fn incomplete_is_not_retried_and_counts_as_admission() {
        let c = FakeClock::new();
        let f = Fake::new(&c, &[Board::ReceiptOnly, Board::Nak(ST_BUSY)]);
        let mut l = opened(&c);
        assert!(matches!(l.push(&f, &c, 0x03, LINE), Err(Failure::Incomplete)));
        assert_eq!(f.written.borrow().len(), 1);
        // Admitted, so a BUSY now is real contention: no grace.
        assert!(matches!(l.push(&f, &c, 0x03, LINE), Err(Failure::Busy)));
    }

    #[test]
    fn a_corrupt_receipt_is_unknown() {
        let c = FakeClock::new();
        let f = Fake::new(&c, &[Board::Corrupt, Board::Corrupt]);
        let mut l = opened(&c);
        assert!(matches!(l.push(&f, &c, 0x03, LINE), Err(Failure::Unknown)));
    }

    #[test]
    fn forbidden_and_unhandled_are_refusals() {
        let c = FakeClock::new();
        let f = Fake::new(&c, &[Board::Nak(ST_FORBIDDEN), Board::Unhandled]);
        let mut l = opened(&c);
        assert!(matches!(l.push(&f, &c, 0x03, LINE), Err(Failure::Forbidden)));
        assert!(matches!(l.push(&f, &c, 0x03, LINE), Err(Failure::Mismatch)));
        assert_eq!(f.written.borrow().len(), 2, "neither retried");
    }

    #[test]
    fn stale_rotates_through_a_quiet_to_a_new_session() {
        let c = FakeClock::new();
        let f = Fake::new(&c, &[Board::Answer { ms: 20 }, Board::Nak(ST_STALE)]);
        let mut l = opened(&c);
        l.probe(&f, &c).unwrap();
        let first = l.session().unwrap();
        assert!(matches!(l.push(&f, &c, 0x03, LINE), Err(Failure::Stale)));
        assert!(!l.qualified());
        assert_eq!(l.session(), None);
        let n = f.written.borrow().len();
        assert!(matches!(l.probe(&f, &c), Err(Failure::Waiting { .. })));
        assert_eq!(f.written.borrow().len(), n, "nothing sent in the quiet");
        c.advance(QUIET);
        l.probe(&f, &c).unwrap();
        assert_eq!(l.generation(), 2);
        assert_eq!(l.sessions, 2);
        let _ = first; // ids are random; reuse is harmless after the quiet
    }

    #[test]
    fn busy_before_the_first_admission_gets_the_grace_three_times() {
        let c = FakeClock::new();
        let busy = [Board::Nak(ST_BUSY), Board::Nak(ST_BUSY), Board::Nak(ST_BUSY), Board::Nak(ST_BUSY)];
        let f = Fake::new(&c, &busy);
        let mut l = opened(&c);
        for _ in 0..GRACE_TRIES {
            match l.probe(&f, &c) {
                Err(Failure::Waiting { until }) => {
                    let n = f.written.borrow().len();
                    assert!(matches!(l.probe(&f, &c), Err(Failure::Waiting { .. })));
                    assert_eq!(f.written.borrow().len(), n, "nothing sent inside the grace");
                    c.advance(until - c.now());
                }
                other => panic!("{other:?}"),
            }
        }
        assert!(matches!(l.probe(&f, &c), Err(Failure::Busy)));
        assert_eq!(f.written.borrow().len(), 4);
    }

    #[test]
    fn a_new_session_gets_a_new_grace() {
        let c = FakeClock::new();
        let f = Fake::new(&c, &[Board::Nak(ST_BUSY), Board::Nak(ST_STALE), Board::Nak(ST_BUSY)]);
        let mut l = opened(&c);
        assert!(matches!(l.probe(&f, &c), Err(Failure::Waiting { .. })));
        c.advance(GRACE_WAIT);
        assert!(matches!(l.probe(&f, &c), Err(Failure::Stale)));
        c.advance(QUIET);
        assert!(matches!(l.probe(&f, &c), Err(Failure::Waiting { .. })), "the new session's own grace");
    }

    #[test]
    fn the_span_rotates_before_the_board_could_see_a_seq_twice() {
        let c = FakeClock::new();
        let f = Fake::new(&c, &[]);
        let mut l = opened(&c);
        let mut rotated = false;
        for _ in 0..20_000 {
            match l.push(&f, &c, 0x04, &[1, 0, 1, 0, 2]) {
                Ok(()) => {}
                Err(Failure::Waiting { .. }) => {
                    rotated = true;
                    break;
                }
                Err(e) => panic!("{e:?}"),
            }
        }
        assert!(rotated);
        let got = f.got.borrow();
        let first = got[0].1;
        let last = got.last().unwrap().1;
        assert!(u32::from(last.wrapping_sub(first)) < ROTATE_SPAN);
    }

    #[test]
    fn a_dirty_queue_sends_nothing() {
        let c = FakeClock::new();
        let f = Fake::new(&c, &[]);
        for _ in 0..DRAIN_LIMIT + 1 {
            f.deliver(0, vec![0x07, 0x12, 0x03]);
        }
        let mut l = opened(&c);
        assert!(matches!(l.push(&f, &c, 0x03, LINE), Err(Failure::Hid(Error::Dirty { .. }))));
        assert!(f.written.borrow().is_empty());
    }

    #[test]
    fn stuck_ends_it_at_once() {
        let c = FakeClock::new();
        let f = Fake::new(&c, &[Board::Stuck]);
        let mut l = opened(&c);
        assert!(matches!(l.push(&f, &c, 0x03, LINE), Err(Failure::Hid(Error::Stuck))));
        assert_eq!(f.written.borrow().len(), 1, "no retry on a stuck handle");
    }

    #[test]
    fn too_long_is_refused_before_anything() {
        let c = FakeClock::new();
        let f = Fake::new(&c, &[]);
        let mut l = opened(&c);
        assert!(matches!(l.push(&f, &c, 0x03, &[0u8; 24]), Err(Failure::TooLong)));
        l.push(&f, &c, 0x03, &[0x41u8; 23]).unwrap();
    }

    #[test]
    fn an_exhausted_search_rotates_with_nothing_sent() {
        let c = FakeClock::new();
        let f = Fake::new(&c, &[]);
        let mut l = opened(&c);
        l.probe(&f, &c).unwrap();
        l.search_tries = 0;
        assert!(matches!(l.push(&f, &c, 0x03, LINE), Err(Failure::Waiting { .. })));
        assert_eq!(l.session(), None, "rotated");
        assert_eq!(f.written.borrow().len(), 1, "only the probe went");
        l.search_tries = SEARCH_TRIES;
        c.advance(QUIET);
        l.probe(&f, &c).unwrap();
        assert_eq!(l.sessions, 2);
    }

    #[test]
    fn a_transport_failure_after_the_write_drops_qualification() {
        let c = FakeClock::new();
        let f = Fake::new(&c, &[Board::Answer { ms: 20 }, Board::Stuck]);
        let mut l = opened(&c);
        l.probe(&f, &c).unwrap();
        assert!(l.qualified());
        assert!(matches!(l.push(&f, &c, 0x03, LINE), Err(Failure::Hid(Error::Stuck))));
        assert!(!l.qualified());
        let c2 = FakeClock::new();
        let f2 = Fake::new(&c2, &[Board::Answer { ms: 20 }, Board::StuckWrite]);
        let mut l2 = opened(&c2);
        l2.probe(&f2, &c2).unwrap();
        assert!(matches!(l2.push(&f2, &c2, 0x03, LINE), Err(Failure::Hid(Error::Stuck))));
        assert!(!l2.qualified());
    }

    #[test]
    fn every_text_command_reaches_the_board_whole() {
        let c = FakeClock::new();
        let f = Fake::new(&c, &[]);
        let mut l = opened(&c);
        let cases: [(u8, &[u8]); 6] = [
            (0x01, b"\x02Paused"),
            (0x02, b""),
            (0x03, b"\x00\x01Zap Zebra ZZZZZZZZZ"),
            (0x03, b"\x01\x00aaaaaaaaaaaaaaaaaaaaa"),
            (0x04, &[1, 0x5A, 0x5B, 0x5C, 0x61]),
            (0x04, &[0, 0, 0, 0, 0]),
        ];
        for (cmd, body) in cases {
            l.push(&f, &c, cmd, body).unwrap();
            let got = f.got.borrow();
            let inner = &got.last().unwrap().2;
            assert_eq!(&inner[..3], &[0x07, 0x12, cmd]);
            assert_eq!(&inner[3..3 + body.len()], body);
            assert!(inner[3 + body.len()..].iter().all(|&b| b == 0));
        }
    }
}
