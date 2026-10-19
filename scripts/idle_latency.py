#!/usr/bin/env python3
"""The first key after a silence, awake: the control for deep_cycles.py's
latency (plans/SLEEP-PLAN.md). Same conditions as a cycle but no sleep: the
pack (relay 2 off), USB data cut, the stage forced dark; a random 5-25 s of
silence, then the wake rig taps F19 (40 ms) and the F19 listener times the
Mac's key-down from the relay command.

    venv/bin/python scripts/idle_latency.py [--n 12] [--out DIR]

If these first keys are as slow as the deep-sleep wakes (41-88 ms on 10-18,
against ~35 ms mid-typing), the extra is the radio link's idle, not the sleep.
Needs the Mac unlocked and the listener running. ⚠️ Hands off the keyboard.
Restored in a `finally`, under a whole-run deadline.
"""
import argparse, json, os, random, subprocess, sys, time

import hid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sleep_baseline as sb  # noqa: E402
import deep_sleep as ds  # noqa: E402
import keydrop_test as kd  # noqa: E402

cm = sb.cm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--out", default=os.path.join(HERE, "..", "history", "idle-latency-" + time.strftime("%Y%m%d-%H%M")))
    a = ap.parse_args()
    if not cm.relay_on():
        sys.exit("relay 2 is off: start from the charging state")
    if kd.mac_locked():
        sys.exit("the Mac is locked: the F19 listener would see nothing")
    if subprocess.run(["pgrep", "-x", "ak820-f19-listener"], capture_output=True).returncode:
        sys.exit("the F19 listener is not running")
    caf = subprocess.Popen(["caffeinate", "-d"])
    devs = hid.enumerate(sb.VID, sb.PID)
    if len(devs) != 1:
        sys.exit(f"expected one USB relay, found {len(devs)}")
    rows, ok = [], True
    guard = sb.deadline_guard(a.n * 30 + 180, lambda: ds.uhub("on"))
    with hid.Device(path=devs[0]["path"]) as dev:
        sb.hold_relay(dev)
        try:
            cm.say(f"{a.n} presses after silences, awake; relay 2 off; stage forced to SCREEN_OFF; data off")
            sb.relay2("off")
            deadline = time.time() + 30
            while not sb.on_battery():
                if time.time() > deadline:
                    raise SystemExit("the board never reported the pack")
                time.sleep(1)
            ds.with_dev(ds.light_off)
            sb.stage(sb.SCREEN_OFF)
            ds.uhub("off")
            for i in range(a.n):
                if kd.mac_locked():
                    cm.say("the Mac locked: stopping")
                    ok = False
                    break
                quiet = random.uniform(5, 25)
                time.sleep(quiet)
                off = os.path.getsize(kd.LOG)
                t = time.time()
                sb.press(dev, 40, 200)
                time.sleep(1.0)
                downs = [e for e, k in kd.listener_events(off) if k == "down"]
                lat = round((downs[0] - t) * 1000, 1) if downs else None
                rows.append({"i": i + 1, "quiet_s": round(quiet, 1), "mac_latency_ms": lat})
                ok &= lat is not None
                cm.say(f"press {i + 1:2d} after {quiet:4.1f} s: Mac " + (f"{lat:.0f} ms" if lat is not None else "nothing"))
        finally:
            done = [sb.attempt("relay 1 open", sb.bounded, sb.relay1, dev, False),
                    sb.attempt("data on", ds.uhub, "on")]
            if sb.attempt("the board on USB", ds.need_bus, 30):
                done.append(sb.attempt("stage released", sb.stage, None))
            else:
                done.append(False)
            done.append(sb.attempt("relay 2 on", sb.relay2, "on"))
            guard.cancel()
            caf.terminate()
            if all(done):
                cm.say("restored: relay 1 open, data on, stage released, relay 2 on")
            else:
                ok = False
                cm.say("⚠️ cleanup incomplete: see above")
    lat = sorted(r["mac_latency_ms"] for r in rows if r["mac_latency_ms"] is not None)
    summary = {"n": len(rows), "median_ms": lat[len(lat) // 2] if lat else None,
               "min_ms": lat[0] if lat else None, "max_ms": lat[-1] if lat else None}
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "idle-latency.json"), "w") as f:
        json.dump({"summary": summary, "rows": rows, "at": time.strftime("%Y-%m-%d %H:%M:%S")}, f, indent=1)
    print("idle latency:", "ok" if ok else "FAIL", json.dumps(summary))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
