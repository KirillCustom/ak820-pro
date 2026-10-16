#!/usr/bin/env python3
"""The per-load current map (plans/current-status.md, 10-15, item 1): step the
board's lighting and screen from the Mac while it runs on the pack, and read
each state's current from the power logger.

    venv/bin/python scripts/current_map.py --log <power.csv> [--hold 90] [--settle 15]
                                           [--out <dir>] [--dry-run]

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
    finally:
        h.close()
    say(f"start: lighting {jd_rgb}, screen {jd_lcd}")
    if a.dry_run:
        return
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "start-state.json"), "w") as f:
        json.dump({"lighting": jd_rgb, "screen": jd_lcd}, f, indent=2)

    was_on = relay_on()
    steps = []
    try:
        if was_on:
            run([PY, RELAY, "off", "2"])
            for _ in range(9):
                if supply() == "battery":
                    break
                time.sleep(5)
            else:
                raise SystemExit("relay 2 off, but the board does not read battery")
        say("on battery")
        plan = STATES + STATES[::-1]
        for i, (name, rgb, screen) in enumerate(plan):
            h = ak820text.open_device()
            try:
                set_rgb(h, dict(jd_rgb) if rgb is None else rgb)
                st = set_lcd(h, screen, jd_lcd)
            finally:
                h.close()
            t0 = time.time()
            say(f"[{i + 1}/{len(plan)}] {name}: screen {st}")
            time.sleep(a.hold)
            t1 = time.time()
            steps.append({"i": i + 1, "name": name, "t0": t0, "t1": t1,
                          "lcd_effective": st["effective"], "lcd_on": st["on"]})
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
                back = (read_rgb(h), lcd(h, 6))
            finally:
                h.close()
            say(f"restored: lighting {back[0]}, screen {back[1]}")
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
            f"vcell {m['vcell_mv']:.0f}" if m else "no data"))
    lines.append("")
    lines.append("state       mean of both passes (charge register)")
    for name, _, _ in STATES:
        ms = [m for m in by.get(name, []) if m]
        if ms:
            passes = ", ".join("%.2f" % m["q_ma"] for m in ms)
            lines.append(f"{name:10s} {sum(m['q_ma'] for m in ms) / len(ms):7.2f} mA  (passes: {passes})")
    text = "\n".join(lines)
    print(text)
    with open(os.path.join(out, "summary.txt"), "w") as f:
        f.write(text + "\n")


if __name__ == "__main__":
    main()
