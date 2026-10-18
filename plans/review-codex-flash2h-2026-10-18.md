**I found one must-fix integration defect: a link drop can be missed by the receiver.** The core decoder’s packing, arithmetic, buffer bounds, CRC checks, and trailer handling look sound.

I made no changes and did not run the build or host suite, which write files. I inspected the source and archived ELFs, verified artifact hashes and structural checks, and independently cross-checked the encoder’s CRC-16 against `binascii.crc_hqx`.

1. **Must-fix — invalidate the receiver when the link changes, before processing subsequent bytes.**

   `lockchan_poll(..., is_module_connected)` runs only after the UART drain. That drain can process a down indication, an up indication, and more LED reports before polling. The receiver therefore never sees the disconnect.

   Concrete example: receive a valid frame through its CRC, leaving `ST_TRL`; then drain `5B 33`, `5B 32`, and the two expected trailer reports together. The old frame commits despite the intervening disconnect. No CRC collision is necessary.

   Reset immediately in the link-down paths, and at explicit slot-selection/pairing boundaries. Merely remembering a disconnect and resetting at the end would still allow a commit earlier in that drain. The existing host test explicitly calls `poll(linked=False)`, so it cannot detect this integration defect. [ch582f_ajazz.c:1192](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:1192), [ch582f_ajazz.c:1221](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:1221), [ch582f_ajazz.c:1243](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:1243), [ch582f_ajazz.c:1363](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:1363), [lockchan_test.py:288](/Users/jdlien/code/ak820-pro/scripts/lockchan_test.py:288).

2. **Should-fix — the Scroll-key shortcut bypasses the new hold.**

   `update_leds()` postpones ordinary settling while busy, but `led_update_kb()` can still assign `scr_shown` immediately through the armed local-key exception.

   For example, press `KC_SCRL` without receiving an immediate echo, then begin P0 within 500 ms. Its first preamble transition changes Scroll and can be mistaken for that echo. The displayed state changes during acquisition and is then held at the changed value.

   Either suppress that shortcut during acquisition/frames or explicitly cancel channel reception when prioritizing a local Scroll press. The new hold alone does not provide the stated guarantee. [indicators.c:185](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/indicators.c:185), [indicators.c:308](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/indicators.c:308).

3. **Should-fix — establish a baseline before the board’s first test frame.**

   `encode()` returns the three preamble transitions onward; it does not emit the starting baseline. `frames_test()` sends that result directly. A fresh or disconnected receiver has `have_prev == false`, so its first report merely establishes `prev`: the first preamble edge disappears, and that frame cannot synchronize. A stale nonzero baseline can similarly lose the first frame.

   Establish the intended baseline, ensure it reaches the receiver, then allow the quiet interval. If repeated reports can be suppressed, use a warm-up change and return to baseline. The host tests already provide an explicit baseline, concealing this board-runner assumption. [lockchan.py:85](/Users/jdlien/code/ak820-pro/scripts/lockchan.py:85), [lockchan.c:160](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/lockchan.c:160), [ledchannel_test.py:176](/Users/jdlien/code/ak820-pro/scripts/ledchannel_test.py:176), [lockchan_test.py:123](/Users/jdlien/code/ak820-pro/scripts/lockchan_test.py:123).

4. **Should-fix — make the board test’s result enforce its claims.**

   `frames_test()` returns normally even if all 18 frames fail. Its “committed exactly” check omits `type`, and it neither requires idle state afterward nor fails on added ≥25-ms stalls. Health snapshots are read but omitted from `frames.json`.

   Check all frame fields, require the expected commit-count change, retain health snapshots/deltas, and return failure for failed assertions. Separate “frame decoded correctly” from “performance budget passed.” Also, the frames have at least a 600-ms pause plus USB reads and app startup between them; “back to back” describes writes within each frame, not closely spaced frames. [ledchannel_test.py:173](/Users/jdlien/code/ak820-pro/scripts/ledchannel_test.py:173), [ledchannel_test.py:183](/Users/jdlien/code/ak820-pro/scripts/ledchannel_test.py:183), [ledchannel_test.py:193](/Users/jdlien/code/ak820-pro/scripts/ledchannel_test.py:193).

**Note — the codec implementation is otherwise consistent with version 0.**

- Profile masks, subset ordering, MSB-first blocks, partial-block lengths, header restrictions, and CRC coverage match the specification and encoder. All 16 type values are currently legitimate because type semantics are deferred. [lockchan.c:27](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/lockchan.c:27), [lockchan.c:105](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/lockchan.c:105), [LOCK-LED-CHANNEL-PLAN.md:165](/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:165).
- Accumulation cannot overflow: maximum block values are `3^12−1 = 531440`, `7^5−1 = 16806`, and **`15^8−1 = 2562890624 < UINT32_MAX`**. Range rejection occurs before writing decoded bits. Header writes stay within four bytes; body writes within 50; CRC indices are at most 48 and 49. Shifts stay below 32. [lockchan.c:89](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/lockchan.c:89), [lockchan.c:120](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/lockchan.c:120).
- The trailer’s alternate intermediate value guarantees two actual transitions. Repeats cannot advance it, and commit requires both expected states. [lockchan.c:128](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/lockchan.c:128), [lockchan.c:217](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/lockchan.c:217).
- State counters are bounded. Abort counters saturate; frame and preamble totals wrap as unsigned statistics. Clearing statistics intentionally preserves an active candidate, so a subsequent commit can produce `frames == 1, preambles == 0`. Payload bytes beyond `last.len` can retain older contents; the host reader correctly truncates them. [lockchan.c:67](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/lockchan.c:67), [lockchan.c:133](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/lockchan.c:133), [lockchan.c:251](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/lockchan.c:251).

**Note — timing and single-candidate liveness are sound.** Quiet accepts exactly 100 ms; gaps accept 150 ms and reject greater values; lifetime accepts 12,000 ms and rejects older candidates. Unsigned subtraction handles ordinary 32-bit timer wrap correctly. Repeats and unused-bit changes do not refresh an active section’s transition deadline; polling expires a stalled candidate even without further reports. [lockchan.c:148](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/lockchan.c:148), [lockchan.c:193](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/lockchan.c:193), [lockchan.c:228](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/lockchan.c:228).

A single abandoned frame therefore cannot hold Scroll forever while polling continues. **Continuous traffic can suppress settling indefinitely**, because successive candidates/frames can keep restarting the settle timer without a 1.5-second settling window. After a failed P0/P2 frame, the last raw Scroll value can instead become visible once settling resumes; holding the display does not restore the channel’s abandoned LED state.

The hold also applies irrespective of the active host. `ch582_cancel_connect()` sets USB mode without clearing `is_module_connected`, so continuing radio reception can hold the USB host’s Scroll indication. Qualifying the hold by active wireless mode would avoid that cross-host effect. [ch582f_ajazz.c:907](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:907).

Caps handling is sensible but slightly narrower than the specification’s wording: a Caps change discards partial preamble acquisition without incrementing the Caps-abort counter; after synchronization it increments that counter. A Caps change on the first acquisition edge is accepted. State explicitly that “any Caps change aborts” applies to an already-started candidate. Normal Caps indication remains on the existing LED path. [lockchan.c:168](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/lockchan.c:168).

**Note — execution context, HID bounds, and build guards are fine.** Bluetooth parsing and raw-HID handling execute sequentially in the main loop. Receiver state needs no ISR synchronization. [main.c:47](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/quantum/main.c:47), [keyboard.c:792](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/quantum/keyboard.c:792), [usb_main.c:517](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/tmk_core/protocol/chibios/usb_main.c:517).

The enclosing five-byte guard protects the operation and page reads; operations 10–12 additionally require 32 bytes:

| Operation | Reply bounds |
|---|---|
| 10 | Fields occupy bytes 4–28; 29–31 are zero |
| 11 | Nine `uint16_t` counters occupy 4–21; remainder zero |
| 12 | Page 0 copies 26 bytes; page 1 copies 22; page number echoed; invalid pages rejected |

`memcpy` avoids unaligned integer accesses and produces the intended little-endian representation on this target. The host’s before/after frame-count check protects the last-frame payload snapshot, assuming no concurrent reset; it does not make busy/preamble/abort statistics one atomic snapshot. [hid_protocol.c:872](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/hid_protocol.c:872), [devhook.py:100](/Users/jdlien/code/ak820-pro/scripts/devhook.py:100).

The guarded header include and non-dev `static inline false` fallback are correct. The board directory is an include path. Daily builds instantiate no receiver and expose no new dev operations; unused decoder code is eligible for section garbage collection. [ch582f_ajazz.h:68](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.h:68), [build_keyboard.mk:521](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/builddefs/build_keyboard.mk:521), [platform.mk:362](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/platforms/chibios/platform.mk:362).

The clean archived 2h dev ELF contains a **180-byte receiver**; total `.bss` increased **184 bytes**, including alignment, leaving **2,376 bytes of linker heap**. The available daily archive contains neither receiver nor dev/bootloader markers and passes the inspected structural checks. Its manifest identifies a dirty parent checkout, however, so this is not certification of a fresh daily build at exactly `3dcbc8b34c`. [dev manifest:3](/Users/jdlien/code/ak820-pro/ak820pro-builds/out/via-dev-3dcbc8b34c-20261018-111611.json:3), [daily manifest:5](/Users/jdlien/code/ak820-pro/ak820pro-builds/out/via-daily-4aa5e6b443-dirty-20261018-111445.json:5).

**Note — decoder cost is bounded and looks reasonable, but does not prove the whole-loop budget.** Ordinary symbols perform small fixed work; completing a block emits at most 31 bits. `digits_for` runs once per partial section block—potentially once for the header and once for the body—and performs at most 12 iterations. The archived ARM code calls `__aeabi_lmul`; there is no division. Using `uint32_t` would suffice for these profiles.

The larger exceptional step is CRC-16 over at most **52 bytes / 416 bit iterations**, computed together at payload completion. That is more than the header’s “few dozen operations” description, but still bounded. Polling is constant work. There are no added waits, allocation, flash writes, or drawing calls. The existing 64-byte UART cap bounds report callbacks per pass. Actual ≥25-ms behavior still needs measurement with display and input load, including the Scroll-shortcut case above. [lockchan.c:34](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/lockchan.c:34), [lockchan.c:122](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/lockchan.c:122), [ch582f_ajazz.c:1146](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:1146).

**Should-fix — strengthen the negative tests before treating their counts as reliability evidence.** The round trips meaningfully check content, anchor time, early commit, count, and idle completion. The fault tests are useful, but:

- Insertions/substitutions randomize Caps, often causing immediate rejection before exercising CRC or trailer defenses.
- Deletions are retimed into a fresh regular timeline, removing the longer arrival gaps that actual losses create.
- The fault oracle checks the final committed frame after the stream, rather than checking every commit immediately; its saved `before` count is unused.
- “Human presses” are all at least 200 ms apart, already exceeding the 150-ms acquisition gap.
- Encoder and decoder share the same profile constants and algorithm choices; agreement alone cannot establish the wire contract.

[lockchan_test.py:116](/Users/jdlien/code/ak820-pro/scripts/lockchan_test.py:116), [lockchan_test.py:172](/Users/jdlien/code/ak820-pro/scripts/lockchan_test.py:172), [lockchan_test.py:192](/Users/jdlien/code/ak820-pro/scripts/lockchan_test.py:192), [lockchan_test.py:248](/Users/jdlien/code/ak820-pro/scripts/lockchan_test.py:248).

The highest-value additions are:

- Parser-level down/up/trailer batches and slot changes in every phase.
- Fixed independent wire vectors; valid-CRC headers with forbidden version/profile/length; explicit full and partial block range boundaries.
- Caps-preserving corruption, longer deletion bursts, OS baseline reassertions, and faults at every trailer boundary.
- Exact 99/100, 150/151, and 12000/12001-ms boundaries, including timeout paths across wrap.
- Repeats and unused-only reports during stalls; reset/stat-clear during every phase; truncation at every write boundary.

The existing wrap test covers a successful crossing, not these timeout boundaries. CRC-16 also permits collisions by design: “no wrong commit in these samples” is justified; “faults can never commit wrongly” is not.

The wire format itself remains workable. Its main exposed omissions are the explicit baseline-establishment requirement and precise acquisition/Caps/timing wording. The Scroll hold is an acceptable experimental policy with the limits above; it is not restoration after a failed sender.