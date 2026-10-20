**No P1 found for an attended dev bench flash.** I reviewed firmware `81a1860c8c` against `1261ab2fa8`, and Rust `23457b9`. Firmware line references below refer to that commit; the working checkout differs.

1. **P2 — Type 3 can keep the wrong title alive indefinitely.**  
   [`display_set_text_icon`](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:2386) renews `text_stamp`, even when the icon is unchanged. Suppose A is displayed, B’s type-1 frames are silently lost, and B’s shorter type-3 frames arrive every minute. Each transport frame renews **A’s** expiry. The previous 180-second stale-title bound disappears; sending type 1 first cannot prevent this on an unacknowledged channel.

   **Fix:** let full text frames renew the text lease; leave `text_stamp` unchanged for type 3. Alternatively, transport needs a text identity that the receiver can match before renewing it. The existing periodic text policy already supplies the appropriate heartbeat.

2. **P2 — A second media-key press makes an optimistic position authoritative.**  
   The resume branch of [`display_playback_key`](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:489) overwrites `pb_base` and `pb_base_ms`, then clears `pb_held`. Starting at position 100 at t=0: an ignored key at t=1 freezes 101; another ignored key at t=11 rebases to 101. At t=20 the readout is 110 instead of 120. There is now **no optimistic deadline left to undo that offset**.

   An expired hold can also enter this branch if Fn+D/animation prevented the expiry tick before the next keypress.

   **Fix:** preserve the host’s position/time anchor through local key guesses. Keep both optimistic freeze and optimistic resume bounded by a deadline, and evaluate expiry before interpreting another keypress. This can preserve the old raw-HID pause/resume presentation while still reverting an unconfirmed guess.

3. **P2 — The sender must follow every type-1 transmission with current transport, not just text changes.**  
   [`lockchan_media_commit`](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:457) correctly switches playback off for **every** type 1. Independent policies can become differently phased after a play/pause or seek. A later unchanged text repeat or refresh then turns the timer off until the transport policy next becomes due.

   **Fix in the forthcoming sender:** every successfully sent type 1 must leave a current type 3 owed, including repeats, refreshes, wake resends and acquisition resends. Service that obligation promptly, even when transport identity is unchanged. CLEAR must cancel it. “A text change forces transport” alone is insufficient.

4. **P3 — Overlay restoration still paints the cached position before catching up.**  
   [`debug_restore_step`](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:1419) calls `draw_clock()` before housekeeping resumes. `draw_playback()` reads cached `pb_pos`; elapsed-time recomputation and expiry happen later. After 20 seconds in Fn+D, the first restored readout is therefore old and then jumps forward. After the playback lease expires under the overlay, restoration can briefly paint expired playback before switching to the clock.

   **Fix:** service playback timing/expiry before restoration chooses and paints the clock-band owner. Add an overlay-exit case covering both elapsed playback and expiry.

The Rust primitives support the proposed sender, subject to those obligations. The duration gate, six-byte encoding, split identities and duration-clamped seek expectation are correct. The remaining integration rules are:

- **Carry the position’s actual as-of instant.** macOS extrapolates when producing the returned snapshot; Windows returns a cached snapshot. Neither a universal `observed` nor a universal “time copied into the slot” works for both. Update the position/time pair on unchanged identities too.
- **Compare positions at the same instant and in the same numeric domain.** Keeping unsaturated host positions and duration for seek detection, with saturation only during encoding, works. Record the picked position with its pick instant on a current successful completion; completion time controls scheduling, not position extrapolation.
- **Keep identity separate from current payload.** An unchanged text identity must still update the icon in future type-1 payloads. Text/transport changes and seeks must invalidate the appropriate pending/in-flight work; ordinary position ticks must not. Startup, acquisition and wake need full state. Staleness/CLEAR must cancel both policies’ obsolete work, with no continuing idle transport refresh.

The remaining display checks passed with these limits:

- `pb_at` uses correct unsigned wrap arithmetic. Even the largest unsigned elapsed value divided by 1,000 plus a `uint16_t` base fits `uint32_t`; duration and `0xFFFF` clamps are sound.
- A single ignored key now recovers the host timeline after the hold expires. Raw HID restores the 20-second lease, and every playback setter clears the hold. Sleep clears `pb_active`, preventing old playback from reviving; subsequent setters replace the anchors.
- Ownership changes and expiry still request a whole-band repaint. `display_get_playback` remains a cached view, reporting ticking false during a hold; it does not independently service elapsed time.
- Apart from the lease issue above, the icon setter follows the intended rules: no resurrection when `text_present` is false, unchanged icons avoid dirty/generation churn, and changed icons recompute presence and bump `band_gen`.

The build checks match the [manifest](/Users/jdlien/code/ak820-pro/ak820pro-builds/out/via-dev-81a1860c8c-20261020-164147.json): BIN/ELF hashes, load segments and emitted token `0x4602647e`. `.data` is **2,728 bytes**, `.bss` **26,856**, and the heap/free interval is **96 bytes**. Reserved MSP/PSP remain **1,024/2,048 bytes**; the rewrite consumed 16 bytes of free space without shrinking those reservations.

All eight existing Rust lock-channel unit tests passed. I inspected the C parser tests but did not rebuild them under the read-only restriction; they do not exercise the display state machine. Pick-to-anchor error remains explicitly documented, and 135 seconds remains a nominal loss margin.

**Verdict: clear `81a1860c8c` for the attended dev bench flash; fix the P2 items before enabling the playback sender for normal use.**