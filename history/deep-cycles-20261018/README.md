# Deep sleep, 50 cycles (20:14-20:43 10-18), and the awake control (20:43-20:47)

`scripts/deep_cycles.py --n 50` on `53afebd553`, the Mac unlocked and the F19
listener timing each key at the Mac. Per cycle: armed, slept (seen in the
logger), a random 5-25 s asleep (the record: 8-28 s), the wake rig's F19
(40 ms), the data back on, the board's record. Raw: `deep-cycles.json`.

**50 of 50 pass**: every cycle slept, woke on the rig's key (P2.8), the
waking key registered on the board and reached the Mac. No stray wakes; the
PLL relocked in 128-130 loops every time.

| first key at the Mac, from the relay command | median | 90th | min | max |
|---|---|---|---|---|
| **after a deep sleep** (50) | **64 ms** | 77 | 46 | 104 |
| **awake, after the same silences** ([`../idle-latency-20261018/`](../idle-latency-20261018/idle-latency.json), 12) | **36 ms** | - | 31 | 42 |
| awake, typing at 9/s (10-16, `../wake-rig-2026-10-16/`) | 33.5 | p95 45 | - | 108 |

**The wake costs ~28 ms at the median, ~60 at worst**, on the first key
only; not the radio link idling (the awake control after the same silences
is as fast as typing). The CPU's own wake is ~0.1 ms (the manual's ~12.7 us
plus the PLL's ~100 us). Leading suspect, untested: the CH582F, silent for
the whole sleep (no battery poll, the UART receiver off), dozes and misses
the first frame, which the board's 10 ms ACK timeout then resends. The next
run records the link's counters per cycle.

The first run (20:10) is not in these numbers: its cycles 1-2 caught JD
typing (a column already low: no sleep, correctly), cycles 3-6 passed, and it
stopped at cycle 7 on the macOS raw-HID collision with the daemon
(`deep_sleep.py` now retries the open).
