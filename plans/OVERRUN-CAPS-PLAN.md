# The link's UART overruns and the Caps repaint: a plan to review

Status: **revision 3 (10-19), after codex's plan review and its verify**
([`review-codex-overrun-caps-plan-2026-10-19.md`](review-codex-overrun-caps-plan-2026-10-19.md),
[`review-codex-overrun-caps-plan-verify-2026-10-19.md`](review-codex-overrun-caps-plan-verify-2026-10-19.md);
both reviews' dispositions are at the end). Nothing is built.

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
  (`history/raw81-echo-20261019-mac/`) had one tainted echo (an overrun
  under its collection) in ~6 s of back-to-back traffic, 22-28 s after a
  reset.
  - **What it was not:** revision 1 put it on the boot acquisition, but
    codex showed that runs earlier (the seed in init, the acquisition after
    the splash with a 1.5 s deadline). Nor can the 60 s reference check
    explain it: its counter starts at zero at boot (`rtc.c:451, 1854`).
  - **Unexplained,** a host sync included as a possibility, and a hint that
    the PCF is not the only cause.
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

1. **The context of each overrun, recorded at the moment it is seen.**
   - **Two counters in the serial LLD's dev hook** (chibios-contrib):
     - **OE observations:** counted from the already-read `ls` at the site
       that reports OE;
     - **refused bytes:** counted on the failed `iqPutI()` branch
       (`hal_serial_lld.c:249, 293`).

     There are no extra LS or RB reads, and every existing flag and its
     consumers stay as they are. Today both share one flag.
   - **Its known gaps:** the BI branch passes only BI, and the drain's
     re-reads of LS don't all pass through `set_error()`, so these counters
     inherit that coverage.
   - **What was running, read by the UART ISR when it sees OE.** Main-loop
     code sets bits in a global activity mask around each candidate:
     - a PCF transaction, with its kind and lock mode;
     - a flash program;
     - a flash erase;
     - a sleep entry or exit.
   - **What the mask is:** the context in which an overrun was observed,
     **not an exclusive cause** (codex's verify, finding 5).
     - **A PCF transaction:** an already-pending UART interrupt runs at
       hal_i2c's unlock, inside the call, so the bit is still set. In byte
       mode it runs in the gaps, also with the bit set. A USB or SysTick
       delay in such a gap is indistinguishable from the PCF's own mask.
     - **A flash program:** each busy window restores PRIMASK before the
       program call returns, so the bit is set when the ISR runs.
     - **A flash erase:** nothing is masked. The fetch stalls until the
       erase ends, and the bit stays set through the delayed service.
   - **Each ring entry** carries the context mask with the masked span and
     the longest USB and SysTick handlers since the last entry. So "several
     contributors" and "unknown" stay readable.
   - **Sleep is accounted apart.** The deep sleep disables RX and discards
     the FIFO, its status and the queue before it unlocks (`sleep.c:335,
     468`), so an ISR afterward cannot see what was lost.
     - **What is recorded:** the count of RX-disabled intervals and their
       total length.
     - **What is not claimed:** an OE seen after RX comes back describes
       the wake, not the sleep.
   - **A 32-entry ring with a sequence number,** so overwrites are counted.
     The per-context counts are kept apart from the ring and survive its
     overwrites.
   - **Nothing new consumes `SD_OVERRUN_ERROR`,** so the raw81 collector's
     taint logic and `uart_err_poll()` are untouched.
2. **How long interrupts are masked, timed by the free-running CT16B5.**
   - **The timer:** 187,500 Hz, 16 bits, ~5.33 µs a tick, wrapping at
     349.5 ms (`chconf.h:24`, `hal_st_lld.h:147`). It counts while masked.
     Spans are unsigned modular differences.
   - **Where the stamps go:**
     - each masked span is stamped before its unlock;
     - its length is published before the UART ISR can read it;
     - the spans covered: the first START chunk, each byte, the final STOP,
       and every error exit.

     The whole-call time is measured separately.
   - **What is kept, for each PCF kind:** count, failures, the longest
     masked span, the longest whole call, and how many masked spans passed
     1.0 and 1.4 ms.
   - **USB and SysTick:** the longest handler of each, from dev hooks at
     their entry and exit (`hal_usb_lld.c:423`, `hal_st_lld.c:120`).
     - **How:** the hooks read the counter directly; `timer_read32()`
       takes a lock.
   - **What a low maximum does not show:** a handler's longest run under
     1 ms excludes nothing. Consecutive delays and a part-full FIFO
     matter, and entry stamps miss the time spent waiting to enter.
   - **The limits:**
     - **Deep sleep:** CT16B5 stops in deep sleep, so it cannot time the
       sleep's PRIMASK-held span or all of the clock's restoration.
     - **Long runs:** a modular span cannot bound a handler that runs past
       one wrap, or never returns.
3. **A burst of PCF reads, on demand** (an HC_DEVHOOK op): N 7-byte reads
   into a scratch buffer, one per pass at most.
   - **The spacing** is random, 40-80 ms, so it cannot phase-lock to the
     echo or the loop.
   - **The admission** is the recovery's: queue empty (both queues, part 2),
     DMA idle, the pass under 10 ms (`rtc.c:1620, 1806-1814`), with the
     pass's display work deferred.
   - **The timeout:** each read sets the short timeout explicitly, the one
     chosen in step 2 (5 or 10 ms). `pcf_read_raw()` would otherwise use
     20 ms (`rtc.c:32`).
   - **The budget:** the pass's measured total includes the stop/start
     recovery (step 2).
   - **It yields to:**
     - `pcf_pending`;
     - a non-idle `ps_state`;
     - an acquisition, its pending start included;
     - the whole sleep recovery.
   - **One PCF transaction a pass, across the whole pass,** using the
     existing `pcf_xfers`/`pcf_xfers_mark` accounting:
     - the burst skips a pass whose earlier handlers already used the bus;
     - the `rtc_task()` reference check is suppressed after a burst read;
     - while a burst runs, the deep sleep is blocked as one of
       `ds_blocked`'s reasons. `rtc_sleep_prepare()`'s pass-end read
       (`sleep.c:823`) has no "bus already used" test.
   - **What it reports:**
     - reads done;
     - the exposure time;
     - the intervals with no echo in flight.

     A successful read resets `pcf_avail_fail`, an observer effect, so the
     report says so.
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

- **What is compared:** the OE-observation deltas and the ring's contexts.
  Echo losses alone are not enough, since the collector's 15 ms lifetime
  loses echoes without any overrun.
- **The exposure, recorded per arm,** since the script has one request in
  flight and waits up to 250 ms on a loss, leaving PCF reads with no load
  behind them:
  - requests sent;
  - the gaps with nothing in flight;
  - reads completed;
  - the arm's duration.
- **Each arm twice,** in the order a b c d d c b a, so drift and the
  exposure's differences show.
- **The same conditions in every arm.**
  - **The daemon:** the same policy in every arm. The cable's raw-HID handle
    is released after each hook command, so the daemon's syncs proceed.
- **What the A/B shows and what it doesn't.** It shows whether long masked
  PCF reads can cause losses under an injected load, and whether byte
  preemption prevents them. It does not show what causes the historical
  rate.
- **The historical rate:** two measurements.
  - **The first night, in transaction mode** (as the board boots): the
    ring's contexts for the old rate, as evidence, not proof of cause.
  - **Then the boot default changes to byte mode,** by a second small flash
    once the A/B and the night pass. A reset would otherwise put the
    runtime switch back.
  - **R5's days in byte mode** give the rate after the change, and the
    contexts of whatever remains.

### Step 2: the fix: release the lock between bytes

**The change:** in the fallback LLD (chibios-contrib, branch
`ak820pro-patches`, recorded in `PATCHES.md`), at the start of
`i2c_write_byte()` and `i2c_read_byte()`:

```c
#if SW_I2C_BYTE_PREEMPT
  osalSysUnlock();   /* let pending interrupts run: UART2's FIFO lasts
                      * ~1.4 ms if it is 16 bytes (its depth is unstated) */
  osalSysLock();
#endif
```

`SW_I2C_BYTE_PREEMPT` is set in the board's `halconf.h`. In the `dev` build
it is gated at run time.

**With two repairs, in every build:**
- **After a failed transaction,** rtc.c does an `i2cStop()` and an
  `i2cStart(&I2CD1, &i2ccfg)` before the next one, so the driver leaves
  `I2C_LOCKED`.
  - **Which failures:** `CHECK_ERROR` turns every negative error into
    `MSG_TIMEOUT` (fallback LLD `:40`), so a NACK or a lost arbitration gets
    the recovery too, not only a deadline.
  - **The config:** the persistent `&i2ccfg`, since `i2cStop()` clears
    `i2cp->config`.
  - **What it doesn't do:** count as a success or retry at once. The
    failure's result and the existing retry and back-off state stand.
  - **What it does to the pins** (codex's verify): the stop changes none;
    the start sets SDA and SCL high as push-pull outputs.
  - **What it is:** a driver-state repair, not a bus clear. It sends no
    clocks to free a stuck line and does not verify a STOP.
  - **Why it is safe:** both calls take their own locks, and every call
    site is main-loop code after hal_i2c has unlocked.
- **`i2c_write_restart()` returns `i2c_write_start()`'s error,** so a failed
  repeated START no longer falls through to another byte.

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

**Expected, on a working bus:**
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
2. **A label is queued whole or not at all.**
   - **The rule:** before queuing a label, `draw_locks_diff()` checks the
     lock queue has room for every glyph.
     - **No room:** nothing is queued, the previous `shown_*` stands, and a
       dirty flag forces a reconcile on the next tick, even if the wanted
       state has meanwhile gone off.
   - **Why:** a partial queue (C and A in, P and S refused) with `shown_caps`
     left false would strand C and A if Caps then went off. False to false
     skips the clear (codex's verify, finding 2).
   - **The capacity:** ten entries hold one complete batch for the band
     (4 + 3 + 3). Housekeeping waits for both queues, so a second batch
     never builds up behind a first.
   - **The text queue stays at 64** (the separate lock queue needs no room
     there).
   - **The text gets a real retry:**
     - `queue_line()` commits its shadow only after every glyph is in;
     - `draw_text_slot()` clears `text_dirty` only after a successful
       queue (`display.c:2914, 2960`);
     - an overflow is counted.
3. **The band is forgotten only where it is really blank.**
   - **Where:** `locks_forget()` is called beside `shadow_invalidate()` at
     the discard and full-clear sites.
   - **Not inside `shadow_invalidate()` itself:** forced text rendering
     calls it after other parts of the dashboard may already have painted
     (`display.c:2976`).
   - **Never on a reset after the queue completes** (codex's must-fix 1): a
     `gq_reset()` after the last glyph would otherwise forget a CAPS on the
     screen, and a Caps-off would then skip its clear.
4. **The Fn+D restore lets queued glyphs go first** (codex's must-fix 2 and
   verify finding 1). Today, while `debug_exit_step` is set, the pump runs a
   stage and returns without pumping (`display.c:1355, 2809`). The new
   order, each pass:
   - **`pass_deferred` stays above everything.**
   - **Stage 1 discards both queues** before its clear.
   - **After stage 1, a pending glyph wins:** if either queue holds one, the
     pump submits one, lock queue first, and returns.
   - **A restore stage runs only with both queues empty,** so a later stage
     never clears a label whose glyphs are still queued.
5. **Every place that asks "is the display idle" counts both queues:**
   - housekeeping's pending gate (`display.c:3182`);
   - `display_bus_idle()` (`display.c:2748`), which the PCF's and the deep
     sleep's admissions use;
   - `display_queue_discard()` (`display.c:2845`);
   - the wake repaint's admission and completion (`display.c:3111`,
     `sleep.c:245`).

   **So ordering holds in normal use.** A Caps-off clear waits for a queued
   CAPS to paint, and the clear itself waits for the DMA in flight
   (`lcd_bus.c:207`).
   - **No other band touches it:** the lock band (y 82-104) overlaps no
     text or clock band (codex checked).

**Expected:**
- **A Caps press:** the padlock appears on the tick, and CAPS a few passes
  later, ahead of any queued text.
- **The tick's pass:** the label's ~10 ms leaves it.

**Not claimed: a 25 ms guarantee for every pass.**
- **The animation's resume** still does a synchronous full-screen clear.
- **The normal PCF paths** keep their 20 ms timeout on a failing bus.

**Test, with a track playing** (`docs/display.md`):
- `scripts/fnb_caps_test.py`'s control, ~600 toggles;
- a toggle off while the label is still queued;
- SCR → FN;
- leaving Fn+D with several locks lit;
- a discard and repaint;
- a sleep's recovery;
- an animation's resume.

To pass:
- the right pixels after each;
- no pass of 25 ms or more;
- `blit_gap_max_ms` well under 22.

Alongside, for the whole build: the link's retries, give-ups and replaced
reports, and every waking key delivered.

## Build, review and order

1. **Codex's verify** of revision 2 is done (dispositions below); revision 3
   goes into the build without another plan round. The remaining risk is in
   the code, and codex reviews the diff.
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
4. **The A/B** (no hands), then the night in transaction mode.
5. **A second small flash: byte mode as the boot default,** if the A/B and
   the night pass. R5 starts on it, unless the A/B sends step 2 back.

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

## Codex's verify (10-19): dispositions

Its verdicts on the table above: 1, 7, 9, 10, 14, 15, 17, 19 and 20
closed; the rest partial or mostly closed, for the reasons below.

| # | finding | disposition |
|---|---|---|
| V1 | must: stage-and-glyph alternation can still clear a label whose glyphs are queued | **Taken.** Caps item 4: stage 1 discards both queues; after it, a pending glyph wins, and a stage runs only with both queues empty. |
| V2 | must: leaving `shown_*` false after a partial enqueue strands glyphs; text has no retry | **Taken.** Caps item 2: a label goes in whole or not at all, with a dirty flag forcing a reconcile; `queue_line()`'s shadow and `text_dirty` commit only after a full insert. |
| V3 | must: `display_bus_idle()`, `display_queue_discard()` and the wake repaint must count both queues; `locks_forget()` beside, not inside, `shadow_invalidate()` | **Taken.** Caps items 3 and 5. |
| V4 | must: the burst must select the short timeout and keep one transaction across the whole pass, `rtc_sleep_prepare()`'s read included | **Taken.** Step 1 item 3: an explicit timeout, `pcf_xfers_mark` across the pass, the reference check suppressed, and the deep sleep blocked while a burst runs. |
| V5 | must: the activity mask is context, not an exclusive cause; sleep is separate; a sub-1 ms maximum excludes nothing | **Taken.** Step 1 item 1: each case's timing stated; ring entries carry the USB and SysTick maxima; sleep's RX-disabled intervals counted apart; the exclusion rule removed. |
| V6 | should: CT16B5's boundaries, wrap, and sleep limits | **Taken.** Step 1 item 2. |
| V7 | should: exposure after a loss's 250 ms wait; the night after arm (d) measures the new rate, not the old | **Taken.** Exposure recorded per arm, each arm twice; the night runs in transaction mode, then byte mode becomes the boot default and R5 measures the rate after. |
| V8 | note: the stop/start repair is safe; use `&i2ccfg`; it repairs the state, not the bus; keep the failure's result | **Taken.** Step 2's repairs, as stated. |
| V9 | note: separate OE and refused-byte counts are feasible at the existing sites | **Taken.** Step 1 item 1; called OE observations, with the LLD's coverage gaps stated. |
| V10 | small: 64 + 10, not 74; the FIFO comment; the healthy-bus qualifier; one tainted echo, not one byte; the 60 s check cannot explain 22-28 s | **Taken.** All five. |
| V11 | closing: no universal 25 ms guarantee; validate retries, give-ups, replacement and the waking key | **Taken.** Part 2's "Not claimed" and its test. |

## Built (10-19, `27db633a44` on the local branch `mac/overrun-caps`, contrib `22596e61cd`)

One change from revision 3, simpler than V1's scheduling: **the Fn+D restore
never uses the lock queue.**
- **How:** `draw_locks_diff(one, queued)` paints at once in the restore
  (`queued` false), one component a pass, exactly as before. Only the 10 Hz
  normal path and the full repaint queue the label.
- **What that removes:** the pump runs no glyphs during the restore, and
  stage 1's discard empties both queues. So no stage can clear a label
  whose glyphs are still queued, and the pump needs no new order.
- **A label is all or nothing,** so the dirty flag is not needed. A refused
  label queues nothing and leaves `shown_*` as it was, so the panel still
  matches it, and the next tick's diff tries again. For the third slot, the
  clear sets `shown_sl3` to NULL before the label is offered.

Waiting on Gremlin's `4c5cb496` (the raw81 drain's 2 ms give-back) to land on
`ak820pro-jdlien`; this branch rebases onto it before codex's review of the
diff and the build.

## Codex's implementation review (10-19): dispositions

[`review-codex-overrun-caps-impl-2026-10-19.md`](review-codex-overrun-caps-impl-2026-10-19.md),
verbatim. It found the LLD's lock handling, the driver repair, the lock queue
and the Fn+D deviation correct.

| # | finding | disposition |
|---|---|---|
| 1 | must: the burst's last read drops `burst_active()`, so the reference check or the pre-sleep read could follow it in the same pass | **Taken** (`a6d0bbb` below): both skip a pass with `rtc_pass_used_bus()`, in every build (the check stays due; the sleep waits a pass). |
| 2 | must: `build.sh` is zsh, so the scalar symbol list iterated once | **Taken:** an array, `"${od_syms[@]}"`. |
| 3 | must: `mac_raw81_echo.py` lacked Gremlin's new op 17 fields (a `KeyError` before its JSON); the runner ignored a failed child | **Taken:** the Mac decoder carries `last_collect_ms`, `last_passes`, `max_passes`; a failed run or a missing report ends the arm. |
| 4 | must: the A/B ran a request count, not time; stopped an unfinished burst; started the burst before the load; idle time estimated; no cleanup | **Taken:** echo runs back to back by elapsed time until `--secs` and one whole run after the burst is seen done; the burst starts 3 s into the load; idle intervals recorded (between runs and each loss's wait); every arm stops any burst first; a `finally` stops the burst and restores per-transaction. |
| 5 | must (pre-existing): the animation's resume still clears the panel synchronously (43-44 ms), and ordinary PCF paths keep the 20 ms timeout | **Not taken in this build:** unchanged by it, and stated in part 2's "Not claimed". It goes to `BACKLOG.md` as its own item. |
| 6 | should: SysTick can slip a maximum between the OE entry's copy and clear | **Taken:** the entry is written under `chSysLockFromISR`. |
| 7 | should: OE timestamps stale (`ms_now` only refreshed by activities) | **Taken already** (`99efdd6784`): the RTC's seconds since boot, one volatile read. |
| 8 | should: the host's ring readers accept unwritten slots, never check sequences, and ignore the wrap | **Taken:** the expected sequence window, each slot checked; the OE ring re-read if it moved during the readout; modular arithmetic. |
| 9 | should: a line that lost a glyph is skipped by `retire_line()`; playback overwrote the clock's retry | **Taken:** a `dropped` mark clears the row whole on retirement; the clock's retry is a flag housekeeping turns into `clock_force_repaint`. |
| N1 | note: receiver-off count only, 16 bits on the wire | **Accepted** as is: the count; the sleep's own accounting has its seconds. |
| N2 | note: whole-call timing ends before the restart; the phase write's completion can pass the lateness check | **Accepted:** the A/B's comparison list (release and boundary errors, restarts, the daemon's residuals) is how it is judged. |

