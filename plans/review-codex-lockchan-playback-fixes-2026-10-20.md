**No P1 found for the attended dev bench flash. Two P2s remain; no additional P3.** Reviewed `afc771237c` and the sender at local `origin/main` (`19b9f19`).

1. **P2 — The new type-3 guard can accept an expired title, and playback can outlive that title.**

   [`lockchan_media_commit()`](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:451) reads `text_present`, but [`display_get_text_line()`](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:2404) returns the cached flag. Only `draw_text_slot()` services the 180-second expiry.

   **Failure scenario:** title A arrives at t=0; subsequent text frames are lost. Fn+D or animation suppresses text housekeeping. A type 3 arriving at t=200 is accepted and receives another 135 seconds. Leaving the overlay at t=201 paints playback, then expires the title, leaving an orphan readout until approximately t=335. Even without an overlay, transport accepted just before text expiry can remain afterward.

   **Fix:** check the text lease’s actual age at transport admission, independently of drawing. Bound lock-channel playback by the remaining text lease, or explicitly retire that source’s playback when its text expires. Preserve raw HID’s independent 20-second lease.

2. **P2 — The sender’s “current text generation was sent” check does not survive its warm-up.**

   [`sender.rs:556`](/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:556) checks eligibility before recovery/warm-up/quiet. The transport pick at [line 654](/Users/jdlien/code/ak820-pro/ak820-agent/src/lockchan/sender.rs:654) subsequently takes the newest media without repeating that check. Before picking, `Picking::abort()` ignores text-epoch changes.

   **Failure scenario:** transport is selected for already-sent A; B arrives during warm-up. The pick sends B’s transport before B’s text. Its guards and completion credit accept B’s newly captured epoch. The board accepts it against A because A is still present. The mismatch lasts through B’s text transmission, potentially longer if that transmission fails.

   **Fix:** retain the successfully sent text epoch and validate it, alongside NOW_PLAYING eligibility, at the post-quiet transport pick. Return no payload when the current text has not been sent; let the next step send text first. Cover A→B, A→CLEAR and A→B→A during warm-up.

The other requested checks passed on inspection:

- **Playback arithmetic and guesses:** unsigned subtraction handles rollover; the largest intermediate is only **4,360,502**, safely within `uint32_t`. Duration and `0xFFFF` clamps are sound. Neither guess changes the host anchor; expiry is checked before another press. The supplied test assertions detect both described mutations.
- **Former `pb_*` sites and raw HID:** normal ~3-second pushes retain the presentation, reset guesses and restore the 20-second lease. Ownership changes still force whole-band repaint; the getter remains cached; sleep clears active playback. Two small differences: an oversized position now clamps immediately, and clearing a frozen guess on expiry/wake can change the diagnostic `ticking` bit while `active` is false.
- **Icon guesses:** the change comparison occurs before replacement, so host corrections repaint correctly. An identical host icon ends the guess without unnecessary repaint; line 1 preserves it. Clear and line-0 setters cancel it. Guess expiry neither renews `text_stamp` nor resurrects `text_present`.
- **Clock painting:** `display_playback_tick()` cannot recurse into drawing. Existing forced/second-edge paths can still call `draw_clock()` twice, normally with the second diff empty. Expiry requests one ownership clear. Both restoration paths now select the owner using serviced playback state, fixing the previous stale first paint.

**The power-mode combination is sound.** The two comparison diffs match after ignoring hunk coordinates and blob IDs. Lock-channel/playback work does not change RGB ceilings, the ladder, or physical-input idle accounting. It does share Minimal’s message mechanism: an icon change or rollback bumps the text generation and can briefly light the screen, consequently delaying sleep while lit. Deep-wake recovery still discards receiver assembly, invalidates text/playback and holds the backlight for repaint. The bulb’s mode selection and repaint logic are unaffected.

The [artifact manifest](/Users/jdlien/code/ak820-pro/ak820pro-builds/out/via-dev-afc771237c-20261020-170027.json) checks out: both hashes match, all file-backed ELF load segments match the BIN, and `health_fill6` emits **`0xf18a6829`**. `ds_deep_wfi` starts at **`0x2000150c`**, with its PMU literal and wake-delay instructions in SRAM. `.data` is **2,752 bytes**, `.bss` **26,832**, and the heap interval remains **96 bytes**; MSP/PSP reservations remain **1,024/2,048 bytes**.

I inspected the host test but did not rebuild or rerun it under the read-only restriction. Hardware validation remains the planned **20 BT cycles, 10 on 2.4G, then the rig ladder check**.

**Verdict: clear `afc771237c` / token `0xf18a6829` for the attended dev bench flash; fix the two P2s before normal playback-sender use.**