**I would not sign off yet.** Findings **1 and 2 are addressed for the production call paths**. Finding **4 remains incomplete for legacy sets**, finding **5 still has sleep/recovery failures**, and **8 fixes ordinary queued writes but leaves an in-flight measurement issue**.

I verified the commit’s parent and checked the relevant working files against its Git objects. No files were changed.

1. **Must-fix — A successful legacy set does not supersede an already planned PCF write.**  
   [rtc.c:1198](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1198), [rtc_pcf_sync.h:71](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc_pcf_sync.h:71)

   Reproducible sequence: writer A asserts STOP and snapshots `ps_S`; before `PS_TIME_WRITE`, a legacy USB set B succeeds. `pcf_sync_written()` clears `stale` but neither advances the generation nor invalidates A’s snapshot. On the following pass, A writes its old `ps_S` over B. `PS_VERIFY` sees matching generations and clears `pending`, leaving the old calendar marked authoritative.

   The legacy caller already received successful `[0]=0x07`. A later sleep or legacy discipline can restore A’s time to the board. The header’s assertion that pending work writes the current board time is false once `ps_S` has been captured.

   Every accepted external correction needs to supersede older persistence work, including successful synchronous sets. Coordinate cancellation/replanning with STOP recovery; merely advancing the generation while clearing `stale` is insufficient if obsolete work can subsequently overwrite the PCF and fail.

2. **Must-fix — A PCF that dies after STOP is asserted still holds the board awake indefinitely.**  
   [rtc.c:1401](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1401), [rtc.c:1517](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1517), [rtc.c:1711](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1711)

   `ps_fail()` calls `pcf_sync_gave_up()` only when `stop_asserted` is false. Otherwise it enters `PS_RECOVER`, which retries indefinitely. `rtc_sleep_ok()` rejects every non-idle writer state, independently of `pending`. Failed recovery writes—or successful releases whose verification never succeeds—therefore prevent sleep forever.

   This is pre-existing behavior, but it directly defeats this fix’s stated acceptance criterion. The new helper solves the **pre-STOP failure** case only.

   Recovery needs a bounded active period followed by a sleep-compatible, deferred recovery obligation. While STOP/running state is uncertain, neither the PCF calendar nor its elapsed-time difference should be trusted. Preserve the obligation to release STOP after wake.

3. **Must-fix — The new stale wake branch double-counts awake seconds before the coarse read.**  
   [rtc.c:1833](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1833), [rtc.c:1856](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1856)

   `after - wk_pre_epoch` includes the time between wake and the admitted coarse read. `wk_counted` includes only the sleep’s counted seconds. The current board calendar has already advanced through those awake seconds, so adding their difference counts them again.

   Example: sleep lasts 3600 seconds, the sleeper counts 3139, and bus admission delays the coarse read through two awake ticks. PCF elapsed is 3602; `diff=463`; the board has already advanced 3141. The new branch advances it to **3604 instead of 3602**. Acquisition then correctly refuses the stale PCF, and a successful retry can persist this erroneous board time.

   Measure both clocks over matching endpoints, subtracting already-accounted awake ticks. Also make the calendar adjustment atomic: the current get/convert/set sequence can overwrite an RTC tick arriving between its read and write. A signed whole-second LLD adjustment would avoid that race and the conversion round trip.

4. **Should-fix — External `SKIP_PCF` now silently schedules persistence anyway.**  
   [rtc.c:1263](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1263), [rtc.c:2106](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:2106), [rtc_pcf_sync.h:76](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc_pcf_sync.h:76)

   An external skipped write still sets `stale`. With initially zero `retry_at`, the next housekeeping call normally queues a write immediately. If an older writer exists, skipping the request-generation bump also lets that older run clear the new correction’s staleness.

   Separate calendar validity from permission to schedule persistence, or explicitly change the flag contract. **Acquisition is unaffected:** its `FORCE_STEP | SKIP_PCF | INTERNAL` combination neither marks stale nor creates a request.

5. **Should-fix — Superseded runs can still corrupt the release-lead measurement.**  
   [rtc.c:1472](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1472), [rtc.c:1498](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1498)

   The idle gate prevents a new run from starting during a slew/reload. It does not invalidate an existing `PS_BRACKET` when another correction changes the board phase. Its timestamps use `board_ms_now()`, whose fractional component changes on a step.

   For example, a +300 ms phase change after release can make an unchanged PCF edge appear 300 ms late, increasing `pcf_d_first_ms` by 150 ms. The replacement write then uses that contaminated lead. Its subsequent bracket adjusts the *next* run’s lead; it does not repair the phase just persisted.

   Invalidate bracket learning on a superseding correction, including corrections arriving after `PS_VERIFY`. Use the run-generation result plus a measurement-validity check.

6. **Should-fix — Legacy takeover still leaves an older slew running.**  
   [rtc.c:1201](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1201), [rtc.c:552](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:552)

   Unlike the millisecond step path, the legacy setter does not cancel `slew_state`. A successful legacy correction during a previous slew inherits its remaining phase adjustment after synchronously writing the new PCF time. This is pre-existing, bounded by the remaining slew, but should be resolved alongside legacy persistence ownership. Cancel the old slew and arrange the nominal-period restore.

For the five original findings, ordinary USB behavior is:

| Finding | `RTC_SET_TIME_MS`, ordinary flags | Legacy `RTC_SET_TIME` |
|---|---|---|
| **1: trim window** | Accepted steps and slews invalidate the window. Discipline waits for writer, stale state, slew, and reload to settle. Accepted reply remains `00`/`01`. | Accepted mutation invalidates the window even if persistence fails. Reply remains success `07` or failure `FF` at byte 0. |
| **2: false synchronization on RETRY** | Fixed: `FE` retries and setter `FF` rejections do not advance `host_sync_gen`; accepted `00`/`01` results do. | Busy refusal does not advance ownership. An I2C failure still advances ownership because SN32 time actually changes, despite reply byte 0 being `FF`. |
| **4: superseded write** | Ordinary accepted sets increment the request generation; an older successful verify leaves the newer request pending. Reply remains immediate `00`/`01`. | Successful synchronous sets lack this protection: must-fix 1 above. |
| **5: failed persistence** | Staleness blocks discipline/acquisition/calendar restoration. Ordinary give-up clears pending and schedules repair after 60 counted seconds. Later I2C failure does not alter the already-sent reply. Recovery gaps remain above. | Successful write clears stale; failed write now queues repair while still applying SN32 time and returning byte-0 `FF`. |
| **8: settled slew write** | New runs wait for slew completion and reload restoration. Step writes also wait for their initial reload. Replies do not wait: still `00`/`01`. | Remains synchronous; the slew gate does not govern this path. |

**Note — Wire layouts are unchanged by all five fixes.** For valid millisecond requests, bytes `[0..2]` remain `07 10 03`; `[3]` is `00` stepped, `01` slewing, `FE` retry, or `FF` reject; `[4..5]` contain little-endian signed **target minus board** milliseconds on acceptance; `[11]=02`. Setter retries/rejections return zero offset through this wrapper. The earlier payload-validation rejection path still leaves other payload bytes unchanged. Legacy replies echo the packet, changing only `[0]` to `FF` on failure/refusal. See [hid_protocol.c:117](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/hid_protocol.c:117) and [hid_protocol.c:1310](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/hid_protocol.c:1310). Health page 4 newly exposes stale at payload `[24]`, report `[28]`.

**Note — The remaining guards are sound within the traced production paths.**

- Acquisition checks cancellation before bus admission and again before stepping. Its internal set preserves host ownership; the caller restores `host_synced=false`.
- The stale wake difference has the correct **sign**, and the bounded sleep keeps it safely within `int32_t`. Whole-second conversion preserves the hardware prescaler phase. `clock_gen` advances when the calendar changes; no advance for `diff==0` is appropriate.
- Retry enqueueing performs no I2C. Writer/recovery states perform at most one transaction each. Legacy sets refuse an already-used pass and sleep recovery; discipline checks the pass counter. Deep-wake work retains `bus_admit()`, the short timeout, and display deferral. I found no new production path violating those bus rules. See [rtc.c:2035](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:2035).
- Ordinary phase snaps still preserve the snap-immune trim window, as required.

**The host test bites at the helper level, but does not validate these fixes end to end.** [rtc_pcf_sync_test.c:24](C:/Users/jdlien/code/ak820-pro/scripts/rtc_pcf_sync_test.c:24) would catch unconditional clearing on an older run’s success; the failure and wrap cases check useful retry bookkeeping. However, its synchronous-write test starts from idle, and its “board may sleep” test checks only `pending`, not `rtc_sleep_ok()`.

It never executes `rtc.c`. Removing the acceptance hooks, slew gate, acquisition cancellation, or discipline gate would leave this test unchanged. Before sign-off, add integration cases for legacy takeover after planning, permanent failure after STOP, delayed stale wake with intervening ticks, and correction during bracket measurement.

I could not compile/run the C test or firmware because process execution was denied. Validation here was source review plus small JavaScript state/arithmetic checks, not hardware testing.