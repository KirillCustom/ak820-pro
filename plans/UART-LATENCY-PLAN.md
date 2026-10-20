# UART2's one-byte receiver: keeping its latency under a byte

Status: **revision 2 (10-19), after codex's plan review** ([`review-codex-uart-latency-plan-2026-10-19.md`](review-codex-uart-latency-plan-2026-10-19.md), dispositions at the end). Nothing built. Follows
[`OVERRUN-CAPS-PLAN.md`](OVERRUN-CAPS-PLAN.md), whose measurements found the
problem (`history/overrun-ab-20261019/`, `history/overrun-depth-20261019/`).
Owner: the Mac session.

## What is known (measured 10-19)

- **UART2's receiver holds about 1-2 bytes,** whatever `FIFOCTRL` says:
  - masked spans as short as 50 us overrun under the 2.4G echo's load (2% of
    spans, rising to 10% at 1.5 ms);
  - every OE is a real loss: the RX tap's byte count falls ~8 bytes short
    per OE with 1.5 ms masks.
- **The deadline:** at 115200 a byte lasts 87 us, so UART2 must be served
  within about that, every time the module sends: a 34-byte `81` runs ~3 ms
  back to back.
- **The PCF's transaction holds the system lock throughout:**
  - **the length:** 2.63 ms for the calendar read, 2.20 for the time write;
  - **the cost:** ~9% of calendar reads met a frame under the echo's load,
    losing up to ~30 bytes each.
- **The per-byte release is no fix,** and is now dev-only and off: its
  0.3 ms stretches still each lose a few bytes, and the read grows to
  5.4 ms.
- **OEs with nothing in progress continue:** ~0.2 a second under the echo's
  load. The USB handler runs up to 0.048 ms (0.085 at enumeration) at
  UART2's own priority, 1, and the system timer 0.021 ms at priority 0.
  Anything else masked over ~80 us is unmeasured.
- **What a loss costs:** the module's frames only (ACKs, which are retried;
  `5C`; link states; the 2.4G tunnel's requests, which the host retries).
  Keys go board to module, but a lost ACK stalls the TX queue: finite
  retries, a near-full queue's replacement that can merge transitions, and
  queue-full drops can each cost a key (codex), so the typing tests stay.

## Why the PCF holds the lock at all

**Only to keep its pin-mode changes atomic.**
- **The I2C's writes:** the fallback LLD emulates open drain by switching
  SDA between input and output (`SW_I2C_USE_OPENDRAIN FALSE`). Each switch is
  a read-modify-write of port A's `MODE` and `CFG`
  (`hal_pal_lld.c:202-250`). Its level changes are `BSET`/`BCLR`, which are
  atomic (`hal_pal_lld.h:411-424`).
- **The only other writer:** the row ISR (PWMD1's periodic callback,
  `drivers/led/sn32f2xx.c`) writes the matrix's row pins' modes (A18 and
  A19, `keyboard.json:144`; with `MATRIX_UNSELECT_DRIVE_HIGH` both select
  and deselect set output, so their modes rarely change, but the writes are
  RMWs of the same `MODE` register; their `CFG` fields are in `CFG1`, A14/A15's
  in `CFG`), on the same port as SCL and
  SDA (A14, A15).
- **Nothing else changes pin modes from an interrupt:** the indicator tick
  and the LCD only write levels, and their mode changes are at init in the
  main loop.

## The fixes

### 1. The PCF's transaction unlocked, with only the row ISR held off (every build)

**The change:** in the fallback LLD's two public transfers (chibios-contrib),
two board hooks that default to nothing:

```c
msg_t i2c_lld_master_transmit_timeout(...) {
  SW_I2C_MASK_BEGIN();
  SW_I2C_XFER_BEGIN();     /* board: hold the row ISR's IRQ, then osalSysUnlock() */
  msg = master_transmit(...);
  SW_I2C_XFER_END();       /* board: osalSysLock(), then release the IRQ as it was */
  SW_I2C_MASK_END();
  return msg;
}
```

**What the board's hooks do** (rtc.c or a small `i2c_unlock.c`):
- **The IRQ hold** is the row timer's line in the NVIC (PWMD1 is CT16B1,
  `CT16B1_IRQn` = 16; `NVIC->ICER`, with
  `ISER` put back as found), the same register writes the deep sleep uses.
  So a disabled IRQ stays disabled, and a pending row interrupt fires once
  at the release, late, as it does today under the global mask.
- **The lock round trip:** the unlock and relock happen inside hal_i2c's
  own lock, so `i2cMasterTransmitTimeout`'s state machine is unchanged.

**What it changes:**
- UART2, USB, the system timer and the indicator tick all run during the
  transaction.
- The row ISR waits ~3 ms, as it does today: the same LED-row hold and the
  same matrix-scan pause.
- The transaction's wall time grows only by those short handlers, unlike
  the per-byte release, which let the row ISR's 188-261 us run at every
  boundary.

**What must hold** (codex, please check each):
1. **No other mode writer runs in an interrupt:** none on port A, and no
   other port's `MODE`/`CFG` RMW that the I2C's `palSetLineMode` could race.
2. **The bus guard is enough for the flash's SPI1 pins:** A12/A13, on the
   same port (`ak820pro.c:607`'s note). No DMA starts from an interrupt
   while the transaction runs, since every PCF path admits only with no blit
   in flight.
3. **The deadline and the phase write:** the timeout checks (system time)
   and the phase write's timing (`PS_RELEASE_WRITE`, the bracket) are
   unaffected beyond the short handlers.
4. **Every caller holds the lock** (hal_i2c takes it) and none runs in an
   ISR. Codex verified this earlier for the per-byte release.
5. **Unlocking inside hal_i2c's lock is legal here,** as the per-byte
   release was: one thread, `chSysUnlock()` does not reschedule.
6. **The deep sleep's pre-sleep read and the wake's reads** see the same
   hooks. The row ISR's IRQ state at those moments is put back as found.
7. **It must not combine with the per-byte release.** The dev switch gets a
   third value: locked (as before), per byte (dev), unlocked (the default
   in every build). The byte boundary does nothing unless the mode is per
   byte, since unlocking an unlocked state would break the lock's state.

**Measured afterwards,** with the existing instruments under the echo's
load:
- `overrun_diag.py ab` arms (a) and (b): the burst's OEs per read should
  fall from ~9% to the background rate;
- the masked span is the hook's own few instructions;
- each kind's whole call;
- the phase set's release and boundary errors, and the daemon's residuals.

### 2. UART2 above USB (every build)

- **The change:** `mcuconf.h`'s `SN32_SERIAL_UART2_PRIORITY` goes from 1 to
  0, beside the system timer, so UART2 preempts the USB handler.
- **Why:** the USB handler (up to 0.085 ms) runs at UART2's priority today
  and alone nearly fills the byte.
- **The cost:** a UART2 handler (~10-20 us a byte) can delay USB's by that
  much, which is well inside USB's tolerances.
- **Codex, please check:** that ChibiOS's ARMv6-M port allows OS calls at
  priority 0 (`CORTEX_SIMPLIFIED_PRIORITY`, PRIMASK locking); that PendSV
  stays the lowest; and that `mcuconf.h`'s existing comment and the
  deep-sleep plan's priority table are updated.

### 3. The hunt: a latency monitor on the indicator tick (dev only)

- **The tick:** GPT CT16B3 runs at 20 kHz (a 50 us period) at priority 2 for
  the backlight and indicator PWM. Its entry stamp, against the previous
  one on CT16B5, shows how late it ran.
- **What the lateness includes:** any masked stretch, plus any higher
  priority handler (UART2, USB, the system timer).
- **What it keeps, in dev builds:**
  - the longest lateness;
  - counts over 80, 150 and 300 us;
  - for the longest of each, the activity mask at the time (`overrun_diag`'s
    bits) and the main loop's current `LOOP_SITE` or `ACCT` scope, so the
    culprit is named, not inferred.
- **Why it suits the job:** it measures what UART2 suffers without touching
  UART2. Its cost is a counter read and a compare in a handler that already
  runs 20,000 times a second.
- **The known lock holders to look at first** (Gremlin's list):
  - the drain's bulk `chnReadTimeout`, up to 64 bytes copied under the lock;
  - `sdWrite`'s 68-byte pair;
  - `uart_err_poll`;
  - `timer_read32`'s tick extension.

  From the board: health's snapshots, `battery.c:111` (the ADC poll),
  `indicators.c:213`, `lcd_bus.c:950`, and the LED driver's flush lock
  (`sn32f2xx.c:969, 1063`).

## Order

1. Codex on this plan; dispositions here.
2. One dev build with 1, 2 and 3. The daily carries 1 and 2.
3. Codex on the diff, then the flash with JD's go.
4. The A/B again (arms a and b, unlocked against locked), the monitor's map
   under the echo's load and in an hour of JD's use, then fixes for whatever
   it names.
5. Then R5.

## Revision 2: codex's plan review, dispositions

| # | finding | disposition |
|---|---|---|
| 1 | must: keep 25 ms as a release gate, failure paths included: ordinary PCF paths keep a 20 ms timeout with no admission; the timeout is no hard return (checked at byte starts, then cleanup, the restart); the animation resume's synchronous clear is 43-44 ms | **Taken.** (a) **The ordinary timeout drops from 20 to 10 ms** (the unlocked transactions are ~3 ms). (b) A dev op provokes the failure path (a transaction to an absent address: the NACK, the restart). The **complete pass** is measured for the NACK path, a stuck SDA (by the probe's method: the pass as a whole), the pre-sleep admission and the wake's recovery. (c) **Fix 4, below: the animation resume through the staged restore.** (d) Before a daily build or R5: every one of those passes under 25 ms, with the row-sampling gap and key delivery checked beside the UART counts. |
| 2 | must: in unlocked mode the mask timer would count the whole unlocked transfer; the dev switch's booleans would enable per-byte inside unlocked | **Taken.** In unlocked mode the opening masked stretch is closed and published **before** the unlock, and a closing one starts **after** the relock (locked and per-byte keep today's measure). The switch is an enum everywhere (`OD_LOCK_TRANSACTION`, `_BYTE`, `_UNLOCKED`): the halconf macro tests `== BYTE`, the setter takes the enum, the ring's flags carry two bits. |
| 3 | must: the monitor measures gaps between its own services, not UART2's latency; UART2's own servicing and USB delay it (false positives); short sections can hurt UART2 under 80 us; no ISR-readable current scope exists | **Taken.** It is an **interrupt-service-gap monitor**, reported as such. Its entries carry an **observed context**: a volatile byte the main loop publishes at each `ACCT()` scope's entry (and the activity bits). It names a candidate, not a cause. **Acceptance never rests on it:** the OE count, refused bytes and the RX tap's byte totals do. |
| 4 | must: arms a and b are both locked; real controls are needed | **Taken.** The dev ops switch the PCF mode (three values), **UART2's priority** (0 or 1, NVIC readback in the reply) and the monitor (off/on) at run time. The runner reads each back before an arm. **Arms:** PCF locked/unlocked x burst/none at UART2 priority 1; then UART2 priority 1/0 with PCF unlocked; then monitor off/on. Each arm compares OEs, refused bytes, the RX tap's byte total against the frames, and echo losses. Cleanup restores the boot defaults. |
| 5 | should: fix 2's port explanation (ARMv6-M uses NMI, not `CORTEX_SIMPLIFIED_PRIORITY`; PendSV is 0); priority 0 cannot preempt USB's PRIMASK FIFO sections; the UART handler loops over RX and TX; SysTick's 0 comes from an 8 truncated | **Taken.** Fix 2's text corrected. SysTick set to 0 explicitly. The UART handler's whole occupancy measured under two-way load (entry and exit hooks like USB's). USB's locked FIFO sections stay a candidate for the monitor. |
| 6 | should: the monitor's cost at 20 kHz (each us is 2% of the CPU); re-prime at every start and stop (light sleep stops CT16B3; deep sleep stops CT16B5); the USB hook's maximum can be torn by UART2 preempting it | **Taken.** The raw counter, constant thresholds, a rare-event path, measured scan rate and pass time with it on and off; re-primed at init, reset and every tick stop or start; the USB and UART hooks' compare-and-store under `chSysLockFromISR`. |
| 7 | should: drop "never a key" | **Taken** (above). Typing tests stay: the rig's presses counted, fast taps, the waking key. |
| N | notes: the rows and `CFG1`; HAL's state protection preserved by the round trip; the bus guard still necessary and sufficient; the CT16B1 mechanism literal (save only its enable bit while locked, `NVIC_DisableIRQ`'s DSB/ISB, restore only that bit; never ChibiOS's `nvicDisableVector`, which clears pending and priority); clock validation adds the release call's duration, the bracket's success and width, restarts, the RTC ISR's lateness | **All taken.** |

### Fix 4: the animation's resume through the staged restore (every build)

**The problem:** `display_redraw_dashboard()` clears the whole panel in one
`lcd_clear_rect()`: 43-44 ms on the matrix's loop.

**The callers:**
- the animation's resume, on its own timer;
- the boot's first paint;
- the bootloader splash (which is terminal).

**The change:** the resume starts the Fn+D restore's staged sequence
(`debug_exit_step = 1`), which clears in bands, one a pass, and repaints one
component a pass, all already measured under 25 ms. The splash stays as it
is.

**To check (codex):**
- that the staged restore repaints everything the full one did: the icon,
  the connection row, the clock and date, the status;
- that the animation's state (`display_paused`, the flash player) is
  released before the restore starts;
- that the boot path's splash timing is unchanged.

**The base:** this build goes on `e7f6890e06` (stage 1, whose echo is the
A/B's load) and is rebased onto stage 2 v2 when that lands.

