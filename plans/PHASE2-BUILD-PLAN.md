# Phase 2, the build: an energy gauge, time left, and the ladder's modes

Status: **draft for codex's review (10-18); build authorized.** The order
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
  in white 188.7 at 255, 43.0 at 128, 17.9 at 64: **not linear in the
  value**, and 255 far above the line through the others. USB data adds 7.1
  (R2: 1.7 mA). The RGB curve is being mapped at 14 values plus red, green
  and blue alone (`current_map.py --set rgb-curve`, scheduled 00:30 10-19).
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

## The design

### 1. The energy count (battery.c)

**The model.** Each second on battery, the board's draw from what it knows
it is doing:

    P_mW = P_BASE + P_BKL(effective backlight level) + P_RGB(LED buffer) + P_USB(data attached)

- `P_BASE` 106.9 mW (R1's 114.0 less R2's 7.1 for USB data).
- `P_BKL` = 39.1 x `bkl_duty[level]` / 48, from the effective (capped) level.
- `P_RGB` = the sum over the LED buffer of a per-value power table `f(v)`,
  per channel (the buffer is after the power cap, so it sees every cap).
  The table's shape comes from tonight's map; until then R1's three white
  points. Computed once a second where `led_pm` is now (the same loop over
  the buffer, with a lookup).
- `P_USB` 7.1 mW while USB is configured on battery (the bench: the trial
  reads over USB with the 5 V cut), so bench runs model what they draw. Zero
  in real use, where USB means USB power.

**The count.** `acc_mws += P_mW` each second; each time it passes
`E_PM_MWS` (the energy per per mille: 15,579 mWh x 3.6 = 56,084 mW.s) the
level drops 1 pm. At JD's 306 mW that is 1 pm per 183 s, which is today's
clamp countdown (`COUNTDOWN_S_PER_PM` 184): the count generalizes it to every
level and every load, and replaces it.

**The voltage's say: a band.** For a fresh estimate below the top clamp at
voltage `V`: `L_v = curve(V)`, `U = (curve(V + 12) − curve(V − 12)) / 2 + 15`
pm (12 mV is `5C`'s accuracy from the meter checks; 15 pm a floor for noise).
U is wide on the flat stretches (the count leads there) and narrow where the
curve is steep (the voltage leads):
- **count above `L_v + U`:** the level steps down toward `L_v + U` by the
  existing ratchet (`STEP_DOWN_MEDIANS` fresh estimates in a row, at most 1%
  a step).
- **count below `L_v − U`:** the count **waits** (stops falling) until the
  voltage comes down to it. The level never rises on battery.
- **at the top clamp** (`5C` 100): the only bound is "at least 90%", as the
  countdown's floor is today.
- **0% stays the RGB cut** (3400 mV), and the protection (warn, cut, the
  critical zeroes) is unchanged.

**Anchors.**
- **FULL** sets 1000 (as now); the count runs from there.
- **The re-seat after a partial charge:** today it adopts `curve(V)` outright
  after `RELAX_S`, which on the flat stretches is the inaccurate number. Now:
  the charging model's level is kept if it is inside the relaxed estimate's
  band, else it moves to the band's nearer edge (the one upward move on
  battery stays allowed here).
- **A boot on battery with no saved level** (a reboot, not a flash: a flash
  is on USB): the first estimate's `curve(V)`, as now. Its error is the
  voltage's (up to ~±12 points on the flat stretches), and the count runs
  from it. The saved level is still written only in the 90-100% band
  (flash writes blink the LEDs); extending it is left out.

### 2. Time left

`P_avg`: a moving average of `P_mW`, time constant 12 h (each minute,
`avg += (P − avg) / 720`, in fixed point), started at the first second's `P`.
It is the draw at JD's recent pattern (the ladder makes idle cheap, so the
draw right now would swing ~3x between typing and idle). **Time left =
level x `E_PM_MWS` / `P_avg`**, on battery only.

Shown on `Fn`+`D`'s camera page, the level line: `58% 31h`, `85% 4d` from 100
h up (7 cells, the line's grid). Over raw HID in the battery status (a
protocol version bump), with `P_mW` and `P_avg`, for the host tools and the
trial. The dashboard's battery row is unchanged for now; whether it should
show time left is JD's call once it has proven itself.

### 3. The log, v6

RAM is the constraint: `log_buf` is 12,960 bytes (720 x 18) of 32 KB, and
the heap above `.bss` has 2,728 bytes. So no entry grows. v6:
- `vdd_min` gives way to **the period's modelled energy** (0.1 mWh, u16; 51
  mWh at 306 mW over 10 min). VDD's minimum was the USB/battery evidence
  before the supply flags; `vdd_avg` and the flags remain.
- **The period goes from 10 to 15 minutes:** 720 entries hold 180 h, since a
  drain with the ladder may run 4-5 days, past today's 120 h. The host reads
  the period from `HC_BATTCFG` already.
- `ak820battery.py` and `scripts/test_battlog_decode.py` learn v6.

### 4. The modes on `Fn`+`B` (power.c), the agreed step 3

As `BATTERY-GAUGE-PLAN.md`, "The proposal", with the ladder `power.c`
already holds (compiled out today):

| | Full Power | **Normal** (default) | Power Saver |
|---|---|---|---|
| RGB to 25% | never | 1 min | 30 s |
| RGB off | never | 5 min | 2 min |
| screen capped at level 4 | never | 5 min | 2 min |
| screen backlight off | never | 15 min | 5 min |

- **On battery only;** on USB every mode is Full Power. Radio and deep sleep
  are not in this build.
- **The dim through the lighting's own value, not the driver:** the board
  overrides QMK's weak `rgb_matrix_hsv_to_rgb()` to cap `hsv.v` at 64 in the
  dim stage (a cap: a lower value is left alone), so purple stays purple. The
  off stage keeps the driver's scale at 0.
- **Every stage stays a cap**, never a write to the user's settings
  (lighting, backlight level).
- **A low-battery lighting cap:** below 15% the RGB and the screen follow
  Power Saver's stages whatever the mode, and the display says so once. The
  RGB cut at 0% is unchanged.
- **The mode** cycles on a new keycode (`AK_PWR_MODE`, appended to the enum
  and `via.json`), shown as an overlay ("Power: Normal"), persisted in the
  board's EEPROM config, Normal after a flash (the EEPROM is erased) and the
  overlay says so once. Bound on `Fn`+`B` on both Fn layers in the default
  keymap; JD's stored VIA keymap needs the same binding written (over VIA's
  dynamic keymap, hands-free, after checking what `Fn`+`B` holds now).
- **The model sees all of it**: `P_RGB` reads the buffer after the caps and
  `P_BKL` the effective level.

### 5. Not in this flash

Light sleep (the agreed step 4), the emergency reserve (step 5), the radio
and deep sleep (step 7), a capacity that learns (the −13% case above), and
time left on the dashboard. Light sleep comes next, measured first.

## Validation

**On the host, before the flash:**
- `scripts/battery_sim` (which compiles `battery.c`) gets the count, the band
  and time left, replayed on R3's and 2e's logs at one-second steps from the
  10-minute entries: **within ±3 points of the truth everywhere above the
  cut on both**, and the existing simulator cases unchanged.
- A mode sweep: every stage change sets the caps it should, on battery only;
  a keypress restores all within one 10 Hz tick; the val cap never raises a
  value.
- The log's v6 encode and decode round trip.

**On the board, after the flash:**
- A short load map with the ladder's stages forced (dev hook) against the
  model's `P_mW` (raw HID) and the logger: **the model within 5% in every
  state**.
- **R5, now a drain in Normal at JD's daily use**, logged (relay off, the
  trial, the guard): the gauge against the logged energy (target: within
  ±3 points); the v6 log's modelled energy against the logger per 15 min;
  the runtime against R3, the matched baseline (same settings, same use,
  no ladder), which is gate 5. Expected ~4-5 days by the model.
- The usual: no pass ≥ 25 ms, the stage changes and the overlay tested with a
  track playing.

## Questions for the review

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
