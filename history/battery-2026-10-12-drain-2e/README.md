# Drain on flash 2e (2026-10-12 → 10-14)

Gate 8's last per-event checks ("Battery low" at 3680 mV and the RGB cut at
3400 mV) on flash 2e (`efd5b0f74a`, token `0x71163528`), and a repeat of the
whole-cycle accuracy check against the runtime line. **Both checks passed;
Phase 1b is closed** (`plans/BATTERY-GAUGE-REFINE-PLAN.md`, "Gate 8 MET").

- **FULL** came at ~01:41 10-12 (the log's first entry with the charger done
  on USB), ~9.3 h after the relay went on at the 10-11 cut. It caused no stall:
  `count_ge_25ms` 0 over the 16 h since the flash; the battery repaint's worst
  5.3 ms (display probe).
- **Unplugged** through relay 2 at 11:34:16 10-12, data left on. `bench_trial.py
  --wait-cut --poll 300 --full` read every 5 min to the cut, dumped the log,
  then charged to FULL and left the relay on (`drain.csv`, `drain.log`).
  `scripts/trial_guard.py` guarded it from 12:19 10-12 and took the
  per-event reads right after the trial's polls (`guard.log`).

## Results

- **"Battery low": PASS.** Fired between the 08:35 and 08:40 reads 10-14
  (gauge 10.0 → 9.8%, pack 3679 → 3676 mV). At 08:40:13: `count_ge_25ms` 0
  since the flash (uptime 220,089,370 ms), loop gap max 21 ms (blit); the
  display probe's worst 12.45 ms (the text slot) over the 5 minutes holding
  the warning (`guard-warned-20261014-084013.txt`).
- **The board was off twice on 10-14, under ~13 minutes in all.** JD tried
  the INA228 harness, built on a 1.25 mm extension; the pack's connector is
  1.0 mm, so it did not fit (`docs/battery.md`, "The pack connector"). The
  log was dumped first (`log-20261014-0950-preswap.csv`, the whole drain to
  09:50). Off from ~09:52 (the 09:55:12 read failed) to before the 10:00:12
  read, then again for part of 10:00:12-10:04:53, when it booted (uptime
  930,341 ms at 10:20:23; the 10:05:12 read had no `5C` yet). The exact
  length is unknown. The trial carried on and the gauge came straight back on
  battery. The INA228's recorder saw no pack voltage or current then: nothing
  made contact.
- **The RGB cut: PASS.** Fired between the 13:55:17 read (3399 mV, lights
  on) and the 14:00:17 read (`lights_cut`). At 14:00:20, before the relay went
  on: `count_ge_25ms` 0 (non-flash 0) since the 10:04:53 reboot, loop gap max
  18 ms (blit); the probe's worst 12.28 ms (the text slot) over the ~5
  minutes holding the cut (`guard-cut-20261014-140020.txt`).
- **The runtime: 50.35-50.43 h** from the unplug to the cut, wall clock;
  50.1-50.4 h under load once the gap is taken out. Last cycle 51.13 h (FULL
  to the cut), run 2 50.56 h.
- **Accuracy** against run 2's line (100% less 1.978%/h), 603 reads:

  | band (line) | reads | mean | range |
  |---|---|---|---|
  | above 85% | 91 | −0.6 | −2.8 to +0.4 |
  | 70-85% (the flat top) | 91 | −5.1 | −7.2 to −1.3 |
  | 52-70% | 110 | −2.4 | −5.1 to −1.0 |
  | below 52% | 311 | −1.6 | −4.6 to −0.2 |

  Within −2.0 below 30%. Against this run's own line (50.39 h to the cut's
  midpoint) much the same: flat top −7.2, below 52% worst −4.4. The late reads
  carry ≤ 0.4 points from the off-time.
- **The recharge from the cut:** relay on 14:01:17; the charger stopped
  between the 23:06:39 read (97.4%, charging) and the 23:11:39 read (100%),
  ~9.1 h. FULL a 2.6-point step; LOST, OVERRUN and self-check 0 in all 80
  entries. FULL logged 23:16:39, the log dumped, the relay **left on**. The
  trial ended `done` at 23:26:39 and the guard at 23:26:41.

## Files

- `drain.csv`, `drain.log`: every read, 11:34 10-12 to 23:26 10-14.
- `log-20261012-1133-full.csv`: the charge to FULL before the run.
- `log-20261014-0950-preswap.csv`: the drain to 09:50 10-14, dumped before
  the harness attempt.
- `log-20261014-1400-rgbcut.csv`: 10:14 to the cut (since the reboot).
- `log-20261014-2326-full.csv`: 10:15 10-14 through the recharge to FULL.
- `guard.log`, `guard-start-*`, `guard-warned-*`, `guard-cut-*`: the guard's
  reads (stall counters and the display probe).
- `*-start-*`: the snapshot at the start.
