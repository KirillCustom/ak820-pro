# One hour of deep sleep (05:44-06:44 10-19)

`scripts/deep_sleep.py --cap 3700 --wait 3600` on `53afebd553`: relay 2 off,
light sleep off, the stage forced dark, armed with a 15 s delay, data off,
then the wake rig's F19 an hour after the logger saw it asleep. Raw:
`deep-sleep.json`, `run.log`.

**Pass.** Asleep at **3.17 mA** (awake 28.05 with data, 27.85 without) for
the whole hour; woke on the rig's key (P2.8), the key registered, no stray
wakes, the PLL back in 127 loops, USB back inside 20 s. The radio link
across the run: 6 frames, 3 ACK timeouts, no give-ups.

**The record counted 3139 s for 3600 asleep** (the logger's 1 s samples: the
current fell at 05:44:32-33 and rose at 06:44:32-33): **0.872 of real time.**
The 10-18 cycle run agrees (its record's seconds against the dwell fit a
slope of 0.842). Asleep, the RTC's second, clocked by the ILRC, comes ~13%
late; the awake clock is held by the SOF loop. The board woke ~461 s behind;
12 s later the daemon found it 1.6 s off, so something caught it between,
presumably the PCF's 2 s snap (not checked). The logger's temperature, beside
the pack, held 21.6-22.1 C. For the build: take the slept time from the PCF
(`plans/SLEEP-PLAN.md`).
