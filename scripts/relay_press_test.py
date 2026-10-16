#!/usr/bin/env python3
"""Press a key through the wake rig's relay (relay 1 of the bench's USB relay,
wired across one key's switch: plans/POWER-CAMPAIGN-PLAN.md, R7) and count
what the board registered, to find how short and how fast a relay press can
be.

    venv/bin/python scripts/relay_press_test.py [--out <csv>] [--n 10] [--trial-csv <drain.csv>]

Each batch is n presses of a set dwell (relay closed) and gap (open), timed
by the Mac. Before and after it, the board's health counters:
- raw_edges: raw matrix transitions BEFORE debounce (a clean press and
  release are two); fewer than 2n means the relay's contact never closed;
- key_presses: presses AFTER debounce (sym_defer_pk, 5 ms per key); fewer than
  the presses whose contact closed means debounce discarded them.
Keep n even if the key toggles something (play/pause ends where it started).

The relay is a separate USB device from the keyboard (16c0:05df), driven here
in-process: a 9-byte feature report, 0xFF on / 0xFD off, relay 1 only, so
relay 2 (the keyboard's 5 V) is never touched; checked before and after. The
board's counters are raw HID, which a running bench_trial owns on macOS:
with --trial-csv the test waits until 20 s after the trial's latest read and
must finish 30 s before the next.
"""
import argparse
import csv
import os
import sys
import time

import hid

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "hostagent"))
import ak820health as health  # noqa: E402

VID, PID = 0x16C0, 0x05DF
# (dwell ms, gap ms): realistic down to below the relay's and debounce's limits,
# then the gap shrunk at a dwell that registers.
BATCHES = [(100, 100), (60, 60), (40, 40), (25, 40), (15, 40), (10, 40), (7, 40),
           (5, 40), (3, 40), (25, 25), (25, 15), (25, 10), (15, 10)]


def relay_state(dev):
    raw = dev.get_feature_report(0, 9)
    return raw[8]


def relay1(dev, on):
    dev.send_feature_report(bytes([0, 0xFF if on else 0xFD, 1, 0, 0, 0, 0, 0, 0]))


def counts():
    s = health.read_stalls()
    r = health.read_rows()
    return s["key_presses"], r["raw_edges"]


def wait_for_gap(trial_csv, need_s):
    with open(trial_csv, newline="") as f:
        last = float(list(csv.DictReader(f))[-1]["epoch"])
    start, deadline = last + 20, last + 300 - 30
    now = time.time()
    if now > deadline - need_s:
        start, deadline = last + 300 + 20, last + 600 - 30
    if start > now:
        print(f"waiting {start - now:.0f} s for the gap after the trial's read", flush=True)
        time.sleep(start - now)
    return deadline


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--out", default=None)
    ap.add_argument("--trial-csv", default=None)
    a = ap.parse_args()
    need = sum(a.n * (d + g) for d, g in BATCHES) / 1000 + len(BATCHES) * 1.5
    deadline = wait_for_gap(a.trial_csv, need) if a.trial_csv else None

    devs = hid.enumerate(VID, PID)
    if len(devs) != 1:
        sys.exit(f"expected one USB relay, found {len(devs)}")
    rows = []
    with hid.Device(path=devs[0]["path"]) as dev:
        before_state = relay_state(dev)
        if before_state & 1:
            sys.exit("relay 1 is already on")
        relay2 = before_state & 2
        # The rig first: one slow press must reach the matrix, or a broken
        # switch reads as "the relay is too slow" (the first sweep, 16:10 10-16).
        kp0, re0 = counts()
        relay1(dev, True)
        time.sleep(0.3)
        relay1(dev, False)
        time.sleep(0.3)
        kp1, re1 = counts()
        if (re1 - re0) & 0xFFFF < 2:
            sys.exit(f"the rig is not pressing: one 300 ms press gave raw edges {(re1 - re0) & 0xFFFF}, "
                     f"presses {(kp1 - kp0) & 0xFFFF}; check the switch's wiring")
        print(f"rig check: one 300 ms press gave raw edges {(re1 - re0) & 0xFFFF}, "
              f"presses {(kp1 - kp0) & 0xFFFF}", flush=True)
        relay1(dev, True)   # the check press toggled the key once; undo it
        time.sleep(0.3)
        relay1(dev, False)
        time.sleep(0.3)
        kp0, re0 = counts()
        for dwell, gap in BATCHES:
            if deadline and time.time() > deadline - (a.n * (dwell + gap) / 1000 + 2):
                print("stopping: the trial's next read is due", flush=True)
                break
            on_ms = []
            for _ in range(a.n):
                t0 = time.perf_counter()
                relay1(dev, True)
                time.sleep(dwell / 1000)
                relay1(dev, False)
                t1 = time.perf_counter()
                on_ms.append((t1 - t0) * 1000)
                time.sleep(gap / 1000)
            time.sleep(0.15)   # the last release's debounce
            kp1, re1 = counts()
            row = {"dwell_ms": dwell, "gap_ms": gap, "n": a.n,
                   "on_ms_mean": round(sum(on_ms) / len(on_ms), 2),
                   "raw_edges": (re1 - re0) & 0xFFFF, "key_presses": (kp1 - kp0) & 0xFFFF}
            rows.append(row)
            print(f"dwell {dwell:3d} ms gap {gap:3d} ms (on {row['on_ms_mean']:6.2f} ms): "
                  f"raw edges {row['raw_edges']:3d}/{2 * a.n}, presses {row['key_presses']:3d}/{a.n}",
                  flush=True)
            kp0, re0 = kp1, re1
        after_state = relay_state(dev)
        if after_state & 1 or (after_state & 2) != relay2:
            print(f"⚠️ relay state changed: {before_state:02x} -> {after_state:02x}", flush=True)
    if a.out and rows:
        new = not os.path.exists(a.out)
        with open(a.out, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["time"] + list(rows[0]))
            if new:
                w.writeheader()
            for r in rows:
                w.writerow({"time": time.strftime("%Y-%m-%d %H:%M:%S"), **r})


if __name__ == "__main__":
    main()
