#!/usr/bin/env python3
"""win_raw81_echo.py on macOS: the same echo, matching, output and board
counters (plans/RAWHID-24G-PLAN.md, stage 1), with the two Windows parts
replaced.

    venv/bin/python scripts/mac_raw81_echo.py [N] [--gap-ms MS] [--timeout-ms MS] [--seed S] [--json OUT]

- The dongle's FF60/61 collection (0C45:FDFD) by its registry id, from
  `hidutil list`, which opens nothing; hidapi's macOS path is
  `DevSrvsID:<id>`. The daemon never opens the dongle.
- HC_DEVHOOK op 17 over the cable through deep_sleep's retrying opener: on
  macOS the board's raw HID is exclusive, and the daemon holds it for a
  moment every ~3 s.

Needs the gremlin/raw81 dev build on the Mac's board, its own dongle in the
Mac and the slider on 2.4G.
"""
import os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import win_raw81_echo as w  # noqa: E402
import deep_sleep as ds  # noqa: E402

cm = ds.cm


def dongle_path(mi):
    """`mi` names the Windows interface; on macOS it is the FF60/61 collection."""
    out = subprocess.run(["hidutil", "list"], capture_output=True, text=True, check=True).stdout
    hits = [ln.split() for ln in out.splitlines()]
    hits = [f[5] for f in hits if len(f) > 6 and f[0] == "0xc45" and f[1] == "0xfdfd"
            and f[3] == "65376" and f[4] == "97" and f[7] == "AppleUserHIDDevice"]
    if len(hits) != 1:
        sys.exit(f"the dongle's FF60/61 collection: found {hits}")
    return f"DevSrvsID:{int(hits[0], 16)}"


def board_counters():
    def read(h):
        out = {}
        for sub in (0, 1):
            r = cm.xfer(h, [cm.SET_VALUE, cm.HEALTH_CH, cm.HC_DEVHOOK, 17, sub] + [0] * 27)
            if len(r) < 12 or r[3] != 17:
                sys.exit(f"a reply to another op: {r[:12].hex()}")
            out[sub] = r
        return out
    d = ds.with_dev(read)
    p0, p1 = d[0], d[1]
    names = ["committed", "bad_sum", "lifetime", "uart_taint", "sent_91", "dropped", "acks_in_window"]
    c = {n: int.from_bytes(p0[4 + 4 * i:8 + 4 * i], "little") for i, n in enumerate(names)}
    c.update(state=p1[4], boundary=p1[5], pending=p1[6], ack_window=p1[7],
             quarantined_bytes=int.from_bytes(p1[8:12], "little"),
             # as win_raw81_echo.py: zeros on 7387d189, filled by the drain-bounded build
             last_collect_ms=int.from_bytes(p1[12:14], "little"), last_passes=p1[14], max_passes=p1[15])
    return c


if __name__ == "__main__":
    # The build first, --no-board or not: an older build reads the random
    # bodies through the 3-byte window alone, where a 5B or 5C with a
    # matching sum would act (a link state, a battery reading).
    board_counters()
    w.dongle_path, w.board_counters = dongle_path, board_counters
    w.main()
