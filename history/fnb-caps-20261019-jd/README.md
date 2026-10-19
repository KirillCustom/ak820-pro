# Fn+B and the lock band (codex, the power-mode icons' review, note 5), 10-19

On the icons build `26fcafaeca`, on the pack. `scripts/fnb_caps_test.py`.

- **Control** (`../fnb-caps-20261019-control/`, 14:44): the Mac's Caps Lock
  toggled 199 times over 90 s, nobody at the board: 202 passes of 10 ms or
  more, **none of 25**.
- **The first window** (14:46-14:49, `run.log`): 397 toggles over 180 s;
  JD's presses never came (0 keys at the board). **One pass of exactly 25
  ms, attributed to a blit** (`blit_gap_max_ms` 25, `loop_gap_max_mark`
  blit): the CAPS paint, which the lock band's notes already named as the
  suspect for the passes of 10-17 and 10-18, caught at about one in 600
  toggles. Not the sleep's, not the icons'.
- **Fn+B without the Caps toggling** (15:33-15:38, `fnb-baseline.json`,
  `fnb-result.json`): JD's Fn+B presses (30 key presses at the board; his Fn
  is the Caps key's position, so the band's FN label changed around every
  one) gave **no pass of 25 ms or more**; the worst, 20-21 ms (`display_hk`
  13). Codex's case with Caps changing in the same pass is not separable from
  the CAPS paint's own 25 ms, which is its own item.
