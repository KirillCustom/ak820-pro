**No P1 found before flashing.** The combined firmware is suitable as JD’s dev candidate. The proposed soak needs additional checks before it can establish RTC sign-off.

1. **P2 — The soak does not guarantee a clock sync and PCF write on every data-on.**

   **Failure:** the daemon’s [clock loop runs every 15 seconds](/Users/jdlien/code/ak820-pro/ak820-agent/src/clock/scheduler.rs:25). The runner [restores data, waits for enumeration, samples, then starts the next cycle](/Users/jdlien/code/ak820-pro/scripts/deep_cycles.py:283); enumeration adds only a two-second settling wait. USB can disappear before the daemon notices it. Consequently, 100 sleep cycles need not exercise 100 reconnect writes. The final counters can also precede a write’s completion.

   **Fix:** keep data on until that cycle has a confirmed successful daemon sync and the PCF writer has settled. Then sample stalls, UART errors and RTC status before cutting data again. Correlate syncs within each actual data-on interval; the existing 60-second log-matching window is insufficient proof.

2. **P2 — Passing `deep_cycles` does not establish RTC correctness or cover all three fixes.**

   **Failure:** [its pass predicate](/Users/jdlien/code/ak820-pro/scripts/deep_cycles.py:314) checks slept duration and transport behavior, but does not gate clock offset, PCF staleness or persistence completion. A second-scale wake error can be corrected by the next enumeration sync and still pass. Repeated short cycles reset the legacy calibration window; an hour asleep does not exercise that awake calibration window either.

   The firmware correctly exports stale at **HC_RTC page 4 payload `[24]`, full report `[28]`**, but the [existing status printer omits it](/Users/jdlien/code/ak820-pro/hostagent/rtc_phase0.py:127). The C host test exercises bookkeeping helpers, not the actual writer, recovery or wake state machines.

   **Fix:** add the RTC gates below, include an uninterrupted awake BT calibration interval, and retain separate deterministic or attended fault tests for supersession, failed planning and deferred STOP recovery.

3. **P3 — The known slept-seconds overcount remains.**

   [The coarse wake path](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1904) still assigns the entire pre-sleep-to-post-wake PCF interval to `wk_slept_s` and adds its difference to uptime.

   **Failure:** a 3,600-second sleep counts 3,139 seconds, followed by two awake ticks before the PCF read. The PCF interval is 3,602; uptime advances by **3,604**, and reported sleep is **3,602**. The new stale-calendar branch correctly produces **3,602** at that read, so its former duplicate calendar correction is fixed. Sleep totals and battery accounting retain the small overcount.

   **Fix:** subtract awake time once when deriving slept duration, then use that corrected duration consistently for uptime and battery accounting. This is already [documented as deferred](/Users/jdlien/code/ak820-pro/plans/BACKLOG.md:137); it is not a new combination regression or flash blocker.

4. **P3 — An existing `clock_relayout` path can still invoke drawing every housekeeping tick.**

   **Failure:** change clock format while playback owns the band. [The playback branch returns](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:1253) before clearing `clock_relayout`, so housekeeping repeatedly calls `draw_clock()` until playback ends. These are normally empty diffs, but the repeated formatting/diff work remains.

   **Fix:** consume the relayout flag during playback, or gate it on clock ownership; leaving playback already forces repaint. **`pb_repaint` itself does not have this defect.**

The combination checks otherwise pass on inspection:

- **RTC and sleep:** accepted corrections invalidate the legacy trim window; stale PCF calendars cannot drive discipline, acquisition or coarse calendar restoration. A running stale PCF can supply elapsed time; a maybe-stopped PCF cannot. Supersession preserves newer requests, failed re-planning cannot adopt their generation, and deferred STOP recovery preserves the repair obligation while permitting sleep. The power ladder still waits for pending writes, slews and recovery. Lock-channel setters neither perform PCF transfers nor reset physical-input idle time. Minimal’s message backlight can briefly defer sleep through the existing lit-screen blocker.
- **UART and stalls:** transfer begin/end still release the system lock while preserving and masking only the row IRQ, including transfer-error exits. Writer/recovery steps retain the single-transaction structure, short recovery timeout and display deferral. The stray diagnostic adds no PCF work to the asleep loop. **A zero increase in passes ≥25 ms remains a hardware gate**, including the complete reconnect write.
- **`pb_repaint`:** it reaches drawing through the same housekeeping gates as `clock_force_repaint`: display deferral, animation pause, Fn+D, staged restoration, splash and queued work. Overlays occupy the separate text band. It neither releases the wake backlight hold nor bypasses wake repaint sequencing. `draw_clock()` clears it before every return, and rendering cannot re-arm it. Ordinary three-second playback polls use glyph diffs; changed layouts use the existing relayout path.

**The 48-byte heap gap is acceptable for this exact image’s normal paths.** Allocator code is linked through libc, but I found no normal heap allocation: the allocation-capable timezone path encounters the ELF’s empty environment, and its scanning/allocation branches remain unused. `_sbrk_r` also bounds allocations through ChibiOS memcore.

The stacks are separately reserved and grow downward; the heap gap is not their headroom.

| Dev measurement | `dd8391c28d` | `7ae8575c1a` |
|---|---:|---:|
| Heap gap | 64 B | 48 B |
| Process / interrupt stack reservations | 2,048 / 1,024 B | unchanged |
| `rtc_fast_task` frame | 112 B | 144 B |
| `rtc_task` frame | 160 B | 168 B |
| `ch582_task` / `deep_sleep` frames | 320 / 192 B | unchanged |

These frame sizes are emitted-code evidence, not a runtime worst-case watermark. Record [health page 6’s stack watermarks](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/health.c:390) before and after testing; its paint method can overstate unused space.

For RTC sign-off, the overnight run must additionally establish:

- **Every reconnect actually syncs and persists:** observe successful SET, then writer idle, pending clear, STOP/maybe-stopped clear and stale clear. Allow slew completion before requiring persistence. Take final stall/UART counters afterward.
- **Wake time is correct before correction:** retain every daemon’s pre-SET measurement, including after the hour asleep. Check the roughly 50-ms phase target with measured read uncertainty and PCF drift allowance; reject unexplained second-scale jumps or accumulating error. A good post-SET reading alone proves little.
- **Legacy calibration works:** add at least a complete 300-second awake BT reference window with USB data absent. Check sensible nominal-period evolution and no correction-induced trim jump. `REF_PCF_LEGACY` being selected alone does not prove discipline ran.
- **The hour is continuously asleep:** inspect the current trace and wake ring for intermediate wakes. Capture slot-8 diagnostics immediately on any stray; require no new ≥25-ms passes, UART errors, resets, LCD faults or exhausted stack canaries.
- **Failure paths have separate coverage:** test failed re-planning, correction during an active write/slew and between release/verify, stale wake with an intervening tick, and STOP recovery giving up then successfully repairing. The healthy-bus soak cannot establish those paths.

Both artifacts’ hashes and file-backed ELF/BIN segments match. Dev token **`0x350912aa`** is confirmed in emitted code. `ds_deep_wfi` remains at **`0x2000150c`**, with SRAM instructions/literals, the 260-iteration delay and discarded flash read intact. The RTC tree matches `76af5c12`; `ak820pro-jdlien` is an ancestor, so the proposed fast-forward and pin are mechanically clean.

Read-only review; no rebuild, host-test execution or hardware run. The cited RTC `verify2` document was absent, so I checked the final fixes directly.

**Verdict: clear `7ae8575c1a` for JD’s dev flash and provisional fast-forward/pin tonight, followed by an attended smoke test and augmented overnight soak; final RTC sign-off requires the additional gates above.**