**Not fully fixed. No must-fix found, but a sender correctness should-fix remains. I would still withhold routine-use clearance on gremlin, independently of the firmware’s open items.**

This was static verification of the three commits and their parents. Process launching was denied, so I could not execute Rust tests or mutation tests. The reviewed sender and agent files match the current checkout.

1. **Should-fix — A warm-up seek back to the previous pick’s numeric position still loses its repeat.**  
   [sender.rs:744](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:744)

   The extra `pos != last.0` condition incorrectly bypasses the elapsed-time-aware `seeked()` check. Concrete sequence:

   - Startup repeats have settled; the last transport picked position **100**.
   - Ten seconds later, a resend starts with playback correctly at **110**.
   - During warm-up, playback seeks back to **100**.
   - `seeked()` would detect that jump, but numeric equality suppresses the revision bump.
   - The pick records **100** as the new baseline at [sender.rs:762](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:762). Completion is credited, and the next step sees no seek. With the repeat already consumed, only the 60-second refresh remains.

   Remove the numeric inequality and rely on `seeked()`. Add this equal-position case. The new 200→50 test cannot detect it.

2. **Should-fix — The requested completion-credit regression test is still missing.**  
   [sender.rs:1545](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:1545), [sender.rs:779](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:779)

   The corrected mid-transport test now exercises position-only cancellation, but stops after checking the recovery gap. It still passes if the completion-time seek check is removed. Add the previously requested seek hook on the **final write**, asserting withheld credit.

   Also extend coverage through the replacement’s repeat after abandoning a first transport with no prior position baseline. The new bookkeeping handles that by inspection, but neither changed test proves it.

The requested fixes and regression value are:

| Change/test | Verdict from tracing old versus new code |
|---|---|
| Warm-up revision and repeat — [sender.rs:1606](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:1606) | **Fixes the reported 200→50 sequence; test detects the old defect.** Old code sends B once; the assertion requires two distinct nearby positions. Equal-position exception remains above. |
| Step baseline and abandoned picks — [sender.rs:541](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:541), [sender.rs:759](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:759) | **Correct by inspection.** Step detection establishes the new baseline; picked positions survive unsuccessful attempts and text refreshes. |
| Position-only mid-transport test — [sender.rs:1557](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:1557) | **Fixed; detects the original missing cancellation check.** Icon, title, playback presence and duration stay unchanged, so the epoch check cannot rescue the old implementation. |
| Bravo transport assertion — [sender.rs:1541](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:1541) | **Fixed.** Ordering still catches transport-before-title, and absence of every Bravo transport now fails. |
| Wake-delay test — [sender.rs:1571](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:1571) | **Detects the old immediate resend.** Its first-two-seconds write assertion fails against that implementation. |

**Note — `WAKE_DELAY` schedules the wake resend; it does not guarantee four seconds without traffic.** At [sender.rs:524](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:524), `force(now + WAKE_DELAY)` uses the earlier deadline through [policy.rs:72](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/policy.rs:72). A refresh already due in one second still goes then; genuine content changes also remain immediate. The test covers the reported wake-triggered regression, not those collisions or the exact four-second boundary.

**Note — `f7b0ffd` looks sound.** Logging occurs only after credited success, once per generation per frame kind, suppressing repeats and refreshes at [sender.rs:789](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:789). Descriptions use the picked payload, and the daemon drains them at [agent.rs:534](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:534). No new functional defect found; logging has no dedicated regression test.