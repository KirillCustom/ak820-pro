#!/usr/bin/env python3
"""Record the bench power logger (scripts/qtpy/code.py on the QT Py RP2040,
docs/test-bench.md): the INA228 in series on the AK820 pack's + lead and the
MAX17048 across the pack, one row per second, timestamped by the Mac.

    venv/bin/python scripts/power_log.py --out <csv> [--detach] [--serial UID]

Columns: time, epoch, qt_ms (the QT Py's clock), vbus_mv (the pack, at the
INA228), vshunt_uv, current_ma (+ out of the pack, - into it), charge_mah (the
INA228's own count, gap-free between rows; it restarts at 0 only when the QT
Py loses power), vcell_mv, soc_pct and crate_pct_h (the MAX17048; empty until
a pack powers it).

The INA228 reads -0.064 mA (-0.97 uV) with the harness on and nothing
flowing (10-14); subtract it when the floor matters. The shunt is taken as
15 mohm (marked R015; Adafruit's driver default), unchecked against a known
load yet.

Serial only, never HID: the QT Py's boot.py turns its HID off. The port is
found by USB ids, so it survives a replug onto another port. With --detach
it forks off (PPID 1), like trial_guard.py; its pid goes in <csv>.pid and
its notes (start, H and E lines, reconnects) in <csv>.log.
"""
import argparse
import csv
import os
import sys
import time

import serial
from serial.tools import list_ports

ADAFRUIT = 0x239A
FIELDS = ["time", "epoch", "qt_ms", "vbus_mv", "vshunt_uv", "current_ma", "charge_mah",
          "vcell_mv", "soc_pct", "crate_pct_h"]
CURRENT_LSB = 5e-6   # must match code.py; checked against its H line


def say(msg):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def find_port(uid):
    for p in list_ports.comports():
        if p.vid == ADAFRUIT and (uid is None or p.serial_number == uid):
            return p.device
    return None


def conv(f):
    """A D line's fields (ms, vbus, vshunt, current, charge, vcell, soc, crate)."""
    def scale(s, k, nd):
        return f"{int(s) * k:.{nd}f}" if s != "" else ""
    return [f[0], scale(f[1], 195.3125e-3, 3), scale(f[2], 78.125e-3, 3),
            scale(f[3], CURRENT_LSB * 1e3, 3), scale(f[4], CURRENT_LSB / 3.6, 6),
            scale(f[5], 78.125e-3, 3), scale(f[6], 1 / 256, 3), scale(f[7], 0.208, 3)]


def detach(note_path):
    if os.fork():
        time.sleep(0.3)
        os._exit(0)
    os.setsid()
    if os.fork():
        os._exit(0)
    log = open(note_path, "a", buffering=1)
    os.dup2(log.fileno(), 1)
    os.dup2(log.fileno(), 2)
    os.dup2(os.open(os.devnull, os.O_RDONLY), 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--serial", default=None, help="the QT Py's USB serial number (default: the first Adafruit board)")
    ap.add_argument("--detach", action="store_true")
    a = ap.parse_args()
    out = os.path.abspath(a.out)
    base = out[:-4] if out.endswith(".csv") else out
    if a.detach:
        print(f"detaching; pid in {base}.pid, notes in {base}.log", flush=True)
        detach(base + ".log")
    with open(base + ".pid", "w") as f:
        f.write(f"{os.getpid()}\n")
    new = not os.path.exists(out)
    f = open(out, "a", newline="", buffering=1)
    w = csv.writer(f)
    if new:
        w.writerow(FIELDS)
    say(f"recording to {out}")

    while True:
        dev = find_port(a.serial)
        if not dev:
            time.sleep(2)
            continue
        try:
            s = serial.Serial(dev, 115200, timeout=5)
            say(f"connected {dev}")
            while True:
                line = s.readline().decode(errors="replace").strip()
                if not line:
                    continue
                kind, _, rest = line.partition(",")
                if kind == "D":
                    t = time.time()
                    fields = rest.split(",")
                    if len(fields) == 8:
                        w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)),
                                    f"{t:.2f}"] + conv(fields))
                elif kind == "H":
                    say(f"header: {rest}")
                    if f"current {CURRENT_LSB:g}A" not in rest:
                        say("current LSB differs from code.py's: the conversions are wrong; stopping")
                        sys.exit(1)
                elif kind == "E":
                    say(f"logger error: {rest}")
        except (serial.SerialException, OSError) as e:
            say(f"disconnected: {e}")
            time.sleep(2)


if __name__ == "__main__":
    main()
