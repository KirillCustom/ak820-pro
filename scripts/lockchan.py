"""The lock-LED channel's reference encoder (plans/LOCK-LED-CHANNEL-PLAN.md,
"The wire format"): a frame as the sequence of LED bytes a host writes, one
write each. The board's receiver is lockchan.c; scripts/lockchan_test.py checks
the two against each other.

    from lockchan import encode, P0, P1, P2, P3
    writes = encode(b"payload", type=1, seq=7, profile=P3, user=0x00)

`user` is the host's real lock state (Num 0x01, Caps 0x02, Scroll 0x04). Every
write keeps its Caps, and the frame ends back on it. A production sender reads
Caps again before each write and abandons the frame when it changes; this
reference takes it as fixed.
"""
P0, P1, P2, P3 = 0, 1, 2, 3
CAPS = 0x02
MAX_PAYLOAD = 48

# mask, data bits in digit-subset order, base, block bits, block digits, trailer bits
PROFILES = {
    P0: (0x05, (0x01, 0x04), 3, 19, 12, (0x01, 0x04)),
    P1: (0x19, (0x01, 0x08, 0x10), 7, 14, 5, (0x08, 0x10)),
    P2: (0x1D, (0x01, 0x04, 0x08, 0x10), 15, 31, 8, (0x08, 0x10)),
    P3: (0xF8, (0x08, 0x10, 0x20, 0x40, 0x80), 31, 29, 6, (0x08, 0x10)),
}
PRE_HIGH, PRE_PAD, PRE_LOW = 0x18, 0xE0, 0x05
HEADER_PROFILE = {P0: P0, P1: P1, P2: P1, P3: P3}
PREAMBLE = {P0: PRE_LOW, P1: PRE_HIGH, P2: PRE_HIGH, P3: PRE_PAD}


def digits_for(base, r):
    n, p = 0, 1
    while p < (1 << r):
        p *= base
        n += 1
    return n


def crc8(data):
    c = 0
    for b in data:
        c ^= b
        for _ in range(8):
            c = ((c << 1) ^ 0x07) & 0xFF if c & 0x80 else (c << 1) & 0xFF
    return c


def crc16(data, c=0xFFFF):
    for b in data:
        c ^= b << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
    return c


def to_digits(data, nbits, profile):
    """The first nbits of data (MSB first) as the profile's digits."""
    _, _, base, bb, bd, _ = PROFILES[profile]
    v = int.from_bytes(data, "big") >> (len(data) * 8 - nbits)
    out, left = [], nbits
    while left:
        bits = min(bb, left)
        block = (v >> (left - bits)) & ((1 << bits) - 1)
        n = bd if bits == bb else digits_for(base, bits)
        ds = []
        for _ in range(n):
            ds.append(block % base)
            block //= base
        out += reversed(ds)
        left -= bits
    return out


def mask_of(profile, d):
    """Digit d as the set of LED bits a transition flips."""
    s, bits = d + 1, PROFILES[profile][1]
    return sum(b for i, b in enumerate(bits) if s >> i & 1)


def header(profile, type_, seq, length, version=0):
    h = bytes([version << 6 | profile << 4 | type_, seq, length])
    return h + bytes([crc8(h)])


def sections(payload, type=1, seq=0, profile=P1):
    """A frame's two sections as (profile, digits): the header, then the
    payload and its CRC-16."""
    h = header(profile, type, seq, len(payload))
    body = payload + crc16(h + payload).to_bytes(2, "big")
    return [(HEADER_PROFILE[profile], to_digits(h, 32, HEADER_PROFILE[profile])),
            (profile, to_digits(body, len(body) * 8, profile))]


def writes(pre, secs, start):
    """The LED bytes for a preamble (the bits it flips), sections of digits,
    and the trailer back to `start`, the LED byte before the frame. The
    trailer is the last section's profile's. Takes any digits, so the tests
    can build frames the encoder never would."""
    state, out = start, []

    def write(s):
        nonlocal state
        state = s
        out.append(s)

    for _ in range(3):
        write(state ^ pre)
    for prof, ds in secs:
        for d in ds:
            write(state ^ mask_of(prof, d))
    mask, _, _, _, _, (ta, tb) = PROFILES[secs[-1][0]]
    b = start & mask
    mid = b ^ (tb if (state & mask) == b ^ ta else ta)
    write((state & ~mask) | mid)
    write((state & ~mask) | b)
    return out


def encode(payload, type=1, seq=0, profile=P1, user=0x00):
    payload = bytes(payload)
    if len(payload) > MAX_PAYLOAD or not 0 <= type < 16 or not 0 <= seq < 256 or profile not in PROFILES:
        raise ValueError("payload <= 48 bytes, type 0-15, seq 0-255, profile P0-P3")
    # Bits 3-7 clear: the baseline.
    return writes(PREAMBLE[profile], sections(payload, type, seq, profile), user & 0x07)
