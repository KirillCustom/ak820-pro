# The link's UART overruns and the Caps repaint: a plan to review

Status: **draft for codex's review (10-19), nothing built.** Two independent
items for one `dev` flash, both to land before R5 (a 4-5 day drain wants a
build that stays put: a flash resets the gauge's state). Owner: the Mac
session. The backlog entry this replaces: `BACKLOG.md`, "UART overruns on the
radio link".

## 1. The overruns

### What is known

**Measured:**
- **The rate.** HC_LINK `uart_overrun`:
  - 92 in 51.9 h on flash 2f's drain;
  - 13 in 16.9 h on 2e;
  - 3 in the 10-19 soak's 85 min.

  No framing or parity errors in any of them. A lost byte costs a module
  frame (an ACK, retried; a `5C`; a link state), never a key.
- **One loss in 6 s.** The 300-echo run (`history/raw81-echo-20261019-mac/`)
  lost one byte in ~6 s of back-to-back traffic, 22-28 s after the board
  reset. That is when the boot seed and the acquisition read the PCF.
- **Where the time goes.** The drains ran with USB data on (the RTC's
  proposals are `sof`), so the host's clock sync ran every ~5 min throughout.
- **The longest PCF transaction since the 10-19 flash: 101 ILRC cycles, ~3.1
  ms** (`i2c_max_cycles`, `rtc_phase0.py status` page 2). The length of each
  kind of transaction is not measured. The deep-sleep plan's "a whole read is
  well under a millisecond" was an estimate.

**Read in the code:**
- **What the counter counts.** `SD_OVERRUN_ERROR` is raised for either of
  two events: a hardware receive overrun (`LS.OE`, chibios-contrib
  `a4f8412134`) or a full 128-byte input queue. It is counted once per task
  pass that saw it (`ch582f_ajazz.c:613-628`).
- **The receive path.** UART2 is at priority 1, the highest in use
  (`mcuconf.h:78`). Its 16550-style FIFO is 16 bytes with an RX trigger of
  1 (`ch582f_ajazz.c:589-596`). At 115200 that is ~1.4 ms of tolerance.
- **The I2C holds the system lock for the whole transaction.**
  - `i2cMasterTransmitTimeout` takes `osalSysLock()` around the LLD call
    (`lib/chibios/os/hal/src/hal_i2c.c:203-214`).
  - The fallback LLD (`lib/chibios-contrib/os/hal/lib/fallback/I2C/hal_i2c_lld.c`)
    bit-bangs with rtc.c's 15-NOP busy delay (`SW_I2C_USE_OSAL_DELAY FALSE`,
    `halconf.h:22`) and never unlocks.
  - On this Cortex-M0 the lock is `cpsid i`, so every interrupt waits for the
    transaction, UART2 and the row ISR included.
- **When the board talks to the PCF:**
  - **The host's sync, every ~5 min.** A STOP read and write, the 8-byte
    time write, the release read and write, a verify, then up to ~120
    one-byte bracket reads, one per pass (`rtc.c:1345-1420`).
  - **Without SOF (cable data off):** the reference check every 60 s, a
    7-byte read (`RTC_CHECK_INTERVAL_S`, `config.h:183`).
  - **Each deep sleep:** a 7-byte read before it, and the wake's reads
    after.
  - **At boot:** the seed and the acquisition.

### Why it is not proven

**A byte is lost only when a transaction outlasts the FIFO.** That needs a
lock of more than ~1.4 ms while 16 or more bytes arrive, which no 1-byte read
is likely to reach. Whether any routine transaction exceeds 1.4 ms is
unknown: only the maximum is recorded.

**Other causes fit the same rate:**
- internal flash writes, during which the CPU stalls on erase and program;
- other critical sections (`chSysLock` sites: `health.c` 7, `sleep.c` 6,
  `rtc.c` 12, `battery.c`, `indicators.c`, `lcd_bus.c` 1 each);
- a pass longer than ~11 ms that coincides with a 128-byte burst, which
  fills the input queue.

**A host-only test is too weak.**
- **The test:** run the echo's load beside the daemon's syncs.
- **Why it fails:** with an echo frame every ~20 ms (34 bytes, ~3 ms on the
  wire), a single ~3 ms lock overlaps enough of a burst only ~15% of the
  time. One sync carries about one such lock.
- **The result:** ~0.3 expected losses in a 10-minute run, which settles
  nothing.

### Step 1: measure (in the `dev` build only)

1. **The length of each kind of transaction.** `i2c_note()` takes a kind:
   - read 1 byte;
   - read 7 bytes;
   - write 2 bytes;
   - write 8 bytes.

   It keeps a count and a maximum in ILRC cycles for each kind, read through
   an HC_DEVHOOK op.
2. **Attributing each overrun.** When `uart_err_poll()` sees
   `SD_OVERRUN_ERROR`, it classifies what happened since its previous call:
   - a PCF transaction (`pcf_xfers` moved, through a getter from rtc.c);
   - an internal flash write (the flash session counter moved);
   - neither.

   Three counters, plus a 4-entry ring of (uptime, class, the PCF kind,
   pass ms). The poll runs once per pass, after `rtc_fast_task`, so "since
   the previous poll" covers the transaction's pass.
3. **A burst of PCF reads, on demand.** An HC_DEVHOOK op starts N 7-byte
   reads, one per main-loop pass and at least M ms apart. They go through
   `pcf_read_raw()` from `rtc_fast_task()`'s slot, only with
   `lcd_blit_busy()` false, deferring the pass's display work the way the
   wake's reads do (`display_defer_pass`).

   The burst never runs during a sync (`ps_state != PS_IDLE`), a deep
   sleep's recovery, or an acquisition. N ≤ 2000, M ≥ 20.
4. **A runtime switch for the lock's span:**
   - per transaction, as today;
   - per byte, step 2's fix.

   The switch is an HC_DEVHOOK op, and it defaults to per byte (below).

**The A/B, with no hands (~12 min).** Three runs of `scripts/mac_raw81_echo.py`
at `--gap-ms 0`, ~3 min each, with the loss times recorded (`lost_at`, added
10-19):

| run | burst | lock span | if the PCF is the cause | if it is not |
|---|---|---|---|---|
| (a) | none | per byte | ~0 | ~0 |
| (b) | 1000 reads, 50 ms apart | per transaction | ~150 (15% of 1000) | ~0 |
| (c) | 1000 reads, 50 ms apart | per byte | ~0 | ~0 |

**Then a night's base rate, from the attribution counters** (the link idle
but for the module's own traffic and the daemon's syncs). If the attribution
names flash writes or "neither", the next suspect is named with it, and step
2 still stands on its own: it shortens a 3 ms interrupt mask to a byte's
length.

### Step 2: the fix: release the lock between bytes

**The change:** in the fallback LLD (chibios-contrib, branch
`ak820pro-patches`, recorded in `PATCHES.md`), at the start of
`i2c_write_byte()` and `i2c_read_byte()`:

```c
#if SW_I2C_BYTE_PREEMPT
  osalSysUnlock();   /* let pending interrupts run: UART2's FIFO is ~1.4 ms */
  osalSysLock();
#endif
```

`SW_I2C_BYTE_PREEMPT` is set in the board's `halconf.h`. In the `dev` build
the runtime switch gates it.

**Why it is safe:**
- **A slave waits at a byte boundary.** Every `i2c_write_bit()`,
  `i2c_read_bit()` and `i2c_write_start()` ends with SCL low, and I2C has
  no minimum clock rate. Start, restart and stop each stay inside one locked
  chunk.
- **P0's `MODE` writes stay inside a chunk.** `palSetLineMode()` does a
  read-modify-write of `MODE` in `i2c_read_bit()`
  (`SW_I2C_USE_OPENDRAIN FALSE`, `hal_pal_lld.c:202-227`). SCL and SDA are
  A14 and A15 (`rtc.c:21-25`), and the row ISR switches A18 and A19's modes
  on the same port (`drivers/led/sn32f2xx.c`, `gpio_set_pin_*`). Every I2C
  mode write still runs under the lock, so the two can never interleave.
- **No caller holds the lock or runs in an ISR.**
  - **To verify:** every `i2cMasterTransmitTimeout` caller is main-loop
    code, `rtc_sleep_prepare()` (`sleep.c:773`) included.
  - **Why it matters:** the unlock would reopen the caller's critical
    section, and ChibiOS's `osalSysLock` does not nest. The same would hold
    today at the transaction's end.
- **ChibiOS allows it:** an unlock-lock pair is the usual preemption point,
  and QMK runs the main thread and idle only.

**What it costs:**
- **Longer transactions.** Each byte boundary lets pending interrupts run,
  mostly the row ISR (188-261 µs). A 10-byte transaction gains up to
  ~2.6 ms.
- **The short timeout is too tight.**
  - **The problem:** the deep sleep's recovery runs its transactions with a
    5 ms deadline (`pcf_short_timeout`, `rtc.c:32-38`), which a stretched
    7-byte read could approach.
  - **The proposal:** 10 ms. It bounds only a stuck bus.
  - **To check:** the recovery's pass budget (DEEP-SLEEP-BUILD-PLAN section
    4, "The budget") must still come in under 25 ms.
- **Jitter in the phase set.** The release write (3 bytes) gains up to
  ~0.8 ms of jitter. The bracket measures the achieved boundary and feeds
  back half of the error.
  - **To compare:** `pcf_release_err_ms`, `pcf_boundary_err_ms` and the
    daemon's sync residuals, before and after.

**Expected:**
- **Each lock lasts about a byte.** About 9 bits of 4 delays each: estimated
  0.15-0.3 ms against the FIFO's 1.4 ms. Step 1 measures it.
- **The row ISR is never held for ~3 ms.** Today a held row stays lit and
  the scan pauses during a long transaction.

**Why the default is per byte:**
- **It lets one flash give both the A/B and the fixed build.** Run (b) sets
  per transaction for its 3 minutes.
- **The risk is bounded.**
  - **If per byte breaks the I2C:** the switch reverts it at runtime with no
    flash.
  - **What breaks:** failed PCF transactions, which the code already
    tolerates. The seed falls back, and the daemon's next sync re-sets the
    clock.

## 2. The Caps repaint

### What is known

**Measured** (`history/fnb-caps-20261019-jd/`): one pass of exactly 25 ms in
~600 Caps toggles, marked `blit`; otherwise 10-22 ms. 25 ms is the shortest
keypress, so a press could be lost at that length.

**Read in the code:**
- **The paint.** `display_housekeeping_task()`'s 10 Hz tick calls
  `draw_locks(false)`, which calls `draw_locks_diff(false)` (`display.c:2075-2125`).
  - The padlock: `fill_rect`s.
  - "CAPS": `lcd_draw_flash_text()`, four synchronous glyphs at ~2.5 ms
    each, plus a wait for any blit in flight.
- **Everything else on the same tick:**
  - the connection digit;
  - the status overlay;
  - the text slot;
  - on a second's edge, the clock and the battery row.

  The Fn+D restore already stages the band one component per pass
  (`draw_locks_staged`); the normal path does not.

### The change: the labels through the glyph queue

1. **The labels are queued.** `draw_locks_diff()` pushes the label glyphs
   (`CAPS`, `WIN`, `FN`, `SCR`) with `gq_push()` instead of painting them
   with `lcd_draw_flash_text()`, and the pump paints one per pass. The
   padlock (`fill_rect`s) and the clears (≤ 40x23 px) stay synchronous.
2. **The queue gets room for them.** `GQ_MAX` grows by 10, the most the
   band can hold (4 + 3 + 3). Today `gq_push()` drops a glyph when the queue
   is full, and `shown_caps` would then claim pixels that were never painted.
3. **A reset of the queue forgets the band.** Every `gq_reset()` also calls
   `locks_forget()`, so queued glyphs that are thrown away are painted
   again.
4. **The order holds by itself.** Housekeeping returns while `gq_pending()`
   (`display.c:3182`), so a Caps-off clear cannot run before a queued CAPS
   has painted. Interleaving with the text band is harmless, since each
   entry carries its own font, x and y.
   - **To check:** the lock band (y 82-104) overlaps no text shadow, so the
     text band's `band_clear()` never needs to forget the band.
5. **The Fn+D restore's stage 5** goes through the same diff, `one = true`.
   - **To check:** that it waits for the queue between components, or the
     restore advances while CAPS is still queued.

**Expected:**
- **A Caps press:** the padlock appears on the tick, and CAPS within ~4
  passes (~12 ms).
- **The pass:** the lock band's share drops from ~10 ms to one glyph.

**Test:**
- **The run:** `scripts/fnb_caps_test.py`'s control, with ~600 toggles and
  a track playing (`docs/display.md`'s rule for display changes).
- **To pass:** no pass of 25 ms or more, and `blit_gap_max_ms` well under
  today's 22.

## Build, review and order

1. **This plan to codex**, every finding dispositioned here.
2. **One `dev` build:**
   - step 1's diagnostics, dev only;
   - step 2 (the LLD patch, the switch in dev);
   - the Caps change.

   The daily build carries step 2 and the Caps change, with no diagnostics,
   and `build.sh`'s symbol checks prove it.
3. **Codex on the diff**, then the flash with JD's go.
4. **The A/B** above (no hands), then the night's attribution.
5. **R5 starts on this build,** unless the A/B sends step 2 back.
