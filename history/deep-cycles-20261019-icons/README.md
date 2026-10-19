# Deep-sleep soak on the icons build, 10-19 13:19-14:43

`26fcafaeca` (dev, token `0x47773c72`: the deep-sleep build `752d4bcda7`, the
USB one-liner `7df8a08e96` and the power-mode icons). `scripts/deep_cycles.py
--n 100`, the ladder's own path: Minimal ("Lights out") with a 30 s bench
sleep time, no stage forced; relay 2 off, USB data cut by `uhubctl` each
cycle; the wake rig's 40 ms F19 tap after 5-25 s asleep. `run.log`,
`deep-cycles.json`.

**97 of 100 passed. The three failures (cycles 31, 60, 83) were each one
UART overrun on the module-to-board link and nothing else.**

Every cycle, all 100:
- asleep and awake seen on the logger; woke on the rig's column (P2.8), no
  strays; exactly one key at the board, and a fresh sleep sequence number;
- no reset; no new pass of 25 ms or more; the recovery closed, no step
  timed out;
- **the PCF's slept seconds within 1 s of the logger's fall and rise
  edges** (the gate allows 2); the counted seconds 11-23% short of them (the
  slow asleep ILRC);
- the Mac's key-down: median 63.8 ms, 95th percentile 86.3, worst 114.8.

The link: 507 ACK timeouts in 838 frames sent (~60%, as the 10-19 soak and
its awake control: not the wake's); no give-up, no full queue, no replaced
frame; 3 UART overruns, no framing or parity error.

**The overruns are the link's base rate, not the sleep's** (found after the
run): the drains' health snapshots, on builds with no deep sleep, show
**92 overruns in 51.9 h awake on flash 2f** (~1.8 an hour,
`../battery-2026-10-15-drain-2f/guard-cut-20261018-020553.txt`) and **13 in
16.9 h on 2e** (~0.8 an hour). The soak's 3 in 85 min is ~2 an hour, and a
51 s cycle at that rate catches one ~3% of the time: 3 in 100 is what an
unchanged base rate predicts (the awake control's 0 in ~30 min too). Six
since this flash at 15:45, two of them with the board awake all along. The
gate ("no new transport error" per cycle) assumed a zero base rate the link
never had.

**Their likely cause (inferred, not measured):** the PCF's I2C is
bit-banged, and ChibiOS runs each whole transaction under `osalSysLock()`
(`lib/chibios/os/hal/src/hal_i2c.c:203-214`), so UART2 is not serviced for
its length; the longest since the flash was 101 ILRC cycles, ~2.9 ms
(`rtc_phase0.py status`, page 2). The UART is 16550-style; if its unstated
FIFO is 16 bytes, it holds ~1.4 ms at 115200. Awake, a host's clock sync queues a PCF time write every 5 min (two of
them fell in the Caps windows after the soak, while the module was busy); a
wake bunches PCF reads with the module's burst too. The driver's
input queue is 128 bytes (QMK's `halconf.h`), so not the cause. A lost byte
here costs a module frame (an ACK, retried; a `5C`; a link state), never a
key: every failed cycle's key reached the Mac (68, 56, 45 ms).

**The clock check (validation 1) got no data:** the script holds raw HID and
cuts USB data each cycle, so every daemon sync during the run failed. The one
after it (14:43:28) read **+288.4 ms**: 85 min of wakes followed the PCF,
whose crystal runs ~58 ppm off (0.30 s in 85 min). As designed standalone;
the per-wake offset needs its own run (validation 4's daemon-paused test).
