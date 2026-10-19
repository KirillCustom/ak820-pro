**I would revise the plan before building it.** Keeping QMK’s clock as awake time is sound, and the existing locked hardware sequence is largely sound. The main gaps are the wake-recovery state, RTC ordering, long-sleep counter widths, and log/replay accounting.

This was a read-only source and plan review. I did not modify files, build, run project scripts, or access hardware.

Below, `kb/` means `qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/`; other firmware paths are relative to `qmk_firmware-ak820pro/`.

**1. QMK’s timer: keep the design, but make the freeze explicit.**

**Must-fix — disabling SysTick does not freeze the underlying time counter throughout the sleep routine.** `deep_sleep()` disables SysTick’s alarm, but leaves CT16B5 running during entry, every RTC service interval, and restoration. CT16B5 stops during Deep-sleep itself, then resumes when HCLK resumes. Thus an overnight sleep accumulates timer ticks during its thousands of brief wakes. See [sleep.c:297](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/sleep.c:297), [sleep.c:335](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/sleep.c:335), and [hal_st_lld.c:146](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios-contrib/os/hal/ports/SN32/LLD/SN32F2xx/SysTick/hal_st_lld.c:146).

The configured counter is 16-bit at 187,500 Hz: a wrap represents about 350 ms of normal-clock time. QMK explicitly requires observing it at least once per wrap; its maintenance callback cannot run under this sleep lock. Accumulated service time can therefore become an apparent loop stall, and sufficiently long accumulation can violate the wrap-extension contract. The existing hour measurement does not establish safety for the proposed maximum. See [timer.c:14](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/platforms/chibios/timer.c:14) and [board chconf.h:24](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/platforms/chibios/boards/SN_SN32F290/configs/chconf.h:24).

Specify preservation/pause/restoration of the ST counter across the long locked interval, including its prescaler state and alarm re-arm. Merely declining to add slept milliseconds is insufficient. Do not reset QMK’s global timer or advance all ChibiOS virtual timers by the sleep duration.

The timer-consumer audit follows. Repeated reads belonging to one operation are grouped; these are the relevant consumers, including indirect use through `sync_timer` and the LED driver’s supplied timestamp.

| Consumer and source locations | Disposition with an actually frozen clock |
|---|---|
| Battery awake energy and minute average: [battery.c:667](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/battery.c:667). Committed LED drive: [sn32f2xx.c:935](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/drivers/led/sn32f2xx.c:935), 942, 972. | **Correct to freeze.** Add sleep energy separately; do not integrate the old committed frame over the night. |
| Battery relaxation: [battery.c:858](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/battery.c:858), 870. | **Missed; must-fix.** The 30-minute physical relaxation after unplug/handover must include sleep. Otherwise an overnight rest can leave `relax_pending` and re-seating unfinished. Age this explicitly, then demand fresh `5C` evidence. |
| Battery log start, boundary and header age: [battery.c:1343](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/battery.c:1343), 1357, 1394. | Plan recognizes period catch-up, but misses the necessary separate wall-time cursor and live/backlog accumulator separation. Detailed below. |
| Ladder activity and boot announcement: [power.c:176](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/power.c:176); `quantum/keyboard.c:160,168,171,179,182`. | Explicit wake activity is required. An encoder edge that produces no decoded event does **not** reset QMK activity. Charge/backstop wakes need a separate recovery hold. |
| Radio ACK/retry, drain timeout, cold select, bounce/select/pair state: `bluetooth/ch582f_ajazz.c:700,709,715,717,743,745,874,875,900,908,931,1067,1079,1090,1103,1335`. | Pausing is appropriate for queued work; admission must exclude pending transactions/pairing. Normal production idle thresholds let short transition timers finish. Do not replay hours of retries. |
| Radio battery polling and battery-report age: [ch582f_ajazz.c:987](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:987), 1160, 1164, 1348. | Immediate poll is covered. **Missed:** forgetting the battery estimator’s ring does not invalidate the radio driver’s cached report age. Mark that observation stale too, without claiming a fresh sample until one arrives. |
| Radio link promotion and dev lock channel/LED age: `ch582f_ajazz.c:314,326,1237,1242,1300,1420`; `hid_protocol.c:935`. | Plan covers promotion and lock-channel reset. Preserve the distinction between resetting diagnostics and discarding a partial lock-channel message. Last-known connection state remaining stale is an explicitly accepted limitation. |
| Playback heartbeat and host-text expiry: [display.c:419](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:419), 441, 2130, 2825. | **Missed; should-fix.** Three-minute host text can survive Minimal’s 30-second admission and reappear after hours. Expire/invalidate host liveness on wake. Playback’s 20-second timeout normally expires before production sleep, but include it in the same policy for forced/short sleeps. |
| Overlay/message/connection holds and blink: `display.c:1461,2083,2088,2279,2298,2305,2315,2319,2348,2376,2489`. | Freezing is the plan’s chosen presentation policy and is fine. These are visibility durations, unlike host-data freshness. |
| Display busy/healing timers; LCD DMA timing: `display.c:2644,2645,2662,2663`; `graphics/lcd_bus.c:703,750,765,772,793`. | Fine with the existing no-DMA/no-queued-work admission. A live operation must not straddle sleep. |
| Pair/debug holds, parameter repeat, modified consumer release: `bt_ui.c:129,159,173`; `ak820pro.c:329,423`; `param_overlay.c:88,89,104,111,112`; `consumer_mod.c:54,58,66`. | Fine at the real 30-second-or-longer thresholds: physical holds block admission and short release deadlines finish first. A shortened dev threshold must retain that invariant. |
| Scroll indicator settling/own-key window and cell grace: `indicators.c:232,236,297,321,357,358,363`. | Fine at production thresholds. Lock-channel state must be cleared as planned. |
| Deferred persistence: `kb_eeconfig.c:52,63`; `ak820pro.c:729,733,736`; [rtc_persist.c:32](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc_persist.c:32). | Fine. Flash admission covers pending board writes. RTC save spacing becomes six **awake** hours, which is conservative for wear. |
| USB remote-wake retry: `ak820pro.c:158,159`; USB report timestamps/idle resend: `tmk_core/protocol/chibios/usb_report_handling.c:23,55,134,150,174,188`. | Fine to pause; USB resume/reset owns endpoint recovery. Do not synthesize hours of report-idle expiry. |
| Housekeeping/statistics/profiling/uptime: `ak820pro.c:208,216,234–278,311,538,585,586,613`; `health.c:79,82,105,140,285,363,411`; `loop_acct.c:88,186`; `flash_stats.c:80`; `display.c:976,992`; radio trace/statistics at 631,1125. | Fine as **awake uptime/rates**, provided the ST freeze is real. Document that meaning in host output. |
| Sleep’s own admission, arm, dev TTL and post-wake observation timers: `sleep.c:38,73,79,91,95,161,417,424,433,438`. | Remove production dependence on the dev arm/deadline state. New USB recovery timers must keep progressing until re-attachment completes. |

For enabled QMK features:

- **Debounce, tap/hold and Space Cadet are fine with the frozen clock.** Relevant sites are [sym_defer_pk.c:43](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/quantum/debounce/sym_defer_pk.c:43), `quantum/keyboard.c:576`, event timestamps in `keyboard.h`, and `process_space_cadet.c:93,101`. Existing input decisions have settled long before automatic sleep; the waking key gets normal debounce time.
- **RGB animation/heatmap timers are fine to pause.** `rgb_matrix.c:282,284,316` and `typing_heatmap_anim.h:68,72` need no wall-time catch-up while dark.
- **Deferred execution is fine for the current caller.** `deferred_exec.c:64,86,119` schedules awake-time work; the board’s boot redraw is finished before normal sleep admission.
- **VIA uptime, command diagnostics and LED modification age** (`via.c:309`, `command.c:242`, `led.c:65,178`) become awake-time diagnostics.
- Encoder quadrature and dip switches do not have an elapsed-time timeout to repair. Their issue is sampling/state, discussed below.
- USB/I2C/SPI timeout machinery must not have an operation outstanding at entry. Wear-leveling’s elapsed-time reads are synchronous erase diagnostics, not a cross-sleep deadline.
- I found no enabled timeout-based oneshot configuration requiring wall-time expiry. Disabled features such as mouse keys and audio should not be added to this review as hypothetical consumers.

Thus the principal omissions from section 3 are **physical battery relaxation, host-content freshness, radio battery-age validity, and the log’s independent timebase**, plus making the underlying timer freeze real.

**2. PCF elapsed time and phase recovery: sound approach, incomplete ordering.**

**Fine:** two valid whole-second PCF timestamps provide elapsed time with approximately one second of quantization uncertainty. Keeping the counted RTC seconds as an explicitly marked fallback is reasonable. The fallback is not accurate wall time: the hour run counted 3,139 seconds for 3,600 real seconds. See [the measurement](/Users/jdlien/code/ak820-pro/history/deep-sleep-20261019-1h/README.md:13).

**Must-fix — give RTC wake recovery one owner and a defined sequence.**

1. Admission must require no pending PCF write, **and `ps_state == PS_IDLE`**, plus no acquisition, slew or reload. `pcf_pending == false` alone is insufficient: verification can clear it while the machine remains in the bracket phase, and STOP recovery has its own state. See [rtc.c:1274](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1274), 1379 and 1424.
2. Invalidate the PCF trim window **before any normal discipline call can see the corrected counters**. Existing `rtc_account_sleepI()` makes the 60-second check immediately due, while `rtc_task()` has no acquisition/write-machine exclusion around `rtc_clock_discipline()`. A discipline call between coarse accounting and final correction can seed the wrong new window. See [rtc.c:464](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:464), [rtc.c:962](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:962), [rtc.c:1540](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1540).
3. Suppress/rebase the ordinary 60-second discipline during coarse recovery and phase acquisition. Otherwise it can perform redundant I2C, snap the calendar during acquisition, and seed a window across a correction.
4. Correct `rtc_seconds_count` atomically, using a signed, checked difference between PCF elapsed seconds and counted seconds. Do not add the correction twice to the calendar: the subsequent absolute calendar set supersedes the coarse calendar addition.
5. Read/repair outside the interrupt lock, but before exposing partially recovered state to ordinary RTC/display work.

**Must-fix — “first pass after wake” can race a new host sync.** Raw HID executes before housekeeping. A new `rtc_set_time_ms()` can therefore run before a recovery hook placed in the next housekeeping pass. Blindly clearing `host_synced` or applying the PCF snapshot there can overwrite a newer host time and re-arm acquisition against its pending PCF write. See [main.c:53](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/quantum/main.c:53) and [rtc.c:1127](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1127).

Complete the coarse read/correction immediately after unlocking, before returning to normal dispatch, or use a recovery generation/cancellation rule under which a newer host sync wins. Clear the *old* host-sync status once; never clear a new one.

**Should-fix — reset the complete SOF continuity state.** Clearing `win_valid` and `fn_valid` protects the estimator window, but leaves `sof_epoch`, good/bad streaks and reference qualification unchanged. Explicitly break the continuity epoch and restart qualification. Also move invalidation outside `rtc_account_sleepI()`’s `seconds == 0` early return: a subsecond sleep still breaks observation continuity. See [rtc.c:464](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:464), [rtc.c:548](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:548), [rtc.c:605](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:605).

**Acquisition itself is reusable, with qualifications.** Reset all its state, including failure count, previous edge, timeout origin, uncertainty and result. Treat `ACQ_ABORTED` as terminal for sleep admission; otherwise a failed PCF can keep the board awake indefinitely. Define the invalid-RTC fallback, because `ACQ_WAIT_SPLASH` waits indefinitely for `rtc_valid`. The existing FORCE_STEP + SKIP_PCF call correctly avoids rewriting the reference clock, and clearing `host_synced` immediately after that internally generated set is correct. See [rtc.c:1442](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1442).

The whole-second calendar set does **not** establish phase. The LLD calendar setter updates software calendar state; acquisition supplies the phase correction later. The boot’s measured accuracy is not a guarantee under wake load: sampling happens every eight eligible passes, and the edge path performs multiple I2C transactions.

**`rtc_persist` is otherwise fine.** Preserve a previously valid awake-period candidate and its save budget. Prevent contaminated post-sleep proposals upstream; there is no need to persist on every wake or discard a good period merely because the board slept.

**3. Wake pins and encoder state: register choice is right; admission and recovery need more detail.**

**Fine:** P1.2/IRQ30 and P0.10/IRQ31 match the encoder, and P1.16 shares IRQ30. The manual’s GPIO edge configuration supports the proposed senses. PAL callbacks being off avoids a competing callback owner. See [docs/sn32f299.md:379](/Users/jdlien/code/ak820-pro/docs/sn32f299.md:379) and [keyboard.json:34](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/keyboard.json:34).

Preserve the existing sequence’s important properties:

- PRIMASK remains set through clock restoration.
- Both-edge sensing applies only to encoder bits; CHRG and matrix columns remain falling-edge.
- P1 joins **both** peripheral cleanup and NVIC disable/pending cleanup before restoring old enables.
- A pending CHRG condition is checked after arming, as existing columns are checked after edge clearing.

See the current good entry and teardown patterns at [sleep.c:299](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/sleep.c:299) and [sleep.c:379](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/sleep.c:379).

**Must-fix — `A == B` before and after arming is not sufficient.** `00 → 11` satisfies equality at both checks while movement has occurred. “Already in its wake state” is also undefined for a both-edge input. Compare the actual two-bit states against the sampled/decoder state and account for pending edges; do not clear an intervening movement and then sleep because the final pair is another resting state.

The encoder driver retains both previous state and fractional pulses. Missing intermediate states can lose a detent or affect the next direction decision. See [encoder_quadrature.c:172](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/drivers/encoder/encoder_quadrature.c:172) and 198. Specify how the first wake sample reaches that decoder. Do not call `encoder_init()` as a generic repair: it clears QMK’s queue. Do not invent direction from GPIO RIS bits, which contain no edge order.

**Must-fix — a wake needs a recovery hold independent of decoded input.** The plan already says backstop wakes wait for poll/log completion, but it needs an explicit state covering:

- coarse RTC correction and completion/abort of acquisition;
- a fresh battery response or bounded failure timeout;
- log backlog completion;
- enough fresh supply samples to classify CHRG/VDD;
- an outstanding USB disconnect/reconnect;
- the first matrix/encoder processing opportunity.

QMK updates encoder activity only after an event is decoded. A wake edge with a lost first detent can therefore leave the old idle deadline satisfied and immediately send the board back to sleep. Likewise, supply classification requires multiple 10 Hz observations, not merely seeing the edge; see [battery.c:190](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/battery.c:190).

Reset user-idle activity for key/knob wake causes themselves. Keep charge/backstop recovery separate from user activity, as the plan intends.

**Should-fix:** make “no wrong-direction event” an acceptance criterion. “At most one lost detent” does not reject a backward volume step. Test both resting states, both directions, reversals, a wiggle producing no detent, and rapid turns.

CHRG’s no-pack pulses are manageable with the existing supply filtering and recovery hold. Without that hold, they can create repeated wake/re-sleep attempts.

**4. USB re-attachment: the two calls are non-blocking and plausibly sufficient, with explicit limits.**

On this LLD, disconnect and connect simply clear/set `CFG.DPPU`. They contain no delay and do not restart the driver. See [hal_usb_lld.h:376](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios-contrib/os/hal/ports/SN32/LLD/SN32F2xx/USB/hal_usb_lld.h:376).

With PHY restored, a D+ detach/attach requests fresh enumeration. A resulting host bus reset runs `_usb_reset()` and resets USB state/endpoints. That supports trying the proposed sequence without `usbStop()/usbStart()`. It does **not** prove recovery from every possible peripheral fault. See [hal_usb_lld.c:234](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios-contrib/os/hal/ports/SN32/LLD/SN32F2xx/USB/hal_usb_lld.c:234).

**Fine:** avoiding `restart_usb_driver()` is correct; its 50 ms wait violates the loop budget. See [usb_main.c:350](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/tmk_core/protocol/chibios/usb_main.c:350).

**Must-fix implementation requirements:**

- The 100 ms disconnect gap must be a state-machine deadline, never a blocking wait.
- Keep the board awake until D+ is restored, including a backstop/charge wake with no decoded input.
- Protect the CFG read-modify-write against the USB ISR’s other CFG updates.
- Latch whether any fresh SOFs arrived after this wake; a “recently live” test is not equivalent to “none arrived.”
- Define pending/success/timeout observation states and associate them with a particular sleep, so another sleep does not leave a misleading result.

**Suspended host:** absence of SOFs cannot distinguish suspension from disconnection. Pulling D+ down can turn a resumable device into a detached device, discard queued reports during reset/reconfiguration, and forfeit the existing remote-wakeup relationship. The plan acknowledges that tradeoff. It is acceptable only under its stated battery-only admission/bench assumptions; it is not transparent USB resume.

Also, re-attachment cannot wake a sleeping MCU. A full-pack cable insertion that produces no CHRG edge still waits for a key/knob/backstop. The plan’s fallback wording should say this plainly.

**5. Daily builds: the guard split is right, but misses the parser’s guard.**

**Must-fix:** moving `deep_sleep()` and LED hooks is not the whole production split. The actual parser-window reset remains under `AK820_DEV_HOOKS` at [ch582f_ajazz.c:1047](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:1047), with related declarations/state also guarded. Make the resync request **and its consumer** available in every sleep-capable build. Draining UART hardware and `SD2.iqueue` alone leaves the three-byte parser history stale.

The production path should contain:

- automatic ladder admission and repeated eligibility checks;
- the locked deep-sleep hardware path, bounded clock restoration and watchdog feeding;
- LED enter/exit hooks and the 20 kHz tick stop/start;
- UART/parser recovery, RTC/battery/log recovery, USB recovery and read-only sleep status.

Keep dev-only:

- light-sleep masks, scan-only mode, yielding and idle WFI/PMU hooks;
- forced arm/cancel and shortened-threshold controls;
- `ds_no_feed`, forced power stages, op 14 mutations and raw bootloader access.

The existing 60-second arm give-up and `DS_DONE` terminal state cannot drive ordinary standby. See [sleep.c:133](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/sleep.c:133) and 438.

Split LED definitions and declarations consistently; retain the I-class calls, PWM pending clear and synchronized counter re-arm at [sn32f2xx.c:803](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/drivers/led/sn32f2xx.c:803). The tick hooks already exist outside the dev guard.

**Should-fix:** extend structural validation beyond presence/absence of `ak820_dev_hooks`. Current build checks inspect that marker and raw bootloader marker, not accidental individual mutation handlers or the existence of the production sleep path. Exercise daily, fpb and instrumented builds as well as dev; instrumented currently does not define `AK820_DEV_HOOKS`. See [build.sh:24](/Users/jdlien/code/ak820-pro/build.sh:24) and [build.sh:127](/Users/jdlien/code/ak820-pro/build.sh:127).

**6. Gauge, log v7 and replay: the energy model is right; catch-up needs a different data flow.**

**Fine:** adding `13,350 µW × slept_ms` to used energy and the discharge count, while excluding it from `e_minute_nj` and the awake-minute accumulator, produces the intended “hours of use.” Existing `energy_tick()` already averages awake elapsed time. Recompute time left immediately after the sleep debit; currently it is only recomputed at a minute boundary. See [battery.c:666](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/battery.c:666).

Use 64-bit arithmetic from the first multiplication. Preserve fractional energy and existing unknown-level, cut, floor and re-seat behavior. Clear stale `band_hold` before applying sleep energy: `c5_forget()` alone does not perform the later `energy_tick()` branch that clears it.

The constant is a reasonable measured starting point; the planned high/mid/low pack validation is necessary to establish constant-power accuracy.

**Must-fix — historical catch-up cannot reuse the live accumulator unchanged.** Current `log_task()`:

- collects this call’s live sample before closing an entry;
- writes all accumulated `e_period_nj`;
- writes the **current** level, backlight, charging state and flags;
- resets the shared accumulators afterward.

See [battery.c:1343](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/battery.c:1343).

Calling a modified version once per 100 ms after wake would otherwise put awake energy/reports into historical sleeping entries, write the final waking level into every historical boundary, and erase current-period observations.

Specify a frozen pre-sleep partial-period record, sleep debt/cursor, and separate live post-wake accumulation. Historical boundaries need their corresponding energy/level state. Fully sleeping entries need zero `5C` observations and physically dark load, not whatever the waking display currently draws.

**Must-fix — give the log its own monotonic elapsed-time base.** Fixed 900-second entry spacing is insufficient. `battery_log_read()` dates the newest entry using `timer_elapsed32(log_last_at)`, and the host anchors every row from that value. With frozen QMK time, naïvely advancing `acc_start` through sleep either leaves the wrong age or puts the boundary in the timer’s future. See [battery.c:1394](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/battery.c:1394) and [ak820battery.py:245](/Users/jdlien/code/ak820-pro/hostagent/ak820battery.py:245).

Use awake elapsed time plus explicitly accounted sleep for log dating. Avoid using a settable calendar directly, because host clock corrections would then move log boundaries.

**Should-fix — dumps during catch-up.** The host retries a moving ring three times, immediately. A ring advancing every 100 ms for several seconds can exhaust all retries. Expose catch-up state or provide bounded retry/backoff/snapshot behavior. See [ak820battery.py:231](/Users/jdlien/code/ak820-pro/hostagent/ak820battery.py:231).

**Must-fix — replay needs sleep semantics, not just a supply fallback.** The CSV parser unconditionally indexes `vdd_avg`; absent-column handling must be fixed. More importantly, `rp_play()` advances all elapsed seconds through normal 10 Hz awake ticks and distributes reports through that entire interval. Merely deriving supply from flags would still charge awake draw and mature the average during sleep. See [sim.c:861](/Users/jdlien/code/ak820-pro/scripts/battery_sim/sim.c:861) and [sim.c:969](/Users/jdlien/code/ak820-pro/scripts/battery_sim/sim.c:969).

Replay must parse slept seconds, invoke the production sleep-accounting behavior, advance awake time only for the awake portion, and place reports only in that portion. Mixed-period ordering is not recoverable from this aggregate log; declare and test that approximation.

**Should-fix — “rebuilt from any dump” is too strong.** Sleep seconds and energy support an interval awake-power estimate:

`awake_power = (entry_energy − sleep_power × sleep_duration) / awake_duration`

Skip wholly sleeping entries. But 15-minute aggregates cannot exactly reconstruct a minute-updated 12-hour average, its partial-minute state, or its initial state when earlier history has been overwritten. The new per-colour model also cannot be recovered from one aggregate `led_pm` alone. Either describe approximate reconstruction with tolerances, or add the necessary checkpoints/resolution. See [plan:259](/Users/jdlien/code/ak820-pro/plans/DEEP-SLEEP-BUILD-PLAN.md:259).

Replacing `vdd_avg` with a versioned `u16` slept-seconds field is otherwise a sensible space trade. Retaining v1–v6 decoding is correct.

**7. The backstop is worth having, but the current types cannot implement it.**

**Must-fix:** `ds_cap_s`, `ds_secs`, the arm argument, the local `secs`, and the existing record are 16-bit. See [sleep.c:140](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/sleep.c:140), 157, 184 and [sleep.c:310](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/sleep.c:310).

Assigning 86,400 to the present cap yields 20,864. Widening only the cap while retaining the local counter prevents the counter from reaching it. Widen the whole path, including last counted/PCF seconds and status serialization. Version any changed dev record rather than silently shifting the old decoder’s layout.

**Note:** this is approximately **27.5 real hours** at the hour run’s rate, and longer at the other measured rate. It bounds a missed GPIO wake only while RTC wakes continue. It is not an independent failsafe against RTC failure: the watchdog does not count while asleep.

The backstop is useful for supply discovery and housekeeping. Require two autonomous backstop/re-sleep cycles without user input in validation; a short forced cap alone misses both the width defect and recovery-state bugs.

**8. Display, HC_POWER, input latency and validation.**

**Display fit is fine, with one correction.** The longest proposed battery strings are seven cells—`100% 9h` or `99% <1h`—not six. They fit the existing seven-cell slot and eight-byte buffers. Define precedence with `BATTERY_SHOW_TENTHS`; appending hours to that representation does not fit. `<1h use left` occupies all twelve overlay cells. See [display.c:1667](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:1667).

Preserve `BATT_TXT_CHUNK == 3`, separate clearing, and one component per call. Existing code documents an actual 27 ms pass caused by combining components. See [display.c:1658](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:1658) and 1711.

**Should-fix:** apply the same maturity/validity/no-pack/cut/USB conditions to the wake message as to the battery row. Queue or deliberately suppress it behind higher-priority alerts; do not let it expire unseen.

**Should-fix:** “calendar set before backlight” does not ensure the panel already contains the corrected clock. Clock painting is queued and pumped incrementally. If showing no stale clock is a requirement, hold backlight release until that clock repaint completes, without synchronously draining the queue. See [ak820pro.c:569](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/ak820pro.c:569), 578, 602 and [display.c:2941](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:2941).

**HC_POWER page 1 can fit.** Four 32-bit counters, three 16-bit diagnostics and four one-byte identification/status fields consume 26 bytes. That accommodates sleep count, total slept seconds, last PCF/count seconds, acquisition step, PLL loops and strays within the 29-byte payload.

**Must-fix protocol details:**

- Capture request `data[3]` before the existing `memset`.
- Preserve page 0 byte-for-byte.
- Give page 1 an identifiable reply version/page discriminator, so an older firmware’s page-0 reply cannot be misparsed as sleep data.
- Include validity/pending/fallback status for PCF duration, acquisition and USB observation; zero is otherwise ambiguous.
- Snapshot fields consistently and retain payload-size assertions.

See [hid_protocol.c:765](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/hid_protocol.c:765).

**Must-fix — budget the entire wake pass.** “Each display operation is under 10 ms” does not prove a pass under 25 ms. The current acquisition edge performs multiple transactions; PCF operations can time out; the same pass can also repaint, run 10 Hz housekeeping and perform persistence. Schedule recovery work with a per-pass budget and test failure paths, including pre-sleep PCF-read failure. Do not hide restoration/correction cost by resetting stall statistics afterward.

**Should-fix — prioritize the waking input over the new battery poll.** A poll queued immediately after wake can precede the first debounced key report and impose ACK/retry delay. Give input its first scan/report opportunity before inserting housekeeping traffic, and measure short bursts rather than only one waking key.

Do not reconstruct a batch of encoder events into QMK’s queue and drain it synchronously: the encoder handler processes the entire queue and waits for press/release spacing on every event. See [encoder.c:30](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/quantum/encoder.c:30).

The validation plan is a good start, but these changes are needed to prevent false passes:

| Gap | Required change |
|---|---|
| `deep_cycles.py` starts `asleep_s` when three samples have confirmed sleep, not at the current-drop edge. | Use logger edge timestamps/brackets and their uncertainty. The current value cannot support a ±1 s comparison. [deep_cycles.py:50](/Users/jdlien/code/ak820-pro/scripts/deep_cycles.py:50), 124, 131. |
| Deep-cycle success accepts `key_gain >= 1`; duplicate presses pass. `awake_seen`, queue replacement/full and UART errors are recorded but not all gated; deep-path stalls are not checked. | Require exactly the expected ordered press/release sequence at the host, fresh sleep sequence numbers, reset continuity, no new ≥25 ms stalls, and explicit transport-counter criteria. [deep_cycles.py:134](/Users/jdlien/code/ak820-pro/scripts/deep_cycles.py:134), 146, 152. |
| Old forced arm/dark-stage testing can pass while automatic standby is broken. | Use the new shortened **ladder** threshold, plus real-threshold/default-mode tests. Resolve whether low-battery Saver override can put Full to sleep: current `power_task()` applies that override, while the plan says Full never sleeps. |
| Daemon sync can conceal failed wake correction. | Measure before the first host SET; test with the daemon withheld. Then inject host sync during each recovery/acquisition stage. Check that the awake period and persistence candidate remain uncontaminated. |
| Short cap tests miss long-counter and ST-counter failures. | Cross 65,535 counted seconds, test the maximum path, and run autonomous repeated backstop wakes. |
| One P2 key does not cover new wake behavior. | Test P0/P2 keys, short taps and chords, presses during arming, both encoder resting states/directions, no-detent wiggles, and slider changes while asleep. |
| CHRG wake alone does not establish USB recovery. | Test already-low CHRG, no-pack pulses, full-pack insertion with no edge, suspended host, absent host, and host frames arriving around both reconnect deadlines. Verify HID traffic, not SOF alone. |
| Log tests can pass with only aligned sleeps. | Include nonaligned entry/exit, repeated partial sleeps, concurrent typing/charging/dumps during catch-up, ring overwrite, PCF fallback and version round-trips. |
| “One hour gives four sleeping entries” assumes alignment. | With arbitrary phase there are edge partial periods. Grade total sleep/energy across boundaries. Separate post-wake awake energy from the expected 13.35 mWh sleep term. |
| Symbol checks alone miss a wrong reachable path. | Verify production default sleep and HC_DEVHOOK rejection on daily/fpb, plus structural guard checks across all flavours. |

Finally, keep the evidence’s scope precise: the 247-cycle run was **board-only**, on P2.8, with 7–30-second sleeps. It established registration at the board and no radio give-ups, not exact host delivery, new wake pins, or overnight recovery. The awake control correctly weakens the claim that radio retries are caused specifically by sleep; it does not explain the first-key latency. See [soak README:3](/Users/jdlien/code/ak820-pro/history/deep-soak-20261019/README.md:3) and [its comparison:24](/Users/jdlien/code/ak820-pro/history/deep-soak-20261019/README.md:24).