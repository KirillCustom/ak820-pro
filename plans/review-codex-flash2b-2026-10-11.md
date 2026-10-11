**1. Fix BT key release before unattended jumping.** At [hid_protocol.c:825](/Users/jdlien/.claude/jobs/dbe817d8/tmp/qmk-flash2/keyboards/a_jazz/ak820pro/hid_protocol.c:825), the splash blocks processing, then `reset_keyboard()` calls `clear_keyboard()`. BT keyboard/consumer releases are merely queued; reset occurs before the CH582F TX pump runs. A held key can therefore remain down at the wireless host. Fn+Esc shares this flaw, but remote entry introduces it during ordinary typing. Add a bounded release-and-drain step before the splash/reset, aborting if delivery fails.

Otherwise:

- **Dispatch:** No unintended entry found. VIA and non-VIA require `07 13 79 BOOT`; short lengths fail before `memcmp`. VIA GET/SAVE forwarding cannot bypass the SET check.
- **Reset:** Raw HID executes on the main thread, not inside a USB ISR. The wait is legal. Watchdog shutdown remains intact; the compiled shutdown hooks add no other problematic work.
- **Host:** Both enabled backups must succeed before jumping. [flash.sh:164](/Users/jdlien/code/ak820-pro/flash.sh:164) independently requires `0C45:7140`. Reply classification can be wrong: foreign `FF 13 79` with another payload counts as “unhandled”; silence or any read exception counts as “jumped.” Neither bypasses bootloader detection.
- **Build:** `awk` uses the correct `nm` field; the archived dev ELF contains the actual volatile load and marker, daily contains neither. This artifact is non-LTO; LTO behavior wasn’t tested.

Artifact hashes match. Evidence correction: the daily’s ten changed bytes comprise **six build-token bytes and four VIA build-date EEPROM-magic bytes**, not solely the token.

No files modified or board I/O performed.

fix 1 first