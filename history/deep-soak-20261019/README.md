# Deep sleep, 247 cycles (03:21-05:43 10-19), and the awake control (06:46-07:02)

`scripts/deep_cycles.py --board-only --n 300` on `53afebd553`, the Mac locked
(the board's record alone; no timing at the Mac). Per cycle: armed, data off,
asleep in the logger, a random 5-25 s, the wake rig's F19, data on, the
record and the radio link's counters (`HC_LINK`) across the cycle. Raw:
`deep-soak.log` (the run stopped before writing its JSON; the script now
keeps a stopped run's cycles), `daemon-clock-syncs.log`.

**247 of 247 pass:** every cycle slept, woke on the rig's key (P2.8), the
waking key registered, no stray wakes, no radio give-ups. The record's
sleeps ran 7-30 s.

**It stopped in cycle 248** (05:43:34): the daemon's clock sync on
enumeration held raw HID for 11 s (05:43:24-35, macOS opens it exclusively)
and the script's open gave up at 8 s. Its cleanup ran and the bench was left
right. The open now waits 30 s (`255aa28`). The last 53 cycles were not
re-run.

## The radio's retries are not the wake's

| | cycles | frames/cycle | ACK timeouts/cycle | timed out | give-ups |
|---|---|---|---|---|---|
| deep sleep (this run) | 247 | 4.86 | **2.95** | 61% | 0 |
| awake control (`../awake-control-20261019/`) | 30 | 8.43 | **3.30** | 39% | 0 |

The control is the same cycle never armed (`--awake`): data off, the same
silence (6 + 2 s + a 5-25 s dwell), the tap, data on. **The board retries as
often awake as after a deep sleep**, ~3 frames a cycle. So the 10-18 suspect
for the wake's ~28 ms first-key cost (the CH582F dozing through the sleep and
missing the first frame) is not supported, and that cost is unexplained. The
retries come from something both arms share (the USB data toggle, the
silence, or frames the board sends unprompted); the counters do not say which
frames timed out. Typing measured 4.2% (09-03).

## The clock: asleep, the RTC runs slow

The daemon resynced the board 16 times during the run, finding it -1.5 to
+2.1 s off each time. The 1-hour sleep (`../deep-sleep-20261019-1h/`) shows
why: asleep, the RTC's second runs ~13% slow, so each ~20 s cycle lost ~3 s
and the PCF discipline snapped it back at its 2 s threshold.
`plans/SLEEP-PLAN.md`, "Asleep, the RTC's second runs ~13% slow".
