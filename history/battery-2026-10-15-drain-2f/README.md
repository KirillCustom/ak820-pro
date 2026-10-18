# R3: the logged drain on flash 2f (from 2026-10-15 23:37:59)

`plans/POWER-CAMPAIGN-PLAN.md`, R3/R4. **Done:** the RGB cut at 02:01:22
10-18 after 50.39 h; charged from 02:06:53 to FULL (the charger stopped at
12:00:49; the trial confirmed at 12:07:13 and ended at 12:17:15).

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

**Not the drain's load either: the lock-LED probe, 23:22:05-23:23:37 10-17**
(`hostagent/ledprobe/`, `plans/BACKLOG.md`): Scroll Lock written twice over
USB, then twelve times over Bluetooth (six SCR blinks on the LCD). No raw
HID, no relay, no cable change; placed between the trial's 23:20:49 and
23:25:49 polls. The guard's count of passes >= 25 ms stayed 0 (23:25:52).

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

**The cut, 10-18.** The logger saw the RGB cut between **02:01:22 and
02:01:23** (90.4 mA, then 37.3; `vcell` 3387.5 mV just before it; the
board's `5C` read 3399 mV at the trial's 02:00:52 poll). That is **50.39 h**
from relay 2 off (drain 2e: 50.35-50.43 h). The trial saw `lights_cut` at
its 02:05:52 poll, dumped the RAM log at 02:05:53
(`log-20261018-0205-rgbcut.csv`, 311 entries) and turned relay 2 on at
02:06:53 (the logger: -565 mA at 02:06:54); FULL-ON 02:07:03. Between the
cut and the relay the board drew 35.2-37.3 mA.
- **Charge to the cut: ~4012 mAh** (mean ~79.6 mA): 3139.3 mAh logged
  before the outage, 771.3 mAh on the restarted register (16:57:12 10-17 to
  the cut), and ~101 mAh bridged over the 1.244 h hole. The bridge is an
  estimate: 81.9 mA (the 10 min before the hole) gives 101.9 mAh, and the
  register's own mean from its restart to the recorder's first row (80.5 mA
  over 13.5 min) gives 100.1.
- **One pass >= 25 ms, and the lock-LED probe was hammering the board when
  it happened.** The guard's count went from 0 (23:45:51 10-17) to 1
  (23:50:52) and stayed 1 to the cut (02:05:54: `loop_gap_max_ms` 25,
  marked `blit`; `blit_gap_max_ms` 25, up from 22 at 20:50). That window
  holds the probe's 200 back-to-back writes (23:46:30-23:46:36, Scroll Lock
  toggled ~33 times a second while JD typed) and its 30 s hold with two
  Caps Lock presses (23:48:30-23:49:00); its later writes, from 23:51:15,
  were all refused by the locked Mac. The guard reads every 5 minutes, so it
  cannot say which of the two did it. Every lock-band change repaints the
  band, so the burst is the likelier one (an inference, not measured).
  The load itself is not something the drain does.

**The charge (R4), 10-18.** Relay 2 on at 02:06:53; the logger's first
charging second 02:06:54 at -564.9 mA (`vcell` 3526 mV). Constant current
~549-565 mA to ~05:00 (`vcell` 4006 mV), then a taper: -486 mA at 06:00, -371
at 08:00, -283 at 10:00, -131 at 11:30. **The charger stopped at 12:00:49**, the
last charging second at -81.1 mA (`vcell` 4186 mV; the 10-15 top-up stopped at
~78 mA). **3972.4 mAh in over 9.90 h**, against ~4012 mAh out to the cut (and
~3 mAh of reserve before the relay): the register says the pack took back
~1% less than it gave, so read the two as equal within the bridge's estimate
and the register's own error, not as a measured efficiency. The trial saw FULL
(CHRG high twice, 5 minutes apart) at 12:07:13 and dumped the RAM log at
12:17:15 (`log-20261018-1217-full.csv`, 372 entries). The board's level read
99.0% from 11:37 to 11:57 and 100% at 12:02.

**The stall count at the end: still 1** (`ak820health.py --stalls`, 12:18:02,
uptime 62.1 h, 43,171 presses): the 08:02 probe run (200 Scroll Lock toggles
at 32/s among them) added none. Flash 2h went on at 12:18.

## A1/A2: the gauge's real accuracy, and the capacity (10-18)

`scripts/r3_level.py` (output `a1-summary.txt`, per point `a1-points.csv`).
To the RGB cut: **50.39 h, 4011.6 mAh, 15,579 mWh** (mean 3884 mV, 79.6 mA,
309 mW; the hole bridged at 81.2 mA, 101.0 mAh, an estimate). Three truths
for "how much is left", each 100% at relay off and 0% at the cut: time
(`1 - t/T`, the gauge's own definition), energy (mWh) and charge (mAh).
**Time and energy agree within 0.1 point everywhere** (the load is nearly
constant power); charge sits 1-1.6 points below them (the current rises as
the pack falls). Against energy, 596 board readings (588 trial reads, 8 from
the RAM log over the outage):

| energy left | n | board − truth, mean [range] | MAX17048 − truth |
|---|---|---|---|
| 90-100% | 61 | −0.5 [−3.4, +0.3] | −6.2 |
| 80-90% | 61 | **−7.9** [−13.4, −2.3] | −4.5 |
| 70-80% | 60 | **−10.1** [−13.7, −6.5] | +3.6 |
| 60-70% | 61 | −4.2 [−6.5, −1.8] | +11.8 |
| 50-60% | 60 | −1.9 [−2.8, −1.3] | +15.2 |
| 40-50% | 60 | −4.9 [−7.0, −2.5] | +15.9 |
| 30-40% | 61 | −4.7 [−6.4, −3.3] | +19.9 |
| 20-30% | 58 | −2.3 [−3.7, −1.3] | +21.4 |
| 10-20% | 53 | −1.1 [−2.5, −0.2] | +11.4 |
| 0-10% | 61 | −0.3 [−0.7, +0.1] | −1.9 |

- **The flat top is worse this cycle than on the last two:** −13.7 worst at
  70-80% (10-10: −8.3, 10-12: −7.2, both judged against time as well). Below
  60% it stays within 7.0 points, and within 2.5 below 20%. It reads low
  throughout (never more than 0.4 high).
- **Why:** from 85% to 70% the pack moves 15 mV (`vcell` 4029.8 to 4015.0),
  about a point per mV, so `5C`'s ~10 mV accuracy and a cycle's drift each
  cost ~10 points there. No voltage-to-level table can fix the plateau.
- **The MAX17048's own SOC is no shortcut:** up to 22 points high in the
  middle, on its generic model.
