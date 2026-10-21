# The mainline's overnight soak (21:58 10-20 to 01:41 10-21)

On `7ae8575c1a` (a `dev` build, token `0x350912aa`, flashed ~21:50 10-20):
the power modes, deep sleep with its SRAM WFI, the lock-light channel with
play times and the clock-flash fix, and Gremlin's PCF writer fixes. The
slider was on BT, and the Mac's daemon ran as installed (`--clock --radio
--lockchan`). `run.sh` chains the three steps; `chain.log` has their exits.
The gates are codex's, from the mainline review
([`../../plans/review-codex-mainline-2026-10-20.md`](../../plans/review-codex-mainline-2026-10-20.md),
"For RTC sign-off").

**Smoke first** (`smoke/`, `smoke2/`): 2/2 with the RTC gate. The offsets
before the SET were 20.5 and 60.8 ms.

## 1. 150 deep-sleep cycles through the ladder, `--rtc-gate` (`cycles/`)

**139 of 150 passed.** The 11 failures were both of known kinds:
- **6 had one pass of ≥25 ms each** (cycles 7, 36, 54, 83, 124, 148). This
  is the known wake paint. At 23:35 the worst pass was exactly 25 ms, marked
  `blit` (`BACKLOG.md`, the 26-28 ms blit around some wakes).
- **5 UART overruns** (cycles 26, 53, 68, 114, 142), about 2 an hour. That
  is the link's long-standing rate (`BACKLOG.md`, "UART overruns").

Nothing else went wrong: no stray, no reset, no timeout. Every waking key
reached the Mac (median 72 ms, max 126). The link carried 1,241 frames with
547 ACK timeouts and no give-ups.

**The clock, at every reconnect.** The daemon synced 150 times, and each PCF
write settled: the writer idle, nothing pending, STOP and maybe-stopped
clear, stale clear.
- **The offset before each SET:** 22.8 to 207.8 ms, median 89.8, always the
  same sign. By the 15-cycle median it rose from ~50 ms to ~100 ms over the
  first hour, then held at 85-120. It does not track the sleep's length
  (r = 0.08) or the awake dwell (r = 0.03). Earlier soaks saw -1.5 to +2.1 s.
  It sits within the PCF's drift and the read's uncertainty, but runs about
  twice the ~50 ms phase target; noted, not chased.
- **The writer restarted twice** (cycles 8 and 103): a release that missed
  its pass (`PCF_LATE_MS`) went back to STOP, and the write then completed.
  That is the designed recovery path, run twice by the soak.
- **The trim period held 32331 for all 150 cycles.** Apparently the ~30 s
  with USB between sleeps never completes a SOF window; inferred from the
  flat period, not checked in the code.

## 2. 15 minutes awake on the pack, the cable's data cut (`awake-window/`)

**Passed.** The board stayed awake for 908 s with no USB data, then
reconnected. The daemon found it **+1438.5 ms** off before the SET, inside
the standalone discipline's ±2 s design bound
([`../../docs/clock.md`](../../docs/clock.md), "Legacy discipline").

**The legacy discipline ran.** HC_FLASHW page 2 counted **2 trim proposals
from the PCF path** since the flash. The cycles' period never moved, so both
came in this window. The nominal period went 32331 → 32498 (32498 at the
first read, 15 s after the reconnect). The direction was right: warm on USB
afterwards, the SOF loop settled at 32537 (below). The link: 188 frames, 101
ACK timeouts, no give-ups, no overruns.

## 3. An hour asleep (`hour/`)

**Passed.** Asleep for the whole hour at **3.01 mA** (awake 32.87 with
data, 25.77 without). It woke on the rig's F19, the key registered, there
were no strays, and USB was back within 20 s. The record counted 3019 s for
3600 asleep, the ILRC 16% slow (13% on 10-19). The daemon found the board
**+313.3 ms** off before the SET, against ~210 ms for the PCF's 58 ppm over
an hour plus the wake's ~90 ms.

**The current had 19 bursts in the first 19 minutes:** ~13 mA for 8-11 s,
every ~63 s, then none. The 10-19 hour had none. They were the Mac daemon's
lock-light frames: with the data cut it took BT, a track was playing, and
its last frame (01:00:23) ends the last burst (01:00:15-25). The screen then
locked, and the frames stopped. The board never woke; it is the module's
radio, ~1.6 mA on average while it lasts. `BACKLOG.md`, "Lock-light frames
into a sleeping board".

**The trim after the hour** (`period-after-hour.json`, every 20 s from
data-on): 32498 → 32654 → 32632 → 32583 → 32555 → 32542 → 32537 within 5
min, as the board warmed with its lights back on. So the ILRC moves ~0.6%
between the cool, lights-out cycles (32331) and warm on USB (32537). That
matters to type 4's slew limit (Gremlin's notes).

## What codex's sign-off asked, and where it stands

| Gate | Result |
|---|---|
| Every reconnect syncs and persists | 150 of 150, plus the awake window and the hour |
| Wake time correct before the correction | median 90 ms, max 208, no second-scale jump; after the hour, 313 ms |
| Legacy calibration works | 2 PCF-path trims in the 908 s window, in the right direction |
| The hour continuously asleep | yes: no strays; the current bursts were radio, not wakes |
| No new ≥25 ms passes, UART errors, resets | 6 wake paints, 5 overruns: both known, neither new |
| Failure paths (re-planning, correction mid-write, STOP recovery) | not covered by a healthy-bus soak; owed (Gremlin) |

⚠️ **Missed:** the final health read after the hour. The type-4 flash at
01:49 reset the counters and the stack watermarks before they were read.
The per-cycle deltas above are the stall and UART totals. The watermarks
were 664/1240 B free at boot on `7ae8575c1a`.

## After: the type-4 firmware

`9e52400ec9` (a `dev` build, token `0x3c899a32`): Gremlin's lock-LED
clock, a fast-forward from `7ae8575c1a`, cleared by codex for an attended
test. Flashed 01:49-01:50 10-21 (`flash-type4.log`). At boot: msp 664, psp
1272 B free, and the first sync found the board 19.8 ms off. The Mac sends
no type 4 until its latency is measured (`clock_latency_ms` is None). That
measurement needs an unlocked screen, so it waits for the morning.

**30 deep-sleep cycles with the RTC gate on it** (01:55-02:19,
`type4-cycles/`): **30/30 passed**. No pass reached 25 ms, and there were
no UART errors, strays or resets. Every reconnect synced, and every PCF write
settled. The offsets before the SET were 29.9 to 103.7 ms (median 67.1).
"Mac nothing" in each line is the Mac's locked screen: its listener sees no
keys there. The board's own count registered every waking key. Afterwards
(`health-after-type4-cycles.json`): worst pass 24 ms (`blit`), msp 640 and
psp 1232 B free.
