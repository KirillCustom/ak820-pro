#!/usr/bin/env python3
"""Raw HID over the 2.4G dongle, stage 1: the echo (plans/RAWHID-24G-PLAN.md).

Writes N random 32-byte reports to the dongle's FF60 interface (0C45:FDFD
MI_03), each answered by the board -- on the gremlin/raw81 dev build -- with
the same 32 bytes as a 91 frame, which the dongle should return as an input
report on the same interface. Matches each reply to its request by content
(Windows delivers input reports to every open handle, so nothing else is
assumed), and prints the round trip, the losses, and the board's own counters
(HC_DEVHOOK op 17, over the cable) before and after.

    win_raw81_echo.py [N] [--gap-ms MS] [--timeout-ms MS] [--seed S] [--json OUT]

Windows only: the dongle's path comes from the Configuration Manager's list,
which opens nothing; never hid_enumerate (CLAUDE.md). Byte 0 of every request
is 0xE0 | the request's sequence's top bits: never a command any firmware
answers (0x01..0x15 VIA, 0x07 custom values), so even a build that dispatched
would not act on it.
"""
import argparse, ctypes, json, os, random, subprocess, sys, time
from ctypes import wintypes
import hid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


def dongle_path(mi):
    class GUID(ctypes.Structure):
        _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD),
                    ("d", ctypes.c_ubyte * 8)]
    g = GUID(0x4D1E55B2, 0xF16F, 0x11CF, (ctypes.c_ubyte * 8)(0x88, 0xCB, 0, 0x11, 0x11, 0, 0, 0x30))
    cm, n = ctypes.windll.cfgmgr32, wintypes.ULONG()
    cm.CM_Get_Device_Interface_List_SizeW(ctypes.byref(n), ctypes.byref(g), None, 0)
    buf = ctypes.create_unicode_buffer(n.value)
    cm.CM_Get_Device_Interface_ListW(ctypes.byref(g), None, buf, n, 0)
    hits = [p for p in buf[:n.value].split("\0")
            if p and len(p.split("#")) == 4 and p.lower().split("#")[1] == f"vid_0c45&pid_fdfd&{mi}"]
    if len(hits) != 1:
        sys.exit(f"the dongle's {mi}: found {hits}")
    return hits[0]


def board_counters():
    """HC_DEVHOOK op 17 over the cable, through ak820health's opener (no enumeration)."""
    code = ("import sys; sys.path.insert(0, %r); import ak820text, json\n"
            "h = ak820text.open_device()\n"
            "out = {}\n"
            "for sub in (0, 1):\n"
            "    h.write(bytes([0, 0x07, 0x13, 0x76, 17, sub]) + bytes(27))\n"
            "    for _ in range(5):\n"
            "        r = h.read(32, 1000)\n"
            "        if r and r[1] == 0x13 and r[2] == 0x76 and r[3] == 17: break\n"
            "    else: sys.exit('no reply to op 17')\n"
            "    if r[0] != 0x07: sys.exit('op 17 unhandled: not the gremlin/raw81 dev build')\n"
            "    out[sub] = list(r)\n"
            "h.close(); print(json.dumps(out))\n") % os.path.join(ROOT, "hostagent")
    r = subprocess.run([PY, "-c", code], capture_output=True, text=True, timeout=30)
    if r.returncode:
        sys.exit(f"board counters: {(r.stderr or r.stdout).strip()}")
    d = json.loads(r.stdout)
    p0, p1 = bytes(d["0"]), bytes(d["1"])
    names = ["committed", "bad_sum", "lifetime", "uart_taint", "sent_91", "dropped", "acks_in_window"]
    c = {n: int.from_bytes(p0[4 + 4 * i:8 + 4 * i], "little") for i, n in enumerate(names)}
    c.update(state=p1[4], boundary=p1[5], pending=p1[6], ack_window=p1[7],
             quarantined_bytes=int.from_bytes(p1[8:12], "little"))
    return c


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("n", nargs="?", type=int, default=100)
    ap.add_argument("--gap-ms", type=float, default=20)
    ap.add_argument("--timeout-ms", type=int, default=250)
    ap.add_argument("--seed", type=int, default=81)
    ap.add_argument("--json")
    ap.add_argument("--no-board", action="store_true", help="leave the counters out of the report")
    a = ap.parse_args()
    rng = random.Random(a.seed)

    # Always, --no-board or not: op 17 answering is the only check that the
    # board runs stage 1. On an older build these random bodies would go
    # through the 3-byte window alone, and a 5B or 5C with a matching sum
    # inside one would act -- a link state, or a battery reading the gauge
    # takes (the Mac session, 10-19).
    before = board_counters()
    if a.no_board:
        before = None
    d = hid.Device(path=dongle_path("mi_03").encode())
    rtts, lost, stray, dup = [], 0, 0, 0
    lost_at = []    # wall-clock send times of the lost ones, to line up with other logs
    try:
        while d.read(33, 0):     # anything already waiting is not ours
            stray += 1
        for i in range(a.n):
            body = bytes([0xE0 | (i >> 8) & 0x0F, i & 0xFF]) + bytes(rng.getrandbits(8) for _ in range(30))
            t_wall = time.time()
            t0 = time.perf_counter()
            d.write(bytes([0]) + body)
            got = None
            while (time.perf_counter() - t0) * 1000 < a.timeout_ms:
                r = d.read(33, max(1, int(a.timeout_ms - (time.perf_counter() - t0) * 1000)))
                if not r:
                    break
                r = bytes(r)
                payload = r[1:33] if len(r) == 33 else r[:32]   # with or without the report id
                if payload == body:
                    got = (time.perf_counter() - t0) * 1000
                    break
                stray += 1
            if got is None:
                lost += 1
                lost_at.append(round(t_wall, 3))
            else:
                rtts.append(got)
            time.sleep(a.gap_ms / 1000)
        time.sleep(0.2)
        while d.read(33, 50):    # late replies: duplicates or very slow ones
            dup += 1
    finally:
        d.close()
    after = None if a.no_board else board_counters()

    rtts.sort()
    pct = lambda q: round(rtts[min(len(rtts) - 1, int(q * len(rtts)))], 2) if rtts else None
    res = {"n": a.n, "replied": len(rtts), "lost": lost, "lost_at": lost_at, "stray_reports": stray, "late_reports": dup,
           "rtt_ms": {"min": pct(0), "median": pct(0.5), "p95": pct(0.95), "max": pct(1.0)},
           "gap_ms": a.gap_ms, "timeout_ms": a.timeout_ms,
           "board_before": before, "board_after": after,
           "board_delta": {k: after[k] - before[k] for k in before if isinstance(before[k], int)}
           if before and after else None,
           "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    print(f"{len(rtts)}/{a.n} echoed, {lost} lost, {stray} stray, {dup} late; "
          f"rtt ms min {res['rtt_ms']['min']} median {res['rtt_ms']['median']} "
          f"p95 {res['rtt_ms']['p95']} max {res['rtt_ms']['max']}")
    if res["board_delta"]:
        print("board: " + ", ".join(f"{k} +{v}" for k, v in res["board_delta"].items() if v))
    if a.json:
        os.makedirs(os.path.dirname(os.path.abspath(a.json)), exist_ok=True)
        with open(a.json, "w") as f:
            json.dump(res, f, indent=1)


if __name__ == "__main__":
    main()
