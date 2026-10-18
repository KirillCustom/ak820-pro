"""The lock-LED channel's codec, tested on the host: the board's receiver
(lockchan.c, compiled here with the system cc) against the reference encoder
(scripts/lockchan.py). No keyboard involved.

    python3 scripts/lockchan_test.py [-v]

It checks round trips in every profile, repeats and unused bits ignored, and
that no fault (a lost, inserted, substituted or merged report, a Caps change, a
pause, a link drop, garbage after a valid preamble, human lock presses) ever
commits a wrong frame. Exit status 1 on any failure.
"""
import ctypes, os, random, subprocess, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lockchan import encode, P0, P1, P2, PROFILES  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro")
SHIM = r"""
#include "lockchan.h"
#include <string.h>
size_t lc_size(void) { return sizeof(lockchan_t); }
void lc_get(const lockchan_t *lc, uint32_t *o) {
    o[0] = lc->frames; o[1] = lc->preambles;
    for (int i = 0; i < LOCKCHAN_NABORTS; i++) o[2 + i] = lc->aborts[i];
    o[11] = lc->last.profile; o[12] = lc->last.type; o[13] = lc->last.seq; o[14] = lc->last.len;
    o[15] = lc->last.anchor_ms; o[16] = lc->last.commit_ms;
}
void lc_payload(const lockchan_t *lc, uint8_t *o) { memcpy(o, lc->last.payload, LOCKCHAN_MAX_PAYLOAD); }
"""
ABORTS = ["gap", "lifetime", "caps", "header_crc", "header", "range", "crc", "trailer", "reset"]
fails = 0
VERBOSE = "-v" in sys.argv


def build():
    d = tempfile.mkdtemp()
    shim = os.path.join(d, "shim.c")
    with open(shim, "w") as f:
        f.write(SHIM)
    lib = os.path.join(d, "liblockchan.dylib")
    subprocess.run(["cc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC",
                    "-I", SRC, os.path.join(SRC, "lockchan.c"), shim, "-o", lib], check=True)
    return ctypes.CDLL(lib)


L = build()
L.lc_size.restype = ctypes.c_size_t
L.lockchan_feed.restype = ctypes.c_bool
L.lockchan_busy.restype = ctypes.c_bool


class Rx:
    def __init__(self):
        self.buf = ctypes.create_string_buffer(L.lc_size())
        L.lockchan_init(self.buf)

    def feed(self, v, t):
        return L.lockchan_feed(self.buf, ctypes.c_uint8(v), ctypes.c_uint32(t & 0xFFFFFFFF))

    def poll(self, t, linked=True):
        L.lockchan_poll(self.buf, ctypes.c_uint32(t & 0xFFFFFFFF), ctypes.c_bool(linked))

    def busy(self):
        return L.lockchan_busy(self.buf)

    def stats(self):
        o = (ctypes.c_uint32 * 17)()
        L.lc_get(self.buf, o)
        p = (ctypes.c_uint8 * 48)()
        L.lc_payload(self.buf, p)
        return {"frames": o[0], "preambles": o[1], "aborts": dict(zip(ABORTS, o[2:11])),
                "profile": o[11], "type": o[12], "seq": o[13], "len": o[14],
                "anchor": o[15], "commit": o[16], "payload": bytes(p[:o[14]])}


def check(name, cond, detail=""):
    global fails
    if not cond:
        fails += 1
        print(f"FAIL {name} {detail}")
    elif VERBOSE:
        print(f"ok   {name}")


def timeline(writes, rng, t0, step=31, jitter=8):
    t, out = t0, []
    for w in writes:
        out.append((w, t))
        t += step + rng.randint(-jitter, jitter)
    return out, t


def random_frame(rng, profile=None):
    profile = rng.choice([P0, P1, P2]) if profile is None else profile
    n = rng.randint(0, 48)
    return dict(payload=bytes(rng.randrange(256) for _ in range(n)), type=rng.randrange(16),
                seq=rng.randrange(256), profile=profile, user=rng.randrange(8))


def matches(st, f):
    return (st["payload"] == f["payload"] and st["type"] == f["type"] and st["seq"] == f["seq"]
            and st["profile"] == f["profile"] and st["len"] == len(f["payload"]))


def run_frame(rx, f, writes, t, rng, settle=True):
    """Feed one frame's writes; returns (committed, end time)."""
    tl, t = timeline(writes, rng, t)
    got = False
    for v, tt in tl:
        got = rx.feed(v, tt) or got
        rx.poll(tt + 1)
    return got, t


def test_roundtrip(rng):
    rx, t = Rx(), 1000
    rx.feed(0, t)
    t += 500
    for i in range(3000):
        f = random_frame(rng)
        w = encode(**f)
        rx.feed(f["user"], t)          # the user's state, then quiet
        t += 300
        tl, t_end = timeline(w, rng, t)
        before = rx.stats()["frames"]
        got = False
        for k, (v, tt) in enumerate(tl):
            r = rx.feed(v, tt)
            if k < len(tl) - 1 and r:
                check("roundtrip early commit", False, f"frame {i}")
            got = got or r
            if k == 3:
                check("busy mid-frame", rx.busy(), f"frame {i}")
        st = rx.stats()
        check("roundtrip commit", got and st["frames"] == before + 1, f"frame {i} {f} {st}")
        check("roundtrip content", matches(st, f), f"frame {i}")
        check("roundtrip anchor", st["anchor"] == tl[2][1], f"frame {i}")
        check("roundtrip ends on the user's lights", w[-1] == f["user"] & 0x07, f"frame {i}")
        check("idle after commit", not rx.busy(), f"frame {i}")
        t = t_end + 300
    return rx.stats()


def test_repeats_and_unused_bits(rng):
    """Repeated reports, and (P1 only) Scroll moving under the frame, change nothing."""
    rx, t = Rx(), 1000
    rx.feed(0, t)
    t += 500
    bad = 0
    for i in range(1000):
        f = random_frame(rng, rng.choice([P0, P1, P2]))
        w = encode(**f)
        out = []
        scroll = 0
        for k, v in enumerate(w):
            if f["profile"] == P1 and k > 3 and rng.random() < 0.05:
                scroll ^= 0x04             # another keyboard's Scroll Lock
            out.append(v ^ scroll)
            if rng.random() < 0.3:
                out.append(v ^ scroll)     # a repeat
        rx.feed(f["user"] & 0x07, t)
        t += 300
        got, t = run_frame(rx, f, out, t, rng)
        st = rx.stats()
        if not (got and matches(st, f)):
            bad += 1
        t += 300
    check("repeats and Scroll under P1", bad == 0, f"{bad} of 1000 frames lost")


def fault(rng, w):
    """One fault at a random place. Returns (writes, kind)."""
    w = list(w)
    i = rng.randrange(len(w))
    kind = rng.choice(["delete", "insert", "substitute", "caps", "swap", "delete2"])
    if kind == "delete":
        del w[i]
    elif kind == "delete2":            # two in a row lost (or merged)
        del w[i:i + 2]
    elif kind == "insert":
        w.insert(i, rng.randrange(32))
    elif kind == "substitute":
        w[i] = rng.randrange(32)
    elif kind == "caps":               # the user (or the Mac) changes Caps mid-frame
        w = w[:i] + [v ^ 0x02 for v in w[i:]]
    elif kind == "swap" and i + 1 < len(w):
        w[i], w[i + 1] = w[i + 1], w[i]
    return w, kind


def test_faults(rng):
    rx, t = Rx(), 1000
    rx.feed(0, t)
    t += 500
    wrong = recovered = clean_lost = 0
    kinds = {}
    for i in range(20000):
        f = random_frame(rng)
        w, kind = fault(rng, encode(**f))
        rx.feed(f["user"], t)
        t += 300
        before = rx.stats()["frames"]
        got, t = run_frame(rx, f, w, t, rng)
        st = rx.stats()
        if got:
            if matches(st, f):
                recovered += 1
                kinds[kind] = kinds.get(kind, 0) + 1
            else:
                wrong += 1
                print(f"  WRONG COMMIT after {kind}: sent {f}, got {st}")
        t += 300
        # The next clean frame always gets through.
        g = random_frame(rng)
        rx.feed(g["user"], t)
        t += 300
        got2, t = run_frame(rx, g, encode(**g), t, rng)
        if not (got2 and matches(rx.stats(), g)):
            clean_lost += 1
        t += 300
    check("faults never commit a wrong frame", wrong == 0, f"{wrong} wrong")
    check("a clean frame after any fault", clean_lost == 0, f"{clean_lost} lost")
    if VERBOSE:
        print(f"     faulted frames that still decoded exactly (harmless faults): {recovered} {kinds}")
    return rx.stats()


def test_garbage_after_preamble(rng):
    rx, t = Rx(), 1000
    rx.feed(0, t)
    t += 500
    for i in range(20000):
        user = rng.randrange(8)
        pre = [user ^ 0x18, user, user ^ 0x18] if rng.random() < 0.7 else [user ^ 0x05, user, user ^ 0x05]
        junk = [rng.randrange(32) & ~0x02 | (user & 0x02) for _ in range(rng.randint(10, 300))]
        rx.feed(user, t)
        t += 300
        _, t = run_frame(rx, None, pre + junk, t, rng)
        t += 300
    st = rx.stats()
    check("garbage after a preamble never commits", st["frames"] == 0, f"{st['frames']} frames")
    check("garbage reached sync", st["preambles"] > 15000, f"{st['preambles']}")
    if VERBOSE:
        print(f"     aborts: {st['aborts']}")


def test_human(rng):
    """An hour of lock presses seconds apart, from any keyboard: nothing syncs."""
    rx, t, state = Rx(), 1000, 0
    rx.feed(0, t)
    for _ in range(3600):
        t += rng.randint(200, 3000)
        state ^= rng.choice([0x01, 0x02, 0x04, 0x05, 0x03, 0x06, 0x07])
        rx.feed(state, t)
        rx.poll(t + 1)
    st = rx.stats()
    check("human presses never sync", st["preambles"] == 0 and st["frames"] == 0, f"{st}")


def test_timing(rng):
    # A 200 ms pause mid-frame aborts (in feed, or in poll), and the next frame decodes.
    rx = Rx()
    rx.feed(0, 0)
    f = dict(payload=b"pause test", type=1, seq=1, profile=P1, user=0)
    w = encode(**f)
    t = 500
    for k, v in enumerate(w):
        if k == 20:
            rx.poll(t + 160)
            check("poll ends a stalled frame", not rx.busy())
            t += 200
        rx.feed(v, t)
        t += 31
    check("pause aborts", rx.stats()["frames"] == 0 and rx.stats()["aborts"]["gap"] >= 1, f"{rx.stats()['aborts']}")
    got, t = run_frame(rx, f, w, t + 300, rng)
    check("frame after a pause", got and matches(rx.stats(), f))
    # Too slow: 140 ms a write stays under the gap but outlives 12 s.
    rx = Rx()
    rx.feed(0, 0)
    w = encode(bytes(48), type=1, seq=2, profile=P0, user=0)
    t = 500
    for v in w:
        rx.feed(v, t)
        rx.poll(t)
        t += 140
    check("lifetime", rx.stats()["frames"] == 0 and rx.stats()["aborts"]["lifetime"] >= 1, f"{rx.stats()['aborts']}")
    # A link drop mid-frame resets, and the next frame decodes.
    rx = Rx()
    rx.feed(0, 0)
    t = 500
    for k, v in enumerate(encode(b"drop", profile=P2)):
        if k == 10:
            rx.poll(t, linked=False)
            t += 50
            rx.poll(t, linked=True)
        rx.feed(v, t)
        t += 31
    check("link drop resets", rx.stats()["frames"] == 0 and rx.stats()["aborts"]["reset"] == 1, f"{rx.stats()}")
    f = dict(payload=b"after drop", type=3, seq=9, profile=P2, user=0)
    rx.feed(0, t + 300)
    got, t = run_frame(rx, f, encode(**f), t + 600, rng)
    check("frame after a link drop", got and matches(rx.stats(), f))
    # Back to back: the next preamble needs 100 ms of quiet after the last trailer.
    for quiet, want in ((50, 0), (150, 1)):
        rx = Rx()
        rx.feed(0, 0)
        a = dict(payload=b"first", type=1, seq=1, profile=P1, user=0)
        b = dict(payload=b"second", type=1, seq=2, profile=P1, user=0)
        _, t = run_frame(rx, a, encode(**a), 500, rng)
        got, _ = run_frame(rx, b, encode(**b), t - 31 + quiet, rng)
        check(f"second frame after {quiet} ms", got == bool(want), f"got {got}")


def test_timer_wrap(rng):
    rx = Rx()
    t = 0xFFFFFFFF - 2000
    rx.feed(0, t)
    f = dict(payload=b"wraps the 32-bit ms timer", type=2, seq=3, profile=P2, user=0x02)
    got, _ = run_frame(rx, f, encode(**f), t + 300, rng)
    check("across the timer's wrap", got and matches(rx.stats(), f))


def report_sizes():
    text = bytes(range(48))
    for prof, name in ((P0, "P0 Num+Scroll"), (P1, "P1 Num+bits 3-4"), (P2, "P2 four lights")):
        for n in (29, 37, 48):
            w = len(encode(text[:n], profile=prof))
            print(f"     {name:16} {n:2}-byte payload: {w:3} writes, ~{w * 31 / 1000:.1f} s at 31 ms")


def main():
    rng = random.Random(820)
    st = test_roundtrip(rng)
    if VERBOSE:
        print(f"     roundtrip: {st['frames']} frames, aborts {st['aborts']}")
    test_repeats_and_unused_bits(rng)
    test_faults(rng)
    test_garbage_after_preamble(rng)
    test_human(rng)
    test_timing(rng)
    test_timer_wrap(rng)
    if VERBOSE:
        report_sizes()
    print("lockchan: all passed" if not fails else f"lockchan: {fails} FAILED")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
