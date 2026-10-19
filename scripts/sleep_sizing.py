#!/usr/bin/env python3
"""Light sleep's sizing run (plans/SLEEP-PLAN.md): what each step of the
dev build's light-sleep mask saves, and what it costs in keypresses,
hands-free.

    venv/bin/python scripts/sleep_sizing.py [--masks 0,1,3:2,3:4,7:2,15:2,0] [--n 200] [--out DIR]

Each entry is MASK[:YIELD_MS] (sleep.h's bits: 1 scan-only ISR, 2 yield,
4 WFI with PMU_CTRL 0, 8 the 20 kHz tick stopped; bit 16, PMU Sleep, cuts
USB and is not run here). With relay 2 off (the pack, USB data up) and the
stage forced to SCREEN_OFF, per entry: set the mask (op 14 sub 3), settle
20 s, reset the health counters (so rowgap and the stall counts are this
window's), the logger's current over 60 s (the charge register), the ISR
rate (health page 4) and WFI count over that window, then 200 presses at
40/60 ms and 200 at 25/40 ms through the wake rig, counted at the board.
The mask actually in force is read back: a step that never engaged cannot
pass. Restored in a `finally`: mask 0, the stage, relay 1 open, relay 2 on.

Pass per entry: the requested bits in force (the mask read back, scan-only
and a WFI rate as asked); every press counted (presses = n, raw edges = 2n)
and none in the quiet window; no stall >= 25 ms in the window or the
presses; rowgap <= 6 ms steady (the entry's transition is recorded, not
gated); no link give-ups, full queues or UART errors (ACK timeouts are
reported); the board still answering. Each mask ends by itself after 240 s
if nothing sets it again. ⚠️ Hands off the keyboard (key_presses counts every key); the
relay is loud; the Mac may be locked.
"""
import argparse, json, os, sys, time

import hid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sleep_baseline as sb  # noqa: E402  (relay1, relay2, on_battery, stage, counts, press, delta, cm, health)

cm, health = sb.cm, sb.health
SETTLE_S, WINDOW_S = 20, 60


def light(h, mask, yield_ms=2, pmu_s=0, ttl_s=0):
    """op 14 sub 3: set the mask (it ends by itself after ttl_s, 0 = 240 s);
    the reply's mask, bits in force, WFI count, scan-only, the yield in effect."""
    r = cm.xfer(h, [cm.SET_VALUE, cm.HEALTH_CH, cm.HC_DEVHOOK, 14, 3, mask, yield_ms, pmu_s, ttl_s] + [0] * 23)
    if len(r) < 13 or r[3] != 14 or r[4] != 3:
        raise SystemExit(f"a reply to another op: {r[:13].hex()}")
    return {"mask": r[5], "applied": r[6], "wfi": int.from_bytes(r[7:11], "little"), "scan_only": bool(r[11]),
            "yield_ms": r[12]}


def with_dev(fn, *args):
    h = cm.ak820text.open_device()
    try:
        return fn(h, *args)
    finally:
        h.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--masks", default="0,1,3:2,3:4,7:2,15:2,0")
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--log", default=os.path.join(HERE, "..", "history", "power-bench-2026-10-15", "power.csv"))
    ap.add_argument("--out", default=os.path.join(HERE, "..", "history", "sleep-sizing-" + time.strftime("%Y%m%d-%H%M")))
    a = ap.parse_args()
    entries = []
    for e in a.masks.split(","):
        m, _, y = e.partition(":")
        mask, y = int(m, 0), (int(y) if y else 2)
        if mask & ~0x0F or not 1 <= y <= 4:
            sys.exit(f"bad entry {e}: mask 0-15 (bit 16, PMU Sleep, cuts USB), yield 1-4 ms")
        entries.append((mask, y))
    if not cm.relay_on():
        sys.exit("relay 2 is off: start from the charging state")
    if with_dev(light, 0)["mask"] != 0:
        sys.exit("op 14 sub 3 did not answer: flash the sleep build")
    devs = hid.enumerate(sb.VID, sb.PID)
    if len(devs) != 1:
        sys.exit(f"expected one USB relay, found {len(devs)}")
    rows, ok = [], True
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
            for mask, y in entries:
                # The entry's own transition: counters reset BEFORE the mask
                # changes, read after the settle (codex: it can reach ~8 ms).
                with_dev(lambda h: health.reset_counters(h))
                with_dev(light, mask, y)
                time.sleep(SETTLE_S)
                gap_transition = health.read_rows()["row_gap_max_ms"]
                # The steady window, then the presses, on fresh counters:
                # the stall count covers both.
                with_dev(lambda h: health.reset_counters(h))
                l0, c0 = sb.link(), sb.counts()
                i0, w0 = health.read_isr(), with_dev(light, mask, y)["wfi"]
                t0 = time.time()
                time.sleep(WINDOW_S)
                t1 = time.time()
                i1, st = health.read_isr(), with_dev(light, mask, y)
                isr = health.isr_rates(i0, i1, t1 - t0)
                m = cm.measure(a.log, t0, t1) if os.path.exists(a.log) else None
                cw = sb.counts()
                for held, gap in sb.RATES:
                    for _ in range(a.n):
                        sb.press(dev, held, gap)
                time.sleep(0.15)
                c1, l1 = sb.counts(), sb.link()
                d, dw = sb.delta(cw, c1), sb.delta(c0, cw)
                dl = {k: l1[k] - l0[k] for k in l1}
                rw = health.read_rows()
                n = a.n * len(sb.RATES)
                wfi_s = (st["wfi"] - w0) / (t1 - t0)
                engaged = (st["applied"] == mask and st["scan_only"] == bool(mask & 1)
                           and (wfi_s > 0) == bool(mask & 4) and (st["yield_ms"] == y or not mask & 2))
                link_ok = (dl["giveups"] == 0 and dl["queue_full"] == 0 and dl["uart_overrun"] == 0
                           and dl["uart_framing"] == 0 and dl["uart_parity"] == 0)
                good = (engaged and link_ok and dw["presses"] == 0 and d["presses"] == n and d["edges"] == 2 * n
                        and c1["stalls"] == c0["stalls"] and rw["row_gap_max_ms"] <= 6)
                ok &= good
                row = {"mask": mask, "yield_ms": y, "applied": st["applied"], "scan_only": st["scan_only"],
                       "wfi_per_s": wfi_s, "isr": isr, "logger": m, "link": dl,
                       "presses": d["presses"], "edges": d["edges"], "stalls": (c1["stalls"] - c0["stalls"]) & 0xFFFF,
                       "window_presses": dw["presses"], "rowgap_ms": rw["row_gap_max_ms"],
                       "rowgap_transition_ms": gap_transition, "engaged": engaged, "n": n, "ok": good}
                rows.append(row)
                cm.say(f"mask {mask:2d} yield {y}: applied {st['applied']:2d}, "
                       + (f"{m['q_ma']:.2f} mA, " if m else "no logger, ")
                       + f"wfi {wfi_s:.0f}/s, presses {d['presses']}/{n}, edges {d['edges']}/{2 * n}, "
                       f"stalls {row['stalls']}, rowgap {rw['row_gap_max_ms']} ms (entry {gap_transition}), "
                       f"ack timeouts {dl['timeouts']}, giveups {dl['giveups']}  {'ok' if good else 'FAIL'}")
        finally:
            sb.attempt("relay 1 open", sb.relay1, dev, False)
            sb.attempt("mask 0", with_dev, light, 0)   # if this fails, the mask ends by itself in 240 s
            sb.attempt("stage released", sb.stage, None)
            if sb.attempt("relay 2 on", sb.relay2, "on"):
                cm.say("restored: mask 0, stage released, relay 2 on")
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "sleep-sizing.json"), "w") as f:
        json.dump({"rows": rows, "at": time.strftime("%Y-%m-%d %H:%M:%S")}, f, indent=1, default=str)
    print("sleep sizing:", "pass" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
