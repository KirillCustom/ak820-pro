# The ladder's stages against the model (02:25-02:49 10-19)

`scripts/current_map.py --set stages --hold 60 --settle 15` on `53afebd553`,
relay 2 off: each of Phase 2's four stages (active, dim, lights off, dark)
forced in turn at three lightings (JD's white, a saturated purple, the
animated cycle), down and back up, 60 s each after 15 s to settle. Raw:
`steps.csv`, `summary.txt`.

**The board's modelled draw is within 2% of the logger at all 24 steps**
(the target was 5%): worst +1.9% (purple, active) and -1.5% (JD's, dark).

| stage | JD's white | purple | cycle |
|---|---|---|---|
| active | 73.31 mA, 303.4 mW | 51.15, 211.9 | 51.78, 214.6 |
| dim | 32.61, 135.3 | 31.41, 130.3 | 31.39, 130.2 |
| lights off | 28.15, 116.8 | 28.37, 117.7 | 28.21, 117.0 |
| dark | 27.75, 115.2 | 27.48, 114.0 | 27.55, 114.3 |

The two passes agree within 0.6 mA everywhere. The cycle's active steps are
noisy (sd ~3.5 mA in the samples) because the effect animates; their means
still sit within 1.2% of the model. Purple's +1.9% is the per-colour error of
`../rgb-curve-20261019/` (red and blue weighted alike); the fit there takes it
to +0.2%.
