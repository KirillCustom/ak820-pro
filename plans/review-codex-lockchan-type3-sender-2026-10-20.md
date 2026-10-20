**Yes—for a supervised gremlin test with the type-3 firmware.** I found no new must-fix blocker for that attended run, but **four should-fix correctness issues remain before routine use**.

Reviewed the exact `5813827` changes and both Mac playback reviews. This was static verification: process launching was denied, so I could not run the 52 lockchan tests.

1. **Should-fix — Recheck transport eligibility when picking its payload.**  
   [sender.rs:556](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:556), [sender.rs:654](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:654).

   `shown` is checked before recovery, warm-up and quiet. If A was sent, transport is selected, then the slot changes to B during warm-up, the pick accepts **B’s transport with B’s epochs**, although B’s text has never been sent. Cancellation and completion checks then accept it; A temporarily gets B’s readout.

   A transition to CLEAR or staleness during that interval similarly permits an idle type-3 frame before CLEAR. This is not sustained transport traffic after a completed CLEAR, but it violates the intended eligibility rule.

   At pick time, require now-playing content whose text identity/generation still matches the successfully sent text; otherwise return `None` and reconsider scheduling.

2. **Should-fix — `pos_at = observed` is not reliably correct on Windows either.**  
   [agent.rs:425](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:425), [windows/smtc.rs:208](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/windows/smtc.rs:208).

   Windows captures `observed` **before** waiting for metadata, then reads the timeline at line 214. Its timeline age—and therefore `smtc::position`’s extrapolation—is measured later.

   Example: metadata starts at t=0 and takes five seconds; the snapshot contains position 105 at t=5. Picking at t=6 with `pos_at=0` sends 111 instead of 106. Variable metadata latency can also manufacture seeks.

   Carry the timeline’s actual as-of instant alongside the cached snapshot, retaining the earlier observation timestamp for freshness. The concurrent follow-up `256bb6a` addresses the Mac’s double-counting, but its Windows `POSITION_AS_OF_OBSERVATION=true` assumption retains this gap.

3. **Should-fix — A seek does not invalidate an in-flight transport or rearm its change repeat.**  
   [sender.rs:140](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:140), [sender.rs:468](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:468), [policy.rs:70](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/policy.rs:70).

   Position changes neither transport identity nor `tr_epoch`. A seek after the pick therefore leaves the obsolete position frame eligible to finish **and receive credit**, even when plenty of writes remain. The next step detects and corrects it, but the required supersession never happens.

   Separately, `force()` changes only the deadline. After initial repeats settle, a seek gets one transmission, then the 60-second refresh—not the change’s two-second repeat. Give detected seeks their own transport revision and repeat obligation, without making ordinary ticks changes.

4. **Should-fix — Clamp the extrapolated current position before seek comparison.**  
   [sender.rs:467](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:467), [sender.rs:657](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:657).

   `is_seek` clamps its expectation, but the sender’s `pos_now` keeps advancing past duration. With sent `(299, t=0)`, slot `(300, pos_at=1)`, duration 300 and now=5, the comparison becomes **304 versus 300**, falsely declaring a seek. Encoding clamps it back to 300, while the recorded picked position remains beyond the end.

   An aging end-of-track snapshot can consequently generate repeated transport traffic. Clamp both extrapolated positions to known duration; retain full-width host values and apply `0xFFFF` saturation only when encoding.

**Notes — the other requested paths look sound on inspection:**

| Area | Assessment |
|---|---|
| Tick-driven title resends | Text identity excludes icon and position; unchanged observations still update the payload and position pair. No ordinary tick-driven title resend found. |
| Text/transport scheduling | Text wins when both are due. Every credited text completion forces transport, including repeats and refreshes: [sender.rs:691](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:691). Stable content does not starve transport. |
| Epochs and credit | After picking, text changes cut either kind; transport identity changes cut only transport. Ownership, epochs and effective identity are checked before credit. The seek omission above is the substantive gap; I found no practical hash-specific defect. |
| Staleness and CLEAR | Stale content becomes CLEAR with `pb=None`. The normal gate suppresses idle transport; the separate text policy preserves CLEAR’s bounded repeats. |
| Acquisition | Both policies reacquire, and both publication memories reset: [sender.rs:487](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:487). |
| Open backoff | Failed opens charge the text policy, but `next_open` gates either kind. This can bring forward a text resend after a transport failure; it does not create a tight retry loop. Restore-open failures retain their separate one-second schedule. |

**The tests bite, but incompletely.** The pause, ticking-position, initial transport and between-frame seek assertions detect useful regressions. However:

- [sender.rs:1280](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:1280) checks frame presence, not ordering or transport after **every** text refresh.
- [sender.rs:1327](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:1327) starts already CLEAR; it never cancels pending/in-flight playback.
- No new test covers a seek during I/O, final-write credit, distinct `observed`/`pos_at`, or sender-level duration clamping.

Add those cases, plus transport changes during text and text changes during transport. Deliberately desynchronize the policies before testing a text refresh.

The paired firmware also retains the previously documented text-lease and optimistic-key P2s in the [Mac follow-up](C:/Users/jdlien/code/ak820-pro/plans/review-codex-lockchan-playback-verify-2026-10-20.md:3). That supports an **attended diagnostic test**, not a claim that the sender/firmware pair is ready for daily use.