**No must-fix or should-fix findings. I found no source-review blocker to flashing `b2a07e0d37` over `ce70c8da41` tonight.** The replacement preserves the admission decision and removes the runtime conversion. All findings below are **notes**.

1. **Note — the comparisons are equivalent for this configuration.**

   The board selects CT16B5 in [mcuconf.h:31](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/mcuconf.h:31). Its configuration specifies **16-bit system time at 187,500 Hz** in [board chconf.h:24](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/platforms/chibios/boards/SN_SN32F290/configs/chconf.h:24). The driver reads the counter masked to 16 bits in [hal_st_lld.h:147](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios-contrib/os/hal/ports/SN32/LLD/SN32F2xx/SysTick/hal_st_lld.h:147).

   `sysinterval_t` is **32-bit**, while `systime_t` is **16-bit**: see [common chconf.h:95](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/platforms/chibios/boards/common/configs/chconf.h:95) and [chtime.h:106](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios/os/rt/include/chtime.h:106).

   With elapsed ticks `d` and `RTC_ADMIT_US = 10000` ([rtc.c:1639](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1639)):

   ```text
   Old: ceil(d × 1,000,000 / 187,500) < 10,000
   New: d < ceil(10,000 × 187,500 / 1,000,000) = 1,875
   ```

   Both accept **0–1,874 ticks** and reject **1,875 onward**. At 1,874 ticks, the old conversion returns 9,995 µs; at 1,875, exactly 10,000 µs. Both macros round upward: [TIME_I2US:273](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios/os/rt/include/chtime.h:273), [TIME_US2I:219](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios/os/rt/include/chtime.h:219). **There is no changed off-by-one admission or refusal.**

   The inner `(systime_t)` cast in [sleep.c:38](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/sleep.c:38) preserves subtraction modulo 65,536 **before** widening. Crossing counter zero works correctly; an elapsed interval of a whole counter period—**349.525⅓ ms**—aliases. That limitation existed unchanged in the parent. The mark is renewed at each pass’s end ([sleep.c:874](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/sleep.c:874)).

   Relative to physical time, the existing tick granularity can refuse admission less than one tick—5⅓ µs—early. It does not admit a sampled interval past 10 ms through rounding. Interrupts and work between the sample and transaction start can still move the actual start past 10 ms; this is an admission check, not an atomic deadline guarantee.

2. **Note — `TIME_US2I(RTC_ADMIT_US)` folds to the constant `1875`.**

   The macro contains only casts and integer arithmetic on constants ([chtime.h:219](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios/os/rt/include/chtime.h:219)). Although `time_conv_t` is `uint64_t` here ([chtime.h:131](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios/os/rt/include/chtime.h:131)), that arithmetic is a constant expression. Neither changed comparison requires a runtime 64-bit divide: [rtc.c:1657](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1657), [rtc.c:2019](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:2019).

3. **Note — no missed callers or include-order problem found.**

   Searching the entire board directory found exactly two callers in the parent, both replaced at the locations above. No `sleep_pass_us` reference remains.

   The five `sleep.h` users are [ak820pro.c:29](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/ak820pro.c:29), [sleep.c:9](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/sleep.c:9), [kb_eeconfig.c:16](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/kb_eeconfig.c:16), [hid_protocol.c:23](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/hid_protocol.c:23), and [rtc.c:15](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:15). All already include `quantum.h`, directly or through `ak820pro.h`, first.

   That existing chain includes `wait.h` ([quantum.h:19](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/quantum/quantum.h:19)), whose ChibiOS implementation includes `ch.h` ([platform _wait.h:18](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/platforms/chibios/_wait.h:18)). `ch.h` is guarded ([ch.h:31](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios/os/rt/include/ch.h:31)). The new include therefore supplies the public type explicitly without changing initialization or declaration order for these users.

4. **Note — no other newly introduced every-pass or per-interrupt 64-bit time conversion found versus `0ae4cf8c80`.**

   I checked the keyboard diff and the changed `chibios-contrib` driver hooks.

   | Path | Result |
   |---|---|
   | Dev accounting scopes | New `ACCT_CTX` operations are byte stores. Timing remains raw counter reads and subtraction: [loop_acct.h:67](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/loop_acct.h:67). |
   | UART, USB, system-timer and I2C hooks | [halconf.h:45](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/halconf.h:45) wires them to raw-tick accounting. `od_now()` reads the counter directly; the handlers subtract and compare: [overrun_diag.c:19](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/overrun_diag.c:19), [overrun_diag.c:104](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/overrun_diag.c:104). |
   | 20 kHz indicator interrupt | The added `od_tick()` call is dev-only ([indicators.c:149](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/indicators.c:149)). The monitor defaults off; when enabled it uses raw ticks and constant thresholds ([overrun_diag.c:206](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/overrun_diag.c:206)). Its ring timestamp calls `rtc_seconds_since_boot()`, a plain counter return ([rtc.c:1725](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1725)). |
   | New stuck-bus test | Adds two `timer_read32()` calls, but only after `stuck_pending` passes its guard—not every pass: [rtc.c:1950](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1950). |
   | Burst diagnostics | Nonconstant `TIME_US2I(mask_us)`, `TIME_MS2I(bu_gap())`, and timer reads already existed. They execute at burst setup or completed burst steps; waiting uses tick differences: [rtc.c:1882](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1882), [rtc.c:1903](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1903). |
   | Display probes | `DPROBE` records raw ticks ([display.c:42](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:42)); its existing 64-bit conversion occurs when filling a requested diagnostic reply ([display.c:3160](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:3160)). |

   **Existing conversions remain:** health sampling ([health.c:138](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/health.c:138)), the housekeeping cadence check ([ak820pro.c:588](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/ak820pro.c:588)), and the display recovery check ([display.c:2866](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:2866)) all predate the baseline. `timer_elapsed32()` calls `timer_read32()`, which performs `TIME_I2MS(ticks)` ([timer.c:14](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/platforms/timer.c:14), [ChibiOS timer.c:119](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/platforms/chibios/timer.c:119)).

   There is also an existing explicit 64-bit division in `board_ms_now()`, reached every pass **while acquisition is active**; it was already present in `0ae4cf8c80`: [rtc.c:1303](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1303), [rtc.c:1552](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1552).

5. **Note — flashing assessment.**

   This commit removes the newly frequent conversion without changing the sampled admission boundary. The dev hooks still add CPU work, including the monitor’s disabled check at 20 kHz, so source inspection cannot establish that the measured `+39` will return to `+11`.

   Review was read-only and confined to the requested directory. No builds or hardware runs were performed.