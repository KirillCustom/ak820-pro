#!/usr/bin/env python3
"""The deep-sleep experiment on the bench (plans/SLEEP-PLAN.md): does a key
wake it, what does it draw asleep, and is the waking key itself lost.

    venv/bin/python scripts/deep_sleep.py [--wait 20] [--no-press] [--no-feed] [--cap 120] [--out DIR]

In order: relay 2 off (the pack); the stage forced to SCREEN_OFF and light
sleep off; arm (op 14 sub 4: a 15 s delay, the cap); USB data off
(`uhubctl`, so USB is not configured, one of the sleep's preconditions);
watch the logger for the current to fall (asleep: entry confirmed, or the
run stops there); --wait s after that the wake rig presses F19 once (unless
--no-press: the cap wakes it); then data back on, wait for the board, and
read the record: why it woke, which column pins, seconds asleep, stray
wakes, the PLL's lock loops, whether the waking key registered, and the USB
transceiver after the wake.

--no-feed leaves the watchdog unfed while asleep: a reset ~12 s in means it
counts in Deep-sleep (the record is then gone with the RAM; the retained
watchdog record, `ak820 health --json`, names site 27); the cap reached
unfed means it does not. Measured 19:16 10-18: it does not.

Pass: entry seen in the logger; the record says it slept and why (a key on a
press run, the cap on a --no-press run, a reset in the deep-sleep site on a
--no-feed run); on a press run the waking key registered. Needs JD at hand:
if it never wakes, the slider brings it back. Restored in a `finally`, each
step on its own: relay 1 open, data on, the arm cancelled, the stage
released, relay 2 on.
"""
import argparse, json, os, subprocess, sys, time

import hid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sleep_baseline as sb  # noqa: E402

cm = sb.cm
HUB, PORT = "0-1.4", "2"   # docs/test-bench.md: the relay cable's port
REASONS = {0: "none", 1: "key", 2: "cap", 3: "strays", 4: "blocked"}
AK820 = os.path.expanduser("~/Library/Application Support/ak820pro/bin/ak820")
DELAY_S = 15


def uhub(state):
    try:
        rc = subprocess.run(["uhubctl", "-l", HUB, "-p", PORT, "-a", state, "-e"],
                            capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        raise SystemExit(f"uhubctl {state} timed out")
    if rc.returncode:
        raise SystemExit(f"uhubctl {state} failed: {rc.stdout}{rc.stderr}")


def on_bus():
    out = subprocess.run(["ioreg", "-p", "IOUSB", "-w0"], capture_output=True, text=True, timeout=10).stdout
    return "AK820 PRO@" in out


def deep(h, arg=0, delay=DELAY_S, cap=120, no_feed=False):
    """op 14 sub 4: arg 0 reads, 1 arms, 2 cancels; returns the record."""
    r = cm.xfer(h, [cm.SET_VALUE, cm.HEALTH_CH, cm.HC_DEVHOOK, 14, 4, arg, delay,
                    cap & 0xFF, cap >> 8, 1 if no_feed else 0] + [0] * 22)
    if len(r) < 29 or r[3] != 14 or r[4] != 4:
        raise SystemExit(f"a reply to another op: {r[:12].hex()}")
    o = r[5:29]
    return {"state": o[0], "reason": REASONS.get(o[1], o[1]), "blocked": o[2], "slept": bool(o[3]),
            "secs": o[4] | o[5] << 8, "strays": o[6] | o[7] << 8,
            "p0": int.from_bytes(o[8:12], "little"), "p2": int.from_bytes(o[12:16], "little"),
            "pll_loops": o[16] | o[17] << 8, "key_gain": o[18] | o[19] << 8, "pll_ceiling": bool(o[20]),
            "phy_was_off": bool(o[21]), "usb_back_20s": bool(o[22])}   # false inside 20 s of the wake can mean pending


def need_bus(wait_s=30.0):
    """Back on USB, waiting up to wait_s for it to enumerate (codex)."""
    deadline = time.time() + wait_s
    while not on_bus():
        if time.time() > deadline:
            raise SystemExit(f"not on USB after {wait_s:.0f} s")
        time.sleep(0.5)
    time.sleep(2)


def open_dev(wait_s=30.0):
    """The board's raw HID, retried: on macOS it opens exclusively, and the
    now-playing daemon reopens it the moment USB comes back (the cycle run's
    seventh cycle, 20:14 10-18: "raw HID interface busy"). 30 s, not 8: the
    daemon's clock sync on enumeration held it 11 s (05:43:24-35 10-19) and
    stopped the 300-cycle soak at cycle 248."""
    deadline = time.time() + wait_s
    while True:
        try:
            return cm.ak820text.open_device()
        except SystemExit:
            if time.time() > deadline:
                raise
            time.sleep(0.3)


def with_dev(fn, *args, **kw):
    h = open_dev()
    try:
        return fn(h, *args, **kw)
    finally:
        h.close()


def light_off(h):
    cm.xfer(h, [cm.SET_VALUE, cm.HEALTH_CH, cm.HC_DEVHOOK, 14, 3, 0, 2, 0, 0] + [0] * 23)


def tail_ma(path, n=8):
    """The logger's last n rows (n <= ~35), (epoch, mA), read from the file's end (it is large)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 4096))
            lines = f.read().decode(errors="replace").splitlines()[1:]
    except OSError:
        return []
    out = []
    for ln in lines[-n:]:
        p = ln.split(",")
        try:
            out.append((float(p[1]), float(p[5])))
        except (IndexError, ValueError):
            pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", type=float, default=20.0, help="s asleep before the press")
    ap.add_argument("--no-press", action="store_true")
    ap.add_argument("--no-feed", action="store_true")
    ap.add_argument("--cap", type=int, default=120)
    ap.add_argument("--log", default=os.path.join(HERE, "..", "history", "power-bench-2026-10-15", "power.csv"))
    ap.add_argument("--out", default=os.path.join(HERE, "..", "history", "deep-sleep-" + time.strftime("%Y%m%d-%H%M")))
    a = ap.parse_args()
    if not 10 <= a.cap <= 7200:
        sys.exit("--cap: 10-7200 s")
    if not a.no_press and not 5 <= a.wait <= a.cap - 5:
        sys.exit("--wait: 5 s to the cap less 5")
    if not cm.relay_on():
        sys.exit("relay 2 is off: start from the charging state")
    if not os.path.exists(a.log):
        sys.exit("the power logger's CSV is missing: entry cannot be confirmed")
    res = {"args": vars(a), "before": with_dev(deep)}
    devs = hid.enumerate(sb.VID, sb.PID)
    if len(devs) != 1:
        sys.exit(f"expected one USB relay, found {len(devs)}")
    ok = False
    guard = sb.deadline_guard(DELAY_S + a.cap + 300, lambda: uhub("on"))
    with hid.Device(path=devs[0]["path"]) as dev:
        sb.hold_relay(dev)   # relay 2 goes through this handle too (macOS opens it exclusively)
        try:
            cm.say("relay 2 off; light sleep off; stage forced to SCREEN_OFF")
            sb.relay2("off")
            deadline = time.time() + 30
            while not sb.on_battery():
                if time.time() > deadline:
                    raise SystemExit("the board never reported the pack")
                time.sleep(1)
            with_dev(light_off)
            sb.stage(sb.SCREEN_OFF)
            time.sleep(8)
            base = [c for _, c in tail_ma(a.log, 8)]
            res["awake_ma"] = sum(base) / len(base) if base else None
            kp0 = sb.counts()["presses"]
            l0 = with_dev(lambda h: sb.health.read_link(h))
            with_dev(deep, 1, DELAY_S, a.cap, a.no_feed)
            t_arm = time.time()
            cm.say(f"armed ({DELAY_S} s delay, cap {a.cap} s); awake {res['awake_ma']:.2f} mA with data; data off")
            uhub("off")
            # The awake draw with the data off (the USB data itself costs ~1.7
            # mA), from the samples before the delay ends; then entry: three
            # samples after it, 2 mA under that (codex).
            time.sleep(max(0.0, t_arm + DELAY_S - 1 - time.time()))
            off = [c for t, c in tail_ma(a.log, 12) if t_arm + 5 < t < t_arm + DELAY_S - 1]
            res["awake_dataoff_ma"] = sum(off) / len(off) if off else None
            t_asleep = None
            if res["awake_dataoff_ma"] is not None:
                while time.time() < t_arm + DELAY_S + 30:
                    cur = [c for t, c in tail_ma(a.log, 3) if t > t_arm + DELAY_S + 1]
                    if len(cur) == 3 and all(c < res["awake_dataoff_ma"] - 2.0 for c in cur):
                        t_asleep = time.time()
                        break
                    time.sleep(1)
            res["asleep_seen_at"] = t_asleep
            if t_asleep is None:
                cm.say("no fall in the current: it did not go to sleep (the record will say why)")
            elif a.no_press or a.no_feed:
                cm.say("asleep (the current fell)")
                time.sleep(a.cap + 10 if a.no_press and not a.no_feed else 25)
            else:
                cm.say(f"asleep (the current fell); pressing F19 in {a.wait:.0f} s")
                time.sleep(a.wait)
                asleep = [c for t, c in tail_ma(a.log, int(a.wait) - 2) if t > t_asleep]
                res["asleep_ma"] = sum(asleep) / len(asleep) if asleep else None
                res["press_at"] = time.time()
                sb.press(dev, 40, 200)   # a realistic tap, not a long hold (codex)
                time.sleep(5)
            cm.say("data on")
            uhub("on")
            need_bus(30)
            rec = with_dev(deep)
            res["record"] = rec
            l1 = with_dev(lambda h: sb.health.read_link(h))
            res["link"] = {k: l1[k] - l0[k] for k in ("sent", "timeouts", "giveups", "queue_full", "uart_overrun",
                                                      "uart_framing", "uart_parity")}
            cm.say(f"link across the run: {res['link']}")
            res["key_presses_gained"] = (sb.counts()["presses"] - kp0) & 0xFFFF
            res["logger"] = [(float(r["epoch"]), float(r["current_ma"])) for r in cm.log_rows(a.log, t_arm - 10, time.time())]
            cm.say(f"record: {rec}")
            if res.get("asleep_ma") is not None:
                cm.say(f"asleep: {res['asleep_ma']:.2f} mA (awake {res['awake_ma']:.2f})")
            if a.no_feed:
                try:
                    hj = json.loads(subprocess.run([AK820, "health", "--json"], capture_output=True, text=True,
                                                   timeout=10).stdout)
                    res["watchdog_record"] = hj.get("watchdog_record")
                except (OSError, ValueError, subprocess.TimeoutExpired) as e:
                    res["watchdog_record"] = f"unread: {e}"
                wr = res["watchdog_record"] if isinstance(res["watchdog_record"], dict) else {}
                # Either answer settles it: a reset recorded in site 27 (the
                # watchdog counts asleep), or the cap reached unfed (it does
                # not: 19:16 10-18, 30 s unfed, no reset).
                reset_asleep = rec["state"] == 0 and wr.get("valid") and wr.get("site_id") == 27
                slept_through = rec["slept"] and rec["reason"] == "cap"
                res["watchdog_counts_asleep"] = True if reset_asleep else (False if slept_through else None)
                ok = t_asleep is not None and (reset_asleep or slept_through)
            elif a.no_press:
                ok = t_asleep is not None and rec["slept"] and rec["reason"] == "cap"
            else:
                ok = (t_asleep is not None and rec["slept"] and rec["reason"] == "key" and rec["key_gain"] >= 1
                      and res["link"]["giveups"] == 0)
        finally:
            done = [sb.attempt("relay 1 open", sb.bounded, sb.relay1, dev, False),
                    sb.attempt("data on", uhub, "on")]
            if sb.attempt("the board on USB", need_bus, 30):
                done += [sb.attempt("arm cancelled", with_dev, deep, 2),
                         sb.attempt("stage released", sb.stage, None)]
            else:
                done.append(False)
            done.append(sb.attempt("relay 2 on", sb.relay2, "on"))
            guard.cancel()
            if all(done):
                cm.say("restored: relay 1 open, data on, the arm cancelled, stage released, relay 2 on")
            else:
                ok = False
                cm.say("⚠️ cleanup incomplete: see above")
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "deep-sleep.json"), "w") as f:
        json.dump(res, f, indent=1, default=str)
    print("deep sleep:", "PASS" if ok else "FAIL", json.dumps(res.get("record", {})))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
