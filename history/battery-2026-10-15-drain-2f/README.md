# R3: the logged drain on flash 2f (from 2026-10-15 23:37:59)

`plans/POWER-CAMPAIGN-PLAN.md`, R3/R4. Running unattended: in progress.

**Start boundary** (codex's must-fix 3): `bench_trial.py --full` topped the
pack up to the charger's termination (FULL 23:27:19, two CHRG-high reads 5
min apart; `topup.csv`, `log-20261015-2337-full.csv`). Relay 2 off at
**23:37:59.44 10-15** (epoch 1792129079.44): the logger's charge register
read **−2.561231 mAh** (`qt_ms` 7514315), pack 4187.5 mV (`vcell_mv`),
current 0.05 mA (terminated), INA228 die 22.2 °C. The board: 100.0%,
`5C` at the clamp, supply battery from 23:38:20.

**Conditions:** flash 2f (`523744c30f`, `dev`, token `0x754f00c1`); JD's
lighting (effect 2, hue 169, sat 0, val 255, speed 231) and screen (level 5);
warn 3680 mV, RGB cut 3400 mV (`cfg`, defaults); slider on BT, linked; USB
data attached (R2: +1.7 mA); JD using the keyboard as usual.

**Running:** `bench_trial.py --wait-cut --poll 300 --full` (pid in
`trial.pid`; `drain.csv`, `drain.log`), guarded by `trial_guard.py` (pid in
`guard.pid`; `guard.log`, and `guard-activity.csv`: key presses and stalls
after every poll; its `loop_gap_max_ms` column stays empty, since the light
read, `read_stalls()`, does not carry that field), with `caffeinate -i -s -w <guard>`. The 1 Hz pack log is
`../power-bench-2026-10-15/power.csv` (recorder pid in its `power.pid`).

**If the Mac reboots:** `scripts/resume_bench.sh` restarts what is not
running and logs to `resume.log` here. A gap in `power.csv`, and a charge
register restarted from 0 if the dock cut the QT Py's power, are to be
bridged in the analysis (the board is a ~307 mW load).

**Expected:** the RGB cut around 02:00 10-18 (drain 2e: 50.35-50.43 h), then
the charge to FULL (~9 h).

**Not JD's typing:** two presses through the wake rig's relay 1 (the
play/pause key, the rig's first test), at 16:07:37 and 16:07:54 10-16.
Both toggled the Mac's music as intended. `guard-activity.csv` counts them
as key presses; relay 2 stayed off. Two more, at 12:50:22 and 12:52:31 10-17
(did F19 wake the sleeping displays? both did).

**The outage, 10-17.** Mains failed at **15:42:35** (the QT Py's last row
15:42:34): the dock and everything on it lost power. The keyboard ran on
through it on its pack. The trial's last good read was 15:39:10 (18.9%,
3776 mV); from 15:44:10 its reads failed, with raw HID gone with the dock.
The Mac, on its own battery, kernel-panicked at ~16:18 (a 60 s busy timeout
in `IOMediaBSDClient`; most likely the external drives losing power
mid-write) and rebooted, which killed the trial, the guard and the recorder
(the trial's last row 16:14:16). The dock's power came back at **16:57:12**
(from the QT Py's `qt_ms`), and `resume_bench.sh` restarted everything at
17:10:43-47 (the trial into `drain-resumed-1.csv`: 16.0%, 3758 mV, on
battery).
- **The board did not restart:** uptime 5,655,943 ms at 23:43:22 10-15 and
  155,126,893 ms at 17:15:45 10-17, one boot (~22:10 10-15), so its RAM log
  is whole. `count_ge_25ms` 0.
- The relay came back up off (its safe state), so no charge went in.
- **The logger's hole is 15:42:34-16:57:12 (1 h 14.6 min).** The charge
  register restarted at 0 at 16:57:12 and has counted since, with or without
  a reader. At the 78-83 mA either side of the gap, that is ~100 mAh to bridge
  (an estimate).
- Logged before it: **3139.3 mAh over 40.08 h** (mean 78.3 mA), `vcell`
  4187.5 to 3781.3 mV.
