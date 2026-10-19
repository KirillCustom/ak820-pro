# The awake control for the deep-sleep soak (06:46-07:02 10-19)

`scripts/deep_cycles.py --board-only --awake --n 30` on `53afebd553`: the
soak's cycle never armed, so the board sits awake through the same silence
(the 6 s delay, 2 s to be seen asleep, a 5.5-23.5 s dwell; 13.5-31.5 s in all), then
the rig's F19, data on. A cycle passes if the logger shows it awake before the
tap, the tap counts exactly one press, and the link gives nothing up. Raw:
`deep-cycles.json`.

**30 of 30 pass.** The link: 8.43 frames and **3.30 ACK timeouts a cycle**
(39%), no give-ups, against 2.95 a cycle after a deep sleep. The comparison
is in `../deep-soak-20261019/README.md`.
