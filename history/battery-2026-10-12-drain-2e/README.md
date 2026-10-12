# Drain on flash 2e (2026-10-12 →)

Gate 8's last per-event checks ("Battery low" at 3680 mV and the RGB cut at
3400 mV) on flash 2e (`efd5b0f74a`, token `0x71163528`), and a repeat of the
whole-cycle accuracy check against the runtime line.

- **FULL** came at ~01:41 10-12 (the log's first entry with the charger done
  on USB), ~9.3 h after the relay went on at the 10-11 cut. It caused no stall:
  `count_ge_25ms` 0 over the 16 h since the flash; the battery repaint's worst
  5.3 ms (display probe).
- **Unplugged** through relay 2 at ~11:34 10-12, data left on. `bench_trial.py
  --wait-cut --poll 300 --full` reads every 5 min to the cut, dumps the log,
  then charges to FULL and leaves the relay on (`drain.csv`, `drain.log`).
- Files: `log-*-full.csv` (the charge to FULL), `*-start-*` (the snapshot).
