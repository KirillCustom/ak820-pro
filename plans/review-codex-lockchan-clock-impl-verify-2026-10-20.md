**Firmware `9e52400e`: yes for an attended test on gremlin’s board. Daemon `5b5fd80`: not yet for persistent installation with `--clock --radio --lockchan`.** One must-fix remains in clock invalidation, plus several should-fixes.

I inspected all four requested commits and their parent differences through Git objects. Process launch was denied, so validation was source inspection, existing-ELF inspection, and JavaScript models—not fresh C/Rust builds or test runs.

| Implementation-review finding | Closure |
|---|---|
| **1 — Wake credit prematurely expires raw-HID ownership** | **Closed for the reported failure.** Both counts are stamped, and subsequent coarse adjustments cancel. Elapsed-time limitations remain below. |
| **2 — In-flight clock invalidation** | **Partially closed; must-fix remains.** Post-construction wall steps and offset changes are checked, including at credit, but construction can record an offset different from its payload. |
| **3 — USB handover floor** | **Closed as a scheduling bypass.** `due()` consistently applies the separate floor to wake, discontinuity, `sent`, `again`, and `later`. |
| **4 — Media changes during warm-up** | **Closed for warm-up.** A smaller selection/opening gap remains. |
| **5 — Windows sampling gap** | **Closed on Windows.** The equivalent Mac gap remains. |
| **6 — Builder failure backoff** | **Partially closed.** One newly added fallible conversion occurs after clearing the failure flag. |
| **7 — Integration coverage** | **Partially closed.** The actual pending machine is now exercised; board-hook wiring and several boundary interleavings remain untested. |

1. **Must-fix — The continuity baseline can bless a payload built under the previous time zone.**

   The builder obtains `l`, checks `later`, and encodes the payload. It then independently calls `local(ws0)` to obtain `off0`. If the zone changes between the span check and that last conversion, the payload contains offset A while `taken` records offset B. Every subsequent continuity check sees B and accepts the stale payload, including successful credit.

   I reproduced that call ordering in a model: payload UTC−6, recorded/current offset UTC−7, span check and continuity both passing, payload wrong by one hour. The next outer observation can schedule a correction, but the incorrect frame can already have applied—the same substantive problem as the original must-fix.

   Tie the continuity offset to the civil conversion used for the payload, and reject disagreement with any subsequent conversion. [sender.rs:991](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:991), [sender.rs:1004](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:1004)

2. **Should-fix — The idle gate does not guarantee `WAKE_DELAY` in all orderings.**

   An ordinary **grant while already idle works**: acquisition stores the deadline, the gate suppresses transmission, and a later observed input change defers an overdue frame.

   Two gaps remain:

   - `wake_guess()` and the idle gate sample input separately. Input arriving between them makes the gate open without firing `woke()`, permitting an overdue frame immediately.
   - A wake detected in the same pass as acquisition is processed **before** `acquired()`. Acquisition replaces that scheduling decision; an already-expired handover deadline can permit transmission before the wake delay.

   Use one input snapshot per pass and preserve the wake deadline through acquisition. The added idle test covers neither ordering. [sender.rs:552](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:552), [sender.rs:576](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:576), [sender.rs:611](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:611), [sender.rs:692](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:692)

3. **Should-fix — The final builder conversion escapes backoff.**

   `failed.set(None)` precedes `platform.local(ws0)?`. If that conversion returns `None`, the builder abandons with no failure recorded and remains immediately due. Move the clear after all fallible construction succeeds. The current Windows/Mac wrappers always return `Some` on normal completion, so this is a remaining generic-platform failure path rather than an ordinary out-of-range-year loop. [sender.rs:1002](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:1002)

4. **Should-fix — Media epochs still are not captured at selection.**

   Warm-up preemption is fixed, but `Kind::Clock` is selected before locking/opening the device. A media change during that preparation becomes the new epoch baseline inside `send_clock()`, allowing the clock to proceed ahead of the newly due media. Capture the selection’s epochs or re-evaluate priority after opening. [sender.rs:697](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:697), [sender.rs:726](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:726), [sender.rs:935](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:935)

5. **Should-fix — The Mac needs the wall-sample bracket before enabling clock transmission.**

   Preemption between `SystemTime::now()` and `Instant::now()` still subtracts time that elapsed **after** the wall sample. A 100 ms interruption produces a roughly 100 ms early timestamp and can remain below the 500 ms continuity threshold.

   Apply the Windows bracket/rejection scheme to `wall_ms_at()`. `local(unix_s)` does **not** need a monotonic sampling bracket: it converts a supplied instant. Its offset must, however, agree with the payload’s continuity baseline. Mac clock transmission remains disabled by the unmeasured-latency default. [macos/lock_lights.rs:376](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/macos/lock_lights.rs:376)

6. **Should-fix — The 208-byte RX tap breaks its reader.**

   The firmware advertises 208 bytes correctly, but `devhook.py` still exits unless `size == 260`. The commit changes its documentation, not this validation. Accept the supported sizes or validate the advertised page-aligned size. [ch582f_ajazz.c:370](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:370), [devhook.py:144](C:/Users/jdlien/code/ak820-pro/scripts/devhook.py:144)

The requested timebase and scheduling checks resolve as follows:

- **Note — Credit arithmetic is internally consistent, but age can read short against real time.** Algebraically, the helper measures elapsed **uncorrected counted ticks**: all later coarse credits cancel. A credit made before the stamp is already in both baselines; positive and negative credits after it cancel equally; ordinary unsigned wraps work. For example, `(100,0) → (1102,1000)` gives age 2, and `(100,0) → (72,−30)` also gives 2. Ten thousand randomized valid-state model cases preserved that invariant. [rtc_payload.h:31](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc_payload.h:31)

  A subsequent sleep’s positive correction is also removed, deliberately making age short and extending ownership. Conversely, if that sleep was **overcounted** and receives a negative correction, removing the correction preserves the overcount: “only lengthens” is not universally true. The final signed cast also clamps ages at or above `2³¹` counted seconds to zero—about 68 years, a note rather than a practical test blocker.

- **Should-fix — The daemon’s 615-second floor can still precede the firmware’s extended lease.** Suppose USB sync is followed by 600 real seconds asleep, counted as 523. At real second 615, firmware age is only 538. The daemon’s floor has expired, but the board refuses the frame; `sent()` nevertheless schedules the next attempt ten minutes later. The original floor-bypass bug is fixed, but the revised firmware timebase creates this remaining handover mismatch. [rtc_payload.h:28](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc_payload.h:28), [clock.rs:99](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/clock.rs:99), [sender.rs:1027](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:1027)

- **Note — `rtc_credit_s` is written and read in the same main-loop context.** Raw HID is drained by `raw_hid_task()` in the main loop; coarse recovery runs through housekeeping; radio ownership checks also run there. The RTC ISR changes the seconds count, not the credit accumulator. I found no concurrent credit update between the paired reads, so the non-`volatile` credit variable is appropriate here. [main.c:53](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/quantum/main.c:53), [rtc.c:1922](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1922), [hid_protocol.c:116](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/hid_protocol.c:116)

- **Note — A wall step during warm-up, before the anchor sample, produces a fresh payload.** No old timestamp has been frozen yet. A step after `w0` is sampled is checked against that sample once construction completes. However, the scheduler retains its pre-frame `wall_ref`, so a correctly credited frame following a warm-up step can be followed by an unnecessary immediate resend when `observe()` notices the already-handled step. [sender.rs:988](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:988), [clock.rs:128](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/clock.rs:128)

- **Note — Continuity checking has real per-write cost.** After `taken` is set, every guarded write calls `wall_ms_at()` and, when the wall comparison passes, `local()`. A successful P0 clock frame has **94 such writes plus the final credit check**. Windows performs up to four sampling brackets per check and invokes its Windows time conversions through `SystemHost.local()`. The loop is bounded, but I cannot establish its latency from source. Measure the production executor with these checks included. [sender.rs:946](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:946), [frame.rs:186](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/frame.rs:186), [windows/host.rs:36](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/windows/host.rs:36)

**Finding 7’s remaining coverage gap:** the C test now exercises the production pending machine’s ownership recheck, original-anchor recomputation, fixed expiry, replacement, drop, wrap, and rejection. That is a substantial improvement. It still would not catch deletion of the actual board wake/drop hook. Also, the purported post-credit example passes `now=102, credit=1000`; a real `+1000` adjustment changes both counters, so the regression example should be `now=1102`, expecting age 2. Add the pre-credit case and the daemon interleavings above. [lockchan_media_test.c:328](C:/Users/jdlien/code/ak820-pro/scripts/lockchan_media_test.c:328), [lockchan_media_test.c:408](C:/Users/jdlien/code/ak820-pro/scripts/lockchan_media_test.c:408)

For the older design findings carried into the review: **1, 2, 4, 5, 8, 14, and 15 remain cleared**; **3’s retry machine, 6’s signed latency, and 9’s stamp placement remain sound**, subject to the lease caveats above. **7 and 11 remain partially open** as described; **10’s late construction remains sound**, with the Mac sampling refinement outstanding. **12 remains open:** Windows’ `+38 ms` is still explicitly provisional. **13 is closed:** the plan now gives target-minus-board and describes the multi-transaction PCF writer correctly. [windows/lock_lights.rs:341](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/windows/lock_lights.rs:341), [LOCK-LED-CHANNEL-PLAN.md:1256](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:1256)

The existing ELF confirms a **208-byte ring and 56-byte heap gap** (`0x20007fb8..0x20007ff0`), with the separate stacks retained. I found no new firmware must-fix preventing an attended test; exercise sleep recovery, USB takeover, pending expiry/drop, and loop timing. Persistent daemon installation should wait for the payload/baseline offset race to be fixed and regression-tested. No files were changed, firmware flashed, or daemon installed.