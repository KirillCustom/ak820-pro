**Revision 3 is nearly adequate, but one must-fix remains: R3-4’s session/desktop validity test.** The other remaining issues can be handled during implementation.

This was a static review against the requested sources and Microsoft’s API documentation. No files changed or board I/O performed.

| R2 finding | Is R3 adequate? | Assessment |
|---|---|---|
| 1 — restoration | **Yes** | [R3-1, plan:785](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:785) supplies the missing persistent recovery state, suppresses payloads, bounds retry frequency, and makes cancellation conditional. |
| 2 — lease and handover | **Mostly** | [R3-2, plan:801](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:801) chooses the correct ingress signal and accepts finite-lease limitations. Clarify handover bookkeeping and invalidate assembling frames, below. |
| 3 — poisoning and shutdown | **Yes, with a timing correction** | [R3-3, plan:823](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:823) fixes resource and lock retention, stuck-open isolation, and `--once`. Three seconds bounds exit, but cannot cover the stated worst-case write plus restoration. |
| 4 — fresh, valid lock observations | **Partial; must-fix** | [R3-4, plan:842](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:842) addresses the observation mechanism and explicit unknown state. `OpenInputDesktop` success is insufficient to establish validity. |
| 5 — supersession/starvation | **Yes** | [R3-5, plan:851](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:851) deliberately accepts starvation during continuous changes and handles late final-write commits honestly. Clarify the snapshot-freezing boundary. |
| 6 — phase deadlines | **Yes** | [R3-6, plan:860](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:860) matches the receiver with conservative host-side margins, subject to the precise clock origins below. |
| 7 — stale source | **Yes** | [R3-7, plan:872](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:872) corrects the poll count, carries observation age, schedules expiry-driven CLEAR, and renews freshness independently of content generation. |
| 8 — timing claims and miscellaneous recovery | **Yes, with minor details** | [R3-8, plan:879](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:879) correctly qualifies latency, CLEAR expiry and icon correction, handles backward input timestamps, and provides bounded re-enumeration. Define the startup CLEAR cutoff when history is absent. |
| 9 — firmware handler | **Yes** | [R2-9, plan:698](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:698), retained by R3, remains implementable: validate everything and pass the lease gate before changing either text or playback. |

1. **Must-fix — `OpenInputDesktop` is an access test, not a complete session-validity test.**  
   [Plan:846](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:846).

   Microsoft explicitly documents that it **can succeed in a disconnected session**, returning the desktop that would become active on reconnection. Opening that desktop also does not establish that it is the sender thread’s desktop. Consequently, a disconnected or inactive sender can treat stale session-local locks as authoritative and write them to the physical keyboard. [OpenInputDesktop contract](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-openinputdesktop), [GetThreadDesktop contract](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getthreaddesktop).

   Require a known active, unlocked session and a matching input desktop before returning `Some`; failed or uncertain checks return `None`. Session-state queries/notifications plus desktop identity checks can implement that. Windows exposes connection state and lock/unlock information separately. [WTS session information](https://learn.microsoft.com/en-us/windows/win32/api/wtsapi32/ns-wtsapi32-wtsinfoex_level1_w).

   The freshness measurements themselves are useful and justify implementing plain `GetKeyState`. They do not establish the desktop gate: [GREMLIN-RESULTS.md:304](C:/Users/jdlien/code/ak820-pro/plans/GREMLIN-RESULTS.md:304) explicitly leaves another keyboard and the scheduled daemon context for the soak. Include lock/unlock, disconnect/reconnect and desktop changes in that verification.

2. **Should-fix — define the handover timestamp conservatively; twelve seconds is sufficient only under stated conditions.**  
   [Plan:818](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:818).

   **For a positively acknowledged last media command, twelve seconds after its completion is enough for the ten-second lease under normal clock progression**, provided nothing subsequently renews it. The board accepted the command before the acknowledgment. Twelve seconds after submission, an old text-success timestamp, or an unknown previous process’s activity does not establish that condition.

   The implementation should track a dedicated monotonic timestamp covering **all potentially delivered media commands**, including playback-only cycles, partial text batches and uncertain outcomes. Use transaction completion/end conservatively; unknown startup history needs a conservative acquisition wait or an explicit later acquisition retry. Unretired or arbitrarily delayed operations remain outside any finite-wait guarantee.

   In particular:

   - **USB present, push failed:** BT remains revoked. R2-5 still requires the opener’s `Absent`, and the existing [agent.rs:493](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:493) correctly keeps post-open failures on USB. An expired board lease does not authorize host fallback. A command accepted before its reply was lost renews the board lease; a command never accepted does not.
   - **The daemon’s own radio counts.** Its media requests reach the shared handler through [ch582f_ajazz.c:840](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:840). PLAYBACK state 0 counts too.
   - **Existing “last push” bookkeeping is insufficient.** [agent.rs:567](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:567) and [radio.rs:277](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/radio.rs:277) update text-success bookkeeping; playback is sent separately afterward.

   The five-second interval cap supports the nominal heartbeat cadence. Transport work, failures and suspension can still produce gaps longer than ten seconds.

3. **Should-fix — invalidate an assembling lock-light frame when raw-HID media is accepted, and broaden the residual-risk wording.**  
   [Plan:802](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:802), [plan:813](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:813).

   R3 specifies the commit-time lease gate but still omits the previously requested invalidation. Reset the lock-channel assembly, or reject an assembly whose raw-HID ownership epoch changed.

   The residual risk is **a commit after lease expiry**, not exclusively a complete frame held for ten seconds before its first report. The receiver permits an assembly lasting twelve seconds from its first preamble edge, so without invalidation an assembly can straddle a raw-HID update and outlive its ten-second lease. See [lockchan.c:158](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/lockchan.c:158) and [lockchan.c:213](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/lockchan.c:213).

   Also represent “no raw-HID media received yet” explicitly; initializing the timestamp to zero should not accidentally impose a lease during the first ten seconds after boot.

4. **Should-fix — distinguish the three-second exit budget from completing restoration.**  
   [Plan:832](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:832).

   The stated worst case on an already-open handle is:

   `2 s outstanding operation + 0.6 s recovery + 2 s restore = 4.6 s`

   Therefore, retain three seconds as a **hard exit budget with restoration attempted only within the remaining time**, or enlarge it if completing that sequence is required. R3 already permits best-effort shutdown, so this is not an architectural blocker.

   The lock-retention design is now correct. Reuse [radio_lock.rs:45](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/windows/radio_lock.rs:45)’s read access, sharing flags and actual byte-range lock. Pending I/O resources must remain alive even when the shutdown budget expires; cancellation alone does not retire them. [CancelIoEx contract](https://learn.microsoft.com/en-us/windows/win32/api/ioapiset/nf-ioapiset-cancelioex).

5. **Should-fix — freeze the snapshot before its header, not at the wire payload boundary.**  
   [Plan:853](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:853).

   If “when the payload starts” means the protocol’s payload section, selection is too late: the header already contains type and length, and participates in the payload CRC. See [lockchan.py:84](C:/Users/jdlien/code/ak820-pro/scripts/lockchan.py:84). Select after warm-up/quiet, before beginning the encoded frame; keep that attempt’s bytes immutable.

6. **Note — R3-6’s numbers are correct against the actual receiver.**  
   [Plan:860](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:860).

   The receiver requires quiet **≥150 ms**, preamble continuations **<150 ms**, subsequent gaps **≤500 ms**, and lifetime **≤12 s**. Its lifetime starts at the **first preamble edge**, not the third-edge anchor. See [lockchan.c:149](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/lockchan.c:149), [lockchan.c:179](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/lockchan.c:179), and [lockchan.c:203](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/lockchan.c:203).

   Enforce the 100/300 ms limits at completion against the **saved previous completion**, before replacing that timestamp. A timely submission followed by a slow completion must fail the check. These remain host-side margins, not guarantees about arrival.

7. **Note — `restore_pending` closes the restoration-policy gap.**  
   [Plan:791](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:791).

   Preserve it across unknown lock state and recoverable reopen attempts. Clear it only after a successful fresh-state repair and matching post-write read; content changes must not clear it. On validity returning, restore before starting another payload. This establishes a completed restoration attempt, not acknowledged board state. The stated ~0.6 s indication disturbance is nominal; pending recovery can last longer.

**Can the sender and transport integration be implemented to this specification? Not exactly as written: R3-4 still needs the must-fix validity rule. Once that correction is incorporated, yes—both can proceed, with the remaining should-fixes handled during implementation.**