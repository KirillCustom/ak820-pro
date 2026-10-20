#!/usr/bin/env python3
"""win_raw81_s2.py on macOS: the same stage-2 tests, checks and report
(plans/RAWHID-24G-PLAN.md), with the platform parts replaced.

    venv/bin/python scripts/mac_raw81_s2.py [all|text|health|clock|nak|stream N|load SECONDS] [--json OUT]

- The dongle's FF60/61 collection by its registry id from `hidutil list`
  (mac_raw81_echo.dongle_path), which opens nothing.
- The machine-wide owner's lock: a POSIX record lock on byte 0 of
  /Users/Shared/ak820pro/raw81.lock, the daemon's when it gains the radio.
- The cable through deep_sleep's retrying opener, opened and closed around
  each transfer: macOS's raw HID is exclusive, and the daemon needs it.

Needs the stage-2 dev build on the Mac's board, its dongle and the slider on
2.4G.
"""
import fcntl, os, sys, types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.modules.setdefault("msvcrt", types.ModuleType("msvcrt"))   # the Windows script imports it; unused here
import win_raw81_s2 as w  # noqa: E402
import deep_sleep as ds  # noqa: E402
import mac_raw81_echo as me  # noqa: E402

cm = ds.cm
LOCK = "/Users/Shared/ak820pro/raw81.lock"


class Owner(w.Owner):
    def __init__(self):
        os.makedirs(os.path.dirname(LOCK), exist_ok=True)
        self.lf = open(LOCK, "a")          # kept empty: the lock is on byte 0
        try:
            fcntl.lockf(self.lf, fcntl.LOCK_EX | fcntl.LOCK_NB, 1, 0)
        except OSError:
            sys.exit("another raw81 owner holds the dongle on this machine")
        self._open()


class Radio(w.Radio):
    def __init__(self):
        import hid
        self.d = hid.Device(path=me.dongle_path("mi_03").encode())
        self.owner = Owner()
        self.max_rtt = 0.0


class Cable:
    """Stands in for the Windows script's held handle: each transfer opens it."""


def usb_open():
    return Cable()


def usb_xfer(h, payload, match=3, wait_s=2.0):
    p = list(payload) + [0] * (32 - len(payload))
    return ds.with_dev(lambda hh: cm.xfer(hh, p))


def stalls(h):
    import ak820health

    def rd(hh):
        return ak820health.read_stalls(hh), ak820health.read_link(hh)
    s, link = ds.with_dev(rd)
    return {"ge25": s["count_ge_25ms"], "ge10": s["count_ge_10ms"],
            **{k: link.get(k) for k in ("giveups", "replaced", "queue_full", "uart_overrun") if k in link}}


w.Owner, w.Radio, w.usb_open, w.usb_xfer, w.stalls = Owner, Radio, usb_open, usb_xfer, stalls

if __name__ == "__main__":
    w.main()
