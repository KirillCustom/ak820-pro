**Not fully fixed. Three must-fix paths remain. I would fix them before flashing this as the attended bench-test candidate.**

I verified that `gremlin/rtc-clock-fixes` points to `8f254b0c`, its parent is `090378fd`, and the changed working files match the commit’s Git blobs.

| Original finding | Closed? |
|---|---|
| **1 — Running write superseded by legacy set** | **Partial.** Normal supersession works; failed re-planning can still certify an obsolete snapshot. |
| **2 — Unbounded STOP recovery** | **Partial.** Recovery now yields and permits sleep, but a legacy success can erase its deferred repair obligation. |
| **3 — Stale-wake arithmetic and tick race** | **Partial.** Ordinary arithmetic is fixed; the retry loop can apply the correction twice. |
| **4 — External `SKIP_PCF`** | **Partial.** A clean, idle PCF no longer gets an unwanted request. Reference validity and in-flight ownership remain unresolved. |
| **5 — Superseded bracket learning** | **Partial.** Corrections during `PS_BRACKET` invalidate it, but corrections before `PS_VERIFY` can lose that invalidation. |
| **6 — Legacy takeover leaves an old slew running** | **Closed.** The setter cancels the slew under lock and requests nominal-period restoration. [rtc.c:1214](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1214) |

**Must-fix — Failed re-planning consumes the newer generation.**  
[rtc.c:1461](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1461)

At `PS_TIME_WRITE`, `pcf_sync_run_start()` updates `run_gen` **before** `ps_plan()` succeeds. If planning fails, the state remains `PS_TIME_WRITE`, with the old `ps_S` and release deadline.

On the next pass, `pcf_sync_superseded()` is false, so the old snapshot gets written. If its release window remains usable, verification clears both `pending` and `stale` as though the newer correction had been persisted.

Advance `run_gen` only after successful planning, or retain an explicit “needs plan” state. A failed plan must never make the previous snapshot eligible for writing.

**Must-fix — A legacy success can strand the PCF in STOP after recovery gives up.**  
[rtc.c:1569](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1569), [rtc.c:1226](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1226)

The new deferred state is correctly idle, not pending, stale, and maybe stopped. But consider a successful legacy set during that interval:

1. `pcf_write()` writes calendar registers; it does **not** release STOP.
2. `ps_state_idle()` is true, so `pcf_sync_written()` clears `stale`.
3. No request is queued, and automatic retry requires `stale`.

The oscillator can remain stopped indefinitely, with the repair obligation lost. Discipline also gates on `stale`, not `pcf_maybe_stopped`, so it can subsequently trust that frozen calendar.

An idle legacy success must still preserve staleness and request full recovery/persistence when STOP is asserted or uncertain.

**Must-fix — The stale-wake retry repeats an already-applied gain.**  
[rtc.c:1920](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1920)

The loop calls `rtcSetTime()` before checking whether a tick occurred. Its next iteration reads the already-adjusted calendar and adds the same `gain` again.

Using the original example:

- Counted sleep: `3139`; awake ticks: `2`; PCF elapsed: `3602`.
- Correct gain: `461`.
- A tick between calendar read and set makes the first write `3602`, losing that tick.
- The retry then writes **4063**, when the correct elapsed calendar is **3603**.

A tick before the get or after the set also causes a duplicate adjustment. Three iterations bound execution, not the clock error.

Apply a signed whole-second adjustment once under lock. Alternatively, validate the snapshot under lock **before committing**, retrying without having written anything.

**Should-fix — `SKIP_PCF` still leaves an obsolete PCF authoritative.**  
[rtc.c:1186](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1186), [rtc.c:1322](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1322)

Suppressing `pcf_sync_host_accepted()` fixes the unwanted idle retry, but also suppresses the fact that the PCF calendar is now wrong. An existing writer receives no generation change, so it can write its earlier snapshot and clear staleness. With no writer, a previously fresh PCF remains fresh despite the board correction; a later wake can restore its old calendar.

Separate reference validity from permission to persist. The public flag currently promises only “do not queue the PCF write.”

**Should-fix — `PS_VERIFY` erases bracket invalidation.**  
[rtc.c:1507](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1507), [rtc.c:1533](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1533)

A correction after release but before verification sets `ps_brk_void=true`. Verification then unconditionally resets it to false, even when `pcf_sync_run_ok()` leaves a newer request pending.

A +300 ms phase step in that interval can still produce roughly +300 ms of apparent edge error and add 150 ms to the learned release lead. Preserve invalidation across verification and reject learning from superseded runs.

**Should-fix — An in-flight replacement can bypass the settled-slew gate.**  
[rtc.c:1444](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1444), [rtc.c:1461](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1461)

The slew/reload gate applies only in `PS_IDLE`. If another accepted correction starts a slew while the writer is active, re-planning immediately adopts its generation and samples the partly corrected clock. Verification can clear `pending/stale` while the slew still has hundreds of milliseconds left to apply.

Keep that newer request outstanding until its phase adjustment settles. The earlier “settled slew write” finding is therefore still incomplete for in-flight supersession.

**Notes on the requested edge cases:**

- **Ordering and `diff == 0`:** subtracting `diff` in the `awake` calculation correctly undoes the preceding counter adjustment. Without an intervening tick, the example now gives `awake=2`, `gain=461`, and calendar elapsed `3602`. Removing the old `diff != 0` guard is correct: when `diff==0`, `gain` can still equal `-awake`. [rtc.c:1898](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1898), [rtc.c:1917](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1917)
- **Remaining elapsed-count error:** that same example still leaves `rtc_seconds_count` advanced by `3604`, because `+= diff` includes the two awake seconds again. `wk_slept_s` likewise includes wake delay. This predates the follow-up; the new gain fixes only the calendar calculation.
- **Loops and sleep:** ordinary release-state supersession progresses through STOP/re-plan/write; it does not inherently loop. Recovery performs six release attempts, then exits on the next due check, preserving stale/maybe-stopped and clearing pending. This permits sleep. It is an admitted-attempt bound, however, not an unconditional 30-second wall-clock deadline. [rtc.c:1561](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1561)
- **Bus rules:** I found no new production violation. The new transitions perform zero or one PCF transaction per step; deep-wake recovery retains `bus_admit()`, the short timeout, and display deferral. The ordinary path retains its already-used-pass guard. [rtc.c:2115](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:2115)

**Should-fix — The test still does not exercise these state-machine failures.**  
[rtc_pcf_sync_test.c:37](C:/Users/jdlien/code/ak820-pro/scripts/rtc_pcf_sync_test.c:37)

The added case checks generation comparison and manually calls `run_start`; it never executes planning, release, recovery, wake adjustment, or `rtc_sleep_ok()`. Add integration cases for failed re-planning, legacy success after deferred STOP recovery, ticks around wake adjustment, correction between release and verify, and a slew superseding an active writer.

Process execution was denied, so I could not compile/run the C test or firmware. Validation was exact-commit source review plus in-memory JavaScript arithmetic/state traces. No files were changed.