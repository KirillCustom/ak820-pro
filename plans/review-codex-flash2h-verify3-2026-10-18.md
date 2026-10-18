**Both requested fixes are correct. No new regression found, but one pre-existing cross-link path remains.**

- **Must-fix — for strict cross-link isolation:** [`5B 32`](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:1242) still preserves the decoder candidate. If an autonomous reconnect’s down notification is lost, with no local select/pair, `is_module_connected` stays true. A valid old-link payload awaiting its trailer can then commit from subsequent LED transitions after reconnect, provided the decoder’s timing limits hold. Resetting reception on `5B 32` closes this observable boundary. This gap predates this commit; completely unreported transitions remain inherently undetectable.

- **Note — TX fix complete:** The check runs immediately before **both** initial and retransmission [`sdWrite` calls](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:697). [Packing](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:823) confirms `data[0] = cmd`, `data[1] = first parameter`, and `len = param_len + 2`. Thus `len >= 3` is correct, and **battery polls `A6 53` never trigger this reset**, including retries.

- **Note — context and effects safe:** The pump runs through `ch582_task`, in main-loop context, including the raw-HID drain path. The added reset only aborts reception and invalidates its baseline; it does not change routing, connection state, TX ordering, or retry budgets.

- **Note — promotion correct, with one explicit behavioral extension:** [Promotion](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:1233) now exactly mirrors `5B 32`: it clears `PA_SELECT`, preserves `PA_BOUNCE`, and **also clears `PA_PAIR`** if pending. That last effect goes beyond retiring a stale select, but correctly follows the existing connected-state policy.

**Should-fix:** none additional in this diff.

Read-only source review; no files changed, build, or flash.