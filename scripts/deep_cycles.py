#!/usr/bin/env python3
"""Deep sleep, many times over (plans/SLEEP-PLAN.md): does every cycle sleep,
wake on a key, keep the waking key, and deliver it to the Mac.

    venv/bin/python scripts/deep_cycles.py [--n 50] [--out DIR] [--board-only] [--awake]

--awake is the control: the same cycle never armed, so the board sits awake
through the same silence (the delay, the time to be seen asleep, the dwell);
a cycle passes when the logger shows it awake before the tap, the tap counts
exactly one press, and the link gives nothing up. Against an armed run it
says which ACK timeouts the wake causes (the 10-19 soak resent ~60% of its
frames; typing, ~4%).

Relay 2 off (the pack) and the stage forced dark for the whole run; per
cycle: data on, arm (op 14 sub 4, a 6 s delay, a 60 s cap), data off
(`uhubctl`); the logger shows it asleep; a random 5-25 s asleep; the wake
rig taps F19 (40 ms); the logger shows it awake; the F19 listener's log
gives the Mac's key-down and its latency from the relay command; data on,
and the board's record: woke on a key, which pin, the waking key
registered.

Pass per cycle: asleep seen, the record says it slept and woke on a key
(P2 bit 8, the rig's column) with the key registered, and the Mac's
key-down within 1 s. Needs the Mac UNLOCKED (the listener sees nothing at
the lock screen; checked at the start and per cycle) and the F19 listener
running; the displays are held awake. ⚠️ Hands off the keyboard. Restored
in a `finally`, each step on its own, under a whole-run deadline: relay 1
open, data on, the arm cancelled, the stage released, relay 2 on.
"""
import argparse, json, os, random, subprocess, sys, time

import hid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sleep_baseline as sb  # noqa: E402
import deep_sleep as ds  # noqa: E402  (uhub, on_bus, need_bus, deep, with_dev, light_off, tail_ma)
import keydrop_test as kd  # noqa: E402  (mac_locked, listener_events, LOG)

cm = sb.cm
DELAY_S, CAP_S = 6, 60
RIG_P2 = 1 << 8   # the wake rig's column, P2.8 (matrix (0,13))


def link():
    """The radio link's counters (HC_LINK), through the retrying open."""
    return ds.with_dev(lambda h: sb.health.read_link(h))


def wait_current(log, pred, after, limit_s):
    """The first time three logger samples taken after `after` satisfy pred."""
    deadline = time.time() + limit_s
    while time.time() < deadline:
        cur = [c for t, c in ds.tail_ma(log, 3) if t > after]
        if len(cur) == 3 and all(pred(c) for c in cur):
            return time.time()
        time.sleep(0.5)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--board-only", action="store_true",
                    help="no Mac-side timing (runs with the Mac locked): pass on the board's record alone")
    ap.add_argument("--awake", action="store_true",
                    help="the control: the same cycle, never armed, so the board stays awake through the silence")
    ap.add_argument("--log", default=os.path.join(HERE, "..", "history", "power-bench-2026-10-15", "power.csv"))
    ap.add_argument("--out", default=os.path.join(HERE, "..", "history", "deep-cycles-" + time.strftime("%Y%m%d-%H%M")))
    a = ap.parse_args()
    if not 1 <= a.n <= 500:
        sys.exit("--n: 1-500")
    if not cm.relay_on():
        sys.exit("relay 2 is off: start from the charging state")
    if not a.board_only:
        if kd.mac_locked():
            sys.exit("the Mac is locked: the F19 listener would see nothing (or --board-only)")
        if subprocess.run(["pgrep", "-x", "ak820-f19-listener"], capture_output=True).returncode:
            sys.exit("the F19 listener is not running")
    if not os.path.exists(a.log):
        sys.exit("the power logger's CSV is missing")
    caf = subprocess.Popen(["caffeinate", "-i"] if a.board_only else ["caffeinate", "-d"])   # the displays may sleep board-only
    devs = hid.enumerate(sb.VID, sb.PID)
    if len(devs) != 1:
        sys.exit(f"expected one USB relay, found {len(devs)}")
    cycles, ok, stopped = [], True, None
    guard = sb.deadline_guard(a.n * 75 + 300, lambda: ds.uhub("on"))
    with hid.Device(path=devs[0]["path"]) as dev:
        sb.hold_relay(dev)
        try:
            cm.say(f"{a.n} cycles{' (the awake control: never armed)' if a.awake else ''}; "
                   "relay 2 off; light sleep off; stage forced to SCREEN_OFF")
            sb.relay2("off")
            deadline = time.time() + 30
            while not sb.on_battery():
                if time.time() > deadline:
                    raise SystemExit("the board never reported the pack")
                time.sleep(1)
            ds.with_dev(ds.light_off)
            sb.stage(sb.SCREEN_OFF)
            time.sleep(5)
            for i in range(a.n):
                c = {"i": i + 1}
                if not a.board_only and kd.mac_locked():
                    cm.say("the Mac locked: stopping (the listener cannot see past the lock screen)")
                    c["stopped"] = "mac locked"
                    cycles.append(c)
                    ok = False
                    break
                l0 = link()
                if a.awake:
                    k0 = ds.with_dev(lambda h: sb.health.read_stalls(h)["key_presses"])
                else:
                    ds.with_dev(ds.deep, 1, DELAY_S, CAP_S, False)
                t_arm = time.time()
                ds.uhub("off")
                dwell = random.uniform(5, 25)
                c["dwell_s"] = round(dwell, 1)
                if a.awake:
                    time.sleep(DELAY_S + 2 + dwell)   # the silence an armed cycle keeps: the delay, seen asleep, the dwell
                    cur = [x for _, x in ds.tail_ma(a.log, 3)]
                    c["stayed_awake"] = len(cur) == 3 and all(x > 15.0 for x in cur)
                else:
                    t_sleep = wait_current(a.log, lambda x: x < 10.0, t_arm + DELAY_S, DELAY_S + 25)
                    c["asleep"] = t_sleep is not None
                    if t_sleep:
                        time.sleep(dwell)
                off = os.path.getsize(kd.LOG)
                t_press = time.time()
                if not a.awake and t_sleep:
                    c["asleep_s"] = round(t_press - t_sleep, 1)   # wall clock, seen asleep to the tap: against rec["secs"]
                sb.press(dev, 40, 200)
                t_wake = wait_current(a.log, lambda x: x > 15.0, t_press - 0.5, 10)
                c["awake_seen"] = t_wake is not None
                time.sleep(1.0)
                downs = [t for t, k in kd.listener_events(off) if k == "down"]
                c["mac_latency_ms"] = round((downs[0] - t_press) * 1000, 1) if downs else None
                ds.uhub("on")
                ds.need_bus(30)
                if a.awake:
                    c["key_gain"] = (ds.with_dev(lambda h: sb.health.read_stalls(h)["key_presses"]) - k0) & 0xFFFF
                else:
                    rec = ds.with_dev(ds.deep)
                    c["record"] = rec
                l1 = link()
                c["link"] = {k: l1[k] - l0[k] for k in ("sent", "timeouts", "giveups", "replaced", "queue_full",
                                                        "uart_overrun", "uart_framing", "uart_parity")}
                mac_ok = a.board_only or (c["mac_latency_ms"] is not None and c["mac_latency_ms"] < 1000)
                if a.awake:
                    good = c["stayed_awake"] and c["key_gain"] == 1 and c["link"]["giveups"] == 0 and mac_ok
                else:
                    good = (c["asleep"] and rec["slept"] and rec["reason"] == "key" and rec["p2"] & RIG_P2
                            and rec["key_gain"] >= 1 and rec["strays"] == 0 and c["link"]["giveups"] == 0 and mac_ok)
                c["ok"] = bool(good)
                ok &= c["ok"]
                cycles.append(c)
                head = (f"stayed awake {c['stayed_awake']}, {dwell + DELAY_S + 2:4.1f} s silent, key {c['key_gain']}"
                        if a.awake else
                        f"asleep {c['asleep']}, {rec['secs']:2d} s, woke {rec['reason']}, "
                        f"p2 {rec['p2']:#x}, key {rec['key_gain']}")
                cm.say(f"cycle {i + 1:3d}: {head}, Mac "
                       + (f"{c['mac_latency_ms']:.0f} ms" if c["mac_latency_ms"] is not None else "nothing")
                       + f", link sent {c['link']['sent']} timeouts {c['link']['timeouts']} giveups {c['link']['giveups']}"
                       + f"  {'ok' if c['ok'] else 'FAIL'}")
        except (Exception, SystemExit) as e:   # a stop mid-run keeps the cycles so far (the 10-19 soak lost its JSON)
            ok = False
            stopped = f"{type(e).__name__}: {e}"
            cm.say(f"stopped: {stopped}")
        finally:
            done = [sb.attempt("relay 1 open", sb.bounded, sb.relay1, dev, False),
                    sb.attempt("data on", ds.uhub, "on")]
            if sb.attempt("the board on USB", ds.need_bus, 30):
                done += [sb.attempt("arm cancelled", ds.with_dev, ds.deep, 2),
                         sb.attempt("stage released", sb.stage, None)]
            else:
                done.append(False)
            done.append(sb.attempt("relay 2 on", sb.relay2, "on"))
            guard.cancel()
            caf.terminate()
            if all(done):
                cm.say("restored: relay 1 open, data on, the arm cancelled, stage released, relay 2 on")
            else:
                ok = False
                cm.say("⚠️ cleanup incomplete: see above")
    lat = sorted(c["mac_latency_ms"] for c in cycles if c.get("mac_latency_ms") is not None)
    summary = {"cycles": len(cycles), "passed": sum(1 for c in cycles if c.get("ok")), "stopped": stopped,
               "mac_latency_ms": {"median": lat[len(lat) // 2], "max": lat[-1]} if lat else None}
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "deep-cycles.json"), "w") as f:
        json.dump({"summary": summary, "cycles": cycles, "at": time.strftime("%Y-%m-%d %H:%M:%S")},
                  f, indent=1, default=str)
    print("deep cycles:", "PASS" if ok else "FAIL", json.dumps(summary))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
