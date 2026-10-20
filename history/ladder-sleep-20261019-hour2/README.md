# An hour asleep through the ladder, untouched, 10-19 19:47-20:48

`7387d18947` (dev), Lights out (30 s), over 2.4G (the Mac's dongle),
`scripts/ladder_sleep.py --mode 3 --asleep-s 3600`, relay 2 off, USB data
cut. Nobody touched the board. `run.log`, `ladder-sleep.json`, and the
board's battery log after the run, `battlog.csv`.

This completes validation 3 (`plans/DEEP-SLEEP-BUILD-PLAN.md`): the first hour
(`../ladder-sleep-20261019-hour/`) lost the board's record of its sleep to a
slider move.

**The board's own count matches the logger:**
- **Asleep 30.0 s after the rig's tap** (the ladder's 30), at 19:48:15.
- **Two sleeps:**
  - 19:48:15-20:47:32, 3,556 s by the logger;
  - 20:47:36-20:48:21, 45 s by the logger.

  In all, ~3,601.5 s by the logger, against the board's **3,603 s**
  (`slept_total_s`, "sleeps 2"). Within ~1.5 s over an hour.
- **The second sleep's PCF read 45 s**, which matches the logger's 45; 38
  counted, the usual 0.84.
- **The run's own check failed only because of the comparison it made.** It
  set the PCF's 45 s, the last sleep, against the logger's first sleep.
- **The battery log laid in each period's slept seconds:**
  - 11 (19:48);
  - 900, 900, 900;
  - 892 (20:48), the period with the wake below.

  That is 3,603 in all, the same as the page. The log's energy, 3.3-3.5
  mWh a period, is 13.2-14.0 mW, the logger's figure.
- **The draw asleep:** 3.316 mA, 13.80 mW at 4159 mV over the first sleep
  (2.4G; BT's hour was 13.25 mW).
- **Every other check passed:**
  - woke on the rig's key, and both taps reached the Mac;
  - the recovery closed, nothing timed out;
  - no reset, no pass of 25 ms or more;
  - no UART overrun.
- **The clock** read +347.9 ms at the daemon's first sync, 13 s after data
  came back: an hour on the PCF, whose crystal runs ~58 ppm off, plus the
  wake's phase acquisition.

**⚠️ One wake that nothing explains, at 20:47:32**, 3,556 s into the sleep
and 48 s before the rig's tap.
- **What the logger shows:** ~30 mA for ~3 s (the MCU awake), then the
  2.4G module's tail (18, 15, 12 mA), then asleep again at 3.6 mA. Samples
  1 s apart: `3.3 3.3 30.3 29.8 29.6 18.2 14.6 12.3 3.6`.
- **Not a key or the knob:**
  - the board counted only the rig's two taps;
  - a key or knob wake restarts the ladder's idle (`sleep.c:543-544`), so
    Lights out would have stayed awake 30 s, not ~3;
  - nobody was at the board.
- **What is left:** the charger (CHRG, with relay 2 off and no 5 V), strays
  (`DS_STRAYS_MAX` non-pin, non-second wakes), or something unlisted.
- **Why the reason is lost:** the sleep page keeps only the last sleep's
  reason, the rig's key.
- **Unexplained.** The fix is a record of each sleep's wake reason and RIS
  bits in the dev build, so the next one names itself.
