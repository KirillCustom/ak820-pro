# Light sleep's sizing, the steps without WFI (18:26-18:36 10-18)

`scripts/sleep_sizing.py --masks 0,1,3:2,3:4,0` on `f38bb1e8a4`, relay 2 off
(the pack, USB data up), the stage forced to `SCREEN_OFF`. Per step: 20 s
settle, 60 s of the logger's charge register, then 400 presses through the
wake rig (200 at 40/60 ms, 200 at 25/40 ms) counted at the board. Raw:
`sleep-sizing.json`.

| mask | what | pack current | presses | raw edges | row gap (entry) | ACK timeouts |
|---|---|---|---|---|---|---|
| 0 | the plain firmware | 27.23 mA | 400/400 | 791/800 | 7 (5) ms | 6 |
| 1 | scan-only ISR | 28.06 mA | 400/400 | 795/800 | 5 (8) ms | 5 |
| 3, 2 ms | scan-only + yield | 28.63 mA | 400/400 | 800/800 | 7 (5) ms | 6 |
| 3, 4 ms | scan-only + yield | 28.74 mA | 400/400 | 800/800 | 5 (5) ms | 6 |
| 0 | the plain firmware again | 27.64 mA | 400/400 | 798/800 | 5 (9) ms | 7 |

**Without WFI the steps cost current, not save it**: up to ~1 mA over the
plain firmware's readings on either side (the two mask-0 readings bracket the
run's drift, 0.4 mA). The CPU never stops at 48 MHz; what the lighter ISR and
the yield free goes to more main-loop passes or the idle thread's own loop,
which draw a little more. Any saving needs the core halted: the WFI steps
(bits 2 and 4), run with JD at hand. **No press was lost at any step**, the
scan-only ISR's ~3.9 ms per key included; no stall >= 25 ms; no link
give-ups or UART errors.

The script's verdict was FAIL on two criteria that were tighter than the
plain firmware itself (raw edges = 2n, the known short-count quirk; row gap
<= 6 ms, mask 0 reached 7). Both loosened to the plain firmware's
(`sleep_sizing.py`, after this run).
