#!/usr/bin/env python3
"""The radio link's UART overruns, measured (plans/OVERRUN-CAPS-PLAN.md, step
1): HC_DEVHOOK ops 20-24 on a dev build that carries overrun_diag.c.

    venv/bin/python scripts/overrun_diag.py status [--json OUT]
    venv/bin/python scripts/overrun_diag.py reset
    venv/bin/python scripts/overrun_diag.py mode transaction|byte
    venv/bin/python scripts/overrun_diag.py burst N [--lo MS] [--hi MS] | burst stop
    venv/bin/python scripts/overrun_diag.py ab [--arms abcddcba] [--secs 90] [--n 1000] [--json OUT]
    venv/bin/python scripts/overrun_diag.py sweep [--us 50,100,...] [--n 500] [--secs 30] [--json OUT]

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
it). `sweep` measures the receiver's depth: per span, a burst of masked
spans of that length (no I2C), the same load and accounting, per transaction.
Needs the Mac's dongle, the slider on 2.4G and the cable's data.
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
    return out, {"ctx_sleep": U16(b, 0), "tick_hz": U32(b, 2), "now_secs": U32(b, 6), "ctx_probe": U16(b, 10)}


def walk_back(slots, seq, cap):
    """The newest entries, oldest first: from seq-1 backward (mod 2^16), each
    taken only if its slot carries exactly that sequence number, so an
    unwritten or overwritten slot ends the walk; wrap-safe."""
    out = []
    for k in range(1, cap + 1):
        want = (seq - k) & 0xFFFF
        e = slots[want % cap]
        if e["seq"] != want:
            break
        out.append(e)
    return out[::-1]


def ring(h, seq):
    """The OE ring's entries, newest 32 at most, and the summary they go with:
    re-read if an OE landed during the 16 pages."""
    for _ in range(3):
        slots = {}
        for page in range(16):
            b = op(h, 21, [page])
            for j in range(2):
                e = b[14 * j:14 * j + 14]
                slots[2 * page + j] = {"seq": U16(e, 0), "secs": U32(e, 2), "act": e[6], "kind": e[7] & 3,
                                       "in_drain": bool(e[7] & 0x40), "byte_mode": bool(e[7] & 0x80),
                                       "span_ms": ms(U16(e, 8)), "usb_max_ms": ms(U16(e, 10)),
                                       "st_max_ms": ms(U16(e, 12))}
        s_now = summary(h)
        if s_now["ring_seq"] == seq:
            return walk_back(slots, seq, 32), s_now
        seq = s_now["ring_seq"]
    sys.exit("the OE ring kept moving during its readout")


def burst(h, sub=0, n=0, lo=40, hi=80, mask_us=0):
    """sub 1 starts PCF reads; sub 3 the depth probe (n masked spans of mask_us)."""
    b = op(h, 23, [sub, n & 0xFF, n >> 8, lo, hi, mask_us & 0xFF, mask_us >> 8])
    return {"left": U16(b, 0), "done": U16(b, 2), "failed": U16(b, 4), "exposure_ms": U32(b, 6),
            "active": bool(b[10]), "skipped": U16(b, 12), "lo": b[14], "hi": b[15]}


def wakes(h):
    """The deep sleep's last eight wakes, oldest first; re-read if one landed
    during the readout (every reply carries the count written)."""
    for _ in range(3):
        out = []
        for slot in range(8):
            b = op(h, 24, [slot])
            out.append({"seq": U16(b, 0), "reason": WAKES[b[2]] if b[2] < len(WAKES) else b[2], "flags": b[3],
                        "counted_s": U32(b, 4), "p0": hex(U32(b, 8)), "p1": hex(U32(b, 12)),
                        "p2": hex(U32(b, 16)), "ms": U32(b, 20), "strays": U16(b, 24), "written": U16(b, 26)})
        if len({w["written"] for w in out}) == 1:
            return walk_back(out, out[0]["written"], 8)
    sys.exit("the wake ring kept moving during its readout")


def snapshot(h):
    r, s = ring(h, summary(h)["ring_seq"])     # the summary the ring was read against
    k, extra = kinds(h)
    return {"summary": s, "kinds": k, "sleep": extra, "burst": burst(h), "ring": r, "wakes": wakes(h)}


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
CHUNK = 1000        # echoes a run, ~17-20 s back to back
MARGIN_S = 10.0     # load after the burst is seen finished
READY_S = 60.0      # the child's counter read and dongle open (its opener retries for up to 30 s)


class Echo:
    """One echo run in a child, its load bounded by READY on stdout and the
    run's own t_first/t_last, so the cable reads and the dongle's open around
    it are never counted as load."""

    def __init__(self, n):
        self.out = tempfile.mktemp(suffix=".json")
        self.n = n
        self.p = subprocess.Popen([sys.executable, os.path.join(HERE, "mac_raw81_echo.py"), str(n),
                                   "--gap-ms", "0", "--ready", "--json", self.out],
                                  stdout=subprocess.PIPE, text=True)

    def wait_ready(self):
        deadline = time.monotonic() + READY_S
        while time.monotonic() < deadline:
            ln = self.p.stdout.readline()
            if not ln:
                break
            if ln.strip() == "READY":
                return
        raise SystemExit("the echo never started sending")

    def finish(self):
        try:
            self.p.communicate(timeout=self.n * 0.3 + READY_S)
        except subprocess.TimeoutExpired:
            raise SystemExit("an echo run hung")
        if self.p.returncode != 0 or not os.path.exists(self.out):
            raise SystemExit(f"the echo run failed (exit {self.p.returncode}): the arm's load is unknown")
        with open(self.out) as f:
            e = json.load(f)
        return {"t_first": e["t_first"], "t_last": e["t_last"], "n": e["n"], "replied": e["replied"],
                "lost": e["lost"], "lost_at": e["lost_at"], "lost_until": e["lost_until"], "rtt_ms": e["rtt_ms"]}

    def close(self):
        if self.p.poll() is None:
            self.p.terminate()
            try:
                self.p.wait(5)
            except subprocess.TimeoutExpired:
                self.p.kill()
                self.p.wait()
        if os.path.exists(self.out):
            os.unlink(self.out)


def run_arm(name, a, mask_us=0):
    """Load from start to end: echo runs back to back until --secs of wall time
    have passed and, in a burst arm, MARGIN_S of load after the burst was seen
    finished. The burst starts once the first run is sending. With mask_us,
    the burst is the depth probe's masked spans instead of PCF reads."""
    byte_mode, with_burst = ARMS[name]

    def prep(h):
        burst(h, 2)                                # nothing left from before
        summary(h, 3 if byte_mode else 2)
        summary(h, 1)                              # zero the counts, ring and kinds
    ds.with_dev(prep)
    cm.say(f"arm {name}: {'per byte' if byte_mode else 'per transaction'}, "
           f"{'a burst of ' + str(a.n) if with_burst else 'no burst'}, at least {a.secs:.0f} s of echo")
    t_arm, t_arm_m = time.time(), time.monotonic()
    runs, b0, done_at = [], None, None
    echo = Echo(CHUNK)
    try:
        echo.wait_ready()
        if with_burst:
            b0 = ds.with_dev(lambda h: burst(h, 3 if mask_us else 1, a.n, a.lo, a.hi, mask_us))
            b0["host_t"] = round(time.time(), 3)
        while True:
            runs.append(echo.finish())
            echo.close()
            if with_burst and done_at is None and not ds.with_dev(burst)["active"]:
                done_at = runs[-1]["t_last"]       # finished somewhere in this run, at the latest at its end
            loaded_after = sum(max(0.0, r["t_last"] - max(r["t_first"], done_at)) for r in runs) if done_at else 0
            elapsed = time.monotonic() - t_arm_m
            if elapsed >= a.secs and (not with_burst or (done_at and loaded_after >= MARGIN_S)):
                break
            if elapsed > a.secs + 900:
                raise SystemExit(f"arm {name} ran 15 minutes past --secs: the burst never finished?")
            echo = Echo(CHUNK)
            echo.wait_ready()
    finally:
        echo.close()
    d = ds.with_dev(snapshot)
    # Time with nothing in flight: between runs, and each loss's wait.
    idle = [[r0["t_last"], r1["t_first"]] for r0, r1 in zip(runs, runs[1:])]
    idle += [[t0, t1] for r in runs for t0, t1 in zip(r["lost_at"], r["lost_until"])]
    arm = {"arm": name, "mask_us": mask_us, "byte_mode": byte_mode, "burst_asked": a.n if with_burst else 0,
           "start": round(t_arm, 3), "end": round(time.time(), 3), "burst_start": b0,
           "burst_done_by": done_at, "board": d, "echo_runs": runs,
           "load_s": round(sum(r["t_last"] - r["t_first"] for r in runs), 2),
           "echo_sent": sum(r["n"] for r in runs), "echo_lost": sum(r["lost"] for r in runs),
           "idle_intervals": sorted(idle), "idle_s": round(sum(b - a_ for a_, b in idle), 2)}
    s = d["summary"]
    cm.say(f"  OE {s['oe_obs']} (+{s['oe_drain']} in the drain), refused {s['rx_drop']}, with PCF {s['ctx_pcf']}; "
           f"burst {d['burst']['done']} done of {arm['burst_asked']}; echo lost {arm['echo_lost']} of "
           f"{arm['echo_sent']} over {arm['load_s']} s of load, {arm['idle_s']} s with nothing in flight")
    return arm


def run_ab(a, masks=None):
    arms = []
    try:
        for name, us in ([("b", us) for us in masks] if masks else [(n, 0) for n in a.arms]):
            arms.append(run_arm(name, a, us))
    finally:
        # Whatever happened: no burst left running, and the lock span back to
        # per transaction, as the board boots. Each tried on its own, so one
        # failing does not skip the other (nor hide the original error).
        for what, fn in (("stop the burst", lambda h: burst(h, 2)), ("restore per transaction", lambda h: summary(h, 2))):
            try:
                ds.with_dev(fn)
            except (Exception, SystemExit) as e:
                cm.say(f"cleanup: could not {what}: {e}")
    return arms


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["status", "reset", "mode", "burst", "ab", "sweep"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--lo", type=int, default=40)
    ap.add_argument("--hi", type=int, default=80)
    ap.add_argument("--arms", default="abcddcba")
    ap.add_argument("--secs", type=float, default=90)
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--us", default="50,100,150,200,300,500,1000,1500",
                    help="sweep: the depth probe's masked spans, us")
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
    elif a.cmd == "sweep":
        masks = [int(x) for x in a.us.split(",")]
        if any(not 1 <= m <= 3000 for m in masks):
            sys.exit("--us: 1..3000 each")
        res = {"args": vars(a), "at": time.strftime("%Y-%m-%d %H:%M:%S"), "arms": run_ab(a, masks)}
    if a.json and res is not None:
        with open(a.json, "w") as f:
            json.dump(res, f, indent=1)


if __name__ == "__main__":
    main()
