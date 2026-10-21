**No P1 or P2 found. One P3 display issue remains.**

1. **P3 — A new title can briefly show the previous track’s time and duration.**  
   [GUESS installs the new playback state](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:489), but [the setter requests repaint only when playback changes between active and inactive](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:430).

   **Failure:** a track change commits just after the clock’s second edge while playback is active. The title repaints at approximately 10 Hz, but the old position/duration can remain underneath it until the next one-second clock repaint.

   **Fix:** request a clock-band update when installing GUESS, consumed on the next housekeeping pass. The existing glyph diff/relayout can update it without requiring a whole-band clear.

The other checks pass, with these qualifications:

- **Title lifetime:** KEEP preserves the playback stamp and timeout—including an existing GUESS’s deadline—and cannot reactivate an expired readout. GUESS gets a fresh 180-second title lease and a titled 10-second playback lease. Explicit clear, empty text/icon setters, natural title expiry, and sleep/wake retire titled playback. The base also rechecks type 3’s prerequisite after its icon setter. Starts and stops request `clock_force_repaint`; title expiry does so before that housekeeping pass reaches the clock.

- **Exact comparison:** parser limits are 19/21 bytes, matching the asserted display capacities. Accepted text is exclusively `0x20–0x7e`, so setters neither truncate nor replace its characters. Both getter buffers are 22 bytes, including the terminator. Length-plus-`memcmp` handles empty lines, prefixes, and changes confined to line 1 correctly. Raw-HID sanitization is already reflected in the copied slot. Expired retained buffers cannot qualify because the lease check rejects them. This establishes equality of stored lines, not unique track identity.

- **Media-key guesses:** a shown guessed PAUSE causes OFF even if the incoming frame says PLAY; that can still produce a clock flash while awaiting type 3. A guessed PLAY over an inactive paused readout produces KEEP but leaves playback inactive. A paused type 1 switches playback off; type 3 clears playback guesses through its setter. KEEP can preserve a double-key **resumed** position guess until type 3 or the existing 15-second deadline, without extending either deadline. I found no normal path retaining an indefinite freeze or ticking after an accepted pause.

- **Raw HID:** yes, KEEP can preserve its readout where old OFF removed it. After the inclusive 10-second arbitration lease expires, an identical playing title can retain raw HID’s original 20-second readout, still `pb_titled=false`. Its remaining nominal lifetime is under 10 seconds; KEEP does not renew it. Consequently, raw text-clear can still leave that independent readout visible. This is bounded source carryover, not an extension of lock-channel playback beyond its title.

- **Guess behavior:** resuming partway knowingly reads low until type 3. Different committed titles—including line-1-only changes—restart GUESS; identical playing resends KEEP its original deadline. Rapid skips therefore follow the latest committed title. A lost type 3 ends the guess on the first playback service after 10 seconds, ordinarily allowing up to roughly another second of display latency. The [quiet timing run](/Users/jdlien/code/ak820-pro/history/lockchan-mac-gaps-20261020/run-d-450-quiet.json) committed 20/20 frames in 2.80–3.54 seconds; noisier runs committed 13/20 and 15/20. Ten seconds is a sensible provisional bound, not a delivery guarantee.

- **Cost and artifacts:** the two arrays total 44 bytes, but emitted `ch582_task` stack usage grows **272 → 320 bytes: +48 bytes**, because the handler is inlined. The ordinary caller chain reserves 400 bytes before its callees, within the separate 2,048-byte PSP reservation; this is static accounting, not a measured worst-case watermark. Dev `.data` remains **2,752 B**, `.bss` **26,864 B**, heap gap **64 B**, and MSP **1,024 B**. `ds_deep_wfi` remains at **`0x2000150c`**, with its SRAM literals, 260-iteration delay, and discarded flash read intact. Both artifacts’ hashes and file-backed ELF LOAD segments match their BINs; emitted dev token is **`0x28e58a4c`**. Daily excludes the dev-only lock-channel handler.

Read-only source/artifact review; I inspected the host test but did not rebuild, rerun it, or flash hardware.

**Verdict: clear `dd8391c28d` / `0x28e58a4c` for an attended dev bench flash, with the P3 repaint lag noted.**