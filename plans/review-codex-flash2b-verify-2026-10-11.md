**1. Finding 1 remains open: BUSY retries can bypass release delivery.** QMK caches the zero report before transmission and suppresses duplicates: [keyboard](/Users/jdlien/.claude/jobs/dbe817d8/tmp/qmk-flash2/quantum/action_util.c:310), [consumer](/Users/jdlien/.claude/jobs/dbe817d8/tmp/qmk-flash2/tmk_core/protocol/host.c:241). If that release exhausts retries, the first request returns BUSY; the next `clear_keyboard()` queues nothing, and the empty drain can succeed while the host still has a key down. Force fresh wireless keyboard/consumer zero frames on every attempt and check enqueue failure too; queue-full drops currently escape the drain’s check.

Otherwise:

- **Re-entrancy:** Safe in this call path. Raw HID runs after `keyboard_task()`; no `ch582_task()` is already active. WDT nesting restores correctly; RX state, pending actions, battery polling and profiling remain sequential.
- **Accounting:** D2 profiles individual calls correctly. Drain time bypasses `ACCT_CH582`, becoming unaccounted time; passes ≥340 ms are excluded from attribution.
- **Transport:** 2.4G uses the same wireless queue. Cable releases use USB interrupts, which continue during the splash. Wireless ACK establishes module receipt, not confirmed host receipt.
- **Host/build:** Mock reply classification and BUSY exhaustion pass. It retries 60 times, potentially longer than one minute. Artifact hashes, flavor symbols and the nine-byte daily difference check out.

No files modified or board I/O performed.

fix 1 first