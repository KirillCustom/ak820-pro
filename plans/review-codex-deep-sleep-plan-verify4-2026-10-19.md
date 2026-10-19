**No remaining build blockers found in revision 5.** X1–X5 are resolved at the plan level.

| Finding | Verdict | Plan and affected code |
|---|---|---|
| **X1 — must-fix: log boundaries** | **Resolved.** Count boundaries through reconciliation; the first closing period always retains its frozen contents. Merge everything only with zero boundaries. | [§6:451](/Users/jdlien/code/ak820-pro/plans/DEEP-SLEEP-BUILD-PLAN.md:451); [battery.c:1357](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/battery.c:1357), reset at 1382. |
| **X2 — should-fix: repaint scheduling** | **Resolved.** Explicit invalidation, per-pass incremental painting, 500 ms release and the earlier gauge step address both delays. | [§4, step 9:324](/Users/jdlien/code/ak820-pro/plans/DEEP-SLEEP-BUILD-PLAN.md:324); [display.c:1809](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:1809), [cadence:3001](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:3001). |
| **X3 — note: admission timestamp** | **Resolved.** The timestamp precedes keyboard processing; housekeeping accounting is explicitly excluded. | [§4, budget:363](/Users/jdlien/code/ak820-pro/plans/DEEP-SLEEP-BUILD-PLAN.md:363); [main.c:48](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/quantum/main.c:48), [health.c:138](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/health.c:138). |
| **X4 — note: acquisition safeguards** | **Resolved.** Final-read equality remains; reacquisition retains the original timeout, checked even while deferred. | [§4, step 4:296](/Users/jdlien/code/ak820-pro/plans/DEEP-SLEEP-BUILD-PLAN.md:296); [rtc.c:1469](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1469), confirmation at 1481. |
| **X5 — note: delay-hook guard** | **Resolved.** The new hook is explicitly dev-only behind the existing guard. | [Validation 4:648](/Users/jdlien/code/ak820-pro/plans/DEEP-SLEEP-BUILD-PLAN.md:648); [hid_protocol.c:913](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/hid_protocol.c:913), hook target [rtc.c:1479](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1479). |

**Step-number audit:** Every numbered reference in §§4–7 and validation matches the swap: gauge/log and pending-clear **3**, acquisition and discipline suspension **4**, radio **5**, display hold **9**, input gate **1**. Historical disposition numbering is appropriately retained.

Answers to the fourth-verify questions:

1. **Yes, W2 is settled.** For sleep 890–899 and reconciliation at 901, the 900 boundary closes with frozen contents plus nine slept seconds; live 899–901 observations/energy go into the next period. The explicit “first period … always” rule and example establish precedence over the generic recovery-boundary wording.
2. **No new blocker from the swap or repaint scheduling.** Step 3 depends only on step 2’s duration. The discipline remains suspended through step 4’s completion or abort, before restarting with a fresh trim window.

**Implementation note:** Service the display hold’s completion/deadline while acquisition is running, as step 9 specifies. Its accelerated painting must still yield on I2C passes under §4’s shared budget.

I found no additional specified path causing lost keystrokes, unavoidable ≥25 ms passes, loss of all wake sources, log/clock corruption or shipping dev hooks. Actual timing remains for the planned hardware validation.

Repository-only static inspection; no changes, builds, tests or project scripts run.