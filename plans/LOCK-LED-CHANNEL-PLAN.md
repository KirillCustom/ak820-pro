# The lock-LED channel: a design to review

Status: **approved by JD and being built (10-18).** The wire format below is
version 0: the board's receiver and the reference encoder exist and are tested
on the host; nothing on the board acts on frames yet. The research, with every
measurement, is `plans/BACKLOG.md`, "Community", quill4gen7 item 1.

## What it is for

Clock sync and now-playing text from the host to the board **without the
cable**. With the cable both go over raw HID (`ak820-agent/`, the Rust daemon,
on macOS and Windows), and that stays the precise path. Over Bluetooth (and
2.4G) the only host-to-board path is the keyboard's LED output report: the
CH582F module forwards it to the SN32 as a UART frame `5A <leds> <sum>`. So
the host writes LED bytes and the board decodes them.

The messages are small: a title and an artist (the LCD's text slot holds ~30
characters), a clock time, maybe a short status. Updates are rare: a track
change every few minutes, a clock sync now and then.

## What is measured (all on JD's Mac and board, 10-17/18)

- **The BLE descriptor's only output is report 2's LED byte**, declared as
  5 bits (Num, Caps, Scroll, Compose, Kana) plus 3 padding bits. No feature
  reports. macOS makes elements for Num, Caps and Scroll only, but a raw
  `IOHIDDeviceSetReport` sends the whole byte.
- **Link 2 proven:** the Mac sets the board's Scroll Lock over Bluetooth (seen
  on the LCD).
- **Speed:** each write is acknowledged. Back to back, ~30 ms per write
  (median 29.9, 24-45), ~33 writes/s, after a ~236 ms wake-up of an idle link.
  Typing removes only the wake-up. **Writes issued at once without waiting
  complete at the same 32/s**: the link, not the Mac's calls, sets the pace
  (two connection events per write at a ~15 ms interval is an inference).
- **Mac side, repeats are sent** (a repeated value takes a full ~30 ms write),
  and **bits 3-4 are sent** (0x08, 0x10, 0x18 each take a full write).
- **The Mac's own LED report for a Caps press kept Scroll Lock** set by the
  host. Not tested for bits 3-4.
- **Locked, the Mac refuses every write** (`kIOReturnNotPermitted`): no
  channel while the session is locked; resync on unlock.
- **The board** accepts a `5A` only while linked and only if `d <= 0x1F`; in BT
  mode it ignores USB LED reports. Its 5A parser and everything a decoder would
  touch run in the main loop. One main-loop pass >= 25 ms (the project's hard
  line: below it no keypress can be lost) came during a 33/s Scroll Lock burst
  plus two Caps presses on 10-17; flash 2g shows Scroll Lock on the LCD only
  once it has settled 1.5 s.

## Unknown until flash 2g's tests (`scripts/ledchannel_test.py`)

1. Which of the five bits the module forwards (width).
2. Whether it forwards padding bits 5-7 (high).
3. Whether a repeated value is forwarded (repeats).
4. Whether writes ~30-70 ms apart, or issued at once, arrive as separate
   frames or merge (speed).
5. The error rate at full speed (random: 44 random values over four bits).
6. Whether the Mac's LED report for a Caps press keeps bits 3-4 (caps).

## Constraints

- **Caps Lock belongs to the user.** It changes typing, so the channel never
  writes it, and the decoder must survive the Mac rewriting the LED byte when
  the user presses Caps mid-frame.
- **The user's real lights come back after every frame.** The board's settled
  Scroll Lock hides a frame only if the frame never leaves Scroll Lock changed
  for 1.5 s at a stretch, pauses and retries included.
- **Human lock presses must never decode as a frame.** On a Mac a person
  cannot touch bits 3-4 at all; Num and Scroll can come from another keyboard.
- **One way.** The board has no reply path over the air that we know of (its
  frames to the module are A1 keyboard, A3 consumer, A6 profile).
- **The board's decoder is cheap:** main-loop context, a Cortex-M0 at 48 MHz
  with ~72% of the CPU already in the row ISR, no LCD drawing in the decoder,
  bounded work per frame.
- **Windows** (unproven, from memory): `HidD_SetOutputReport` on a keyboard
  collection is probably refused; `IOCTL_KEYBOARD_SET_INDICATORS` is untested;
  the last resort is toggling the real Num and Scroll Lock (global state, so
  only while the user is idle, restored after). That gives 2 data lights, not 4.
- **2.4G** goes through a USB dongle on the host; quill4gen7 measured 42/42
  frames intact at 8-40 ms a bit there. Untested here.

## quill4gen7's scheme, for comparison

Num toggle = 0, Scroll toggle = 1: one bit per write, ~10 ms a bit on 2.4G,
CRC-8, every frame sent twice. Linux host only.

## The draft design

**Symbols.** Four data lights: Num `0x01`, Scroll `0x04`, bit 3 `0x08`, bit 4
`0x10`; Caps never. A symbol is a change: `next = current XOR mask`, mask one
of the 15 non-empty subsets of the four, so one base-15 digit (~3.9 bits) per
write. The decoder ignores any frame whose data bits did not change: repeats
(if the module forwards them) and the Mac's Caps rewrites both fall out.

**Alternative if tests 3 and 6 pass: plain values.** Each write is a 4-bit
nibble, all 16 values; a byte is two writes. The decoder skips a frame whose
Caps bit changed (the Mac's Caps rewrite) and takes every other frame,
repeats included.

**Packing (change coding).** The frame's bytes as one base-15 number: the
board multiplies its buffer by 15 and adds the digit, O(n) per symbol over
<= 64 bytes. 32 bytes take 66 symbols.

**Frame:** `[sync: 2 symbols][header: 3 symbols][payload][CRC-16][restore]`.
- Sync: two fixed masks that both flip bits 3-4 (a person cannot, on a Mac).
- Header: 3 base-15 digits (15^3 = 3375 > 2^11): a 4-bit type and a 7-bit
  payload length, so the board knows how many symbols follow.
- A gap of more than ~150 ms between changes ends the frame (writes land every
  ~30 ms; human presses come seconds apart).
- CRC-16 over header and payload; a bad frame is dropped whole.
- The host's last write restores the user's real lights.

**Errors.** Bluetooth's link layer already retransmits corrupted packets, so
what reaches the module is right; what can go wrong after is a lost frame (UART
overrun, a long stall), a merge (two writes coalesced) or an interfering Mac
report. Those are deletions, which shift every later symbol, not
substitutions. So: CRC-16 to detect, and no duplicate frames. Content is
state (the time, the track), so a lost frame waits for the next send; an
important one goes again ~2 s later (spaced, since losses come in bursts).
Forward error correction only if the tests show losses: then Reed-Solomon over
GF(16) (e.g. RS(15,11), 4 check nibbles) with the board turning timing gaps
(~60 ms where ~30 ms is normal) into erasures at known positions. That fits the
plain-value alphabet; change coding's 15 symbols do not map onto GF(16).

**Clock.** One symbol is the timing edge: the host notes when its write
completed, the board when it arrived; ~±20 ms is the guess (not measured),
against ~3 ms on the cable.

**Expected rate**, at ~32 writes/s: 4 lights ~125 bit/s, a 30-character title
(a 34-byte frame) in ~2.5 s; 2 lights (Num and Scroll, Windows) base 3, ~50
bit/s, ~5.5 s.

**Where it lives:** the Rust daemon (`ak820-agent/`), as a second transport for
the messages it already sends over raw HID, used when the cable is absent; the
board's decoder beside the 5A parser.

## Measured on the board, flash 2h (12:20-12:30 10-18)

`scripts/ledchannel_test.py`, all seven tests, Mac to board over Bluetooth;
results in `history/ledchannel-2026-10-18/`.

- **The module sends every change three times**, 0-5 ms apart (96 frames for
  32 writes). Harmless to change coding (copies are repeats), and a lost copy
  costs nothing.
- **Width: all five LED bits arrive** (0x01-0x1F exact).
- **High: the padding bits 5-7 arrive too** (0x20, 0x40, 0x80, 0xE0, 0x24
  exact). The board's `d <= 0x1F` guard keeps them out of QMK's LED state (only
  the 0x00s were accepted); the receiver gets them raw. So the module passes
  the whole byte.
- **Repeats do not arrive**: 18 writes (12 of them repeats) gave 6 changes x 3
  copies. Change coding it is; plain values are out.
- **Speed: no merging at any spacing**, back to back or issued at once; one
  write per ~30 ms, every one delivered. A pause of even 10 ms between writes
  makes each write take ~150 ms (the link slows when idle), so a frame's writes
  go back to back.
- **Random: 44 of 44 exact** at full speed (132 frames, 3 per value).
- **Caps: the Mac's own Caps report zeroes bits 3-4** (0x18, then 0x02, then
  0x00, and not restored). Aborting a frame on a Caps change is required.
- **Frames: 17 of 18 committed exactly** (P2 6/6, P0 6/6, P1 5/6). The miss:
  one write took 366 ms mid-frame (write 34 of 109), past the 150 ms gap limit.
  Every other slow write (130-376 ms) was a frame's first, the link waking,
  which the gap does not time. Anchor to commit: P2 1.9-2.9 s, P1 2.5 s, P0
  4.4-6.5 s for 20-37-byte texts. (The test's count check cascaded after the
  miss: fixed, it compares each frame's own delta.)
- **Stalls:** one pass >= 25 ms, in the width test, the only test that toggles
  Caps (the band's CAPS paint, ~2 a second); none in the 200-toggle Scroll
  bursts, the frames or anything else; the 10-17 stall's window also held Caps
  presses. So the Caps paint is the suspect, not the channel (an inference).
  UART overruns +7 during the frames; harmless with three copies, still a
  trend to watch.

**What changes:**
1. **The gap limit goes from 150 to 500 ms**, to ride out a Bluetooth hiccup
   (366 ms seen); a lost write is still the CRC's to catch.
2. **A new profile, P3: bits 3-7, base 31** (29 bits in 6 digits, ~4.8 bits a
   write, ~155 bit/s): five data bits with Num, Caps and Scroll never touched,
   so no light changes at all and no Scroll rule. The candidate default.
3. The Caps paint's stall goes to the display work (`Fn`+`D`'s probe, a track
   playing).

## Measured on the board, flash 2i (15:18-15:25 10-18)

`ledchannel_test.py frames`, results in `history/ledchannel-2026-10-18-2i/`:
**24 of 24 frames committed exactly**, six per profile, 20-37-byte texts:

| | writes | host send | anchor to commit (board) |
|---|---|---|---|
| **P3** | 52-74 | 1.85-2.75 s | 1484-2217 ms |
| P1 | 83-129 | 2.76-4.09 s | 2413-3777 ms |
| P2 | 67-96 | 2.31-3.08 s | 1933-2833 ms |
| P0 | 148-188 | 4.62-5.82 s | 4383-5579 ms |

No pass >= 25 ms (`count_ge_25ms` 0 throughout, from the flash), UART
overruns +3 in all. No write in this run went past the old 150 ms gap, so the
500 ms limit was not exercised on the board, only in the host tests.

**Where the channel stands:** P3 works end to end on the Mac over Bluetooth
and moves no light. **Parked here (10-18)** while the power work comes first.
Left for when it resumes, in order:
1. ~~Codex's 2i should-fixes for the host tests~~ **done 10-19**
   (`lockchan_test.py`): valid-CRC headers announcing forbidden profiles,
   versions and lengths (each one header abort and nothing else, then the
   next frame decodes; length 48 commits); blocks exactly at and below the
   range limit, full and partial, in the header and the payload, and the
   largest value the digits hold; falling-edge starts for both high
   preambles; a stray transition 300 ms before a frame (decodes) and 100 ms
   before (may cost the frame, never a wrong one); unused-bit-only and
   repeated reports in a stall (resumed at 480 ms the frame decodes, at
   520 ms it is a gap abort); the golden writes fed into the C; content
   checked in the boundary tests. To show the tests bite, eight bugs were
   planted one at a time in a scratch copy of the receiver (an unused bit
   extending the deadline, P1 announcing P3, any version, length 49, the
   range check one high, a falling-edge start, the old hold, no hold
   through a P1 header): each failed the suite. The board test's initial
   wait (codex 2i, should-fix 6) was already in `ledchannel_test.py`.
2. ~~The Scroll hold only for frames that can carry Scroll~~ **built 10-19,
   not flashed** (firmware `442c9f49c9`, local): `lockchan_holds_scroll()`
   holds for P0 and P2 throughout and for a bits 3-4 preamble and header
   (they may announce P2), never for P1's payload or for P3; both of the
   band's uses (the settle hold, the own-key shortcut) take it, and
   `lockchan_busy()` stays for the devhook. Tested on the host
   (`test_scroll_hold`); a codex review before it goes on a flash.
3. A Caps change while padding bits are set: does the Mac's own Caps report
   keep bits 5-7 (the board's guard would then drop it, and the band would
   miss the change)? Needs the probe's `capsmix` to take a value.
4. Then the daemon's sender (codex 2i, note 3): serialized write completions;
   the gap's wait counted from the last completion, with margin, and already
   elapsed idle time counts; coalesce to the latest state and resend it now
   and then, since nothing acknowledges; a clock frame's time refers to the
   anchor.

## The wire format (version 0, built 10-18)

Approved by JD 10-18 after codex's review (below). Flash 2i (10-18) added P3,
raised the gap to 500 ms and made the preamble's spacing the quiet (150 ms);
the board results above are flash 2h's. The receiver is
`lockchan.c` in the board directory (dev builds only for now: frames are
decoded and counted, `HC_DEVHOOK` ops 10-13 read them, nothing acts on them
yet); the reference encoder is `scripts/lockchan.py`; `scripts/lockchan_test.py`
compiles the receiver on the Mac and checks the two against each other.

**Profiles** (which LED bits carry data; Caps `0x02` never does):

| | data bits | base | block | rate at 32 writes/s |
|---|---|---|---|---|
| P0 | Num `0x01`, Scroll `0x04` | 3 | 19 bits in 12 digits | ~51 bit/s |
| P1 (default until P3 passes) | Num, bit 3 `0x08`, bit 4 `0x10` | 7 | 14 bits in 5 digits | ~90 bit/s |
| P2 | Num, Scroll, bits 3-4 | 15 | 31 bits in 8 digits | ~124 bit/s |
| **P3 (candidate default, 2i)** | bits 3-7 `0x08`-`0x80` | 31 | 29 bits in 6 digits | ~155 bit/s |

P3 carries data in bits 3-4 and the padding bits 5-7, which the module
passes. The board's `d <= 0x1F` guard keeps a byte with any padding bit set out
of QMK's LED state; the others change only Compose and Kana there, which
nothing on the board reads. Num, Caps and Scroll never move, so no light
changes and no Scroll rule. `31^6 < 2^32`, so a block still fits the
receiver's `uint32_t`.

**A digit is a transition.** Each write flips a non-empty subset of the
profile's data bits; subset `s` (bit i of `s` = the profile's i-th data bit)
is digit `s - 1`. Once synced, a write that flips none of them is ignored
(repeats, Scroll under P1, Num and Scroll under P3) and does not restart the
gap clock; but after the gap has run out, any non-Caps transition ends the
frame. Before sync, every non-Caps transition counts, for the quiet and for
the preamble's spacing.

**Blocks.** A section's bits, most significant first, in blocks of the
profile's size; the last block holds what is left in the fewest digits `n`
with `base^n >= 2^r`. Digits most significant first; a block that decodes to
`2^bits` or more aborts the frame.

**A frame:**
0. **Warm-up:** more than 500 ms (the gap) after its last write, so a
   receiver that a lost or abandoned frame left mid-section has given it up,
   the sender makes sure the board has its baseline, with one change and back
   (bit 3 on, then off): after a reset or a link change the board's first LED
   byte only sets the baseline (codex, flash 2h).
1. **Quiet:** no data-bit transition for 150 ms.
2. **Preamble:** three transitions, the first after the quiet, the second and
   third each less than 150 ms after the one before, each flipping bits 3-4 together (from bits 3-4 clear), `00 → 11 →
   00 → 11`, for a P1 or P2 frame; bits 5-7 together (from bits 5-7 clear),
   for a P3 frame; or Num and Scroll together, for a P0 frame. A transition
   150 ms or more after the last is a new first transition, never a
   continuation, so a stray light change just before a frame cannot pass for
   its first one (with the 500 ms gap it could, and the frame was lost). The
   third is the frame's anchor.
3. **Header:** 32 bits in P1 (after a bits 3-4 preamble), P3 (after a bits
   5-7 one) or P0: `[version(2)=0 | profile(2) | type(4)] [seq] [len <= 48]
   [CRC-8 of the three]`, CRC-8 poly `0x07`, init 0. A bits 3-4 preamble may
   announce P1 or P2; a bits 5-7 one only P3; a Num + Scroll one only P0.
4. **Payload:** `len` bytes then a CRC-16/CCITT-FALSE (poly `0x1021`, init
   `0xFFFF`, big-endian) over the 4 header bytes and the payload, in the
   announced profile.
5. **Trailer:** two transitions back to the baseline (the LED byte before the
   preamble, on the profile's bits): first to `baseline ^ 0x08` (P0: `^ 0x01`),
   or `^ 0x10` (P0: `^ 0x04`) when the payload already ended there; then the
   baseline. The frame commits only then.

**Aborts:** more than 500 ms between transitions once synced (150 ms before:
the preamble's own limit); a frame older than 12 s; a
Caps change once synced (before sync it only cancels the preamble); a bad
header CRC, version, profile or length; a block out of range; a bad CRC-16; a
wrong trailer; the link going down, a slot select, pairing or cable mode, at
once, before the next byte. After any abort the receiver needs the quiet and a
new preamble.

**On the board** a frame in progress holds the LCD's Scroll Lock (the settle
clock restarts when it ends), which covers P0 and P2; P1 and P3 never touch
Scroll.
Only while the radio is the active host. Limits (codex): back-to-back frames
can hold it indefinitely, and after a sender dies mid-frame the light it left
shows once settled; the hold is not a restore.

**Sizes** (from the encoder): a 29-byte payload (30 characters of 7-bit text
plus two length bytes) is 64 writes in P3 (~2.0 s at 31 ms a write), 106 in
P1 (~3.3 s), 81 in P2 (~2.5 s), 183 in P0 (~5.7 s); 48 bytes is 95, 160, 121
and 279. Add the warm-up's wait (over 0.5 s) and the link's wake-up (the
first write, 130-376 ms measured) to each frame.

**Not yet:** what a frame carries (types: text, clock), 7-bit text packing,
the publication policy, the daemon's sender, and acting on frames. Those
follow once the board shows frames arriving intact.

**Windows:** JD has a second AK820 Pro on the Windows PC (not on a relay), the
test bed for the Windows routes when they come.

## Codex's review (10-18) and what changed

[Verbatim](review-codex-lockled-plan-2026-10-18.md), prompt
[`review-prompt-lockled-plan.txt`](review-prompt-lockled-plan.txt), at
gpt-6-astra xhigh with a brief to go wide. Its verdict: keep change coding,
pack in small fixed blocks, compress the text, start with CRC and delayed
retries, no Reed-Solomon yet; the real speed-ups are forwarded padding bits
and the 2.4G dongle, not a cleverer four-light alphabet; and look for an
acknowledgement path before any error correction.

| # | finding (short) | disposition |
|---|---|---|
| 1 | Every raw report carries Caps; production must write the user's current Caps in each one, and the read-then-write race cannot be closed from user space | **Agreed.** The probe's Caps = 0 is for experiments only. The daemon reads Caps before each write and aborts the frame on a change. The race is stated as a limit, not designed away. |
| 2 | Skipping every report whose Caps bit changed is unreliable (the daemon's own next write can carry a new Caps) | **Agreed:** a Caps transition aborts the frame; no classifying. |
| 3 | The trace timestamps parsing, not UART arrival, so a 60 ms gap is not a known-position erasure | **Agreed. Timing-derived erasures dropped.** |
| 4 | A lost restore does not fail the payload's CRC (BACKLOG said it did) | **Agreed; BACKLOG fixed.** |
| 5 | The alphabet does not enforce the 1.5 s Scroll rule, and the board's 500 ms own-key exception can show channel traffic | **Agreed:** the default profile leaves Scroll alone (next row). The four-light profile restores at checkpoints. |
| 6 | **Default profile: Num plus bits 3-4, Scroll untouched.** Base-7 change coding, 14 bits in 5 changes, ~89.6 bit/s; four lights optional, with a restore checkpoint every 24 payload writes | **Adopted, pending the width test** (it needs bits 3-4 to arrive; if they do not, only Num and Scroll remain and Scroll must be used). |
| 7 | Whole-frame base-15 is unnecessary: 31 bits in 8 base-15 digits into one `uint32_t` (99.2% of capacity), 14 bits in 5 base-7 digits for three lights | **Adopted.** Constant work per symbol, bounded state, test vectors. |
| 8 | Padding bits 5-7 are the best speed-up: bits 3-7 would be five data bits with every visible lock left alone, ~158 bit/s; the `d <= 0x1F` guard must then split transport data from QMK's LED bitmap, not be removed | **High-priority test (`high`), and the guard rule adopted** if they arrive. |
| 9 | The 2.4G dongle could beat every BLE refinement (8-40 ms a bit there) | **Test first, high priority**, after BLE. |
| 10 | Timing modulation only pays if a delay under ~15.6 ms is reliably distinguishable; the writes already jitter 24-45 ms | **Rejected for version one.** |
| 11 | Caps as a data bit | **Rejected** (as the draft). |
| 12 | The display holds 19 artist + 21 title characters; the daemon resends text every 30 s and playback every 3 s, which this channel cannot carry as is | **Agreed:** wireless gets its own publication policy (send on change, once more ~2 s later, a slow full refresh; playback extrapolated on the board). |
| 13 | Text: 7-bit ASCII packing (12.5%) now; a 6-bit alphabet, base-95, dictionaries and deltas later or after a corpus; deltas need a base id | **Adopted as ordered.** Absolute snapshots are the first wire contract. |
| 14 | Clock: anchor to the preamble's last edge, send the timestamp after it, add the board's elapsed time on commit; ±20 ms is a hypothesis; keep wireless syncs out of the SOF bias learner; settle who owns the RTC reference off USB (PCF discipline still runs, `rtc.c:1514`) | **Agreed**, and the RTC ownership question goes to the clock work before any wireless clock is built. |
| 15 | **An acknowledgement path first:** unusual `A3` consumer usages (BACKLOG idea 2, which worked for quill4gen7 on Linux/2.4G) could carry acks over BLE to a Mac; never at the expense of real input | **Test first, highest priority** of the architecture questions. |
| 16 | CRC-16 and delayed full-state retries to start; Reed-Solomon only once symbol positions are known; reject fountain codes, convolutional codes and CRC-guided search; deletion-correcting codes only if measured | **Adopted.** |
| 17 | Timeouts: 150 ms between symbols on a warm link, a separate ~8 s frame lifetime, warm the link before framing, re-arm only on a preamble, never round a gap into missing symbols | **Adopted.** |
| 18 | Sync: an absolute training pattern, bits 3-4 `00 → 11 → 00 → 11` with Num and Scroll held; with only Num and Scroll nothing can make a frame impossible to imitate | **Adopted.** |
| 19 | Windows' real-lock route: reject for production (an idle check cannot keep the user idle; apps and KVMs act on the toggles) | **JD's call**, against his earlier "a reasonable tradeoff". Per-device indicator access is tested first either way. |
| 20 | Framing: a header with version, profile, type, sequence, length and a CRC-8, sent in the three-light alphabet; payload <= 48 bytes; CRC-16/CCITT-FALSE; a two-change restore trailer, and commit only after it | **Adopted.** |
| 21 | Tests: 44 clean symbols bound the error rate only to ~6.6%; ~3,000 clean frames bound frame failure near 0.1%; missing tests: Caps races (Caps starting on, changes mid-frame, another keyboard), failure at every write boundary, fault injection, typing and display load, clock ground truth, long patterns, the ack and dongle probes | **Agreed.** The six tests pick the physical channel; reliability needs a frame-level soak, after the decoder exists. |

**Codex's estimates** (warm link, calculated, not measured): a 30-character
text in ~3.3 s on the three-light profile, ~2.7 s on four lights with
checkpoints; ~0.4 s more for the quiet interval and a cold-link wake.
