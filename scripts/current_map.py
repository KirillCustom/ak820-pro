#!/usr/bin/env python3
"""The per-load current map (plans/current-status.md, 10-15, item 1): step the
board's lighting and screen from the Mac while it runs on the pack, and read
each state's current from the power logger.

    venv/bin/python scripts/current_map.py --log <power.csv> [--hold 90] [--settle 15]
                                           [--set loads|rgb-curve] [--out <dir>] [--dry-run]

What it does, in order:
- reads JD's lighting (VIA's channel 3) and the screen (HC_DEVHOOK op 6, flash
  2f or later) and saves both beside the output;
- turns relay 2 off and waits for the board's `supply battery`, so the pack's
  current is the board's (on USB the charger would be in it);
- runs STATES forward and then backward, holding each --hold seconds, so a
  slow drift (the pack falling) cancels in each state's mean;
- in a `finally`: puts the lighting and the screen back and the relay back on
  if it was on.

Each state's current is the INA228's charge register across the hold, less
the first --settle seconds: (charge_end - charge_start) / time, gap-free, not
the mean of the 1 s samples (both are printed). The power logger
(`power_log.py`) must already be recording into --log.

The lighting goes over VIA's custom-value channel without its SAVE: VIA's
set paths are all _noeeprom (quantum/via.c), so nothing reaches flash. The
screen goes over HC_DEVHOOK ops 4/5, which never persist either.
Writes go out only between holds, and the HID handle is closed during each
hold, so the daemon keeps its now-playing and clock traffic.

--set picks the states: `loads` (R1's ten, the default), `rgb-curve` (white
from 16 to 255 and red, green and blue alone, for the gauge's RGB model in
buffer units: QMK's CIE curve makes values 64 and 128 drives of 12 and 48), or
`stages` (Phase 2's ladder stages forced, at JD's lighting, a purple and an
animated effect, and JD's lighting in Power saver while typing, at its 128
ceiling, each beside the board's own modelled draw from HC_POWER). Each state
also records the board's own LED drive (`led drive`, per mille of the maximum)
and the pack power (mA x vcell).

Above 15% only: it refuses at a level of 150 per mille or below, where the
low cap's ceiling (Power saver's 128 while typing, in every mode) would hold
every state's lighting under it, a held stage or not
(plans/POWER-MODES-REVISION-PLAN.md).
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
sys.path.insert(0, os.path.join(ROOT, "hostagent"))
import ak820text  # noqa: E402  (the safe opener: cached path, no enumeration of other devices)

PY = os.path.join(ROOT, "venv", "bin", "python3")
RELAY = os.path.join(ROOT, "scripts", "usb_relay.py")
BATT = os.path.join(ROOT, "hostagent", "ak820battery.py")

GET_VALUE, SET_VALUE, RGB_CH = 0x08, 0x07, 3
POWER_FULL, POWER_SAVER = 0, 2   # power.c's POWER_MODE_FULL, POWER_MODE_SAVER
LOW_CAP_PM = 150                 # power.c's LOW_CAP_PM: the low cap at or below it
BRIGHTNESS, EFFECT, SPEED, COLOR = 1, 2, 3, 4
HEALTH_CH, HC_DEVHOOK = 0x13, 0x76

# (name, lighting or None for JD's own, screen level or "off" or None for JD's own)
# RGB states with the screen as JD has it; screen states with the RGB off, so
# each family isolates one load. White is effect 1 (solid colour), sat 0.
STATES = [
    ("jd",           None,                 None),
    ("rgb off",      {"effect": 0},        None),
    ("white 255",    {"effect": 1, "h": 0, "s": 0, "v": 255}, None),
    ("white 128",    {"effect": 1, "h": 0, "s": 0, "v": 128}, None),
    ("white 64",     {"effect": 1, "h": 0, "s": 0, "v": 64},  None),
    ("lcd max",      {"effect": 0},        "max"),
    ("lcd 12",       {"effect": 0},        12),
    ("lcd 4",        {"effect": 0},        4),
    ("lcd 1",        {"effect": 0},        1),
    ("lcd off",      {"effect": 0},        "off"),
]

def _white(v):
    return (f"white {v}", {"effect": 1, "h": 0, "s": 0, "v": v}, None)


# The low end too (is a dim green cheaper than a dim red?): white
# at value 3 (drive 1 of 255 after QMK's CIE curve) for the fixed cost of any
# LED lit, and each colour alone at value 16 (drive 2).
RGB_CURVE = ([("jd", None, None), ("rgb off", {"effect": 0}, None)]
             + [_white(v) for v in (255, 254, 252, 248, 240, 224, 192, 160, 128, 96, 64, 48, 32, 16, 3)]
             + [(f"{name} {v}", {"effect": 1, "h": h, "s": 255, "v": v}, None)
                for v in (255, 128, 16) for name, h in (("red", 0), ("green", 85), ("blue", 170))])
# Phase 2: the ladder's stages forced (HC_DEVHOOK op 14), at JD's lighting and
# at a coloured and an animated effect, each against the board's own modelled
# draw (HC_POWER), which the summary sets beside the logger's. A 4th element
# is the stage to force (0 active .. 3 screen off), a 5th the power mode to
# hold it in (Always on otherwise). Effect 3 is Cycle All (animated); effect 1
# at hue 191 is a saturated purple. "jd saver" is JD's lighting typing in
# Power saver, at its 128 ceiling: ~161 mW with the bench's USB data (~154
# untethered) at his white and screen level.
PURPLE = {"effect": 1, "h": 191, "s": 255, "v": 255}
CYCLE = {"effect": 3, "h": 0, "s": 255, "v": 255}
STAGES = ([(f"{who} {stg}", rgb, None, k)
           for who, rgb in (("jd", None), ("purple", PURPLE), ("cycle", CYCLE))
           for k, stg in enumerate(("active", "dim", "off", "dark"))]
          + [("jd saver", None, None, 0, POWER_SAVER)])
SETS = {"loads": STATES, "rgb-curve": RGB_CURVE, "stages": STAGES}


def stamp():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def say(msg):
    print(f"{stamp()} {msg}", flush=True)


def xfer(h, payload, overall_s=5.0):
    """One round trip, the reply matched on its first three bytes (another
    process's traffic is discarded, as in ak820lighting.py)."""
    want = tuple(payload[:3])
    h.write(bytes([0x00] + payload + [0x00] * (32 - len(payload))))
    deadline = time.monotonic() + overall_s
    while time.monotonic() < deadline:
        r = h.read(32, 500)
        if r and tuple(r[:3]) == want:
            return bytes(r)
        if r and r[1:3] == bytes(payload[1:3]) and r[0] == 0xFF:
            raise SystemExit(f"unhandled: {bytes(payload[:5]).hex()} (flash 2f or later?)")
    raise SystemExit(f"no reply to {bytes(payload[:3]).hex()}")


def read_rgb(h):
    c = xfer(h, [GET_VALUE, RGB_CH, COLOR])
    return {"effect": xfer(h, [GET_VALUE, RGB_CH, EFFECT])[3], "h": c[3], "s": c[4],
            "v": xfer(h, [GET_VALUE, RGB_CH, BRIGHTNESS])[3],
            "speed": xfer(h, [GET_VALUE, RGB_CH, SPEED])[3]}


def set_rgb(h, cfg):
    xfer(h, [SET_VALUE, RGB_CH, EFFECT, cfg["effect"]])
    if "v" in cfg:
        xfer(h, [SET_VALUE, RGB_CH, BRIGHTNESS, cfg["v"]])
    if "h" in cfg:
        xfer(h, [SET_VALUE, RGB_CH, COLOR, cfg["h"], cfg["s"]])
    if "speed" in cfg:
        xfer(h, [SET_VALUE, RGB_CH, SPEED, cfg["speed"]])


def lcd(h, op, arg=0):
    r = xfer(h, [SET_VALUE, HEALTH_CH, HC_DEVHOOK, op, arg])
    if len(r) < 9 or r[3] != op:   # codex, flash 2f: check the echoed op too
        raise SystemExit(f"a reply to another devhook op: {r[:9].hex()}")
    level, top, on, eff, saved = r[4:9]
    return {"level": level, "max": top, "on": bool(on), "effective": eff,
            "saved": saved - 1 if saved else None}


def force_stage(h, stage):
    """Hold the ladder at `stage` (0..3), or release it (None): op 14 sub 2."""
    xfer(h, [SET_VALUE, HEALTH_CH, HC_DEVHOOK, 14, 2, 0xFF if stage is None else stage] + [0] * 26)


def model_mw(h):
    """The board's modelled draw now (HC_POWER), mW, or None without it."""
    try:
        r = xfer(h, [SET_VALUE, HEALTH_CH, 0x0F])
    except SystemExit:
        return None
    return (r[9] | r[10] << 8 | r[11] << 16 | r[12] << 24) / 1000.0


def power_mode(h, mode=None):
    """The ladder's mode over HC_DEVHOOK op 14 (Phase 2): set it if `mode` is
    given; returns the mode now, or None from firmware without the op (no
    ladder: nothing can dim the lights mid-hold)."""
    try:
        r = xfer(h, [SET_VALUE, HEALTH_CH, HC_DEVHOOK, 14, 0 if mode is None else 1, mode or 0] + [0] * 26)
    except SystemExit:   # xfer's "unhandled": no op 14, so no ladder
        return None
    return r[4]


def power_state(h):
    """Op 14's read (sub 0): the mode, the stage, the value ceiling applied,
    the stage held (0xFF none) and whether the board is on the pack. A set
    (sub 1 or 2) takes effect at power.c's next 10 Hz tick, so read this after
    one, not in the set's own reply."""
    r = xfer(h, [SET_VALUE, HEALTH_CH, HC_DEVHOOK, 14, 0, 0] + [0] * 26)
    return {"mode": r[4], "stage": r[5], "val_cap": r[6], "forced": r[7], "on_battery": bool(r[8])}


def set_lcd(h, want, jd):
    if want is None:
        lcd(h, 4, jd["level"])
        return lcd(h, 5, 1 if jd["on"] else 0)
    if want == "off":
        return lcd(h, 5, 0)
    lcd(h, 5, 1)
    return lcd(h, 4, jd["max"] if want == "max" else want)


def run(args):
    rc = subprocess.run(args, capture_output=True, text=True, cwd=ROOT)
    return rc.returncode, rc.stdout + rc.stderr


def supply():
    rc, out = run([PY, BATT])
    m = re.search(r"^supply\s+(\S+)", out, re.M)
    return m.group(1) if m else None


def led_drive():
    """The board's LED drive over the last second, per mille (None if unread)."""
    rc, out = run([PY, BATT])
    m = re.search(r"^led drive\s+([0-9.]+) %", out, re.M)
    return round(float(m.group(1)) * 10) if m else None


def level_pm():
    """The gauge's level, per mille (None if unknown or unread)."""
    rc, out = run([PY, BATT])
    m = re.search(r"^level\s+([0-9.]+) %", out, re.M)
    return round(float(m.group(1)) * 10) if m else None


def relay_on():
    rc, out = run([PY, RELAY, "status"])
    return "relay 2 ON" in out


def log_rows(path, t0, t1):
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            if t0 <= float(r["epoch"]) <= t1 and r["charge_mah"] != "":
                rows.append(r)
    return rows


def measure(path, t0, t1):
    rows = log_rows(path, t0, t1)
    if len(rows) < 10:
        return None
    ms = [int(r["qt_ms"]) for r in rows]
    if any(b < a for a, b in zip(ms, ms[1:])):
        return None   # the QT Py restarted, so the register did too
    hours = (ms[-1] - ms[0]) / 3.6e6
    q = (float(rows[-1]["charge_mah"]) - float(rows[0]["charge_mah"])) / hours
    cur = [float(r["current_ma"]) for r in rows]
    mean = sum(cur) / len(cur)
    sd = (sum((c - mean) ** 2 for c in cur) / (len(cur) - 1)) ** 0.5
    vc = [float(r["vcell_mv"]) for r in rows if r["vcell_mv"]]
    return {"n": len(rows), "q_ma": q, "mean_ma": mean, "sd_ma": sd,
            "vcell_mv": sum(vc) / len(vc) if vc else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True, help="the power logger's CSV, being recorded now")
    ap.add_argument("--hold", type=int, default=90)
    ap.add_argument("--settle", type=int, default=15)
    ap.add_argument("--out", default=None, help="directory for steps.csv and the summary")
    ap.add_argument("--set", choices=sorted(SETS), default="loads", help="which states")
    ap.add_argument("--dry-run", action="store_true", help="read and print the start state only")
    a = ap.parse_args()
    log = os.path.abspath(a.log)
    out = os.path.abspath(a.out or os.path.join(os.path.dirname(log), "current-map-" + time.strftime("%Y%m%d-%H%M")))

    with open(log, newline="") as f:
        last = list(csv.DictReader(f))[-1]
    if time.time() - float(last["epoch"]) > 5:
        sys.exit(f"{log} is not being recorded (last row {last['time']})")

    h = ak820text.open_device()
    try:
        jd_rgb, jd_lcd = read_rgb(h), lcd(h, 6)
        jd_mode = power_mode(h)
    finally:
        h.close()
    say(f"start: lighting {jd_rgb}, screen {jd_lcd}, power mode {jd_mode}")
    if a.dry_run:
        return
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "start-state.json"), "w") as f:
        json.dump({"lighting": jd_rgb, "screen": jd_lcd}, f, indent=2)

    was_on = relay_on()
    steps = []
    try:
        if jd_mode is not None:
            # The ladder would dim an idle board's lights mid-hold: Full Power
            # for the map, JD's mode back in the finally.
            h = ak820text.open_device()
            try:
                say(f"power mode Full for the map: {power_mode(h, POWER_FULL)}")
            finally:
                h.close()
        if was_on:
            run([PY, RELAY, "off", "2"])
            for _ in range(9):
                if supply() == "battery":
                    break
                time.sleep(5)
            else:
                raise SystemExit("relay 2 off, but the board does not read battery")
        say("on battery")
        lv = level_pm()
        if jd_mode is not None and lv is not None and lv <= LOW_CAP_PM:
            raise SystemExit(f"the level is {lv / 10:.1f}%: at 15% or below the low cap holds every "
                             "state under Power saver's ceiling; charge first")
        states = SETS[a.set]
        plan = states + states[::-1]
        for i, state in enumerate(plan):
            name, rgb, screen = state[:3]
            stage = state[3] if len(state) > 3 else None
            mode = state[4] if len(state) > 4 else POWER_FULL
            h = ak820text.open_device()
            try:
                set_rgb(h, dict(jd_rgb) if rgb is None else rgb)
                st = set_lcd(h, screen, jd_lcd)
                if jd_mode is not None:
                    # Every state's stage held, in its mode: the one it names,
                    # or active, so the ladder does not move a load mid-hold.
                    # Holding the stage keeps the low cap's stages out, but not
                    # its ceiling (Saver's 128 while typing), hence the refusal
                    # at 15% above.
                    power_mode(h, mode)
                    force_stage(h, 0 if stage is None else stage)
            finally:
                h.close()
            t0 = time.time()
            time.sleep(min(a.settle, a.hold))
            drive = led_drive()
            say(f"[{i + 1}/{len(plan)}] {name}: screen {st}, led drive {drive}")
            time.sleep(max(0, a.hold - a.settle - 3))
            h = ak820text.open_device()
            try:
                model = model_mw(h)   # the last full second's model, inside the hold
                st = lcd(h, 6)        # the screen as the stage left it, settled (codex)
            finally:
                h.close()
            time.sleep(min(3, a.hold))
            t1 = time.time()
            steps.append({"i": i + 1, "name": name, "t0": t0, "t1": t1,
                          "lcd_effective": st["effective"], "lcd_on": st["on"], "led_pm": drive,
                          "model_mw": model})
            with open(os.path.join(out, "steps.csv"), "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(steps[0]))
                w.writeheader()
                w.writerows(steps)
    finally:
        try:
            h = ak820text.open_device()
            try:
                set_rgb(h, dict(jd_rgb))
                set_lcd(h, None, jd_lcd)
                if jd_mode is not None:
                    force_stage(h, None)
                    power_mode(h, jd_mode)
                back = (read_rgb(h), lcd(h, 6), power_mode(h))
            finally:
                h.close()
            say(f"restored: lighting {back[0]}, screen {back[1]}, power mode {back[2]}")
        finally:
            if was_on:
                run([PY, RELAY, "on", "2"])
                say(f"relay 2 on; supply {supply()}")

    time.sleep(3)   # the last hold's rows
    by = {}
    lines = []
    for s in steps:
        m = measure(log, s["t0"] + a.settle, s["t1"])
        by.setdefault(s["name"], []).append(m)
        lines.append(f"{s['i']:2d} {s['name']:10s} " + (
            f"{m['q_ma']:7.2f} mA (samples {m['mean_ma']:7.2f}, sd {m['sd_ma']:.2f}) "
            f"vcell {m['vcell_mv']:.0f}, {m['q_ma'] * m['vcell_mv'] / 1000:6.1f} mW, "
            f"led {s['led_pm']}" + (f", model {s['model_mw']:.1f} mW "
                                    f"({(s['model_mw'] / (m['q_ma'] * m['vcell_mv'] / 1000) - 1) * 100:+.1f}%)"
                                    if s.get("model_mw") else "") if m else "no data"))
    lines.append("")
    lines.append("state       mean of both passes (charge register), and pack power")
    for st in SETS[a.set]:
        name = st[0]
        ms = [m for m in by.get(name, []) if m]
        if ms:
            passes = ", ".join("%.2f" % m["q_ma"] for m in ms)
            mw = sum(m["q_ma"] * m["vcell_mv"] / 1000 for m in ms) / len(ms)
            lines.append(f"{name:10s} {sum(m['q_ma'] for m in ms) / len(ms):7.2f} mA  {mw:6.1f} mW  (passes: {passes})")
    text = "\n".join(lines)
    print(text)
    with open(os.path.join(out, "summary.txt"), "w") as f:
        f.write(text + "\n")


if __name__ == "__main__":
    main()
