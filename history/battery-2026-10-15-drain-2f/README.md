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
after every poll), with `caffeinate -i -s -w <guard>`. The 1 Hz pack log is
`../power-bench-2026-10-15/power.csv` (recorder pid in its `power.pid`).

**Expected:** the RGB cut around 02:00 10-18 (drain 2e: 50.35-50.43 h), then
the charge to FULL (~9 h).
