**I found no new default-off path that blocks scanning, sleeps, or introduces a 25 ms delay. I would fix the wake and bench-recovery issues below before enabling the experiments. The daily build is not completely free of the additions.**

This was a static, read-only review of the specified commits and their dependencies. Nothing was modified, built, flashed, or executed against hardware. Manual references are printed pages of the [SN32F299 V1.8 manual](/Users/jdlien/code/ajazz-ak820-pro/docs/SN32F299_V1.8_EN.pdf).

1. **Must-fix — USB recovery imposes a 50 ms input-service gap after deep wake.**

   [sleep.c:312](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/sleep.c:312) calls `restart_usb_driver()` before returning to the keyboard loop. That function contains [an unconditional `wait_ms(50)`](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/tmk_core/protocol/chibios/usb_main.c:349).

   Interrupt-driven scanning resumes, but debounce and report production do not. A 25 ms waking tap can be sampled down and then overwritten by its release before [the main loop consumes `shared_matrix`](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/drivers/led/sn32f2xx.c:1031). The script’s **60 ms hold** can conceal this.

   USB recovery belongs after unlocking, as implemented, but it needs a deferred/nonblocking disconnect–restart sequence that permits input processing. Otherwise the experiment has a built-in ≥50 ms wake-service gap, regardless of the manual’s approximately 12.7 µs oscillator wake and 100 µs PLL settling figures—pp. 58, 76.

2. **Must-fix — UART recovery from the plan remains incomplete.**

   Clearing **`CTRL.RXEN` bit 6**, retaining UART/TX enable, and restoring it after 48 MHz returns are correct—[sleep.c:192](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/sleep.c:192), manual p. 188.

   However, restoration merely writes the saved `CTRL`. It does not drain/reset stale hardware receive data, clear receive errors, discard queued pre-sleep bytes, or reset the parser’s [persistent `b2/b1/b0` window](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:1037). Old prefixes can therefore combine with post-wake bytes and produce a false ACK/status frame. `RXEN=0` is not documented as flushing those states; receive/error clearing is described on manual pp. 184–186.

   Also, [`ch582_tx_idle()`](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:723) checks only the protocol queue/in-flight flag. [Module ACK responses bypass that queue through `sdWrite()`](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:845). Add an explicit serial-output-queue-empty check alongside hardware `TEMT`, preferably in the final locked admission check.

   This is an unfinished part of plan-review finding 4. Use an audited receive cleanup; the manual marks FIFOCTRL bits 5:1 reserved, so blindly copying generic 16550 FIFO-reset bits is not sufficient.

3. **Must-fix before calling sizing hands-free — the unverified WFI probe lacks autonomous recovery.**

   The deadline in [sleep.c:54](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/sleep.c:54) removes only **bit 4**. Bit 2 can run indefinitely.

   Manual p. 79 documents `PMU_CTRL=0` as **Disable**, not a guaranteed USB-preserving sleep mode. If that probe disrupts USB while the main loop continues, the watchdog continues receiving kicks and the HID command in `finally` cannot clear the mask. Restoring relay 2 supplies power; it does not necessarily reset the already battery-powered board.

   Give the initial bit-2 probe a local expiry and an input-friendly USB recovery path. Bit 4 already has a finite admission window, but clearing its applied bit alone does not explicitly restore `PHY_EN`; verify reconnection after expiry against manual p. 245.

4. **Must-fix — cleanup failures can leave battery power or USB data cut.**

   Both scripts call `sb.relay1(dev, False)` **before** entering the nested cleanup protection:

   - [sleep_sizing.py:114](/Users/jdlien/code/ak820-pro/scripts/sleep_sizing.py:114)
   - [deep_sleep.py:135](/Users/jdlien/code/ak820-pro/scripts/deep_sleep.py:135)

   If that HID operation raises, all subsequent cleanup is skipped. In the deep script, an exception from `uhub()` other than `SystemExit` also skips the later relay-2 restoration. `current_map.run()` supplies [no subprocess timeout](/Users/jdlien/code/ak820-pro/scripts/current_map.py:183).

   Make relay 1 release, data restoration, mask/stage restoration, and relay 2 restoration independent attempts, with bounded commands and truthful status reporting.

   There is also **no deep-arm cancellation operation**: sub 4, arg 0 only reads. An aborted script can leave `DS_ARMED` pending if USB restoration fails. Add cancellation and use it during cleanup; clear light features before the deep test as well.

5. **Should-fix — daily-build exclusion is incomplete; default-off behavior is otherwise sound.**

   The daily flavor omits `AK820_DEV_HOOKS`, so the idle-hook replacement, HID commands, and substantive `sleep.c` implementation are excluded. But [scan-only state and checks](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/drivers/led/sn32f2xx.c:665) have no dev guard. The ISR checks at line 726 remain applicable to daily hardware configuration, including a volatile request read. [`sleep_task()` is also called unconditionally](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/ak820pro.c:628), with an empty daily implementation.

   Guard those additions if “daily has none of it” is the requirement. The build’s existing flavor check verifies the dev-hook marker, not absence of these driver branches.

   **What is fine with features off after boot:**

   - `ds_state`, `ls_mask`, and both WFI flags start inactive.
   - `deep_task()` returns immediately; `light_task()` performs bounded checks/assignments and requests normal scanning. It does not stop/start timers or yield.
   - Removing the idle-entry `PMU_CTRL=4` write is harmless while no WFI executes. Mode selection alone does not enter Sleep—manual pp. 75, 79.
   - `power_task()` remains in its original place, after parameter status handling and before RTC/animation/display housekeeping.
   - Moving the indicator output cache to file scope preserves its initialization and ordinary behavior.

   Thus behavior is functionally unchanged in the relevant ways, **not cycle-identical**. I see no added default-off operation capable of consuming 25 ms; exact timing still requires measurement.

6. **Should-fix — scan-only’s steady-state design is sound, but transitions are not covered by the claimed timing guarantee.**

   [sn32f2xx.c:726](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/drivers/led/sn32f2xx.c:726) correctly changes **PWMD1 alone** to period 3000, hence MR12=2999. Exit restores period 256/MR12=255. The final `TC=UINT16_MAX` re-arm prevents a shortened period from waiting for an entire wrap—manual pp. 125, 132.

   The separate counter visits all six key rows while freezing both LED-field counters. Blanking disables column PWM ownership; subsequent scan-only calls do not re-enable it—manual pp. 138, 143. The flash-program guard, dirty publication, instrumentation, and auxiliary-counter re-arm remain intact. **Those parts are fine.**

   Two qualifications:

   - The independent scan-only phase can follow an already-old normal sample with nearly a whole scan-only cycle; exit can do the reverse. Transition gaps can approach the sum of the two cycles, roughly **8 ms**, rather than the claimed ≤6 ms. The script resets maxima **after settling**, hiding entry transitions.
   - `pwmChangePeriodI()` is called before the callback’s `chSysLockFromISR()`. Its current implementation is simple and single-owner here, but the plan’s proper I-class critical section was not implemented.

   [`sn32f2xx_flush()`](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/drivers/led/sn32f2xx.c:939) only publishes colors; it cannot undo scan-only blanking. LED-drive accounting remains based on the committed frame, however, so it can briefly overcount forced darkness before the zero-scaled frame commits. That is a measurement caveat, not a default-off regression.

7. **Should-fix — light-sleep gates are good, but tick bookkeeping and idle entry need corrections.**

   **The gates are appropriate:** [light_task():50](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/sleep.c:50) uses effective RGB darkness, effective backlight level, and wanted indicators. It does not mistake an instantaneous PWM-low phase for darkness. The yield is correctly at the completed pass’s end, after the watchdog kick.

   [`pwm_tick_stopI/startI()`](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/indicators.c:192) preserve the outer lock, force the pins low, invalidate `ind_last_out`, and restore keyed MR0RST on restart. However:

   - The underlying [GPT stop routine](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios-contrib/os/hal/ports/SN32/LLD/SN32F2xx/CT/hal_gpt_lld.c:516) writes MCTRL without its required `0x5A` key and uses read-modify-write on write-only IC. These do not establish clean interrupt shutdown—manual pp. 128, 147. Counter stopping works, but explicitly clear the peripheral/NVIC pending state.
   - Deep wake unconditionally starts the tick. If light bit 3 was already applied, `ls_applied` still says it is stopped, so the next `light_task()` sees no transition and leaves it running. Restore its previous state or reconcile the cached applied state.
   - The [idle hook](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/chconf.h:35) leaves flag check, PMU selection, counter increment, and WFI interruptible. A preemption can clear PMU_CTRL through the leave hook, then return to the old WFI instruction; the recorded entry may therefore use mode 0 instead of requested mode 4. Make this sequence atomic using the audited masked-WFI idle pattern.

   A 4 ms yield also does **not** guarantee a ≤5.5 ms parser-service interval: loop work and interrupts add to it. Measure complete pass gaps and UART errors.

8. **Deep-sleep sequence — mostly correct, with specific remaining qualifications.**

   | Check | Assessment |
   |---|---|
   | **Locked section** | **Fine:** I found no hidden unlock in the called GPIO/PAL, PWM-disable, GPT I-class, or RTC-add paths. [`_pal_lld_setpadmode()`](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios-contrib/os/hal/ports/SN32/LLD/SN32F2xx/GPIO/hal_pal_lld.c:202) performs register operations directly. The new RTC helper only adds to `rtc_time`. |
   | **Outputs and wake GPIO** | **Fine:** [sleep.c:198](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/sleep.c:198) makes all key rows low and columns pull-up inputs. The masks resolve to **P0 `0x00F0`, P2 `0x43FF`**. IS=0, IBS=0, IEV=1, IC-before-IE, and the physical DATA read correctly implement falling-edge wake and catch a held key whose edge was cleared. Manual pp. 75, 81–84. LED row modes remain outputs throughout. |
   | **RTC/NVIC** | **Fine:** [take_second():167](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/sleep.c:167) clears both SECIF and NVIC pending state. Entry and final pending seconds are consumed; coincident GPIO/RTC wakes account for the second before taking the key exit. Temporary GPIO/RTC enables are disabled before saved enables return. Manual pp. 44–45, 156–157. Masked-WFI wake remains a hardware integration test; the Sonix manual does not explicitly describe that PRIMASK case. |
   | **Clock and flash** | **Fine ordering:** [sleep.c:232](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/sleep.c:232) checks IHRC readiness, selects it, waits for selection, then disables PLL and writes `0x5AFA0000`. Restore writes saved flash timing—normally `0x5AFA0029`—before selecting 48 MHz. These are timing writes, not firmware programming. Manual pp. 61–64, 255. **Should-fix:** PLL-ready polling is bounded and resets on failure, but both clock-selection waits remain unbounded. |
   | **ADC** | **Fine for this board:** EOC/ADS are removed from the saved ADM, ADENB bit 12 is cleared, and restoration precedes the settling delay. Battery conversions leave GCHS bit 5 clear, preserving P2 GPIO. Manual pp. 96–100. |
   | **SysTick/system time** | **Fine for the stated experiment:** disabling/clearing SysTick then pending its handler lets ChibiOS service/re-arm its alarm. It does not attempt the invalid saved-VAL restoration. CT16B5 freezes in Deep-sleep and advances at quarter speed during IHRC service; that accepted limitation is accurately stated in “As built.” Manual pp. 41–42, 78, 125. The earlier claim that no timeout is armed remains false: QMK’s overflow-maintenance timer exists. |
   | **Restore** | Rows return high, clocks precede UART/ADC/timer use, and both software RTC clocks are advanced before unlocking. **Should-fix:** [sn32f2xx_sleep_exitI():810](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/drivers/led/sn32f2xx.c:810) re-arms counters without explicitly clearing stale PWMD1 match/pending state. Handle that alongside the GPT cleanup. |
   | **RTC accounting** | **Fine:** [rtc_account_sleepI():464](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:464) updates `rtc_seconds_count` and LLD calendar time, saturates the calibration interval, and invalidates SOF/window continuity. It does not restart RTC or rewrite SECCNTV, preserving its fraction—manual pp. 156–157. |
   | **Record** | Layout fits the 24-byte payload, and key gain is collected after normal processing resumes. **Should-fix:** [sleep.c:264](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/sleep.c:264) increments `strays` after every ordinary RTC wake too. A normal 120-second cap reports about 119 strays; a cap above 1000 can terminate as “strays.” Increment only when neither RTC nor GPIO caused the wake. |

   **Record notes:** `slept=true` means WFI was attempted, not proof of a measurable residence interval. `secs` counts consumed second boundaries, not precise elapsed duration. PLL ceiling failure resets before its record can be read; the script needs retained reset evidence for that case.

9. **Preconditions — reasonable for the bench, with additional checks advisable.**

   [blockers():147](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/sleep.c:147) correctly checks cooked keys, USB-active state, protocol TX/ACK state plus TEMT, queued/display work, keyboard-config writes/EFL state, RTC slew/reload, column mapping, and an actually enabled non-degraded watchdog. The later DATA read closes the important key-arrival gap.

   **Should-fix:** add the serial output-queue check described above and explicit SPI/DMA idle checks. The display callback normally proves completion and raises both chip selects, but [the DMA completion handler’s shifter wait is bounded and can fall through](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios-contrib/os/hal/ports/SN32/LLD/SN32F2xx/SPI/hal_spi_v2_lld.c:293); `blit_done` alone is weaker than checking DMAEN and both BUSY states.

   **Note:** `battery_usb_configured()` means exactly `state == USB_ACTIVE`. A suspended configured host passes this gate. That matches the documented bench definition, but it does not prove physical disconnection. Recheck admission under the final lock if reconnection can race entry.

   Persistent blockers are possible and acceptable: held keys, active USB, continuous TX/display/write activity, unresolved RTC work, or a watchdog disabled for the whole boot. The implementation gives up **60 seconds after the requested delay**, as intended. It never enters sleep to resolve a blocker.

10. **Protocol and scripts — wire layouts match; the current tests cannot substantiate all claimed passes.**

   **Fine:** both scripts send 32-byte op-14 requests. Sub 3’s mask/applied/WFI/scan-only offsets and sub 4’s little-endian cap and record decode match [hid_protocol.c:964](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/hid_protocol.c:964). Reserved record bytes are zero. Immediate sub-3 replies describe the previously applied state; the sizing script waits before its meaningful checks.

   **Should-fix protocol details:**

   - The reply comment promises effective yield, but no yield byte is written. Firmware silently maps invalid yields to 2 ms; `--masks 3:5` would be labelled as 5 ms by the script.
   - Check length before reading `data[5]`. Fixed-size raw HID protects the current transport, but the parser’s validation order is wrong.
   - Validate reply length and echoed **op 14**, not just the first three bytes and sub-op.
   - Add the deep cancellation operation.

   **Should-fix measurement/pass criteria:**

   - [sleep_sizing.py:92](/Users/jdlien/code/ak820-pro/scripts/sleep_sizing.py:92) takes its stall baseline **after the 60-second measurement window**. Its pass ignores stalls during that window.
   - It logs WFI count, scan-only state, and timebase ratio without requiring them to demonstrate the requested behavior. It checks neither ACK timeouts/drops/UART errors nor watchdog/reset continuity.
   - Board press/edge counts do not prove delivered BLE reports. The script does not verify F19 mapping or host delivery.
   - [deep_sleep.py:99](/Users/jdlien/code/ak820-pro/scripts/deep_sleep.py:99) presses at a fixed time after arming; it does not actually watch for confirmed entry. A blocked run can receive its press while still awake.
   - The deep script has **no pass/fail predicate**. “Blocked,” “strays,” “did not sleep,” or zero waking-key gain can all finish successfully. `--no-feed` does not retrieve watchdog/reset breadcrumbs, so it cannot establish why the record disappeared.
   - Validate arguments. In particular, `--cap 0` means 120 seconds to firmware but only 25 seconds of waiting in the script’s no-press path.
   - The default sequences and charge-register integration are sensible. Bit 4’s matched data-off comparison and the P0/other-row manual wake tests remain separate work; disposition 10’s “Done” overstates what these scripts implement.

**Recoverability:** I found no flash erase/program operation or persistent sleep enable in these changes. Reset returns the experiments to off. The direct watchdog feed value is correct—manual p. 153—and checking watchdog availability is a real improvement. The remaining routes to needing hands are the unexpired WFI/USB probe, failed bench cleanup, and an unsuccessful deep-wake/watchdog integration test. JD’s presence covers the last case; it does not make the scripts’ current success output conclusive.