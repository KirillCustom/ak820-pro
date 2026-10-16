#!/usr/bin/env python3
"""Guard a running bench_trial.py, so closing the session that launched it
cannot lose the trial, and take the per-event reads it does not take (gate 8,
plans/BATTERY-GAUGE-REFINE-PLAN.md). It detaches itself (double fork +
setsid), so it outlives the shell and the Claude session that start it.

    venv/bin/python scripts/trial_guard.py --pid PID --csv <the trial's --out>

The trial's own arguments are taken from `ps`. Polling the trial's CSV every
5 s, it:
- reads the stall counters (`ak820health.py --stalls --json`) and the display
  probe (`devhook.py read`, dev builds only) once at its start;
- once `pack_mv` reaches --near (3720 mV), after each of the trial's reads:
  `ak820battery.py cfg` until it says `warned 1`, then the counters and the
  probe ("Battery low"); and a probe reset, so each event's probe covers only
  the last poll;
- after the trial's rgbcut dump, in the 60 s before its relay goes on: the
  counters and the probe (the cut, without the plug-in);
- if the trial's process is gone without a `done` or `FAIL` row:
  - before the cut: restarts it with the same arguments and a new --out
    (`<name>-resumed-N.csv`), and guards that;
  - after the cut, before FULL-ON: dumps the log unless the trial did, then
    `usb_relay.py on 2`, confirmed by the board's supply line; then stops;
  - after FULL-ON: nothing (the relay is on).

- after every successful drain poll, never at the cut (from 10-15): one light
  health read (`read_stalls`, one transaction) as a row of
  guard-activity.csv (key presses, a 16-bit count that a reboot or a reset
  restarts; stalls >= 25 ms; the worst loop gap), for JD's idle pattern (the
  power ladder's sizing, plans/POWER-CAMPAIGN-PLAN.md) and a running stall
  watch. A failed read is a blank row: an unknown interval, never zero
  presses;
- progress, not just survival (codex, 10-15): before the cut, no successful
  read for --stall-min (45 min) stops the trial and turns relay 2 ON, so the
  pack charges instead of draining through the reserve unwatched; a FAIL
  before FULL-ON also ends with the relay ON (the trial's own fail() turns it
  off); and no `done` within --charge-h (16 h) of FULL-ON stops the trial with
  the relay left on.

Its reads go only right after one of the trial's, because raw HID is
exclusive on macOS: two readers at once fail. Its actions go to
guard.log beside the CSV, each read's output to guard-<tag>-<time>.txt.
Stop it with `kill $(cat <dir>/guard.pid)`.
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
PY = os.path.join(ROOT, "venv", "bin", "python3")
BATT = os.path.join(ROOT, "hostagent", "ak820battery.py")
HEALTH = os.path.join(ROOT, "hostagent", "ak820health.py")
DEVHOOK = os.path.join(ROOT, "scripts", "devhook.py")
RELAY = os.path.join(ROOT, "scripts", "usb_relay.py")
TRIAL = os.path.join(ROOT, "scripts", "bench_trial.py")


def stamp():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def say(msg):
    print(f"{stamp()} {msg}", flush=True)


def run(args, timeout=150):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=ROOT)
        return r.returncode, r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return -1, "timed out"


def rows(path):
    try:
        with open(path, newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def trial_args(pid):
    """The trial's arguments, less --out, from its command line."""
    rc, out = run(["ps", "-o", "command=", "-p", str(pid)])
    words = out.split()
    if rc != 0 or not any(w.endswith("bench_trial.py") for w in words):
        sys.exit(f"pid {pid} is not a bench_trial.py: {out.strip()!r}")
    words = words[[i for i, w in enumerate(words) if w.endswith("bench_trial.py")][0] + 1:]
    keep, skip = [], False
    for w in words:
        if skip:
            skip = False
        elif w == "--out":
            skip = True
        elif not w.startswith("--out="):
            keep.append(w)
    return keep


def snapshot(outdir, tag):
    """The stall counters and the display probe, saved and logged."""
    path = os.path.join(outdir, f"guard-{tag}-{time.strftime('%Y%m%d-%H%M%S')}.txt")
    rc1, stalls = run([PY, HEALTH, "--stalls", "--json"])
    rc2, probe = run([PY, DEVHOOK, "read"])
    with open(path, "w") as f:
        f.write(f"{stamp()} {tag}\n--- ak820health.py --stalls --json (rc {rc1})\n{stalls}\n"
                f"--- devhook.py read (rc {rc2})\n{probe}\n")
    m = re.search(r'"count_ge_25ms": (\d+)', stalls)
    say(f"{tag}: count_ge_25ms {m.group(1) if m else '?'} (rc {rc1}), probe rc {rc2}; {path}")


def probe_reset():
    rc, out = run([PY, DEVHOOK, "reset"])
    if rc:
        say(f"probe reset failed (rc {rc}): {out.strip()[-120:]}")


ACTIVITY = ["time", "epoch", "trial_phase", "key_presses", "count_ge_25ms", "loop_gap_max_ms", "note"]
LIGHT_READ = ("import sys, json; sys.path.insert(0, %r); import ak820health as a; "
              "print(json.dumps(a.read_stalls()))" % os.path.dirname(HEALTH))


def activity(outdir, phase):
    """One light health read, after one of the trial's successful drain polls."""
    path = os.path.join(outdir, "guard-activity.csv")
    rc, out = run([PY, "-c", LIGHT_READ], timeout=30)
    try:
        d = json.loads(out[out.index("{"):])
        row = [stamp(), f"{time.time():.0f}", phase, d.get("key_presses", ""),
               d.get("count_ge_25ms", ""), d.get("loop_gap_max_ms", ""), ""]
    except ValueError:
        row = [stamp(), f"{time.time():.0f}", phase, "", "", "", f"read failed rc {rc}"]
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(ACTIVITY)
        w.writerow(row)


def stop_trial(pid, child):
    try:
        if child:
            child.terminate()
        else:
            os.kill(pid, 15)
    except OSError:
        pass


def relay_on_confirmed():
    rc, out = run([PY, RELAY, "on", "2"])
    say(f"relay on 2: rc {rc} {out.strip()[-120:]}")
    end = time.time() + 90
    while time.time() < end:
        rc, out = run([PY, BATT])
        m = re.search(r"^supply\s+(\S+)", out, re.M)
        if m and m.group(1) == "external":
            say("confirmed: supply external (charging)")
            return
        time.sleep(10)
    say("NOT confirmed by a board read (the reads may be what failed); the relay's own reply is above")


def warned():
    rc, out = run([PY, BATT, "cfg"])
    m = re.search(r"warned (\d+)", out)
    return (int(m.group(1)) > 0) if (rc == 0 and m) else None


def finish_after_cut(outdir, dumped):
    if not dumped:
        path = os.path.join(outdir, f"log-{time.strftime('%Y%m%d-%H%M')}-guard.csv")
        rc, out = run([PY, BATT, "log", path])
        say(f"log dump {path}: rc {rc} {out.strip().splitlines()[-1] if out.strip() else ''}")
    rc, out = run([PY, RELAY, "on", "2"])
    say(f"relay on 2: rc {rc} {out.strip()[-120:]}")
    end = time.time() + 90
    while time.time() < end:
        rc, out = run([PY, BATT])
        m = re.search(r"^supply\s+(\S+)", out, re.M)
        if m and m.group(1) == "external":
            say("confirmed: supply external (charging)")
            return
        time.sleep(10)
    say("NOT confirmed: the board does not read external after relay on 2")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, required=True)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--near", type=int, default=3720, help="pack mV from which to watch for the warning")
    ap.add_argument("--stall-min", type=int, default=45,
                    help="before the cut, no successful read this long: stop the trial, relay on")
    ap.add_argument("--charge-h", type=float, default=16,
                    help="no 'done' this long after FULL-ON: stop the trial, relay left on")
    a = ap.parse_args()
    csv_path = os.path.abspath(a.csv)
    outdir = os.path.dirname(csv_path)
    args = trial_args(a.pid)

    if os.fork():
        time.sleep(0.5)
        print(f"guard detached; pid in {outdir}/guard.pid, log {outdir}/guard.log", flush=True)
        os._exit(0)
    os.setsid()
    if os.fork():
        os._exit(0)
    log = open(os.path.join(outdir, "guard.log"), "a", buffering=1)
    os.dup2(log.fileno(), 1)
    os.dup2(log.fileno(), 2)
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    with open(os.path.join(outdir, "guard.pid"), "w") as f:
        f.write(f"{os.getpid()}\n")
    say(f"guarding pid {a.pid}, {csv_path}; trial args {' '.join(args)}")

    pid, child, restarts = a.pid, None, 0
    seen = len(rows(csv_path))
    started = snap_warned = snap_cut = False
    is_warned = None
    last_mv = None
    last_good = time.time()
    full_on_at = None
    while True:
        time.sleep(5)
        rs = rows(csv_path)
        phases = [r["phase"] for r in rs]
        new = rs[seen:]
        seen = len(rs)
        for r in new:
            if r.get("pack_mv"):
                last_mv = int(r["pack_mv"])
            if r.get("supply"):
                last_good = time.time()
            if r["phase"] == "FULL-ON" and full_on_at is None:
                full_on_at = time.time()
        last = new[-1] if new else None

        # A wait-cut row showing the cut is followed at once by CUT and the
        # trial's dump: no reads of ours then.
        if last and last["phase"] == "wait-cut" and "lights_cut" not in last["flags"]:
            if not started:
                started = True
                snapshot(outdir, "start")
            if last_mv is not None and last_mv <= a.near:
                if not is_warned:
                    is_warned = warned()
                    if is_warned and not snap_warned:
                        snap_warned = True
                        snapshot(outdir, "warned")
                probe_reset()
            # A successful drain poll, before the cut: the trial has finished
            # its read, so the HID is free for one light read of ours.
            if last.get("supply"):
                activity(outdir, last["phase"])
        if last and last["phase"] == "dump" and "rgbcut" in last["note"] and not snap_cut:
            snap_cut = True
            snapshot(outdir, "cut")

        alive = child.poll() is None if child else (
            run(["ps", "-o", "command=", "-p", str(pid)])[1].find("bench_trial.py") >= 0)
        if alive and "CUT" not in phases and time.time() - last_good > a.stall_min * 60:
            say(f"STALLED: no successful board read for {a.stall_min} min before the cut; "
                f"stopping trial pid {pid} and charging, so the pack does not drain unwatched")
            stop_trial(pid, child)
            relay_on_confirmed()
            return
        if alive and full_on_at and time.time() - full_on_at > a.charge_h * 3600:
            say(f"no 'done' {a.charge_h:g} h after FULL-ON; stopping trial pid {pid}, relay left on")
            stop_trial(pid, child)
            return
        if alive:
            continue
        if "FAIL" in phases and "FULL-ON" not in phases:
            say(f"trial pid {pid} ended FAIL before FULL-ON (its fail() turns the relay off); "
                "turning relay 2 on so the pack charges, then the guard stops")
            relay_on_confirmed()
            return
        if "done" in phases or "FAIL" in phases:
            say(f"trial pid {pid} ended ({'done' if 'done' in phases else 'FAIL'}); guard stops")
            return
        if "FULL-ON" in phases:
            say(f"trial pid {pid} gone after FULL-ON; the relay is on; guard stops")
            return
        if "CUT" in phases:
            dumped = any(r["phase"] == "dump" and "rgbcut" in r["note"] for r in rs)
            say(f"trial pid {pid} gone after the cut (dumped: {dumped}); finishing by hand")
            finish_after_cut(outdir, dumped)
            return
        restarts += 1
        base = re.sub(r"(-resumed-\d+)?\.csv$", "", csv_path)
        csv_path = f"{base}-resumed-{restarts}.csv"
        out = open(csv_path[:-4] + ".log", "a")
        child = subprocess.Popen([PY, TRIAL, "--out", csv_path] + args, cwd=ROOT,
                                 stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        pid, seen = child.pid, 0
        say(f"trial gone before the cut: restarted as pid {pid}, {csv_path}")


if __name__ == "__main__":
    main()
