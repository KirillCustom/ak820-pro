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


def envelope(session, seq, inner):
    """The 32-byte request for one inner command (at most 26 bytes)."""
    if not 1 <= session <= 255:
        raise ValueError("session is 1..255")
    inner = bytes(inner)
    if len(inner) > INNER:
        raise ValueError(f"inner command is {len(inner)} bytes, at most {INNER}")
    inner = inner + bytes(INNER - len(inner))
    head = bytes([MAGIC, session, (seq >> 8) & 0xFF, seq & 0xFF])
    c = crc16(head + inner)
    return head + bytes([c >> 8, c & 0xFF]) + inner


def parse_receipt(r):
    """A dict if r is a well-formed receipt (magic and its own CRC), else None."""
    r = bytes(r)
    if len(r) < 11 or r[0] != MAGIC or crc16(r[:9]) != (r[9] << 8 | r[10]) or any(r[11:32]):
        return None
    return {"session": r[1], "seq": r[2] << 8 | r[3], "status": r[4],
            "last_seq": r[5] << 8 | r[6], "reply_crc": r[7] << 8 | r[8]}


class Transaction:
    """One request in flight, per codex's revision-2 verify (must-fix 2):
    hold the latest non-receipt report as the candidate reply; a valid
    receipt for this session and seq decides. Either half missing is never
    success; a reply after its receipt cannot match (the transaction is
    closed); anything else, or the timeout, is unknown.

        t = Transaction(session, seq); for each report: done = t.feed(report)
        t.result in ("ok", "nak", "incomplete", "unknown"); t.reply; t.status
    """

    def __init__(self, session, seq):
        self.session, self.seq = session, seq & 0xFFFF
        self.candidate = None
        self.result = None     # None while open
        self.reply = None
        self.status = None
        self.receipt = None

    def feed(self, report):
        if self.result is not None:
            return True
        r = bytes(report)
        if len(r) == 33:       # with the report id (hidapi on Windows)
            r = r[1:]
        if len(r) != 32:
            return False
        if r[0] == MAGIC:
            rc = parse_receipt(r)
            if rc is None or rc["session"] != self.session or rc["seq"] != self.seq:
                return False   # not ours, or corrupt: ignore, keep waiting
            self.receipt, self.status = rc, rc["status"]
            if rc["status"] != 0:
                self.result = "nak"
            elif self.candidate is not None and crc16(self.candidate) == rc["reply_crc"]:
                self.result, self.reply = "ok", self.candidate
            else:
                self.result = "incomplete"
            return True
        self.candidate = r     # the latest; a stale one is replaced or fails the CRC
        return False

    def timeout(self):
        if self.result is None:
            self.result = "unknown"


def _selftest():
    assert crc16(b"123456789") == 0x29B1
    r = envelope(5, 0x1234, b"\x07\x13\x01")
    assert len(r) == 32 and r[:4] == bytes([MAGIC, 5, 0x12, 0x34])
    assert crc16(r[:4] + r[6:]) == (r[4] << 8 | r[5])

    def receipt(session, seq, status, reply=None, last=0):
        out = bytearray(32)
        out[0], out[1], out[2], out[3], out[4] = MAGIC, session, seq >> 8, seq & 0xFF, status
        out[5], out[6] = last >> 8, last & 0xFF
        if status == 0 and reply is not None:
            c = crc16(reply); out[7], out[8] = c >> 8, c & 0xFF
        c = crc16(bytes(out[:9])); out[9], out[10] = c >> 8, c & 0xFF
        return bytes(out)

    reply = b"\x07\x13\x01" + bytes(range(29))
    t = Transaction(5, 7); t.feed(reply); assert t.feed(receipt(5, 7, 0, reply)) and t.result == "ok"
    t = Transaction(5, 7); assert t.feed(receipt(5, 7, 0, reply)) and t.result == "incomplete"   # receipt first
    t = Transaction(5, 7); t.feed(reply); t.feed(receipt(5, 6, 0, reply)); assert t.result is None  # older seq
    t = Transaction(5, 7); t.feed(reply); t.feed(receipt(9, 7, 0, reply)); assert t.result is None  # other session
    t = Transaction(5, 7); t.feed(b"\x07\x13\x01" + bytes(29)); t.feed(receipt(5, 7, 0, reply))
    assert t.result == "incomplete"                                                                # stale candidate
    t = Transaction(5, 7); bad = bytearray(receipt(5, 7, 0, reply)); bad[4] = 2; t.feed(reply); t.feed(bytes(bad))
    assert t.result is None                                                                        # corrupt receipt
    t = Transaction(5, 7); t.feed(receipt(5, 7, 2)); assert t.result == "nak" and t.status == 2
    t = Transaction(5, 7); t.feed(bytes([0]) + reply); t.feed(bytes([0]) + receipt(5, 7, 0, reply))
    assert t.result == "ok"                                                                        # with report ids
    t = Transaction(5, 7); t.feed(reply); t.timeout(); assert t.result == "unknown"
    print("raw81_proto: self-test passed")


if __name__ == "__main__":
    _selftest()
