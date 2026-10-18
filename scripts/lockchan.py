"""The lock-LED channel's reference encoder (plans/LOCK-LED-CHANNEL-PLAN.md,
"The wire format"): a frame as the sequence of LED bytes a host writes, one
write each. The board's receiver is lockchan.c; scripts/lockchan_test.py checks
the two against each other.

    from lockchan import encode, P0, P1, P2
    writes = encode(b"payload", type=1, seq=7, profile=P1, user=0x00)

`user` is the host's real lock state (Num 0x01, Caps 0x02, Scroll 0x04). Every
write keeps its Caps, and the frame ends back on it. A production sender reads
Caps again before each write and abandons the frame when it changes; this
reference takes it as fixed.
"""
P0, P1, P2 = 0, 1, 2
CAPS = 0x02
MAX_PAYLOAD = 48

# mask, data bits in digit-subset order, base, block bits, block digits, trailer bits
PROFILES = {
    P0: (0x05, (0x01, 0x04), 3, 19, 12, (0x01, 0x04)),
    P1: (0x19, (0x01, 0x08, 0x10), 7, 14, 5, (0x08, 0x10)),
    P2: (0x1D, (0x01, 0x04, 0x08, 0x10), 15, 31, 8, (0x08, 0x10)),
}
PRE_HIGH, PRE_LOW = 0x18, 0x05


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


def header(profile, type_, seq, length):
    h = bytes([profile << 4 | type_, seq, length])
    return h + bytes([crc8(h)])


def encode(payload, type=1, seq=0, profile=P1, user=0x00):
    payload = bytes(payload)
    if len(payload) > MAX_PAYLOAD or not 0 <= type < 16 or not 0 <= seq < 256 or profile not in PROFILES:
        raise ValueError("payload <= 48 bytes, type 0-15, seq 0-255, profile P0-P2")
    state = user & 0x07    # bits 3-4 clear: the baseline
    base = state
    out = []

    def write(s):
        nonlocal state
        state = s
        out.append(s)

    hdr_profile = P0 if profile == P0 else P1
    pre = PRE_LOW if profile == P0 else PRE_HIGH
    for _ in range(3):
        write(state ^ pre)
    h = header(profile, type, seq, len(payload))
    for d in to_digits(h, 32, hdr_profile):
        write(state ^ mask_of(hdr_profile, d))
    body = payload + crc16(h + payload).to_bytes(2, "big")
    for d in to_digits(body, len(body) * 8, profile):
        write(state ^ mask_of(profile, d))
    mask, _, _, _, _, (ta, tb) = PROFILES[profile]
    b = base & mask
    mid = b ^ (tb if (state & mask) == b ^ ta else ta)
    write((state & ~mask) | mid)
    write((state & ~mask) | b)
    return out
