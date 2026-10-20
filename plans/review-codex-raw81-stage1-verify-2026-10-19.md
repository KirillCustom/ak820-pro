**Nothing found blocks flashing `7387d189` for the stage-1 echo measurements.** All four original findings are closed; no new must-fix or should-fix.

1. **Window history — closed.** `b2/b1/b0` are cleared before collection admission, including tainted admission, and when quarantine ends. The phantom ACK/`5x` path is removed. [ch582f_ajazz.c:1521](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:1521), [1484](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:1484).

2. **Buffered-stream taint — closed.** Drain-start errors latch even while idle; a later `81` enters quarantine instead of collection. The flag clears only at silence or reset. [ch582f_ajazz.c:1443](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:1443), [1525](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:1525), [1487](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:1487), [694](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:694).

3. **Lifetime — closed.** Commit now rejects elapsed lifetime ≥15 ms before accepting the frame. [ch582f_ajazz.c:714](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:714).

4. **Link-change cleanup — closed.** Every macro invocation drops the pending echo and quarantines an active collection, preserving tail isolation. [ch582f_ajazz.c:421](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:421), [701](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:701).

Checked all ten macro sites in that file: **900, 984, 1051, 1133, 1184, 1210, 1602, 1612, 1628, 1645**. All accept the `do/while` statement, including both unbraced `if`s. Quarantine survives repeated calls; collection/quarantine bypass the short-frame handlers, so their final boundary assignment cannot undo it. Wake subsequently performs its resync reset.

**New note:** the commit-time UART poll aborts without literally setting `r81_tainted`. This is behaviorally safe: quarantine prevents further admission until silence/reset, but “set on any UART error” is not strictly true. [ch582f_ajazz.c:710](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:710).

Exact commit contents compared; nothing modified, built, or tested. The accepted `sdWrite` and measurement limits stand.