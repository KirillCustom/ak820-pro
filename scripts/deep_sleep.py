#!/usr/bin/env python3
"""The deep-sleep experiment on the bench (plans/SLEEP-PLAN.md): does a key
wake it, what does it draw asleep, and is the waking key itself lost.

    venv/bin/python scripts/deep_sleep.py [--press-at 40] [--no-press] [--no-feed] [--cap 120] [--out DIR]

In order: relay 2 off (the pack); the stage forced to SCREEN_OFF; arm (op
14 sub 4: a 15 s delay, the cap); USB data off (`uhubctl`, so USB is not
configured, one of the sleep's preconditions); watch the logger for the
current to fall (asleep); at --press-at s after the arm the wake rig presses
F19 once (unless --no-press: the cap wakes it); then data back on, wait for
the board, and read the record: why it woke, which column pins, seconds
asleep, stray wakes, the PLL's lock loops, and whether the waking key
registered. --no-feed leaves the watchdog unfed while asleep: a reset at
~12 s says it counts in Deep-sleep (the record is then gone; the boot shows
it).

Needs JD at hand: if it never wakes, the slider brings it back. Restored in
a `finally`: relay 1 open, data on, the stage released, relay 2 on.
"""
import argparse, json, os, subprocess, sys, time

import hid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sleep_baseline as sb  # noqa: E402

cm = sb.cm
HUB, PORT = "0-1.4", "2"   # docs/test-bench.md: the relay cable's port
REASONS = {0: "none", 1: "key", 2: "cap", 3: "strays", 4: "blocked"}


def uhub(state):
    rc = subprocess.run(["perl", "-e", "alarm 15; exec @ARGV", "uhubctl", "-l", HUB, "-p", PORT, "-a", state, "-e"],
                        capture_output=True, text=True)
    if rc.returncode:
        raise SystemExit(f"uhubctl {state} failed: {rc.stdout}{rc.stderr}")


def on_bus():
    out = subprocess.run(["ioreg", "-p", "IOUSB", "-w0"], capture_output=True, text=True).stdout
    return "AK820 PRO@" in out


def deep(h, arm=False, delay=15, cap=120, no_feed=False):
    r = cm.xfer(h, [cm.SET_VALUE, cm.HEALTH_CH, cm.HC_DEVHOOK, 14, 4, 1 if arm else 0, delay,
                    cap & 0xFF, cap >> 8, 1 if no_feed else 0] + [0] * 22)
    if r[4] != 4:
        raise SystemExit(f"a reply to another sub-op: {r[:12].hex()}")
    o = r[5:29]
    return {"state": o[0], "reason": REASONS.get(o[1], o[1]), "blocked": o[2], "slept": bool(o[3]),
            "secs": o[4] | o[5] << 8, "strays": o[6] | o[7] << 8,
            "p0": int.from_bytes(o[8:12], "little"), "p2": int.from_bytes(o[12:16], "little"),
            "pll_loops": o[16] | o[17] << 8, "key_gain": o[18] | o[19] << 8, "pll_ceiling": bool(o[20])}


def logger_ma(path, t0, t1):
    rows = cm.log_rows(path, t0, t1) if os.path.exists(path) else []
    return [(float(r["epoch"]), float(r["current_ma"])) for r in rows]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--press-at", type=float, default=40.0, help="s after the arm")
    ap.add_argument("--no-press", action="store_true")
    ap.add_argument("--no-feed", action="store_true")
    ap.add_argument("--cap", type=int, default=120)
    ap.add_argument("--log", default=os.path.join(HERE, "..", "history", "power-bench-2026-10-15", "power.csv"))
    ap.add_argument("--out", default=os.path.join(HERE, "..", "history", "deep-sleep-" + time.strftime("%Y%m%d-%H%M")))
    a = ap.parse_args()
    if not cm.relay_on():
        sys.exit("relay 2 is off: start from the charging state")
    h = cm.ak820text.open_device()
    try:
        before = deep(h)
    finally:
        h.close()
    devs = hid.enumerate(sb.VID, sb.PID)
    if len(devs) != 1:
        sys.exit(f"expected one USB relay, found {len(devs)}")
    res = {"args": vars(a), "before": before}
    with hid.Device(path=devs[0]["path"]) as dev:
        try:
            cm.say("relay 2 off; stage forced to SCREEN_OFF")
            sb.relay2("off")
            deadline = time.time() + 30
            while not sb.on_battery():
                if time.time() > deadline:
                    raise SystemExit("the board never reported the pack")
                time.sleep(1)
            sb.stage(sb.SCREEN_OFF)
            kp0 = sb.counts()["presses"]
            h = cm.ak820text.open_device()
            try:
                deep(h, arm=True, delay=15, cap=a.cap, no_feed=a.no_feed)
            finally:
                h.close()
            t_arm = time.time()
            cm.say(f"armed (15 s delay, cap {a.cap} s); data off")
            uhub("off")
            if a.no_press:
                time.sleep(15 + a.cap + 10)
            else:
                time.sleep(max(0.0, t_arm + a.press_at - time.time()))
                t_press = time.time()
                cm.say("pressing F19 (the wake rig)")
                sb.press(dev, 60, 200)
                res["press_at"] = t_press
                time.sleep(5)
            t_on = time.time()
            cm.say("data on")
            uhub("on")
            deadline = time.time() + 30
            while not on_bus():
                if time.time() > deadline:
                    raise SystemExit("the board did not come back on USB in 30 s: check it by hand")
                time.sleep(0.5)
            time.sleep(3)
            h = cm.ak820text.open_device()
            try:
                rec = deep(h)
            finally:
                h.close()
            res["record"] = rec
            res["key_presses_gained"] = (sb.counts()["presses"] - kp0) & 0xFFFF
            cur = logger_ma(a.log, t_arm - 10, t_on)
            res["logger"] = cur
            cm.say(f"record: {rec}")
            asleep = [c for t, c in cur if t > t_arm + 20 and t < (res.get("press_at", t_on) - 1)]
            if asleep:
                res["asleep_ma"] = sum(asleep) / len(asleep)
                cm.say(f"logger asleep: {res['asleep_ma']:.2f} mA mean over {len(asleep)} s "
                       f"(min {min(asleep):.2f}, max {max(asleep):.2f})")
        finally:
            sb.relay1(dev, False)
            try:
                uhub("on")
            except SystemExit as e:
                print(e)
            try:
                if on_bus():
                    sb.stage(None)
            finally:
                sb.relay2("on")
                cm.say("restored: data on, stage released, relay 2 on")
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "deep-sleep.json"), "w") as f:
        json.dump(res, f, indent=1, default=str)
    rec = res.get("record", {})
    print("deep sleep:", rec.get("reason"), "slept" if rec.get("slept") else "did not sleep",
          f"{rec.get('secs')} s, key gained {rec.get('key_gain')}")


if __name__ == "__main__":
    main()
