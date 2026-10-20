//! The lock-LED channel's sender: a thread of its own, fed by the daemon's
//! loop (`plans/LOCK-LED-CHANNEL-PLAN.md`, the sender design and R2-R4, and
//! codex's implementation review of 10-20).
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
//! **kept for the life of the process if a write or an open is stuck**.
//!
//! Only the platform value moves to the thread: the lights are opened there
//! and never leave it (the Mac's device is scheduled on the opening thread's
//! run loop), so `LockLights` need not be `Send`.

use std::any::Any;
use std::cell::Cell;
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

use super::frame::{self, Abort, Control, Line, LockLights, Outcome, Payload, Time, Why};
use super::policy::{Content, Policy};

/// Past this without a fresh observation the content becomes CLEAR (R3-7).
pub const STALE_AFTER: Duration = Duration::from_secs(30);
/// The handover's wait after the daemon's last raw-HID media request (R4-2).
pub const HANDOVER: Duration = Duration::from_secs(12);
/// Absent keyboard: the list is read again this often (R3-8).
pub const RELIST: Duration = Duration::from_secs(10);
/// The wake guess: armed past this much input idleness (R3-8, R4).
pub const IDLE_ARM: Duration = Duration::from_secs(30);
/// How often a pending restore is tried, and the wake guess and the locks'
/// validity are sampled while the channel is granted.
pub const TICK: Duration = Duration::from_secs(1);

/// What the thread needs from the platform, beyond the lights themselves.
pub trait Platform {
    type Lights: LockLights;
    /// The board's keyboard: `Ok(None)` when not listed.
    fn open(&mut self) -> Result<Option<Self::Lights>, String>;
    /// An open left an operation the kernel still owns (a stuck query): the
    /// channel is poisoned for the life of the process, its lock kept
    /// (implementation review, 6). Default: never.
    fn poisoned(&self) -> bool {
        false
    }
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
    /// Frames lost to chance and retried at once (a deadline, a lock change).
    pub retries: u64,
    pub last_sent: Option<Instant>,
    pub last_error: Option<String>,
    pub restore_pending: bool,
    pub writes: u64,
}

/// What the panel should show, as the loop sees it each poll: the text frame
/// (type 1 or 2, its icon included), the icon on its own, and the playback
/// readout while playing (`lockchan::Playback::of`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Media {
    pub text: Content,
    pub icon: crate::text::Icon,
    pub pb: Option<super::Playback>,
}

impl Media {
    /// From a snapshot, as the daemon's loop builds it.
    pub fn of(snapshot: &crate::smtc::Snapshot) -> Media {
        Media { text: super::content(snapshot), icon: snapshot.icon, pb: super::Playback::of(snapshot) }
    }
}

#[derive(Default)]
struct Slot {
    media: Option<Media>,
    /// Bumped on every different text identity (the lines) and transport
    /// identity (icon, playing, duration): each frame's own generation check,
    /// so A, B, A is two changes, not none (implementation review, 10).
    text_epoch: u64,
    tr_epoch: u64,
    observed: Option<Instant>,
    /// When the media's position was true (codex, play times, P2-2): apart
    /// from `observed`, the staleness clock.
    pos_at: Option<Instant>,
    granted: bool,
    owner_gen: u64,
    force: bool,
    not_before: Option<Instant>,
    stop: bool,
    done: bool,
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

    /// The media now, when the source last observed it, and when its
    /// position was true.
    pub fn set(&self, media: Media, observed: Instant, pos_at: Instant) {
        self.with(|s| {
            let (t, r) = (text_id(&media.text), tr_id(&media));
            match &s.media {
                Some(old) => {
                    if text_id(&old.text) != t {
                        s.text_epoch += 1;
                    }
                    if tr_id(old) != r {
                        s.tr_epoch += 1;
                    }
                }
                None => {
                    s.text_epoch += 1;
                    s.tr_epoch += 1;
                }
            }
            s.media = Some(media);
            s.observed = Some(observed);
            s.pos_at = Some(pos_at);
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

    /// Stop, and wait up to `budget` for the thread's one bounded restore and
    /// its exit (R3-3, R4-4); past it, the thread is left detached.
    pub fn stop_and_wait(&self, budget: Duration) -> bool {
        self.stop();
        let deadline = Instant::now() + budget;
        let mut s = self.0.slot.lock().unwrap_or_else(|e| e.into_inner());
        while !s.done {
            let left = deadline.saturating_duration_since(Instant::now());
            if left.is_zero() {
                return false;
            }
            s = self.0.cv.wait_timeout(s, left).unwrap_or_else(|e| e.into_inner()).0;
        }
        true
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

/// The text frame's identity: its lines, never its icon (the icon rides in
/// type 3).
fn text_id(text: &Content) -> Content {
    super::text_identity(text.0, &text.1)
}

/// The transport's identity as policy content: icon, playing, duration.
fn tr_id(m: &Media) -> Content {
    let t = super::transport_identity(m.icon, m.pb);
    let mut v = vec![t.icon, t.playing as u8];
    v.extend_from_slice(&t.dur_s.to_be_bytes());
    (super::TYPE_TRANSPORT, v)
}

/// What the slot asks for now: the media made idle (CLEAR, no readout) once
/// its observation is stale, and when that happens.
fn current_of(slot: &Slot, now: Instant) -> (Option<(Media, Instant)>, Option<Instant>) {
    let Some(m) = slot.media.clone() else { return (None, None) };
    let pos_at = slot.pos_at.unwrap_or(now);
    match slot.observed {
        Some(o) if now.saturating_duration_since(o) < STALE_AFTER => (Some((m, pos_at)), Some(o + STALE_AFTER)),
        _ => (
            Some((Media { text: (super::TYPE_CLEAR, Vec::new()), icon: crate::text::Icon::None, pb: None }, pos_at)),
            None,
        ),
    }
}

/// Which frame a step sends.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Kind {
    Text,
    Transport,
}

/// What a frame picked, for its in-flight checks and its credit.
#[derive(Clone, Copy, Debug)]
struct Picked {
    gen: u64,
    text_epoch: u64,
    tr_epoch: u64,
    /// The picked identity, hashed (`Cell` wants `Copy`).
    id: u64,
    /// A transport's position, and when it was picked.
    pos: Option<u32>,
    at: Instant,
}

fn id_hash(c: &Content) -> u64 {
    use std::hash::{Hash, Hasher};
    let mut h = std::collections::hash_map::DefaultHasher::new();
    c.hash(&mut h);
    h.finish()
}

/// The thread's state: everything [`Core::step`] carries between calls.
pub struct Core<P: Platform> {
    platform: P,
    lights: Option<P::Lights>,
    guard: Option<Box<dyn Any + Send>>,
    owner_seen: u64,
    /// Type 1/2: when the lines change, refresh every 60 s.
    text: Policy,
    /// Type 3: when the icon, playing or duration change, a seek, after
    /// every text frame (type 1 turns the board's playback off), refresh.
    tr: Policy,
    /// The text generation last sent whole: type 3 goes only while a title
    /// is shown (the Mac session: it cannot bring back an icon after a clear).
    text_sent: Option<u64>,
    /// The position the last type 3 carried and when it was picked, for the
    /// seek check.
    sent_pos: Option<(u32, Instant)>,
    line: Line,
    next_open: Option<Instant>,
    armed: Option<u32>,
    locks_known: bool,
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

/// Whether the frame in flight is still wanted: the slot's stop, its owner
/// generation and its content epoch, read with the lock released before
/// anything else is asked.
struct Ctl<'a> {
    h: &'a Handle,
    owner: u64,
}

impl Ctl<'_> {
    /// Stop or ownership, and the slot's two epochs.
    fn check(&self) -> (Option<Abort>, u64, u64) {
        let s = self.h.0.slot.lock().unwrap_or_else(|e| e.into_inner());
        let a = if s.stop {
            Some(Abort::Stop)
        } else if !(s.granted && s.owner_gen == self.owner) {
            Some(Abort::Revoked)
        } else {
            None
        };
        (a, s.text_epoch, s.tr_epoch)
    }
}

/// A restore needs no ownership: it only puts the user's lights back.
struct Always<'a>(&'a Handle);

impl Control for Always<'_> {
    fn abort(&self) -> Option<Abort> {
        let s = self.0 .0.slot.lock().unwrap_or_else(|e| e.into_inner());
        s.stop.then_some(Abort::Stop)
    }
}

impl<P: Platform> Core<P> {
    pub fn new(platform: P) -> Core<P> {
        Core {
            platform,
            lights: None,
            guard: None,
            owner_seen: 0,
            text: Policy::default(),
            tr: Policy::default(),
            text_sent: None,
            sent_pos: None,
            line: Line::default(),
            next_open: None,
            armed: None,
            locks_known: true,
            poisoned: false,
            seq: 0,
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

    /// Stuck: the kernel owns a buffer, and so does this process the device.
    fn poison(&mut self, h: &Handle) -> Next {
        self.poisoned = true;
        std::mem::forget(self.lights.take());
        std::mem::forget(self.guard.take());
        self.publish(h, |st| {
            st.state = "poisoned";
            st.failures += 1;
            st.last_error = Some("a write or an open is stuck: the channel is off until the daemon restarts".into());
        });
        Next::Exit
    }

    /// The lights, opened if they are not: `Err(next)` when the step must
    /// wait or exit instead.
    fn ensure_open(&mut self, h: &Handle, now: Instant, for_restore: bool) -> Result<(), Next> {
        if self.lights.is_some() {
            return Ok(());
        }
        if let Some(t) = self.next_open {
            if t > now {
                return Err(Next::WaitUntil(t));
            }
        }
        match self.platform.open() {
            Ok(Some(l)) => {
                self.lights = Some(l);
                self.next_open = None;
                Ok(())
            }
            Ok(None) => {
                self.publish(h, |st| st.state = "absent");
                self.next_open = Some(now + RELIST);
                Err(Next::WaitUntil(now + RELIST))
            }
            Err(e) => {
                if self.platform.poisoned() {
                    return Err(self.poison(h));
                }
                self.publish(h, |st| {
                    st.failures += 1;
                    st.last_error = Some(e);
                });
                // A restore keeps its own one-second schedule, never the
                // payload's backoff or handover (verify, 5).
                let t = if for_restore {
                    now + TICK
                } else {
                    self.text.failed(self.text.generation(), now);
                    self.text.due().unwrap_or(now + RELIST)
                };
                self.next_open = Some(t);
                Err(Next::WaitUntil(t))
            }
        }
    }

    /// One pass: update from the slot, service a pending restore, and send a
    /// frame if one is due: text first, then the transport.
    pub fn step<T: Time>(&mut self, h: &Handle, time: &T) -> Next {
        if self.poisoned {
            return Next::Exit;
        }
        let now = time.now();
        let woke = self.wake_guess();
        let (granted, owner, stop, stale_at, view) = {
            let mut s = h.0.slot.lock().unwrap_or_else(|e| e.into_inner());
            let (view, stale_at) = current_of(&s, now);
            if let Some((m, _)) = &view {
                self.text.set(text_id(&m.text), now);
                self.tr.set(tr_id(m), now);
            }
            if std::mem::take(&mut s.force) || woke {
                self.text.force(now);
                self.tr.force(now);
            }
            if let Some(nb) = s.not_before.take() {
                self.text.not_before(nb);
                self.tr.not_before(nb);
            }
            (s.granted, s.owner_gen, s.stop, stale_at, view)
        };
        // A seek: the position is not where the last type 3 said it would be.
        if let (Some((m, pos_at)), Some((sent, at))) = (&view, self.sent_pos) {
            if let Some(pb) = m.pb {
                let pos_now = pb.pos_s.saturating_add(now.saturating_duration_since(*pos_at).as_secs() as u32);
                if super::is_seek(sent, now.saturating_duration_since(at), pos_now, pb.dur_s) {
                    self.tr.force(now);
                    self.sent_pos = None;
                }
            }
        }
        if stop {
            // One bounded restore if one is owed and the lights are open,
            // its waits through the stop flag (verify, 1).
            if self.line.restore_pending {
                if let Some(l) = self.lights.as_mut() {
                    if let Err(Outcome::Poisoned) = frame::restore_only(l, time, &Always(h), &mut self.line, true) {
                        return self.poison(h);
                    }
                }
            }
            self.release_all();
            return Next::Exit;
        }
        if granted && owner != self.owner_seen {
            // A new acquisition: the current content again, with its repeat
            // (implementation review, 11); the board's state is unknown.
            self.owner_seen = owner;
            self.text.acquired(now);
            self.tr.acquired(now);
            self.text_sent = None;
            self.sent_pos = None;
        }

        // A pending restore goes first, granted or not, every TICK, until it
        // is confirmed (R3-1; implementation review, 1).
        if self.line.restore_pending {
            if let Err(next) = self.ensure_open(h, now, true) {
                return next;
            }
            let lights = self.lights.as_mut().expect("opened");
            match frame::restore_only(lights, time, &Always(h), &mut self.line, false) {
                Ok(true) => {}
                Ok(false) => {
                    self.publish_counts(h, "restore pending");
                    return Next::WaitUntil(time.now() + TICK);
                }
                Err(Outcome::Poisoned) => return self.poison(h),
                Err(Outcome::Abandoned(Why::Abort(Abort::Stop))) => {
                    // Stopped mid-restore: once more, through the flag.
                    let lights = self.lights.as_mut().expect("opened");
                    if let Err(Outcome::Poisoned) = frame::restore_only(lights, time, &Always(h), &mut self.line, true) {
                        return self.poison(h);
                    }
                    self.release_all();
                    return Next::Exit;
                }
                Err(_) => {
                    self.lights = None;
                    self.publish_counts(h, "restore pending");
                    return Next::WaitUntil(time.now() + TICK);
                }
            }
        }

        if !granted {
            self.release();
            self.publish_counts(h, "off");
            return Next::Wait;
        }

        // Granted: sample the wake guess, the locks' validity and staleness on
        // their own clocks, not only when a frame is due (review, 9).
        if let Some(l) = self.lights.as_mut() {
            if l.user().is_none() {
                self.locks_known = false;
                self.publish_counts(h, "locks unknown");
                return Next::WaitUntil(now + TICK);
            }
            if !self.locks_known {
                // Known again (an unlock, a reconnect): a resend reason.
                self.locks_known = true;
                self.text.force(now);
                self.tr.force(now);
            }
        }
        let wake_at = now + TICK;
        let stale_wait = stale_at.filter(|t| *t > now);
        let soonest = |due: Option<Instant>| {
            [due, Some(wake_at), stale_wait].into_iter().flatten().min().expect("wake_at is always there")
        };
        // Type 3 only while a title is shown: the current text generation sent
        // whole, and a now-playing one.
        let shown = view.as_ref().is_some_and(|(m, _)| m.text.0 == super::TYPE_NOW_PLAYING)
            && self.text_sent == Some(self.text.generation());
        let text_due = self.text.due();
        let tr_due = if shown { self.tr.due() } else { None };
        let kind = match (text_due, tr_due) {
            (Some(t), _) if t <= now => Kind::Text,
            (_, Some(t)) if t <= now => Kind::Transport,
            _ => {
                let due = [text_due, tr_due].into_iter().flatten().min();
                self.publish_counts(h, if due.is_some() { "waiting" } else { "idle" });
                return Next::WaitUntil(soonest(due));
            }
        };
        if self.guard.is_none() {
            match self.platform.lock() {
                Ok(Some(g)) => self.guard = Some(g),
                Ok(None) => {
                    self.publish(h, |st| {
                        st.state = "lock held elsewhere";
                        st.last_error = Some("another process holds the lock-LED lock".into());
                    });
                    return Next::WaitUntil(now + RELIST);
                }
                Err(e) => {
                    self.publish(h, |st| {
                        st.failures += 1;
                        st.last_error = Some(e);
                    });
                    return Next::WaitUntil(now + RELIST);
                }
            }
        }
        if let Err(next) = self.ensure_open(h, now, false) {
            return next;
        }

        if self.lights.as_mut().expect("opened above").user().is_none() {
            self.locks_known = false;
            self.publish_counts(h, "locks unknown");
            return Next::WaitUntil(now + TICK);
        }
        if !self.locks_known {
            // Known again (an unlock, a reconnect): a resend reason (review, 9).
            self.locks_known = true;
            self.text.force(now);
            self.tr.force(now);
        }

        self.publish_counts(h, "sending");
        let seq = self.seq;
        // The newest media is picked after the warm-up and the quiet, and its
        // epochs and identity are what the frame then guards (R4-5; review,
        // 10; verify, 3). A transport frame is also cut by a text change.
        let picked: Cell<Option<Picked>> = Cell::new(None);
        let out = {
            struct Picking<'a, T: Time> {
                ctl: Ctl<'a>,
                kind: Kind,
                picked: &'a Cell<Option<Picked>>,
                time: &'a T,
            }
            impl<T: Time> Control for Picking<'_, T> {
                fn abort(&self) -> Option<Abort> {
                    let (a, te, re) = self.ctl.check();
                    if a.is_some() {
                        return a;
                    }
                    let Some(p) = self.picked.get() else { return None };
                    if te != p.text_epoch || (self.kind == Kind::Transport && re != p.tr_epoch) {
                        return Some(Abort::Superseded);
                    }
                    // Still fresh: an identity gone stale mid-frame is
                    // superseded by the idle that replaces it.
                    let s = self.ctl.h.0.slot.lock().unwrap_or_else(|e| e.into_inner());
                    let now_id = current_of(&s, self.time.now()).0.map(|(m, _)| match self.kind {
                        Kind::Text => text_id(&m.text),
                        Kind::Transport => tr_id(&m),
                    });
                    (now_id.map(|c| id_hash(&c)) != Some(p.id)).then_some(Abort::Superseded)
                }
            }
            let ctl = Picking { ctl: Ctl { h, owner }, kind, picked: &picked, time };
            let lights = self.lights.as_mut().expect("opened above");
            let (text, tr) = (&mut self.text, &mut self.tr);
            let line = &mut self.line;
            frame::run(lights, time, &ctl, line, || {
                let at = time.now();
                let (view, te, re) = {
                    let s = h.0.slot.lock().unwrap_or_else(|e| e.into_inner());
                    (current_of(&s, at).0, s.text_epoch, s.tr_epoch)
                };
                let (m, pos_at) = view?;
                let (gen, id, payload, pos) = match kind {
                    Kind::Text => {
                        let id = text_id(&m.text);
                        text.set(id.clone(), at);
                        (text.generation(), id, m.text.clone(), None)
                    }
                    Kind::Transport => {
                        let id = tr_id(&m);
                        tr.set(id.clone(), at);
                        let pos = m.pb.map(|pb| pb.pos_s.saturating_add(at.saturating_duration_since(pos_at).as_secs() as u32));
                        (tr.generation(), id, super::transport(m.icon, m.pb, pos.unwrap_or(0)), pos)
                    }
                };
                picked.set(Some(Picked { gen, text_epoch: te, tr_epoch: re, id: id_hash(&id), pos, at }));
                Some(Payload { kind: payload.0, seq, bytes: payload.1 })
            })
        };
        let done = time.now();
        let p = picked.get();
        let policy = match kind {
            Kind::Text => &mut self.text,
            Kind::Transport => &mut self.tr,
        };
        let gen = p.map_or(policy.generation(), |p| p.gen);
        // Credited only if the owner, the epochs and the identity's freshness
        // are all still the frame's (verify, 4).
        let credited = p.is_some_and(|p| {
            let s = h.0.slot.lock().unwrap_or_else(|e| e.into_inner());
            let id = current_of(&s, done).0.map(|(m, _)| match kind {
                Kind::Text => text_id(&m.text),
                Kind::Transport => tr_id(&m),
            });
            s.granted
                && s.owner_gen == owner
                && s.text_epoch == p.text_epoch
                && (kind == Kind::Text || s.tr_epoch == p.tr_epoch)
                && id.map(|c| id_hash(&c)) == Some(p.id)
        });
        match out {
            Outcome::Sent if credited => {
                self.seq = self.seq.wrapping_add(1);
                policy.sent(gen, done);
                match kind {
                    Kind::Text => {
                        self.text_sent = Some(gen);
                        // Type 1 turned the board's readout off: the
                        // transport goes right after (the Mac session).
                        self.tr.force(done);
                        self.sent_pos = None;
                    }
                    Kind::Transport => {
                        self.sent_pos = p.and_then(|p| p.pos.map(|pos| (pos, p.at)));
                    }
                }
                self.publish(h, |st| {
                    st.frames += 1;
                    st.last_sent = Some(done);
                });
            }
            Outcome::Sent => {}
            Outcome::Unknown => {
                self.locks_known = false;
                self.publish_counts(h, "locks unknown");
                return Next::WaitUntil(done + TICK);
            }
            Outcome::Poisoned => return self.poison(h),
            Outcome::Abandoned(why) => match why {
                Why::Abort(Abort::Superseded) | Why::Abort(Abort::Revoked) => {}
                Why::Abort(Abort::Stop) => {
                    self.release_unless_pending();
                    self.publish_counts(h, "stopped");
                    return Next::Exit;
                }
                Why::Write(e) => {
                    // The handle may be bad: drop it; the next frame reopens.
                    self.lights = None;
                    policy.failed(gen, done);
                    self.publish(h, |st| {
                        st.failures += 1;
                        st.last_error = Some(e);
                    });
                }
                Why::Deadline | Why::UserChanged => {
                    policy.retry(gen, done);
                    self.publish(h, |st| st.retries += 1);
                }
                Why::RestorePending => {}
            },
        }
        self.publish_counts(h, "waiting");
        Next::WaitUntil(done)
    }

    fn release(&mut self) {
        if self.line.restore_pending {
            return; // the lights and the lock stay until the restore is done
        }
        self.release_all();
    }

    /// At exit: whatever is owed, the thread is ending.
    fn release_all(&mut self) {
        self.lights = None;
        self.guard = None;
    }

    fn release_unless_pending(&mut self) {
        self.release();
    }

    fn publish(&self, h: &Handle, f: impl FnOnce(&mut Status)) {
        let mut s = h.0.slot.lock().unwrap_or_else(|e| e.into_inner());
        f(&mut s.status);
        s.status.restore_pending = self.line.restore_pending;
        s.status.writes = self.line.writes;
    }

    fn publish_counts(&self, h: &Handle, state: &'static str) {
        self.publish(h, |st| st.state = state);
    }
}

/// Wall-clock time whose sleeps end early on a stop.
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
    fn sleep_through(&self, t: Instant) {
        std::thread::sleep(t.saturating_duration_since(Instant::now()));
    }
}

/// Start the sender thread. Only `platform` moves to it.
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
                // A stop that came between the step and this lock: step again
                // at once, which exits (codex, verify 2).
                if s.stop && next != Next::Exit {
                    continue;
                }
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
            theirs.with(|s| s.done = true);
        })
        .map_err(|e| format!("spawning the lock-LED thread: {e}"))?;
    Ok(h)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::lockchan::frame::WriteError;
    use crate::lockchan::Profile;
    use std::cell::RefCell;
    use std::rc::Rc;


    impl Handle {
        /// A text frame and no readout, its icon from the payload.
        fn set_text(&self, text: Content, observed: Instant) {
            let icon = match text.1.first() {
                Some(1) if text.0 == super::super::TYPE_NOW_PLAYING => crate::text::Icon::Play,
                Some(2) if text.0 == super::super::TYPE_NOW_PLAYING => crate::text::Icon::Pause,
                _ => crate::text::Icon::None,
            };
            self.set(Media { text, icon, pb: None }, observed, observed);
        }
    }

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

    /// What the fake keyboard does, shared with the test.
    #[derive(Default)]
    struct Kb {
        writes: Vec<(Instant, u8)>,
        user: u8,
        unknown: bool,
        fail: bool,
        stuck: bool,
        /// Called after each write with its index: the test's hook to change
        /// the slot mid-frame.
        hook: Option<Box<dyn FnMut(usize)>>,
    }

    struct Lights {
        kb: Rc<RefCell<Kb>>,
        t: Rc<FakeTime>,
    }
    impl LockLights for Lights {
        fn profile(&self) -> Profile {
            Profile::P0
        }
        fn spacing(&self) -> Duration {
            Duration::from_millis(16)
        }
        fn user(&mut self) -> Option<u8> {
            let k = self.kb.borrow();
            (!k.unknown).then_some(k.user)
        }
        fn write(&mut self, v: u8) -> Result<(), WriteError> {
            {
                let k = self.kb.borrow();
                if k.stuck {
                    return Err(WriteError::Stuck);
                }
                if k.fail {
                    self.t.0.set(self.t.0.get() + Duration::from_secs(1));
                    return Err(WriteError::Failed("gone".into()));
                }
            }
            self.t.0.set(self.t.0.get() + Duration::from_millis(1));
            let i = {
                let mut k = self.kb.borrow_mut();
                k.writes.push((self.t.0.get(), v));
                k.writes.len() - 1
            };
            let hook = self.kb.borrow_mut().hook.take();
            if let Some(mut f) = hook {
                f(i);
                self.kb.borrow_mut().hook = Some(f);
            }
            Ok(())
        }
    }

    struct Plat {
        kb: Rc<RefCell<Kb>>,
        t: Rc<FakeTime>,
        present: bool,
        opens: usize,
        open_stuck: bool,
        input: Rc<Cell<Option<(u32, u32)>>>,
    }
    unsafe impl Send for Plat {}
    impl Platform for Plat {
        type Lights = Lights;
        fn open(&mut self) -> Result<Option<Lights>, String> {
            self.opens += 1;
            if self.open_stuck {
                return Err("query: Stuck".into());
            }
            Ok(self.present.then(|| Lights { kb: self.kb.clone(), t: self.t.clone() }))
        }
        fn poisoned(&self) -> bool {
            self.open_stuck
        }
        fn lock(&mut self) -> Result<Option<Box<dyn Any + Send>>, String> {
            Ok(Some(Box::new(())))
        }
        fn input(&self) -> Option<(u32, u32)> {
            self.input.get()
        }
    }

    struct Rig {
        core: Core<Plat>,
        h: Handle,
        t: Rc<FakeTime>,
        kb: Rc<RefCell<Kb>>,
        input: Rc<Cell<Option<(u32, u32)>>>,
    }

    fn rig() -> Rig {
        let t = Rc::new(FakeTime(Cell::new(Instant::now())));
        let kb = Rc::new(RefCell::new(Kb::default()));
        let input = Rc::new(Cell::new(None));
        let p = Plat { kb: kb.clone(), t: t.clone(), present: true, opens: 0, open_stuck: false, input: input.clone() };
        Rig { core: Core::new(p), h: Handle::new(), t, kb, input }
    }

    fn title(s: &str) -> Content {
        let mut p = vec![1u8, s.len() as u8];
        p.extend_from_slice(s.as_bytes());
        (super::super::TYPE_NOW_PLAYING, p)
    }

    /// Step until the core rests past `until`, advancing fake time.
    fn run_until(r: &mut Rig, until: Instant) -> Next {
        for _ in 0..10_000 {
            match r.core.step(&r.h, &*r.t) {
                Next::WaitUntil(w) if w <= until => {
                    r.t.sleep_until(w);
                }
                other => return other,
            }
        }
        panic!("no rest");
    }

    /// Decode what the board would have committed from the writes (the
    /// encoder's inverse is the receiver's job; here the last frame's bytes
    /// are matched against the encoder's output).
    fn sent(r: &Rig, c: &Content, user: u8) -> bool {
        let w: Vec<u8> = r.kb.borrow().writes.iter().map(|w| w.1).collect();
        (0..=255u8).any(|seq| {
            let e = crate::lockchan::encode(&c.1, c.0, seq, Profile::P0, user).unwrap();
            w.windows(e.len()).any(|x| x == &e[..])
        })
    }

    fn granted(r: &mut Rig, c: Content) -> Instant {
        let t0 = r.t.now();
        r.h.set_text(c, t0);
        r.h.grant(Some(t0 - HANDOVER), t0);
        t0
    }

    #[test]
    fn nothing_goes_until_granted_then_after_the_handover_wait() {
        let mut r = rig();
        let t0 = r.t.now();
        r.h.set_text(title("A"), t0);
        assert_eq!(r.core.step(&r.h, &*r.t), Next::Wait);
        assert!(r.kb.borrow().writes.is_empty());
        r.h.grant(None, t0);
        assert!(matches!(r.core.step(&r.h, &*r.t), Next::WaitUntil(_)));
        assert!(r.kb.borrow().writes.is_empty(), "no history: the full 12 s wait");
        r.h.set_text(title("A"), t0 + HANDOVER);
        r.t.0.set(t0 + HANDOVER);
        run_until(&mut r, t0 + HANDOVER + Duration::from_secs(1));
        assert!(sent(&r, &title("A"), 0));
        assert_eq!(r.h.status().frames, 1);
        assert_eq!(r.h.status().state, "waiting", "not left at sending");
    }

    #[test]
    fn stale_content_becomes_clear_on_its_own_schedule() {
        let mut r = rig();
        let t0 = granted(&mut r, title("A"));
        run_until(&mut r, t0 + Duration::from_secs(5));
        assert!(sent(&r, &title("A"), 0));
        // No more observations: CLEAR goes at the staleness, not at the
        // 60 s refresh.
        run_until(&mut r, t0 + STALE_AFTER + Duration::from_secs(6));
        assert!(sent(&r, &(super::super::TYPE_CLEAR, vec![]), 0));
    }

    #[test]
    fn a_newer_content_mid_frame_supersedes_it_and_goes_next() {
        let mut r = rig();
        let t0 = granted(&mut r, title("Alpha"));
        let h = r.h.clone();
        let t = r.t.clone();
        r.kb.borrow_mut().hook = Some(Box::new(move |i| {
            if i == 20 {
                h.set_text(title("Bravo"), t.now());
            }
        }));
        run_until(&mut r, t0 + Duration::from_secs(20));
        assert!(!sent(&r, &title("Alpha"), 0), "A was abandoned");
        assert!(sent(&r, &title("Bravo"), 0));
    }

    #[test]
    fn a_b_a_is_two_changes_not_none() {
        let mut r = rig();
        let t0 = granted(&mut r, title("Alpha"));
        let h = r.h.clone();
        let t = r.t.clone();
        r.kb.borrow_mut().hook = Some(Box::new(move |i| {
            if i == 20 {
                h.set_text(title("Bravo"), t.now());
                h.set_text(title("Alpha"), t.now());
            }
        }));
        run_until(&mut r, t0 + Duration::from_secs(20));
        // The first Alpha was cut off at write 20 (the old byte comparison
        // saw no change and finished it): the next write is the restore,
        // after the recovery wait. Alpha then went whole.
        let w = r.kb.borrow().writes.clone();
        assert!(w[21].0 - w[20].0 >= frame::RECOVERY, "write 21 is the restore, {:?} after", w[21].0 - w[20].0);
        assert!(sent(&r, &title("Alpha"), 0));
        assert!(r.h.status().frames >= 1);
    }

    #[test]
    fn a_title_going_stale_mid_frame_is_cut_off_for_its_clear() {
        let mut r = rig();
        let t0 = r.t.now();
        // Observed 28 s ago: fresh when picked (~1 s in), stale ~1 s into the
        // frame, which runs ~3.5 s.
        r.h.set_text(title("A rather long title to take time"), t0 - STALE_AFTER + Duration::from_secs(2));
        r.h.grant(Some(t0 - HANDOVER), t0);
        run_until(&mut r, t0 + Duration::from_secs(10));
        assert!(!sent(&r, &title("A rather long title to take time"), 0), "never committed whole");
        assert!(sent(&r, &(super::super::TYPE_CLEAR, vec![]), 0));
    }

    #[test]
    fn a_stop_with_a_restore_owed_restores_once_through_the_flag() {
        let mut r = rig();
        let t0 = granted(&mut r, title("Alpha"));
        let (h, kb) = (r.h.clone(), r.kb.clone());
        r.kb.borrow_mut().hook = Some(Box::new(move |i| {
            if i == 20 {
                h.revoke();
                kb.borrow_mut().unknown = true;
            }
        }));
        run_until(&mut r, t0 + Duration::from_secs(5));
        assert!(r.core.line.restore_pending);
        let n = r.kb.borrow().writes.len();
        r.kb.borrow_mut().unknown = false;
        r.h.stop();
        assert_eq!(r.core.step(&r.h, &*r.t), Next::Exit);
        assert_eq!(r.kb.borrow().writes.len(), n + 1, "the one restore at the stop");
        assert!(!r.core.line.restore_pending);
    }

    #[test]
    fn poison_keeps_the_guard_alive() {
        use std::sync::atomic::{AtomicUsize, Ordering};
        use std::sync::Arc;
        struct Counted(Arc<AtomicUsize>);
        impl Drop for Counted {
            fn drop(&mut self) {
                self.0.fetch_add(1, Ordering::SeqCst);
            }
        }
        let drops = Arc::new(AtomicUsize::new(0));
        let mut r = rig();
        granted(&mut r, title("Alpha"));
        r.core.guard = Some(Box::new(Counted(drops.clone())));
        r.kb.borrow_mut().stuck = true;
        assert_eq!(r.core.step(&r.h, &*r.t), Next::Exit);
        drop(r);
        assert_eq!(drops.load(Ordering::SeqCst), 0, "forgotten, never dropped");
    }

    #[test]
    fn a_revoke_mid_frame_stops_before_the_next_write_and_restores() {
        let mut r = rig();
        let t0 = granted(&mut r, title("Alpha"));
        let h = r.h.clone();
        r.kb.borrow_mut().hook = Some(Box::new(move |i| {
            if i == 20 {
                h.revoke();
            }
        }));
        run_until(&mut r, t0 + Duration::from_secs(20));
        let w = r.kb.borrow().writes.clone();
        assert_eq!(w.len(), 22, "write 21 is the restore, nothing of the frame after 20");
        assert_eq!(w[21].1, 0x00);
        assert!(w[21].0 - w[20].0 >= frame::RECOVERY);
        assert!(r.core.guard.is_none() && r.core.lights.is_none(), "released once restored");
    }

    #[test]
    fn a_pending_restore_is_retried_every_second_even_when_revoked() {
        let mut r = rig();
        let t0 = granted(&mut r, title("Alpha"));
        let (h, kb) = (r.h.clone(), r.kb.clone());
        r.kb.borrow_mut().hook = Some(Box::new(move |i| {
            if i == 20 {
                h.revoke();
                kb.borrow_mut().unknown = true; // the session locks
            }
        }));
        run_until(&mut r, t0 + Duration::from_secs(10));
        assert!(r.core.line.restore_pending);
        assert!(r.core.guard.is_some(), "the lock stays while a restore is owed");
        let n = r.kb.borrow().writes.len();
        r.kb.borrow_mut().unknown = false; // unlocked
        let t1 = r.t.now();
        run_until(&mut r, t1 + Duration::from_secs(3));
        let w = r.kb.borrow().writes.clone();
        assert_eq!(w.len(), n + 1, "one restore write");
        assert!(!r.core.line.restore_pending);
        assert!(r.core.guard.is_none(), "released after");
    }

    #[test]
    fn a_failed_write_waits_the_recovery_from_its_own_end() {
        let mut r = rig();
        let t0 = granted(&mut r, title("Alpha"));
        let kb = r.kb.clone();
        r.kb.borrow_mut().hook = Some(Box::new(move |i| {
            if i == 20 {
                kb.borrow_mut().fail = true;
            }
        }));
        r.core.step(&r.h, &*r.t);
        // Write 21 failed after 1 s; the handle was dropped and nothing more
        // was written in that step.
        assert_eq!(r.kb.borrow().writes.len(), 21);
        assert!(r.core.lights.is_none());
        assert!(r.h.status().failures >= 1);
        let _ = t0;
    }

    #[test]
    fn a_stuck_write_or_open_poisons_and_keeps_the_lock() {
        let mut r = rig();
        let t0 = granted(&mut r, title("Alpha"));
        r.kb.borrow_mut().stuck = true;
        assert_eq!(r.core.step(&r.h, &*r.t), Next::Exit);
        assert_eq!(r.h.status().state, "poisoned");
        assert!(r.core.guard.is_none(), "forgotten, not dropped");
        assert_eq!(r.core.step(&r.h, &*r.t), Next::Exit);
        let _ = t0;

        let mut r = rig();
        granted(&mut r, title("Alpha"));
        r.core.platform.open_stuck = true;
        assert_eq!(r.core.step(&r.h, &*r.t), Next::Exit);
        assert_eq!(r.core.platform.opens, 1, "never retried");
        assert_eq!(r.h.status().state, "poisoned");
    }

    #[test]
    fn locks_unknown_hold_the_frame_and_their_return_resends() {
        let mut r = rig();
        let t0 = granted(&mut r, title("Alpha"));
        run_until(&mut r, t0 + Duration::from_secs(5));
        let n = r.kb.borrow().writes.len();
        r.kb.borrow_mut().unknown = true;
        let t1 = r.t.now();
        run_until(&mut r, t1 + Duration::from_secs(5));
        assert_eq!(r.kb.borrow().writes.len(), n, "nothing while unknown");
        assert_eq!(r.h.status().state, "locks unknown");
        r.kb.borrow_mut().unknown = false;
        let t2 = r.t.now();
        r.h.set_text(title("Alpha"), t2);
        run_until(&mut r, t2 + Duration::from_secs(6));
        assert!(r.kb.borrow().writes.len() > n + 20, "resent on their return, before the 60 s refresh");
    }

    #[test]
    fn the_wake_guess_resends_on_new_input_after_idleness() {
        let mut r = rig();
        let t0 = granted(&mut r, title("Alpha"));
        r.input.set(Some((1000, 1000)));
        // The title, its transport, and both repeats settle first.
        run_until(&mut r, t0 + Duration::from_secs(15));
        let n = r.kb.borrow().writes.len();
        r.input.set(Some((1000, 40_000))); // 39 s idle: armed
        let t1 = r.t.now();
        r.h.set_text(title("Alpha"), t1);
        run_until(&mut r, t1 + Duration::from_secs(2));
        assert_eq!(r.kb.borrow().writes.len(), n);
        r.input.set(Some((40_500, 40_500))); // a key
        let t2 = r.t.now();
        r.h.set_text(title("Alpha"), t2);
        run_until(&mut r, t2 + Duration::from_secs(6));
        assert!(r.kb.borrow().writes.len() > n + 20);
    }

    // --- type 3, the transport -------------------------------------------

    use crate::lockchan::Playback;
    use crate::text::Icon;

    fn playing(lines: &str, icon: Icon, pos_s: u32, dur_s: u32) -> Media {
        let (_, text) = title(lines);
        let mut text1 = text.clone();
        text1[0] = icon.id();
        Media { text: (super::super::TYPE_NOW_PLAYING, text1), icon, pb: (icon == Icon::Play).then_some(Playback { pos_s, dur_s }) }
    }

    /// Every type-3 frame in the writes, as (icon, state, pos, dur), for any
    /// position up to `max_pos`.
    fn transports(r: &Rig, icon: Icon, dur: u32, max_pos: u32) -> Vec<u32> {
        let w: Vec<u8> = r.kb.borrow().writes.iter().map(|w| w.1).collect();
        let mut found = vec![];
        for pos in 0..=max_pos {
            let (k, b) = crate::lockchan::transport(icon, Some(Playback { pos_s: pos, dur_s: dur }), pos);
            if (0..=255u8).any(|seq| {
                let e = crate::lockchan::encode(&b, k, seq, Profile::P0, 0).unwrap();
                w.windows(e.len()).any(|x| x == &e[..])
            }) {
                found.push(pos);
            }
        }
        found
    }

    fn paused_transport_sent(r: &Rig) -> bool {
        let (k, b) = crate::lockchan::transport(Icon::Pause, None, 0);
        let w: Vec<u8> = r.kb.borrow().writes.iter().map(|w| w.1).collect();
        (0..=255u8).any(|seq| {
            let e = crate::lockchan::encode(&b, k, seq, Profile::P0, 0).unwrap();
            w.windows(e.len()).any(|x| x == &e[..])
        })
    }

    #[test]
    fn a_playing_title_sends_its_text_then_its_transport_with_the_position_at_the_pick() {
        let mut r = rig();
        let t0 = r.t.now();
        r.h.set(playing("Alpha", Icon::Play, 100, 300), t0, t0);
        r.h.grant(Some(t0 - HANDOVER), t0);
        run_until(&mut r, t0 + Duration::from_secs(8));
        assert!(sent(&r, &playing("Alpha", Icon::Play, 0, 0).text, 0), "the title");
        let pos = transports(&r, Icon::Play, 300, 140);
        assert!(!pos.is_empty(), "a transport followed");
        // Picked a few seconds after t0: the position moved with the clock.
        assert!(pos[0] > 100 && pos[0] < 110, "{pos:?}");
    }

    #[test]
    fn a_pause_sends_only_the_transport() {
        let mut r = rig();
        let t0 = r.t.now();
        r.h.set(playing("Alpha", Icon::Play, 100, 300), t0, t0);
        r.h.grant(Some(t0 - HANDOVER), t0);
        run_until(&mut r, t0 + Duration::from_secs(15));
        let n = r.kb.borrow().writes.len();
        let t1 = r.t.now();
        r.h.set(playing("Alpha", Icon::Pause, 115, 300), t1, t1);
        run_until(&mut r, t1 + Duration::from_secs(3));
        let new = r.kb.borrow().writes.len() - n;
        assert!(paused_transport_sent(&r), "the pause went as type 3");
        // A type 3 is short; a whole title would be far more writes.
        assert!(new > 10 && new < 90, "{new} writes after the pause");
    }

    #[test]
    fn a_seek_sends_the_transport_at_once() {
        let mut r = rig();
        let t0 = r.t.now();
        r.h.set(playing("Alpha", Icon::Play, 100, 300), t0, t0);
        r.h.grant(Some(t0 - HANDOVER), t0);
        run_until(&mut r, t0 + Duration::from_secs(15));
        let n = r.kb.borrow().writes.len();
        let t1 = r.t.now();
        // Jumped to 200 s: far from 100 + 15.
        r.h.set(playing("Alpha", Icon::Play, 200, 300), t1, t1);
        run_until(&mut r, t1 + Duration::from_secs(4));
        assert!(r.kb.borrow().writes.len() > n, "something went at once");
        assert!(transports(&r, Icon::Play, 300, 210).iter().any(|p| *p >= 200), "the new position");
    }

    #[test]
    fn no_transport_follows_a_clear() {
        let mut r = rig();
        let t0 = r.t.now();
        r.h.set(Media { text: (super::super::TYPE_CLEAR, vec![]), icon: Icon::None, pb: None }, t0, t0);
        r.h.grant(Some(t0 - HANDOVER), t0);
        run_until(&mut r, t0 + Duration::from_secs(8));
        assert!(sent(&r, &(super::super::TYPE_CLEAR, vec![]), 0));
        let (k, b) = crate::lockchan::transport(Icon::None, None, 0);
        let w: Vec<u8> = r.kb.borrow().writes.iter().map(|w| w.1).collect();
        let t3 = (0..=255u8).any(|seq| {
            let e = crate::lockchan::encode(&b, k, seq, Profile::P0, 0).unwrap();
            w.windows(e.len()).any(|x| x == &e[..])
        });
        assert!(!t3, "no type 3 after a clear");
    }

    #[test]
    fn the_position_ticking_is_not_a_change() {
        let mut r = rig();
        let t0 = r.t.now();
        r.h.set(playing("Alpha", Icon::Play, 100, 300), t0, t0);
        r.h.grant(Some(t0 - HANDOVER), t0);
        run_until(&mut r, t0 + Duration::from_secs(15));
        let n = r.kb.borrow().writes.len();
        // The loop's polls: the position moving with the clock.
        for i in 1..=20u64 {
            let t = t0 + Duration::from_secs(15 + i);
            r.t.0.set(r.t.now().max(t));
            r.h.set(playing("Alpha", Icon::Play, 115 + i as u32, 300), t, t);
            run_until(&mut r, t);
        }
        assert_eq!(r.kb.borrow().writes.len(), n, "nothing sent for a ticking position");
    }

    #[test]
    fn absent_relists_every_10_s() {
        let mut r = rig();
        r.core.platform.present = false;
        let t0 = granted(&mut r, title("Alpha"));
        assert_eq!(r.core.step(&r.h, &*r.t), Next::WaitUntil(t0 + RELIST));
        assert_eq!(r.h.status().state, "absent");
        assert_eq!(r.core.step(&r.h, &*r.t), Next::WaitUntil(t0 + RELIST));
        assert_eq!(r.core.platform.opens, 1);
    }

    #[test]
    fn stop_exits_and_releases() {
        let mut r = rig();
        r.h.stop();
        assert_eq!(r.core.step(&r.h, &*r.t), Next::Exit);
        assert!(r.core.guard.is_none());
    }
}
