# An hour asleep through the ladder, 10-19 16:41-17:38

`83d8768c64` (dev), Lights out (30 s), `scripts/ladder_sleep.py --mode 3
--asleep-s 3600`, relay 2 off, USB data cut. `run.log`, `ladder-sleep.json`.

- **Asleep 30.6 s after the rig's tap** (the ladder's 30), at 16:41:53.
- **JD's key woke it at 17:37:45, after 3,355 s (55 min 55 s) asleep** by
  the logger's edges, before the script's own wake at 17:41:55. Then JD
  moved the slider to 2.4G at ~17:38:30, which reset the board (uptime 213 s
  at the read; the mode slider is a power-source switch), so the board's
  record of the sleep (the PCF's slept seconds, the log's v7 entries) went
  with the RAM. The script's checks that read it fail for that reason only;
  `run.log` shows them.
- **The asleep draw, from the logger: 3.207 mA mean (2.94-4.08) at 4133 mV,
  13.25 mW, over 55.7 min (3,341 samples). The firmware assumes 13.35 mW
  (`BATTERY_P_SLEEP_UW`): within 0.8%.**
- JD: "the board seemed to behave just fine after its long sleep".
- The clock read +562.7 ms at the first sync, 3 s after data on: the
  reboot's seed from the PCF at a random sub-second phase, not the sleep's.

Still owed from validation 3: the PCF's seconds and the log's slept entries
across a long sleep, which need a run nobody touches.
