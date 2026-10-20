**No P1 found for the dev bench flash.** The image is structurally sound, but the proposed sender integration needs these corrections.

1. **P2 — The extension violates the frozen type contract and loses text on older boards.**  
   [R2-9](/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:698) explicitly requires a new type. An old board rejects an entire playback-bearing type-1 frame: lengths above 42 fail immediately; shorter frames fail the icon check. Consequently, a new daemon sending playback can leave an old board showing stale text until expiry. Old-daemon/new-board operation remains compatible.  
   **Fix:** preserve type 1, allocate a playback type, and gate its use on known firmware support or explicit configuration. A new type alone does not provide fallback; unsupported boards still need legacy text frames.

2. **P2 — The proposed `observed` timestamp is not necessarily the position’s sampling time.**  
   On macOS, [`health_of`](/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/macos/media/mod.rs:571) calculates the snapshot’s position at the current wall time, but reports freshness from `v.updated`. The [agent](/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:414) derives `observed` from that freshness. If the source observation is ten seconds old, a position already extrapolated to “now” would gain those ten seconds again at pick time.  
   **Fix:** carry separate source-freshness and position-as-of timestamps. Extrapolate from the latter; retain the former for stale-source CLEAR. This is a defect in the proposed wiring, not an already implemented sender change.

3. **P2 — `Playback::of` turns unavailable timelines into invented stream timers.**  
   [`Playback::of`](/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/mod.rs:231) accepts playing snapshots with `pos=0,dur=0`. Existing [`Snapshot::playback`](/Users/jdlien/code/ak820-pro/ak820-agent/src/smtc/mod.rs:118) deliberately suppresses these: foobar2000 can report that pair when no timeline exists. The board would count upward, while later zero observations can repeatedly trigger seeks.  
   **Fix:** retain the existing duration gate until snapshots distinguish “valid elapsed time, unknown duration” from “no usable timeline.” Genuine streams can then keep `dur=0`.

4. **P2 — `is_seek` does not model the duration clamp.**  
   [`is_seek`](/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/mod.rs:272) predicts indefinitely increasing position. With a last position of 299/300, five seconds later an unchanged, still-playing snapshot clamped to 300 is classified as a seek: expected 304, difference four. This can generate repeated change/repeat traffic at track endings.  
   **Fix:** pass the timeline’s duration and clamp the expectation. Compare in a consistent position domain, including saturation if using transmitted values. Preserve the position’s **pick/sample timestamp**, not frame completion: pairing position 100 with a completion four seconds later immediately creates another false seek.

5. **P2 — Display ownership pauses lose elapsed playback time.**  
   [`display_housekeeping_task`](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:3273) returns during animation/Fn+D, while playback advances by one increment per serviced second. Spend 20 seconds in Fn+D after receiving playback, then leave: the readout resumes roughly 19 seconds behind until another frame arrives. Frequent raw-HID updates previously corrected this promptly.  
   **Fix:** advance playback from elapsed time independently of painting, preserving the duration cap and explicit pause behavior. Add an overlay-entry/exit case to display verification.

6. **P2 — An ignored media key can freeze the readout for the extended lifetime.**  
   [`display_playback_key`](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:455) toggles `pb_ticking`. If the player ignores Pause, host identity and timeline remain unchanged, so neither identity comparison nor seek detection requests correction. The timer can remain frozen until the next successful refresh—or approximately 135 seconds before expiry.  
   **Fix:** give the optimistic freeze its own short deadline and restore the extrapolated position when it expires, or arrange a reliable corrective resend for the key action. The longer playback lease should not also become the optimistic-state deadline.

7. **P3 — Pick time and anchor time differ.**  
   The [sender picks before all three preamble writes](/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/frame.rs:287); the board anchors at the third transition. [`lockchan_media_position`](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/lockchan_media.c:48) therefore omits pick-to-anchor latency. Usually this is small, but the first preamble write has no inter-write deadline, so a slow successful write can make it exceed ordinary subsecond jitter.  
   **Fix:** finalize the position at the anchor, before constructing the header/payload, or explicitly document and measure this remaining timing error.

The other requested checks passed or have these limits:

- **Parser:** lengths 2–42 without playback and 7–47 with it; 48 rejects. Truncated tails, other icon bits, invalid state, excessive lines and nonprintable text all reject before writing `*m`. The five-byte tail participates in neither line length nor printable validation. Old valid frames retain their behavior. The handler still validates and checks the lease before touching display state or expiry.
- **Position arithmetic:** milliseconds become seconds with half-up rounding; ordinary timer wrap works. The receiver’s 12-second frame lifetime keeps the `+500` operation safely away from overflow. Known duration and `0xFFFF` caps are correct. Advancing state-zero input is harmless because its display setter disables playback.
- **Rust bytes and identity:** the tail’s order, endianness, state and saturation match C exactly for a valid base payload. Keeping both icon and playback presence is correct; excluding position prevents tick-driven generations. The sender must still update its stored position/sample time when identity is unchanged, and a detected seek must invalidate both the slot epoch and policy generation.
- **Expiry/display:** every setter selects its own timeout, even when playback remains active. Raw HID restores 20 seconds; `pb_timeout` initializes to 20,000 in the ELF. Ownership repainting, expiry repainting and raw-HID key/tick behavior otherwise remain unchanged.
- **135 seconds:** reasonable for nominal bench traffic, **not a guaranteed loss bound**. Refreshes are scheduled 60 seconds after completion, so two opportunities also include frame/recovery time. Two silent losses can exhaust it; sufficiently slow attempts can exhaust the 15-second margin. The measured Mac failures were largely known deadline abandons with retries, so their percentage cannot directly predict silent-refresh loss.
- **ELF:** BIN/ELF hashes match the manifest, load segments match the BIN, and emitted token is `0xc03820fb`. `ds_deep_wfi.constprop.0` remains in copied SRAM at `0x200014f4`, with WFI at `0x200014fe`; the delay and dropped flash read remain intact. `.data` is 2,728 bytes, `.bss` 26,840, leaving **112 bytes** of heap/free gap versus 120 previously. Reserved MSP/PSP remain 1,024/2,048 bytes; that gap is not stack headroom.
- **Verification:** all eight lock-channel tests passed in the existing Rust test executable. I inspected the C suite but did not rebuild it in the read-only workspace.

**Verdict: clear `7566194642` for the attended dev bench flash; address the P2 findings before enabling the playback sender for normal use.**