**One must-fix remains before building: W2’s rules conflict when sleep crosses no boundary but recovery crosses one.** W4 also has a scheduling caveat. This was read-only inspection; no files changed, builds or project scripts run.

`kb/` below means `qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/`.

| Finding | Revision 4 disposition | Plan and affected code |
|---|---|---|
| **W1 — pass budget** | **Resolved in design.** Shared arbitration, 5 ms timeout, admission deadline and display deferral address the identified combinations. | [§4, budget](plans/DEEP-SLEEP-BUILD-PLAN.md:344); [kb/ak820pro.c:569](qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/ak820pro.c:569), [rtc.c:1429](qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1429). |
| **W2 — log reconciliation** | **Partial; must-fix** below. Closure suppression is correct; the combined boundary case remains contradictory. | [§6](plans/DEEP-SLEEP-BUILD-PLAN.md:438); [kb/battery.c:1357](qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/battery.c:1357), reset at 1382. |
| **W3 — acquired second** | **Resolved.** Matching the bracketed second, rejecting expired brackets and checking cancellation/timeout while deferred close the reported hole. | [§4, step 3](plans/DEEP-SLEEP-BUILD-PLAN.md:284); [rtc.c:1474](qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1474). |
| **W4 — repaint generations** | **Resolved as a deliberately relaxed policy**, with a should-fix below. Final DMA completion and timeout failure are explicit. | [§4, step 9](plans/DEEP-SLEEP-BUILD-PLAN.md:321); [display.c:1809](qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:1809), [2619](qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:2619). |
| **W5 — supply deadline** | **Resolved.** Three seconds is unconditional; fresh sampling continues. | [§4, step 7](plans/DEEP-SLEEP-BUILD-PLAN.md:314); [kb/battery.c:1456](qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/battery.c:1456). |
| **W6 — radio during input gate** | **Resolved.** Only polling/recovery I/O waits; parsing, ACKs and report transmission continue. | [§4, steps 1/5](plans/DEEP-SLEEP-BUILD-PLAN.md:263), [W6 disposition](plans/DEEP-SLEEP-BUILD-PLAN.md:777); [ch582f_ajazz.c:1159](qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:1159), RX at 1183. |

Answers to the four questions:

1. **Budget:** No remaining unavoidable ≥25 ms combination identified in the proposed recovery transaction scheduling. **Note:** “`loop_acct`’s stamp” needs correction: existing accounting closes intervals at [housekeeping entry](qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/health.c:138), after input processing. Establish the admission timestamp before [keyboard processing](qmk_firmware-ak820pro/quantum/main.c:48); do not restart it at housekeeping. Hardware validation still establishes actual timing.

2. **Log — must-fix:** Consider a period ending at 900 s: sleep starts at 890, wakes at 899, and step 4 runs at 901. The zero-boundary rule says merge frozen and live data; the recovery-boundary rule says close with **only the sleep remainder**. That latter rule would discard the frozen pre-sleep observations/energy in this case. Define reconciliation through **step-4 time**: the first closing period always retains its frozen contents plus its sleep share; live observations can follow the declared approximation into the current period. Merge everything only when no boundary occurred through reconciliation. This must cover both [direct `5C` intake](qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/battery.c:354) and [live energy](qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/battery.c:666).

3. **Acquisition:** Yes, the revised safeguard resolves W3. Retain the final confirming read’s equality check and the original overall timeout across reacquisition.

4. **Repaint — should-fix:** Generations establish which data was painted, but must also schedule the paint. Today a clean battery row waits for the next second; dirty components advance at 10 Hz ([display.c:3001](qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:3001)). A healthy seven-character repaint plus bar can exceed 250 ms. Explicitly invalidate and service the post-debit row, or adjust the deadline. Also, step 4 follows acquisition, so ignoring step 3’s generation does not itself avoid its 1.5 s delay.

I found no additional specified no-wake path or dev-hook exposure; retain the [HC_DEVHOOK guard](qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/hid_protocol.c:913) for the new delay hook too.