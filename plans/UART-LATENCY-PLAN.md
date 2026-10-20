# UART2's one-byte receiver: keeping its latency under a byte

Status: **draft for codex's review (10-19), nothing built.** Follows
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
  Never a key: those go board to module.

## Why the PCF holds the lock at all

**Only to keep its pin-mode changes atomic.**
- **The I2C's writes:** the fallback LLD emulates open drain by switching
  SDA between input and output (`SW_I2C_USE_OPENDRAIN FALSE`). Each switch is
  a read-modify-write of port A's `MODE` and `CFG`
  (`hal_pal_lld.c:202-250`). Its level changes are `BSET`/`BCLR`, which are
  atomic (`hal_pal_lld.h:411-424`).
- **The only other writer:** the row ISR (PWMD1's periodic callback,
  `drivers/led/sn32f2xx.c`) switches the matrix's column pins between modes,
  A18 and A19 among them (`keyboard.json:144`), on the same port as SCL and
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
