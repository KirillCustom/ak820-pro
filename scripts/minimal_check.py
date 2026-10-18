#!/usr/bin/env python3
"""Minimal mode's wake on the board (plans/PHASE2-BUILD-PLAN.md, 5b): on
battery the backlight is dark, and lights for 4 s when a message is first
painted in the band. The simulator cannot run display.c, so this is the
check of its paint-time wake, through the one source the bench can make
without hands: host text.

    venv/bin/python scripts/minimal_check.py [--log history/.../power.csv] [--out DIR]

Relay 2 off (the board on its pack, USB data still up), Minimal over
HC_DEVHOOK op 14, then: dark; new host text; the same text again; new text
again. HC_POWER's modelled power is read twice a second (the board updates
it once a second), and the power logger's current is averaged per phase if
it is running. Pass: the model is at its dark level before, rises for each
new text and is back within 6 s, and an identical push changes nothing.

One handle is held open throughout: on macOS that keeps the now-playing
daemon from pushing its own text mid-check, which would be a real wake. The
mode, the relay and the band (cleared; the daemon re-pushes) are restored in
a `finally`. Needs a dev build with op 14 and HC_POWER, and the relay on at
the start. The keyboard's lights go dark for about a minute; typing works.
"""
import argparse, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import current_map as cm  # noqa: E402  (xfer, power_mode, relay_on, log_rows, ak820text)

HC_POWER = 0x0F
MINIMAL = 3          # power.c's POWER_MODE_MINIMAL
WAKE_S = 4.0         # power.c's MESSAGE_WAKE_MS
RISE_MW = 2.0        # a lift smaller than this is noise, not the backlight
PHASE_S = 9.0


def power(h):
    r = cm.xfer(h, [cm.SET_VALUE, cm.HEALTH_CH, HC_POWER])
    return {"t": time.time(), "mode": r[4], "stage": r[5],
            "mw": (r[9] | r[10] << 8 | r[11] << 16 | r[12] << 24) / 1000.0, "battery": bool(r[29])}


def text(h, line0, line1):
    for line, s in ((0, line0), (1, line1)):
        payload = cm.ak820text._line_packet(line, s)
        h.write(bytes([0x00] + payload + [0x00] * (32 - len(payload))))


def sample(h, secs, phase, rows):
    end = time.time() + secs
    while time.time() < end:
        p = power(h)
        p["phase"] = phase
        rows.append(p)
        time.sleep(0.5)


def relay(state):
    rc, out = cm.run([cm.PY, cm.RELAY, state, "2"])
    if rc:
        raise SystemExit(f"relay {state} 2 failed: {out.strip()}")


def lifted(rows, phase, t_push, dark):
    """Seconds after the push that the model sat above dark by RISE_MW."""
    up = [p["t"] - t_push for p in rows if p["phase"] == phase and p["mw"] - dark >= RISE_MW]
    return (min(up), max(up)) if up else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=os.path.join(HERE, "..", "history", "power-bench-2026-10-15", "power.csv"))
    ap.add_argument("--out", default=os.path.join(HERE, "..", "history", "phase2-minimal-check-" + time.strftime("%Y%m%d-%H%M")))
    a = ap.parse_args()
    if not cm.relay_on():
        sys.exit("relay 2 is off: start from the charging state")
    rows, pushes, jd_mode = [], {}, None
    h = cm.ak820text.open_device()
    try:
        jd_mode = cm.power_mode(h)
        if jd_mode is None:
            sys.exit("no HC_DEVHOOK op 14 on this firmware")
        cm.power_mode(h, MINIMAL)
        cm.say(f"mode {jd_mode} -> Minimal; relay 2 off")
        relay("off")
        deadline = time.time() + 30
        while not power(h)["battery"]:
            if time.time() > deadline:
                raise SystemExit("the board never reported the pack")
            time.sleep(1)
        time.sleep(6)   # past any wake the supply change itself caused
        sample(h, PHASE_S, "dark", rows)
        tag = time.strftime("%H:%M:%S")
        for phase, l0, l1 in (("new text", "Minimal check", tag),
                              ("same text", "Minimal check", tag),
                              ("new text 2", "Minimal check 2", tag)):
            pushes[phase] = time.time()
            text(h, l0, l1)
            sample(h, PHASE_S, phase, rows)
    finally:
        try:
            if jd_mode is not None:
                cm.power_mode(h, jd_mode)
            h.write(bytes([0x00, cm.ak820text.SET_VALUE, cm.ak820text.TEXT_CHANNEL, cm.ak820text.TEXT_CLEAR] + [0] * 29))
        finally:
            h.close()
            relay("on")
            cm.say(f"restored: relay 2 on, mode {jd_mode}")

    dark_rows = [p["mw"] for p in rows if p["phase"] == "dark"]
    dark = sorted(dark_rows)[len(dark_rows) // 2]
    ok = all(p["mode"] == MINIMAL and p["battery"] for p in rows)
    ok &= max(dark_rows) - dark < RISE_MW
    print(f"dark: model {dark:.1f} mW (range {min(dark_rows):.1f}-{max(dark_rows):.1f})")
    for phase in ("new text", "same text", "new text 2"):
        span = lifted(rows, phase, pushes[phase], dark)
        peak = max(p["mw"] for p in rows if p["phase"] == phase)
        if phase == "same text":
            good = span is None
        else:
            good = span is not None and span[0] <= 1.5 and span[1] <= WAKE_S + 2.0
        ok &= good
        where = f"lifted {span[0]:.1f}-{span[1]:.1f} s after the push" if span else "no lift"
        print(f"{phase:10}: peak {peak:.1f} mW (+{peak - dark:.1f}), {where}  {'ok' if good else 'FAIL'}")
    logger = {}
    if os.path.exists(a.log):
        for phase in ("dark", "new text", "same text", "new text 2"):
            ts = [p["t"] for p in rows if p["phase"] == phase]
            t0 = pushes.get(phase, ts[0])
            # the lit window for the new texts, the whole phase otherwise
            t1 = t0 + WAKE_S - 0.5 if phase.startswith("new") else ts[-1]
            r = cm.log_rows(a.log, t0 + (0.5 if phase.startswith("new") else 0), t1)
            if r:
                logger[phase] = sum(float(x["current_ma"]) for x in r) / len(r)
        if logger:
            print("logger (pack current): " + ", ".join(f"{k} {v:.2f} mA" for k, v in logger.items()))
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "minimal-check.json"), "w") as f:
        json.dump({"rows": rows, "pushes": pushes, "dark_mw": dark, "logger_ma": logger,
                   "at": time.strftime("%Y-%m-%d %H:%M:%S")}, f, indent=1)
    print("minimal check:", "pass" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
