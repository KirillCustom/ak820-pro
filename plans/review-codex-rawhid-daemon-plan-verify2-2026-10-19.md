**Not ready to build.** Finding 1 is closed at the design level, subject to the accepted boundary residual. Finding 3 remains must-fix; finding 7 is partly closed.

1. **Must-fix — 3 / R3-2: quiet time does not establish the proposed session reset.**

   - **Same ID:** the board checks freshness against `last_seq` regardless of idle time. A process that dies mid-session, restarts, waits a second and randomly chooses the same ID can receive `STALE`; waiting longer cannot fix that. Recovery must explicitly choose a different ID from the rejected one, or change the receiver’s session rules.
   - **Different ID:** an old request sent just before the quiet period can arrive near its end. The new request then encounters `BUSY`, because the board measures one second from **admission**, not host transmission. R3-3’s “opened under a second ago” grace is already expired after the mandatory one-second wait, unless “opened” is explicitly defined differently. Use an explicit initial-admission grace state. [Admission rules](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/raw81.c:157)
   - **Old responses:** the stated lifetime bounds **request delivery**, not the complete request/reply/receipt lifetime. The board can retain a response awaiting keyboard-TX idleness, without an expiry. After identity reuse, an old matching reply/receipt can arrive after the host’s empty drain and satisfy a new transaction. Thus “no packet … can then arrive” is not established. [Lifetime assumption](C:/Users/jdlien/code/ak820-pro/plans/RAWHID-24G-PLAN.md:592), [response scheduling](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:1075)

   Persistent allocation can be dropped **if** the replacement bounds all outstanding traffic through final response delivery and specifies admission recovery. The machine-wide byte-zero lock closes the previous cross-user/offset problem in principle; hold it throughout quiet time and all outstanding I/O, with every participating sender using it.

2. **Finding 1 / R3-1 — Closed in principle; the encoding argument works.**

   For 25 arbitrary bytes, payload constraints exclude at most 100 masks; excluding the four mask-byte values makes **104**, leaving **at least 152** choices. Zero-pad **before** masking; each encoded padding byte then equals the safe mask. This covers arbitrary printable text and arbitrary playback position/duration bytes.

   Restoring `07` is sound: every allowed command requires it, and the remaining channel/op/page restrictions still apply. CRC verification must cover the transmitted masked bytes before decoding, as the board now does. Host encoders must reject an input whose leading byte is not `07`. [Decoder and allow-list](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/raw81.c:172)

   Header/CRC/sum safety requires checking the **whole candidate frame**. Success is not guaranteed within an arbitrary bounded sequence search: 64 candidates starting at `0x5A00` all fail on the high sequence byte alone. R3-3’s exhaustion/rotation rule closes that should-fix. The 16,384 limit counts numerical positions consumed—including skips and retries—not successful sends; searches must not cross that limit or wrap to reuse identities.

3. **Should-fix — The residual is almost complete, but its elimination claim is wrong.**

   For an **intact, contiguous frame**, the listed effects cover the window: at most one valid boundary match, up to two malformed counts, and no forged inbound ACK. Malformed counts do **not** require the exact valid checksum.

   However, putting the current collector in every build does **not** eliminate this boundary residual: it deliberately lets pending `5C 25 | 81` reach the window when no boundary/silence has been established. Correct that sentence and retain the intact/contiguous qualification. [Collector boundary rule](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/raw81.c:97)

4. **Must-fix — 7 / R3-3: partly closed.**

   Injecting time into the production drain and making the fake honor timeouts closes the timing-seam issue. Add explicit cases for the session/late-response failures above, full-v2 encoding/decoding properties across allowed commands, and search/span boundaries. Arbitrary-window tests must assert the **permitted residual**, not universally “no window action.”

5. **Other should-fixes — Closed at plan level:** drain/write budgets and read-deadline placement; the private qualification-health exception; silence backoff (8); full refresh on session recovery/rotation (9). R3-3’s BUSY grace remains open under item 1 above.

Repository-only static inspection; no modifications, builds, tests, or repository programs run.