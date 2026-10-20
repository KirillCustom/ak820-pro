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
