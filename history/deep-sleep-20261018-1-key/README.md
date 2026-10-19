# The deep-sleep experiment, runs 1-3 (19:12-19:17 10-18, JD at hand)

`scripts/deep_sleep.py` on `53afebd553` (the sleep build with USB judged by
its frame number and the flash blocker fixed; `f38bb1e8a4`'s first run at
19:06 never slept, blocked by both). The pack (relay 2 off), the stage forced
dark, USB data cut (`uhubctl`), `PMU_CTRL = 2` + WFI with interrupts locked,
the RTC second feeding the watchdog. Raw: `deep-sleep.json` in this folder,
`../deep-sleep-20261018-2-cap/`, `../deep-sleep-20261018-3-nofeed/`.

| run | wake | asleep | strays | PLL loops | waking key | USB after | current asleep (awake, data off) |
|---|---|---|---|---|---|---|---|
| 1, a rig press (F19, 40 ms) | **a key**: P2 bit 8, the rig's column | 19 s | 0 | 127 | **registered** (`key_gain` 1) | transceiver off, set back, frames again | **3.16 mA** (26.94) |
| 2, no press, cap 60 s | the cap | 60 s | 0 | 125 | - | as run 1 | **3.27 mA** (27.01) |
| 3, no press, the watchdog unfed, cap 30 s | the cap, **no reset** | 30 s | 0 | 125 | - | as run 1 | - |

**Deep sleep wakes on a key, and the waking key is not lost** (on the board:
whether it reached the Mac is unverified, the Mac being locked). **It draws
~3.2 mA against ~27 mA awake and dark: 88% less.** What is left is the
radio keeping its link, the regulator, the panel's logic and the rest
(unapportioned). The PLL relocks in ~125 loops at 12 MHz; the USB
transceiver bit is cleared by the hardware and setting it back is enough for
the host to come back.

**The watchdog does not count in Deep-sleep** (run 3: 30 s unfed, its
timeout ~12 s, no reset, the retained record empty), though the ILRC it runs
from kept the RTC counting. So asleep, only the RTC's wake can catch a hang;
a real build should not rely on the watchdog there.

Not yet: a P0 column (only the rig's P2.8 tested), the key's delivery to the
host, longer sleeps, the radio's behaviour over long silences.
