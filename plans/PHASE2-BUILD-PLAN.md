# Phase 2, the build: an energy gauge, time left, and the ladder's modes

Status: **revision 2, after codex's plan review (10-18; dispositions at the
end); build authorized.** The order
it builds in is `BATTERY-GAUGE-PLAN.md`'s ("Phase 2: the agreed order"),
with the gauge and a time-left estimate first, as `POWER-CAMPAIGN-PLAN.md`
("What follows in firmware") put them. Every flash gets a codex review first.

## What the measurements say (10-15 to 10-18)

- **The board is a constant-power load.** Over R3, 305-311 mW from 4.17 V to
  the cut (`vcell` binned by 25 mV), at JD's settings: full white, screen
  level 4-5, BT linked, USB data attached.
- **The capacity:** 15,579 mWh (4011.6 mAh) from FULL to the RGB cut (R3,
  `history/battery-2026-10-15-drain-2f/`, "A1/A2").
- **The gauge reads low on two flat stretches** of the voltage curve, worst
  −13.8 at 70-80% and −5.0 at 40-50% (R3), against the logged energy. Time
  left and energy left agree within 0.1 point at this load. A voltage table
  cannot fix a stretch where 15 mV spans 15 points.
- **The charging display needs nothing:** within ±1.1 points of the logged
  charge over a whole charge from 0 (A3, 10-18).
- **The loads (R1, mW at ~4.17 V, data attached):** every light out 114.0;
  the backlight 39.1 at full, linear in its duty (`bkl_duty[]` / 48); the RGB
  in white 188.7 at value 255, 43.0 at 128, 17.9 at 64. QMK's CIE curve maps
  those values to a drive of 255, 48 and 12 in the LED buffer (codex), and in
  drive the power is close to linear: **~9.5 mW + 0.70 mW per buffer unit**
  (all 246 channels), so 187 at 255. (The first draft placed 64 and 128 at
  drives of 64 and 128 and read a nonlinearity that is the curve's.) USB data
  adds 7.1 (R2: 1.7 mA). The RGB is being mapped at 14 values plus red, green
  and blue alone (`current_map.py --set rgb-curve`, scheduled 00:30 10-19),
  with the board's own LED drive read in each state, so the fit is in buffer
  units and per colour.
- **JD's idle (A8, R3's 48.7 polled hours):** typing in 11% of the 5-minute
  intervals; 88% of the time in idle runs of 5 min or more, 84% in runs of
  2 h or more. RGB off while idle takes the mean draw from ~306 mW to ~140:
  **about 2.2x the runtime** (an estimate from the model, not measured).
- **The replay (A7, `scripts/gauge_replay.py`):** the gauge below, run over
  R3's and drain 2e's 10-minute logs, is within **+0.8 points** of the truth
  everywhere on both (the board as built: −13.8 and −7.3 worst). That is one
  load, the one the model was measured at, so it tests the arithmetic and the
  capacity, not the model's other states. With the capacity deliberately
  wrong: ±5% stays within 3.7 points; +13% within 4.1 (the band pulls the
  count down); **−13% reads up to 7.6 low** (the count waits for the voltage
  but never rises on battery).

## The design (revision 2)

### 1. The energy count (battery.c)

**The model.** The board's draw from what it knows it is doing:

    P_mW = P_BASE + P_BKL(effective backlight level) + P_RGB(committed LED frames) + P_USB(data attached)

- `P_BASE` 106.9 mW: R1's 114.0 less R2's 7.1 for USB data. Calibrated for
  JD's conditions (the MCU awake, BT linked, the panel drawing): the radio's
  other states, 2.4G, temperature and other units are not measured, and the
  plan claims nothing for them.
- `P_BKL` = 39.1 x `bkl_duty[level]` / 48 at the effective (capped) level.
- `P_RGB` from **the committed frames, time-weighted**: the driver sums the
  buffer it hands the row ISR at each flush (in the main loop, after the
  masked copy, not inside it), and keeps `sum x ms` since the last read, so
  the battery code gets the exact mean drive over each second, whatever the
  effect's frame pattern. Then `P_RGB = A_RGB + B_RGB x mean drive` while the
  RGB is lit (R1's fit: 9.5 mW and 0.70 mW per buffer unit; tonight's map
  refits both and checks the colours; if they differ, a gain per channel).
  Linear in drive, so the mean is the right input; a nonlinear `f` would have
  to be summed per frame before averaging (codex), which the flush makes
  possible if tonight's map asks for it.
- `P_USB` 7.1 mW while USB is configured on battery (the bench); zero in
  real use.

**Elapsed time, not ticks.** The count integrates `P_mW x elapsed ms` from
the timer (the 10 Hz housekeeping's ten calls are not a second), in a
`uint64_t` mW.ms accumulator with its remainder kept, and drops 1 pm per
`E_PM_MWMS` = 15,579 mWh x 3,600,000 / 1000 = 56,084,400 mW.ms. Two figures
are kept apart:
- **`e_used`**: the modelled energy drawn from the pack, always counted on
  battery (the log's field, the replay's input). Never held.
- **the level's count**: `e_used` turned into pm, subject to the band below.

**The band: the curve at both ends of the voltage's allowance** (codex
must-fix 1). For a fresh estimate below the top clamp at `V`:
`lower = max(0, curve(V − 12) − 15)`, `upper = min(1000, curve(V + 12) + 15)`
pm, in signed arithmetic. 12 mV is an allowance from eight meter checks, not
a proven bound. Then, each second:
- **level above `upper`:** steps down toward `upper` by the existing ratchet
  (`STEP_DOWN_MEDIANS` estimates in a row, at most 1% a step). The count
  rebases there: no energy is "owed" after the correction.
- **level below `lower`:** the level holds; the count's pending energy is
  **discarded**, not banked, so the hold's end cannot drain the level in a
  rush (codex must-fix 6). `e_used` keeps counting.
- **at the top clamp** (`5C` 100): `curve()` saturates at 900, which is no
  upper bound; the only bound is "at least 90%" (as the countdown's floor is
  today), and the count leads.

**0% is the cut, explicitly** (codex must-fix 2). The count never takes the
level below 10 pm (1%) on its own; **the protection's cut latching sets 0**
(today only the critical-zero path does), and after it the panel shows
`Low` and no time left: the reserve is outside the calibrated capacity. The
whole-percent display rounds 10 pm to 1%.

**Anchors.**
- **FULL** sets 1000 and zeroes the count's remainder.
- **The unplug:** the count runs from the charging model's level at once
  (the relaxation's 30 min cost ~1 point at 306 mW if held, codex), and the
  re-seat after `RELAX_S` applies the band to that counted level instead of
  adopting `curve(V)` outright: inside the band, kept; outside, to the band's
  nearer edge (the one upward move on battery stays allowed there). An
  UNKNOWN or LOST session re-seats from `curve(V)` as today, and counts from
  there.
- **A boot on battery** with no saved level: `curve(V)` from the first
  estimate, as now, and the count from it. Its error is the voltage's (up to
  ~12 points on the flat stretches): **the accuracy target does not cover a
  reboot on battery** (codex).
- **Brief replugs, a handover, stale reports:** the count runs only on
  battery; on USB it stops (the charging model owns the level); a stale
  estimate holds the band (no correction), the count continues.

**Integer ranges.** Signed 32-bit for the band; `uint64_t` for the mW.ms
accumulator and the time-left division (once a minute); no per-LED division,
no floating point.

### 2. Time left (codex should-fix 9)

- **The draw it divides by:** each minute, that minute's integrated mean
  power (from `e_used`), into an exponential average whose time constant
  grows from 10 min to 12 h over the first 12 h on battery (`maturity`), so
  the boot's first minutes do not bias a whole drain. Signed fixed point.
- **On USB:** hidden, and the average **frozen** (not reseeded), so a short
  charge does not reset what JD's pattern taught it.
- **Shown:** `Fn`+`D`'s camera page, the level line, now 8 cells (`100% 51h`
  fits: 8 x 15 px = 120 of 128). `~` before it while immature (under 1 h of
  history); `<1h` below an hour; `4d` from 100 h; nothing after the cut,
  on USB, or with the level unknown.
- **Time left = level x `E_PM_MWMS` / average**, to the cut.

### 3. Raw HID: a new command, `HC_POWER` (0x0F) (codex must-fix 7)

`HC_CONN` is full, so the power figures get their own reply: mode, stage,
`P_mW` now (0.1 mW), the average, time left (minutes, `0xFFFF` none),
`e_used` since FULL (0.1 mWh), the count's state (normal / held / ratcheting
/ clamp / cut), the band's edges, and the maturity. `ak820battery.py`
prints it; the trial logs it each poll beside its existing level line.

### 4. The log, v6 (codex must-fix 7)

720 entries in the same 12,960 bytes: `vdd_min` gives way to **the period's
modelled battery energy** (`e_used`'s increase, 0.1 mWh, u16; ~76.5 mWh at
306 mW over 15 min; zero for time on USB), and the period becomes **15 min**
(180 h). Every consumer changes with it:
- `ak820battery.py`: v6 decoded (energy in place of `vdd_min`), v1-v5 kept.
- `scripts/battery_sim`: the CSV reader takes `vdd_min` as optional; the log
  scenarios that hard-code v5 and 600 s are updated to v6 and 900 s.
- `bench_trial.py`: the final wait after FULL becomes 900 s + margin, so the
  dump holds a completed FULL entry.
- `battery_fit.py`: stays historical (it reads v3-v5 at 10 min); noted in its
  docstring.
- The guard: no log dependency (codex); it adds the `HC_POWER` read to its
  activity rows.

### 5. The modes on `Fn`+`B` (power.c), the agreed step 3

As built in `37b844b83c` (Full / Normal / Saver; the stage times in
`BATTERY-GAUGE-PLAN.md`), with codex's fixes:
- **Every stage change repaints every LED** (must-fix 4): it restarts the
  current effect without touching the EEPROM (`rgb_matrix_mode_noeeprom` on
  the current mode), so an effect that updates only some LEDs per frame
  (Jellybean Raindrops) is dark at once on the off stages and whole again on
  the wake, and the dim cap reaches every LED. The driver's scale still does
  the dark; the restart only makes every LED pass through it.
- **The dim** caps the lighting's value through `rgb_matrix_hsv_to_rgb()`:
  every effect enabled here converts through it (JD's Alphas Mods included,
  codex confirmed). DRIFT caps its authored palette, then applies the user's
  brightness, so its dim is not exactly "brightness capped at 25%" when that
  brightness is already low: accepted for a custom effect JD does not use.
  The claim is "keeps the hue on the effects JD uses", not "on every effect".
- **Overlay texts fit the 12-character slot:** `Full power`, `Normal power`,
  `Power saver`; the low-battery notice `Saving power`.
- **The low-battery cap** keeps the selected mode, is battery-only, and holds
  through keypresses (it is a ceiling, as now). Its notice waits for the
  screen: shown at the first keypress after the cap engages if the backlight
  was off (codex should-fix 12). It re-arms after a real charge session
  (`session`), the warning's rule.

### 6. Not in this flash

Light sleep (step 4), the reserve (step 5), the radio and deep sleep (step
7), a capacity that learns, time left on the dashboard.

## Validation (revision 2)

**On the host, before the flash (codex must-fix 8):**
- `scripts/battery_sim`, the firmware's `battery.c` and `power.c` built on
  the Mac:
  - the count's arithmetic: elapsed-time integration with an uneven tick,
    the remainder, the 32-bit timer's wrap, FULL and the re-seat rebasing;
  - the band: both edges at a flat and a steep stretch, the clamp, a held
    count discarding its energy, the ratchet rebasing it;
  - 0%: the count stopping at 1% before the cut; the cut setting 0; time
    left gone after it;
  - `5C` faults: bursts, a sustained +-15 mV bias, an excursion, stale and
    recovered reports, clamp crossings both ways;
  - load steps: the ladder's stages near each flat stretch and near the cut;
  - partial charges, the unplug's relaxation, an UNKNOWN session, a reboot
    on battery;
  - a model error and a capacity error, each alone and together (+-5%,
    +-13%): the replay's numbers at one-second steps;
  - R3's and 2e's logs replayed: within ±3 points of the truth above the cut
    at the true model and capacity, and the band's worst case reported at the
    wrong ones;
  - time left: maturity, the frozen average on USB, the display strings;
  - the log v6 round trip, and every existing scenario unchanged or updated
    for v6 on purpose.
- The modes: every stage through every enabled effect is a firmware matter
  the sim cannot render, so it is checked on the board (below).

**On the board, after the flash:**
- **The forced-stage load map** (`HC_DEVHOOK` op 14): every stage at JD's
  lighting and at a coloured and an animated effect, the model's `P_mW`
  (`HC_POWER`) against the logger: the model within 5% in each, at the top
  of the pack now, and again mid-drain and low.
- **Each enabled effect through dim, off, the cut and the wake,** looked at
  on the panel and the LEDs (a short script cycles them; JD's eyes or a
  photo for the look, the logger for the dark).
- **No lost keys across the transitions:** the wake rig (relay 1 presses F19)
  through every stage and back, `keydrop_test.py`'s three counts (sent,
  board, host) and the UART and TX loss counters, not only the 25 ms stall
  count (codex).
- **R5: a drain in Normal at JD's daily use**, from a verified FULL, the
  logger on: the gauge against the logged energy (target ±3 points above
  the cut, battery reboots excepted); the log's modelled energy against the
  logger per 15 min; the endpoint from the protection's latch (with the RGB
  already off, the logger may see no step). The runtime against R3 is
  reported as **an uncontrolled comparison**, not gate 5: different days of
  use, firmware and an outage. Gate 5 proper needs a repeatable activity
  schedule (the wake rig can type one), left for after R5.

## Questions put to the review (revision 1)

1. The band's rule (wait below, ratchet above, never rise): is the
   asymmetry acceptable for a first build, or should the count be allowed to
   creep up toward the band's lower edge?
2. The model's structure: anything on this board's draw it misses (the
   radio's states, the panel's drawing, the CPU's load, temperature)?
3. Taking `vdd_min` and lengthening the period for log v6, against RAM.
4. The `rgb_matrix_hsv_to_rgb()` override: does every effect go through it?
   Any that compute their RGB directly?
5. The modes' persistence and the keymap binding, given VIA's stored keymap
   overrides the default one.
6. Anything here that could stall the main loop or cost a keystroke.

## Codex's plan review (10-18) and what changed

[Verbatim](review-codex-phase2-plan-2026-10-18.md), prompt
[`review-prompt-phase2-plan.txt`](review-prompt-phase2-plan.txt). The
approach held; eight must-fixes, all taken.

| # | finding (short) | disposition |
|---|---|---|
| 1 | **must fix:** a symmetric band is not ±12 mV through a nonlinear curve; the clamp's saturated 900 is no upper bound | **Fixed:** the band is the curve at `V − 12` and `V + 12`, ±15 pm; at the clamp the only bound is "at least 90%". |
| 2 | **must fix:** "0% is the cut" needs a rule: the band reaches 0 above 3400 mV and stays above 0 at it; only the critical zeroes set 0 today | **Fixed:** the count stops at 1%; the cut's latch sets 0; after it `Low` and no time left. |
| 3 | **must fix:** QMK's CIE curve: values 64 and 128 are drives of 12 and 48, so the RGB table was in the wrong units | **Fixed:** the model is in buffer units (linear: ~9.5 mW + 0.70 per unit), refitted on tonight's map, which reads the board's drive; the measurements section corrected. |
| 4 | **must fix:** the HSV hook does not reach LEDs an effect does not rewrite (Jellybean Raindrops), and the driver's scale only applies when an LED is set; DRIFT's dim differs | **Fixed:** every stage change restarts the effect (no EEPROM); DRIFT's difference accepted and the claim narrowed; each enabled effect checked on the board. |
| 5 | **must fix:** a 1 Hz sample of the render buffer is not an energy meter for animated effects | **Fixed:** time-weighted sums of the committed frames at each flush, outside the masked copy. |
| 6 | **must fix:** elapsed time, not ticks; no debt across a hold; `e_used` apart from the level; the relaxation's energy; signed and wide arithmetic | **Fixed:** all in section 1. |
| 7 | **must fix:** `HC_CONN` is full; the v6 log's consumers (the sim's reader and tests, the trial's wait, `battery_fit.py`) | **Fixed:** `HC_POWER` (0x0F); every consumer listed in section 4. Giving up `vdd_min` and the 15-minute period: codex agreed. |
| 8 | **must fix:** the replay is not firmware evidence; the sim lacked faults, load steps, reboots, integer and wrap cases, and rendering; keys need the rig, not only the stall count | **Fixed:** the validation section rewritten. |
| 9 | should fix: the average's seed, maturity, USB, `<1h`, the cut; `100% 51h` needs 8 cells | **Fixed:** section 2. |
| 10 | should fix: the model is calibrated for stated conditions; map the stages at three pack voltages and with coloured and animated effects | **Agreed:** stated in section 1; the forced-stage map at the top, mid-drain and low. |
| 11 | should fix: R3 is no matched control for gate 5; endpoint from the latch; R5 held out | **Agreed:** R5 validates the gauge and the model; the runtime comparison is reported as uncontrolled; gate 5 waits for a scripted schedule. |
| 12 | should fix: the overlay is 12 characters; the low notice can go unseen; re-arm policy; read the bindings back | **Fixed:** shorter texts, the notice deferred to a keypress, re-armed by a real charge; the keymap read back after the restore. |
| — | note: keep "never rises on battery" for a first build; the asymmetry is acceptable if the claim is scoped | **Agreed.** |
