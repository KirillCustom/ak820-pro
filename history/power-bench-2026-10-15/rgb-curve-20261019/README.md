# The RGB curve, and the model per colour (00:30-01:22 10-19)

`scripts/current_map.py --set rgb-curve` on `53afebd553`, relay 2 off (the
pack), the screen at JD's level 5: solid white at 16 values from 255 to 3,
then red, green and blue alone at 255, 128 and 16, every step held 60 s and
run in both directions, each beside the board's own LED drive (per mille,
from the committed frames) and its modelled draw (`HC_POWER`). Raw:
`steps.csv`, `summary.txt`.

**White: today's model is within 0.7% at every value** (one `B` for every
channel, `battery.c`). **Single colours are not:** red at 255 reads 7.7%
under, blue 7.2% over, green 4.2% over. Red draws more per unit of drive than
green or blue.

**The fit per colour** (`scripts/rgb_fit.py`): P = P_off + A + B_r S_r +
B_g S_g + B_b S_b, with S a colour's channel sum in buffer units (QMK's CIE
curve already applied) and P_off the map's RGB-off steps (119.0 mW):

| | A (lit) | B red | B green | B blue | (one B today) |
|---|---|---|---|---|---|
| | 7.71 mW | 3.636 | 2.533 | 2.294 uW/unit | 2.857 |
| weight to the mean | | 1.289 | 0.898 | 0.813 | |

**Every step within 0.6%**, white and the single colours alike (the colours
add: white is their sum within the fit). Out of sample, the stage map's
purple (`../stages-20261019/`) fits within 0.2%, against +1.8% today.

**For the firmware:** three channel sums in the committed-frame accounting
(`sn32f2xx.c` sums r+g+b as one), weighted once a second in `model_nj()`,
and A refitted against the model's own base (this fit's intercept is against
the measured RGB-off, 0.9 mW above the model's). It matters only for
coloured effects: JD's white is already within 0.4%. Small; for a flash that
is happening anyway.
