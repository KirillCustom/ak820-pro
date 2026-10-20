**No—I would not approve `60a7b43` for the integrated supervised soak on gremlin yet.** Most fixes are sound, but shutdown remains incomplete, and restoration still has a validity-check gap.

This was a static verification against the actual Git objects; the reviewed working files match `60a7b43`. Process launch was denied, so I could not run Cargo or hardware tests. The macOS writer was excluded. One history correction: `017f59d` is the **parent of `7bcc0ea`** here; `Policy::retry` was already present in the review baseline.

**Remaining findings:**

1. **Must-fix — shutdown still has no usable normal path, and pending restoration is skipped.**  
   [agent.rs:299](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:299), [agent.rs:546](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:546), [sender.rs:364](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:364).

   `StopOnDrop` is valid RAII, but the running loop only returns for `--once`, which never starts lockchan. Task termination bypasses it; release builds also use `panic = "abort"`. Thus the guard does not supply the missing cooperative shutdown mechanism.

   Separately, stopping between frames with `restore_pending` immediately returns `Exit`. `release()` preserves the fields temporarily, but the worker then drops `Core`, releasing them without attempting repair. Stopping inside `restore_only()` similarly exits without switching to stop-tolerant recovery. `sleep_through()` fixes **in-flight frame cancellation**, not these paths.

2. **Must-fix — repair writes still sample validity before their spacing wait.**  
   [frame.rs:184](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/frame.rs:184), [frame.rs:145](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/frame.rs:145).

   `restore()` reads `user()`, then calls `write(u, None, None)`. On subsequent repair attempts, that call can wait 16 ms without rechecking user state or session validity. A lock/UAC transition during that wait still permits the stale repair write.

   Restoration should ignore **payload ownership and content cancellation**, but must still obtain valid, fresh lock state after spacing, immediately before submission.

3. **Should-fix — the epoch change regresses in-flight freshness expiry.**  
   [sender.rs:217](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:217), [sender.rs:495](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:495).

   The old control recomputed `current(..., time.now())` at write boundaries. The new control checks only `Slot::epoch`; time passing does not increment it. A title selected at observation age 29 seconds can therefore continue past 30 seconds and commit before CLEAR is scheduled. Conversely, a fresh observation of identical bytes during an already-selected stale CLEAR does not cancel that CLEAR.

   Keep the real epoch, but also validate the effective content’s freshness during the frame. Between frames, `<= STALE_AFTER` also postpones expiry by another tick when awakened exactly at the threshold.

4. **Should-fix — completion validates ownership but not the picked content epoch.**  
   [sender.rs:512](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:512).

   `still_owner` checks only ownership. A→B→A during the final blocking write is invisible to `Policy::set` on the next step, so the old completion consumes the send/repeat bookkeeping despite a newer slot epoch. An already-submitted trailer committing remains accepted; crediting its completion against superseded bookkeeping is a separate issue. Validate both captured epochs.

5. **Should-fix — restoration is not completely independent of payload scheduling.**  
   [sender.rs:334](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:334), [sender.rs:377](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:377).

   Once pending repair needs reopening and an open fails, `ensure_open()` schedules through `policy.failed()`/`policy.due()`. Repairs can again inherit growing backoff and the payload handover deadline instead of the promised one-second recovery schedule. Give repair reopening its own deadline. The deliberate ten-second relist while the keyboard is absent is a separate case.

For the original thirteen findings, “would fail before” below means the **behavioral assertion detects the old defect**, not merely that a new test API would fail to compile against old code.

| # | Fix verdict | Do the tests detect the old defect? |
|---|---|---|
| **1 — independent restoration** | **Mostly fixed**, with reopening caveat above. Pending repair precedes ownership and publication checks. | **Yes** for revoked/unknown→known recovery: [sender.rs:879](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:879). No final-CLEAR exhaustion or failed-reopen regression. |
| **2 — retired failure timestamp** | **Fixed.** Failed retired writes advance `last_completion`. | **Yes:** [frame.rs:534](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/frame.rs:534) asserts the extra 600 ms after the one-second failure. The similarly named sender test does **not** assert recovery timing. |
| **3 — checks after spacing** | **Fixed for frame writes; incomplete for repairs**, as above. | **No:** [frame.rs:549](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/frame.rs:549) permits the old extra write. It includes writes through `changed_at + 17 ms` in its “before” set. Tracing its fake clock gives the old late write at 1191 ms after the change at 1183 ms, and the assertion still passes. |
| **4 — revoke before transport push** | **Fixed on inspection:** radio callback precedes media requests; `open_noting()` revokes before clock/health transactions. | **No new tests** for either hook. Existing agent/radio tests were unchanged. |
| **5 — uncertain radio completion timestamp** | **Fixed on inspection:** timestamp recorded after each text/playback `push`, independently of its result or transport label. | **No new regression test.** |
| **6 — poisoned opening QUERY** | **Fixed.** Explicit stuck result reaches sticky `WinPlat::poisoned`, then permanent sender poison. | **Yes** for stopping after poisoned open: [sender.rs:922](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:922). But `guard.is_none()` cannot distinguish forgetting from dropping; use a drop-counted guard to prove retention. |
| **7 — diagnostic owner lock** | **Fixed on inspection** for Windows CLI and Python writer. | **No new cross-language contention test.** Existing lock test exercises Rust’s raw81 lock. |
| **8 — graceful shutdown** | **Not fixed overall.** In-flight stop waits improved; reachable shutdown and pending-repair stop remain missing. | **No.** Existing stop tests use an empty core or a fake clock whose waits always succeed. |
| **9 — freshness/wake/unlock scheduling** | **Partial.** Between-frame polling and observed unlock resend improve; freshness regression above remains. Wake sampling still occurs only between steps/frames, and explicit resume notification remains absent. | **Yes** for independent stale CLEAR and unknown-lock polling. **No** for the wake test’s scheduling claim: it explicitly calls `step()` after changing input, which also lets the old implementation notice the wake. |
| **10 — epochs and selection** | **Partial.** Real slot epochs and selection after quiet are correct; completion/freshness checks remain incomplete. | **No** for the central A→B→A regression: [sender.rs:843](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:843) merely asserts that some Alpha frame completed. The old uninterrupted Alpha satisfies that. Ordinary mid-frame supersession/revocation also already worked. No after-quiet selection regression. |
| **11 — handover publication memory/repeat** | **Fixed on inspection:** `Via::Nothing` on grant and `Policy::acquired()` resetting the repeat. | **No new Bluetooth-handover or reacquisition-repeat test.** |
| **12 — final whole-frame deadline** | **Fixed:** completion beyond ten seconds no longer returns `Sent`. [frame.rs:303](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/frame.rs:303) | **No new final-write deadline-crossing test.** |
| **13 — status and CLI outcomes** | **Fixed** for the reported waiting/poison state, immediate counters, and CLI failure outcomes. | **Yes** for the added waiting-state assertion and poison status. **No CLI exit-code regression test.** |

The other specific concerns are **notes / satisfactory on inspection**:

- **Restore while revoked:** **yes, it can write while USB or radio owns media, and that is acceptable for restoration.** [sender.rs:377](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:377) sends only restoration through `restore_only`, retains the lockchan guard, and waits recovery silence. It does not send warm-up or payload. This is the intended cleanup exception; it does not exempt fresh validity checks.
- **`Picking` and its cells:** [sender.rs:472](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:472) is thread-safe as used. Both cells stay on the sender thread; selection releases the slot mutex before I/O. Ignoring content epochs before selection is appropriate. The omissions are freshness and completion validation, not `Cell` safety.
- **Non-`Send` `Radio::before_media`:** [radio.rs:123](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/radio.rs:123) is fine for the current synchronous main-thread owner. No unsafe cross-thread movement is introduced.
- **`LOCKCHAN` OnceLock:** [agent.rs:568](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:568) correctly enables immediate revoke. It assumes one `agent::run` per process: a second invocation would retain the first handle because `set` failure is ignored. That assumption holds for the current executable.
- **`WinPlat` poison and CLI `mem::forget`:** [lock_lights.rs:363](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/windows/lock_lights.rs:363), [cli.rs:1157](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/windows/cli.rs:1157), [cli.rs:1174](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/windows/cli.rs:1174). The sticky poison flag and deliberate resource retention are correct. Opening poison retains the guard; SET poison retains lights and guard. Ordinary unresolved restoration now reports failure.
- **Python versus Rust lock:** [ledchannel_test.py:73](C:/Users/jdlien/code/ak820-pro/scripts/ledchannel_test.py:73) seeks to offset zero and locks one byte of the same `%ProgramData%\ak820pro\lockchan.lock`; [radio_lock.rs:81](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/windows/radio_lock.rs:81) uses zeroed offsets and length one. **Same byte, conflicting locks**, including on an empty file. This matches the documented [CRT locking semantics](https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/locking?view=msvc-170) and [Win32 byte-range locking](https://learn.microsoft.com/en-us/windows/win32/fileio/locking-and-unlocking-byte-ranges-in-files).
- **`Policy::retry`:** three immediate retries, fourth loss entering backoff, and success/content-change reset are sound. Its existing [policy.rs:276](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/policy.rs:276) test meaningfully distinguishes retry from unconditional backoff.

I confirmed **39 lockchan unit tests and 708 parity cases**, with every length 0–48, every type, all eight baselines, and both trailer branches per profile. That coverage expansion is real; the unchanged baseline encoder would also pass it. The plan’s assertion that the tests “bite on each” of the thirteen fixes is not supported.