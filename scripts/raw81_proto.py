"""Raw HID over the 2.4G dongle, stage 2: the host side of the protocol
(plans/RAWHID-24G-PLAN.md, "Stage 2, revision 3"). Pure code, no I/O, so the
Windows and macOS scripts share it and the Rust daemon has a reference to
check itself against. The board's side is bluetooth/raw81.c.

A request is a 32-byte output report on the dongle's FF60 interface:

    [0] 0xC7  [1] session (nonzero)  [2..3] seq, big-endian
    [4..5] CRC-16/CCITT-FALSE over [0..3] and [6..31]  [6..31] inner (26 bytes)

The board answers with two 32-byte input reports: the handler's reply (byte
for byte what the same command returns over USB; status 0 only), then a
receipt:

    [0] 0xC7  [1] session  [2..3] seq  [4] status  [5..6] last admitted seq
    [7..8] CRC-16 of the reply (status 0)  [9..10] CRC-16 of [0..8]  [11..31] 0
"""

MAGIC = 0xC7
HEAD = 6
INNER = 26
STATUS = {0: "ok", 1: "bad CRC", 2: "not allowed", 3: "stale", 4: "session busy"}


def crc16(data, crc=0xFFFF):
    """CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, no reflection, no xorout.
    crc16(b"123456789") == 0x29B1."""
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


MAGIC_REQ = 0xC6        # envelope v2 (masked); the receipt keeps MAGIC (0xC7)
WINDOW_BYTES = (0x5A, 0x5B, 0x5C, 0x61)   # what an older board's 3-byte window acts on


def pick_mask(rest):
    """The lowest mask under which no byte of `rest` (inner[1..25]) nor the
    mask itself is a window byte. One always exists: each byte excludes at
    most 4 masks (<= 100 of 256), the mask's own exclusion 4 more."""
    for m in range(256):
        if m not in WINDOW_BYTES and all((b ^ m) not in WINDOW_BYTES for b in rest):
            return m
    raise AssertionError("no mask: impossible for 25 bytes")


def envelope(session, seq, inner):
    """The 32-byte request (envelope v2) for one inner command: at most 26
    bytes, starting 0x07 (every allow-listed command does; it is not sent).
    [0] 0xC6 [1] session [2..3] seq [4..5] CRC over [0..3]+[6..31] [6] mask
    [7..31] inner[1..25] XOR mask."""
    if not 1 <= session <= 255:
        raise ValueError("session is 1..255")
    inner = bytes(inner)
    if len(inner) > INNER:
        raise ValueError(f"inner command is {len(inner)} bytes, at most {INNER}")
    if not inner or inner[0] != 0x07:
        raise ValueError("inner commands start 0x07")
    inner = inner + bytes(INNER - len(inner))
    rest = inner[1:]
    m = pick_mask(rest)
    body = bytes([m]) + bytes(b ^ m for b in rest)
    head = bytes([MAGIC_REQ, session, (seq >> 8) & 0xFF, seq & 0xFF])
    c = crc16(head + body)
    return head + bytes([c >> 8, c & 0xFF]) + body


def wire_sum(payload):
    """The module's sum after an 81 frame's 32 bytes."""
    return (0x81 + sum(payload)) & 0xFF


def frame_is_inert(payload):
    """No byte of the whole wire frame (81, the 32 bytes, the sum) is a window byte."""
    return not any(b in WINDOW_BYTES for b in bytes([0x81]) + bytes(payload) + bytes([wire_sum(payload)]))


def safe_session(s):
    return 1 <= s <= 255 and s not in WINDOW_BYTES and s != 0x81


def clean_seq(seq):
    """The first seq >= seq (mod 2^16) whose two bytes are not window bytes:
    a bad high byte jumps to the next high byte's start (stepping one at a
    time from 0x5A00 would never leave it)."""
    seq &= 0xFFFF
    while True:
        hi, lo = seq >> 8, seq & 0xFF
        if hi in WINDOW_BYTES:
            seq = (((hi + 1) & 0xFF) << 8)
            continue
        if lo in WINDOW_BYTES:
            seq = (seq + 1) & 0xFFFF
            continue
        return seq


def safe_request(session, seq_from, inner, tries=64):
    """The first seq from seq_from (counting up, skipping seqs with a window
    byte) whose v2 request is inert, header, CRC and sum included:
    (seq, payload), or None. Every seq skipped counts toward the session's
    span (the caller's seq moves past them)."""
    if not safe_session(session):
        return None
    seq = clean_seq(seq_from)
    for _ in range(tries):
        p = envelope(session, seq, inner)
        if frame_is_inert(p):
            return seq, p
        seq = clean_seq(seq + 1)
    return None


def parse_receipt(r):
    """A dict if r is a well-formed receipt (magic and its own CRC), else None."""
    r = bytes(r)
    if len(r) < 11 or r[0] != MAGIC or crc16(r[:9]) != (r[9] << 8 | r[10]) or any(r[11:32]):
        return None
    return {"session": r[1], "seq": r[2] << 8 | r[3], "status": r[4],
            "last_seq": r[5] << 8 | r[6], "reply_crc": r[7] << 8 | r[8]}


def reply_shaped(reply, inner):
    """Is this report shaped like an answer to `inner`? Its first three bytes
    are the request's, or 0xFF (unhandled) and then the request's [1..2]."""
    inner = bytes(inner)
    return len(reply) == 32 and reply[1:3] == inner[1:3] and reply[0] in (inner[0], 0xFF)


class Transaction:
    """One request in flight: plans/RAWHID-24G-PLAN.md, R4-3, rule by rule.

    Per 32-byte report (a 33-byte one with a leading 0x00 report id is
    stripped; any other length is ignored):
      - starting 0xC7: its own CRC fails -> closes UNKNOWN (a corrupt receipt
        may be ours); valid but another session or seq -> ignored; ours:
        status 0 with a candidate whose CRC matches -> OK, status 0 otherwise
        -> INCOMPLETE, status 1-4 -> NAK, any other status -> UNKNOWN;
      - otherwise: shaped like an answer to this command -> the candidate (the
        latest wins); not -> ignored.
    The caller enforces the deadline: timeout() closes UNKNOWN and discards
    the candidate; a reconnect does the same.

        t = Transaction(session, seq, inner); done = t.feed(report) ...
        t.result in ("ok", "nak", "incomplete", "unknown"); t.reply; t.status
    """

    def __init__(self, session, seq, inner):
        self.session, self.seq = session, seq & 0xFFFF
        self.inner = bytes(inner) + bytes(INNER - len(bytes(inner)))
        self.candidate = None
        self.result = None     # None while open
        self.reply = None
        self.status = None
        self.receipt = None

    def _close(self, result):
        self.result = result
        if result != "ok":
            self.candidate = None

    def feed(self, report):
        if self.result is not None:
            return True
        r = bytes(report)
        if len(r) == 33 and r[0] == 0x00:
            r = r[1:]
        if len(r) != 32:
            return False
        if r[0] == MAGIC:
            rc = parse_receipt(r)
            if rc is None:
                self._close("unknown")
                return True
            if rc["session"] != self.session or rc["seq"] != self.seq:
                return False
            self.receipt, self.status = rc, rc["status"]
            if rc["status"] == 0:
                if self.candidate is not None and crc16(self.candidate) == rc["reply_crc"]:
                    self.reply = self.candidate
                    self._close("ok")
                else:
                    self._close("incomplete")
            elif 1 <= rc["status"] <= 4:
                self._close("nak")
            else:
                self._close("unknown")
            return True
        if reply_shaped(r, self.inner):
            self.candidate = r
        return False

    def timeout(self):
        if self.result is None:
            self._close("unknown")


def _selftest():
    assert crc16(b"123456789") == 0x29B1
    r = envelope(5, 0x1234, b"\x07\x13\x01")
    assert len(r) == 32 and r[:4] == bytes([MAGIC_REQ, 5, 0x12, 0x34])
    assert crc16(r[:4] + r[6:]) == (r[4] << 8 | r[5])
    m = r[6]
    assert bytes([0x07]) + bytes(b ^ m for b in r[7:]) == b"\x07\x13\x01" + bytes(23)
    # Every printable text line and any playback value masks clean.
    import random
    rng = random.Random(6)
    for _ in range(2000):
        line = bytes(rng.randrange(0x20, 0x7F) for _ in range(21))
        for inner in (b"\x07\x12\x03\x00\x01" + line, b"\x07\x12\x04\x01" + bytes(rng.randrange(256) for _ in range(4))):
            p = envelope(9, rng.randrange(65536), inner)
            assert not any(b in WINDOW_BYTES for b in p[6:]), p.hex()
            got = safe_request(9, rng.randrange(65536), inner)
            assert got and frame_is_inert(got[1])
    assert safe_request(0x5A, 0, b"\x07\x13\x01") is None
    for start in (0x5A00, 0x5AFF, 0x6100, 0x005A, 0xFFFF):   # a bad high byte: codex's probe(1, 0x5A00, 64)
        got = safe_request(1, start, b"\x07\x13\x01")
        assert got and frame_is_inert(got[1]) and (got[0] >> 8) not in WINDOW_BYTES, (hex(start), got)

    def receipt(session, seq, status, reply=None, last=0):
        out = bytearray(32)
        out[0], out[1], out[2], out[3], out[4] = MAGIC, session, seq >> 8, seq & 0xFF, status
        out[5], out[6] = last >> 8, last & 0xFF
        if status == 0 and reply is not None:
            c = crc16(reply); out[7], out[8] = c >> 8, c & 0xFF
        c = crc16(bytes(out[:9])); out[9], out[10] = c >> 8, c & 0xFF
        return bytes(out)

    q = b"\x07\x13\x01"
    reply = q + bytes(range(29))
    T = lambda: Transaction(5, 7, q)
    t = T(); t.feed(reply); assert t.feed(receipt(5, 7, 0, reply)) and t.result == "ok" and t.reply == reply
    t = T(); assert t.feed(receipt(5, 7, 0, reply)) and t.result == "incomplete"               # receipt first
    t = T(); t.feed(reply); t.feed(receipt(5, 6, 0, reply)); assert t.result is None           # older seq: ignored
    t = T(); t.feed(reply); t.feed(receipt(9, 7, 0, reply)); assert t.result is None           # other session: ignored
    t = T(); t.feed(q + bytes(29)); t.feed(receipt(5, 7, 0, reply)); assert t.result == "incomplete"  # stale candidate
    t = T(); bad = bytearray(receipt(5, 7, 0, reply)); bad[4] = 2; t.feed(reply); t.feed(bytes(bad))
    assert t.result == "unknown" and t.candidate is None                                       # corrupt receipt
    t = T(); t.feed(receipt(5, 7, 2)); assert t.result == "nak" and t.status == 2
    t = T(); t.feed(receipt(5, 7, 9)); assert t.result == "unknown"                            # unknown status
    t = T(); t.feed(bytes([0]) + reply); t.feed(bytes([0]) + receipt(5, 7, 0, reply)); assert t.result == "ok"
    t = T(); t.feed(b"\x07\x12\x04" + bytes(29)); t.feed(receipt(5, 7, 0, b"\x07\x12\x04" + bytes(29)))
    assert t.result == "incomplete"                                                            # wrong shape ignored
    unh = b"\xff" + reply[1:]
    t = T(); t.feed(unh); t.feed(receipt(5, 7, 0, unh)); assert t.result == "ok" and t.reply[0] == 0xFF
    t = T(); t.feed(reply); t.timeout(); assert t.result == "unknown" and t.candidate is None
    t = T(); t.feed(reply); t.feed(receipt(5, 7, 0, reply)); t.feed(receipt(5, 7, 2)); assert t.result == "ok"  # closed
    print("raw81_proto: self-test passed")


if __name__ == "__main__":
    _selftest()
