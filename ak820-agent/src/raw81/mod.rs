//! Raw HID over the 2.4G dongle: the host side of the board's stage-2
//! protocol (`plans/RAWHID-24G-PLAN.md`, revisions 2-5; the daemon's use of it
//! is `plans/RAWHID-24G-DAEMON-PLAN.md`).
//!
//! A port of `scripts/raw81_proto.py`, rule for rule, with its golden vectors
//! below, so this, the Python, and (through `scripts/raw81_test.py`) the
//! board's `raw81.c` agree byte for byte. Pure: no I/O.
//!
//! A request is one 32-byte output report on the dongle's FF60 interface,
//! envelope v2 (`plans/RAWHID-24G-DAEMON-PLAN.md`, R3-1):
//!
//! ```text
//! [0] 0xC6  [1] session (nonzero)  [2..3] seq, big-endian
//! [4..5] CRC-16/CCITT-FALSE over [0..3] and [6..31]
//! [6] mask  [7..31] the inner command's bytes 1..25, XOR mask
//! ```
//!
//! Every allowed inner command starts `0x07`; it is not sent, and the board
//! restores it. The mask keeps every masked byte off the four values an older
//! board's 3-byte window could act on, and [`safe_request`] steps the seq
//! until the header, the CRC and the module's sum are off them too, so no
//! request can be misread by a board without stage 2, whatever the dongle is
//! paired to.
//!
//! The board answers with two 32-byte input reports: the handler's reply
//! (byte for byte the USB reply; status 0 only), then a receipt:
//!
//! ```text
//! [0] 0xC7  [1] session  [2..3] seq  [4] status  [5..6] last admitted seq
//! [7..8] CRC-16 of the reply (status 0)  [9..10] CRC-16 of [0..8]  [11..31] 0
//! ```

/// The receipt's first byte: never a first byte any reply on this board
/// starts with (those are `0x07`, or VIA's `0xFF` unhandled).
pub const MAGIC: u8 = 0xC7;
/// A request's first byte, envelope v2. (v1, `0xC7` and unmasked, is retired.)
pub const MAGIC_REQ: u8 = 0xC6;
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
    /// Every allowed command starts `0x07`, which v2 does not send.
    NotCustomValue,
}

/// Bytes an older board's 3-byte window could act on: a frame type
/// (`5A`/`5B`/`5C`) or the start of the `61 0D 0A` ACK. A frame without
/// them can match nothing in any alignment from a cleared window
/// (`plans/RAWHID-24G-DAEMON-PLAN.md`, R2-1; the leading `0x81` is the
/// stated residual, R4-2).
pub const WINDOW_BYTES: [u8; 4] = [0x5A, 0x5B, 0x5C, 0x61];

/// The lowest mask under which no byte of `rest` (inner[1..25]) nor the mask
/// itself is a window byte. One always exists: each byte excludes at most 4
/// masks (<= 100 of 256), the mask's own exclusion 4 more.
pub fn pick_mask(rest: &[u8]) -> u8 {
    (0u8..=255)
        .find(|&m| !WINDOW_BYTES.contains(&m) && rest.iter().all(|&b| !WINDOW_BYTES.contains(&(b ^ m))))
        .expect("a mask exists for 25 bytes")
}

/// The 32-byte request (envelope v2) for one inner command: at most
/// [`INNER`] bytes, starting `0x07`, zero-padded before masking.
pub fn envelope(session: u8, seq: u16, inner: &[u8]) -> Result<[u8; PAYLOAD], EnvelopeError> {
    if session == 0 {
        return Err(EnvelopeError::ZeroSession);
    }
    if inner.len() > INNER {
        return Err(EnvelopeError::TooLong(inner.len()));
    }
    if inner.first() != Some(&0x07) {
        return Err(EnvelopeError::NotCustomValue);
    }
    let mut padded = [0u8; INNER];
    padded[..inner.len()].copy_from_slice(inner);
    let m = pick_mask(&padded[1..]);
    let mut out = [0u8; PAYLOAD];
    out[0] = MAGIC_REQ;
    out[1] = session;
    out[2..4].copy_from_slice(&seq.to_be_bytes());
    out[HEAD] = m;
    for (o, &b) in out[HEAD + 1..].iter_mut().zip(&padded[1..]) {
        *o = b ^ m;
    }
    let crc = crc16(&out[HEAD..], crc16(&out[..4], 0xFFFF));
    out[4..6].copy_from_slice(&crc.to_be_bytes());
    Ok(out)
}

/// The sum the module puts after an `81` frame's 32 bytes.
pub fn wire_sum(payload: &[u8; PAYLOAD]) -> u8 {
    payload.iter().fold(0x81u8, |s, &b| s.wrapping_add(b))
}

/// No byte of the whole wire frame (`81`, the 32 bytes, the sum) is a
/// window byte.
pub fn frame_is_inert(payload: &[u8; PAYLOAD]) -> bool {
    !payload.iter().chain(std::iter::once(&wire_sum(payload))).any(|b| WINDOW_BYTES.contains(b))
}

/// Is this session id usable on the radio (nonzero, not a window byte, not
/// `0x81`)?
pub fn safe_session(session: u8) -> bool {
    session != 0 && session != 0x81 && !WINDOW_BYTES.contains(&session)
}

/// The first seq at or after `seq` (mod 2^16) whose two bytes are not window
/// bytes: a bad high byte jumps to the next high byte's start (stepping one at
/// a time from 0x5A00 would never leave it).
pub fn clean_seq(mut seq: u16) -> u16 {
    loop {
        let [hi, lo] = seq.to_be_bytes();
        if WINDOW_BYTES.contains(&hi) {
            seq = u16::from(hi.wrapping_add(1)) << 8;
        } else if WINDOW_BYTES.contains(&lo) {
            seq = seq.wrapping_add(1);
        } else {
            return seq;
        }
    }
}

/// The first seq from `from` (counting up, skipping seqs with a window byte,
/// at most `tries` candidates) whose v2 request is inert, header, CRC and sum
/// included: `(seq, payload)`, or `None` (an unusable session, a bad inner
/// command, or the search exhausted). Every seq skipped counts toward the
/// session's span: the caller's seq moves past them.
pub fn safe_request(session: u8, from: u16, inner: &[u8], tries: u16) -> Option<(u16, [u8; PAYLOAD])> {
    if !safe_session(session) {
        return None;
    }
    let mut seq = clean_seq(from);
    for _ in 0..tries {
        let p = envelope(session, seq, inner).ok()?;
        if frame_is_inert(&p) {
            return Some((seq, p));
        }
        seq = clean_seq(seq.wrapping_add(1));
    }
    None
}

/// The qualification probe's inner command: the health read (`HC_GET`), on
/// every stage-2 board's allow-list. (With v2 every request is inert; the
/// probe only tells whether anything answers.)
pub const PROBE_INNER: [u8; 3] = [0x07, 0x13, 0x01];

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
            ("071301", 1u8, 1u16, "c6010001b31a0013010000000000000000000000000000000000000000000000"),
            ("0712030001526164696f207469746c65", 90, 4660, "c65a1234061e0113020100536065686e217568756d6401010101010101010101"),
            ("071202", 255, 65535, "c6ffffffb1e30012020000000000000000000000000000000000000000000000"),
            ("07120401001000f0", 9, 0, "c6090000178c00120401001000f0000000000000000000000000000000000000"),
            ("07120301005a7a615b5c", 3, 7, "c60300075ada0210010302587863595e02020202020202020202020202020202"),
        ];
        for (inner, s, q, want) in golden {
            assert_eq!(envelope(s, q, &hex(inner)).unwrap().to_vec(), hex(want), "{inner}");
        }
        // safe_request: a window high byte jumps, a clean start is kept.
        let safe = [
            (0x5A00u16, 0x5D00u16, "c6015d00f9120013010000000000000000000000000000000000000000000000"),
            (0, 0, "c6010000a67c0013010000000000000000000000000000000000000000000000"),
            (0x1234, 0x1234, "c6011234190d0013010000000000000000000000000000000000000000000000"),
        ];
        for (from, seq, want) in safe {
            let (q, p) = safe_request(1, from, &PROBE_INNER, 64).unwrap();
            assert_eq!((q, p.to_vec()), (seq, hex(want)), "{from:#x}");
        }
    }

    #[test]
    fn envelope_refusals() {
        assert_eq!(envelope(0, 1, b"\x07\x13\x01"), Err(EnvelopeError::ZeroSession));
        let mut long = [0u8; 27];
        long[0] = 0x07;
        assert_eq!(envelope(1, 1, &long), Err(EnvelopeError::TooLong(27)));
        assert_eq!(envelope(1, 1, &[0u8; 26]), Err(EnvelopeError::NotCustomValue));
        assert_eq!(envelope(1, 1, &[]), Err(EnvelopeError::NotCustomValue));
        assert!(envelope(1, 1, &long[..26]).is_ok());
    }

    #[test]
    fn clean_seq_skips_window_bytes() {
        assert_eq!(clean_seq(0x5A00), 0x5D00);
        assert_eq!(clean_seq(0x5BFF), 0x5D00);
        assert_eq!(clean_seq(0x615A), 0x6200);
        assert_eq!(clean_seq(0x125A), 0x125D);
        assert_eq!(clean_seq(0x1261), 0x1262);
        assert_eq!(clean_seq(0x1234), 0x1234);
        for s in 0..=u16::MAX {
            let [h, l] = clean_seq(s).to_be_bytes();
            assert!(!WINDOW_BYTES.contains(&h) && !WINDOW_BYTES.contains(&l), "{s:#x}");
        }
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

    /// The window parser's rules (ch582f_ajazz.c, every build): an ACK is
    /// `61 0D 0A`; a frame is `5A|5B|5C <d> <type + d>`. A model of it, run
    /// over the probe's whole wire frame from a cleared window.
    fn window_matches(bytes: &[u8]) -> usize {
        let (mut b1, mut b0) = (0u8, 0u8);
        let mut n = 0;
        for &c in bytes {
            let b2 = b1;
            b1 = b0;
            b0 = c;
            let ack = (b2, b1, b0) == (0x61, 0x0D, 0x0A);
            let frame = matches!(b2, 0x5A | 0x5B | 0x5C) && b0 == b2.wrapping_add(b1);
            if ack || frame {
                n += 1;
                b1 = 0;
                b0 = 0;
            }
        }
        n
    }

    /// What a stage-2 board does with a v2 payload: check the CRC, unmask,
    /// restore the 0x07.
    fn board_unwrap(p: &[u8; PAYLOAD]) -> Option<[u8; INNER]> {
        if p[0] != MAGIC_REQ || p[1] == 0 {
            return None;
        }
        if crc16(&p[HEAD..], crc16(&p[..4], 0xFFFF)) != u16::from_be_bytes([p[4], p[5]]) {
            return None;
        }
        let mut inner = [0u8; INNER];
        inner[0] = 0x07;
        for (o, &b) in inner[1..].iter_mut().zip(&p[HEAD + 1..]) {
            *o = b ^ p[HEAD];
        }
        Some(inner)
    }

    #[test]
    fn every_request_is_inert_to_an_older_board() {
        // A fixed LCG: random text lines and playback values, every byte value.
        let mut x = 0x2545_F491u32;
        let mut rnd = move || {
            x = x.wrapping_mul(1_103_515_245).wrapping_add(12345);
            (x >> 16) as u8
        };
        let mut inners: Vec<Vec<u8>> = vec![PROBE_INNER.to_vec(), vec![0x07, 0x12, 0x02]];
        for _ in 0..300 {
            let mut v = vec![0x07, 0x12, 0x03, rnd() & 3, 0];
            let n = usize::from(rnd()) % 22;
            v.extend((0..n).map(|_| rnd()));
            inners.push(v);
            let mut w = vec![0x07, 0x12, 0x04];
            w.extend((0..5).map(|_| rnd()));
            inners.push(w);
        }
        let mut built = 0;
        for (i, inner) in inners.iter().enumerate() {
            for session in 1..=255u8 {
                let from = [0u16, 0x5A00, 0x1234, 0x7FFF, 0xFFF0][(i + usize::from(session)) % 5];
                let Some((seq, p)) = safe_request(session, from, inner, 256) else {
                    assert!(!safe_session(session), "session {session} from {from}");
                    continue;
                };
                let mut wire = vec![0x81];
                wire.extend_from_slice(&p);
                wire.push(wire_sum(&p));
                assert!(!wire.iter().any(|b| WINDOW_BYTES.contains(b)), "{session} {seq}");
                assert_eq!(window_matches(&wire), 0, "{session} {seq}");
                // And a stage-2 board reads back exactly the inner command.
                let got = board_unwrap(&p).expect("crc");
                assert_eq!(&got[..inner.len()], &inner[..]);
                assert!(got[inner.len()..].iter().all(|&b| b == 0));
                built += 1;
            }
        }
        assert!(built > 150_000, "{built}");
    }

    #[test]
    fn unusable_sessions() {
        for s in [0u8, 0x5A, 0x5B, 0x5C, 0x61, 0x81] {
            assert!(!safe_session(s));
            assert!(safe_request(s, 0, &PROBE_INNER, 1000).is_none(), "{s:#x}");
        }
        assert!(safe_request(1, 0, &[0x13, 0x01], 1000).is_none());
    }

    #[test]
    fn the_model_does_match_what_it_should() {
        assert_eq!(window_matches(&[0x5C, 0x25, 0x81]), 1);
        assert_eq!(window_matches(&[0x61, 0x0D, 0x0A]), 1);
        assert_eq!(window_matches(&[0x5A, 0x01, 0x5B, 0x00, 0x5B]), 1);
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
