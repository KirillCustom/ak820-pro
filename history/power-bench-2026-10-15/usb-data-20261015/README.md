# R2: USB data attached against untethered (2026-10-15, 22:41-22:57)

On battery (relay 2 off), JD's settings (73.5 mA in R1), the Rust daemon
running as usual. USB data on for 5 min, off for 5 min
(`uhubctl -l 0-1.4 -p 2 -a off -e`, port status `0000 off`), on again for 5
min. Each segment is the INA228's charge register, less its first 30 s.
`r2.log` has the moments; the 1 Hz rows are in `../power.csv`.

| segment | current | vcell |
|---|---|---|
| data on (A1) | 73.75 mA | 4153 mV |
| data off | **72.15 mA** | 4148 mV |
| data on (A2) | 73.99 mA | 4143 mV |

**USB data attached costs ~1.7 mA (2.3%) at this load**, the USB link and
the host's traffic together (the daemon's now-playing and clock; no trial
was polling). Every bench drain carries it, so a bench runtime runs ~2%
short of real untethered use at JD's settings. Model it as a current, not
as hours (codex 7); one pass only, at one load and voltage. `uhubctl` took
~15 s each way and restored data cleanly.
