**No P1 found. Two P2 title-handling gaps remain; the diagnostic is safe to bench but has a P3 interpretation gap.**

1. **P2 — Empty text setters still bypass playback retirement.**  
   [`display_set_text_line()`](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:2326) can make `text_present` false without calling `playback_title_gone()`. `display_set_text()` reaches this path too.

   **Failure:** type 1 supplies a title, type 3 starts playback, then raw HID sends `TEXT_SET` with icon NONE and empty text. The title disappears, but its readout can retain almost its entire 135-second lease. The expiry hook cannot catch this because it requires `text_present`.

   **Fix:** retire titled playback whenever a text setter leaves the slot absent. Keep the `pb_titled` guard so raw HID playback retains its independent lease.

2. **P2 — Type 3 can remove its own prerequisite, then activate playback.**  
   [`lockchan_media_commit()`](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:453) checks the title lease **before** changing its icon. [`display_set_text_icon()`](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:2401) can make the slot absent.

   **Failure:** a parser-valid type 1 has empty lines and a PLAY icon; a subsequent parser-valid type 3 carries icon NONE and state 1. Admission succeeds, the icon update removes the slot, and `display_set_playback_for()` starts a titleless readout.

   **Fix:** recheck the live slot after applying the icon, and prevent titled playback activation without it. Calling the removal hook in the icon setter alone is insufficient: the following playback setter would reactivate it.

3. **P3 — The first-stray snapshot cannot fully identify an outside-mask source or establish noise.**  
   [`stray_note()`](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/sleep.c:373) captures useful evidence, particularly PendSV through `ICSR`, but omits GPIO enables and terminal pending state.

   **Failure:** unrelated outside-mask `RIS` flags or a disabled UART’s `ISPR` bit accompany the stray and get mistaken for its cause. With GPIO `IE` restricted to wake pins, an outside-mask raw flag cannot assert the port IRQ. Also, the first unexplained return need not represent a later storm.

   **Fix within 28 bytes:** pack P0/P1/P2 RIS into 3/3/2 bytes, preserving all implemented bits. Use their spare bits for per-port unexpected-`IE` and `(RIS & IE & ~wake_mask) != 0` flags, plus an NVIC-enable mismatch flag. The four freed bytes can hold an exit pending/exception summary, sampled before teardown clears anything. Retain sequence, first second, count, first ISPR and first ICSR. Interpret ISPR against the enabled mask, **`0xE0800000`**; P3 is disabled, so its omission is reasonable. An empty snapshot means “unexplained return,” not proven electrical noise.

The asleep-loop safety checks pass:

- Both ELFs retain the SRAM workaround. Dev `ds_deep_wfi` is at **`0x2000150c`**; its instructions and literals remain inside the startup-copied `.data`. The 260-iteration delay and discarded flash read precede every return to flash. This preserves the documented workaround, whose physical minimum delay remains unmeasured.
- The diagnostic executes afterward at IHRC speed with the existing slow-flash setting and PRIMASK held. Its emitted code uses aligned SRAM stores and MMIO reads; it introduces no allocation, lock/unlock, timer call or peripheral write.
- GPIO RIS, NVIC ISPR and SCB ICSR reads have **no read-clear side effects**. GPIO acknowledgement uses IC; NVIC clearing uses ICPR; ICSR clearing requires writes.
- Wake classification, second accounting, watchdog feeding and the 1,000-stray cutoff remain unchanged. Added work is bounded: a few microseconds per repeated stray versus the existing roughly 108 µs settle. It can shift an RTC-boundary race slightly; “zero timing perturbation” would overstate the result.

The specifically patched display paths pass: explicit raw-HID `TEXT_CLEAR`, type 2, natural text expiry and sleep wake retire titled playback. Type 1 turns playback off. Normal expiry sets `clock_force_repaint` before the same housekeeping pass reaches `draw_clock()`. Raw-HID playback still clears `pb_titled`, uses **20,000 ms**, and survives text clear/expiry independently. Playback’s own expiry leaving `pb_titled` set is harmless because playback is already inactive.

Both manifests’ hashes match, every file-backed ELF LOAD matches its BIN, and the dev token is **`0x88d55921`**. Dev RAM is `.data` **2,752 B**, `.bss` **26,864 B**, heap gap **64 B**. MSP/PSP remain **1,024/2,048 B**; `deep_sleep()`’s frame grew **176 → 192 B**. The heap gap is not stack headroom, and allocation remains bounds-checked. The daily artifact excludes the stray diagnostic.

Read-only source and artifact verification; no rebuild, execution or flash.

**Verdict: clear `a5a176bef4` / `0x88d55921` for an attended dev bench flash; the blanket title-lifetime claim remains incomplete.**