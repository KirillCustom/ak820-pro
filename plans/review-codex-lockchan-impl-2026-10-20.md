**No—I would not run the integrated daemon on gremlin for the supervised soak as-is.** The main blockers are unresolved restoration, cancellation boundaries, and transport ownership. The encoder and ordinary successful-frame path look sound.

Reviewed through `7bcc0ea7`, against the original design and R2/R3/R4. This was a static review: process launch was denied, so I could not run Cargo tests. No files changed or hardware writes performed.

1. **Must-fix — `restore_pending` is retained but is not independently serviced.**  
   [sender.rs:265](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:265), [sender.rs:277](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:277), [frame.rs:278](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/frame.rs:278).

   When revoked with a restoration outstanding, `step()` keeps the handle and guard but returns `Wait` forever without trying restoration—even after lock validity returns. While granted, restoration is gated by `policy.due()`: it can wait 60 seconds, follow exponential backoff, or never run when CLEAR has exhausted its scheduled sends.

   The last case is concrete: the final CLEAR frame completes, its post-trailer repairs exhaust three attempts, `frame()` still returns `Sent`, and `Policy::sent()` sets the next send to `None`. The indicators can remain wrong indefinitely.

   R3-1/R4 require a separate recovery state: retry restoration every second, including while payload ownership is revoked; prohibit payloads until confirmed; release ownership after recovery.

2. **Must-fix — a failed, retired write does not update the recovery clock.**  
   [frame.rs:143](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/frame.rs:143), [frame.rs:159](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/frame.rs:159).

   `last_completion` advances only on success. If a write consumes the one-second timeout and retires through cancellation, recovery still measures from the preceding successful write. That timestamp is already over 600 ms old, so restoration can follow immediately after the failed operation retires.

   This defeats the intended silence before repair. A possibly delivered transition near cancellation can be followed by a repair that finishes an abandoned trailer. Conservatively record the end of every retired write attempt before recovery; never repair after `Stuck`.

3. **Must-fix — cancellation and lock validity are checked before the spacing wait, not immediately before submission.**  
   [frame.rs:135](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/frame.rs:135), [frame.rs:265](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/frame.rs:265), [sender.rs:415](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:415).

   `checked()` runs, then `write()` waits, then submits without checking again. `Wall::sleep_until()` responds to stop, but content/ownership notifications do not terminate its wait.

   Consequently, B can supersede A—or USB can revoke ownership—during the final trailer’s spacing wait, and A’s final write is still issued. That is outside the accepted race where supersession happens **after** submission. A session becoming invalid during that wait likewise permits another write.

   Recheck ownership, content, validity, and deadlines after waiting, immediately before submission. Check completion against current generations too.

4. **Must-fix — radio ownership is revoked after its first push; other USB evidence is also deferred.**  
   [agent.rs:367](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:367), [radio.rs:265](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/radio.rs:265), [agent.rs:395](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:395), [agent.rs:474](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:474).

   `Radio::tick()` can qualify and send text/playback before returning. Bluetooth is revoked only afterward, when the returned transport is examined. A failed radio push can leave transport `"none"`, so Bluetooth may never be revoked.

   Similarly, `open_noting()` merely sets `USB_SEEN`; the sender remains granted while the clock/health interaction runs, until the loop consumes the flag.

   USB’s media closure correctly revokes before `cycle()`. Apply that ordering at radio qualification and at the USB-evidence observation point. The firmware lease is useful protection, but does not implement R2-5’s host ownership rule.

5. **Must-fix — `raw_media_last` misses possibly delivered radio requests.**  
   [agent.rs:383](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:383), [agent.rs:612](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:612), [radio.rs:272](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/radio.rs:272).

   The timestamp is updated from the **resulting transport label**. A partial radio text batch, unanswered request, or playback failure can change the radio to silent/unresponsive, producing `"none"` despite possible delivery. Bluetooth then uses an older timestamp—or remains granted with its existing deadline.

   This can discard the initial Bluetooth frame and repeat against a freshly renewed firmware lease, leaving correction to the minute refresh or board expiry.

   Record media-attempt completion inside the radio request path, including uncertain outcomes, and propagate that timestamp independently of qualification. USB’s end-of-cycle timestamp is conservative and covers its partial/failed batches.

6. **Must-fix — poisoning during the opening QUERY is flattened into an ordinary retryable error.**  
   [lock_lights.rs:188](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/windows/lock_lights.rs:188), [sender.rs:313](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:313).

   A stuck `QUERY_INDICATORS` correctly leaks its pending allocation, handle, and event. But `open()` converts `WriteError::Stuck` to `String`; `Core` consequently retries opening. Later revocation can release the owner guard despite that outstanding operation.

   The established-handle SET poison path correctly forgets both lights and guard. Opening needs an equally explicit poisoned result, permanently disabling the channel and retaining ownership. Otherwise repeated queries can accumulate abandoned resources.

7. **Must-fix — diagnostic writers bypass the machine-wide owner lock.**  
   [cli.rs:1147](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/windows/cli.rs:1147), [ledchannel_test.py:109](C:/Users/jdlien/code/ak820-pro/scripts/ledchannel_test.py:109).

   Windows `ak820 lockchan send/clear` opens and writes directly without taking `lockchan.lock`. The Python harness also does not take the promised lock. Either can interleave reports with the daemon, including while the daemon deliberately retains ownership after poisoning.

   Acquire the same guard before opening and retain it through restoration and poison handling. This matters particularly for a supervised soak that uses diagnostic tools alongside the daemon.

8. **Must-fix for approved-design compliance — there is no graceful daemon shutdown path, and the sender’s stop restoration does not work with its real clock.**  
   [agent.rs:324](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:324), [sender.rs:429](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:429), [frame.rs:218](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/frame.rs:218), [task.rs:267](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/windows/task.rs:267).

   The daemon runs until externally terminated. Nothing calls `Handle::stop()` outside tests; the join handle is discarded; no three-second shutdown budget exists. Install/uninstall stop the Scheduled Task.

   Even if stop were wired in, restoration normally calls `write()`, whose spacing wait immediately returns false once `s.stop` is set. Thus the claimed bounded repair is not submitted. Stopping between frames simply releases resources, even with restoration pending.

   Hard termination remains an unavoidable best-effort exception. Making it the only normal stop mechanism is **not** implementation of R3-3/R4, and can leave indicators wrong until another host report. Add a cooperative stop path with a bounded recovery allowance, then detach/exit at the deadline.

9. **Should-fix — freshness expiry and resend events need their own scheduling.**  
   [sender.rs:212](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:212), [sender.rs:277](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:277), [sender.rs:220](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:220).

   Observation age is correctly carried, and an in-flight frame detects expiry. Between frames, however, the sender sleeps until the publication deadline without scheduling `observed + STALE_AFTER`. If the main loop stops feeding notifications, CLEAR can wait until the next minute refresh.

   Wake arming is sampled only at step boundaries: crossing 30 seconds and receiving input during a frame can be missed if it was not already armed. There are also no production resume/unlock calls to `Handle::force()`.

   Schedule freshness expiry explicitly, sample wake state sufficiently often, and supply resume/unlock events. The existing wrapping subtraction and “any changed timestamp” comparison are correct.

10. **Should-fix — the generation implementation is weaker than the approved contract.**  
    [sender.rs:90](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:90), [sender.rs:322](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:322), [sender.rs:339](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:339).

    **The `policy_gen() != self.gen` comparison itself is correct**, and `Ctl::abort()` avoids recursively holding the mutex.

    But `policy_gen` synthesizes a generation from current byte equality; `Handle::set()` has no generation counter. A→B→A can disappear entirely. Content is captured before warm-up; the later `pick` closure returns that captured content rather than selecting the newest snapshot. Finally, `policy.sent(gen, done)` checks a policy generation that could not change while `frame::run()` ran, and checks no owner generation.

    Carry actual content/owner epochs, select after quiet, and validate completion against both. Preserve the accepted limitation that an already-issued final write cannot be recalled.

11. **Should-fix — Bluetooth handover does not invalidate USB’s publication memory.**  
    [agent.rs:399](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:399), [agent.rs:550](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:550), [agent.rs:592](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:592).

    `Via` remains `Usb` across Bluetooth ownership. USB A → Bluetooth B/CLEAR → USB A can therefore skip the required full USB entry refresh because `Publisher` still remembers A. Playback resumes immediately while text can remain wrong until the raw-HID keepalive.

    Mark the intervening transport transition without treating Bluetooth completion as acknowledged publication. Also reset acquisition-specific repeat bookkeeping: `Policy::force()` currently retains the previous acquisition’s `repeat_owed` state.

12. **Should-fix — the total frame deadline is checked only before writes.**  
    [frame.rs:264](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/frame.rs:264).

    The 100/300 ms completion checks correctly use the saved previous completion. The ten-second whole-frame limit does not: the final spacing/write can cross it and still return `Sent`. Check the total deadline after completion as R3-6 requires.

13. **Should-fix — status and CLI outcomes can report misleading success/activity.**  
    [sender.rs:281](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:281), [sender.rs:351](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:351), [cli.rs:1151](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/windows/cli.rs:1151).

    After a successful title frame, state can remain `"sending"` throughout the minute wait. Poison also leaves that state while exiting the worker. Write/restoration counters are published at the next step, which poison never reaches. The Windows CLI returns exit status zero for `Unknown`, `Abandoned`, `Poisoned`, and unresolved restoration.

    Publish terminal state and counters immediately; make CLI success require `Sent` with no pending repair.

The tests **bite at the encoder/frame level, but do not yet bite at the critical integration boundaries**:

- The 224 generated vectors cover all profiles, all eight baselines, and both trailer branches. I independently inspected that coverage. They do not cover every length 0–48 as originally specified; P1 also misses types 10 and 11. See [gen_lockchan_vectors.py:17](C:/Users/jdlien/code/ak820-pro/scripts/gen_lockchan_vectors.py:17).
- Frame tests meaningfully cover completion-based spacing, slow preamble/data writes, unknown locks, repair exhaustion, and user changes after every successful write.
- The fake clock always returns true, masking the real stop/restore failure. Failed fake writes consume no time, masking finding 2. Cancellation is injected at selection, not every write/wait boundary. See [frame.rs:295](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/frame.rs:295), [frame.rs:330](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/frame.rs:330), [frame.rs:425](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/frame.rs:425).
- The seven sender tests never change slot content/ownership during I/O, never produce `Stuck`, and always return `None` for input. The stale-content test checks frame length rather than decoding CLEAR. These need regression cases for the findings above. See [sender.rs:484](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:484).
- Existing agent handover tests exercise USB/radio, not the new Bluetooth handle and timestamp wiring. The earlier firmware ingress/lease integration-test gap also remains.

The remaining design checks are **notes / satisfactory on inspection**:

| Area | Assessment |
|---|---|
| Encoder/content, R2-9 | Full fitted snapshots, printable ASCII, valid icons, empty CLEAR, no playback extension. |
| Windows validity, R4-1 | Active/unlocked WTS session plus matching desktop names is implemented; failures suppress sending. Scheduled-daemon and second-keyboard freshness still require the soak. |
| Real user lock state | No lock-key injection or logical-lock mutation found. The identified risk is incorrect **physical indication**, potentially indefinite. |
| Ordinary I/O lifetime | Heap `Pending`, manual-reset event, cancellation-race success, and SET poison resource retention follow `device.rs`’s approach. Cancellation itself does not retire I/O. [Microsoft contract](https://learn.microsoft.com/en-us/windows/win32/api/ioapiset/nf-ioapiset-cancelioex). |
| Thread safety | No slot mutex held across board I/O; condvar waits release it. No `Ctl::abort` self-deadlock found. A stuck open strands the sender thread rather than holding the main loop’s slot access. |
| Discovery/retry | Strict keyboard-class selection, ambiguity refusal, per-open UnitId, ten-second absent relisting, and capped failure backoff are present. |
| Soft retries | Three immediate retries, fourth loss entering backoff; success/content change resets the counters. Supersession does not increase backoff. |
| Handover/flags | `grant(None, now)` correctly waits twelve seconds. USB opener `Absent` plus transport `"none"` gates grants; USB push failure does not grant. `--once` skips the sender and the five-second interval cap is enforced. |
| Firmware dependencies | Relevant R2-9/R3-2/R4-3 paths validate before mutation, disable playback, count accepted media commands, distinguish never-received history, and invalidate assembly. |
| Accepted limits | No acknowledgement, finite lease protection, latest-state starvation during continuous changes, optimistic-icon delay, and board-relative expiry remain accepted limitations—not new guarantees. |