#!/usr/bin/env python3
"""Light sleep's first gate (plans/BATTERY-GAUGE-PLAN.md, "Its gate is the
wake rig"): what the wake rig's presses do on the pack, in the ladder's dark
stage, on the firmware light sleep will change -- the baseline every sleep
build is held to -- and the dark stage's current from the power logger.

    venv/bin/python scripts/sleep_baseline.py [--n 200] [--bursts 5] [--idles 60,300,900] [--quiet 300] [--out DIR]

In order, with relay 2 off (the board on its pack, USB data up) and the
stage forced to SCREEN_OFF over HC_DEVHOOK op 14 (keypresses do not lift a
forced stage, so the board stays dark throughout):
1. a quiet window, no presses, for the logger's current;
2. bursts of n presses at typing speed (40 ms held, 60 ms open) and at the
   rig's fast edge (25 ms, 40 ms);
3. one press after each idle in --idles, the first press after a silence.
Each counted at the board: raw_edges (before debounce, two per press) and
key_presses (after). Pass: every press counted, raw edges at least 98% of 2n
(the plain firmware's own short count), and no new stall >= 25 ms.

Nothing here reads the Mac, so it runs with the Mac locked (keydrop_test.py
times presses at the Mac, unlocked). ⚠️ Hands off the keyboard: key_presses
counts every key. The relay is loud. The stage and relay 2 are restored in a
`finally`. Needs a dev build with op 14 and HC_POWER, and relay 2 on at the
start.
"""
import argparse, json, os, subprocess, sys, threading, time

import hid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import current_map as cm  # noqa: E402  (xfer, force_stage, relay_on, run, measure, ak820text)
sys.path.insert(0, os.path.join(cm.ROOT, "hostagent"))
import ak820health as health  # noqa: E402

VID, PID = 0x16C0, 0x05DF   # the USB relay, as relay_press_test.py
HC_POWER = 0x0F
SCREEN_OFF = 3
RATES = [(40, 60), (25, 40)]   # (held ms, open ms)


def relay1(dev, on):
    dev.send_feature_report(bytes([0, 0xFF if on else 0xFD, 1, 0, 0, 0, 0, 0, 0]))


# The relay while a run holds it open: macOS opens it EXCLUSIVELY, so with
# relay 1 held for the presses, relay 2 must go through the same handle (a
# usb_relay.py subprocess cannot open it then: "exclusive access and device
# already open", 18:25 10-18). hold_relay() sets it.
_dev = None


def hold_relay(dev):
    global _dev
    _dev = dev


def _relay2_inproc(on):
    _dev.send_feature_report(bytes([0, 0xFF if on else 0xFD, 2, 0, 0, 0, 0, 0, 0]))
    if bool(relay_state(_dev) & 2) != on:
        raise SystemExit("relay 2's read-back does not match the command")


def relay_state(dev):
    raw = dev.get_feature_report(0, 9)
    return raw[8]


def relay2(state):
    """Bounded: a hung relay call must not hold a cleanup (codex). Through the
    held handle when a run has one, else a usb_relay.py subprocess."""
    if _dev is not None:
        return bounded(_relay2_inproc, state == "on", timeout=5.0)
    try:
        rc = subprocess.run([cm.PY, cm.RELAY, state, "2"], capture_output=True, text=True, cwd=cm.ROOT, timeout=20)
    except subprocess.TimeoutExpired:
        raise SystemExit(f"relay {state} 2 timed out")
    if rc.returncode:
        raise SystemExit(f"relay {state} 2 failed: {(rc.stdout + rc.stderr).strip()}")


def link():
    """The radio link's loss counters (HC_LINK): timeouts, give-ups, queue full, UART errors."""
    h = cm.ak820text.open_device()
    try:
        return health.read_link(h)
    finally:
        h.close()


def bounded(fn, *args, timeout=5.0):
    """Run fn in a thread and give up waiting after `timeout` s: an in-process
    HID call that hangs must not hold every cleanup step behind it (codex)."""
    box = {}

    def run():
        try:
            box["ok"] = fn(*args)
        except BaseException as e:
            box["err"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise SystemExit(f"timed out after {timeout:.0f} s")
    if "err" in box:
        raise box["err"]
    return box.get("ok")


# One cleanup at a time (codex, the deep-sleep build's second verify): a run's
# `finally` and its deadline guard both restore the bench, and must not tap
# the rig or close the relay's handle under each other. Re-entrant, so the
# guard can hold it across its own restore and its exit.
cleanup_lock = threading.RLock()


def _relays_safe():
    """Relay 1 open and relay 2 on through the held handle, each bounded: the
    last thing before an exit (a tap cut short must not leave F19 held)."""
    for what, fn in (("relay 1 open", lambda: relay1(_dev, False)), ("relay 2 on", lambda: _relay2_inproc(True))):
        if _dev is None:
            break
        try:
            bounded(fn, timeout=2.0)
            print(f"emergency: {what}", flush=True)
        except BaseException as e:
            print(f"⚠️ emergency: {what} failed: {e}", flush=True)


def deadline_guard(limit_s, *also, hard_s=300.0):
    """A last resort for the whole run (codex): if it is still going after
    limit_s, run `also` (the run's restore) from this timer's thread, holding
    cleanup_lock so the run's own cleanup cannot interleave, then open relay 1,
    turn relay 2 on, and end the process. A second, independent timer ends it
    hard_s later whatever `also` is stuck on (an HID open or write has no
    timeout of its own), after one more bounded try at the relays. A hung call
    anywhere can then strand the board for at most limit_s + hard_s. Cancel
    it on a normal finish."""
    def hard_exit():
        print(f"⚠️ the emergency restore passed {hard_s:.0f} s: exiting", flush=True)
        _relays_safe()
        os._exit(4)

    def fire():
        h = threading.Timer(hard_s, hard_exit)
        h.daemon = True
        h.start()
        print(f"⚠️ the run passed its {limit_s:.0f} s limit: restoring and exiting", flush=True)
        with cleanup_lock:
            for fn in also:
                try:
                    fn()
                except BaseException as e:
                    print(f"⚠️ emergency step failed: {e}", flush=True)
            if _dev is not None:
                _relays_safe()
            else:
                try:
                    relay2("on")
                    print("emergency: relay 2 on", flush=True)
                except BaseException as e:
                    print(f"⚠️ emergency: relay 2 on failed: {e}", flush=True)
            os._exit(3)
    t = threading.Timer(limit_s, fire)
    t.daemon = True
    t.start()
    return t


def attempt(what, fn, *args):
    """One cleanup step, independent of the others; says how it went."""
    try:
        fn(*args)
        return True
    except BaseException as e:   # SystemExit included: the next step still runs
        print(f"⚠️ cleanup: {what} failed: {e}", flush=True)
        return False


def on_battery():
    h = cm.ak820text.open_device()
    try:
        return bool(cm.xfer(h, [cm.SET_VALUE, cm.HEALTH_CH, HC_POWER])[29])
    finally:
        h.close()


def stage(s):
    h = cm.ak820text.open_device()
    try:
        cm.force_stage(h, s)
    finally:
        h.close()


def counts():
    st, rw = health.read_stalls(), health.read_rows()
    return {"presses": st["key_presses"], "edges": rw["raw_edges"], "stalls": st["count_ge_25ms_nonflash"]}


def delta(a, b):
    return {k: (b[k] - a[k]) & 0xFFFF for k in a}


def press(dev, held, gap):
    relay1(dev, True)
    time.sleep(held / 1000)
    relay1(dev, False)
    time.sleep(gap / 1000)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--bursts", type=int, default=5, help="bursts per rate")
    ap.add_argument("--idles", default="60,300,900", help="seconds of silence before each single press")
    ap.add_argument("--quiet", type=int, default=300, help="seconds with no presses, for the logger")
    ap.add_argument("--log", default=os.path.join(HERE, "..", "history", "power-bench-2026-10-15", "power.csv"))
    ap.add_argument("--out", default=os.path.join(HERE, "..", "history", "sleep-baseline-" + time.strftime("%Y%m%d-%H%M")))
    a = ap.parse_args()
    idles = [int(x) for x in a.idles.split(",") if x]
    if not cm.relay_on():
        sys.exit("relay 2 is off: start from the charging state")
    devs = hid.enumerate(VID, PID)
    if len(devs) != 1:
        sys.exit(f"expected one USB relay, found {len(devs)}")
    res = {"bursts": [], "idles": [], "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    ok = True
    guard = deadline_guard(a.quiet + sum(idles) + 2 * a.bursts * a.n * 0.2 + 600)
    with hid.Device(path=devs[0]["path"]) as dev:
        hold_relay(dev)   # relay 2 goes through this handle too (macOS opens it exclusively)
        try:
            cm.say("relay 2 off; stage forced to SCREEN_OFF")
            relay2("off")
            deadline = time.time() + 30
            while not on_battery():
                if time.time() > deadline:
                    raise SystemExit("the board never reported the pack")
                time.sleep(1)
            stage(SCREEN_OFF)
            time.sleep(5)
            start = counts()
            t0 = time.time()
            time.sleep(a.quiet)
            t1 = time.time()
            q = delta(start, counts())
            m = cm.measure(a.log, t0, t1) if os.path.exists(a.log) else None
            res["quiet"] = {"s": a.quiet, "counts": q, "logger": m}
            cm.say(f"quiet {a.quiet} s: {q}; logger "
                   + (f"{m['q_ma']:.2f} mA (charge), {m['mean_ma']:.2f} mean, {m['vcell_mv']:.0f} mV" if m else "none"))
            ok &= q["presses"] == 0 and q["edges"] == 0
            for held, gap in RATES:
                for b in range(a.bursts):
                    c0 = counts()
                    for _ in range(a.n):
                        press(dev, held, gap)
                    time.sleep(0.15)   # the last release's debounce
                    d = delta(c0, counts())
                    # Raw edges come up a few short of 2n on the plain firmware too
                    # (791/800, 798/800 on 10-18): at least 98%, as sleep_sizing.py.
                    good = d["presses"] == a.n and d["edges"] >= 0.98 * 2 * a.n and d["stalls"] == 0
                    ok &= good
                    res["bursts"].append({"held_ms": held, "gap_ms": gap, "n": a.n, **d, "ok": good})
                    cm.say(f"burst {held}/{gap} ms #{b + 1}: presses {d['presses']}/{a.n}, "
                           f"edges {d['edges']}/{2 * a.n}, stalls {d['stalls']}  {'ok' if good else 'FAIL'}")
            for idle in idles:
                c0 = counts()
                time.sleep(idle)
                t = time.time()
                press(dev, 40, 150)
                d = delta(c0, counts())
                good = d["presses"] == 1 and d["edges"] == 2 and d["stalls"] == 0
                ok &= good
                res["idles"].append({"idle_s": idle, "at": t, **d, "ok": good})
                cm.say(f"one press after {idle} s idle: presses {d['presses']}, edges {d['edges']}, "
                       f"stalls {d['stalls']}  {'ok' if good else 'FAIL'}")
        finally:
            done = [attempt("relay 1 open", bounded, relay1, dev, False),
                    attempt("stage released", stage, None),
                    attempt("relay 2 on", relay2, "on")]
            guard.cancel()
            if all(done):
                cm.say("restored: relay 1 open, stage released, relay 2 on")
            else:
                ok = False
                cm.say("⚠️ cleanup incomplete: see above")
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "sleep-baseline.json"), "w") as f:
        json.dump(res, f, indent=1)
    print("sleep baseline:", "pass" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
