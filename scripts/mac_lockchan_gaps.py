#!/usr/bin/env python3
"""Why the receiver aborts Mac frames for a gap (plans/LOCK-LED-CHANNEL-PLAN.md,
"The macOS writer", 10-20): N frames back to back through `ak820 lockchan
send`, each frame's outcome and slowest write as the host saw it, and the
board's commit or abort for it (devhook.py frames, read after each).

    venv/bin/python scripts/mac_lockchan_gaps.py [--n 20] [--hold-radio] [--out FILE]

--hold-radio takes the radio's owner lock for the run, so the daemon stops
probing the 2.4G dongle ("contended"): the same band as BT.
"""
import argparse, fcntl, json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
PY = os.path.join(ROOT, "venv", "bin", "python")
AK820 = os.path.join(ROOT, "ak820-agent", "target", "debug", "ak820")


def frames():
    r = subprocess.run([PY, os.path.join(HERE, "devhook.py"), "frames", "json"], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        sys.exit(f"devhook.py frames json: {(r.stderr or r.stdout).strip()}")
    d = json.loads(r.stdout)
    return d["frames"], sum(d["aborts"].values()), d["aborts"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--hold-radio", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args()
    held = None
    if a.hold_radio:
        held = open("/Users/Shared/ak820pro/raw81.lock", "r")
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        time.sleep(8)   # the daemon's radio notices and stands down
    rows = []
    f0, ab0, _ = frames()
    for i in range(a.n):
        r = subprocess.run([AK820, "lockchan", "send", "Gap study", f"frame {i:02d} of {a.n}"],
                           capture_output=True, text=True)
        out = (r.stdout + r.stderr).strip()
        f1, ab1, aborts = frames()
        m = re.search(r": (\w+(?:\(\w+\))?), (\d+) writes in ([\d.]+) s, slowest (\d+) ms, (\d+) over 100", out)
        row = {"i": i, "outcome": m.group(1) if m else out, "writes": int(m.group(2)) if m else None,
               "secs": float(m.group(3)) if m else None, "slowest_ms": int(m.group(4)) if m else None,
               "over_100ms": int(m.group(5)) if m else None,
               "board": "commit" if f1 > f0 else "abort" if ab1 > ab0 else "nothing", "aborts": aborts}
        rows.append(row)
        print(f"{i:2d} {row['outcome']:<22} slowest {row['slowest_ms']} ms, {row['over_100ms']} >100 ms -> board {row['board']}",
              flush=True)
        f0, ab0 = f1, ab1
    commits = sum(r["board"] == "commit" for r in rows)
    print(f"{'radio held' if a.hold_radio else 'radio as is'}: {commits}/{a.n} committed")
    if a.out:
        with open(a.out, "w") as f:
            json.dump({"hold_radio": a.hold_radio, "rows": rows}, f, indent=1)


if __name__ == "__main__":
    main()
