//! The lock-LED channel: now-playing to the board over Bluetooth, where there
//! is no raw HID, through the keyboard's LED output report
//! (`plans/LOCK-LED-CHANNEL-PLAN.md`).
//!
//! This module is the **encoder**: a frame as the sequence of LED bytes a host
//! writes, one write each. It is a port of `scripts/lockchan.py`, and that file
//! stays the oracle -- `scripts/lockchan_test.py` proves it against the board's
//! receiver (`lockchan.c`), and `tests/lockchan_parity.rs`, generated from it by
//! `scripts/gen_lockchan_vectors.py`, proves this against it. Never hand-edit
//! the parity file; regenerate it.
//!
//! `user` is the host's real lock state as HID LED bits (Num `0x01`, Caps
//! `0x02`, Scroll `0x04`). Every write keeps its Caps, and the frame ends back
//! on it.

pub mod frame;
pub mod policy;

/// Which LED bits carry data (the plan's "Profiles").
#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum Profile {
    /// Num and Scroll, base 3: the only one Windows can write.
    P0 = 0,
    /// Num and bits 3-4, base 7.
    P1 = 1,
    /// Num, Scroll and bits 3-4, base 15.
    P2 = 2,
    /// Bits 3-7, base 31: no light moves. The Mac's.
    P3 = 3,
}

pub const CAPS: u8 = 0x02;
pub const MAX_PAYLOAD: usize = 48;

struct Spec {
    mask: u8,
    bits: &'static [u8],
    base: u32,
    block_bits: u32,
    block_digits: u32,
    trailer: (u8, u8),
}

const fn spec(p: Profile) -> Spec {
    match p {
        Profile::P0 => Spec { mask: 0x05, bits: &[0x01, 0x04], base: 3, block_bits: 19, block_digits: 12, trailer: (0x01, 0x04) },
        Profile::P1 => Spec { mask: 0x19, bits: &[0x01, 0x08, 0x10], base: 7, block_bits: 14, block_digits: 5, trailer: (0x08, 0x10) },
        Profile::P2 => Spec { mask: 0x1D, bits: &[0x01, 0x04, 0x08, 0x10], base: 15, block_bits: 31, block_digits: 8, trailer: (0x08, 0x10) },
        Profile::P3 => Spec { mask: 0xF8, bits: &[0x08, 0x10, 0x20, 0x40, 0x80], base: 31, block_bits: 29, block_digits: 6, trailer: (0x08, 0x10) },
    }
}

/// The profile a frame's header is sent in: P2's header goes in P1.
fn header_profile(p: Profile) -> Profile {
    if p == Profile::P2 { Profile::P1 } else { p }
}

/// The bits a preamble's three transitions flip.
fn preamble(p: Profile) -> u8 {
    match p {
        Profile::P0 => 0x05,
        Profile::P1 | Profile::P2 => 0x18,
        Profile::P3 => 0xE0,
    }
}

/// The fewest base-`base` digits that hold `r` bits.
fn digits_for(base: u32, r: u32) -> u32 {
    let (mut n, mut p) = (0, 1u64);
    while p < 1u64 << r {
        p *= base as u64;
        n += 1;
    }
    n
}

pub fn crc8(data: &[u8]) -> u8 {
    let mut c = 0u8;
    for &b in data {
        c ^= b;
        for _ in 0..8 {
            c = if c & 0x80 != 0 { (c << 1) ^ 0x07 } else { c << 1 };
        }
    }
    c
}

/// CRC-16/CCITT-FALSE.
pub fn crc16(data: &[u8]) -> u16 {
    let mut c = 0xFFFFu16;
    for &b in data {
        c ^= (b as u16) << 8;
        for _ in 0..8 {
            c = if c & 0x8000 != 0 { (c << 1) ^ 0x1021 } else { c << 1 };
        }
    }
    c
}

/// The first `nbits` of `data`, most significant first, as the profile's
/// digits: blocks of the profile's size, the last in the fewest digits that
/// hold what is left.
fn to_digits(data: &[u8], nbits: u32, p: Profile) -> Vec<u8> {
    let s = spec(p);
    let bit = |i: u32| (data[(i / 8) as usize] >> (7 - i % 8)) & 1;
    let (mut out, mut at, mut left) = (Vec::new(), 0u32, nbits);
    while left > 0 {
        let bits = s.block_bits.min(left);
        let mut block = 0u64;
        for _ in 0..bits {
            block = block << 1 | bit(at) as u64;
            at += 1;
        }
        let n = if bits == s.block_bits { s.block_digits } else { digits_for(s.base, bits) };
        let mut ds = vec![0u8; n as usize];
        for d in ds.iter_mut().rev() {
            *d = (block % s.base as u64) as u8;
            block /= s.base as u64;
        }
        out.extend(ds);
        left -= bits;
    }
    out
}

/// Digit `d` as the set of LED bits a transition flips.
fn mask_of(p: Profile, d: u8) -> u8 {
    let sub = d as u32 + 1;
    spec(p).bits.iter().enumerate().filter(|(i, _)| sub >> i & 1 != 0).fold(0, |m, (_, b)| m | b)
}

fn header(p: Profile, kind: u8, seq: u8, len: u8) -> [u8; 4] {
    let h = [(p as u8) << 4 | kind, seq, len];
    [h[0], h[1], h[2], crc8(&h)]
}

#[derive(Debug, PartialEq, Eq)]
pub enum EncodeError {
    /// More than [`MAX_PAYLOAD`] bytes.
    TooLong(usize),
    /// A type past 15.
    BadType(u8),
}

/// A frame as the LED bytes to write, in order: the preamble, the header, the
/// payload and its CRC-16, and the trailer back to `user`'s Num/Caps/Scroll
/// with bits 3-7 clear.
pub fn encode(payload: &[u8], kind: u8, seq: u8, p: Profile, user: u8) -> Result<Vec<u8>, EncodeError> {
    if payload.len() > MAX_PAYLOAD {
        return Err(EncodeError::TooLong(payload.len()));
    }
    if kind > 15 {
        return Err(EncodeError::BadType(kind));
    }
    let h = header(p, kind, seq, payload.len() as u8);
    let mut body = h.to_vec();
    body.extend_from_slice(payload);
    let crc = crc16(&body);
    let mut body = payload.to_vec();
    body.extend_from_slice(&crc.to_be_bytes());
    let hp = header_profile(p);
    let sections = [(hp, to_digits(&h, 32, hp)), (p, to_digits(&body, body.len() as u32 * 8, p))];

    let start = user & 0x07;
    let mut state = start;
    let mut out = Vec::new();
    for _ in 0..3 {
        state ^= preamble(p);
        out.push(state);
    }
    for (prof, ds) in &sections {
        for &d in ds {
            state ^= mask_of(*prof, d);
            out.push(state);
        }
    }
    let s = spec(p);
    let (ta, tb) = s.trailer;
    let b = start & s.mask;
    let mid = b ^ if state & s.mask == b ^ ta { tb } else { ta };
    out.push((state & !s.mask) | mid);
    out.push((state & !s.mask) | b);
    Ok(out)
}

/// Frame types, version 0 (the plan's R2-9, frozen for the first release).
pub const TYPE_NOW_PLAYING: u8 = 1;
pub const TYPE_CLEAR: u8 = 2;

/// What the panel should show, as a frame's type and payload. This pair is
/// the channel's content identity (codex, sender design, 16): two snapshots
/// that fold and fit to the same bytes are the same content.
///
/// Type 1 is `[icon][n0][line 0][line 1]`, the lines exactly what raw HID's
/// `SET_LINE`s carry (folded to ASCII and fitted, 19 and 21); type 2 (idle) is
/// empty. Any byte the board would refuse becomes `?`, as the firmware's own
/// setter would draw it, so a frame is never rejected whole for one glyph.
pub fn content(snapshot: &crate::smtc::Snapshot) -> (u8, Vec<u8>) {
    use crate::text::{fit, Line};
    let Some((row0, row1, icon)) = snapshot.lines() else {
        return (TYPE_CLEAR, Vec::new());
    };
    let clean = |s: String| -> Vec<u8> {
        s.bytes().map(|b| if (0x20..=0x7E).contains(&b) { b } else { b'?' }).collect()
    };
    let l0 = clean(fit(row0, Line::WithIcon.budget()));
    let l1 = clean(fit(row1, Line::FullWidth.budget()));
    let mut p = vec![icon.id(), l0.len() as u8];
    p.extend(l0);
    p.extend(l1);
    (TYPE_NOW_PLAYING, p)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn content_is_both_fitted_lines_or_clear() {
        use crate::smtc::Snapshot;
        use crate::text::Icon;
        assert_eq!(content(&Snapshot::default()), (TYPE_CLEAR, vec![]));
        let s = Snapshot {
            artist: "Cosmic Cadet".into(),
            title: "Europa - The Adventure Continues".into(),
            icon: Icon::Play,
            ..Snapshot::default()
        };
        let (t, p) = content(&s);
        assert_eq!(t, TYPE_NOW_PLAYING);
        assert_eq!(p[0], Icon::Play.id());
        let n0 = p[1] as usize;
        assert!(n0 <= 19 && p.len() - 2 - n0 <= 21 && p.len() <= 42);
        assert!(p[2..].iter().all(|b| (0x20..=0x7E).contains(b)));
        // The same bytes raw HID's SET_LINEs carry.
        let (row0, row1, _) = s.lines().unwrap();
        assert_eq!(&p[2..2 + n0], crate::text::fit(row0, 19).as_bytes());
        assert_eq!(&p[2 + n0..], crate::text::fit(row1, 21).as_bytes());
    }

    #[test]
    fn crcs_match_their_check_values() {
        assert_eq!(crc16(b"123456789"), 0x29B1); // CCITT-FALSE's check
        assert_eq!(crc8(b"123456789"), 0xF4); // poly 0x07, init 0
    }

    #[test]
    fn limits_are_refused() {
        assert_eq!(encode(&[0; 49], 1, 0, Profile::P0, 0), Err(EncodeError::TooLong(49)));
        assert_eq!(encode(b"x", 16, 0, Profile::P0, 0), Err(EncodeError::BadType(16)));
        assert!(encode(&[0; 48], 15, 255, Profile::P3, 7).is_ok());
    }

    #[test]
    fn every_write_keeps_caps_and_the_frame_ends_on_the_user() {
        for p in [Profile::P0, Profile::P1, Profile::P2, Profile::P3] {
            for user in 0..8u8 {
                let w = encode(b"Artist - Title", 1, 9, p, user).unwrap();
                assert!(w.iter().all(|&v| v & CAPS == user & CAPS), "{p:?} {user}");
                assert_eq!(*w.last().unwrap(), user);
                // Each write is a change: the receiver ignores repeats.
                let mut prev = user;
                for &v in &w {
                    assert_ne!(v, prev, "{p:?} {user}");
                    prev = v;
                }
            }
        }
    }

    #[test]
    fn p0_touches_only_num_and_scroll() {
        let w = encode(&[0xA5; 48], 2, 3, Profile::P0, 0x02).unwrap();
        assert!(w.iter().all(|&v| v & !0x07 == 0));
    }
}
