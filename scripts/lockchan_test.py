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

    def link_lost(self):
        L.lockchan_link_lost(self.buf)

    def warm(self, user, t):
        """What a sender does first: a change and back, so the board has the
        baseline whatever it saw last, then the quiet. Returns the time after."""
        self.feed((user & 0x07) ^ 0x08, t)
        self.feed(user & 0x07, t + 31)
        return t + 31 + 300

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


def fault(rng, tl):
    """One fault on a timeline of (value, time), at a random place. Removed
    reports keep the others' times, so a loss leaves its real gap; inserted
    and substituted values keep the neighbours' Caps, so they reach the CRC
    and trailer checks instead of the Caps abort. Returns (timeline, kind)."""
    tl = list(tl)
    i = rng.randrange(len(tl))
    caps = tl[i][0] & 0x02
    kind = rng.choice(["delete", "burst", "insert", "substitute", "caps", "swap", "dup"])
    if kind == "delete":
        del tl[i]
    elif kind == "burst":              # 2-10 in a row lost (or merged)
        del tl[i:i + rng.randint(2, 10)]
    elif kind == "insert":
        t_prev = tl[i - 1][1] if i else tl[i][1] - 31
        tl.insert(i, ((rng.randrange(32) & ~0x02) | caps, (t_prev + tl[i][1]) // 2))
    elif kind == "substitute":
        tl[i] = ((rng.randrange(32) & ~0x02) | caps, tl[i][1])
    elif kind == "caps":               # the user (or the Mac) changes Caps mid-frame
        tl = tl[:i] + [(v ^ 0x02, t) for v, t in tl[i:]]
    elif kind == "swap" and i + 1 < len(tl):
        (a, ta), (b, tb) = tl[i], tl[i + 1]
        tl[i], tl[i + 1] = (b, ta), (a, tb)
    elif kind == "dup":
        tl.insert(i, (tl[i][0], tl[i][1]))
    return tl, kind


def feed_checked(rx, tl, f):
    """Feed a timeline; every commit must be exactly f. Returns (commits, wrong)."""
    commits = wrong = 0
    for v, tt in tl:
        if rx.feed(v, tt):
            commits += 1
            if f is None or not matches(rx.stats(), f):
                wrong += 1
        rx.poll(tt + 1)
    return commits, wrong


def test_faults(rng):
    rx, t = Rx(), 1000
    wrong = recovered = clean_lost = 0
    kinds = {}
    for i in range(30000):
        f = random_frame(rng)
        t = rx.warm(f["user"], t)
        tl, t_end = timeline(encode(**f), rng, t)
        tl, kind = fault(rng, tl)
        commits, bad = feed_checked(rx, tl, f)
        wrong += bad
        if commits and not bad:
            recovered += 1
            kinds[kind] = kinds.get(kind, 0) + 1
        t = t_end + 300
        # The next clean frame always gets through.
        g = random_frame(rng)
        t = rx.warm(g["user"], t)
        tl, t = timeline(encode(**g), rng, t)
        commits, bad = feed_checked(rx, tl, g)
        if commits != 1 or bad:
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
    """Lock presses from any keyboard, single lights at 20 ms to 3 s apart (a
    report changes one light at a time when a person presses keys), plus the
    odd simultaneous pair from a host restoring state: nothing ever commits,
    and the bits 3-4 preamble never starts."""
    rx, t, state = Rx(), 1000, 0
    rx.feed(0, t)
    for _ in range(50000):
        t += rng.randint(20, 3000)
        state ^= rng.choice([0x01, 0x02, 0x04] * 10 + [0x05, 0x03, 0x06, 0x07])
        rx.feed(state, t)
        rx.poll(t + 1)
    st = rx.stats()
    check("human presses never commit", st["frames"] == 0, f"{st}")
    check("any preamble human presses reach dies at the header", st["aborts"]["header_crc"] + st["aborts"]["header"] == st["preambles"], f"{st}")


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


def test_link_lost_everywhere(rng):
    """The link lost before each write of a frame, in turn: never a commit,
    and the next frame (after the sender's warm-up) always decodes."""
    bad = 0
    for prof in (P0, P1, P2):
        f = dict(payload=b"link lost here", type=4, seq=5, profile=prof, user=0x04)
        w = encode(**f)
        for k in range(len(w)):
            rx, t = Rx(), 1000
            t = rx.warm(f["user"], t)
            tl, t = timeline(w, rng, t)
            for j, (v, tt) in enumerate(tl):
                if j == k:
                    rx.link_lost()
                if rx.feed(v, tt):
                    bad += 1
            t = rx.warm(f["user"], t + 300)
            tl, _ = timeline(w, rng, t)
            commits, wrong = feed_checked(rx, tl, f)
            bad += (commits != 1) + wrong
    check("link lost before any write", bad == 0, f"{bad} bad")


def test_boundaries():
    """Quiet 99/100 ms, gap 150/151 ms and lifetime 12000/12001 ms, in feed and
    in poll, each once well clear of the 32-bit timer's wrap and once with the
    wrap falling inside the interval under test (codex, flash 2h verify)."""
    W = 1 << 32
    q = dict(payload=b"q", type=1, seq=0, profile=P1, user=0)
    for quiet, want in ((99, 0), (100, 1)):
        for t0 in (1000, W - 31 - quiet // 2):          # the wrap inside the quiet
            rx = Rx()
            rx.feed(0x08, t0)
            rx.feed(0x00, t0 + 31)
            tl = [(v, t0 + 31 + quiet + 31 * k) for k, v in enumerate(encode(**q))]
            commits, _ = feed_checked(rx, tl, q)
            check(f"quiet {quiet} ms (t0 {t0 % W:#x})", commits == want, f"{commits}")
    g = dict(payload=b"gap", type=1, seq=0, profile=P1, user=0)
    for gap, want in ((150, 1), (151, 0)):
        for t in (1331, W - 31 * 9 - gap // 2):          # the wrap inside the gap
            rx = Rx()
            rx.warm(0, t - 331)
            tl = [(v, t + 31 * k + (gap - 31 if k >= 10 else 0)) for k, v in enumerate(encode(**g))]
            commits, _ = feed_checked(rx, tl, g)
            check(f"gap {gap} ms in feed (t {t % W:#x})", commits == want, f"{commits}")
            # The same gap seen by poll alone, before the late write arrives.
            rx = Rx()
            rx.warm(0, t - 331)
            for v, tt in tl[:10]:
                rx.feed(v, tt)
            rx.poll(tl[9][1] + gap)
            check(f"gap {gap} ms in poll (t {t % W:#x})", rx.busy() == bool(want), f"busy {rx.busy()}")
    f = dict(payload=bytes(range(48)), type=6, seq=7, profile=P0, user=0)
    w = encode(**f)
    n = len(w)
    for life, want in ((12000, 1), (12001, 0)):
        for t in (1331, W - life // 2):                  # the wrap inside the frame
            rx = Rx()
            rx.warm(0, t - 331)
            # Evenly spread so the trailer's last write lands `life` ms after the
            # preamble's first, every gap under 150 ms.
            tl = [(v, t + (life * k) // (n - 1)) for k, v in enumerate(w)]
            commits, _ = feed_checked(rx, tl, f)
            check(f"lifetime {life} ms in feed (t {t % W:#x})", commits == want, f"{commits}")
            # Poll's lifetime check: the frame still running, last write < 150 ms ago.
            rx = Rx()
            rx.warm(0, t - 331)
            for v, tt in tl[:-3]:
                rx.feed(v, tt)
            rx.poll(t + life)
            check(f"lifetime {life} ms in poll (t {t % W:#x})", rx.busy() == bool(want), f"busy {rx.busy()}")


# Pinned writes for one short frame per profile: the encoder must not drift
# from what the board was built against. Regenerate only with a version bump.
GOLDEN = {
    P0: [7, 2, 7, 6, 7, 6, 7, 3, 2, 3, 6, 2, 3, 6, 3, 2, 3, 7, 2, 6, 3, 6, 2, 6, 7, 2, 3, 6, 2, 3, 7,
         6, 3, 7, 2, 7, 2, 3, 6, 7, 3, 2, 7, 3, 2, 6, 2, 7, 6, 2, 3, 2, 3, 7, 6, 7, 6, 2, 6, 2, 3, 2],
    P1: [26, 2, 26, 27, 11, 3, 10, 19, 3, 19, 27, 26, 3, 10, 11, 3, 27, 19, 27, 2, 26, 10, 26, 27, 2,
         3, 26, 10, 18, 2, 10, 18, 19, 18, 10, 18, 2],
    P2: [26, 2, 26, 27, 2, 10, 3, 11, 27, 11, 10, 18, 11, 2, 10, 2, 10, 14, 7, 15, 26, 19, 27, 26, 19,
         27, 18, 11, 18, 3, 10, 2],
}


def test_golden():
    for prof in (P0, P1, P2):
        w = encode(b"AK820", type=1, seq=42, profile=prof, user=0x02)
        if GOLDEN[prof] is None:
            print(f"     golden P{prof}: {w}")
        else:
            check(f"golden P{prof}", w == GOLDEN[prof], f"{w}")


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
    test_link_lost_everywhere(rng)
    test_boundaries()
    test_golden()
    if VERBOSE:
        report_sizes()
    print("lockchan: all passed" if not fails else f"lockchan: {fails} FAILED")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
