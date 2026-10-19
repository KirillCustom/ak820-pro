#!/usr/bin/env python3
"""Drop the AK820 Pro into its bootloader over raw HID, so a flash needs no
Fn+Esc. flash.sh runs this after its backups.

Only `dev` and instrumented builds carry the jump (build.sh). Any other build
answers "unhandled", and this exits 2 so flash.sh falls back to asking for
Fn+Esc. The command is [SET_VALUE, HEALTH_CHANNEL, HC_BOOTLOADER, 'B', 'O',
'O', 'T'] (hid_protocol.c). The board refuses with "BUSY" while a key moved in
the last 2 s, or if it could not deliver the key releases over BT; this
retries for a minute (each try sends fresh releases). Otherwise it releases every key, shows its bootloader
splash for 1.5 s, then resets into the ROM: it leaves USB as 0C45:8009 and
comes back as 0C45:7140.

⚠️ The bootloader looks exactly like a dead board. Run this only to flash; a
cold power-off (slider to cable, unplug ~10 s) gets back out without one.

Opens the board through ak820health.open_device(): hid_enumerate on macOS, and
on Windows a path listed by the Configuration Manager, which opens nothing but
the board (ak820text.raw_hid_paths; CLAUDE.md, "Never enumerate HID").

Exit: 0  the jump was sent and no reply came (the board is resetting);
      2  the firmware answered unhandled (no jump in this build);
      3  still BUSY after a minute;
      1  anything else.
"""
import sys
import time

import venv_bootstrap  # noqa: F401 -- re-execs under the repo venv if hid is missing
from ak820health import open_device

SET_VALUE, HEALTH_CHANNEL, HC_BOOTLOADER = 0x07, 0x13, 0x79
UNHANDLED = 0xFF        # RTC_UNHANDLED, == VIA's id_unhandled


BUSY_WHY = {1: "a key moved in the last 2 s", 2: "the BT key releases were not delivered"}


def ask(h):
    """One request. Returns "jumped", "unhandled", ("busy", why) or an error."""
    h.write(bytes([0x00, SET_VALUE, HEALTH_CHANNEL, HC_BOOTLOADER]) + b"BOOT" + bytes(25))
    # The jump never replies: the splash holds 1.5 s, then the board leaves the
    # bus. Other processes' reports can land on this handle too, so take only
    # one that answers this command and echoes its bytes.
    for _ in range(5):
        try:
            rep = h.read(32, 2500)
        except Exception:               # the board left the bus mid-read
            rep = None
        if not rep:
            return "jumped"
        if rep[1] != HEALTH_CHANNEL or rep[2] != HC_BOOTLOADER:
            continue
        if rep[0] == UNHANDLED and bytes(rep[3:7]) == b"BOOT":
            return "unhandled"
        if rep[0] == SET_VALUE and bytes(rep[3:7]) == b"BUSY":
            return ("busy", rep[7])
    return "five reports, none of them an answer to the jump"


def main():
    h = open_device()
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            r = ask(h)
            if isinstance(r, tuple):
                print(f"board busy: {BUSY_WHY.get(r[1], r[1])}; retrying")
                time.sleep(1)
                continue
            if r == "jumped":
                print("jump sent: no reply, so the board is resetting into its bootloader")
                return 0
            if r == "unhandled":
                print("this firmware has no raw-HID bootloader jump (not a dev or instrumented build)")
                return 2
            print(r)
            return 1
        print("still busy after a minute: not jumping")
        return 3
    finally:
        try:
            h.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
