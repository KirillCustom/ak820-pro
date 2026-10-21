- **P1 — None found before the attended dev bench flash.**

- **P2 — Both title fixes are closed.** The [text setter](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:2327) and [icon setter](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:2403) retire titled playback when they empty the slot; independent raw-HID playback remains protected by `pb_titled`. [Type 3](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:459) rechecks the lease after changing the icon and rejects before activating playback.

- **P3 — Small reader fallback defect.** [`stray_diag()`](/Users/jdlien/code/ak820-pro/scripts/deep_cycles.py:75) promises `None` for unsupported firmware, but [`cm.xfer()`](/Users/jdlien/code/ak820-pro/scripts/current_map.py:119) exits on an unhandled response or timeout. Handle unsupported replies explicitly or correct that promise. The previous build’s slot-8 layout also cannot be distinguished by this reader; use it with the verified build token.

The revised diagnostic otherwise passes. Its packed layout, `<HHIIIHHII>`, matches all reader offsets and flag bits; a synthetic payload decoded correctly. `same_s` includes the first stray, and `last_n` includes the latest. Flags describe the **first** stray; latest pending state describes the **latest stray**, not necessarily sleep exit. Interpret ISPR against `0xE0800000` when the enable-mismatch flag is clear; an empty snapshot still does not establish noise.

The emitted `stray_note` code is inlined, uses aligned SRAM stores, and only **reads** peripheral/core registers. It adds no register clearing, calls, allocation, locking, or interrupt enabling. It runs after the SRAM workaround with PRIMASK held. Nominal diagnostic cost at 12 MHz is approximately **9.2 µs first stray**, **4.4 µs subsequent strays in that second**, and **3.3 µs in later seconds**, excluding bus/fetch waits and shared loop bookkeeping. These are instruction estimates, not measured timings.

In the dev ELF, `ds_deep_wfi` remains at **`0x2000150c`**, wholly inside startup-copied `.data`, including its literals. The **260-iteration delay and discarded flash read** remain before returning to flash. RAM is unchanged: `.data` **2,752 B**, `.bss` **26,864 B**, heap gap **64 B**, MSP/PSP **1,024/2,048 B**, and `deep_sleep()` frame **192 B**. The heap gap is not stack headroom.

Both artifact hashes and file-backed ELF LOAD/BIN comparisons pass. Dev token **`0xa647bcb6`** is confirmed in emitted code; daily excludes the diagnostic. Read-only verification; no rebuild or hardware run.

**Verdict: clear `6fda6cd1c2` / `0xa647bcb6` for the attended dev bench flash.**