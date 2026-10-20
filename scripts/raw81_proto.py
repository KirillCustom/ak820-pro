"""Raw HID over the 2.4G dongle, stage 2: the host side of the protocol
(plans/RAWHID-24G-PLAN.md, "Stage 2: dispatch, as designed"). Pure functions,
no I/O, so the Windows and macOS test scripts share them and the Rust daemon
has a reference to check itself against.

A request is a 32-byte output report on the dongle's FF60 interface:

    [0] 0xC7  [1] seq  [2..3] CRC-16/CCITT-FALSE, big-endian, over [0..1]
    and [4..31]  [4..31] the ordinary command (28 bytes, zero-padded)

The board answers with the handler's 32-byte reply, byte for byte what the
same command returns over USB, or with a NAK:

    [0] 0xC7  [1] the seq it received  [2] reason  [3..31] 0
"""

MAGIC = 0xC7
INNER = 28
NAK_REASONS = {1: "bad CRC", 2: "not on the allow-list", 3: "busy", 4: "not 2.4G-linked"}


def crc16(data, crc=0xFFFF):
    """CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, no reflection, no xorout.
    crc16(b"123456789") == 0x29B1."""
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


def envelope(seq, inner):
    """The 32-byte request for one inner command (at most 28 bytes)."""
    inner = bytes(inner)
    if len(inner) > INNER:
        raise ValueError(f"inner command is {len(inner)} bytes, at most {INNER}")
    inner = inner + bytes(INNER - len(inner))
    head = bytes([MAGIC, seq & 0xFF])
    c = crc16(head + inner)
    return head + bytes([c >> 8, c & 0xFF]) + inner


def is_nak(reply):
    return len(reply) >= 3 and reply[0] == MAGIC


def nak_reason(reply):
    return NAK_REASONS.get(reply[2], f"reason {reply[2]}")


def match_len(inner):
    """How many leading bytes of a reply must echo the request: the command
    header (07 <channel> <sub>), plus the page byte for the paged health
    commands (HC_LINK 0x0E)."""
    inner = bytes(inner)
    if len(inner) >= 4 and inner[0] == 0x07 and inner[1] == 0x13 and inner[2] == 0x0E:
        return 4
    return min(3, len(inner))


def answers(reply, seq, inner):
    """Classify one input report against the request in flight: 'reply',
    'nak', or None (someone else's, or stale). The reply leaves VIA with its
    first bytes as the request's (a handler that declines sets byte 0 to
    0xFF, RTC_UNHANDLED / VIA's id_unhandled), so an unhandled answer to
    THIS request still matches on bytes 1..n."""
    reply = bytes(reply)
    if is_nak(reply):
        return "nak" if reply[1] == (seq & 0xFF) else None
    n = match_len(inner)
    inner = bytes(inner)
    if reply[1:n] != inner[1:n]:
        return None
    if reply[0] == inner[0] or reply[0] == 0xFF:
        return "reply"
    return None


if __name__ == "__main__":
    assert crc16(b"123456789") == 0x29B1, hex(crc16(b"123456789"))
    r = envelope(5, b"\x07\x13\x01")
    assert len(r) == 32 and r[0] == MAGIC and r[1] == 5
    assert crc16(r[0:2] + r[4:]) == (r[2] << 8 | r[3])
    assert answers(b"\x07\x13\x01" + bytes(29), 5, b"\x07\x13\x01") == "reply"
    assert answers(b"\xff\x13\x01" + bytes(29), 5, b"\x07\x13\x01") == "reply"
    assert answers(b"\x07\x12\x04" + bytes(29), 5, b"\x07\x13\x01") is None
    assert answers(bytes([MAGIC, 5, 2]) + bytes(29), 5, b"\x07\x13\x01") == "nak"
    assert answers(bytes([MAGIC, 6, 2]) + bytes(29), 5, b"\x07\x13\x01") is None
    assert answers(b"\x07\x13\x0e\x01" + bytes(28), 1, b"\x07\x13\x0e\x01") == "reply"
    assert answers(b"\x07\x13\x0e\x02" + bytes(28), 1, b"\x07\x13\x0e\x01") is None
    print("raw81_proto: self-test passed")
