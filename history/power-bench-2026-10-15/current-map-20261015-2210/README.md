# R1: the per-load current map at the top (2026-10-15, 22:10-22:40)

`scripts/current_map.py` on flash 2f, the board on battery (relay 2 off, USB
data attached, BT linked), pack 4.155-4.180 V (`vcell_mv`), INA228 die
23-24 °C. Ten states forward, then backward, 90 s each; each state's current
is the INA228's charge register over the last 75 s. Lighting over VIA's
channel (`_noeeprom`: the flash counters confirm no lighting write), the screen
over `HC_DEVHOOK` ops 4/5. JD's lighting and screen were restored after,
and the relay switched back on. Files: `map.log`, `steps.csv`,
`summary.txt`, `start-state.json`; the 1 Hz rows are in `../power.csv`.

| state | pass 1 | pass 2 | mean |
|---|---|---|---|
| JD's (effect 2, sat 0, val 255; screen 5) | 73.36 | 73.71 | **73.53 mA** |
| RGB off (screen 5) | 28.19 | 28.39 | **28.29** |
| white 255 (screen 5) | 73.42 | 73.68 | 73.55 |
| white 128 | 38.52 | 38.65 | 38.59 |
| white 64 | 32.52 | 32.64 | 32.58 |
| screen max (RGB off) | 36.65 | 36.75 | 36.70 |
| screen 12 | 29.68 | 29.81 | 29.74 |
| screen 4 | 28.08 | 28.13 | 28.10 |
| screen 1 | 27.51 | 27.54 | 27.52 |
| screen off (backlight) | 27.32 | 27.33 | **27.33** |

- **RGB at JD's setting: 45.2 mA, 62% of the total.** At brightness 128 it is
  10.3 mA (23% of full), at 64 4.3 mA: the LED current falls much faster
  than the brightness value. JD's effect at saturation 0 draws the same as
  plain white.
- **The screen's backlight: ~9.4 mA at full, linear in its PWM duty**
  (`bkl_duty[]`/48: level 12 is 12/48, measured 2.41 against 2.34; level 4,
  0.77 against 0.78). JD's level 5 is ~1.0 mA, 1.3% of his total.
- **The floor, every light out: 27.3 mA** (the MCU, which never sleeps; the
  CH582F and its BT link; the panel's drawing; USB data attached).
- The second pass reads 0.03-0.3 mA higher, as the pack fell ~10 mV: a
  constant-power load draws more current as the voltage drops.
- Mean of the 1 s samples and the charge register agree within 0.06 mA in
  every state.
