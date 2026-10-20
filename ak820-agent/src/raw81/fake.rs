//! A fake dongle with a stage-2 board behind it, and a clock that moves only
//! when told to: the seams for [`super::link`]'s tests and the daemon's
//! radio tests (`plans/RAWHID-24G-DAEMON-PLAN.md`, R2-6, R3-3).

use std::cell::{Cell, RefCell};
use std::collections::VecDeque;
use std::time::{Duration, Instant};

use super::link::Clock;
use super::{crc16, MAGIC, MAGIC_REQ, PAYLOAD, ST_OK, WINDOW_BYTES};
use crate::hid::exchange::{Sent, Wire};
use crate::hid::Error;

/// Time that moves only when told to, or when a read waits out its
/// timeout with nothing to hand back.
pub(crate) struct FakeClock {
    t: Cell<Instant>,
}

impl FakeClock {
    pub(crate) fn new() -> FakeClock {
        FakeClock { t: Cell::new(Instant::now()) }
    }
    pub(crate) fn advance(&self, d: Duration) {
        self.t.set(self.t.get() + d);
    }
}

impl Clock for FakeClock {
    fn now(&self) -> Instant {
        self.t.get()
    }
}

/// What the fake board does with the next request.
#[derive(Clone, Debug)]
pub(crate) enum Board {
    /// Reply (the echo) then the OK receipt, `ms` after the write.
    Answer { ms: u64 },
    /// The receipt only: the reply was lost.
    ReceiptOnly,
    /// A NAK receipt.
    Nak(u8),
    /// Nothing.
    Silent,
    /// A receipt whose own CRC fails.
    Corrupt,
    /// Another session's receipt and a stray reply first, then answer.
    ForeignFirst,
    /// Answer with 0xFF (unhandled).
    Unhandled,
    /// Every read fails as a stuck handle.
    Stuck,
}

/// A fake dongle with a stage-2 board behind it: it decodes each v2
/// envelope as the board does and answers per its script.
pub(crate) struct Fake<'c> {
    pub(crate) clock: &'c FakeClock,
    pub(crate) script: RefCell<VecDeque<Board>>,
    /// (deliver at, report)
    pub(crate) inbox: RefCell<VecDeque<(Instant, Vec<u8>)>>,
    /// Every request the board received: (session, seq, inner).
    pub(crate) got: RefCell<Vec<(u8, u16, Vec<u8>)>>,
    pub(crate) written: RefCell<Vec<Vec<u8>>>,
    pub(crate) stuck: Cell<bool>,
}

pub(crate) fn receipt(session: u8, seq: u16, status: u8, reply: Option<&[u8; PAYLOAD]>) -> Vec<u8> {
    let mut out = vec![0u8; PAYLOAD];
    out[0] = MAGIC;
    out[1] = session;
    out[2..4].copy_from_slice(&seq.to_be_bytes());
    out[4] = status;
    if let Some(r) = reply {
        out[7..9].copy_from_slice(&crc16(r, 0xFFFF).to_be_bytes());
    }
    let c = crc16(&out[..9], 0xFFFF);
    out[9..11].copy_from_slice(&c.to_be_bytes());
    out
}

impl<'c> Fake<'c> {
    pub(crate) fn new(clock: &'c FakeClock, script: &[Board]) -> Fake<'c> {
        Fake {
            clock,
            script: RefCell::new(script.iter().cloned().collect()),
            inbox: RefCell::new(VecDeque::new()),
            got: RefCell::new(Vec::new()),
            written: RefCell::new(Vec::new()),
            stuck: Cell::new(false),
        }
    }

    pub(crate) fn deliver(&self, after_ms: u64, r: Vec<u8>) {
        let at = self.clock.now() + Duration::from_millis(after_ms);
        let mut with_id = vec![0u8];
        with_id.extend(r);
        self.inbox.borrow_mut().push_back((at, with_id));
    }
}

impl Wire for Fake<'_> {
    fn write_report(&self, data: &[u8], _timeout: Duration) -> Result<Sent, Error> {
        assert_eq!(data.len(), 33);
        assert_eq!(data[0], 0);
        let p: [u8; PAYLOAD] = data[1..].try_into().unwrap();
        // The rule every request keeps: nothing an older board acts on.
        let sum = p.iter().fold(0x81u8, |s, &b| s.wrapping_add(b));
        assert!(!p.iter().chain(std::iter::once(&sum)).any(|b| WINDOW_BYTES.contains(b)), "{p:02x?}");
        self.written.borrow_mut().push(data.to_vec());
        // The board's decode: CRC, unmask, restore 0x07.
        assert_eq!(p[0], MAGIC_REQ);
        let crc = crc16(&p[6..], crc16(&p[..4], 0xFFFF));
        assert_eq!(crc, u16::from_be_bytes([p[4], p[5]]));
        let (s, q) = (p[1], u16::from_be_bytes([p[2], p[3]]));
        let mut inner = vec![0x07u8];
        inner.extend(p[7..].iter().map(|&b| b ^ p[6]));
        self.got.borrow_mut().push((s, q, inner.clone()));
        let mut reply = [0u8; PAYLOAD];
        reply.copy_from_slice(&inner[..PAYLOAD - 6].iter().chain([0u8; 6].iter()).copied().collect::<Vec<_>>());
        let step = self.script.borrow_mut().pop_front().unwrap_or(Board::Answer { ms: 20 });
        match step {
            Board::Answer { ms } => {
                self.deliver(ms, reply.to_vec());
                self.deliver(ms, receipt(s, q, ST_OK, Some(&reply)));
            }
            Board::ReceiptOnly => self.deliver(20, receipt(s, q, ST_OK, Some(&reply))),
            Board::Nak(st) => self.deliver(10, receipt(s, q, st, None)),
            Board::Silent => {}
            Board::Corrupt => {
                let mut r = receipt(s, q, ST_OK, Some(&reply));
                r[9] ^= 1;
                self.deliver(20, r);
            }
            Board::ForeignFirst => {
                let mut other = reply;
                other[5] ^= 0x40;
                self.deliver(5, other.to_vec());
                self.deliver(5, receipt(s.wrapping_add(1), q, ST_OK, Some(&other)));
                self.deliver(20, reply.to_vec());
                self.deliver(20, receipt(s, q, ST_OK, Some(&reply)));
            }
            Board::Unhandled => {
                let mut u = reply;
                u[0] = 0xFF;
                self.deliver(20, u.to_vec());
                self.deliver(20, receipt(s, q, ST_OK, Some(&u)));
            }
            Board::Stuck => self.stuck.set(true),
        }
        Ok(Sent::Yes)
    }

    /// Honours the timeout: the next report if it is due within it (time
    /// moves to its arrival), else time moves by the whole timeout.
    fn read_report(&self, timeout: Duration) -> Result<Option<Vec<u8>>, Error> {
        if self.stuck.get() {
            return Err(Error::Stuck);
        }
        let mut inbox = self.inbox.borrow_mut();
        let now = self.clock.now();
        match inbox.front() {
            Some((at, _)) if *at <= now + timeout => {
                if *at > now {
                    self.clock.advance(*at - now);
                }
                Ok(inbox.pop_front().map(|(_, r)| r))
            }
            _ => {
                self.clock.advance(timeout);
                Ok(None)
            }
        }
    }
}

