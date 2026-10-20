#!/usr/bin/env python3
"""The radio link's UART overruns, measured (plans/OVERRUN-CAPS-PLAN.md, step
1): HC_DEVHOOK ops 20-24 on a dev build that carries overrun_diag.c.

    venv/bin/python scripts/overrun_diag.py status [--json OUT]
    venv/bin/python scripts/overrun_diag.py reset
    venv/bin/python scripts/overrun_diag.py mode transaction|byte
    venv/bin/python scripts/overrun_diag.py burst N [--lo MS] [--hi MS] | burst stop
    venv/bin/python scripts/overrun_diag.py ab [--arms abcddcba] [--secs 90] [--n 1000] [--json OUT]

`status` prints the OE counts and their contexts, each PCF kind's spans, the
burst, the OE ring and the deep sleep's last eight wakes. `ab` runs the A/B:

    arm  lock span        burst
    a    per transaction  none
    b    per transaction  N reads
    c    per byte         N reads
    d    per byte         none

each for --secs (or until its burst is done, plus 10 s), under the 2.4G echo's
load (mac_raw81_echo.py at --gap-ms 0), with the board's figures zeroed at
each arm's start and read at its end. The cable's raw HID is opened only
between the echo's runs (macOS's is exclusive, and the daemon's syncs need
it). Needs the Mac's dongle, the slider on 2.4G and the cable's data.
"""
import argparse, json, os, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import deep_sleep as ds  # noqa: E402

cm = ds.cm
TICK_HZ = 187500
KINDS = ["read 1 (4 B)", "read 7 (10 B)", "write 1 (3 B)", "write 8 (9 B)"]
WAKES = ["none", "key", "backstop", "strays", "blocked", "knob", "charge"]
U16 = lambda b, o: int.from_bytes(b[o:o + 2], "little")
U32 = lambda b, o: int.from_bytes(b[o:o + 4], "little")
ms = lambda ticks: round(ticks * 1000.0 / TICK_HZ, 3)


def op(h, n, args=()):
    r = cm.xfer(h, [cm.SET_VALUE, cm.HEALTH_CH, cm.HC_DEVHOOK, n] + list(args) + [0] * (28 - len(args)))
    if len(r) < 32 or r[3] != n:
        sys.exit(f"op {n}: unhandled or another op's reply ({bytes(r[:8]).hex()}); a build with overrun_diag?")
    return bytes(r[4:32])


def summary(h, sub=0):
    b = op(h, 20, [sub])
    return {"oe_obs": U32(b, 0), "oe_drain": U32(b, 4), "rx_drop": U32(b, 8), "ring_seq": U16(b, 12),
            "byte_mode": b[14], "usb_max_ms": ms(U16(b, 16)), "st_max_ms": ms(U16(b, 18)),
            "ctx_pcf": U16(b, 20), "ctx_flash": U16(b, 22), "ctx_none": U16(b, 24), "rx_off": U16(b, 26)}


def kinds(h):
    out = []
    for k in range(4):
        b = op(h, 22, [k])
        out.append({"kind": KINDS[k], "count": U32(b, 0), "fails": U16(b, 4), "max_span_ms": ms(U16(b, 6)),
                    "max_whole_ms": ms(U16(b, 8)), "spans_ge_1ms": U32(b, 10), "spans_ge_1_4ms": U32(b, 14),
                    "spans": U32(b, 18)})
    b = op(h, 22, [0xFF])
    return out, {"ctx_sleep": U16(b, 0), "tick_hz": U32(b, 2), "now_secs": U32(b, 6)}


def ring(h, seq):
    slots = {}
    for page in range(16):
        b = op(h, 21, [page])
        for j in range(2):
            e = b[14 * j:14 * j + 14]
            slots[2 * page + j] = {"seq": U16(e, 0), "secs": U32(e, 2), "act": e[6], "kind": e[7] & 3,
                                   "in_drain": bool(e[7] & 0x40), "byte_mode": bool(e[7] & 0x80),
                                   "span_ms": ms(U16(e, 8)), "usb_max_ms": ms(U16(e, 10)),
                                   "st_max_ms": ms(U16(e, 12))}
    n = min(seq, 32)
    return [slots[s % 32] for s in range(seq - n, seq)]


def burst(h, sub=0, n=0, lo=40, hi=80):
    b = op(h, 23, [sub, n & 0xFF, n >> 8, lo, hi])
    return {"left": U16(b, 0), "done": U16(b, 2), "failed": U16(b, 4), "exposure_ms": U32(b, 6),
            "active": bool(b[10]), "skipped": U16(b, 12), "lo": b[14], "hi": b[15]}


def wakes(h):
    out = []
    for slot in range(8):
        b = op(h, 24, [slot])
        out.append({"seq": U16(b, 0), "reason": WAKES[b[2]] if b[2] < len(WAKES) else b[2], "flags": b[3],
                    "counted_s": U32(b, 4), "p0": hex(U32(b, 8)), "p1": hex(U32(b, 12)), "p2": hex(U32(b, 16)),
                    "ms": U32(b, 20), "strays": U16(b, 24), "written": U16(b, 26)})
    written = out[0]["written"]
    return sorted([w for w in out if w["seq"] < written], key=lambda w: w["seq"])[-8:] if written else []


def snapshot(h):
    s = summary(h)
    k, extra = kinds(h)
    return {"summary": s, "kinds": k, "sleep": extra, "burst": burst(h), "ring": ring(h, s["ring_seq"]),
            "wakes": wakes(h)}


def show(d):
    s = d["summary"]
    print(f"OE observed {s['oe_obs']} (+{s['oe_drain']} in the drain's re-reads), bytes refused {s['rx_drop']}; "
          f"lock span {'per byte' if s['byte_mode'] else 'per transaction'}")
    print(f"  seen with: a PCF transaction {s['ctx_pcf']}, a flash write {s['ctx_flash']}, a sleep "
          f"{d['sleep']['ctx_sleep']}, none {s['ctx_none']}; receiver-off intervals {s['rx_off']}")
    print(f"  longest handlers: USB {s['usb_max_ms']} ms, system timer {s['st_max_ms']} ms")
    for k in d["kinds"]:
        if k["count"]:
            print(f"  PCF {k['kind']:14s} {k['count']:6d} ({k['fails']} failed); longest masked "
                  f"{k['max_span_ms']} ms, whole call {k['max_whole_ms']} ms; spans >= 1.0 ms "
                  f"{k['spans_ge_1ms']}, >= 1.4 ms {k['spans_ge_1_4ms']} of {k['spans']}")
    b = d["burst"]
    print(f"  burst: {'active, ' if b['active'] else ''}{b['done']} done, {b['failed']} failed, {b['left']} left, "
          f"{b['skipped']} skipped passes, {b['exposure_ms']} ms first to last ({b['lo']}-{b['hi']} ms gaps)")
    now = d["sleep"]["now_secs"]
    for e in d["ring"]:
        print(f"  OE #{e['seq']} {now - e['secs']} s ago (board second {e['secs']}): act {e['act']:#x} kind {e['kind']}{' drain' if e['in_drain'] else ''}"
              f"{' byte' if e['byte_mode'] else ''}; last span {e['span_ms']} ms, USB {e['usb_max_ms']}, "
              f"timer {e['st_max_ms']}")
    for w in d["wakes"]:
        print(f"  sleep #{w['seq']}: woke on {w['reason']} after {w['counted_s']} counted s (flags {w['flags']:#x}); "
              f"P0 {w['p0']} P1 {w['p1']} P2 {w['p2']}, strays {w['strays']}, at {w['ms']} ms")


ARMS = {"a": (False, False), "b": (False, True), "c": (True, True), "d": (True, False)}


def run_ab(a):
    arms = []
    for name in a.arms:
        byte_mode, with_burst = ARMS[name]

        def start(h):
            summary(h, 3 if byte_mode else 2)
            summary(h, 1)                          # zero everything
            return burst(h, 1, a.n, a.lo, a.hi) if with_burst else None
        b0 = ds.with_dev(start)
        secs = a.secs
        if with_burst:                             # the burst's expected length, plus margin
            secs = max(secs, a.n * (a.lo + a.hi) / 2000.0 + 10)
        out = tempfile.mktemp(suffix=".json")
        cm.say(f"arm {name}: {'per byte' if byte_mode else 'per transaction'}, "
               f"{'a burst of ' + str(a.n) if with_burst else 'no burst'}, ~{secs:.0f} s of echo")
        t0 = time.time()
        subprocess.run([sys.executable, os.path.join(HERE, "mac_raw81_echo.py"), str(int(secs * 50)),
                        "--gap-ms", "0", "--json", out], check=False)
        echo = json.load(open(out)) if os.path.exists(out) else None
        d = ds.with_dev(snapshot)
        if with_burst and d["burst"]["active"]:
            ds.with_dev(lambda h: burst(h, 2))
        arms.append({"arm": name, "byte_mode": byte_mode, "burst_asked": a.n if with_burst else 0,
                     "seconds": round(time.time() - t0, 1), "burst_start": b0, "board": d,
                     "echo": {k: echo[k] for k in ("n", "replied", "lost", "lost_at", "rtt_ms")} if echo else None,
                     "echo_idle_s": round(echo["lost"] * echo["timeout_ms"] / 1000.0, 2) if echo else None})
        s = d["summary"]
        cm.say(f"  OE {s['oe_obs']} (+{s['oe_drain']}), refused {s['rx_drop']}, with PCF {s['ctx_pcf']}; "
               f"burst {d['burst']['done']}/{a.n if with_burst else 0}; echo lost "
               f"{echo['lost'] if echo else '?'} of {echo['n'] if echo else '?'}")
    ds.with_dev(lambda h: summary(h, 2))           # back to per transaction, as the board boots
    return arms


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["status", "reset", "mode", "burst", "ab"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--lo", type=int, default=40)
    ap.add_argument("--hi", type=int, default=80)
    ap.add_argument("--arms", default="abcddcba")
    ap.add_argument("--secs", type=float, default=90)
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--json")
    a = ap.parse_args()
    res = None
    if a.cmd == "status":
        res = ds.with_dev(snapshot)
        show(res)
    elif a.cmd == "reset":
        ds.with_dev(lambda h: summary(h, 1))
        print("zeroed")
    elif a.cmd == "mode":
        if a.arg not in ("transaction", "byte"):
            sys.exit("mode: transaction or byte")
        s = ds.with_dev(lambda h: summary(h, 3 if a.arg == "byte" else 2))
        print("lock span:", "per byte" if s["byte_mode"] else "per transaction")
    elif a.cmd == "burst":
        if a.arg == "stop":
            res = ds.with_dev(lambda h: burst(h, 2))
        else:
            n = int(a.arg or 0)
            if not 1 <= n <= 2000 or a.lo < 20 or a.hi < a.lo:
                sys.exit("burst: 1..2000 reads, --lo >= 20, --hi >= --lo")
            res = ds.with_dev(lambda h: burst(h, 1, n, a.lo, a.hi))
        print(res)
    elif a.cmd == "ab":
        if any(c not in ARMS for c in a.arms):
            sys.exit("--arms: letters from abcd")
        res = {"args": vars(a), "at": time.strftime("%Y-%m-%d %H:%M:%S"), "arms": run_ab(a)}
    if a.json and res is not None:
        with open(a.json, "w") as f:
            json.dump(res, f, indent=1)


if __name__ == "__main__":
    main()
