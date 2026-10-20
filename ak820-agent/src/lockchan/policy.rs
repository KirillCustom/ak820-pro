//! When a frame is due (`plans/LOCK-LED-CHANNEL-PLAN.md`, the sender design's
//! policy, R2-3/R2-4, R3-5, R3-7, R3-8, R4-2). Pure: the sender thread asks
//! [`Policy::due`] when to wake and reports each attempt back.
//!
//! - **A change** (a new `(type, payload)`, the content identity) is due at
//!   once, and once more [`REPEAT`] after it is sent; a newer change replaces
//!   it, and nothing of the older is ever sent again (its generation is gone).
//! - **A title** is refreshed every [`REFRESH`] while it stands.
//! - **CLEAR** is repeated on the same cadence until [`CLEAR_FOR`] after the
//!   last title was sent (host time: a bounded retry, not proof the board
//!   cleared), then the channel goes quiet.
//! - **A resend** (startup, an acquisition, a resume, the wake guess) makes
//!   the current content due at once, unchanged or not.
//! - **A failed attempt** keeps it due, after 1, 2, 4 ... 30 s; supersession
//!   is not a failure.
//! - **Not before** an instant the caller sets: the handover wait (R4-2).

use std::time::{Duration, Instant};

pub const REPEAT: Duration = Duration::from_secs(2);
pub const REFRESH: Duration = Duration::from_secs(60);
pub const CLEAR_FOR: Duration = Duration::from_secs(180);
const BACKOFF_MAX: Duration = Duration::from_secs(30);
/// Chance losses retried at once before one counts as a failure.
pub const SOFT_TRIES: u32 = 3;

pub type Content = (u8, Vec<u8>);

#[derive(Debug)]
pub struct Policy {
    gen: u64,
    content: Option<Content>,
    /// The next send is due at this instant, if any.
    next: Option<Instant>,
    /// The 2 s repeat still owed for this generation.
    repeat_owed: bool,
    last_title_sent: Option<Instant>,
    backoff: Duration,
    soft: u32,
    not_before: Option<Instant>,
}

impl Default for Policy {
    fn default() -> Self {
        Policy { gen: 0, content: None, next: None, repeat_owed: false, last_title_sent: None, backoff: Duration::ZERO, soft: 0, not_before: None }
    }
}

fn is_clear(c: &Content) -> bool {
    c.0 == super::TYPE_CLEAR
}

impl Policy {
    /// The content now. A different one is a new generation, due at once;
    /// the same one changes nothing (freshness is the caller's, R3-7).
    pub fn set(&mut self, content: Content, now: Instant) -> bool {
        if self.content.as_ref() == Some(&content) {
            return false;
        }
        self.gen += 1;
        self.content = Some(content);
        self.next = Some(now);
        self.repeat_owed = true;
        self.backoff = Duration::ZERO;
        self.soft = 0;
        true
    }

    /// A resend reason: the current content due now.
    pub fn force(&mut self, now: Instant) {
        if self.content.is_some() {
            self.next = Some(self.next.map_or(now, |n| n.min(now)));
        }
    }

    /// A new acquisition of the channel: the current content due now, with
    /// its repeat owed again (codex, implementation review, 11).
    pub fn acquired(&mut self, now: Instant) {
        if self.content.is_some() {
            self.next = Some(now);
            self.repeat_owed = true;
            self.soft = 0;
        }
    }

    /// The handover wait: nothing goes before `t`.
    pub fn not_before(&mut self, t: Instant) {
        self.not_before = Some(self.not_before.map_or(t, |n| n.max(t)));
    }

    pub fn generation(&self) -> u64 {
        self.gen
    }

    /// When the next frame is due, if one is.
    pub fn due(&self) -> Option<Instant> {
        let n = self.next?;
        Some(self.not_before.map_or(n, |nb| n.max(nb)))
    }

    /// What to send for generation `gen`, if it is still current.
    pub fn content(&self, gen: u64) -> Option<&Content> {
        (gen == self.gen).then_some(self.content.as_ref()?)
    }

    /// An attempt for `gen` went out whole.
    pub fn sent(&mut self, gen: u64, now: Instant) {
        if gen != self.gen {
            return; // a late completion of an older generation (R2-3)
        }
        let Some(c) = &self.content else { return };
        self.backoff = Duration::ZERO;
        self.soft = 0;
        if !is_clear(c) {
            self.last_title_sent = Some(now);
        }
        let follow = if self.repeat_owed {
            self.repeat_owed = false;
            REPEAT
        } else {
            REFRESH
        };
        let at = now + follow;
        self.next = if is_clear(c) {
            // CLEAR only until CLEAR_FOR after the last title went; with no
            // title in this process's history, once and its repeat.
            match self.last_title_sent {
                Some(t) if at <= t + CLEAR_FOR => Some(at),
                _ if follow == REPEAT => Some(at),
                _ => None,
            }
        } else {
            Some(at)
        };
    }

    /// An attempt for `gen` failed (not superseded): due again after a
    /// growing wait.
    pub fn failed(&mut self, gen: u64, now: Instant) {
        if gen != self.gen {
            return;
        }
        self.soft = 0;
        self.backoff = (self.backoff * 2).clamp(Duration::from_secs(1), BACKOFF_MAX);
        self.next = Some(now + self.backoff);
    }

    /// An attempt lost to chance, not a fault: a missed phase deadline (the
    /// Mac's BLE writes spike to 88-428 ms, so ~1 frame in 4 misses one,
    /// measured 10-20) or the user's lock changing mid-frame. Due again at
    /// once (the frame's own recovery wait spaces it), without growing the
    /// backoff; after [`SOFT_TRIES`] in a row it counts as a failure.
    pub fn retry(&mut self, gen: u64, now: Instant) {
        if gen != self.gen {
            return;
        }
        self.soft += 1;
        if self.soft > SOFT_TRIES {
            self.failed(gen, now);
        } else {
            self.next = Some(now);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn title(s: &str) -> Content {
        (super::super::TYPE_NOW_PLAYING, s.as_bytes().to_vec())
    }
    fn clear() -> Content {
        (super::super::TYPE_CLEAR, vec![])
    }

    #[test]
    fn a_change_is_due_now_then_2_s_then_every_60() {
        let t0 = Instant::now();
        let mut p = Policy::default();
        assert!(p.set(title("A"), t0));
        assert_eq!(p.due(), Some(t0));
        let g = p.generation();
        p.sent(g, t0 + Duration::from_secs(3));
        assert_eq!(p.due(), Some(t0 + Duration::from_secs(5)));
        p.sent(g, t0 + Duration::from_secs(8));
        assert_eq!(p.due(), Some(t0 + Duration::from_secs(68)));
        p.sent(g, t0 + Duration::from_secs(71));
        assert_eq!(p.due(), Some(t0 + Duration::from_secs(131)));
    }

    #[test]
    fn the_same_content_is_not_a_change() {
        let t0 = Instant::now();
        let mut p = Policy::default();
        p.set(title("A"), t0);
        let g = p.generation();
        p.sent(g, t0);
        assert!(!p.set(title("A"), t0 + Duration::from_secs(1)));
        assert_eq!(p.generation(), g);
        assert_eq!(p.due(), Some(t0 + REPEAT));
    }

    #[test]
    fn a_newer_change_drops_the_older_generation_and_its_late_completion() {
        let t0 = Instant::now();
        let mut p = Policy::default();
        p.set(title("A"), t0);
        let a = p.generation();
        p.set(title("B"), t0 + Duration::from_secs(1));
        let b = p.generation();
        assert!(p.content(a).is_none());
        assert_eq!(p.content(b), Some(&title("B")));
        // A's frame finishing late changes nothing for B.
        p.sent(a, t0 + Duration::from_secs(2));
        assert_eq!(p.due(), Some(t0 + Duration::from_secs(1)));
        p.failed(a, t0 + Duration::from_secs(2));
        assert_eq!(p.due(), Some(t0 + Duration::from_secs(1)));
    }

    #[test]
    fn clear_repeats_until_180_s_after_the_last_title_then_stops() {
        let t0 = Instant::now();
        let mut p = Policy::default();
        p.set(title("A"), t0);
        p.sent(p.generation(), t0);
        p.set(clear(), t0 + Duration::from_secs(10));
        let g = p.generation();
        let mut t = t0 + Duration::from_secs(10);
        let mut sends = 0;
        while let Some(d) = p.due() {
            t = d.max(t);
            p.sent(g, t);
            sends += 1;
            assert!(sends < 20);
        }
        assert!(t <= t0 + CLEAR_FOR, "{:?}", t - t0);
        assert!(sends >= 3, "the change, its repeat and refreshes: {sends}");
    }

    #[test]
    fn clear_with_no_title_in_history_goes_twice() {
        let t0 = Instant::now();
        let mut p = Policy::default();
        p.set(clear(), t0);
        let g = p.generation();
        p.sent(g, t0);
        assert_eq!(p.due(), Some(t0 + REPEAT));
        p.sent(g, t0 + REPEAT);
        assert_eq!(p.due(), None);
        // A resend reason still sends it.
        p.force(t0 + Duration::from_secs(50));
        assert_eq!(p.due(), Some(t0 + Duration::from_secs(50)));
    }

    #[test]
    fn failures_back_off_and_a_success_resets() {
        let t0 = Instant::now();
        let mut p = Policy::default();
        p.set(title("A"), t0);
        let g = p.generation();
        let mut waits = vec![];
        let mut t = t0;
        for _ in 0..7 {
            p.failed(g, t);
            waits.push((p.due().unwrap() - t).as_secs());
            t = p.due().unwrap();
        }
        assert_eq!(waits, [1, 2, 4, 8, 16, 30, 30]);
        p.sent(g, t);
        p.failed(g, t);
        assert_eq!(p.due(), Some(t + Duration::from_secs(1)));
    }

    #[test]
    fn chance_losses_retry_at_once_then_back_off() {
        let t0 = Instant::now();
        let mut p = Policy::default();
        p.set(title("A"), t0);
        let g = p.generation();
        for i in 1..=SOFT_TRIES {
            let t = t0 + Duration::from_secs(i as u64);
            p.retry(g, t);
            assert_eq!(p.due(), Some(t), "try {i}");
        }
        let t = t0 + Duration::from_secs(10);
        p.retry(g, t);
        assert_eq!(p.due(), Some(t + Duration::from_secs(1)), "the fourth backs off");
        p.sent(g, t);
        p.retry(g, t);
        assert_eq!(p.due(), Some(t), "a success resets the count");
    }

    #[test]
    fn force_and_not_before() {
        let t0 = Instant::now();
        let mut p = Policy::default();
        p.force(t0);
        assert_eq!(p.due(), None, "nothing to send yet");
        p.set(title("A"), t0);
        p.sent(p.generation(), t0);
        p.force(t0 + Duration::from_secs(1));
        assert_eq!(p.due(), Some(t0 + Duration::from_secs(1)));
        p.not_before(t0 + Duration::from_secs(12));
        assert_eq!(p.due(), Some(t0 + Duration::from_secs(12)));
    }
}
