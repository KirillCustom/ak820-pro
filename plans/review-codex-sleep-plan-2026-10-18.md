**The light-sleep sizing experiment is reasonable. The deep-sleep plan needs several concrete corrections before implementation, especially interrupt cleanup, UART recovery and RTC restoration.** I found no inherent flash-erasure or bootloader-bricking operation in the proposed sleep sequence.

This was a read-only review; nothing was modified, built or tested on hardware. Page numbers below are the printed pages of the [SN32F299 V1.8 manual](/Users/jdlien/code/ajazz-ak820-pro/docs/SN32F299_V1.8_EN.pdf).

1. **Must-fix — preserve PRIMASK throughout teardown and restoration, and clear NVIC pending state between sleeps.**

   The warning in [SLEEP-PLAN.md:157](/Users/jdlien/code/ak820-pro/plans/SLEEP-PLAN.md:157) must cover the entire locked section, not just its WFI loop. Ordinary `pwmDisableChannel()`, `pwmDisablePeriodicNotification()` and GPT start/stop wrappers acquire and then release the system lock. On this Cortex-M0 port, releasing it executes `__enable_irq()` unconditionally. Calling these inside the outer lock breaks the experiment’s central assumption. Use the appropriate I-class operations or audited register operations throughout. See [hal_pwm.c:258](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios/os/hal/src/hal_pwm.c:258) and [chcore.h:503](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios/os/common/ports/ARMv6-M/chcore.h:503).

   **An NVIC-enabled interrupt becoming pending can wake Cortex-M0 WFI with PRIMASK set; PRIMASK prevents handler execution.** The Sonix manual requires NVIC enables for GPIO deep wake, but does not explicitly discuss the masked-WFI case: treat the first bench run as verification of that integration, not an already demonstrated property. Manual pp. 45, 48, 75.

   Because no exception is accepted, there is no normal pending-to-active transition. After each RTC wake, consume `RTC_RIS.SECIF`, write `RTC_IC = 1`, then clear `NVIC_ICPR` bit 23 before sleeping again. Otherwise the pending RTC interrupt can make subsequent WFIs return immediately. GPIO needs the same peripheral-plus-NVIC cleanup. On exit, **disable the temporary GPIO vectors before restoring the saved enable mask**: writing the old mask to `ISER` alone does not disable newly enabled vectors. Manual pp. 44–45, 156–157.

2. **Must-fix — make GPIO arming race-safe and specify the falling-edge encoding.**

   The column map and IRQ numbers in the plan are correct. Use these masks:

   | Port | Columns | Mask | NVIC IRQ |
   |---|---|---:|---:|
   | P0 | 4–7 | `0x00F0` | 31 |
   | P2 | 0–9, 14 | `0x43FF` | 29 |

   For those bits: `MODE=0`, each two-bit `CFG` field `00` for pull-up, `IS=0` for edge, `IBS=0` for single edge, **`IEV=1` for falling**, then write the mask to `IC` and enable it in `IE`. Falling polarity is easy to reverse on this part. Manual pp. 81–84; [keyboard.json:141](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/keyboard.json:141).

   The initial “no key down” check is insufficient: a press can arrive before the later interrupt-clear operation and have its edge erased. After configuring all six rows low and arming the columns, **read the physical column levels immediately before WFI**. An already-low column must abort entry or count as a key wake. Preserve any subsequent latched edge. If GPIO and RTC are both pending, account for both before exiting.

   Falling-edge wake is supported; the manual *recommends* both-edge sensing on p. 75, but does not require it. Falling-only is appropriate for this experiment with the held-key check above.

3. **Must-fix — restore both software RTC clocks, before unlocking.**

   [SLEEP-PLAN.md:136](/Users/jdlien/code/ak820-pro/plans/SLEEP-PLAN.md:136) restores only `rtc_seconds_count`. There is another clock: the LLD’s private `rtc_time`, incremented by the RTC ISR and returned by `rtcGetTime()`. The board uses both in `rtc_now()`. Restoring only the board counter leaves the displayed/calendar time behind by the entire sleep duration. See [hal_rtc_lld.c:118](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios-contrib/os/hal/ports/SN32/LLD/SN32F2xx/RTC/hal_rtc_lld.c:118) and [rtc.c:674](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:674).

   Advance both clocks exactly once for every consumed `SECIF`; update or saturate `rtc_check_seconds`, and invalidate the SOF estimator’s continuity/window state. Handle an RTC flag already pending at entry and one coincident with final wake. Do this before normal RTC interrupts resume.

   Do not restart the RTC or rewrite `SECCNTV` merely to restore software time: those operations reset its fractional counter. Manual pp. 156–157. Counting second boundaries is sufficient for calendar restoration; accurate elapsed-duration telemetry also needs the entry/exit `SECCNT` fractions.

4. **Must-fix — turn “restore in reverse” into an explicit clock/peripheral contract.**

   **The manual’s deep-entry sequence is indeed `PMU_CTRL=2; WFI`.** It does not require setting `SCB->SCR.SLEEPDEEP`, manually powering down flash, or executing the entry from SRAM. `DSB` before WFI is sensible. Manual pp. 75, 79.

   The proposed explicit IHRC transition is sensible, with this ordering:

   - Ensure `SYS0_ANBCTRL.IHRCEN` bit 0 and `SYS0_CSST.IHRCRDY` bit 0.
   - Set `SYS0_CLKCFG.SYSCLKSEL[2:0]=0`; wait for `SYSCLKST[6:4]=0`.
   - Only then clear `SYS0_PLLCTRL.PLLEN` bit 15.
   - For a documented 12 MHz flash configuration, use `FLASH_LPCTRL=0x5AFA0000` after slowing down.
   - On final wake, restore `FLASH_LPCTRL=0x5AFA0029` before selecting 48 MHz; restore the PLL configuration, wait for `CSST.PLLRDY` bit 6, select `SYSCLKSEL=4`, and verify `SYSCLKST=4`.

   The manual prohibits disabling a selected—or selected-to-become-current—PLL. Its nominal settling time is 100 µs; poll readiness with a bounded failure path. Manual pp. 58, 61–64, 255; [system_SN32F290.c:226](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios-contrib/os/common/ext/SONiX/SN32F2xx/system_SN32F290.c:226). These flash-control writes change operating timing, not stored firmware.

   The missing peripheral details are:

   | Resource | Required treatment |
   |---|---|
   | **UART2** | Require no outstanding protocol ACK, empty serial output queue, and `UART2_LS.TEMT` **bit 6**, not merely `THRE` bit 5. Disable RX during the clock transition/sleep; otherwise partial characters can survive clock stoppage or be sampled at the temporary wrong baud rate. After restoring 48 MHz, clear/reset RX FIFO and error state, discard incomplete parser state, and restore RX/interrupt configuration. If disabling `UARTEN`, explicitly preserve TX idle **high**. |
   | **SPI0, SPI1 and DMA** | Require completion through the callback/driver state, `DMAEN=0`, both shifters idle, and both chip selects high. A completed hardware count alone is insufficient if the completion callback is still pending. Retaining idle peripheral registers is fine; a full `spiStart()` is not inherently necessary. |
   | **ADC** | It remains enabled between battery conversions. Explicitly clear `ADC_ADM.ADENB` bit 12 for low power; keep `GCHS` bit 5 clear so P2 remains GPIO. After enabling again, allow **100 µs before conversion**. Do not apply the manual’s analog-input “no pull-up” setting to the digital wake columns. |
   | **CT16B5 / SysTick** | Preserve the free-running counter and kernel alarm relationship. Stop CT16B5 counting through the temporary-clock/RTC-service portions if system time is intended to freeze; do not reset its `TC`. Disable SysTick and clear its pending request during sleep. Re-arm/service the kernel alarm after restoring the clock. Writing a saved value back to `SysTick->VAL` does **not** restore that value—it clears it. |
   | **PWMD0/1/2 and pins** | Disabling PWMD1 notification does not stop the timers. Keep all column output enables off; clear stale timer pending state and synchronize counters before resuming normal output. Restore LED row **output modes**, and restore all key rows **high**, as required by this board’s `MATRIX_UNSELECT_DRIVE_HIGH`. |

   Sources: UART manual pp. 184–188 and [hal_serial_lld.c:194](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios-contrib/os/hal/ports/SN32/LLD/SN32F2xx/UART/hal_serial_lld.c:194); [lcd_bus.c:699](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/lcd_bus.c:699); ADC manual pp. 96–99 and [battery.c:98](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/battery.c:98); timer manual pp. 41–42, 124–125 and [hal_st_lld.h:147](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios-contrib/os/hal/ports/SN32/LLD/SN32F2xx/SysTick/hal_st_lld.h:147); [config.h:141](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/config.h:141).

   The proposed pending SysTick on final restoration is reasonable: this port’s tickless handler evaluates current CT16B5 time and re-arms the next deadline. It does **not** recover elapsed wall time. Also, “no timeout is armed across sleep” is false: QMK always has its approximately 175 ms overflow-maintenance virtual timer armed. Freezing that time domain is acceptable for this bounded experiment if stated explicitly. [timer.c:36](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/platforms/chibios/timer.c:36), [chvt.c:499](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios/os/rt/src/chvt.c:499).

5. **Must-fix — treat `PMU_CTRL=0 + WFI` as an unverified probe, and give USB-affecting modes an autonomous expiry.**

   The register definition says **`000: Disable`**, `010: Deep-sleep`, `100: Sleep`—manual p. 79. It does **not** document a third “core clock gated, nothing else” operating mode. Page 245 says `USB_CFG.PHY_EN` bit 30 is automatically disabled in Sleep and Deep-sleep. It neither establishes that every WFI gates USB nor proves that PMU-disabled WFI leaves it untouched.

   Therefore [SLEEP-PLAN.md:84](/Users/jdlien/code/ak820-pro/plans/SLEEP-PLAN.md:84) overstates the evidence. Measure PMU-disabled WFI separately, including USB continuity and `USB_CFG` observations, before treating masks 7/15 as safe unattended tests.

   The existing idle hooks are **harmless with no WFI executed**: writing 4 selects a mode; WFI triggers entry. `CORTEX_ENABLE_WFI_IDLE FALSE` suppresses the stock idle WFI, but does not suppress the enter/leave hooks. Adding WFI to `CH_CFG_IDLE_LOOP_HOOK` while leaving those hooks unchanged would activate PMU Sleep. Replace both hooks in the board-local configuration and explicitly select/reset the intended mode around the experiment. [Board chconf.h:30](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/platforms/chibios/boards/SN_SN32F290/configs/chconf.h:30), [chinstances.c:66](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios/os/rt/src/chinstances.c:66).

   Bit 4 needs a timed, local disarm **before USB reconnection**. Otherwise repeated Sleep entry while USB is still unconfigured can interfere with enumeration, preventing the raw-HID command needed to disable it. Define “not configured” precisely: this board’s `battery_usb_configured()` means `state == USB_ACTIVE`; ChibiOS suspend retains the configuration value. [ak820pro.c:147](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/ak820pro.c:147), [hal_usb.c:730](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios/os/hal/src/hal_usb.c:730).

6. **Must-fix — verify watchdog availability before relying on it for recovery.**

   The direct feed value **`SN_WDT->FEED = 0x5AFA55AA` is correct**: key `0x5AFA`, feed value `0x55AA`, manual p. 153. Keep the watchdog register clock accessible. Its counter uses ILRC independently of HCLK, and p. 150 says reset occurs whenever it is running with an operating clock. With RTC retaining ILRC, expecting continued counting is well-founded.

   The manual also confirms `WDTPRE=4` means `/16`—p. 71—and the reload is `TC+1`, p. 152. Thus `TC=188` gives about **12.096 s at nominal 32 kHz**, subject to ILRC error.

   But after three consecutive watchdog resets, this firmware deliberately leaves the watchdog off. Check `watchdog_degraded()` and actual `WDT_CFG.WDTEN` before arming; do not assume successful feed writes prove it is running. Ten minutes of uptime clears retained reset accounting, but does not clear the current boot’s `degraded_mode` latch. [watchdog.c:74](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/watchdog.c:74), [watchdog_record.c:109](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/watchdog_record.c:109).

   Keep arming one-shot and reset-default-off; bound stray-wake and PLL-wait failure paths. No flash persistence is needed: the breadcrumb and sacrificial write are SRAM operations. The existing bootloader path stops the watchdog before setting DFU magic. The remaining worst case is a board needing a physical reset/power cycle, not intrinsically erased firmware. [watchdog_record.c:124](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/watchdog_record.c:124), [watchdog.c:126](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/watchdog.c:126).

7. **Note — the scan-only timer and row-counter design is fundamentally sound.**

   `pwmChangePeriodI(&PWMD1, 4000)` writes its software period and **MR12=3999**. The final `TC=UINT16_MAX` forces wrap to zero, so the next interval uses the new match value. This avoids waiting a full counter wrap when shortening the period below the current counter. Perform the change in the callback’s proper I-class critical section, before its final re-arm. Manual pp. 116, 125, 132; [hal_pwm_lld.h:370](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios-contrib/os/hal/ports/SN32/LLD/SN32F2xx/CT/hal_pwm_lld.h:370), [sn32f2xx.c:735](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/drivers/led/sn32f2xx.c:735).

   PWMD0/2 can retain their 256-tick periods while their outputs are disabled. Their continued end-of-ISR re-arm is harmless; restoring PWMD1 to 256 restores the existing phase relationship. **Do not extend the period-change macro to PWMD0/2 unchanged:** it indexes `MR[channels]`, whereas these timers use **MR9**, as the corrected start routine recognizes. [hal_pwm_lld.c:145](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios-contrib/os/hal/ports/SN32/LLD/SN32F2xx/CT/hal_pwm_lld.c:145).

   A separate scan-only counter preserves LED pairing **provided both existing `current_row` and `current_key_row` remain frozen**. Continue the existing dirty/publication and row instrumentation paths. [sn32f2xx.c:490](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/drivers/led/sn32f2xx.c:490).

   **Blanking once is sufficient** if it clears every column’s PWM IO enable and no code re-enables them during scan-only operation. That returns the pins to GPIO; repeated blanking today is necessary because normal scanning re-enables PWM after each scan. Merely setting colour duties to zero is insufficient. Manual pp. 138, 143; [sn32f2xx.c:471](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/drivers/led/sn32f2xx.c:471). Avoid blindly reusing exported `sn32f2xx_blank()`: it also changes LED rows to inputs and uses non-I-class calls. [sn32f2xx.c:823](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/drivers/led/sn32f2xx.c:823).

8. **Should-fix — qualify the debounce and yield claims; measure actual service gaps.**

   Approximately 5.2 ms sampling is plausible for the proposed timer, but **“25 ms is sampled several times” does not establish unchanged debounce behavior**. `sym_defer_pk` expires a wall-clock counter using the last published raw state; it does not require another physical sample of that key. At a sampling interval longer than 5 ms, debounce can accept a stale sample before the next observation detects a bounce. The relay test cannot establish mechanical-switch bounce immunity. [sym_defer_pk.c:37](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/quantum/debounce/sym_defer_pk.c:37).

   This is acceptable as an explicitly measured sizing tradeoff. For an unchanged debounce guarantee, shorten the full-row cycle below 5 ms with margin or make acceptance depend on fresh samples. Also test entry/exit transitions: preserving LED phase does not itself guarantee a ≤6 ms sampling gap across a mode switch.

   A 5 ms thread sleep gives **less than 200 Hz** polling once work and interrupt delays are included. UART interrupts keep filling the 128-byte queue, but the parser consumes at most 64 bytes per pass; sustained full-rate input therefore needs a pass roughly every **5.56 ms**, not merely every 11.1 ms. RX is drained before ACK timeout judgment, which is good, but delayed passes still reduce transmission throughput and increase queue pressure. [Common halconf.h:417](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/platforms/chibios/boards/common/configs/halconf.h:417), [ch582f_ajazz.c:1170](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:1170), [ch582f_ajazz.c:1391](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:1391).

   Yield also delays raw-HID processing, report production, encoder polling and the one-glyph-per-pass pump. DMA and USB interrupt handling can continue in ordinary thread sleep. The watchdog kick at the end of the completed pass is correctly placed, and 1–5 ms is negligible against its timeout. [main.c:48](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/quantum/main.c:48), [ak820pro.c:570](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/ak820pro.c:570), [keyboard.c:742](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/quantum/keyboard.c:742).

9. **Should-fix — gate CT16B3 on desired effective outputs, and restore its software state.**

   `SCREEN_OFF` alone is insufficient: Minimal mode can temporarily lift its backlight cap for a message. Use effective backlight duty and desired indicator duties, not instantaneous pin levels during PWM’s off phase. [power.c:106](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/power.c:106).

   Stopping CT16B3 is fine when all outputs should be off. Restart independently of the stopped callback, restore `MR0RST` bit 1 with the `0x5A` write key, and invalidate/reconcile `indicators_tick()`’s cached `last_out` after forcibly changing pins. Otherwise a previously steady full-duty indicator can remain low because the callback thinks its output is unchanged. [indicators.c:97](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/indicators.c:97), [indicators.c:158](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/indicators.c:158).

10. **Should-fix — tighten the measurements; the basic bench sequence is right.**

   The cumulative masks `0 → 1 → 3 → 7 → 15`, charge-register integration, settling period and same-run baseline are good for measuring incremental savings. Add:

   - Requested **and effective** mask, per-feature active time, WFI entries and actual sleep-entry status. A gate that never activates must not produce a passing result.
   - A matched **data-off baseline** for bit 4; compare `15` versus `31` under identical disconnected conditions.
   - Per-window counter resets/deltas and maxima. `rowgap` is a max-hold; it cannot be isolated by subtracting two maxima.
   - UART overrun/framing/parity, queue drops/giveups and actual host delivery alongside ACK timeouts. Board `key_presses` proves processing, not delivery; `raw_edges` counts changes at main-loop consumption, not every electrical transition.
   - An explicit interpretation of the 2 mA cutoff: the best passing configuration, and whether that includes PMU Sleep.

   These counter semantics are documented in [health.c:49](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/health.c:49) and [ch582f_ajazz.c:580](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:580).

   **For deep sleep, pack → arm → data off → relay key → data on → read is correct.** The repository documents relay 2 as power control and the selected `uhubctl` port as data control, including successful battery operation through disconnection. [test-bench.md:138](/Users/jdlien/code/ak820-pro/docs/test-bench.md:138). Make the sequence conclusive:

   - Verify F19 mapping after flashing: it is a VIA override; the compiled default is Delete. [test-bench.md:451](/Users/jdlien/code/ak820-pro/docs/test-bench.md:451), [keymap.c:32](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/keymaps/default/keymap.c:32).
   - Record actual entry and GPIO wake before reconnecting USB; reject “never entered” runs.
   - Take the “after” key count only after normal scanning/debounce has resumed. An immediate count inside the sleep-return function will falsely report a lost waking key.
   - Verify a fresh delivered report after wake. `bluetooth_is_connected()` returns remembered state, which can remain stale when sleep loses radio messages. [ch582f_ajazz.c:420](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:420).
   - The relay tests row 0/P2.8 only; use JD’s assistance to exercise a P0 column and another row.
   - Run the no-key cap test next, then the unfed-watchdog test with watchdog activity verified. Restore relay 1 open, data on, masks/arming off, stage restored and relay 2 on.

   Finally, the manual lists LCD and **ELS-clocked CT16B5** as additional deep-wake sources, pp. 77–78, 125. They are not required here: this board’s CT16B5 uses HCLK. The quoted ~12.7 µs IHRC wake time and ~100 µs PLL settling time are valid nominal figures, not bounds on restored keyboard-report latency.