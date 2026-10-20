//! Raw HID over the 2.4G dongle: the host side of the board's stage-2
//! protocol (`plans/RAWHID-24G-PLAN.md`, revisions 2-5; the daemon's use of it
//! is `plans/RAWHID-24G-DAEMON-PLAN.md`).
//!
//! A port of `scripts/raw81_proto.py`, rule for rule, with its golden vectors
//! below, so this, the Python, and (through `scripts/raw81_test.py`) the
//! board's `raw81.c` agree byte for byte. Pure: no I/O.
//!
//! A request is one 32-byte output report on the dongle's FF60 interface:
//!
//! ```text
//! [0] 0xC7  [1] session (nonzero)  [2..3] seq, big-endian
//! [4..5] CRC-16/CCITT-FALSE over [0..3] and [6..31]  [6..31] inner (26 bytes)
//! ```
//!
//! The board answers with two 32-byte input reports: the handler's reply
//! (byte for byte the USB reply; status 0 only), then a receipt:
//!
//! ```text
//! [0] 0xC7  [1] session  [2..3] seq  [4] status  [5..6] last admitted seq
//! [7..8] CRC-16 of the reply (status 0)  [9..10] CRC-16 of [0..8]  [11..31] 0
//! ```

/// The envelope's and the receipt's first byte: never a first byte any reply
/// on this board starts with (those are `0x07`, or VIA's `0xFF` unhandled).
pub const MAGIC: u8 = 0xC7;
/// The envelope's header: magic, session, seq (2), CRC (2).
pub const HEAD: usize = 6;
/// The inner command's room: every allow-listed command fits (a text line is
/// 5 + 21 bytes; the health reads at most 4).
pub const INNER: usize = 26;
/// A report's payload, without the report id.
pub const PAYLOAD: usize = 32;

/// CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, no reflection, no xorout.
pub fn crc16(data: &[u8], mut crc: u16) -> u16 {
    for &b in data {
        crc ^= u16::from(b) << 8;
        for _ in 0..8 {
            crc = if crc & 0x8000 != 0 { (crc << 1) ^ 0x1021 } else { crc << 1 };
        }
    }
    crc
}

/// Why an envelope could not be built.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EnvelopeError {
    /// Session 0 is reserved: the board refuses it as a bad envelope.
    ZeroSession,
    /// The inner command is longer than [`INNER`] bytes.
    TooLong(usize),
}

/// The 32-byte request for one inner command (at most [`INNER`] bytes,
/// zero-padded).
pub fn envelope(session: u8, seq: u16, inner: &[u8]) -> Result<[u8; PAYLOAD], EnvelopeError> {
    if session == 0 {
        return Err(EnvelopeError::ZeroSession);
    }
    if inner.len() > INNER {
        return Err(EnvelopeError::TooLong(inner.len()));
    }
    let mut out = [0u8; PAYLOAD];
    out[0] = MAGIC;
    out[1] = session;
    out[2..4].copy_from_slice(&seq.to_be_bytes());
    out[HEAD..HEAD + inner.len()].copy_from_slice(inner);
    let crc = crc16(&out[HEAD..], crc16(&out[..4], 0xFFFF));
    out[4..6].copy_from_slice(&crc.to_be_bytes());
    Ok(out)
}

/// A well-formed receipt's fields.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Receipt {
    pub session: u8,
    pub seq: u16,
    pub status: u8,
    pub last_seq: u16,
    pub reply_crc: u16,
}

/// Receipt status: the request was dispatched; the reply came first.
pub const ST_OK: u8 = 0;
/// The envelope's CRC failed at the board (the line is quarantined).
pub const ST_BADCRC: u8 = 1;
/// Well formed, but not on the board's allow-list.
pub const ST_FORBIDDEN: u8 = 2;
/// The seq was not fresh in the session; `last_seq` is the board's.
pub const ST_STALE: u8 = 3;
/// Another session is live on the board.
pub const ST_BUSY: u8 = 4;

/// A receipt if `r` is one (magic, its own CRC, zero padding), else `None`.
pub fn parse_receipt(r: &[u8]) -> Option<Receipt> {
    if r.len() != PAYLOAD || r[0] != MAGIC {
        return None;
    }
    if crc16(&r[..9], 0xFFFF) != u16::from_be_bytes([r[9], r[10]]) || r[11..].iter().any(|&b| b != 0) {
        return None;
    }
    Some(Receipt {
        session: r[1],
        seq: u16::from_be_bytes([r[2], r[3]]),
        status: r[4],
        last_seq: u16::from_be_bytes([r[5], r[6]]),
        reply_crc: u16::from_be_bytes([r[7], r[8]]),
    })
}

/// Is `reply` shaped like an answer to `inner`: its first three bytes the
/// request's, or `0xFF` (unhandled) and then the request's `[1..2]`.
pub fn reply_shaped(reply: &[u8], inner: &[u8]) -> bool {
    reply.len() == PAYLOAD
        && inner.len() >= 3
        && reply[1..3] == inner[1..3]
        && (reply[0] == inner[0] || reply[0] == 0xFF)
}

/// How a transaction ended.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Outcome {
    /// The reply, completed by its receipt.
    Ok([u8; PAYLOAD]),
    /// The board refused it; the receipt says why.
    Nak(Receipt),
    /// A status-0 receipt with no matching reply before it.
    Incomplete,
    /// A corrupt receipt, an unknown status, the deadline, or a reconnect.
    Unknown,
}

/// One request in flight: plans/RAWHID-24G-PLAN.md, R4-3, rule by rule.
///
/// Per report (a 33-byte one with a leading 0x00 report id is stripped; any
/// other length than 32 is ignored):
/// - starting `0xC7`: a failed CRC closes it `Unknown` (a corrupt receipt may
///   be ours); valid but for another session or seq, ignored; ours: status 0
///   with a candidate whose CRC matches closes `Ok`, status 0 otherwise
///   `Incomplete`, 1-4 `Nak`, any other `Unknown`;
/// - otherwise: shaped like an answer to this command, it becomes the
///   candidate (the latest wins); not, ignored.
///
/// The caller owns the deadline: [`Transaction::timeout`] closes it `Unknown`
/// and discards the candidate; a reconnect does the same.
#[derive(Debug, Clone)]
pub struct Transaction {
    session: u8,
    seq: u16,
    inner: [u8; INNER],
    candidate: Option<[u8; PAYLOAD]>,
    outcome: Option<Outcome>,
}

impl Transaction {
    pub fn new(session: u8, seq: u16, inner: &[u8]) -> Self {
        let mut padded = [0u8; INNER];
        let n = inner.len().min(INNER);
        padded[..n].copy_from_slice(&inner[..n]);
        Transaction { session, seq, inner: padded, candidate: None, outcome: None }
    }

    /// The outcome once closed; `None` while open.
    pub fn outcome(&self) -> Option<&Outcome> {
        self.outcome.as_ref()
    }

    fn close(&mut self, o: Outcome) {
        if !matches!(o, Outcome::Ok(_)) {
            self.candidate = None;
        }
        self.outcome = Some(o);
    }

    /// Feed one input report; true once the transaction is closed.
    pub fn feed(&mut self, report: &[u8]) -> bool {
        if self.outcome.is_some() {
            return true;
        }
        let r = match report.len() {
            33 if report[0] == 0x00 => &report[1..],
            PAYLOAD => report,
            _ => return false,
        };
        if r[0] == MAGIC {
            let Some(rc) = parse_receipt(r) else {
                self.close(Outcome::Unknown);
                return true;
            };
            if rc.session != self.session || rc.seq != self.seq {
                return false;
            }
            let o = match rc.status {
                ST_OK => match self.candidate {
                    Some(c) if crc16(&c, 0xFFFF) == rc.reply_crc => Outcome::Ok(c),
                    _ => Outcome::Incomplete,
                },
                ST_BADCRC..=ST_BUSY => Outcome::Nak(rc),
                _ => Outcome::Unknown,
            };
            self.close(o);
            return true;
        }
        if reply_shaped(r, &self.inner) {
            let mut c = [0u8; PAYLOAD];
            c.copy_from_slice(r);
            self.candidate = Some(c);
        }
        false
    }

    /// The deadline passed (or the device was reconnected): `Unknown`.
    pub fn timeout(&mut self) {
        if self.outcome.is_none() {
            self.close(Outcome::Unknown);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn hex(s: &str) -> Vec<u8> {
        (0..s.len()).step_by(2).map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap()).collect()
    }

    fn receipt(session: u8, seq: u16, status: u8, reply: Option<&[u8]>, last: u16) -> [u8; PAYLOAD] {
        let mut out = [0u8; PAYLOAD];
        out[0] = MAGIC;
        out[1] = session;
        out[2..4].copy_from_slice(&seq.to_be_bytes());
        out[4] = status;
        out[5..7].copy_from_slice(&last.to_be_bytes());
        if let (ST_OK, Some(r)) = (status, reply) {
            out[7..9].copy_from_slice(&crc16(r, 0xFFFF).to_be_bytes());
        }
        let c = crc16(&out[..9], 0xFFFF);
        out[9..11].copy_from_slice(&c.to_be_bytes());
        out
    }

    #[test]
    fn crc16_check_value() {
        assert_eq!(crc16(b"123456789", 0xFFFF), 0x29B1);
    }

    /// From `scripts/raw81_proto.py` (the generator is in the commit that added
    /// these): the Python's envelopes for fixed inputs.
    #[test]
    fn envelopes_match_the_python() {
        let golden = [
            ("071301", 1u8, 1u16, "c7010001cf410713010000000000000000000000000000000000000000000000"),
            ("0712030001526164696f207469746c65", 90, 4660, "c75a1234679e0712030001526164696f207469746c6500000000000000000000"),
            ("071202", 255, 65535, "c7ffffffcdb80712020000000000000000000000000000000000000000000000"),
            ("07120401001000f0", 9, 0, "c70900006bd707120401001000f0000000000000000000000000000000000000"),
        ];
        for (inner, s, q, want) in golden {
            assert_eq!(envelope(s, q, &hex(inner)).unwrap().to_vec(), hex(want), "{inner}");
        }
    }

    #[test]
    fn envelope_refusals() {
        assert_eq!(envelope(0, 1, b"\x07\x13\x01"), Err(EnvelopeError::ZeroSession));
        assert_eq!(envelope(1, 1, &[0u8; 27]), Err(EnvelopeError::TooLong(27)));
        assert!(envelope(1, 1, &[0u8; 26]).is_ok());
    }

    #[test]
    fn receipts_match_the_python() {
        let reply = hex("071301000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c");
        assert_eq!(
            receipt(9, 0x1234, ST_OK, Some(&reply), 0x1233).to_vec(),
            hex("c7091234001233a86b4d2e000000000000000000000000000000000000000000")
        );
        assert_eq!(
            receipt(9, 0x1235, ST_FORBIDDEN, None, 0x1234).to_vec(),
            hex("c709123502123400008087000000000000000000000000000000000000000000")
        );
        let rc = parse_receipt(&receipt(9, 0x1234, ST_OK, Some(&reply), 0x1233)).unwrap();
        assert_eq!((rc.session, rc.seq, rc.status, rc.last_seq), (9, 0x1234, ST_OK, 0x1233));
    }

    // The Python self-test's cases, one each.
    const Q: &[u8] = b"\x07\x13\x01";

    fn reply() -> [u8; PAYLOAD] {
        let mut r = [0u8; PAYLOAD];
        r[..3].copy_from_slice(Q);
        for (i, b) in r[3..].iter_mut().enumerate() {
            *b = i as u8;
        }
        r
    }

    fn t() -> Transaction {
        Transaction::new(5, 7, Q)
    }

    #[test]
    fn reply_then_receipt_is_ok() {
        let mut x = t();
        assert!(!x.feed(&reply()));
        assert!(x.feed(&receipt(5, 7, ST_OK, Some(&reply()), 0)));
        assert_eq!(x.outcome(), Some(&Outcome::Ok(reply())));
    }

    #[test]
    fn receipt_first_is_incomplete() {
        let mut x = t();
        assert!(x.feed(&receipt(5, 7, ST_OK, Some(&reply()), 0)));
        assert_eq!(x.outcome(), Some(&Outcome::Incomplete));
    }

    #[test]
    fn other_seq_and_session_are_ignored() {
        let mut x = t();
        x.feed(&reply());
        assert!(!x.feed(&receipt(5, 6, ST_OK, Some(&reply()), 0)));
        assert!(!x.feed(&receipt(9, 7, ST_OK, Some(&reply()), 0)));
        assert_eq!(x.outcome(), None);
    }

    #[test]
    fn a_stale_candidate_fails_the_crc() {
        let mut x = t();
        let mut stale = [0u8; PAYLOAD];
        stale[..3].copy_from_slice(Q);
        x.feed(&stale);
        x.feed(&receipt(5, 7, ST_OK, Some(&reply()), 0));
        assert_eq!(x.outcome(), Some(&Outcome::Incomplete));
    }

    #[test]
    fn a_corrupt_receipt_closes_unknown() {
        let mut x = t();
        x.feed(&reply());
        let mut bad = receipt(5, 7, ST_OK, Some(&reply()), 0);
        bad[4] = 2;
        assert!(x.feed(&bad));
        assert_eq!(x.outcome(), Some(&Outcome::Unknown));
        assert!(x.candidate.is_none());
    }

    #[test]
    fn nak_and_unknown_status() {
        let mut x = t();
        x.feed(&receipt(5, 7, ST_FORBIDDEN, None, 0));
        assert!(matches!(x.outcome(), Some(Outcome::Nak(rc)) if rc.status == ST_FORBIDDEN));
        let mut y = t();
        y.feed(&receipt(5, 7, 9, None, 0));
        assert_eq!(y.outcome(), Some(&Outcome::Unknown));
    }

    #[test]
    fn report_ids_are_stripped() {
        let mut x = t();
        let mut a = vec![0u8];
        a.extend_from_slice(&reply());
        let mut b = vec![0u8];
        b.extend_from_slice(&receipt(5, 7, ST_OK, Some(&reply()), 0));
        x.feed(&a);
        x.feed(&b);
        assert_eq!(x.outcome(), Some(&Outcome::Ok(reply())));
    }

    #[test]
    fn a_wrong_shape_is_ignored() {
        let mut x = t();
        let mut other = [0u8; PAYLOAD];
        other[..3].copy_from_slice(b"\x07\x12\x04");
        x.feed(&other);
        x.feed(&receipt(5, 7, ST_OK, Some(&other), 0));
        assert_eq!(x.outcome(), Some(&Outcome::Incomplete));
    }

    #[test]
    fn an_unhandled_reply_is_ours() {
        let mut x = t();
        let mut unh = reply();
        unh[0] = 0xFF;
        x.feed(&unh);
        x.feed(&receipt(5, 7, ST_OK, Some(&unh), 0));
        assert!(matches!(x.outcome(), Some(Outcome::Ok(r)) if r[0] == 0xFF));
    }

    #[test]
    fn timeout_discards_the_candidate_and_closed_stays_closed() {
        let mut x = t();
        x.feed(&reply());
        x.timeout();
        assert_eq!(x.outcome(), Some(&Outcome::Unknown));
        assert!(x.candidate.is_none());
        let mut y = t();
        y.feed(&reply());
        y.feed(&receipt(5, 7, ST_OK, Some(&reply()), 0));
        assert!(y.feed(&receipt(5, 7, ST_FORBIDDEN, None, 0)));
        assert_eq!(y.outcome(), Some(&Outcome::Ok(reply())));
    }
}
