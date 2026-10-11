**Finding 1 remains open, partially fixed.** Fresh frames defeat QMK duplicate suppression, but [the drop-counter snapshot](/Users/jdlien/.claude/jobs/dbe817d8/tmp/qmk-flash2/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:640) occurs **after** release enqueueing. A full ring without a replaceable matching frame drops the release; an ensuing ACK-only drain still passes. Snapshot before enqueueing, or return and check enqueue success.

Otherwise:

- **Coalescing:** Safe when a matching queued frame exists: it replaces the last matching frame, excludes the in-flight head, and preserves the final released state.
- **2.4G / USB:** Both wireless modes use A1/A3. `usb_mode` tracks slider/profile selection, not cable presence; receiving this command over USB does not suppress wireless releases.
- **Veneers:** Swap is benign. All five calls retarget correctly, retaining the same Thumb destinations. Both helpers run before flash programming/erase starts; the busy wait remains in RAM. Confirmed exactly 20 changed bytes and no other moved symbols.
- **Host/build:** Hashes match. The monotonic deadline prevents new attempts after 60 seconds; an outstanding request and sleep can finish later.

No files modified or board I/O performed.

fix 1 first