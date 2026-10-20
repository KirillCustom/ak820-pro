**The two changes are reasonable, but the plan needs revisions before implementation.** The byte-boundary preemption point is sound on the normal path for this board. The largest concrete defects are in queue invalidation, Fn+D scheduling, attribution, and the pass-budget argument.

I read only within the repository and made no changes. Below, board filenames refer to `qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/`; “fallback LLD” means `lib/chibios-contrib/os/hal/lib/fallback/I2C/hal_i2c_lld.c`.

1. **Must-fix — do not call `locks_forget()` on ordinary queue completion.**

   `gq_reset()` runs both when discarding work **and when the pump successfully submits the last glyph**. `locks_forget()` means “the band is physically blank,” setting `shown_caps`, `shown_gui`, and `shown_pad` false.

   With the proposed change, painting CAPS would immediately forget that CAPS is on screen. If Caps then turns off, the diff sees false→false and skips its clear: **CAPS and the padlock can remain stranded indefinitely.** If Caps stays on, subsequent ticks unnecessarily repaint them. Completing unrelated clock/text work would also forget the locks.

   Put lock invalidation in the **discard/full-clear path**, alongside `shadow_invalidate()`, while ordinary queue completion preserves it. If cancellation can occur without a physical clear, use an “unknown/dirty” state rather than “known empty.” The current discard callers perform full-screen clears, so that narrower change is fine.  
   [display.c:2066](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:2066), [display.c:2835](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:2835), `display.c:2845–2847`, `1360–1364`, `1404–1407`.

2. **Must-fix — Fn+D needs a pump/scheduler change, not merely a wait in stage 5.**

   While `debug_exit_step` is nonzero, `display_blit_pump()` executes a restore stage and **returns before pumping the queue**. Stage 5 currently does not wait for queued glyphs; it updates shadows and advances. Adding `if (gq_pending()) return` inside that stage would deadlock it.

   Without a scheduling change, a queued CAPS can also be followed by a synchronous Caps-off clear during the restore, then painted *after* that clear when restoration finally ends.

   Preserve stage 1’s discard-before-clear behavior, but permit queued glyphs to drain between subsequent restore components. Each pass should perform either a restore component or a glyph submission. Keep the existing `pass_deferred` gate above both.  
   [display.c:1355](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:1355), `display.c:1378–1394`, [display.c:2809](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:2809).

3. **Must-fix — the attribution must distinguish causes and preserve the echo parser’s error handling.**

   The counter’s two sources are confirmed: hardware `LS.OE`, and failure to enqueue a received byte. They are merged into one sticky event flag. Consequently, `uart_overrun` counts **polls observing the flag**, not lost bytes or necessarily passes. In `dev`, `r81_commit()` also calls `uart_err_poll()`, in addition to the main receive-loop poll.  
   [hal_serial_lld.c:263](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios-contrib/os/hal/ports/SN32/LLD/SN32F2xx/UART/hal_serial_lld.c:263), `hal_serial_lld.c:293–301`; [ch582f_ajazz.c:617](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:617), `ch582f_ajazz.c:710`, `1436`.

   Also, the ordinary poll currently runs through `keyboard_task()` **before** housekeeping’s `rtc_fast_task()`. Its interval spans parts of adjacent passes. It can contain the preceding pass’s I2C, display, flash, and sleep work. Moving or adding a flag-consuming poll after I2C must not hide an error from the raw81 taint logic.  
   `quantum/keyboard.c:793`, `quantum/main.c:48–75`, `ak820pro.c:569–631`.

   Specify:

   - Separate dev counters at the LLD sites for **hardware OE** and **software input-queue overflow**.
   - An attribution bitmask: PCF, flash, sleep transition, and unknown; allow **multiple candidates**.
   - PCF kind mask/count, actual duration, lock mode, and flash program/erase deltas.
   - Poll interval/pass identity; finalize full-pass duration at pass end.
   - Preserve every existing parser-visible error and taint check.

   “PCF occurred since the previous poll” is correlation, not proof. A four-entry ring is adequate for a short sample, but will overwrite most overnight evidence.

4. **Must-fix — retain an explicit whole-pass budget for the burst and revalidate the 10 ms timeout.**

   The proposed burst checks only `lcd_blit_busy()`. The established recovery admission is stronger: **queue empty, DMA idle, and elapsed pass time below 10 ms**. Use that admission for the burst, plus display deferral. Otherwise it can follow an echo pass already costing 13–15 ms, then add a stretched read and housekeeping.  
   [rtc.c:1620](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1620), `rtc.c:1806–1814`; [echo measurements:46](/Users/jdlien/code/ak820-pro/history/raw81-echo-20261019-mac/README.md:46).

   Raising the short timeout to 10 ms is plausible, but the new argument is approximately:

   **under 10 ms before admission + 10 ms deadline + timeout/STOP overhead + remaining housekeeping < 25 ms.**

   That needs measurement, including failed reads, NACK, stuck bus, pre-sleep admission, and wake recovery. The timeout is checked at byte boundaries and in clock waits; it is not a hard function-return deadline. Nor does it “bound only a stuck bus”: interrupts admitted between bytes consume the deadline too.  
   [fallback LLD:106](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios-contrib/os/hal/lib/fallback/I2C/hal_i2c_lld.c:106), `:117–127`, `:234`, `:263`; [deep-sleep budget:355](/Users/jdlien/code/ak820-pro/plans/DEEP-SLEEP-BUILD-PLAN.md:355).

   Two failure-path qualifications belong in the plan:

   - A stuck clock wait still holds the lock until timeout. **Per-byte preemption does not guarantee every mask is below 0.3 ms.**
   - `MSG_TIMEOUT` leaves `I2CD1.state = I2C_LOCKED`, but RTC code only starts the driver at initialization. Add a defined stop/restart recovery before retrying. Current disabled assertions conceal that contract violation; the runtime lock-mode switch alone does not repair it.  
     [hal_i2c.c:201](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios/os/hal/src/hal_i2c.c:201), `rtc.c:1076`; fallback LLD `:389–391`.

5. **Must-fix — change what the A/B claims to establish.**

   A successful burst test would show that long locked PCF reads **can cause losses under the injected load**, and that byte preemption prevents those losses. It would not establish that PCF activity explains the historical idle rate.

   Assuming an empty 16-byte FIFO, a 3 ms mask cannot overflow it when only one ordinary three-byte ACK/status frame arrives. At twelve syncs/hour, explaining 1–2 overruns/hour from one long transaction per sync requires roughly **8–17% of those transactions to encounter enough incoming traffic**. That needs evidence of actual bursts or existing FIFO occupancy. The parser’s normal ACK, `5A`, `5B`, and `5C` frames are three bytes; battery polling is every five seconds.  
   [ch582f_ajazz.c:1540](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:1540), `ch582f_ajazz.c:49–50`, `1430`.

   The ~15% estimate is reasonable **as an illustrative calculation**: with uniformly distributed phases, burst duration \(B≈3\) ms, mask \(L≈3\) ms, and required overlap \(F≈1.4\) ms, the qualifying window is approximately \(L+B-2F≈3.2\) ms per 20 ms. It is not a prediction for this test:

   - Fixed 50 ms spacing can phase-lock against echo and main-loop scheduling.
   - The script sends one request, waits for its response, and waits **250 ms on a loss**. A loss therefore removes traffic during several subsequent PCF reads.
   - The raw81 collector has a **15 ms lifetime**; echo losses can occur without a hardware overrun.
   - One thousand reads spaced by 50 ms finish in roughly 50 seconds plus deferrals, rather than occupying the whole three-minute run.  
     [win_raw81_echo.py:95](/Users/jdlien/code/ak820-pro/scripts/win_raw81_echo.py:95), `ch582f_ajazz.c:665–667`, `710–716`.

   Use varied spacing or repeated phase offsets, record completed reads and exposure time, and compare **hardware OE deltas**, not just `lost_at`. Include a transaction-lock/no-burst control. Keep the daemon policy identical across arms, and release the cable HID handle after setting the hook so the test does not inadvertently prevent its syncs.

The remaining factual checks and safety details are as follows.

| Rank | Check | Verdict |
|---|---|---|
| **Note** | Whole-transaction locking | **Correct for this configuration.** HAL locks around the LLD; `SW_I2C_USE_OSAL_DELAY` is false, so the fallback calls the busy delay and never sleeps/unlocks. The delay is a volatile loop containing 15 NOPs, **not just 15 CPU cycles**. `hal_i2c.c:203–214`; fallback LLD `:88–94`; `halconf.h:19–22`; `rtc.c:46–53`. |
| **Note** | M0 masking | **Correct for maskable interrupts**, including UART2 and row scan. OSAL maps to `chSysLock()`, and the ARMv6-M port calls `__disable_irq()`/`__enable_irq()`. NMI/HardFault are exceptions to “all interrupts.” [chcore.h:499](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios/os/common/ports/ARMv6-M/chcore.h:499); `osal/rt-nil/osal.h:568–586`. |
| **Should-fix** | FIFO depth | FIFO **enablement**, trigger setting, and 115200 8N1 are confirmed. **Sixteen-byte hardware depth is not proved by this code.** The repository’s manual notes explicitly call it unstated. Keep 1.39 ms conditional on that assumption, and account for occupancy at lock entry. `hal_serial_lld.c:214–225`; `ch582f_ajazz.c:426–434`; [docs/sn32f299.md:938](/Users/jdlien/code/ak820-pro/docs/sn32f299.md:938). |
| **Note** | Software queue | **128 bytes is confirmed**, for both input and output. The echo TX comment claiming a 16-byte output queue is stale. Eleven milliseconds is the approximate fill time only for an initially empty queue under continuous line-rate reception; the main drain also caps consumption at 64 bytes/call. `platforms/chibios/boards/common/configs/halconf.h:418`; serial LLD header `:236–238`; `ch582f_ajazz.c:1461`. |
| **Should-fix** | “Never a key” | Too absolute. Incoming loss does not directly erase an outgoing key report, but lost ACKs cause retries, eventual give-up, and queue pressure/replacement. The recorded runs showing no give-ups are evidence about those runs. `ch582f_ajazz.c:599–604`, `916–925`. |
| **Note** | Recorded rates | The saved snapshots support 92/51.92 h and 13/16.88 h, with zero framing/parity errors. They are cumulative observed-flag counts, not exact byte-loss counts. [2f capture:3](/Users/jdlien/code/ak820-pro/history/battery-2026-10-15-drain-2f/guard-cut-20261018-020553.txt:3), [2e capture:3](/Users/jdlien/code/ak820-pro/history/battery-2026-10-12-drain-2e/guard-start-20261012-122418.txt:3). The soak record supports three in 85 minutes: `history/deep-cycles-20261019-icons/README.md:23–31`. |
| **Should-fix** | Echo loss at 22–28 seconds | The recorded failed echo is real; **the claim that this is when boot seed/acquisition run is unsupported and normally false**. Seed runs during initialization, before display initialization. Acquisition starts after the splash and has a 1.5-second deadline; a host sync cancels it. `ak820pro.c:81–85`; `rtc.c:1100–1101`, `1488–1489`, `1518–1534`; `display.c:1453`. |
| **Should-fix** | SOF implies daemon syncs | SOF proposals prove the frequency estimator had USB frames, **not that host SETs succeeded every five minutes**. The daemon’s nominal interval is 300 seconds, with a 180-second fast interval. Use successful host-sync evidence for the exposure count. `rtc.c:615–705`; `ak820-agent/src/clock/scheduler.rs:27–34`. |
| **Note** | PCF transaction schedule | The normal sync sequence is correctly described: STOP read/write, calendar write, release read/write, verify, bracket. The bracket has a **time window**, not an enforced 120-read cap. Without qualified SOF, the 60-second calendar check runs when not recovering or writing, and RTC housekeeping is skipped during animation. Pre-sleep/wake and boot reads are real. Also include legacy synchronous `rtc_set_time()`, retry/recovery writes, and instrumented-only PCF hooks. `rtc.c:1122–1135`, `1345–1467`, `1636–1643`, `1743`, `1832–1875`; `ak820pro.c:607`. |
| **Should-fix** | Transaction lengths | The four kinds are right, but wire-byte counts are **4, 10, 3, 9**, respectively: register reads include write address, register pointer, repeated-start read address, then data. Nine clock bits per byte is right; write-one bits add an arbitration delay, so “four delays per bit” is approximate. `rtc.c:259–265`, `329–335`, `1273–1285`; fallback LLD `:179–198`, `:446–484`. |

**Should-fix — measure interrupt masking separately from transaction duration.** The reported 101-cycle maximum is a wall-time measurement around the API, including interrupts before lock acquisition and after its final unlock. With byte preemption it will also include every admitted ISR. It cannot demonstrate the proposed maximum byte-sized mask. Its seconds/counter stamp is also not a coherent snapshot across RTC rollover.

Keep per-kind counts/maxima, but add maximum uninterrupted masked duration, failures, and counts above relevant thresholds. The free-running CT16B5 counter is a better short-interval timer; it continues while interrupts are masked. The “3.1 ms” conversion is approximate and depends on the actual ILRC rate.  
[rtc.c:216](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:216), `rtc.c:231–236`; `chvt.h:122–128`; [hal_st_lld.h:147](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios-contrib/os/hal/ports/SN32/LLD/SN32F2xx/SysTick/hal_st_lld.h:147).

**Should-fix — include these real alternative UART delays.**

| Candidate | What the code establishes |
|---|---|
| **USB, priority 1** | UART2 is not alone at priority 1. USB cannot be preempted by equal-priority UART2, and its FIFO routines contain unbounded `RWSTATUS` polling, including under ISR locks. These are genuine candidates to measure; source inspection does not establish their normal duration exceeds 1.4 ms. [hal_usb_lld.h:76](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios-contrib/os/hal/ports/SN32/LLD/SN32F2xx/USB/hal_usb_lld.h:76); USB LLD `:148`, `:154`, `:197`, `:203`, `:369–389`. |
| **SysTick, effectively priority 0 in this source configuration** | Its default priority is **8**, outside the M0’s four levels. The priority conversion and packed system-handler write reduce this to priority zero for SysTick. It runs the kernel timer handler under a lock. Add priority readback and handler-duration evidence; do not call UART2 “highest in use.” `SysTick/hal_st_lld.h:49`, `.c:158`, `:192–194`; `SN32F290.h:103`; `nvic.h:69`; [nvic.c:147](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios/os/hal/ports/common/ARMCMx/nvic.c:147). |
| **Internal flash** | Program/erase routines execute from SRAM. Programming explicitly masks each 8-byte busy window; erase does not mask, but vector/handler fetches from busy flash can still stall. Documented program time is 10–20 µs; page erase is 1–2 ms. **Both drain captures report their last erase at uptime 99 ms**, so repeated later erases are not supported as the explanation for those captures’ ongoing rate. Track program/erase operations, not only sessions. [hal_efl_lld.c:30](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios-contrib/os/hal/ports/SN32/SN32F290/hal_efl_lld.c:30), `:336–339`, `:392–422`; `docs/sn32f299.md:1088–1121`; captures above. |
| **Deep sleep** | It holds PRIMASK across sleep and clock restoration, but **disables UART RX**, then drains/resets receive state before unlocking. That creates deliberate receive blindness rather than an ordinary accumulating FIFO overrun. Entry/exit deserve separate attribution. It cannot explain the pre-sleep-build drain rate. [sleep.c:325](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/sleep.c:325), `:335–338`, `:468–488`. |
| **Other critical sections** | Health/RTC snapshots and indicator changes are short bounded operations. ADC polling is bounded by 2,000 spins; RGB publication copies a small fixed buffer; matrix GPIO operations have short atomic regions. The LCD abort lock contains register operations, not its long DMA wait. None is an obvious normal >1.4 ms section from source, though fault-path duration should be measured. `health.c:180–190`; `battery.c:111–118`; `drivers/led/sn32f2xx.c:969–972`; `quantum/matrix.c:75–92`; `lcd_bus.c:950–957`; SPI LLD `:239–263`. |

The row ISR’s ordinary 188–261 µs body is lower priority and can be interrupted by UART2. Its short critical regions still matter on M0, but its whole body is not automatically a UART blackout. Conversely, display/USB/report waits can delay **main-loop queue draining** without masking UART interrupts; keep that distinction in the attribution.

**Note — the proposed unlock/relock is compatible with this board’s normal ChibiOS usage.**

- On successful paths, START, transmitted bits, received bits, and ACK/NACK leave SCL low before the next byte. Restart and STOP remain within locked chunks. One existing exception needs acknowledging: `i2c_write_restart()` ignores `i2c_write_start()`’s error result, so the unconditional SCL-low claim is false on that failure path. Propagate that error. Fallback LLD `:130–155`, `:195`, `:220–224`, `:247`, `:272`.
- I found no relevant I2C caller executing under an outer system lock or in an ISR. `rtc_sleep_prepare()` precedes `deep_sleep()`’s lock. `rtc_wake_beginI()` only sets state; actual wake reads occur later in `rtc_fast_task()`. USB raw-HID handlers execute from the main-loop receive task. `sleep.c:773–775`, `826–828`; `rtc.c:1646–1663`, `1792–1817`; `usb_main.c:517–520`.
- Unlock/relock changes ChibiOS’s debug lock count correctly. `chSysUnlock()` does **not** itself reschedule and asserts that the caller has not left a higher-priority thread ready. This LLD does not ready threads between boundaries; interrupts can perform their normal scheduling. Leaving `I2CD1.state` ACTIVE during the unlocked interval is fine with the current single main-thread owner. A future second owner would need bus serialization. [chsys.h:407](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios/os/rt/include/chsys.h:407), `chdebug.c:154–176`.
- Timeout system-time reads remain valid while locked: this build reads a free-running hardware counter, not a tick variable that requires an ISR.
- The P0 RMW concern is handled correctly by the proposed placement. A14/A15 and A18/A19 share `MODE`; their configuration fields are in `CFG` versus `CFG1`. Keep every mode RMW inside a locked chunk. Main-thread execution cannot resume halfway through the row ISR’s RMW. [hal_pal_lld.c:205](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios-contrib/os/hal/ports/SN32/LLD/SN32F2xx/GPIO/hal_pal_lld.c:205), `:236–245`; `keyboard.json:144`; `quantum/matrix.c:75–92`.
- Watchdog scopes are breadcrumbs, not timing guards or feeds. Existing read/write scopes survive the proposed interrupt windows; raw register helpers currently inherit the RTC-fast scope. The approximately 12-second watchdog cannot enforce the 25 ms pass limit. `watchdog_record.h:35–38`; `watchdog_record.c:98–106`; `watchdog.c:10–15`.

**Should-fix — quantify phase-write timing rather than treating feedback as a complete correction.** The release schedules against `PCF_WRITE_MS = 1`, and `PCF_LATE_MS = 10` is checked **before** the release transaction. Its completion can therefore be later than that window. The bracket adapts systematic boundary error, but cannot eliminate per-run interrupt jitter or its own sampling uncertainty. The stated +0.8/+2.6 ms figures are row-ISR estimates, not upper bounds including USB/SysTick. Compare release duration, release/boundary error, restart counts, bracket success/width, and host residuals under both quiet and echo load.  
[rtc.c:1296](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1296), `rtc.c:1375–1447`.

**Note — repeated PCF reads are safe for the clock state machine with explicit arbitration.** `pcf_read_raw()` reads/decodes into its output and updates diagnostics/availability; it does not set either clock or advance acquisition. Use a scratch output. Yield to `pcf_pending` as well as non-IDLE `ps_state`, acquisition—including its pending start—and the whole sleep recovery. Prevent a later `rtc_task()` discipline read from creating a second transaction in the same test pass. Successful diagnostic reads also reset `pcf_avail_fail`, so report that observer effect.  
`rtc.c:251–300`, `1507–1531`, `1816–1817`, `1852–1875`.

**Note — most of the normal Caps queue proposal is fine after findings 1–2.**

- The synchronous glyph loop and padlock fills are confirmed; the recorded 25 ms **whole pass** marked `blit` is confirmed. That mark does not isolate ten milliseconds specifically to CAPS, and the per-glyph numbers are measurements/estimates rather than code guarantees. [lcd_bus.c:569](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/lcd_bus.c:569), `display.c:2039–2045`, `2102–2120`; [Caps record:8](/Users/jdlien/code/ak820-pro/history/fnb-caps-20261019-jd/README.md:8).
- Growing capacity from **64 to 74** covers one maximum batch of 19+21 text glyphs, 24 clock/playback glyphs, and ten lock glyphs. `gq_push()` currently silently drops overflow. Enforce that batch invariant or make insertion failure observable; do not update shadows after an unsuccessful enqueue. `display.h:88–89`; `display.c:414`, `2677`, `2758–2761`.
- Normal housekeeping’s pending-queue gate protects subsequent lock clears. Queue empty means the last DMA was submitted, not completed, but synchronous clear operations serialize through the LCD bus. Keep that serialization. `display.c:3182`, `2835`; `lcd_bus.c:542–551`.
- The lock band does not overlap the current text/clock bands. `band_clear()` need not invalidate locks for these layouts. `display.c:366–371`, `2010–2023`, `2158`, `3015–3016`.
- **Four passes/~12 ms is not a general latency bound.** Text is queued before locks, so CAPS can wait behind forty glyphs: roughly 44 pump opportunities, before DMA waits or other deferrals. The padlock can therefore precede its label by hundreds of milliseconds under load. Consider queueing lock labels before text if prompt agreement matters. `display.c:3191–3197`.
- Recovery’s deferral gates already protect the pump and housekeeping; preserve them. Pause/animation suspends queue pumping, and resume discards work and performs a full dashboard repaint. That resume still includes the existing synchronous full-screen clear, so this change alone cannot establish a universal <25 ms guarantee for animation transitions. `display.c:145–147`, `1398–1419`, `2785`, `2822`, `3155`.

Expand the Caps validation beyond 600 toggles: toggle off while labels are pending, exercise SCR→FN, exit Fn+D with multiple locks active, and test discard/repaint, sleep recovery, animation resume, and a track playing. Check actual pixels and total-pass ≥25 ms counters.

**Default/build decision:** I would accept per-byte as the `dev` default **after the above correctness and timing conditions are resolved**. The runtime switch is useful, but “only failed PCF transactions can happen” is not a sufficient safety argument: longer passes, phase error, STOP recovery, and failed wake accounting also matter. Starting the first experiment in transaction mode and enabling byte mode through the hook still permits both arms and the fixed mode on one flash.

`daily` should contain the validated byte-preemption path, necessary timeout/recovery changes, and the complete Caps scheduler/invalidation fix. New burst machinery, runtime rollback state, attribution rings, and measurement hooks belong under `AK820_DEV_HOOKS`, not `WDT_TEST_HOOKS`. Existing production health counters can remain. Extend the build checks: the current checks prove absence of the dev dispatcher and selected symbols, **not absence of every diagnostic nor execution of an inlined byte-preemption path**. `build.sh:24–27`, [build.sh:125](/Users/jdlien/code/ak820-pro/build.sh:125).