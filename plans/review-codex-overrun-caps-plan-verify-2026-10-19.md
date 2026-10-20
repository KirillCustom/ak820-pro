**Revision 2 is substantially better, but several dispositions remain incomplete.** The main remaining problems are Fn+D ordering, partial-enqueue recovery, the burst’s timeout, and claims that the activity mask proves causation.

I read only inside the repository and made no changes. This is a source review of the plan; no build or hardware validation was performed.

1. **Must-fix — Fn+D must drain queued work before reevaluating locks, not literally alternate a stage and a glyph.**

   [Plan:306](/Users/jdlien/code/ak820-pro/plans/OVERRUN-CAPS-PLAN.md:306) removes the old pump deadlock, but “stage 5 advances only when both queues are empty” does not prevent that stage from **clearing a label while some of its glyphs remain queued**.

   For example: stage 5 queues CAPS; the pump submits C; Caps turns off; subsequent stage calls clear the padlock and CAPS slot; remaining queued letters paint afterward. The existing diff reads current lock state on every call and performs synchronous clears. [display.c:2076](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:2076), [display.c:2094](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:2094)

   Specify this scheduling rule:

   - Preserve `pass_deferred` above everything.
   - Restore stage 1 discards **both** queues before clearing.
   - After stage 1, pending glyphs take precedence: submit at most one, lock queue first, and return.
   - Only with both queues empty may another restore component run.

   That closes both the deadlock and stale-paint problem. Merely delaying stage advancement does not. [display.c:1360](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:1360), [display.c:1385](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:1385)

2. **Must-fix — “leave `shown_*` false” is unsafe after a partial enqueue.**

   [Plan:297](/Users/jdlien/code/ak820-pro/plans/OVERRUN-CAPS-PLAN.md:297) recreates the forgotten-pixels bug on overflow: if C and A fit but P and S do not, the accepted glyphs can paint while `shown_caps` remains false. If Caps turns off before retry, false→false skips the clear.

   Reserve capacity for an entire label and enqueue it atomically, retaining the previous shadow if rejected; alternatively, track an explicit dirty/unknown state that forces reconciliation even when the desired state becomes off. Capacity ten is sufficient for one complete lock-band batch, provided the scheduler prevents another batch from accumulating.

   Text needs an explicit retry mechanism too. `queue_line()` currently commits its shadow after insertion, while `draw_text_slot()` clears `text_dirty` before insertion. An overflow counter or invalid shadow alone does not guarantee another attempt. [display.c:2758](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:2758), [display.c:2914](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:2914), [display.c:2960](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:2960)

3. **Must-fix — extend the shared idle/discard contract to both queues.**

   The proposed housekeeping gate is **correct for normal Caps ordering**. Once it checks both queues, a Caps-off clear waits for pending label submissions; the clear itself waits for the last DMA. [Plan:315](/Users/jdlien/code/ak820-pro/plans/OVERRUN-CAPS-PLAN.md:315), [lcd_bus.c:207](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/lcd_bus.c:207)

   But the plan must also explicitly update:

   - `display_bus_idle()`;
   - `display_queue_discard()`;
   - wake-paint admission and completion.

   Currently these inspect or reset only the text queue. `display_bus_idle()` feeds both PCF admission and deep-sleep admission; wake repaint completion also depends on it. Otherwise a lock-only queue can be waiting while the system declares the display idle or the wake repaint complete. [display.c:2748](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:2748), [display.c:2845](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:2845), [display.c:3111](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:3111), [sleep.c:245](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/sleep.c:245)

   **`locks_forget()` placement is otherwise fine:** put it beside `shadow_invalidate()` in the discard/full-clear path. Do **not** put it inside `shadow_invalidate()` itself: forced text rendering calls that helper after other dashboard components may already have painted. [display.c:2976](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:2976)

4. **Must-fix — the burst must explicitly select the short timeout and enforce its transaction budget across the whole pass.**

   The revised admission and yields are good: scratch output, pending host write, every non-idle phase state, acquisition including its pending start, and the entire sleep recovery. These protect clock ownership. [Plan:140](/Users/jdlien/code/ak820-pro/plans/OVERRUN-CAPS-PLAN.md:140)

   However, copying `bus_admit()` does **not** select the recovery timeout. `pcf_read_raw()` normally uses **20 ms**; the recovery separately sets `pcf_short_timeout`. Consequently, the specified burst could take nearly 10 ms before admission plus 20 ms waiting, exceeding 25 ms before remaining housekeeping. [rtc.c:32](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:32), [rtc.c:259](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:259), [rtc.c:1806](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1806)

   Explicitly bracket burst reads with the selected 5/10 ms timeout and include stop/start overhead in the measured budget.

   Also make “one transaction per pass” concrete using the existing transaction accounting:

   - Reject a burst if an earlier main-loop handler already used the bus.
   - Suppress the later reference check.
   - Defer pre-sleep sampling if the burst already used this pass.

   The last case matters because `rtc_sleep_prepare()` can perform another read from `sleep_task()` at pass end; its admission currently has no “already used bus” test. [rtc.c:1636](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1636), [rtc.c:1675](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1675), [sleep.c:823](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/sleep.c:823)

5. **Must-fix — the activity mask identifies observation context, not an exclusive cause; sleep needs separate accounting.**

   The blanket statement at [Plan:126](/Users/jdlien/code/ak820-pro/plans/OVERRUN-CAPS-PLAN.md:126) is too strong. The cases differ:

   | Candidate | Does the relevant UART ISR run while the main-loop bit remains set? |
   |---|---|
   | **PCF transaction** | **Yes for an already-pending UART interrupt**, with the bit surrounding the API call. HAL unlocks before returning, so the caller has not cleared it. Byte-mode interrupts likewise run while the transaction bit remains set. [hal_i2c.c:203](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios/os/hal/src/hal_i2c.c:203) |
   | **Flash program** | **Yes on the current unlocked main-loop path.** Each busy window restores PRIMASK before the program operation returns. Preserve that ordering and the saved-PRIMASK semantics. [EFL:60](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios-contrib/os/hal/ports/SN32/SN32F290/hal_efl_lld.c:60), [EFL:332](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios-contrib/os/hal/ports/SN32/SN32F290/hal_efl_lld.c:332) |
   | **Flash erase** | **The bit can remain valid through delayed service**, but there is no erase PRIMASK window to “lift.” The SRAM routine leaves interrupts enabled; flash vector/handler fetch can stall until erase completes. The existing operation hooks surround the erase and wait. [EFL:392](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios-contrib/os/hal/ports/SN32/SN32F290/hal_efl_lld.c:392), [wear_leveling_efl.c:144](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/platforms/chibios/drivers/wear_leveling/wear_leveling_efl.c:144) |
   | **Deep sleep** | **Not for the receive blindness being investigated.** RX is disabled, then existing bytes/error status and the software queue are discarded before unlocking. An ISR afterward cannot reconstruct those losses. [sleep.c:335](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/sleep.c:335), [sleep.c:468](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/sleep.c:468) |

   Even for PCF, a USB/SysTick delay during an unlocked byte gap occurs while the PCF bit remains set. The mask therefore cannot distinguish that delay from the PCF’s own masking. Record candidate context and timing, retaining unknown/multiple-contributor interpretations.

   Record sleep entry/exit and RX-disabled intervals separately. An OE observed after RX reenable may describe the final wake interval; it does not count traffic lost during sleep.

   Similarly, **individual USB/SysTick maxima below 1 ms do not exclude them** as contributors. FIFO occupancy and consecutive delays matter. Their entry/exit timing also omits time waiting to enter the handler. Remove the exclusion rule at [Plan:138](/Users/jdlien/code/ak820-pro/plans/OVERRUN-CAPS-PLAN.md:138).

6. **Should-fix — specify the timing boundaries and CT16B5 limitations.**

   **CT16B5 is the right timer for short awake intervals.** It runs independently of interrupt servicing, at 187,500 Hz with 16-bit resolution: approximately 5.33 µs per tick and 349.5 ms per wrap. Use unsigned modular subtraction. [chconf.h:24](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/platforms/chibios/boards/SN_SN32F290/configs/chconf.h:24), [ST header:147](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios-contrib/os/hal/ports/SN32/LLD/SN32F2xx/SysTick/hal_st_lld.h:147)

   Specify that masked-span timestamps are captured **before unlocking**, and the completed span is published before the UART ISR can inspect it. Cover the first START chunk, final STOP, and error exits—not just byte bodies. Whole-call timing remains a separate measurement.

   USB and SysTick wrapper hooks are feasible without changing their dispatch. Use the ISR-safe counter directly, not `timer_read32()`, which takes a lock. [USB LLD:423](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios-contrib/os/hal/ports/SN32/LLD/SN32F2xx/USB/hal_usb_lld.c:423), [ST LLD:120](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios-contrib/os/hal/ports/SN32/LLD/SN32F2xx/SysTick/hal_st_lld.c:120), [health.c:89](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/health.c:89)

   CT16B5 **stops during deep sleep**. It cannot measure the full PRIMASK-held sleep interval or all clock-restoration time. Also, a modulo duration cannot establish an upper bound for an unbounded USB handler that runs beyond one wrap—or never returns. [sleep.c:348](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/sleep.c:348), [sleep.c:477](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/sleep.c:477)

7. **Should-fix — the four-arm experiment is good, but exposure and historical conclusions still need qualification.**

   **The four arms, randomized spacing, OE comparison, identical daemon policy, and released cable handle are sound improvements.** [Plan:162](/Users/jdlien/code/ak820-pro/plans/OVERRUN-CAPS-PLAN.md:162)

   The previous 250 ms observation remains unresolved: the script has one request outstanding and waits up to 250 ms on a loss. Several PCF reads can therefore occur with no injected traffic after each failed echo. Record request counts, actual traffic/timeout intervals, completed reads, and per-arm durations; use repeated arms or matched random schedules when comparing rates. Random spacing reduces phase locking but does not make exposure identical. [win_raw81_echo.py:74](/Users/jdlien/code/ak820-pro/scripts/win_raw81_echo.py:74), [win_raw81_echo.py:95](/Users/jdlien/code/ak820-pro/scripts/win_raw81_echo.py:95)

   The night after arm (d), if left in byte mode, measures the **post-change** base rate. It cannot by itself explain the old transaction-mode rate, especially if the suspected cause has been removed. Specify the overnight mode and soften “is what answers that.” A 32-entry ring also needs sequence/overwrite accounting; aggregate counters must survive ring replacement. [Plan:183](/Users/jdlien/code/ak820-pro/plans/OVERRUN-CAPS-PLAN.md:183)

8. **Note — stop/start and restart-error propagation are appropriate, with important factual qualifications.**

   **The stop/start repair is safe at the current transaction call sites.** HAL has unlocked before returning; the RTC callers are in main-thread context, pre-sleep sampling precedes the deep-sleep lock, and wake I2C occurs later in `rtc_fast_task()`. Both lifecycle APIs take their own locks, protecting the shared GPIO mode writes. Use the persistent `&i2ccfg`: `i2cStop()` clears `i2cp->config`. [hal_i2c.c:94](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios/os/hal/src/hal_i2c.c:94), [hal_i2c.c:130](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios/os/hal/src/hal_i2c.c:130), [sleep.c:773](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/sleep.c:773)

   Precisely what it does:

   - Fallback `i2c_lld_stop()` changes **no pins**.
   - Start sets SDA and SCL high and configures both as push-pull outputs in this board configuration.
   - It repairs driver state; it supplies no bus-clear clocks, verified STOP sequence, or guarantee that an externally stuck line recovers.
   - The existing `CHECK_ERROR` already forces output/high cleanup and converts negative errors to `MSG_TIMEOUT`. Thus this recovery also follows propagated NACK/arbitration failures, not just elapsed deadlines. [fallback LLD:40](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios-contrib/os/hal/lib/fallback/I2C/hal_i2c_lld.c:40), [fallback LLD:343](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios-contrib/os/hal/lib/fallback/I2C/hal_i2c_lld.c:343)

   Returning `i2c_write_start()`’s result from `i2c_write_restart()` is **correct**: the outer caller already checks it, so a failed repeated START no longer falls through into another byte. Preserve the original failure result and existing retry/backoff state; do not turn stop/start into a successful transaction or an immediate unbudgeted retry. [fallback LLD:143](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios-contrib/os/hal/lib/fallback/I2C/hal_i2c_lld.c:143), [fallback LLD:472](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios-contrib/os/hal/lib/fallback/I2C/hal_i2c_lld.c:472)

9. **Note — separate UART counters are feasible without changing parser behavior.**

   Increment a hardware-observation counter from the already-read `ls` at the OE reporting site, and a software-drop counter on the existing failed `iqPutI()` branch. Preserve all existing flag additions and consumers; add no extra LS/RB reads. This cleanly separates the two sources currently reported through `SD_OVERRUN_ERROR`. [serial LLD:249](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios-contrib/os/hal/ports/SN32/LLD/SN32F2xx/UART/hal_serial_lld.c:249), [serial LLD:293](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios-contrib/os/hal/ports/SN32/LLD/SN32F2xx/UART/hal_serial_lld.c:293)

   Call the hardware count **OE observations**, not lost bytes. Instrumenting only `set_error()` also inherits existing coverage limitations: the BI branch passes only BI, and LS is reread inside the drain without passing every snapshot through `set_error()`. Broader diagnostic coverage can inspect existing snapshots while leaving flag behavior unchanged. [serial LLD:282](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios-contrib/os/hal/ports/SN32/LLD/SN32F2xx/UART/hal_serial_lld.c:282)

The remaining factual corrections are small:

- **Separate queues need 64 text/clock entries plus ten lock entries.** Increasing the text queue to 74 is harmless extra capacity, but the old 74-entry arithmetic assumed one combined queue. [display.c:2674](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:2674)
- “Conditional throughout” is not quite true: the code comment still states a 1.4 ms FIFO budget unconditionally. Likewise “the row ISR is never held for ~3 ms” needs the healthy-bus qualification already acknowledged elsewhere. [Plan:194](/Users/jdlien/code/ak820-pro/plans/OVERRUN-CAPS-PLAN.md:194), [Plan:250](/Users/jdlien/code/ak820-pro/plans/OVERRUN-CAPS-PLAN.md:250), [Plan:262](/Users/jdlien/code/ak820-pro/plans/OVERRUN-CAPS-PLAN.md:262)
- The saved echo run proves one failed/tainted **echo**, not exactly one lost byte. The 60-second check also ordinarily cannot explain activity 22–28 seconds after reset: its counter starts at zero and increments with elapsed RTC seconds. Leave the cause unknown, including possible host-sync activity. [capture README:17](/Users/jdlien/code/ak820-pro/history/raw81-echo-20261019-mac/README.md:17), [rtc.c:451](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:451), [rtc.c:1854](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1854)

For the [disposition table](/Users/jdlien/code/ak820-pro/plans/OVERRUN-CAPS-PLAN.md:361), my verdicts are:

| # | Status |
|---|---|
| 1 | **Closed**, with `locks_forget()` beside the discard-site invalidation, not inside the general helper. |
| 2 | **Partial**: pumping is restored, but literal alternation still permits stale label painting. |
| 3 | **Partial**: separate counters and untouched parser flags are sound; direct-cause attribution is overstated. |
| 4 | **Partial**: admission and timeout validation improved; explicitly select the burst timeout and cover the entire pass. |
| 5 | **Partial**: four arms are good; loss-induced traffic gaps and the overnight inference remain. |
| 6 | **Mostly closed**: fix the remaining unconditional FIFO comment. |
| 7 | **Closed**: the absolute keystroke claim is removed. |
| 8 | **Mostly closed**: acquisition attribution removed; correct “one byte” and the early reference-check suggestion. |
| 9 | **Closed**: daemon evidence, not SOF, supplies sync exposure. |
| 10 | **Closed**: 4/10/3/9 wire bytes are correct. |
| 11 | **Partial**: timer choice is good; define boundaries, wrap limits, and sleep exclusion. |
| 12 | **Partial**: priorities/candidates corrected; a sub-1 ms maximum cannot exclude contribution. |
| 13 | **Mostly closed**: program/erase distinction is good; erase is a fetch stall, and its bit records context. |
| 14 | **Closed at plan level**: the phase-write measurement list addresses the concern. |
| 15 | **Closed**: propagate the repeated-START error. |
| 16 | **Mostly closed**: scratch buffer and yields are correct; enforce one transaction across the whole pass. |
| 17 | **Closed**: serving the lock queue first removes the text backlog ahead of an enqueued label. |
| 18 | **Partial**: capacity arithmetic changed, and partial-enqueue recovery is unsafe. |
| 19 | **Closed at plan level**: boot in transaction mode and validate before adopting byte mode. |
| 20 | **Closed at plan level**: dev-only diagnostics and expanded build checks are appropriate. |

The normal byte-boundary placement, GPIO protection, clock-state ownership, and revised phase-timing validation are sound. **A universal 25 ms guarantee remains unproved**: normal PCF paths still have the 20 ms timeout, remaining housekeeping contributes, and animation resume retains its synchronous full-screen clear. Preserve the stated hardware validation gate and include retries, give-ups, report replacement, and waking-key delivery alongside pass timing.