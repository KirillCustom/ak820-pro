# UART2's receiver depth, measured, 10-19 22:19-22:40

`e7f6890e06` (dev, token `0xfa68ad25`, flashed 22:19), per transaction, the
slider on 2.4G, the cable's data on. The load is the 2.4G echo back to back.
The plan: [`plans/OVERRUN-CAPS-PLAN.md`](../../plans/OVERRUN-CAPS-PLAN.md).

**The sweep** (`overrun_diag.py sweep --n 500 --secs 30`, `sweep.json`,
`control.json`, `run.log`): 500 spans of interrupts masked for each length,
at random 40-80 ms gaps, no I2C.

| masked span | OEs seen with the probe | per span | other OEs | echoes lost |
|---|---|---|---|---|
| 50 us | 10 | 2.0% | 17 | 24 of 3000 |
| 100 us | 24 | 4.8% | 16 | 35 of 3000 |
| 150 us | 22 | 4.4% | 11 | 33 of 3000 |
| 200 us | 29 | 5.8% | 15 | 41 of 3000 |
| 300 us | 34 | 6.8% | 19 | 49 of 3000 |
| 500 us | 29 | 5.8% | 12 | 37 of 3000 |
| 1000 us | 39 | 7.8% | 10 | 47 of 3000 |
| 1500 us | 51 | 10.2% | 5 | 62 of 2000 |
| control (none) | 0 | - | 19 | 16 of 4000 |

**Even 50 us masked, under one byte's 87 us at 115200, overruns.** A
16-byte FIFO would make anything under ~1.4 ms harmless.

**The OEs are real losses** (`bytecount.py`, `bytecount.json`). The RX tap
counts every byte the driver reads; each echo is a 34-byte `81`.

| run | OEs | bytes read | against 34 x 1000 |
|---|---|---|---|
| no probe | 4 | 34,027 | +27 (the module's other frames) |
| 1500 us probes | 45 | 33,700 | -300 |
| no probe | 2 | 34,024 | +24 |
| 1500 us probes | 40 | 33,694 | -306 |

- **About 8 bytes lost per OE with 1.5 ms masks,** of the ~17 that arrive
  in one.
- **What that implies:** a 16-byte FIFO would lose at most one, so the
  receiver holds about 1-2 bytes. The FIFO is enabled in the driver
  (`FIFOEN`, trigger 1), and the register's trigger choices (1, 4, 8, 14)
  imply 16, but in effect there is none.
- **The cost:** UART2 must be serviced within about one byte's time, every
  time the module sends.

**What follows:**
- **The PCF's 2.6 ms mask** loses up to ~30 bytes when it meets a frame.
  Releasing per byte (0.3 ms) loses ~3 each time instead, more often.
- **Any masked stretch over ~80 us** while bytes arrive can lose one: the
  main loop's critical sections, the USB handler at UART2's own priority
  (up to 0.085 ms), and the system timer's locked part. That is the
  "nothing in progress" rate.
- **A key never goes this way** (board to module): what is lost is the
  module's frames (ACKs, `5C`, link states, the 2.4G tunnel's requests),
  which are retried or re-sent.

**The fix this points to:**
- **The PCF.** The lock exists only because the row ISR rewrites port A's
  mode register (A18/A19), as the I2C does (A14/A15). Disabling just the
  row ISR's interrupt for the transaction, instead of every interrupt,
  leaves UART2 served throughout and keeps the transaction at ~3 ms.
- **UART2's priority:** raise it above USB's.
- **The other stretches over ~80 us:** a hunt, with the masked-span
  instrumentation extended.
