#!/usr/bin/env python3
"""Raw HID over the 2.4G dongle, stage 2: the board's pure module
(keyboards/a_jazz/ak820pro/bluetooth/raw81.c), compiled here with the host's
C compiler and driven through ctypes, against the host side
(scripts/raw81_proto.py). The cases are codex's (plans/RAWHID-24G-PLAN.md,
stage 2 revision 2, "The acceptance matrix"): every split of a frame across
drains, a deleted byte at every position, truncation, an 81 inside a 5A
payload, `5C 25 | 81`, a pending `61 0D` across a frame, a UART error under a
frame, CRC-valid forbidden commands, stale and wrapped seqs, link loss.

    python scripts/raw81_test.py [--cc gcc]

The byte stream runs through a model of ch582f_ajazz.c's drain: each byte to
raw81_byte(), TO_WINDOW bytes through the real 3-byte window rules (5A/5B/5C
with a good sum, 61 0D 0A), raw81_window_done() after each, raw81_empty() at a
drain's end. Times are simulated. Exits 1 on any failure.
"""
import argparse, ctypes, os, random, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.environ.get("RAW81_SRC") or os.path.join(ROOT, "qmk_firmware-ak820pro", "keyboards", "a_jazz", "ak820pro", "bluetooth", "raw81.c")
sys.path.insert(0, HERE)
import raw81_proto as P   # noqa: E402

FRAME = 34
SILENCE = 938          # ticks: TIME_MS2I(5) at 187.5 kHz
TICKS_PER_MS = 187.5
LIFETIME = 20


class RX(ctypes.Structure):
    _fields_ = [("state", ctypes.c_int), ("boundary", ctypes.c_bool), ("tainted", ctypes.c_bool),
                ("buf", ctypes.c_uint8 * 34), ("len", ctypes.c_uint8), ("start_ms", ctypes.c_uint32),
                ("last_tick", ctypes.c_uint16), ("have_last", ctypes.c_bool), ("last_seq", ctypes.c_uint8),
                ("seq", ctypes.c_uint8), ("inner", ctypes.c_uint8 * 28)] + \
               [(n, ctypes.c_uint32) for n in ("ok", "stale", "forbidden", "badenv", "badsum",
                                              "taint", "lifetime", "qbytes")]


def build(cc):
    d = tempfile.mkdtemp(prefix="raw81_")
    lib = os.path.join(d, "raw81" + (".dll" if os.name == "nt" else ".so"))
    cmd = [cc, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-shared", "-o", lib, SRC]
    if os.name != "nt":
        cmd.insert(-3, "-fPIC")
    subprocess.run(cmd, check=True)
    L = ctypes.CDLL(lib)
    L.raw81_byte.restype = ctypes.c_int
    L.raw81_byte.argtypes = [ctypes.POINTER(RX), ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint32]
    L.raw81_empty.restype = ctypes.c_bool
    L.raw81_empty.argtypes = [ctypes.POINTER(RX), ctypes.c_uint16, ctypes.c_uint16]
    L.raw81_commit.restype = ctypes.c_int
    L.raw81_commit.argtypes = [ctypes.POINTER(RX), ctypes.c_bool, ctypes.c_uint32, ctypes.c_uint32]
    L.raw81_poll.argtypes = [ctypes.POINTER(RX), ctypes.c_bool, ctypes.c_uint32, ctypes.c_uint32]
    L.raw81_window_done.argtypes = [ctypes.POINTER(RX), ctypes.c_bool]
    L.raw81_allowed.restype = ctypes.c_bool
    L.raw81_crc16.restype = ctypes.c_uint16
    L.raw81_crc16.argtypes = [ctypes.c_char_p, ctypes.c_uint8, ctypes.c_uint16]
    return L


V = {0: "OK", 1: "STALE", 2: "FORBIDDEN", 3: "BADENV", 4: "BADSUM", 5: "TAINT", 6: "LIFETIME"}


class Board:
    """ch582f_ajazz.c's drain around the module, with the real window rules."""

    def __init__(self, L):
        self.L, self.rx = L, RX()
        L.raw81_init(ctypes.byref(self.rx))
        self.w = [0, 0, 0]
        self.ms, self.tick = 1000, 0
        self.verdicts, self.frames, self.acks = [], [], 0

    def advance(self, ms):
        self.ms += ms
        self.tick = (self.tick + int(ms * TICKS_PER_MS)) & 0xFFFF

    def drain(self, data, uart_err=False, err_at_commit=False, step_ms=0):
        """One ch582_task() pass: poll, the bytes, then an empty read.
        err_at_commit: the UART error flag is first seen by the poll just
        before a commit (an error raised during this drain)."""
        self.err_at_commit = err_at_commit
        self.L.raw81_poll(ctypes.byref(self.rx), uart_err, self.ms, LIFETIME)
        for c in data:
            self.advance(step_ms)
            r = self.L.raw81_byte(ctypes.byref(self.rx), c, self.tick, self.ms)
            if r == 2:                       # STARTED: the window starts over
                self.w = [0, 0, 0]
            elif r == 3:                     # FRAME
                v = self.L.raw81_commit(ctypes.byref(self.rx), self.err_at_commit, self.ms, LIFETIME)
                self.verdicts.append((V[v], self.rx.seq, bytes(self.rx.inner) if v == 0 else None))
            elif r == 0:                     # the window's turn
                self.w = self.w[1:] + [c]
                b2, b1, b0 = self.w
                matched = False
                if (b2, b1, b0) == (0x61, 0x0D, 0x0A):
                    self.acks += 1
                    matched = True
                elif b2 in (0x5A, 0x5B, 0x5C) and b0 == (b2 + b1) & 0xFF:
                    self.frames.append((b2, b1))
                    matched = True
                if matched:
                    self.w = [0, 0, 0]
                self.L.raw81_window_done(ctypes.byref(self.rx), matched)
        if self.L.raw81_empty(ctypes.byref(self.rx), self.tick, SILENCE):
            self.w = [0, 0, 0]

    def quiet(self, ms=10):
        self.advance(ms)
        self.drain(b"")


def wire81(payload):
    return bytes([0x81]) + payload + bytes([(0x81 + sum(payload)) & 0xFF])


def short(t, d):
    return bytes([t, d, (t + d) & 0xFF])


FAIL = []


def check(name, cond, detail=""):
    if not cond:
        FAIL.append(name)
        print(f"FAIL {name} {detail}")


def fresh(L):
    b = Board(L)
    b.quiet(20)
    return b


def run(L):
    text = b"\x07\x12\x03\x00\x01Hello radio"
    health = b"\x07\x13\x01"

    # 1. A request in one drain, then in every split across two drains.
    b = fresh(L)
    b.drain(wire81(P.envelope(1, text)))
    check("whole", b.verdicts == [("OK", 1, text + bytes(28 - len(text)))], b.verdicts)
    for cut in range(1, FRAME):
        b = fresh(L)
        w = wire81(P.envelope(1, health))
        b.drain(w[:cut]); b.advance(1); b.drain(w[cut:])
        check(f"split@{cut}", [v[0] for v in b.verdicts] == ["OK"], b.verdicts)

    # 2. Short frames and ACKs around a request still parse.
    b = fresh(L)
    b.drain(short(0x5C, 0x40) + b"\x61\x0d\x0a" + wire81(P.envelope(2, health)) + short(0x5B, 0x32))
    check("around", [v[0] for v in b.verdicts] == ["OK"] and b.frames == [(0x5C, 0x40), (0x5B, 0x32)]
          and b.acks == 1, (b.verdicts, b.frames, b.acks))

    # 3. `5C 25 | 81`: the 81 is the 5C's sum, never a start.
    b = fresh(L)
    b.drain(b"\x5c\x25"); b.advance(1); b.drain(b"\x81")
    check("5C 25|81", b.frames == [(0x5C, 0x25)] and b.rx.state == 0 and not b.verdicts, (b.frames, b.rx.state))

    # 4. P3's `5A 81 DB` split after the 5A: stays in the window.
    b = fresh(L)
    b.drain(b"\x5a"); b.advance(1); b.drain(b"\x81\xdb")
    check("5A|81 DB", b.frames == [(0x5A, 0x81)] and b.rx.state == 0, (b.frames, b.rx.state))

    # 5. A pending `61 0D`, silence, a whole 81, then `0A`: no phantom ACK.
    b = fresh(L)
    b.drain(b"\x61\x0d"); b.quiet(10); b.drain(wire81(P.envelope(3, health)) + b"\x0a")
    check("no phantom ACK", b.acks == 0 and [v[0] for v in b.verdicts] == ["OK"], (b.acks, b.verdicts))

    # 6. A byte deleted at every position: never OK, never a phantom frame.
    for pos in range(FRAME):
        b = fresh(L)
        w = bytearray(wire81(P.envelope(4, text)))
        del w[pos]
        b.drain(bytes(w)); b.advance(25); b.drain(b"")
        ok = [v for v in b.verdicts if v[0] == "OK"]
        check(f"delete@{pos}", not ok and not b.frames and not b.acks, (b.verdicts, b.frames, b.acks))
        b.quiet(10)
        b.drain(wire81(P.envelope(5, health)))   # recovers after silence
        check(f"delete@{pos} recovers", b.verdicts[-1:] and b.verdicts[-1][0] == "OK", b.verdicts)

    # 7. Codex's constructed case: a TEXT_SET with one byte lost that still
    #    sums, followed by a real 5B: the CRC rejects it, the 5B is not forged.
    payload = bytearray(P.envelope(6, b"\x07\x12\x01\x02\x11"))
    w = bytearray(wire81(bytes(payload)))
    for pos in range(2, FRAME - 1):
        v = bytearray(w)
        del v[pos]
        v = bytes(v) + short(0x5B, 0x23)
        b = fresh(L)
        b.drain(v)
        check(f"deletion+5B@{pos}", not [x for x in b.verdicts if x[0] == "OK"], b.verdicts)

    # 8. Truncated (the rest never comes): lifetime, quarantine, recovery.
    b = fresh(L)
    b.drain(wire81(P.envelope(7, health))[:20]); b.advance(LIFETIME + 1); b.drain(b"")
    # The lifetime aborts it; the same empty read, 21 ms after the last byte,
    # is also silence, so the quarantine ends at once: idle, not collecting.
    check("truncated", b.rx.lifetime == 1 and b.rx.state == 0, (b.rx.lifetime, b.rx.state))
    b = fresh(L)
    b.drain(wire81(P.envelope(7, health))[:20]); b.advance(LIFETIME + 1)
    b.L.raw81_poll(ctypes.byref(b.rx), False, b.ms, LIFETIME)
    check("truncated -> quarantine before silence is judged", b.rx.state == 2, b.rx.state)
    b.quiet(10); b.drain(wire81(P.envelope(8, health)))
    check("truncated recovers", [v[0] for v in b.verdicts] == ["OK"], b.verdicts)

    # 9. A UART error during the frame taints it.
    b = fresh(L)
    w = wire81(P.envelope(9, health))
    b.drain(w[:10]); b.advance(1); b.drain(w[10:], uart_err=True)
    check("taint", b.rx.taint >= 1 and not [v for v in b.verdicts if v[0] == "OK"], (b.rx.taint, b.verdicts))
    # ... and one first seen at the commit, raised during this very drain.
    b = fresh(L)
    b.drain(wire81(P.envelope(9, health)), err_at_commit=True)
    check("taint at commit", [v[0] for v in b.verdicts] == ["TAINT"] and b.rx.state == 2, (b.verdicts, b.rx.state))
    # ... and one latched while idle, before the 81 starts.
    b = fresh(L)
    b.drain(short(0x5C, 0x40), uart_err=True); b.advance(1); b.drain(wire81(P.envelope(9, health)))
    check("taint while idle", not [v for v in b.verdicts if v[0] == "OK"] and b.rx.taint >= 1, (b.verdicts, b.rx.taint))

    # 10. Bad magic, bad CRC: BADENV and quarantine.
    for name, mut in (("magic", 0), ("crc", 2), ("inner", 10)):
        p = bytearray(P.envelope(10, health)); p[mut] ^= 0x40
        b = fresh(L); b.drain(wire81(bytes(p)))
        check(f"badenv {name}", [v[0] for v in b.verdicts] == ["BADENV"] and b.rx.state == 2, b.verdicts)

    # 10b. A valid CRC over a wrong magic: still BADENV.
    for magic in (0x00, 0x07, 0xC6, 0xFF):
        inner = health + bytes(28 - len(health))
        head = bytes([magic, 13])
        c = P.crc16(head + inner)
        b = fresh(L); b.drain(wire81(head + bytes([c >> 8, c & 0xFF]) + inner))
        check(f"magic {magic:02x}", [v[0] for v in b.verdicts] == ["BADENV"], b.verdicts)

    # 10c. A collection that crosses its lifetime inside the drain that
    #      completes it: the poll at the drain's start passed, the commit must not.
    b = fresh(L)
    w = wire81(P.envelope(14, health))
    b.drain(w[:30]); b.advance(LIFETIME - 2)
    b.drain(w[30:], step_ms=1)
    check("lifetime at commit", [v[0] for v in b.verdicts] == ["LIFETIME"], b.verdicts)

    # 11. Forbidden but well formed: FORBIDDEN, boundary kept.
    for inner in (b"\x07\x13\x05", b"\x07\x13\x79BOOT", b"\x07\x13\x76\x01", b"\x07\x10\x01",
                  b"\x07\x11\x01", b"\x05\x00\x00\x00\x00\x00", b"\x07\x13\x0e\x05", b"\x07\x12\x05"):
        b = fresh(L); b.drain(wire81(P.envelope(11, inner)) + wire81(P.envelope(12, health)))
        check(f"forbidden {inner.hex()}", [v[0] for v in b.verdicts] == ["FORBIDDEN", "OK"], b.verdicts)
    for inner in (b"\x07\x12\x01", b"\x07\x12\x02", b"\x07\x12\x03", b"\x07\x12\x04", b"\x07\x13\x01",
                  b"\x07\x13\x02", b"\x07\x13\x04", b"\x07\x13\x06", b"\x07\x13\x07", b"\x07\x13\x0e\x00",
                  b"\x07\x13\x0e\x21", b"\x07\x13\x0e\x2a"):
        check(f"allowed {inner.hex()}", L.raw81_allowed((ctypes.c_uint8 * 28)(*(inner + bytes(28 - len(inner))))))

    # 12. Freshness: a duplicate is stale; wrap; the window of 127; link loss.
    b = fresh(L)
    for s in (250, 251, 251, 255, 0, 3, 3 + 128, 4):
        b.drain(wire81(P.envelope(s, health))); b.advance(5)
    got = [v[0] for v in b.verdicts]
    check("freshness", got == ["OK", "OK", "STALE", "OK", "OK", "OK", "STALE", "OK"], got)
    b.drain(wire81(P.envelope(4, health)))
    check("repeat is stale", b.verdicts[-1][0] == "STALE", b.verdicts[-1])
    L.raw81_link_lost(ctypes.byref(b.rx))
    b.drain(wire81(P.envelope(4, health)))
    check("the same seq is fresh after link loss", b.verdicts[-1][0] == "OK", b.verdicts[-1])

    # 13. Back to back, and a random soak with junk between requests.
    b = fresh(L)
    b.drain(b"".join(wire81(P.envelope(s, health)) for s in range(1, 6)))
    check("back to back", [v[0] for v in b.verdicts] == ["OK"] * 5, b.verdicts)
    rng = random.Random(81)
    b, sent = fresh(L), 0
    for s in range(1, 400):
        junk = b"".join(rng.choice([short(0x5C, rng.randrange(101)), b"\x61\x0d\x0a", short(0x5A, rng.randrange(32))])
                        for _ in range(rng.randrange(3)))
        b.drain(junk + wire81(P.envelope(s % 256, bytes([0x07, 0x13, 0x01]))))
        sent += 1
        b.advance(rng.choice([1, 3, 7]))
    ok = sum(1 for v in b.verdicts if v[0] == "OK")
    check("soak", ok == sent, f"{ok}/{sent} {b.verdicts[-3:]}")

    # 14. The receipt and the 91 frame agree with the host side.
    reply = bytes(range(32))
    out = (ctypes.c_uint8 * 32)()
    L.raw81_receipt(out, 7, 0, True, 7, (ctypes.c_uint8 * 32)(*reply))
    r = bytes(out)
    c = P.crc16(reply)
    check("receipt", r[:7] == bytes([0xC7, 7, 0, 7, c >> 8, c & 0xFF, 1]) and r[7:] == bytes(25), r.hex())
    f = (ctypes.c_uint8 * 34)()
    L.raw81_frame91(f, (ctypes.c_uint8 * 32)(*reply))
    check("frame91", bytes(f) == bytes([0x91]) + reply + bytes([(0x91 + sum(reply)) & 0xFF]))
    check("crc16", L.raw81_crc16(b"123456789", 9, 0xFFFF) == 0x29B1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cc", default="gcc" if os.name == "nt" else "cc")
    a = ap.parse_args()
    L = build(a.cc)
    run(L)
    if FAIL:
        print(f"{len(FAIL)} FAILED")
        sys.exit(1)
    print("raw81_test: all cases pass")


if __name__ == "__main__":
    main()
