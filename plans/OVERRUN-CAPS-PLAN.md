# The link's UART overruns and the Caps repaint: a plan to review

Status: **revision 2 (10-19), after codex's plan review**
([`review-codex-overrun-caps-plan-2026-10-19.md`](review-codex-overrun-caps-plan-2026-10-19.md);
every finding's disposition is at the end). Nothing is built.

Two independent items for one `dev` flash, both to land before R5 (a 4-5
day drain wants a build that stays put: a flash resets the gauge's state).
Owner: the Mac session. The backlog entry this replaces: `BACKLOG.md`, "UART
overruns on the radio link".

## 1. The overruns

### What is known

**Measured:**
- **The count.** HC_LINK `uart_overrun`:
  - 92 in 51.9 h on flash 2f's drain;
  - 13 in 16.9 h on 2e;
  - 3 in the 10-19 soak's 85 min.

  No framing or parity errors in any of them. It counts **polls that saw the
  flag**, not lost bytes (below).
- **A loss under load with no known PCF cause.** The 300-echo run
  (`history/raw81-echo-20261019-mac/`) lost one byte in ~6 s of back-to-back
  traffic, 22-28 s after a reset. Revision 1 put that loss on the boot
  acquisition; codex showed it runs earlier (the seed in init, the
  acquisition after the splash with a 1.5 s deadline). So nothing known
  touched the PCF then, unless the 60 s reference check ran before SOF
  qualified. **Unexplained, and a hint that the PCF is not the only cause.**
- **The longest PCF call since the 10-19 flash: 101 ILRC cycles, ~3.1 ms**
  (`i2c_max_cycles`).
  - **What it includes:** wall time around the API call, interrupts outside
    the lock among them. Codex also notes its stamp is not coherent across
    the RTC's second rollover.
  - **What it doesn't say:** how long interrupts were actually masked, or
    how long each kind of transaction takes.

**Read in the code** (codex confirmed each, with the corrections folded in):
- **What the counter counts.** `SD_OVERRUN_ERROR` is one sticky event flag
  raised for either of two things: a hardware receive overrun (`LS.OE`,
  chibios-contrib `a4f8412134`) or a byte that would not fit the 128-byte
  input queue (`hal_serial_lld.c:263, 293-301`).
  - **Where it is read:** `uart_err_poll()`, which `r81_commit()` also calls
    in `dev` builds (`ch582f_ajazz.c:617, 710, 1436`).
  - **When it is read:** through `keyboard_task()`, before housekeeping's
    `rtc_fast_task()`. So one poll's interval spans parts of two passes.
- **The receive path.**
  - **The FIFO:** UART2's is enabled with an RX trigger of 1, at 115200 8N1.
    **Its depth is unstated** (`docs/sn32f299.md:938`). If it is 16 bytes,
    it holds ~1.4 ms, less whatever is already in it when interrupts are
    masked.
  - **The priorities:** UART2 is at priority 1. So is USB, which it cannot
    preempt (`hal_usb_lld.h:76`). SysTick resolves to priority 0
    (`hal_st_lld.h:49`, `nvic.c:147`).
- **The I2C masks interrupts for the whole transaction.**
  - `i2cMasterTransmitTimeout` takes `osalSysLock()` around the LLD call
    (`hal_i2c.c:203-214`).
  - The fallback LLD bit-bangs with rtc.c's busy delay (a volatile loop of
    15 NOPs), never sleeping or unlocking.
  - On ARMv6-M the lock is `__disable_irq()` (`chcore.h:499`): every
    maskable interrupt waits.
- **The wire bytes per kind:**

  | kind | wire bytes |
  |---|---|
  | 1-byte register read | 4 |
  | 7-byte calendar read | 10 |
  | 2-byte register write | 3 |
  | 8-byte time write | 9 |

- **When the board talks to the PCF:**
  - **The host's sync.** A STOP read and write, the time write, the release
    read and write, a verify, then the bracket's one-byte reads over a time
    window (`rtc.c:1345-1467`).
  - **The legacy synchronous `rtc_set_time()`**, with its retry and
    recovery writes.
  - **Without a qualified SOF:** the 60 s reference check, a 7-byte read
    (`RTC_CHECK_INTERVAL_S`, `config.h:183`).
  - **Each deep sleep:** a read before it and the wake's reads after.
  - **At boot:** the seed and the acquisition.
- **The drains' syncs: unknown.** The drains had USB data on, but SOF
  proposals do not prove the host's syncs succeeded. The exposure (syncs an
  hour) comes from the daemon's log, not from SOF.
- **An existing contract violation.**
  - **The path:** a `MSG_TIMEOUT` leaves `I2CD1.state = I2C_LOCKED`, and
    rtc.c calls `i2cStart()` only in `rtc_init()` (`rtc.c:1076`).
  - **Why nothing notices:** with assertions compiled out, the next
    transaction proceeds anyway.
  - **A second defect:** `i2c_write_restart()` ignores
    `i2c_write_start()`'s error (fallback LLD `:130-155`).

### What the PCF can and cannot explain

**One ordinary frame cannot overflow the FIFO.** The module's normal frames
(ACK, `5A`, `5B`, `5C`) are 3 bytes, and the battery poll comes every 5 s. A
3 ms mask that catches one of them does not overflow a 16-byte FIFO.

**So for the PCF to explain 1-2 an hour,** roughly 8-17% of the long
transactions at ~12 syncs an hour would have to meet real bursts, or a FIFO
already part full. That is possible at a wake or a reconnect, and unshown.

**The real alternatives** (from codex's audit, all to be measured):
- **The USB ISR.** It runs at UART2's priority, and its FIFO routines poll
  `RWSTATUS` without a bound (`hal_usb_lld.c:148-203, 369-389`).
- **SysTick at priority 0.**
- **Internal flash erase.** It lasts 1-2 ms and stalls instruction fetch.
  Both drain captures show the last erase at uptime 99 ms, so it is unlikely
  for those runs.
- **The deep sleep's RX blindness.** A designed loss, attributed apart.
- **Main-loop delays** that fill the 128-byte queue without masking
  anything.

### Step 1: measure (in the `dev` build, under `AK820_DEV_HOOKS`)

1. **What causes each overrun, recorded at the moment it happens.**
   - **Two counters in the serial LLD's dev hook** (chibios-contrib), one
     for hardware `LS.OE` and one for an input queue that refused a byte.
     Today the two share one flag.
   - **What was running, read by the UART ISR when it sees OE.** Main-loop
     code sets bits in a global activity mask around each candidate:
     - a PCF transaction, with its kind and lock mode;
     - a flash program or erase;
     - a sleep entry or exit.

     The ISR first runs as the mask lifts, while the bit is still set. So
     the entry names the cause directly, not by correlation across a poll.
   - **A 32-entry ring** of (uptime, cause mask, PCF kind, the masked span),
     plus a count for each cause and for none.
   - **Nothing new consumes `SD_OVERRUN_ERROR`.** The ring reads the ISR's
     own state, so the raw81 collector's taint logic and `uart_err_poll()`
     are untouched.
2. **How long interrupts are masked, timed by the free-running CT16B5**
   (`hal_st_lld.h:147`; it counts while masked):
   - for each PCF kind: count, failures, the longest masked span, the
     longest whole call, and how many masked spans passed 1.0 and 1.4 ms;
   - in byte mode, each chunk, timed by a dev hook in the LLD;
   - the longest USB ISR and the longest SysTick handler, from dev hooks at
     their entry and exit. If either stays under ~1 ms, it is excluded.
3. **A burst of PCF reads, on demand** (an HC_DEVHOOK op): N 7-byte reads
   into a scratch buffer, one per pass.
   - **The spacing** is random, 40-80 ms, so it cannot phase-lock to the
     echo or the loop.
   - **The admission** is the recovery's: queue empty, DMA idle, the pass
     under 10 ms (`rtc.c:1620, 1806-1814`), with the pass's display work
     deferred.
   - **It yields to:**
     - `pcf_pending`;
     - a non-idle `ps_state`;
     - an acquisition, its pending start included;
     - the whole sleep recovery.
   - **One transaction a pass at most:** no `rtc_task()` reference check in
     the same pass.
   - **What it reports:** reads done and the exposure time. A successful
     read resets `pcf_avail_fail`, an observer effect, so the report says
     so.
4. **The lock's span, at run time:** per transaction (as today) or per
   byte (step 2).
   - **This build boots per transaction** (codex), and the hook switches it.
   - **The daily build compiles in per byte only.**

**The A/B, with no hands.** `scripts/mac_raw81_echo.py` at `--gap-ms 0`,
~3 min an arm, with the loss times recorded (`lost_at`). Each arm runs until
its burst completes, plus margin.

| arm | lock span | burst | measures |
|---|---|---|---|
| (a) | per transaction | none | the load's own rate |
| (b) | per transaction | 1000 reads | whether a long mask can cause losses under load |
| (c) | per byte | 1000 reads | whether the fix prevents them |
| (d) | per byte | none | the fix's own control |

- **What is compared:** the hardware-OE deltas and the ring's causes. Echo
  losses alone are not enough, since the collector's 15 ms lifetime loses
  echoes without any overrun.
- **The same conditions in every arm.**
  - **The daemon:** the same policy in every arm. The cable's raw-HID handle
    is released after each hook command, so the daemon's syncs proceed.
- **What the A/B shows and what it doesn't.** It shows whether long masked
  PCF reads can cause losses under an injected load, and whether byte
  preemption prevents them. It does not show what causes the historical
  rate.
- **The historical rate:** the night's base rate after the A/B, read from
  the ring and the counts, is what answers that.

### Step 2: the fix: release the lock between bytes

**The change:** in the fallback LLD (chibios-contrib, branch
`ak820pro-patches`, recorded in `PATCHES.md`), at the start of
`i2c_write_byte()` and `i2c_read_byte()`:

```c
#if SW_I2C_BYTE_PREEMPT
  osalSysUnlock();   /* let pending interrupts run: UART2's FIFO lasts ~1.4 ms */
  osalSysLock();
#endif
```

`SW_I2C_BYTE_PREEMPT` is set in the board's `halconf.h`. In the `dev` build
it is gated at run time.

**With two repairs, in every build:**
- **After a `MSG_TIMEOUT`,** rtc.c does an `i2cStop()` and an `i2cStart()`
  before the next transaction, so the driver leaves `I2C_LOCKED`.
- **`i2c_write_restart()` returns `i2c_write_start()`'s error.**

**Why it is safe** (codex confirmed on the normal path):
- **A slave waits at a byte boundary.** START, every data bit and every
  ACK/NACK leave SCL low before the next byte, and I2C has no minimum clock
  rate. Restart and STOP each stay inside one locked chunk.
  - **The exception:** the failure path above, which the second repair
    closes.
- **Port A's `MODE` writes stay inside a chunk.** SCL and SDA are A14 and
  A15 (`rtc.c:21-25`), and they share `MODE` with A18 and A19, which the row
  ISR switches (`drivers/led/sn32f2xx.c`, `gpio_set_pin_*`). Their `CFG`
  fields are in different registers.
  - **Why no write is lost:** every I2C mode write runs under the lock, and
    the main thread cannot resume halfway through the ISR's own write.
- **No caller holds the lock or runs in an ISR.**
  - `rtc_sleep_prepare()` runs before `deep_sleep()` takes the lock
    (`sleep.c:773-775`).
  - The wake's reads come later, from `rtc_fast_task()`.
  - The raw-HID handlers run from the main loop.
- **ChibiOS allows it.**
  - **The debug lock count:** the unlock and lock keep it correct.
  - **No reschedule:** `chSysUnlock()` does not reschedule.
  - **The driver's state:** staying ACTIVE while unlocked is fine with one
    owner.
  - **The timeout's clock:** it reads a free-running counter, so it stays
    valid while masked.
  - **A future second I2C user** would need bus serialization.

**What it costs:**
- **Longer transactions.** Each byte boundary lets pending interrupts run.
  Codex: the +2.6 ms (10 bytes) and +0.8 ms (the 3-byte release write)
  figures are row-ISR estimates, not upper bounds, since USB and SysTick can
  add more.
- **The short timeout (5 ms; the deep sleep's recovery,
  `rtc.c:32-38`).** It is chosen from step 1's data:
  - **If the stretched 7-byte read stays under ~3 ms,** 5 ms stays.
  - **Otherwise, 10 ms,** and only after the budget below is shown by
    measurement, failure paths included (NACK, a stuck bus, the pre-sleep
    admission, the wake's recovery):

    > under 10 ms before admission + the deadline + the timeout's STOP +
    > the housekeeping left < 25 ms

    The deadline is checked at byte boundaries and in clock waits, not as a
    hard return.
- **A stuck clock wait still holds the lock until the timeout.** Per byte
  bounds the mask on working buses only.
- **The phase set's timing.** `PCF_LATE_MS` is checked before the release
  write, so the write can finish later than the window. The bracket's
  feedback corrects the systematic part, not per-run jitter.
  - **To compare, quiet and under the echo's load:**
    - the release write's duration;
    - `pcf_release_err_ms` and `pcf_boundary_err_ms`;
    - `pcf_restarts`;
    - whether the bracket succeeds, and how wide it is;
    - the daemon's residuals.

**Expected:**
- **Each mask lasts about a byte.** Step 1 measures it.
- **The row ISR is never held for ~3 ms.**

## 2. The Caps repaint

### What is known

**Measured** (`history/fnb-caps-20261019-jd/`): one pass of exactly 25 ms in
~600 Caps toggles, marked `blit`; otherwise 10-22 ms. That is a whole pass:
the mark does not isolate the share that CAPS took.

**Read in the code:**
- **The paint.** The 10 Hz housekeeping tick calls `draw_locks(false)`,
  which calls `draw_locks_diff(false)` (`display.c:2075-2125`).
  - The padlock: `fill_rect`s.
  - The label: `lcd_draw_flash_text()`, synchronous, one glyph at a time
    (`lcd_bus.c:569`).
- **Everything else on the same tick:**
  - the connection digit;
  - the status overlay;
  - the text slot;
  - on a second's edge, the clock and the battery row.
- **The Fn+D restore** stages the band one component per pass; the normal
  path does not.

### The change: the labels through a lock queue the pump serves first

1. **The labels are queued.** `draw_locks_diff()` queues the label glyphs
   (`CAPS`, `WIN`, `FN`, `SCR`) instead of painting them synchronously.
   - **Where:** a small lock queue (10 entries: 4 + 3 + 3), which the pump
     serves before the text queue. Codex: behind the text queue, CAPS could
     wait ~44 pump turns, and the padlock would lead it by hundreds of ms.
   - **What stays synchronous:** the padlock (`fill_rect`s) and the clears
     (≤ 40x23 px).
2. **A queue full is visible, never silent.** A glyph that doesn't fit is
   counted and leaves its `shown_*` false, so it paints again.
   - **The text queue gets its 64 → 74 too** (codex's count: one batch of
     19 + 21 text, 24 clock, 10 lock), with the same rule.
3. **The band is forgotten only where it is really blank.** `locks_forget()`
   goes with `shadow_invalidate()` on the discard and full-clear paths.
   - **Never on a reset after the queue completes** (codex's must-fix 1): a
     `gq_reset()` after the last glyph would otherwise forget a CAPS on the
     screen, and a Caps-off would then skip its clear.
4. **The Fn+D restore alternates in the pump** (codex's must-fix 2). Today,
   while `debug_exit_step` is set, the pump runs a stage and returns without
   pumping (`display.c:1355, 2809`).
   - **The new order:** stage 1 still discards before its clear. After it,
     each pass either runs one restore stage or submits one queued glyph,
     lock queue first.
   - **Stage 5 advances only when both queues are empty.**
   - **`pass_deferred` stays above both.**
5. **Ordering in normal use holds as today.**
   - **Housekeeping waits for the queues:** it returns while either one is
     pending (`display.c:3182`), so a Caps-off clear cannot run before a
     queued CAPS has painted.
   - **The bus serializes the rest:** a synchronous clear waits for the
     DMA in flight.
   - **No other band touches it:** the lock band (y 82-104) overlaps no
     text or clock band (codex checked).

**Expected:**
- **A Caps press:** the padlock appears on the tick, and CAPS a few passes
  later, ahead of any queued text.
- **The tick's pass:** the label's ~10 ms leaves it.

**Not claimed:** the animation's resume still does a synchronous
full-screen clear, so this is not a guarantee for every display path.

**Test, with a track playing** (`docs/display.md`):
- `scripts/fnb_caps_test.py`'s control, ~600 toggles;
- a toggle off while the label is still queued;
- SCR → FN;
- leaving Fn+D with several locks lit;
- a discard and repaint;
- a sleep's recovery;
- an animation's resume.

Pass: the pixels right after each, no pass of 25 ms or more, and
`blit_gap_max_ms` well under 22.

## Build, review and order

1. **Codex's verify** of this revision.
2. **One `dev` build,** in three groups:
   - **Diagnostics, dev only, under `AK820_DEV_HOOKS`:** the measurement,
     the burst, the ring and the switch.
   - **Step 2:** the LLD patch, the timeout recovery and the restart's
     error.
   - **The Caps change.**

   **The daily build** carries step 2 and the Caps change.
   - **`build.sh`'s checks are extended (codex):** they prove that no
     diagnostic symbol is present, and that the byte preemption is compiled
     in.
3. **Codex on the diff**, then the flash with JD's go.
4. **The A/B** (no hands), then the night's attribution.
5. **R5 starts on this build,** unless the A/B sends step 2 back.

## Codex's plan review (10-19): dispositions

| # | finding | disposition |
|---|---|---|
| 1 | must: `locks_forget()` on every `gq_reset()` would forget a painted CAPS (completion also resets) | **Taken.** Caps item 3: only on the discard and full-clear paths. |
| 2 | must: the Fn+D restore never pumps; a wait in stage 5 deadlocks | **Taken.** Caps item 4: the pump alternates a stage and a glyph. |
| 3 | must: attribution must split hardware OE from queue overflow, allow several causes, not consume the raw81 path's flag, and keep more than 4 entries | **Taken,** at the source: step 1 item 1. Two LLD counters, the cause read in the UART ISR from an activity mask, a 32-entry ring, and no new flag consumer. |
| 4 | must: the burst needs the recovery's admission; the 10 ms timeout needs a measured budget; `I2C_LOCKED` after a timeout is never cleared | **Taken.** Step 1 item 3; step 2's timeout is chosen from data, with the budget measured on failure paths; the stop/start repair, in every build. |
| 5 | must: the A/B shows capability and the fix, not the historical rate; fixed spacing phase-locks; a loss's 250 ms wait removes load; compare OE, not echo losses | **Taken.** The four arms, random spacing, exposure recorded, OE deltas, the cable handle released. The historical rate is answered by the night's attribution. |
| 6 | should: the 16-byte FIFO is unproven | **Taken.** Conditional throughout. |
| 7 | should: "never a key" is too absolute (ACK loss leads to retries, give-ups, queue pressure) | **Taken.** Dropped. |
| 8 | should: the 300-echo loss was not during the acquisition | **Taken.** Recorded as unexplained, and as a hint of a second cause. |
| 9 | should: SOF does not prove syncs | **Taken.** Exposure from the daemon's log. |
| 10 | should: wire bytes are 4/10/3/9 | **Taken.** |
| 11 | should: measure masking, not call time; CT16B5 | **Taken.** Step 1 item 2. |
| 12 | should: USB (priority 1, unbounded polling) and SysTick (priority 0) are candidates; UART2 is not "highest" | **Taken.** Their longest handlers are measured; the wording is fixed. |
| 13 | should: flash erase is unlikely for the drains (last erase at 99 ms); track program and erase | **Taken.** In the activity mask. |
| 14 | should: the phase set's timing needs numbers, not "feedback corrects it" | **Taken.** The comparison list in step 2. |
| 15 | note: `i2c_write_restart()` ignores the start's error | **Taken.** Repaired, in every build. |
| 16 | note: the burst needs a scratch buffer, to yield to `pcf_pending` and the acquisition's start, and one transaction a pass | **Taken.** Step 1 item 3. |
| 17 | note: CAPS can wait ~44 turns behind text | **Taken.** The lock queue served first. |
| 18 | note: `GQ_MAX` 64 → 74; overflow must be visible | **Taken.** Caps item 2. |
| 19 | note: per byte as the default only after the conditions; boot per transaction and switch by hook | **Taken.** Step 1 item 4. |
| 20 | note: diagnostics under `AK820_DEV_HOOKS`; extend `build.sh`'s checks | **Taken.** Build step 2. |
