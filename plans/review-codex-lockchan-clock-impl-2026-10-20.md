**I found two must-fixes: one firmware ownership issue and one daemon invalidation issue.** The firmware is reasonable for a controlled, attended test on gremlin’s board, but I would not install this daemon revision for persistent use with `--clock --lockchan` yet.

This review targets **d70a9097 / 13a2338**, including their original line numbers. The checkout changed during review; later changes are not assessed here. Process launch was denied, so validation was source review, Git-object verification, inspection of the existing ELF, and small JavaScript state models—not a fresh build or test run.

1. **Must-fix — Wake recovery can prematurely expire a newly stamped raw-HID lease.**

   `rawhid_clock_set()` stamps `rtc_get_seconds()`, but that counter is subsequently adjusted by `wake_coarse()` for the **preceding sleep**. That adjustment happens even when a newly accepted USB correction has changed `host_sync_gen`.

   For example: sleep counted 7,000 seconds; PCF reports 8,000. USB sets the clock and stamps ownership before coarse recovery. Recovery then adds 1,000 to the counter. Two seconds after the USB set, `hid_rawhid_clock_within()` sees an age of 1,002 seconds and permits wireless takeover.

   This is reachable because `rtc_set_time_ms()` permits an accepted correction during wake recovery. Preserve the lease across that counter adjustment, defer its baseline until recovery settles, or use a sleep-aware timebase that does not retroactively move after stamping. This is a new ownership interaction, not a reopening of the cleared calendar-generation fixes. [hid_protocol.c:116](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/hid_protocol.c:116), [rtc.c:1254](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1254), [rtc.c:1913](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1913)

2. **Must-fix — Original finding 7 remains open for changes during transmission.**

   `observe()` runs between frames. Once the builder has captured the timestamp, `ClockCtl` checks ownership and media epochs only. A wall-clock step or manual time-zone change during the remaining writes therefore does **not** invalidate the clock frame.

   The `SPAN` check correctly avoids a predictable DST transition under the zone rules sampled during construction. It cannot predict a subsequent manual zone change or wall step. The next pass schedules a correction, but the stale frame can already have changed and queued persistence of the board’s time.

   Add clock-continuity checks to the in-flight control, using the captured wall/monotonic relationship and offset, and invalidate successful credit when continuity changed. [sender.rs:617](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:617), [sender.rs:935](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:935), [sender.rs:962](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:962)

3. **Should-fix — Wake and discontinuity scheduling bypass the USB handover floor.**

   `acquired()` incorporates the USB owner deadline, but retains it only in `due`. `woke()` can lower that deadline, and `observe()` can replace it with `now`.

   A USB sync at second 0 followed by acquisition gives a deadline of 615. A wake at second 40 changes it to 44. The board refuses that transmission, while the daemon credits it and schedules the next one roughly ten minutes later. Repeated wakes can keep postponing the first useful correction.

   Keep ownership/handover eligibility separately from the requested send time, and enforce it for every scheduling reason. [clock.rs:85](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/clock.rs:85), [clock.rs:104](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/clock.rs:104), [clock.rs:117](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/clock.rs:117)

4. **Should-fix — Media preemption misses changes during clock warm-up.**

   The worker selects `Kind::Clock` before recovery/warm-up/quiet, but captures media epochs only afterward. A title change during that interval becomes the clock frame’s new baseline, so the clock proceeds ahead of the newly due title.

   Capture the epochs when choosing clock work, or re-evaluate media priority before starting its preamble. Changes after the current capture are correctly detected, including A→B→A. [sender.rs:692](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:692), [sender.rs:928](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:928), [sender.rs:951](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:951)

5. **Should-fix — Windows wall/monotonic sampling has an unbounded sampling gap.**

   Subtracting elapsed monotonic time correctly handles delay **before** the builder samples the clocks. However, `SystemTime::now()` precedes `Instant::now()`. Preemption between those calls is subtracted from a wall sample that predates it.

   A 100 ms interruption there produces a timestamp 100 ms early and can still satisfy the 300 ms header deadline. Bracket the wall sample with monotonic reads and reject an excessively wide bracket. [lock_lights.rs:393](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/windows/lock_lights.rs:393)

6. **Should-fix — Some builder failures retry continuously instead of backing off.**

   Failure of `local()` or `clock_payload()` follows the executor’s proper abandon/restore path, but the sender leaves the clock immediately due whenever a later `wall_ms_at(done)` succeeds. An out-of-range host year therefore repeatedly sends warm-ups and preambles.

   Record the builder’s failure reason explicitly; apply the intended backoff to conversion/payload failures while retaining immediate scheduling for media preemption. [sender.rs:964](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:964), [sender.rs:1011](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:1011)

7. **Should-fix — Integration coverage is substantially weaker than the mutation results suggest.**

   The C test exercises parsing and civil arithmetic; it never executes `lockchan_clock_try/commit/poll/drop`, the raw-HID stamp, or wake integration. Removing an ownership recheck or wake-drop call would escape this test. The Rust tests cover useful ordinary behavior, but miss the interleavings above. [lockchan_media_test.c:225](C:/Users/jdlien/code/ak820-pro/scripts/lockchan_media_test.c:225), [sender.rs:2038](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:2038)

   Add tests for fixed retry expiry, recomputation from the original anchor, USB takeover, sleep-counter adjustment after stamping, wake during handover, clock changes after payload construction, and media changes during warm-up. Include negative latency and builder conversion failure.

   The reported surviving mutants are plausibly redundant under the daemon’s current fixed-at-startup clock setting. That is narrower than equivalence across all public `Handle` call sequences—for example, toggling `set_clock(false)` then `true` without reacquisition. I could not rerun the mutants.

The remaining requested checks came out as follows:

| Original finding | Assessment |
|---|---|
| **1, 2, 4, 5, 8** | Remain cleared; no regression identified in the reviewed integration. |
| **3 — retry ownership** | The retry mechanics are correct: owned copy, unchanged commit deadline, original-anchor recomputation, ownership checked on every attempt, and cancellation on link changes, USB mode, accepted raw-HID sets and actual deep-sleep wake. The new lease-timebase issue above remains. |
| **6 — signed latency** | Correct: signed `i32`, added before local conversion; unmeasured backends remain disabled. |
| **7 — discontinuities** | Between-frame detection and predictable DST avoidance work; in-flight invalidation remains missing. |
| **9 — raw-HID stamp** | Correct placement outside the shared setter; `RETRY`/`REJECT` excluded; legacy stamping is conservatively correct even on persistence failure. The daemon records every completed USB attempt, including failures. Wake-counter and handover issues remain above. |
| **10 — late construction** | `run_late` preserves the anchor and original deadlines, including header completion, and restores on builder failure. Windows sampling needs the refinement above. |
| **11 — precedence** | Text/transport priority and post-pick preemption work. Warm-up preemption remains open. Five minutes is effectively a wake throttle; acquisition, discontinuity and retry rules have exceptions. |
| **12 — calibration** | Still open: +38 ms is explicitly provisional. Measure the production overlapped executor and retain reproducible raw calibration data. |
| **13 — diagnostics** | `off` is correctly documented as **target minus board** in the implementation. The design document still contains the opposite sign and the inaccurate single-transaction PCF-cost description. |
| **14 — arithmetic** | Sound. The bounded day/millisecond decomposition safely fits signed 32-bit arithmetic; it does not require a full 64-bit epoch. Unsigned timer subtraction and post-carry year validation are correct. |
| **15 — scope** | Unchanged: `usb_mode` is active-host mode, and raw81 still excludes RTC commands. |

**Note — UART context and board state transitions look sound.** The parser runs in thread/main-loop context, and `rtc_set_time_ms()` queues PCF work rather than performing synchronous I²C. Pairing and slot selection clear connectivity and drop pending work; USB mode is rechecked at every attempt. I found no concurrent `usb_mode` mutation inside the setter path. Actual deep sleep calls `ch582_sleep_wake()` before subsequent radio work, dropping both decoder state and pending clock work. [ch582f_ajazz.c:487](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:487), [ch582f_ajazz.c:1414](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:1414)

**Note — Retry cost is bounded, but needs bench measurement.** An idle poll returns immediately. A pending attempt performs civil conversion—about twenty software division calls in the inspected ELF—and the bounded RTC snapshot, without I²C or a blocking retry loop. I cannot establish the project’s loop-latency budget from source inspection alone. [ch582f_ajazz.c:529](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:529)

**Note — The 8-byte heap is credible for this linked image.** The inspected ELF has heap bounds `0x20007fe8..0x20007ff0`, plus separately reserved 2 KiB process and 1 KiB interrupt stacks. Its environment points to an empty `initial_env`; no environment setter is linked. Newlib’s allocation paths come through timezone parsing/scanning, which the unset-`TZ` path avoids. The linked `printf` implementation is heap-free, and I found no active application allocation path. This supports “heap unused” for this image, not for arbitrary future additions; reclaiming static headroom would still be prudent.

**Note — Status does not prove board acceptance.** Owner-generation checks prevent ordinary revoked-work credit, and slot access is mutex-protected. `clock_frames` and `clock_last` correctly describe transmissions, not acknowledgements. I found no additional status race that independently applies a stale clock; the missing clock-continuity guard is the substantive race.

For gremlin: **yes to a restricted attended firmware test**, with loop timing, retry/drop counters, sleep and USB takeover explicitly exercised. **No to persistent `--clock --lockchan` installation at 13a2338** until the must-fixes are addressed; foreground bench use can validate the known failures. No files were changed, firmware flashed, or daemon installed.