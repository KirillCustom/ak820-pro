# Deep sleep over 2.4G, the Mac's dongle (2026-10-19, 17:47-17:56)

`venv/bin/python scripts/deep_cycles.py --n 10 --out history/deep-cycles-20261019-2g4`
on `83d8768c64`, the slider on 2.4G (the dongle in the Mac), the USB cable in
for raw HID with its data cut each cycle, relay 2 off for the run. Ten cycles
through the ladder (Minimal, the bench's 30 s sleep time), each woken by the
rig's F19.

**The run's verdict was 1 of 10. Re-judged, 10 of 10.** Every failure was the
PCF check alone, the PCF 4-6 s longer than the logger's sleep. The logger's
edge was at 15 mA, and in 2.4G the draw stays at ~15 mA for ~5 s after the MCU
stops (`current.csv`):

| | BT (13:20:57, the 100-cycle soak) | 2.4G (17:48:28, cycle 2) |
|---|---|---|
| awake, dark | ~27.0 mA | ~28.2 mA |
| first seconds asleep | ~2.3-3.1 mA | ~15.2 mA for 4 s |
| then | ~3.1 mA | ~3.9 mA for 19 s, then ~3.2 |

**The tail is the module's, not the MCU's.** The MCU counts seconds only while
its CPU is stopped, and counted/PCF is 0.842 here and 0.842 over the BT soak's
100 cycles. A 5 s awake tail before the stop would have pulled this run's ratio
to ~0.67.

`rescore.py` (output `rescore.txt`) times the edges at 22 mA instead, and
checks everything else from the run's own record:

- **The PCF within 1 s** of the logger in every cycle.
- **Woke on the key every time**, the rig's column (P2.8), no strays.
- **Exactly one key each**, a fresh sequence, no reset, no pass of 25 ms or
  more.
- **The recovery closed with nothing timed out.**
- **No give-ups, queue-full, replaced or UART errors.**
- **The Mac's key-down**: a median 48.0 ms after the relay, worst 56.4,
  against the BT soak's 63.7 and 114.8.
- **Frames per wake**: 9 sent, 4 ACK timeouts, every cycle. The BT soak sent
  8-9 with 3-7.

Over the same 100 BT cycles a 22 mA edge gives the same PCF differences as
15 mA (worst 1.04 s, none over 2), so the bench scripts' edge is now 22 mA
(`deep_sleep.SLEEP_EDGE_MA`).

The tail costs ~60 mA·s a sleep (~12 mA extra for ~5 s, plus the ~0.7 mA
step): nothing against the pack, and the firmware's sleep power (13.35 mW)
holds from then on.
