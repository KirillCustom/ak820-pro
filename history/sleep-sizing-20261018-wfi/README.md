# Light sleep's sizing, the WFI steps (18:53-19:03 10-18, JD at hand)

`scripts/sleep_sizing.py --masks 0,6:2,7:2,15:2,0` on `f38bb1e8a4`, as the
run without WFI ([`../sleep-sizing-20261018-nowfi/`](../sleep-sizing-20261018-nowfi/README.md)).
The 30 s plain-WFI probe kept USB up; so did every step. Raw: `sleep-sizing.json`.

| mask | what | pack current | WFI/s | ISR | presses | UART overruns |
|---|---|---|---|---|---|---|
| 0 | the plain firmware | 27.91 mA | 0 | 3,882/s, 72.7% CPU | 400/400 | 0 |
| 6 | yield + WFI | 27.08 mA | 2,207 | 3,879/s, 72.8% | 400/400 | **5** |
| 7 | + scan-only ISR | **24.21 mA** | 14,269 | 1,481/s, 4.9% | 400/400 | 0 |
| 15 | + the 20 kHz tick stopped | **23.01 mA** | 1,553 | 1,495/s, 4.4% | 400/400 | 0 |
| 0 | the plain firmware again | 27.43 mA | 0 | 3,884/s, 72.8% | 400/400 | 0 |

**Light sleep is worth building: ~4.7 mA of the ~27.7 mA dark draw (17%)**
with everything on (mask 15), ~3.5 mA without stopping the tick. WFI alone
saves ~0.6 mA: the full-rate row ISR keeps the core 73% busy, so it has
little time to halt; the scan-only ISR (4.4-4.9% of the CPU) is what gives it
that time, and the 20 kHz tick (14,269 wakes/s at mask 7) what takes most of
it back. Every press counted at every step, no stall >= 25 ms, row gaps
5-7 ms, ACK timeouts 5-7 per run as at mask 0, timebase 0.9999 of the wall.

⚠️ **Mask 6 had 5 UART receive overruns** (hardware, `LS.OE`): the CH582F's
bytes lost at the UART, in that configuration only (the full-rate ISR with
the yield and WFI); none at masks 7 and 15, none in the run without WFI.
Unexplained; repeat it before a real build, and soak the real rung with the
UART error counters gated.

Not measured yet: bit 4 (PMU Sleep around the WFI), which needs the data cut
(the transceiver turns off), against a matched data-off baseline.

What it is worth (an estimate from these numbers, not measured over a day):
the dark stages are most of a day's idle time (A8: idle >= 5 min is 88% of
the time), so ~4.7 mA there is ~66 mAh over a 14 h night, ~1.6% of the pack.
