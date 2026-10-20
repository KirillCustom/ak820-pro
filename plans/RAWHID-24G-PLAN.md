# Raw HID over the 2.4 GHz dongle: a plan to review

Status: **draft for codex's review (10-19), nothing built.** The findings are
measured on gremlin's board and read out of the stock firmware; the design is
untested. Owner: the Gremlin session.

## What is known

**Measured** (RX tap, `GREMLIN-RESULTS.md` section 5): a 32-byte output report
written to the dongle's `MI_03` (`0C45:FDFD`, usage page `FF60`, usage `61`,
33-byte reports) reaches the SN32 from the CH582F as

```
81  <the 32 bytes verbatim>  <sum = (0x81 + Σ32) & 0xFF>        34 bytes
```

Two probes, both intact (`81 01 00.. 82`, `81 07 13 01 00.. 9c`). Our parser's
3-byte window drops them.

**Read out of the stock V1.13 image** (fpb's `StockFWBinaries`, base 0;
capstone, `GREMLIN-RESULTS.md` will carry the addresses):

- Stock frames the module's bytes **by silence**: the UART ISR counts bytes into
  `0x20002670`, a tick countdown (`0x186E4`) marks the frame complete when the
  line goes quiet, and the main loop dispatches on the count: 3 for
  `5A/5B/5C/ACK`, **34 (`0x22`) for `81`, and only in 2.4G mode** (mode byte
  `== 2`; `0xC890`).
- The `81` handler (`0x689C`): checks the sum over 33 bytes and the type,
  copies the 32 bytes to a request buffer, clears a 32-byte reply buffer,
  echoes the first three bytes into it, sets a reply flag, and dispatches on
  byte 0 (AJAZZ's own configurator protocol; e.g. command 2 returns 28 bytes
  of a flash page). The reply's byte 31 is the sum of bytes 0-30 (that
  protocol's checksum, not the transport's).
- The reply (`0xC3CC`): builds `91 <32-byte reply> <sum = (0x91 + Σ32)>`, 34
  bytes, and sends it through the same TX routine as the `A1` keyboard report
  (`0x1871A`). No `61 0D 0A` ACK is sent for an `81` on that path.

So raw HID runs both ways over 2.4G: **host → board `81`, board → host `91`**.
Not known: whether the module ACKs a `91` (our TX queue waits for an ACK per
frame and retransmits up to 8 times), the round-trip time, the loss rate,
whether a `91` reaches `MI_03`'s input report as the stock configurator
implies, and whether BLE has any equivalent (stock gates `81` on 2.4G).

## What it buys

Every raw-HID command the board answers on the cable, answered over the dongle:
the clock sync (`ak820-agent`'s transaction), now-playing text, health reads,
VIA. Against the lock-light channel's ~200 bit/s one way, this is 32-byte
reports with replies.

## Design

### 1. Receive: an `81` collector beside the 3-byte window

The 3-byte window stays as it is for `5A/5B/5C` and the ACK. A collector runs
on the same bytes:

- **Starts** on a byte `0x81` that arrives **at a frame boundary**: the window
  was just cleared by a matched frame or ACK, or the byte is the first of a
  `ch582_task()` drain after the input queue was found empty. A `0x81` inside
  another frame (a `5A` byte under the lock channel's P3 can be `0x81`) is
  never a start. Only while the module is linked **in 2.4G**, as stock gates
  it.
- **Collects** 33 more bytes. While it collects, its bytes are not fed to the
  3-byte window (they would form phantom frames: a payload byte `5C` followed
  by two bytes that happen to sum).
- **Commits** only if byte 33 equals `(0x81 + Σ bytes 1..32) & 0xFF`.
- **Aborts** on a bad sum, or when the next byte has not come within a bound
  (the drain finds the queue empty for more than N ms mid-frame; at the link's
  baud a 34-byte frame takes ~3 ms). An aborted frame's bytes are dropped and
  counted (a new `HC_LINK` field, `raw81_bad`), never replayed into the window:
  a lost `81` costs one request, which the host retries; replaying risks
  phantom `5x` frames.
- A false start that happens to sum (1 in 256 for a frame-shaped run that
  begins with `0x81` at a boundary) executes a request nobody sent. See 4.

### 2. Dispatch: the same handler as the cable

A committed `81` is handed to `raw_hid_receive()` (VIA's, which calls our
`via_custom_value_command_kb()`) from `ch582_task()`'s main-loop context, with
a flag set for the duration: "this request came over the radio". VIA edits the
32-byte buffer in place and ends with `raw_hid_send()`.

### 3. Reply: `raw_hid_send()` diverted while the flag is set

`raw_hid_send()` is core (`quantum/raw_hid.c`) and not weak. A seventh core
change, weak-hooked and a no-op for other boards: a
`raw_hid_send_kb(data, length)` hook that returns true when it has taken the
report. The board's hook, while the radio flag is set, queues `91 <32> <sum>`
on the CH582F TX queue and returns true, so the reply does not also go out
over USB (where every open handle on the host would see a stray report).

The TX queue's payload cap rises from 16 to 32 bytes (`TX_MAX_PAYLOAD`), which
grows every queue slot; or a separate one-slot buffer for `91`, sent through
the same pump. Whether the module ACKs `91` decides which: if it does not,
the reliable queue would retransmit it 8 times. **Stage 1 measures that.**

### 4. What the radio may do

A program on the host that opens the dongle's `MI_03` gets the board's raw HID,
the same as a program on the USB cable; the dongle's pairing is the trust
boundary. The board's raw HID includes VIA's keymap and EEPROM writes, our
`HC_BOOTLOADER` (behind a `BOOT` magic), and `ak820ctl`'s flash writes. And a
false start (section 1) could execute any of them. So **an allow-list on the
radio path in the first builds**: the text channel (`0x12`), health (`0x13`,
less `HC_BOOTLOADER` and the dev hook), and the RTC's get and set. Everything
else answers `unhandled` over the radio. VIA over the dongle is a later,
separate decision.

### Stages

1. **Echo (dev only).** A committed `81` is answered with `91` + the same 32
   bytes, no dispatch. Measures: does `91` reach `MI_03`'s input report; does
   the module ACK it; round-trip time; loss over a few thousand requests; UART
   overruns and stalls beside it. `scripts/` gains a host probe (the dongle's
   path from the Configuration Manager, never HID enumeration).
2. **Dispatch with the allow-list (dev only).** Health and text over the
   dongle; then the clock's GET/SET, with the transaction's timing measured
   against the cable's.
3. **The daemon's second transport,** when the cable is absent and the dongle
   is present. The one-clock-owner rule holds across transports: the radio
   path is the same owner's.

## Questions for the review

1. The collector's start rule: is "a `0x81` at a frame boundary, or first in a
   drain after an empty queue" enough against false starts, with the
   allow-list behind it? What else (a stricter gap rule, a magic in the
   payload) would cost little?
2. Feeding the window while collecting: dropped, as above, or fed in parallel?
3. The reply hook in core versus alternatives (VIA's in-place buffer read back
   after `raw_hid_receive()` returns, with USB send suppressed another way).
4. TX: grow the queue's slots or a separate `91` buffer, given the ACK unknown.
5. Anything in this that could cost a keystroke or stall the main loop: the
   dispatch runs our raw-HID handlers from `ch582_task()`, some of which (text,
   RTC set) do I2C or LCD work.

## Codex's plan review (10-19) and stage 1 as specified

[Verbatim](review-codex-rawhid-24g-plan-2026-10-19.md), prompt
[`review-prompt-rawhid-24g-plan.txt`](review-prompt-rawhid-24g-plan.txt).
Verdict: not ready as written; stage 1 buildable once its RX and TX must-fixes
are specified; dispatch a separate gate. The Mac session's notes (10-19) are
folded in where they match. Dispositions:

| # | finding | disposition |
|---|---|---|
| 1 | "First byte after an empty queue" is no boundary: `5C 25 81` (37%), `5A 27 81` and P3's `5A 81 ..` all put a `0x81` where a drain can split | **Taken.** Start eligibility is decided *before* the byte is consumed, from a `boundary` flag: set when the window is cleared by a matched frame or ACK, or by **measured silence** (below); cleared by any byte the window takes without a match. So `5C 25 \| 81` completes the 5C and never starts. Silence: a drain that finds the input queue empty at least `RAW81_SILENCE_MS` (5) after the last byte was read. The queue is filled by the UART ISR as bytes arrive, so an empty queue then means nothing arrived in that span, give or take the 16-byte FIFO's trigger and a masked ISR (both well under 5 ms; the PCF's ~2.9 ms is the known worst). Plus an inter-frame lifetime: a collection older than `RAW81_LIFETIME_MS` (15) aborts. |
| 2 | An overrun can produce a valid wrong command (codex's constructed `TEXT_SET` -> `TEXT_CLEAR`); errors are only counted at the drain's start; an aborted tail can forge `5B 32 8D` | **Taken.** A UART error flag seen during a collection, at the drain's start or polled again just before a commit, aborts it. Any abort enters **quarantine**: every byte is dropped (counted) until the line has been silent `RAW81_SILENCE_MS`, so no tail reaches the window. The cost codex names is real and accepted: short frames in that burst are lost too (counted). False-commit frequency is unknown; **stage 1 only echoes**, so a false commit costs nothing but a stray reply, and stage 2 is gated on stronger end-to-end integrity (a sequence and a CRC inside the payload) before anything changes state. |
| 3 | The allow-list must be positive, granular and upstream of VIA; `HC_STALL` mode 2 writes flash; counter resets are not reads | **Taken, for stage 2.** Stage 1 dispatches nothing. |
| 4 | Bound dispatch separately; legacy RTC SET can take 20 ms of I2C | **Taken, for stage 2.** |
| 5 | TX: a 34-byte frame in a 16-byte queue; no-ACK means 8 retries (~90 ms) at the head; ACKs carry no sequence, so a late `91` ACK could release an A1; `sdWrite` must be frame-atomic | **Taken.** A **separate one-slot `91` buffer**, sent only when the keyboard queue is empty and nothing is in flight (keys first), never retried, never coalesced (the Mac session's point), with a counter for a reply dropped because the slot was full. After a `91` goes out, the A-frame pump waits `RAW91_ACK_WINDOW_MS` (15) so an ACK in that window is counted as the `91`'s and cannot pop an A1; this is what measures whether the module ACKs `91`. `sdWrite` is the same blocking write every frame uses (a single thread; 34 bytes through the 16-byte output queue block ~1.6 ms at most, bounded by the UART). |
| 6 | Request ownership; `ch582_tx_drain()` recurses into `ch582_task()`; reset on resync, sleep, link loss; recognised raw frames parsed and discarded even when dispatch is forbidden | **Taken.** The collector runs in every link state and mode (a `81` is always consumed whole, so its payload never reaches the window); only the echo is gated on 2.4G and linked, checked again when sending. Collector, quarantine and the reply slot reset on `rx_resync`, a sleep's wake and a link loss; `ch582_tx_idle()` includes the slot and the ACK window, so sleep admission waits for them. Stage 1 has no dispatch, so the drain's recursion cannot nest a handler. |
| 7 | The reply abstraction: the core hook is reasonable for VIA later; a board-level dispatcher with explicit reply ownership avoids the core change for stage 2's restricted set | **Agreed; decided at stage 2.** Stage 1 needs neither. |
| 8 | Observability: `HC_LINK` page 0 has 2 spare bytes; separate counters; explicit stage exits | **Taken.** Stage 1's counters live on a dev-hook page of their own (`HC_DEVHOOK` op 17): commits, bad sums, lifetime aborts, UART-taint aborts, quarantined bytes, `91`s sent, replies dropped, ACKs inside the window. The stage exits are adopted as codex wrote them (its finding 8). |

Stage 1 is dev-only (`AK820_DEV_HOOKS`), like the tap.

## Stage 1 built and reviewed (10-19)

`gremlin/raw81` at `7387d189` (on the RX tap's `4ee56290`):
[review](review-codex-raw81-stage1-2026-10-19.md) of `5dbca738` (1 must-fix,
3 should-fix) and [verify](review-codex-raw81-stage1-verify-2026-10-19.md) of
the fixes, both verbatim; prompts beside them.

| finding | disposition |
|---|---|
| Must-fix 1: the 3-byte window survived a collection and a quarantine (a pending `61 0D`, a whole `81`, then `0A` forged an ACK that could release an A1) | **Fixed:** the window starts over at a collection's start and at a quarantine's end. Verified closed. |
| Should-fix 2: a UART error seen while idle was forgotten, so a corrupt burst's `81` could start clean | **Fixed:** `r81_tainted`, set by any drain-start error and cleared only by silence or a reset, sends the next `81` to quarantine. Verified closed; its note (the commit-time poll aborts without setting the flag) is behaviourally safe, since quarantine holds until silence. |
| Should-fix 3: the lifetime was checked only before a drain | **Fixed:** checked again at commit. Verified closed. |
| Should-fix 4: no reset on link changes | **Fixed:** `LOCKCHAN_LINK_LOST()` (all ten sites) also drops the pending echo and quarantines a collection. Verified closed. |
| Notes: the unbounded `sdWrite`; ACK attribution by the window's flag; an ACK after the window closes could still pop an A1 | **Accepted** as limits of a dev experiment; the echo test measures the ACK timing that decides them. |

The daily build carries none of it: no new symbols, and the same text, data
and bss as `de5450e487`'s daily. **Verdict: nothing blocks flashing it for
the stage-1 measurements.**

## BLE has no equivalent (the Mac session, 10-19)

Read in BT mode on the Mac (`docs/wireless.md`, "The GATT services over BLE";
`history/ble-gatt-20261019/`): besides HID, the module offers 180A (WCH's
unfilled example strings; software 2024.07.26 SVN0138, PnP 0C45:FEFE), 180F,
1813, and **FEE0 with one characteristic FEE1 (read, write, write without
response): WCH's CH58x OTA profile, the module's own firmware update. Never
write FEE1.** So raw HID over the dongle is the only two-way wireless path;
over BLE the lock-LED report stays the only host-to-board one.

## Stage 2: dispatch, as designed (10-19; for codex before it is built)

Stage 1's echo works from the Mac (429/430; `history/raw81-echo-20261019-mac/`)
and its one cost, a 13-15 ms pass per echo, has a fix in review
(`gremlin/raw81`, the drain bound). Stage 2 makes the board *act* on a
request over the dongle, under the conditions codex's plan review set
(findings 2, 3, 4, 6, 7): end-to-end integrity before anything changes
state, a positive allow-list upstream of VIA, bounded dispatch, and explicit
reply ownership. Dev builds only, like stage 1.

### The request envelope (inside the 81's 32 bytes)

```
[0]    0xC7          magic: never a first byte any firmware's raw HID answers
                     (VIA's ids are 0x01-0x15; ours ride on 0x07)
[1]    seq           the host's request number, mod 256
[2..3] CRC-16        CCITT-FALSE (poly 0x1021, init 0xFFFF), big-endian, over
                     [0..1] and [4..31]
[4..31] inner        the ordinary 28-byte command, e.g. 07 13 01 (health get)
```

The board dispatches `inner` as a 32-byte buffer (inner, then 4 zero bytes,
length 32), exactly as a USB report of the same command would arrive. Every
command on the allow-list fits in 28 bytes (a text line is at most 26; the
health reads at most 6). The CRC is what the transport's 1-byte sum is not:
codex's constructed case (one byte lost under an overrun turns `TEXT_SET`
into a valid `TEXT_CLEAR` with a matching sum) fails it with probability
2^-16 rather than 1 in 1.

### The reply

The handler edits the 32-byte buffer in place, as it does on USB, and the
whole buffer goes back as a `91`: the reply is byte for byte the USB reply.
Replies carry no envelope: the health replies fill all 32 bytes, the board's
TX is not where bytes were lost (overruns are an RX phenomenon), and the radio
and the USB legs below the module are CRC-protected. A request that is not
dispatched gets a **NAK** instead, so the host learns at once rather than by
timeout:

```
[0] 0xC7  [1] seq as received  [2] reason: 1 bad CRC, 2 not on the
          allow-list, 3 busy (a reply still waiting), 4 not 2.4G-linked
[3..31] 0
```

A NAK's first byte (`0xC7`) can never begin a real reply (those begin with
`0x07` or VIA's `0xFF` unhandled).

**Correlation, host side:** one request in flight per host; before sending,
drain whatever input reports are waiting (Windows delivers to every open
handle); accept only a reply whose first three bytes equal the request's inner
`[0..2]` (and a health page byte where the command has one), or a NAK with the
request's seq. A stale reply to an earlier request of the *same* command could
still pass; for the stage-2 set that is harmless (a text echo, a health read
a few ms old). The clock is not on the list for exactly this reason: its GET
reply fills all 32 bytes, so a stale one cannot be told from a fresh one. It
is stage 3, with a radio clock command whose reply carries the seq.

### The allow-list (positive, exact, checked before dispatch)

| inner | what | why safe |
|---|---|---|
| `07 12 01`, `07 12 03`, `07 12 04`, `07 12 02` | text set, set line, playback, clear | RAM and dirty flags only; the display draws later through the glyph queue (codex plan review, 4) |
| `07 13 01`, `07 13 02`, `07 13 04`, `07 13 06`, `07 13 07` | health get, conn, get2, get3, get4 | pure reads |
| `07 13 0E <page>` | HC_LINK pages | pure reads |

Everything else, VIA's own commands, the RTC, flash provisioning, every
health command that resets, configures, injects, stalls or jumps
(`HC_RESET`, `HC_BATTCFG`, `HC_ACCT`'s ops, `HC_BOOTLOADER`, `HC_DEVHOOK`
and the instrumented ones), NAKs with reason 2. The check matches whole
bytes, not prefixes.

### Dispatch

- **Where:** `ch582_task()`, after the drain and the TX pump, **at most one
  request per pass**, from a one-slot request buffer filled at commit. A
  commit with the slot full NAKs busy.
- **How:** `via_custom_value_command_kb(buf, 32)` directly: the board's own
  entry for its channels (`0x07` `id_custom_set_value` reaches it through VIA
  today). No core hook and no `raw_hid_send()`: the reply is read out of
  `buf` and queued as a `91`, so it can never leave over USB, and a USB
  request can never reply over the radio (codex plan review, 7).
- **Reentrancy:** `ch582_tx_drain()` (the USB bootloader path) calls
  `ch582_task()` from inside a USB raw-HID handler; a depth guard skips radio
  dispatch when `ch582_task()` is entered recursively, so no handler nests in
  another (codex plan review, 6).
- **Budget:** the allow-listed handlers are RAM work; the pass is measured
  (`HC_ACCT`, `count_ge_10ms`, `count_ge_25ms`) under a request stream with
  and without USB traffic beside it.

### Stage 2's exit (codex plan review, 8)

Text and health round trips over the dongle from Windows and macOS (the Mac
session); every NAK reason provoked; forbidden commands shown to have no
effect; simultaneous USB and radio requests correctly routed; under a typing
burst no new pass >= 25 ms and no A1 drop, replacement or give-up; and a
sustained request stream's loss and latency.

## The drain bound (10-19): `ea73efb2`

Every echo cost one 13-15 ms pass, 8-9 ms of it in the RX drain (the Mac
session's `HC_ACCT` attribution, `history/raw81-echo-20261019-mac/`). The
fix (`4c5cb496`): mid-collection the drain stops after `RAW81_DRAIN_MS` (2),
the lifetime rises to 40 ms to span the passes, and op 17 page 1 reports the
last commit's ms and passes. [Review](review-codex-raw81-drain-2026-10-19.md)
and [verify](review-codex-raw81-drain-verify-2026-10-19.md), verbatim:

| finding | disposition |
|---|---|
| Must-fix: a partial drain left an A-frame's ACK queued while the pump could retransmit, close the 91's window or advance (the documented keystroke-loss path) | **Fixed in `ea73efb2`:** a drain stopped by its budget sets `rx_left` and the pump skips that pass. Verified closed. |
| Note: the comments called 8-9 ms the module's delivery time; the measurement was the drain's | **Fixed** in the same commit. |
| Note: 40 ms extends a false start's damage; 128 bytes of input queue cover ~11 ms; silence survives the new scheduling | **Accepted** for stage 1 (one request at a time). |
| Verify, should-fix: under a continuous request stream `rx_left` can recur pass after pass with no global bound on a keyboard frame's wait | **Taken for stage 2:** a bound on consecutive skipped pumps (after it, one full drain to the queue's end, then the pump), and stage 2's one-dispatch-per-pass. Stage 1's echo, one request at a time, cannot produce the pattern. |
| Verify, note: the 64-byte cap still exits without `rx_left` (the existing limitation) | **Accepted;** recorded, unchanged from before stage 1. |

## The real cause, and stage 1 passing (10-19, gremlin)

`ea73efb2` on gremlin echoed **0 of 10**: every collection hit its 40 ms
lifetime, though the RX tap showed all 34 bytes read. The cause of both that
and 7387d189's 8-9 ms drain: stage 1 stamped each byte with `timer_read()`,
and the budget added a `timer_elapsed()` per iteration. QMK's `timer_read32()`
converts with `TIME_I2MS`, which on the M0 is `__aeabi_lmul` +
`__aeabi_uldivmod` in software (the disassembly), ~250 us a byte at the row
ISR's load. **Fixed in `a42a317e`/`79245590`:** both use the 16-bit system
tick (`chVTGetSystemTimeX`, `chTimeDiffX`, compile-time `TIME_MS2I`: 375 and
938 ticks at 187.5 kHz). [Review](review-codex-raw81-ticks-2026-10-19.md),
verbatim: no must-fix; the getter is a single CT16B5 register read; no
per-byte ms conversion remains; a stale stamp only under-measures silence
(its note that "~5 ms late" bounds the alias window, not recovery, is taken).

**Measured on gremlin, `79245590` (Windows, `win_raw81_echo.py`,
`history/raw81-echo-20261019-gremlin/`): 430 of 430.**

| run | echoed | RTT median | p95 | max | passes >= 10 ms added |
|---|---|---|---|---|---|
| 10 at 20 ms | 10/10 | 17.6 | 19.9 | 19.9 | 0 |
| 100 at 20 ms | 100/100 | 17.9 | 20.7 | 23.1 | 1 |
| 300 back to back | 300/300 | 17.0 | 21.0 | 24.0 | 3 |
| 20 at 100 ms | 20/20 | 17.8 | 20.6 | 20.6 | 1 |

Each 81 now takes 6-7 ms over 2 passes (the module's own pacing). No pass
>= 25 ms, no UART overrun, no A1 give-up, replacement or queue-full. **No ACK
was ever counted in a 91's window: the module does not ACK 91.** The one
open item from the drain-bound verify, TX fairness under a continuous stream,
belongs to stage 2.

**A board-wide follow-up (proposal, held until the Mac session's overrun
A/B and its night baseline are done):** 142 `timer_read*`/`timer_elapsed*`
call sites, many per pass, each paying the same software divide. Fix once in
`platforms/chibios/timer.c` (32-bit, carrying the sub-ms remainder over the
16-bit tick's increments), after measuring one call's cost and the calls per
pass; codex to check the stall counters' 25 ms arithmetic, which runs through
it.

### Stage 2 addendum: TX fairness (from the drain-bound verify)

A drain stopped by its budget defers the TX pump to the next pass
(`rx_left`). Under a steady request stream that could recur pass after pass.
Stage 2 bounds it: after `RAW81_MAX_SKIPS` (2) consecutive skipped pumps, the
next drain ignores the budget and reads to the queue's end (at most the 128
bytes queued, now ~microseconds a byte), and the pump runs. With the tick fix
a collection already takes 2 passes and the budget rarely binds; the bound
is the guarantee, not the common path.

## Stage 2, revision 2: after codex's plan review (10-19)

[Review](review-codex-rawhid-stage2-plan-2026-10-19.md), verbatim (prompt
beside it): not ready unchanged; ready to build as a dev experiment once its
must-fixes are in. Revision 2 replaces the stage-2 sections above where they
differ.

### What changes

**1. No drain budget (must-fix 1).** The budget existed because reading an
81 took 8-9 ms; that was `timer_read()` per byte, and with ticks a byte costs
microseconds (an 81 now takes 6-7 ms of the *module's* pacing, over 2 passes,
with the drain reaching an empty queue each time). So `RAW81_DRAIN_MS`,
`rx_left` and the skipped pump go: the drain is back to its pre-stage-1
shape (empty queue or the 64-byte cap), and the TX pump runs every pass as it
always did. The 64-byte cap's old limit (codex, drain verify) is unchanged
and pre-dates all of this. `RAW81_LIFETIME_MS` returns to 20 (3x the
measured 6-7 ms; codex's note that 40 extends a false start's damage).

**2. Dispatch never nests (must-fix 2).** `ch582_tx_drain()` sets a flag for
its whole loop, and dispatch runs only when it is clear and `ch582_task()` is
at depth 0. A request stays in its slot until a top-level pass.

**3. One response slot, reserved before anything runs (must-fixes 3, 4).**
A committed, CRC-valid request is admitted only if both the request slot and
the response slot are free; otherwise it is dropped and counted (the host
retries on its timeout). Dispatch writes the response into the reserved slot,
so a reply can never be overwritten and nothing is written to the UART from
the RX path. A request that cannot be answered (the link not 2.4G and up) is
dropped and counted, not NAK'd: there is no path to say so. Both slots are
cleared by `r81_reset()` (resync, wake) and `r81_link_lost()` (every link
change), eligibility (2.4G, linked) is checked again before dispatch and
before sending, and `ch582_tx_idle()` counts both for sleep admission.

**4. Integrity failure quarantines (must-fix 5).** A collection commits to
the protocol only with a good outer sum, magic `0xC7` and a good CRC-16.
An outer-sum-valid collection that fails the magic or the CRC is treated as
corruption: the line goes to quarantine (no trusted boundary, no tail into
the window), counted, and a NAK "bad CRC" is queued if the response slot is
free (its seq is as received, and may itself be wrong). A CRC-valid request
that is not on the allow-list is a well-formed frame: boundary kept, NAK
"not allowed". Stage 1's echo is retired.

**5. Every response is two reports: the reply, then a receipt (must-fix 6,
note 10).**

```
report 1 (only for status 0): the handler's 32-byte reply, verbatim
report 2, the receipt:
  [0] 0xC7  [1] seq  [2] status: 0 ok, 1 bad CRC, 2 not allowed, 3 stale seq
  [3] the board's last accepted seq  [4..5] CRC-16 of report 1 (status 0)
  [6..31] 0
```

Both go as `91` frames from the one response slot, back to back. The host
accepts a reply only when the receipt that follows it carries the request's
seq and that reply's CRC: a delayed reply to an older request is followed by
the older seq and is discarded, whatever its header says; two lines of text
can no longer be confused; and the CRC covers the board's own TX leg, which
the radio's and USB's CRCs do not.

**6. Sequence freshness on the board (must-fix 6).** The board keeps the
last accepted seq (none after a reset or link change). A request is fresh if
`(seq - last) mod 256` is 1..127, or if there is no last; otherwise status 3,
stale, with the board's last seq in byte 3 so the host continues from
`last + 1`. A host never reuses a seq: a retry is a new request with a new
seq. So a radio-level duplicate is rejected as stale instead of executed
twice, and a replayed old text cannot overwrite newer state.

**7. HC_LINK pages exactly 0 and 0x21-0x2A (should-fix 7).** The receipt
does the correlation, so the header-match quirks (page 0 answers with the
protocol version in byte 3) no longer matter to the host.

**8. The collector and the envelope as a pure module (should-fix 8).**
`raw81.c` holds the collector (boundary, silence, lifetime, quarantine, taint),
the envelope check, the freshness rule and the allow-list, as functions of
bytes, ticks and flags with no hardware calls; `ch582f_ajazz.c` feeds it and
acts on its verdicts. `scripts/raw81_test.py` compiles it on the host (as
`lockchan_test.py` does `lockchan.c`) and runs the cases codex listed: every
split of a frame across drains, a deleted byte at every position, truncation,
an 81 inside a 5A payload, `5C 25 | 81`, a pending `61 0D` across a frame,
CRC-valid forbidden commands, stale and wrapped seqs, back-to-back requests
with the slots full.

**9. The reply window goes (note 10).** No ACK was counted in any 91's window
over 430 echoes: the module does not ACK 91. Holding the A-frame pump 15 ms
after every response delays keys for nothing, so the window is removed; an
ACK arriving with nothing in flight is counted as an orphan (as the console
build already does), which would show it if the module ever did.

**10. VIA builds only (note 9).** Dispatch goes through
`via_custom_value_command_kb()`, which exists only with `VIA_ENABLE`; the
radio path compiles out without it.

### The acceptance matrix (should-fix 8)

On the host, `raw81_test.py`: the cases in 8, all passing, and planted bugs
caught (as lockchan's suite did).

On gremlin (Windows, no hands): text set/line/playback/clear over the dongle,
each read back over USB (the band's state through health or a text echo);
every health read compared with the same read over USB; every NAK and status
provoked (bad CRC by a corrupted request, forbidden commands, a stale seq, the
slots full); a stream at full rate with USB raw-HID requests interleaved,
each reply on its own transport; loss and latency at full rate (p95 under
30 ms, losses 0 in 1,000); no pass >= 25 ms and no A1 drop, replacement or
give-up throughout; fresh counter baselines for each run.

On the Mac (the Mac session's rig, no hands): the same protocol from macOS;
a relay-driven typing burst under a full-rate request stream, every press
and release seen at the host; a power cut with a request in flight, then
recovery with no stale answer; deep sleep with requests arriving, answered
after a key wake and never before.
