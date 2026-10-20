No confirmed **P1** defect in the macOS writer. I found three **P2** issues and two **P3** issues.

This reviews **1457125**, excluding the working-tree edits made during review. I stayed inside the repo; no build, tests, or hardware commands were run. I reproduced the tick arithmetic separately in memory.

1. **P2 — The existing probe bypasses the owner lock.**  
   The daemon and new CLI cooperate through [radio_lock.rs](/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/macos/radio_lock.rs:31), but [the Swift probe](/Users/jdlien/code/ak820-pro/hostagent/ledprobe/main.swift:147) and its Python harness never acquire it. Running `ledchannel_test.py frames` or the probe beside the future daemon can interleave LED writes, corrupt frames, and overwrite the restored baseline. R2-8 explicitly anticipated the test tool participating.

   **Fix:** make every probe write mode participate in the same lock, including direct Swift launches. Coordinate ownership between the harness and probe without independently acquiring the same exclusive lock twice.

2. **P2 — `Mac::default()` silently disables wake detection permanently.**  
   In [lock_lights.rs, commit lines 267–305](/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/macos/lock_lights.rs), derived `Default` leaves `start = None`; `input()` consequently always returns `None`. Wiring the daemon with `sender::spawn(Mac::default())` compiles but never detects an idle-to-active wake. An unchanged title then waits for another resend reason or refresh.

   **Fix:** implement `Default` using `new()`, preferably making `start: Instant` nonoptional. Test both construction paths.

3. **P2 — CLI failures return exit status zero; poisoning also releases the guard before process exit.**  
   [cli.rs:506](/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/macos/cli.rs:506) prints every `Outcome` and then returns `Ok(())`. A locked-session `Unknown`, disconnected-device failure, missed deadline, or `Poisoned` result therefore looks successful to a script. A pending restore also gets discarded when the command ends.

   On `Poisoned`, ordinary scope exit drops `_owner`, unlike `Core`’s deliberate lifetime retention. The CLI normally exits immediately afterward, but this still opens an avoidable interval contrary to R3-3.

   **Fix:** return an error for unsuccessful outcomes and unresolved restoration; retain the owner guard through process exit on poisoning. Keep “sent” distinct from acknowledged board delivery.

4. **P3 — Saturating idle milliseconds creates false wakes after approximately 49.7 days without input.**  
   In [lock_lights.rs, commit line 302](/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/macos/lock_lights.rs), `now` wraps but `idle` saturates. Once saturated, the estimated last-input tick advances with every poll. For example, `now = 100000`, then `100500`, produces last-input ticks `100001`, then `100501`, with no input. The sender repeatedly arms and fires.

   **Fix:** use compatible modular arithmetic for both values, or retain full-width idle/time state and explicitly adapt it to the platform interface. Test the conversion boundary, not just `last_tick()` with already-truncated inputs.

5. **P3 — A malformed screen-lock value is accepted as unlocked.**  
   [lock_lights.rs:84](/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/macos/lock_lights.rs:84) collapses missing and wrong-type values into `None`; `!= Some(true)` accepts both. If the lock property becomes an unexpected type, `user()` returns a known Caps baseline despite a failed validity check, contrary to R4-1.

   **Fix:** distinguish absent, valid Boolean, and malformed values; reject malformed values. **Do not simply require `Some(false)`**: [keydrop_test.py:43](/Users/jdlien/code/ak820-pro/scripts/keydrop_test.py:43) records that the console-registry lock key is normally absent when unlocked. That observation supports the convention, but does not independently establish the contract of `CGSessionCopyCurrentDictionary`.

The remaining checks came out as follows:

| Area | Assessment |
|---|---|
| **CF/IO ownership** | Matching dictionaries are transferred to the matching APIs; adding a `Cf` owner around them would double-release. Returned services and iterators use `Io`. Copied session dictionaries, created keys and registry properties use `Cf`; dictionary values remain borrowed. |
| **Device/connection cleanup** | Device-open failure releases the created device. HID-system-open failure closes/releases the device. Normal `Drop` unschedules, closes/releases the device, and closes the HID-system connection. `mach_task_self_` and the current run loop are borrowed, not owned resources. |
| **HID-system FFI** | `IOServiceOpen(..., kIOHIDParamConnectType, ...)`, selector `kIOHIDCapsLockState`, and `bool*` agree with the Swift probe and [the ctypes Caps test](/Users/jdlien/code/ak820-pro/scripts/fnb_caps_test.py:33). Those tests also support the numeric values `1` and `1`. I found no mismatch. |
| **Access checking** | `IOHIDCheckAccess` only checks permission; this code never requests it. ListenEvent `1`, granted `0`, denied `1` match the expected API values, but the repo contains no SDK enum declaration to independently audit them. |
| **Report layout** | `[2, leds]`, length `2`, with report ID argument `2`, matches both synchronous and asynchronous probe paths. Today’s exact 51-write commit is strong confirmation. The raw-HID transport’s omission of its zero ID from the buffer is not a reason to strip this numbered report’s ID. |
| **Timeout/run loop/pool** | The `1000.0` timeout is correct: [the leak investigation quotes the SDK’s millisecond contract](/Users/jdlien/code/ak820-pro/plans/AK820-AGENT-MACOS-LEAK-PLAN.md:116). `CFRunLoopRunInMode` takes seconds. Running the scheduled default mode and rechecking `Done` after each handled source is appropriate. The calling-thread autorelease pool follows the established transport fix. |
| **Callback lifetime and poisoning** | The context and report live in stable heap allocations. Only one operation is outstanding. A completion error—including timeout—is retired and becomes `Failed`; no callback by the outer deadline becomes `Stuck`. Subsequent writes refuse immediately. Poisoned `Drop` preserves the original buffers/context/device, and also leaks the HID-system connection. That extra connection leak is unnecessary but bounded. |
| **R3-3 in the sender** | [sender.rs:349](/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:349) forgets both lights and guard and exits the worker. This preserves process-lifetime exclusion. A late callback cannot reach freed application storage. |

The exact current SDK declarations, the public/private guarantees for the two CGSession keys, and IOKit’s internal retention on pathological async paths are **not independently verifiable from the repo**. The ownership conclusions above rely on the normal API retirement contract; I found no concrete violation in this implementation.

**`user()` and threading:** `user()` returns `None` for an inactive session or failed Caps query, otherwise `Some(0)` or `Some(CAPS)`. It can still return `Some` after poisoning; that is acceptable because it reports user state, while `write()` and `Core` enforce channel shutdown. Reading HID-system Caps is the appropriate source rather than reading back the LED report the sender itself modifies. Num/Scroll zero is consistent with the stated macOS model; P3 preserves those low bits and carries data in bits 3–7.

Thread affinity is **type-enforced in safe Rust**, not merely convention: the raw-pointer fields make `BleLights` neither `Send` nor `Sync`, and there is no unsafe override. `spawn()` moves only `Mac`, constructs `Core` inside the worker, and opens the device there. The callback’s `Cell` is appropriate for that scheduled run loop.

**Wake inference and CLI coexistence:** ordinary wrapping subtraction and the ±250 ms slack are sound for small sampling jitter. An input after 30 seconds of idleness moves the estimate well beyond that slack. `HIDIdleTime` is nevertheless a machine-wide activity estimate, not Windows’ session-local event timestamp or proof this keyboard woke. It is suitable for the plan’s best-effort resend heuristic; suspend/resume clock behavior remains unverified here.

`lockchan check` is read-only and opens no keyboard. In this commit it does **not** query Caps, despite its comment. `send`/`clear` acquire the lock before opening and retain it through frame execution. They exclude cooperating lock-channel senders, but do not acquire the radio lock or implement the daemon’s transport handover policy; the firmware lease may consequently discard their frames beside an active raw-HID daemon.

The most useful additional tests are:

- Session decoding: absent versus malformed keys, inactive console, locked/unlocked, and failed Caps reads.
- Async state transitions through an injectable wrapper: submission rejection, successful/error completion, missing completion, late completion, and refusal after poisoning.
- `Core` poisoning with drop counters proving the **guard and device remain retained**; the existing frame-level poison test does not establish that.
- P3 executor cases with zero spacing and realistic completion delays, Caps changes at write boundaries, and restoration across unknown state.
- Constructor equivalence, slack boundaries, long-idle conversion, failed idle reads, and wake arming/firing.
- A real daemon-context soak covering lock/unlock, fast user switching, Caps from another keyboard, disconnect, and sleep/wake. The successful frame does not cover these paths.

**Verdict: the macOS FFI and threading design look sound; address the P2 issues and verify session/failure behavior before enabling the daemon channel.**