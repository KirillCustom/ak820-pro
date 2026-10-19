#!/usr/bin/env python3
"""One sleep through the ladder's own path at a mode's real sleep time
(plans/DEEP-SLEEP-BUILD-PLAN.md, validation 2, 3 and 6): no bench sleep time,
no stage forced, no arm.

    venv/bin/python scripts/ladder_sleep.py --mode 1 [--asleep-s 60] [--charge-wake] [--out DIR]

In order: the mode set (op 14 sub 1; put back at the end); relay 2 off (the
pack); a rig tap (F19, to the Mac: the ladder's idle starts there); USB data
off (`uhubctl`). The logger then shows the sleep: its fall is timed from the
tap and must come at the mode's sleep time (Balanced 420 s, Power saver 180,
Lights out 30), within the stages' pass. --asleep-s after that, the wake:
the rig's tap, or with --charge-wake relay 2 on (CHRG falls if the pack takes
a charge; a full pack's charger gives no edge, so after 20 s with no rise the
rig taps). Then data on, and the board's figures: why it woke, the PCF's
slept seconds against the logger's edges, the recovery, the battery log, and
the daemon's first clock offset after the data came back (its "before").

Pass: asleep at the mode's time (+-15 s), woke on the expected source, the
PCF within 2 s of the logger's edges, the recovery closed with nothing timed
out, exactly one key for a rig wake, no reset, no pass of 25 ms or more.
⚠️ Hands off the keyboard. Restored in a `finally` (deep_sleep.restore):
relay 1 open, relay 2 on, data on, the board back, the mode put back.
"""
import argparse, json, os, re, subprocess, sys, time

import hid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sleep_baseline as sb  # noqa: E402
import deep_sleep as ds  # noqa: E402
import ak820battery as ba  # noqa: E402

cm = sb.cm
SLEEP_S = {0: None, 1: 420, 2: 180, 3: 30}   # Always on never; Balanced 7 min; Power saver 3; Lights out 30 s
AGENT_LOG = os.path.expanduser("~/Library/Logs/ak820pro/ak820-agent.log")


def snap(h):
    st = sb.health.read_stalls(h)
    return {"presses": st["key_presses"], "ge25": st["count_ge_25ms"], "uptime_ms": sb.health.read_isr(h)["uptime_ms"],
            "link": sb.health.read_link(h), "page": ba.sleep_page(h)}


def first_sync_after(t0, limit_s=90):
    pat = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) sync \(([^)]*)\): .*?before ([+-]?[\d.]+) ms")
    deadline = time.time() + limit_s
    while time.time() < deadline:
        try:
            with open(AGENT_LOG, errors="replace") as f:
                for ln in f:
                    m = pat.match(ln)
                    if m:
                        t = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
                        if t >= t0 - 1:
                            return {"at_s": round(t - t0), "why": m.group(2), "before_ms": float(m.group(3))}
        except OSError:
            pass
        time.sleep(3)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", type=int, required=True, help="1 Balanced, 2 Power saver, 3 Lights out")
    ap.add_argument("--asleep-s", type=float, default=60.0)
    ap.add_argument("--charge-wake", action="store_true", help="wake by relay 2 on, then the rig after 20 s")
    ap.add_argument("--log", default=os.path.join(HERE, "..", "history", "power-bench-2026-10-15", "power.csv"))
    ap.add_argument("--out", default=os.path.join(HERE, "..", "history", "ladder-sleep-" + time.strftime("%Y%m%d-%H%M")))
    a = ap.parse_args()
    want = SLEEP_S.get(a.mode)
    if want is None:
        sys.exit("--mode: 1, 2 or 3 (Always on never sleeps)")
    if not 20 <= a.asleep_s <= 4 * 3600:
        sys.exit("--asleep-s: 20 s to 4 h")
    if not cm.relay_on():
        sys.exit("relay 2 is off: start from the charging state")
    devs = hid.enumerate(sb.VID, sb.PID)
    if len(devs) != 1:
        sys.exit(f"expected one USB relay, found {len(devs)}")
    mode0, _ = ds.with_dev(ds.power_op)
    ds.with_dev(ds.deep, 2)
    controls = [("arm cancelled", ds.deep, 2), (f"mode {mode0} put back", ds.power_op, 1, mode0)]
    res = {"args": vars(a), "mode_before": mode0, "want_s": want}
    ok = False
    with hid.Device(path=devs[0]["path"]) as dev:
        sb.hold_relay(dev)
        guard = sb.deadline_guard(want + a.asleep_s + 900, lambda: ds.restore(dev, *controls))
        try:
            ds.with_dev(ds.power_op, 1, a.mode)
            ds.with_dev(ds.bench_sleep, 0)
            ds.with_dev(cm.force_stage, None)
            ds.with_dev(ds.light_off)
            sb.relay2("off")
            deadline = time.time() + 30
            while not sb.on_battery():
                if time.time() > deadline:
                    raise SystemExit("the board never reported the pack")
                time.sleep(1)
            time.sleep(3)
            s0 = ds.with_dev(snap)
            res["before"] = s0
            t_tap = time.time()
            sb.press(dev, 40, 200)
            ds.uhub("off")
            cm.say(f"mode {a.mode}: tapped and data off; waiting for the sleep (want {want} s)")
            t_sleep = None
            limit = time.time() + want + 120
            while time.time() < limit:
                cur = [c for t, c in ds.tail_ma(a.log, 3) if t > t_tap + 5]
                if len(cur) == 3 and all(c < 10.0 for c in cur):
                    t_sleep = time.time()
                    break
                time.sleep(1)
            if t_sleep is None:
                raise SystemExit(f"no sleep within {want + 120} s of the tap")
            ed0 = ds.edges(ds.tail_rows(a.log, kb=256), t_tap + 5, time.time())
            fall = ed0[0] if ed0 else None
            if fall is None:   # still asleep: the fall alone
                rows = [r for r in ds.tail_rows(a.log, kb=256) if r[0] > t_tap + 5]
                fall = next(((r0[0] + r1[0]) / 2 for r0, r1 in zip(rows, rows[1:]) if r0[1] >= 15.0 > r1[1]), None)
            res["sleep_after_tap_s"] = round(fall - t_tap, 1) if fall else None
            cm.say(f"asleep {res['sleep_after_tap_s']} s after the tap; waking in {a.asleep_s:.0f} s")
            time.sleep(a.asleep_s)
            t_wake_cmd = time.time()
            if a.charge_wake:
                sb.relay2("on")
                t_up = None
                while time.time() < t_wake_cmd + 20:
                    if any(c > 15.0 for t, c in ds.tail_ma(a.log, 3) if t > t_wake_cmd):
                        t_up = time.time()
                        break
                    time.sleep(0.5)
                res["charge_woke"] = t_up is not None
                cm.say("relay 2 on: " + ("it woke" if t_up else "no wake in 20 s (a full pack gives no CHRG edge); tapping"))
                if t_up is None:
                    sb.press(dev, 40, 200)
            else:
                sb.press(dev, 40, 200)
            time.sleep(3)
            ds.uhub("on")
            t_on = time.time()
            ds.need_bus(30)
            # The daemon's sync on enumeration first, with the raw HID left
            # to it: macOS opens it exclusively, and this script's reads would
            # make that sync fail (the soak's every sync did).
            res["clock"] = first_sync_after(t_on, 60)
            deadline = time.time() + 12
            while True:
                pg = ds.with_dev(ba.sleep_page)
                if not pg["open"] or time.time() > deadline:
                    break
                time.sleep(0.5)
            s1 = ds.with_dev(snap)
            rec = ds.with_dev(ds.deep)
            res.update(after=s1, record=rec)
            ed = ds.edges(ds.tail_rows(a.log, kb=1024 if a.asleep_s > 1800 else 256), t_tap + 5, time.time())
            res["asleep_s"] = round(ed[1] - ed[0], 1) if ed else None
            pg = s1["page"]
            res["pcf_s"], res["counted_s"] = pg["pcf_s"], pg["counted_s"]
            res["key_gain"] = (s1["presses"] - s0["presses"]) & 0xFFFF
            res["new_ge25"] = (s1["ge25"] - s0["ge25"]) & 0xFFFF
            res["no_reset"] = s1["uptime_ms"] > s0["uptime_ms"]
            res["link"] = {k: s1["link"][k] - s0["link"][k] for k in s0["link"]}
            woke_want = "charge" if a.charge_wake and res.get("charge_woke") else "key"
            keys_want = 2 if (not a.charge_wake or not res.get("charge_woke")) else 1   # the tap before, the waking tap
            checks = {
                "asleep at the mode's time": res["sleep_after_tap_s"] is not None and abs(res["sleep_after_tap_s"] - want) <= 15,
                f"woke on {woke_want}": pg["wake"] == woke_want,
                "the PCF within 2 s of the logger": res["asleep_s"] is not None and "pcf" in pg["valid"]
                                                     and abs(pg["pcf_s"] - res["asleep_s"]) <= 2.0,
                "the recovery closed, nothing timed out": not pg["open"] and not pg["timed_out"],
                f"{keys_want} keys (the tap{'s' if keys_want == 2 else ''})": res["key_gain"] == keys_want,
                "no reset": res["no_reset"],
                "no pass of 25 ms or more": res["new_ge25"] == 0,
            }
            res["checks"] = checks
            ok = all(checks.values())
            cm.say(f"asleep {res['asleep_s']} s by the logger, PCF {pg['pcf_s']}, counted {pg['counted_s']}; woke "
                   f"{pg['wake']}; keys {res['key_gain']}; clock " +
                   (f"{res['clock']['before_ms']:+.1f} ms at the first sync ({res['clock']['why']}, "
                    f"{res['clock']['at_s']} s after data on)" if res["clock"] else "no sync seen in 90 s"))
            for k, v in checks.items():
                cm.say(f"  {'ok  ' if v else 'FAIL'} {k}")
        except (Exception, SystemExit) as e:
            res["stopped"] = f"{type(e).__name__}: {e}"
            cm.say(f"stopped: {res['stopped']}")
        finally:
            done = ds.restore(dev, *controls)
            guard.cancel()
            cm.say("restored" if done else "⚠️ cleanup incomplete: see above")
            ok = ok and done
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "ladder-sleep.json"), "w") as f:
        json.dump(res, f, indent=1, default=str)
    print("ladder sleep:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
