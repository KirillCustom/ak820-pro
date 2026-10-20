**R2 is not ready for full implementation as written.** The encoder and R2-9 payload validator/display adapter can start. The production sender and transport integration need the restoration, ownership, and I/O fixes below first.

This was a static review against the requested sources. No files changed or hardware exercised.

For each original finding, the disposition is reasonable; several revisions only partially resolve it:

| Original # | Adequate? | Assessment |
|---|---|---|
| 1 — restoration | **No** | R2-1 adds the right mechanism, but its cancellation guarantee is false, and exhaustion after three repairs has no recovery policy. |
| 2 — fresh locks | **Conditional** | R2-2 establishes a useful measurement gate. Moving reads to the main thread is not an established fallback. |
| 3 — obsolete frames | **Partial** | Generations correctly protect bookkeeping. Cancellation introduces starvation and cannot retract an already-issued final write. |
| 4 — stale bounds/CLEAR | **Partial** | Board-relative 180 s expiry is correct. The ~6 s maximum and host-relative CLEAR cutoff remain unjustified. |
| 5 — host ownership | **Yes, at design level** | Revocation before the higher-priority push is correct. It requires new integration points, not merely the existing deferred `USB_SEEN` handling. |
| 6 — board ownership | **No** | Ten seconds is a lease heuristic, not proof of source ownership or protection against every late commit. |
| 7 — handover publication | **Mostly** | Separate BT bookkeeping and forced snapshots are correct. R2-6 can nevertheless discard both initial handover attempts. |
| 8 — I/O/shutdown/serialization | **No** | Poisoning must retain the machine-wide lock as well as all live I/O resources. Open and shutdown semantics remain incomplete. |
| 9 — payload contract | **Yes** | R2-9 is sufficient to implement a bounded firmware handler. |
| 10 — playback off | **Yes** | Both accepted types explicitly turn playback off. Rejected frames must not do so. |
| 11 — stale observations | **Mostly** | Observation age is the right signal. Correct the poll count and specify expiry-driven CLEAR scheduling. |
| 12 — retries/discovery | **Mostly** | Pending state, backoff, ambiguity refusal, and per-handle `UnitId` are correct. Discovery wakeup and repair-failure handling need definition. |
| 13 — phase timing | **Partial** | The proposed 300 ms rule does not enforce the preamble’s `<150 ms` requirement. |
| 14 — wake resends | **Mostly** | Resend reasons and idle arming are good. Nonmonotonic input timestamps need handling beyond wraparound. |
| 15 — optimistic icon | **Partial** | Accepting slower correction is legitimate; “up to 60 s” and “3 s on raw HID” are inaccurate. |
| 16 — serialized identity | **Yes** | `(type, payload)` is correct; keep freshness updates independent of content generation. |
| 17 — tests | **Yes, as a framework** | Add the counterexamples below, particularly repair exhaustion, lease handover, and sustained supersession. |
| 18 — soak | **Yes** | R2-16 covers the relevant input, display, timing, and transport effects. |
| 19 — full snapshots/no wire ID | **Conditional** | Full snapshots need no delta ID. Local generations alone cannot provide strict ordering against delayed wire traffic. |
| 20 — evidence wording | **Mostly** | The disposition corrects the measurements. R2-1 and R2-6 introduce new unsupported “never” guarantees. |
| 21 — deferred playback | **Yes** | Rejecting bit 7 and assigning extensions new types closes this. |

The remaining findings, in priority order:

1. **Must-fix — R2-1 needs an explicit unresolved-restoration state, and must stop claiming that its wait guarantees cancellation.**  
   Applies at [plan:625](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:625).

   A repair is an ordinary LED report to the receiver:

   - **It can start a preamble.** After quiet, `prev=0x05 → restore=0x00` produces `x=0x05`, exactly the P0 preamble-start condition. The decoder enters `ST_PRE` and holds Scroll. One such report does not commit a frame; without continuations, it expires after 150 ms. See [lockchan.c:149](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/lockchan.c:149), [lockchan.c:237](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/lockchan.c:237), and [lockchan.c:258](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/lockchan.c:258).
   - **It can be interpreted within an active frame** if the receiver has not actually expired. Its gap is measured from received transitions, not the sender’s last completion. Delayed reports and genuine Num/Scroll changes are outside that host-side clock. Once in the trailer state, an appropriate transition commits normally. See [lockchan.c:197](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/lockchan.c:197).
   - **Three tries bound work; they do not establish restoration.** A lock change during the third write can leave the final read disagreeing with the value written. That is a known unresolved state, not success.

   Retain a `restore_pending` condition after exhaustion or a recoverable repair error; suppress new payload transmission, retry fresh restoration after a bounded cooldown, and report degraded status. Shutdown can still stop after a bounded best-effort attempt. Never repair over an unretired operation.

   The >600 ms wait does **not** change Windows’ lock state or directly change typed characters. It can leave the board’s Caps indication wrong during that interval. Scroll has additional latency: while decoding, its settle timer is continually restarted; afterward it requires 1.5 s settled, and the board-local key’s fast-response window is only 500 ms. Immediate repeated frames can postpone that indication further. See [indicators.c:82](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/indicators.c:82), [indicators.c:234](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/indicators.c:234), and [indicators.c:361](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/indicators.c:361).

   Keep the recovery wait, but describe its cancellation protection as conditional on receiver-observed silence. Include repair-created preambles, lock changes during recovery, and a mismatch after the third repair in tests.

2. **Must-fix — R2-6’s signal and lease semantics are insufficient, and the lease creates a handover hole.**  
   Applies at [plan:673](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:673).

   The publisher behavior is:

   | Publisher | Idle behavior relevant to a 10 s lease |
   |---|---|
   | Rust USB/raw81 daemon | Sends PLAYBACK state 0 every media cycle, including idle. Default cycle is 3 s. |
   | Windows Python agent | Also sends PLAYBACK state 0 every poll, including idle. |
   | Legacy macOS shell/Python agent | Sends playback-off once after playback stops; afterward relies on text/CLEAR keepalives, default 30 s. |

   Sources: [media.rs:94](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/media.rs:94), [agent.rs:586](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:586), [radio.rs:285](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/radio.rs:285), [nowplaying-windows.py:259](C:/Users/jdlien/code/ak820-pro/hostagent/nowplaying-windows.py:259), and [nowplaying-macos.sh:138](C:/Users/jdlien/code/ak820-pro/hostagent/nowplaying-macos.sh:138). The Rust push interval is also configurable above ten seconds: [daemon.rs:65](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/windows/daemon.rs:65).

   Therefore:

   - A timestamp for **SET/SET_LINE only** is wrong even for the default Rust daemon: text keepalives are 30 s apart.
   - A timestamp for **successfully accepted raw-HID media commands**, including CLEAR and PLAYBACK state 0, supports the default Rust and Windows Python cadences.
   - Ten seconds still does not cover every existing producer or supported interval.

   Record this at the raw-HID ingress, [hid_protocol.c:317](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/hid_protocol.c:317), shared with raw81 dispatch. Do **not** reuse `text_stamp` or update the ownership timestamp inside generic display setters: BT uses those setters too, CLEAR does not refresh `text_stamp`, and PLAYBACK has a separate timestamp.

   **The late-commit race is only temporarily masked.** After the lease expires, an old delayed frame becomes eligible again. The receiver’s 12 s lifetime starts on reception; it does not bound how old a frame was before its preamble arrived. R2-5’s host generation cannot recall reports already accepted by Windows.

   **Handover also regresses without any packet loss.** Suppose raw-HID activity stops just before BT acquisition. A P0 CLEAR has 37 writes: the first attempt can finish around 1.5 s and its repeat around 4.4 s. Both are rejected by the ten-second gate. Many short title frames behave similarly. The next opportunity is then the minute refresh.

   Specify a real ownership/lease contract: accepted heartbeat commands, supported producer cadence, acquisition/release behavior, invalidation of an assembling lower-priority frame, and publication after the exclusion interval. If retaining a finite lease, explicitly accept its residual delayed-frame risk. It cannot support “never overwrite … whatever the host’s races.”

3. **Must-fix — poisoning must retain the machine-wide lock, and bounded shutdown needs more than an IOCTL timeout.**  
   Applies at [plan:688](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:688).

   R2-8 retains the handle and buffers but does not explicitly retain the owner guard. If revocation, destruction, or a failure path releases that guard, another process can start writing while the poisoned operation may still complete.

   The existing raw81 implementation deliberately retains the guard for the process lifetime: [radio.rs:341](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/radio.rs:341). Adopt that invariant explicitly for lockchan.

   Retain the pending `OVERLAPPED`, event, buffers, device handle, and owner guard together. Cancellation can finish normally, and requesting cancellation is not completion. [Microsoft’s cancellation contract](https://learn.microsoft.com/en-us/windows/win32/api/ioapiset/nf-ioapiset-cancelioex).

   Also define:

   - The numeric cancellation grace and shutdown budget.
   - How a stuck discovery/open operation is isolated; `FILE_FLAG_OVERLAPPED` does not give `CreateFileW` an open deadline.
   - Whether `--once` waits for one bounded BT attempt. Returning after the existing main-loop pass can otherwise terminate before publication.
   - Lock ownership through warm-up, transmission, recovery, restoration, and poisoning.

   “No lock is held across I/O” should say **no latest-slot mutex**. The machine-wide owner lock must remain held. Reuse the actual byte-range-lock semantics, access permissions, and no-delete sharing from [radio_lock.rs:39](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/windows/radio_lock.rs:39); a file’s existence is not serialization.

4. **Must-fix before the Windows sender — the main-thread fallback in R2-2 is not a freshness contract.**  
   Applies at [plan:635](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:635).

   Measuring the actual background-thread behavior is appropriate. But “if nothing is fresh, read on the daemon’s main thread” does not resolve the problem: that thread is a scheduling loop, can perform blocking transport work, and sleeps between passes. See [agent.rs:293](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:293) and [agent.rs:447](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:447).

   Establish a verified observation provider with validity/session handling and defined freshness at each write. Represent unknown explicitly in the trait. A cached main-thread value must not silently become “fresh.” `GetKeyState` follows input-message processing, regardless of which thread is named “main.” [Microsoft’s GetKeyState contract](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getkeystate).

5. **Should-fix — cancel-on-supersede can starve indefinitely; specify that policy deliberately.**  
   Applies at [plan:645](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:645).

   During continuous track changes faster than a complete attempt, **nothing need finish**. Each new generation cancels the previous attempt, then incurs restoration and framing overhead. Once changes stop, the final snapshot can finish; there is no guarantee for intermediate tracks.

   That is a defensible latest-state policy, but document it and test sustained skipping, followed by a stable final track. Coalesce throughout restoration and warm-up, selecting the latest generation before starting payload transmission. Supersession should not count as an I/O failure that increases retry backoff.

   If periodic progress during continuous changes is required, choose a bounded noncancellable attempt or another explicit progress policy, accepting temporarily obsolete content. Debouncing alone reduces wasted traffic but does not guarantee progress under endless changes.

   Separately, a content change after the final write is issued can still be followed by a commit of that frame. Generation checks protect local state; they cannot retract that write.

6. **Should-fix — R2-12 needs separate preamble and data deadlines, checked before issuing another write.**  
   Applies at [plan:726](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:726).

   A 200 ms preamble continuation passes the proposed 300 ms threshold but fails the receiver’s `<150 ms` rule. See [lockchan.c:179](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/lockchan.c:179).

   Define phase-specific limits: recovery, deliberate quiet, preamble continuations, and header/payload/trailer. Exclude intentional 300/600 ms waits from the ordinary inter-write rule. Check elapsed deadlines before submission after a suspension, then check completion as well. A post-write check cannot undo a trailer already delivered.

7. **Should-fix — R2-10’s 30 s policy is acceptable, but its rationale and scheduling need correction.**  
   Applies at [plan:714](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:714).

   The actual worker interval is capped at 0.5 s by [windows/mod.rs:72](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/windows/mod.rs:72). Thirty seconds represents about **60 nominal polling intervals**, not ten; read duration adds to each interval.

   Thirty seconds can remain an intentional grace period. It need not automatically become five seconds.

   `Health::stale_for` is appropriate: it derives from the recorded observation instant, not the latest slot read. See [windows/smtc.rs:325](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/windows/smtc.rs:325) and [windows/smtc.rs:379](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/windows/smtc.rs:379).

   Specify that crossing the threshold **creates and schedules a CLEAR generation**, including cancellation of stale pending work. Merely checking age when the next 60 s refresh happens delays that policy. Carry an observation instant/deadline so age continues increasing while queued. Fresh observations of identical content renew freshness without cancelling the frame; failed reads still produce idle immediately.

8. **Should-fix — several remaining timing statements are opportunities, not bounds.**  
   Applies at [plan:653](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:653), [plan:736](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:736), and [plan:744](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:744).

   - **~6 s:** reasonable nominal estimate for stable content and prompt I/O, not “at most.” Worker polling, main-loop observation, I/O duration, repeated supersession, and ownership exclusion all add latency.
   - **CLEAR cutoff:** 180 s after host transmission is not 180 s after board application. Delayed or ambiguously completed title attempts matter; startup also lacks previous-process history. State a bounded retry policy without claiming that host time proves board expiry.
   - **Icon correction:** unchanged raw-HID text normally reasserts at the **30 s text keepalive**, not every 3 s PLAYBACK command. PLAYBACK does not repair `text_icon`. BT correction requires the next refresh **plus transmission**, and successful reception. See [media.rs:94](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/media.rs:94) and [display.c:421](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:421).
   - **Wake detection:** handle backward-moving input timestamps as well as wrap. Microsoft explicitly permits nonincremental values. [GetLastInputInfo contract](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getlastinputinfo).
   - **Discovery:** “until the list changes” needs a notification or bounded re-enumeration mechanism, including recovery from missed notifications.

9. **Note — R2-9 is ready as a firmware-handler contract.**  
   Applies at [plan:698](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:698).

   Its lengths, subtraction guard, icon range, printable ASCII restriction, empty-line behavior, and unsupported-type handling match the display API. Validate everything and pass the ownership gate before mutating anything. Then update both lines and call `display_set_playback(0, 0, 0)`; CLEAR clears text and playback.

   Run this once on a successful decoder commit in main-loop context, using bounded RAM-state setters and the existing deferred paint path. Keep raw-HID ownership bookkeeping outside those shared setters. The relevant entry points are [ch582f_ajazz.c:1837](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:1837), [display.c:2263](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:2263), and [display.c:2337](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:2337).

**Implementation can start on the encoder, payload validation, and isolated display-handler tests. Full sender implementation should wait for the four must-fix contracts above; R2 should not yet be treated as an approved implementation specification.**