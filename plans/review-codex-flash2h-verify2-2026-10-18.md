**The host fixes are correct. Link-change coverage still has one must-fix gap.**

1. **Must-fix — reset at transmission, too.** The new reset runs at **enqueue**, but both [first transmission and retransmission](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:688) bypass it.

   A concrete possible sequence: a deferred select resets reception while two unacknowledged frames precede it in TX. During their roughly 180 ms timeout budget, old-link LED traffic supplies a baseline and reacquires a preamble. The queued A6 then transmits with that candidate still active. Retransmissions likewise never reset. A subsequent down notification would rescue this, but coverage must not depend on receiving one.

   **Keep the enqueue reset and add the same A6 classification immediately before both TX-pump writes.** Add a driver-level backlog/retransmission regression; the decoder-only suite cannot catch this.

2. **Should-fix — promotion still leaves `PA_SELECT` pending.** [Promotion](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:1219) declares the link connected without retiring its select retry. The timer or `5B 23` can therefore issue another select afterward. Its enqueue now resets reception correctly, but unnecessary reselection remains a separate, pre-existing control-state issue. Clear `PA_SELECT` on promotion; preserve `PA_BOUNCE`. I would not infer harmless hardware reselection from the decoder tests.

3. **Note — reset itself is safe.** [It](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/lockchan.c:228) clears the candidate and baseline, releases busy immediately, and leaves keyboard reports, routing and TX state untouched. Repeated resets cannot strand the decoder or directly discard keystrokes. Direct/deferred selects, pairing, cable cancellation and parsed down states are covered at their existing call sites.

4. **Note — A6 classification checks out for normal callers.** They send selects `30–33`, pairing `51`, or battery poll `53`; I found no other normal link-changing opcode. One qualification: the `WDT_TEST_HOOKS` [HC_DRIVE handler](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/hid_protocol.c:790) accepts an arbitrary profile byte, so the statement is not literally universal across fault-injection builds.

5. **Note — host fixes verified.** [frames_test](/Users/jdlien/code/ak820-pro/scripts/ledchannel_test.py:165) completes without the KeyError, enforces the quiet interval, separates records/verdicts, and exits 1 for either failure. My in-memory checks exercised all four decode/budget combinations. The [boundary tests](/Users/jdlien/code/ak820-pro/scripts/lockchan_test.py:365) now genuinely cross wrap; lifetime polling leaves a 130 ms gap, correctly isolating lifetime from gap expiry. Quiet acquisition is naturally feed-only.

No files changed. Source inspection and in-memory checks only; no build or flash.