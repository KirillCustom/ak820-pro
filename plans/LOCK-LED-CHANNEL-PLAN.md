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
change every few minutes, a clock sync every few minutes (resume item 5).

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
- **Windows** (measured on gremlin over the 2.4G dongle, 10-19; "Windows,
  measured" below): `WriteFile` and `HidD_SetOutputReport` on the keyboard
  collection are refused (error 1); **`IOCTL_KEYBOARD_SET_INDICATORS` on the
  keyboard-class interface works, unprivileged, per device, and leaves
  Windows' lock state alone** -- Num and Scroll only (no Kana in the dongle's
  descriptor, no padding bits in the API), so **P0**, at ~125 changes/s.
  Toggling the real locks is rejected: JD's PC shows every lock change on
  screen (a Logitech utility), the clearest case of codex's finding 19.
  Bluetooth on Windows is untested.
- **2.4G** goes through a USB dongle on the host; quill4gen7 measured 42/42
  frames intact at 8-40 ms a bit there. Here: every LED change reaches the
  board three times, 0-5 ms apart (the dongle repeats it).

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
2. ~~The Scroll hold only for frames that can carry Scroll~~ **flashed 10:34
   10-19** (flash 2j, firmware `442c9f49c9`, local): `lockchan_holds_scroll()`
   holds for P0 and P2 throughout and for a bits 3-4 preamble and header
   (they may announce P2), never for P1's payload or for P3; both of the
   band's uses (the settle hold, the own-key shortcut) take it, and
   `lockchan_busy()` stays for the devhook. Tested on the host
   (`test_scroll_hold`). **Codex's review (10-19, flash 2j:
   [verbatim](review-codex-flash2j-2026-10-19.md), prompt
   [`review-prompt-flash2j.txt`](review-prompt-flash2j.txt)): no must-fix,
   nothing blocks the flash.** Notes 1, 2 and 4: the rule matches the wire
   format (it holds conservatively through P0 and the ambiguous bits 3-4
   phases, never releases early in an intact frame), both call sites are
   right, no blocking work, daily builds unchanged; agreed. Should-fix 3:
   each profile ran on a fresh receiver, whose `pay_profile` starts as P0, so
   a rule that read the previous frame's profile would pass. **Fixed:** every
   profile after every other on one receiver; the planted rule fails 6 of
   them, the old test none. Its other half (the test calls only the
   predicate, not the band's two call sites) is **accepted**: `indicators.c`
   does not build on the host, the call sites are source-reviewed (note 2),
   and a real Scroll change under P1 and P3 traffic is a board check for
   when the daemon sends (item 4).
3. A Caps change while padding bits are set: does the Mac's own Caps report
   keep bits 5-7 (the board's guard would then drop it, and the band would
   miss the change)? Needs the probe's `capsmix` to take a value.
4. Then the daemon's sender (codex 2i, note 3): serialized write completions;
   the gap's wait counted from the last completion, with margin, and already
   elapsed idle time counts; coalesce to the latest state and resend it now
   and then, since nothing acknowledges; a clock frame's time refers to the
   anchor.
5. **The wireless clock, once deep sleep is built and tested and the
   channel carries now-playing.** An every-few-minutes job, not "now and
   then": the display reads the SN32's RTC, clocked by the ILRC, which
   wandered ~0.9% across a day (09-03/04, `docs/clock.md`) and a few hundred
   ppm on 5-minute scales. With no host, the PCF discipline holds it only to
   its 2 s snap, visibly (a wander, then a jump). Deep sleep is a separate
   bug the sleep build fixes (asleep, the RTC's second runs ~13% slow and
   the snap catches it: `SLEEP-PLAN.md`). So
   a clock frame every few minutes, and the board learns the ILRC's
   frequency from successive frames (a sparse SOF loop: each sync's residual
   over the time since the last), to within the jitter of a lock-light
   change's arrival. That jitter is unmeasured (~±20 ms is the guess
   above) and sets how often and how close: measure it first. P3 moves no
   light, so frequent frames are invisible. Codex's row 14 still holds:
   settle who owns the RTC's reference off USB first, and keep wireless
   syncs out of the SOF bias learner.

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

**Text stays 7-bit.** The atlases draw the 95 printable ASCII characters
(`ak820text.py` folds everything else into them), so a 6-bit alphabet (64
codes, codex's row 13) cannot carry them all without an escape: the 63
commonest in one 6-bit code, the rest as the escape plus a second. That
saves ~18 bits on a 30-character title with a couple of escapes (210 bits
as 7-bit), ~4 P3 writes or ~0.1 s of a ~2 s frame; base-95 packing (6.57
bits a character) saves less. Not worth the complexity unless a corpus of
real titles says otherwise.

**Windows:** JD has a second AK820 Pro on the Windows PC (not on a relay), the
test bed for the Windows routes. Measured below.

## Windows, measured (gremlin, 2.4G dongle, 10-19)

Details and every run in [`GREMLIN-RESULTS.md`](GREMLIN-RESULTS.md), section
2. In short:

- **The route:** `IOCTL_KEYBOARD_SET_INDICATORS` (`0x000B0008`) on the
  dongle's keyboard-class interface, found in the Configuration Manager's
  `GUID_DEVINTERFACE_KEYBOARD` list (no HID enumeration) and opened with
  access 0 (read is refused). Unprivileged; it addresses only that device;
  **`GetKeyState` never moves**, so nothing watching the system's locks sees
  the channel. `LedFlags`: Scroll `1`, Num `2`, Caps `4`, Kana `8`.
- **Flags come from `GetKeyState`, never from `IOCTL_KEYBOARD_QUERY_INDICATORS`:**
  the class driver answered `0x0000` with Num on, so query-then-set would
  turn the user's Num off.
- **Num and Scroll only, so P0.** Kana is refused with
  `HIDP_STATUS_USAGE_NOT_FOUND` (the dongle's descriptor has no Kana LED), and
  the API cannot express padding bits. P3's and P1's bits are out of reach on
  this route.
- **Speed:** changes issued back to back merge (6 of 16 arrive); spaced 3 ms
  or more, 16 of 16 in every run, 5-10 ms apart on the board: **~125
  changes/s**, about 4x BLE, so P0 at ~200 bit/s (a 30-character title in
  ~1.5 s). No pass >= 25 ms and no UART error after the bursts, with each
  change arriving 3x.
- **Not yet:** P0 frames through the receiver on this route; a long error-rate
  run; the user's own Num or Scroll press mid-frame (Windows then sends its
  own LED report, which is a real transition on P0's bits: the frame aborts
  or decodes wrong-and-CRC-fails, never commits wrong, if the receiver is
  right); the Bluetooth route on Windows (the board paired over BLE, its
  keyboard-class interface).

This changes resume item 4: the daemon's Windows sender is this IOCTL, with P0
as its only profile over 2.4G, while the Mac's is `IOHIDDeviceSetReport` with
P3. The publication policy can afford P0's ~1.5 s frames over 2.4G.

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

## The daemon's sender: design (Gremlin, 10-20, for codex)

Resume item 4, briefed by JD through the Mac session 10-20: the shared half
(encoder, payload, policy) and the Windows sender are Gremlin's; the macOS
sender (P3 through `IOHIDDeviceSetReport`, `hostagent/ledprobe/`) and the Caps
check (item 3) are the Mac's, after its deep-sleep series.

**What decides it, measured** (`GREMLIN-RESULTS.md` sections 3 and 8):
Windows reaches the board with `IOCTL_KEYBOARD_SET_INDICATORS` on the
keyboard-class interface over **both** radios (the dongle's `MI_00`, the BLE
keyboard's `Col01`), P0 only, 6/6 frames at 10 ms a change and the cliff at
6-7 ms on both. Over 2.4G the daemon already carries now-playing on raw HID
(`radio.rs`), so on Windows the channel is for **BT only**; on the Mac, BT
only as well.

### 1. What a frame carries: a firmware change is needed

The receiver decodes and counts frames; **nothing on the board shows them**
(the wire format's "Not yet"). So the sender is half the job: the board must
act on a committed frame, which is a firmware change (a handler beside
`lockchan_feed`'s commit in `ch582f_ajazz.c`, calling the same display entry
points as the raw-HID text channel). Proposed contract, version 0's `type`
field:

- **Type 1, now playing:** `[icon][n0][line 0: n0 bytes][line 1: the rest]`.
  The lines are what the raw-HID `SET_LINE`s carry (already folded to ASCII
  and fitted, 19 + 21 at most), so 1 + 1 + 40 = 42 bytes at most, inside the
  48. The board applies both lines as one update. `icon`'s bit 7 flags 5
  trailing bytes of playback `[state, pos_hi, pos_lo, dur_hi, dur_lo]` (47
  bytes at most), position taken at the preamble's last write and advanced
  by the board's own anchor-to-commit time, so a 3 s frame does not show the
  time 3 s behind. Reserved in v1 (section 4, the readout).
- **Type 2, clear:** no payload; the band back to the clock (`CLEAR`).
- Text stays 7-bit ASCII, unpacked (the wire format's "Text stays 7-bit");
  packing is a later saving of ~12%.
- **Only while the radio is the host and the cable is not**: the board feeds
  the receiver only outside USB mode already.

The firmware half is small (a switch on `type`, bounds checks, the display
calls) but it touches the display's entry points, so it is coordinated with
the Mac session and goes through codex with the sender.

### 2. The encoder (`src/lockchan/`)

A port of `scripts/lockchan.py`: profiles, CRC-8/CRC-16, blocks, `writes()`,
`encode()`. **Parity is generated, as the text folding is**: a script
(`scripts/gen_lockchan_vectors.py`) runs the Python encoder over fixed seeds
(every profile, payload lengths 0-48, every type and a spread of seqs, user
lock states with Caps on and off) and writes `tests/lockchan_parity.rs`; the
Rust must reproduce every write sequence byte for byte. Python stays the
oracle: `lockchan_test.py` already proves it against the C receiver.

### 3. The platform trait

```rust
pub trait LockLights {
    /// Windows P0; macOS P3.
    fn profile(&self) -> Profile;
    /// Windows 16 ms; macOS: zero (each write blocks until its completion).
    fn spacing(&self) -> Duration;
    /// The user's real Num/Caps/Scroll as HID bits, read fresh.
    fn user(&self) -> u8;
    /// One LED byte to the board; returns once the platform has accepted it.
    fn write(&mut self, leds: u8) -> Result<(), LockError>;
    /// How long since the last user input on this machine, if knowable
    /// (Windows `GetLastInputInfo`), for the wake resend.
    fn input_idle(&self) -> Option<Duration>;
}
```

Opening is the platform's (Windows: the Configuration Manager's
`GUID_DEVINTERFACE_KEYBOARD` list, device id
`{00001812-...}_dev_vid&010c45_pid&fefe_...&col01`, access 0; never HID
enumeration; absent means no channel). A handle error closes it; the next
frame reopens.

### 4. The sender (`src/lockchan/sender.rs`, platform-neutral)

**A thread of its own.** A frame is 2.5-4.5 s of writes at 16 ms; the
daemon's loop must not wait on it. The loop hands the sender the **latest
content** (a slot it overwrites: coalescing by construction); the sender
takes whatever is newest when a frame starts and never queues.

**One frame, in order:**
1. **Warm-up wait:** more than the receiver's 500 ms gap plus 100 ms margin
   since the previous write's **completion**, idle time already past
   counting (codex 2i, note 3). Then the warm-up, one change and back
   (Windows: Num, as `ledchannel_test.py` does), then the 150 ms quiet plus
   150 ms margin.
2. **The writes,** spaced by `spacing()` measured from each write's
   completion, never from its start.
3. **Before each write, the user's locks again.** Any change of Num, Caps or
   Scroll from the frame's baseline (the user pressed one, or another
   keyboard did) abandons the frame: no more writes. Windows sends its own
   LED report for the user's press, so the board is left on the user's real
   state. The receiver aborts on Caps and fails the CRC on the rest; it
   never commits wrong (none in 60 frames measured, including 28 broken at
   too-close spacing).
4. **The trailer** returns the lights to the user's baseline (the encoder's).
5. A write error abandons the frame and closes the handle.

**The policy (when a frame goes):**
- **On change** of what the panel should show (`text_differs`, the icon
  included), at once, and **once more 2 s after it completes** (codex row
  12; losses come in bursts, so spaced).
- **A refresh every 60 s** while something is shown, which keeps the board's
  3 min text expiry from lapsing on its own. **The longest a stale title can
  stay on the board: one refresh period plus one frame, ~65 s**, if every
  frame in between is lost. Without loss: one frame, ~3-5 s after the change.
- **The playback readout is off over BT in v1.** The board's readout expires
  after 20 s; keeping it alive would take a frame every ~15 s (the channel
  ~30% busy) for a timer that is coarse anyway. Type 1 goes without the
  playback bit; the band keeps the clock. Revisit after a soak.
- **After a deep sleep:** frames sent while the board sleeps are lost
  (UART2's receiver is off), and the wake expires host content (`display.c`,
  "A deep sleep's wake"), so the board shows the clock, never a stale title.
  The resend: when this machine's input goes from idle (more than 30 s, the
  shortest rung, Lights out's) to active, send the current content at once.
  `GetLastInputInfo` is system-wide (any keyboard, any mouse), a proxy for
  "the board just woke", and a wrong guess costs one frame. The 60 s refresh
  bounds the miss when it fails (a board woken by a key that goes nowhere).
- **Nothing to show:** one type 2 on the change, and its 2 s repeat, then
  silence.
- **The cable and 2.4G:** the channel runs only when the daemon's USB opener
  says Absent **and** the radio is not qualified, so no two transports carry
  the same content at once. Wiring that into `agent.rs`'s transport choice
  (USB, then radio, then lock lights) is announced to the Mac session first.

### 5. Limits stated, not designed away

- The read-then-write race on the user's locks (codex row 1): a press
  between our read and our write is caught at the next read, one write
  late; the frame fails its CRC, never commits wrong.
- During a P0 frame Num and Scroll change on the board (not in Windows);
  the board's Scroll indicator is held while a P0 frame is in progress
  (`lockchan_holds_scroll`) and shows the settled state after. What Num's
  flicker shows on the board, if anything, is a check for the soak.
- `GetLastInputInfo` wakes are a guess (above).
- No acknowledgement: the 2 s repeat and the refresh are the only recovery.

### 6. Order of work

1. Codex on this design, and the type 1 contract agreed with the Mac session.
2. The encoder and its generated parity test.
3. The sender and its policy, test-first on a fake `LockLights` and a fake
   clock (the raw81 daemon's pattern).
4. The Windows `LockLights`.
5. The firmware's type 1/2 handler, codex, flash on Gremlin.
6. Wiring into `agent.rs`, announced; a frame-level soak over BT on Gremlin.

### Codex on the sender design (10-20) and revision 2

[Verbatim](review-codex-lockled-sender-2026-10-20.md), prompt
[`review-prompt-lockled-sender.txt`](review-prompt-lockled-sender.txt).
Verdict: the basic choices are sound (full snapshots, one frame for both
lines, P0 at 16 ms on Windows, a sender thread, delayed repeats); revise
before implementing: restoration after an abandon, stale-content ordering,
transport handover, bounded cancellation. Eleven must-fixes, seven
should-fixes, three notes. **Every one is agreed**; revision 2 below
replaces the sections above where they differ.

| # | finding (short) | disposition |
|---|---|---|
| 1 | "Abandon leaves the board on the user's state" is false (a user report can be overwritten by our next prepared write); a baseline repair can complete an abandoned frame's trailer | **Agreed.** R2-1: an explicit restore with fresh locks after every abandon and after every frame, and the repair only after the receiver's gap has expired the frame |
| 2 | `user()` needs a freshness contract: `GetKeyState` follows the calling thread's input queue; unknown must suppress sending | **Agreed, measured before the Windows half is built** (R2-2) |
| 3 | A latest-content slot does not stop an obsolete frame already running; completions and repeats must name their generation | **Agreed.** R2-3: content and transport generations; a newer content cancels the frame in flight |
| 4 | ~65 s is not a bound: refreshes can be lost; the real fallback is the board's 180 s expiry; in-flight frames and polling add latency; lost CLEARs | **Agreed.** R2-4 restates both numbers honestly and repeats CLEAR |
| 5 | Checking the transports when a frame starts is not exclusivity for its next 4 s; revoke the running worker; keep Absent distinct from other USB failures | **Agreed.** R2-5: an ownership generation checked between writes, revoked by USB evidence and radio qualification |
| 6 | The firmware's `usb_mode` follows the slider, not the cable: a lock-light frame can commit after USB raw HID published newer text | **Agreed.** R2-6: the board applies a lock-light frame only when raw HID's text channel has been silent for 10 s |
| 7 | Handover in every direction needs publication state; LED completion is not delivery; BT's bookkeeping must be its own | **Agreed.** R2-7 |
| 8 | A thread does not solve a stuck IOCTL or shutdown; cancellation races completion; one writer per device across processes | **Agreed.** R2-8: overlapped I/O with deadlines, a poisoned state, interruptible waits, a machine-wide lock |
| 9 | Finish the type 1/2 contract first: lengths, icon ids, ASCII, empty lines, invalid frames change nothing | **Adopted as codex's table** (R2-9) |
| 10 | Omitting playback does not turn it off: an old timer runs beside a BT title | **Agreed.** Type 1 without the bit and type 2 both turn playback off (R2-9) |
| 11 | Unchanged content is not the same as a media source that stopped observing: a wedged SMTC read could keep yesterday's title alive by refresh | **Agreed.** R2-10: observation age gates every send |
| 12 | Retry and discovery separate from content changes; backoff; absence vs discovery failure; UnitId on each handle | **Agreed** (R2-8, R2-11) |
| 13 | Keep the timings; define phase deadlines and what completion means; spacing and lock checks on warm-up and repair writes too | **Agreed** (R2-1, R2-12) |
| 14 | `GetLastInputInfo` is per session and can wrap; keep an idle-armed state; more resend reasons | **Agreed** (R2-13) |
| 15 | The board's optimistic play/pause icon is corrected only by the next frame: up to a minute over BT | **Accepted as a stated limit for v1**, checked in the soak (R2-14) |
| 16 | Compare the serialized payload, not the Unicode strings | **Agreed:** content identity is `(type, payload bytes)` |
| 17 | Parity proves the encoder only; test validation, display effects and the sender at every write boundary | **Agreed:** the parity set covers all eight user baselines and both trailer branches (R2-15); the sender's tests use timestamped fake writes |
| 18 | The soak must cover the handler's display effects and real input | **Agreed** (R2-16) |
| 19 | One frame for both lines is right; no wire content id needed with local generations and source ownership | **Noted, agreed** |
| 20 | Say "no wrong commit observed", not "never commits wrong"; the dongle's cliff is 6-7 ms, BLE's 7-10 ms | **Agreed;** corrected here |
| 21 | Keep playback unsupported until it has a sampling contract; reject the bit in the first handler | **Agreed** (R2-9) |

**R2-1. Restoring the user's lights.** Every frame ends, and every abandon
ends, with a **restore**: read the locks fresh (R2-2), write them, read them
again, and if they changed during the write, write again (at most three
times, each spaced). After an abandon the restore waits first for the
receiver to expire the frame (more than 500 ms since our last write, plus
100 ms), so the restore can never complete an abandoned frame's trailer.
After a normal trailer the restore is a check: if the fresh locks equal the
trailer's, nothing is written. Best effort on removal, process death or a
stuck write (R2-8).

**R2-2. The user's locks, fresh.** `GetKeyState` reflects the calling
thread's input-queue state, so its freshness on a sender thread is measured
before the Windows half is built: a thread with no message loop, then one
that pumps (`PeekMessage`) before each read, against lock changes made with
`SendInput` and by hand, from this keyboard and another. The implementation
uses whatever the measurement shows is fresh; if nothing is, the sender
reads the locks on the daemon's main thread and passes them in. **Unknown
locks suppress sending** (no all-off baseline). Lock and unlock of the
session are tested too.

**R2-3. Generations.** A monotonic **content generation** per distinct
`(type, payload)` and a **transport generation** per acquisition of the
channel. A frame carries both; a completion, a repeat and a refresh are acted
on only if both are still current. **A newer content cancels the frame in
flight** at the next write boundary: abandon, restore (R2-1), then the new
frame. So the board never newly commits content that was obsolete when its
frame started.

**R2-4. The latencies, stated honestly.**
- **No loss:** a change reaches the board in at most one poll (0.5 s) + an
  abandon and restore (~0.7 s) + the warm-up and quiet (~0.9 s) + a frame
  (up to ~4 s at 16 ms, 249 writes for 42 bytes): **~6 s**; typically 3-4 s.
- **Every later frame lost:** the board shows the last title it applied
  until its own expiry, **180 s after that frame** (`DISPLAY_TEXT_TIMEOUT_MS`).
  That is the true bound; the 60 s refresh and the 2 s repeat make it
  unlikely, not impossible.
- **CLEAR:** sent on the change, repeated at 2 s, then every 60 s until 180
  s after the last title frame was sent (by then the board's own expiry has
  cleared it), then silence. Startup and every acquisition send the current
  state, CLEAR included.

**R2-5. Ownership.** The channel has an **owner generation**. It is granted
only when the USB opener says `Absent` (not busy, refused or a discovery
failure) **and** the radio is not qualified; every USB evidence (media,
clock or health opens, as `USB_SEEN` today) and the radio's qualification
revoke it **before** their first push. The sender checks it between writes and
after every blocking call; a revoked frame is abandoned and restored.

**R2-6. The board's source ownership (firmware).** A committed lock-light
frame is **applied only if raw HID's text channel has been silent for 10 s**
(the USB or radio daemon pushes playback every 3 s, so 10 s of silence means
neither is publishing). Raw-HID text always applies. So a late Bluetooth
frame can never overwrite what USB or the radio just showed, whatever the
host's races. The receiver's existing reset on cable mode stays.

**R2-7. Handover.** Entering the channel (from USB or the radio) sends the
full current state at once, unchanged content included. Leaving it (to USB
or the radio) bumps the transport generation, so its repeats, refreshes and
late completions are dropped; USB and the radio refresh on entry as they do
today (`Publisher::refresh`). The channel's bookkeeping is its own (attempted,
sent, last sent generation), never `Publisher`'s; the status file says
"sent", never "shown".

**R2-8. I/O and shutdown.** The handle opens with `FILE_FLAG_OVERLAPPED`;
each `DeviceIoControl` has a 1 s deadline, then `CancelIoEx`, then a bounded
wait for the completion. An operation that does not retire **poisons the
channel for the life of the process** (raw81's pattern): the handle and its
buffers are kept, nothing reopens. Waits are on a condvar with the stop flag,
so shutdown and `--once` are bounded; no lock is held across I/O or a wait.
**One writer per board, machine-wide:** a lock file beside raw81's
(`%ProgramData%\ak820pro\lockchan.lock`), which `ledchannel_test.py` takes
too.

**R2-9. The contract, version 0's types, frozen for the first release.**

| Field/rule | Definition |
|---|---|
| Type 1 length | `2 <= len <= 42` |
| Lines | `n0 <= 19`, `n0 <= len - 2`, `n1 = len - 2 - n0 <= 21` |
| Icon | the ids 0-3 exactly (none, play, pause, stop); any other bit, the reserved playback bit 7 included, rejects the frame |
| Text | printable ASCII `0x20-0x7E` only; any other byte rejects the frame |
| Update | both lines replaced, the icon set, **playback turned off**; an empty line clears that line |
| Type 2 | exactly zero bytes; the host text cleared and **playback turned off** |
| Anything else | no change to the display and no refresh of its expiry |

The handler validates the whole payload before any display call. Playback
over the channel waits for its own sampling contract (codex note 21); a
future extension takes a new type, never a reinterpretation.

**R2-10. A stale media source.** Every send is gated on the observation's
age (`Health::stale_for`), not on when the loop copied the snapshot. Past 30
s (10 polls) without a fresh observation, the content becomes CLEAR, as a
failed read already does: a wedged SMTC read cannot keep a title alive.

**R2-11. Retries.** A failed attempt keeps the content pending and retries at
1, 2, 4 ... 30 s, never marking it sent. Absence (no BLE keyboard listed) is
not a failure: the channel idles until the list changes. A discovery error,
an ambiguous match or a failed open is logged and retried on the backoff.
`UnitId` comes from `IOCTL_KEYBOARD_QUERY_INDICATORS` on each new handle;
the flags never do.

**R2-12. Phase deadlines.** The receiver's rules (quiet >= 150 ms, preamble
continuations < 150 ms, gaps <= 500 ms, lifetime <= 12 s) become the
sender's deadlines with margins: a write that completes more than 300 ms
after the previous one, or a frame past 10 s, abandons and restores rather
than finishing a frame the receiver has already dropped. Spacing and the lock
check apply to warm-up and restore writes too. Timing is monotonic, kept
across a reopen, and starts from "unknown history" (the full recovery wait).
Windows accepting a write is not the board receiving it: the margins are
measured choices, not proof.

**R2-13. Resends.** Reasons: startup, an acquisition, a resume or unlock, and
the wake guess. The wake guess keeps an **idle-armed** state: armed once
`GetLastInputInfo` shows more than 30 s idle, fired on the first newer
input, so a wake during a frame is not missed; it handles tick wrap and a
failed call (no guess). It is per session. A resend forces the current
content through the slot even when unchanged and coalesces with pending
work.

**R2-14. The optimistic icon.** The board flips its play/pause icon on the
media key; if the player ignores the key, nothing on the host changes and the
channel sends nothing, so the wrong icon can stay until the next refresh,
**up to 60 s** over BT (3 s on raw HID). Accepted for v1 and checked in the
soak.

**R2-15. Tests.** The parity set adds every user baseline (0-7) for every
profile and checks both trailer branches are exercised; the sender's tests
run on a fake `LockLights` with timestamped writes and completion delays, a
fake clock, and a change, error, revocation or cancellation at every write
boundary (both trailer writes in particular), A to B mid-frame, every
handover, a lost wake, a superseded repeat and a stuck write at shutdown. The
handler's tests: every row of R2-9's table, invalid frames leaving the state
and the expiry untouched.

**R2-16. The soak** (Gremlin, over BT): maximum-length frames while typing,
media keys, each lock from this keyboard and another, the overlays, sleep and
wake, removal, every handover; checked: keys delivered, passes >= 25 ms, UART
errors, the final lights and the final text.

### Codex's verification of revision 2 (10-20) and revision 3

[Verbatim](review-codex-lockled-sender-r2-2026-10-20.md), prompt
[`review-prompt-lockled-sender-r2.txt`](review-prompt-lockled-sender-r2.txt).
Verdict: R2 is not yet an implementation specification. The encoder, the
payload validator and the display handler (R2-9) can start; the sender and the
transport integration wait for four must-fixes. Five should-fixes and a note
follow. **All agreed**; revision 3 replaces R2's text where they differ.

| # | finding (short) | disposition |
|---|---|---|
| 1 | R2-1's wait does not guarantee cancellation (the receiver's gap runs on its own clock; a repair can start a preamble); three tries bound the work, not the outcome | **Agreed.** R3-1: a `restore_pending` state, and the guarantee restated as conditional |
| 2 | R2-6's 10 s rule is a lease, not ownership; it must count every raw-HID media command at ingress, not text alone; a late frame after the lease is possible; handover can lose both first attempts | **Agreed.** R3-2 |
| 3 | Poisoning must keep the machine-wide lock; numeric shutdown budget; a stuck open; `--once`; the owner lock is held across I/O, only the slot mutex is not | **Agreed.** R3-3 |
| 4 | The main-thread fallback is not a freshness contract; unknown must be explicit in the trait | **Agreed.** R3-4: the fallback is dropped (the measurement made it unnecessary) and `user()` returns `Option` |
| 5 | Cancel-on-supersede can starve during continuous changes | **Agreed:** latest-state is the policy, stated (R3-5) |
| 6 | Separate deadlines for the preamble (< 150 ms) and the data, checked before each write | **Agreed** (R3-6) |
| 7 | 30 s is ~60 polls at 0.5 s, not 10; crossing it must schedule a CLEAR at once | **Agreed** (R3-7) |
| 8 | ~6 s is nominal, not a maximum; the CLEAR cutoff is host time; the icon is corrected by the 30 s text keepalive on raw HID, not by PLAYBACK; input timestamps can go backwards; discovery needs a re-list | **Agreed** (R3-8) |
| 9 | R2-9 is ready as the handler's contract; validate and pass the ownership gate before any mutation | **Agreed;** the handler starts now |

**R3-1. Restore.** The restore of R2-1 stays, with its guarantee stated as
conditional: the recovery wait (more than 500 ms plus 100 ms since our last
completion) makes a repair harmless **if** the receiver saw the same silence;
a report delayed in the stack, or the user's own Num/Scroll change, is outside
the host's clock. A repair is a single transition: it can start a preamble
(`0x05 -> 0x00` is a P0 preamble's first edge), which with no continuation
expires in 150 ms and commits nothing. After three repairs that still
disagree with a fresh read, or a recoverable write error, the channel enters
**`restore_pending`**: no new payload is sent, a fresh restore is tried every
1 s, and the status says "lights not restored". A repair is never written
over an operation that has not retired (R3-3). Shutdown makes one bounded
attempt and stops. Tests: a repair that starts a preamble, a lock change
during recovery, a mismatch after the third repair. Known cost: during the
wait the board's Caps and Scroll indication can be wrong for ~0.6 s, and the
board's settled Scroll needs 1.5 s after the last frame.

**R3-2. Source ownership on the board: a lease, stated as one.**
- **The signal:** a timestamp `rawhid_media_at`, set at the raw-HID ingress
  of the text channel (`hid_protocol.c`'s `text_command`, which raw81's
  dispatch shares) for **every accepted command**: SET, SET_LINE, PLAYBACK
  (state 0 included) and CLEAR. Never in the display's setters, which the
  lock-light handler also calls, and never `text_stamp`.
- **The lease:** a committed lock-light frame is applied only if
  `rawhid_media_at` is more than 10 s old. The Rust daemon sends PLAYBACK
  every media cycle, idle included (3 s by default), and so does the Windows
  Python agent; **the daemon refuses `--lockchan` with `--interval` over 5
  s**, and the legacy macOS shell agent (30 s keepalives after playback
  stops) is not a supported companion: the daemon replaces it.
- **Stated residual risk:** a lock-light frame delayed by more than the lease
  after the host stopped it could still apply. Windows completes a
  `SET_INDICATORS` in milliseconds and the receiver's 12 s lifetime bounds a
  frame once it starts arriving; a frame held more than 10 s before its first
  report reaches the board is accepted as a risk, not designed away.
- **Handover from raw HID to the lock lights:** the daemon knows when it last
  pushed on raw HID (USB or radio). The channel's **first frame waits until
  12 s after that push**, so the board's lease has expired before it arrives
  and the first attempt and its repeat are not thrown away.

**R3-3. I/O, the lock and shutdown.**
- The **machine-wide lock** is raw81's mechanism, reused (`radio_lock.rs`: a
  byte-range lock with no delete sharing) on its own file,
  `%ProgramData%\ak820pro\lockchan.lock`. It is taken when the channel is
  granted and held through warm-up, frames, recovery, restores and
  `restore_pending`; released only when the channel is released with no
  operation outstanding. **Poisoning keeps it** with the handle, the
  `OVERLAPPED`, the event and the buffers, for the life of the process (the
  guard is forgotten, as `radio.rs` does).
- Numbers: each `DeviceIoControl` 1 s, then `CancelIoEx` and 1 s more for the
  completion; not retired by then: poisoned. Shutdown sets the stop flag,
  waits up to 3 s for the sender thread (one write and one restore), and
  then exits regardless; a sender stuck in `CreateFileW` (which overlapped
  I/O cannot bound) is left detached, holding its lock, and process exit
  ends it.
- `--once` does not run the channel (it is one diagnostic pass over raw HID).
- "No lock across I/O" means the latest-content slot's mutex, never the
  owner lock.

**R3-4. The user's locks.** `user()` returns `Option<u8>`. On Windows it is
plain `GetKeyState` on the sender thread: measured fresh (GREMLIN-RESULTS
section 9: software toggles seen in 1-8 ms; JD's real Caps over BT, all of
them) — and checked again in the daemon's own context by the soak. It returns
`None` when the input desktop cannot be opened (`OpenInputDesktop` fails
while the session is locked or on the secure desktop), and `None` stops the
channel before the next write (abandon, no restore until known again). The
main-thread fallback is dropped.

**R3-5. Supersession: latest state.** A newer content cancels the frame in
flight at the next write boundary; content is coalesced through recovery,
restore and warm-up, and the newest generation is taken only when the payload
starts. Under changes faster than a frame, nothing need finish until they
stop; then the last one goes. Intermediate tracks are never guaranteed.
Supersession is not a failure and does not back off. A change after the
trailer's last write is issued can still be followed by that frame's commit;
the next frame corrects it.

**R3-6. Phase deadlines,** checked before each write is submitted and again
on its completion:
- recovery: at least 600 ms of our silence (above);
- quiet: at least 300 ms after the warm-up (the receiver needs 150);
- preamble: each continuation submitted within 100 ms of the previous
  completion (the receiver needs < 150);
- header, payload and trailer: each write within 300 ms of the previous
  completion (the receiver allows 500);
- the whole frame within 10 s (the receiver allows 12).
A deadline missed (a suspension, a slow write) abandons and restores. The
deliberate waits are outside the inter-write rule.

**R3-7. A stale source.** The threshold stays 30 s, about 60 polls at the
worker's 0.5 s, as a grace period. Crossing it **creates a CLEAR generation
at once** (cancelling pending work for the stale content), not at the next
refresh. The sender carries the observation instant, so age keeps growing
while content waits; fresh observations of identical content renew it
without a new generation; a failed read is idle at once, as today.

**R3-8. Wording and the rest.**
- Latency: ~6 s is a nominal figure for prompt I/O, not a maximum.
- CLEAR's repeats continue until 180 s after the last title frame was
  **sent** (host time; the board's own expiry is board time, and the two are
  not the same clock): a bounded retry policy, not a proof the board cleared.
  Startup sends the current state, CLEAR included, with no history.
- The optimistic icon: on raw HID the 30 s text keepalive corrects it (PLAYBACK
  does not touch the icon); over BT the next refresh plus its frame, if it
  arrives.
- The wake guess: armed when `GetTickCount() - dwTime` (wrapping) passes 30
  s; fired on **any change** of `dwTime` while armed, forwards or backwards.
- Discovery: while the BLE keyboard is absent, the Configuration Manager's
  list is read again every 10 s (it opens nothing).

### Codex's verification of revision 3 (10-20) and revision 4

[Verbatim](review-codex-lockled-sender-r3-2026-10-20.md), prompt
[`review-prompt-lockled-sender-r3.txt`](review-prompt-lockled-sender-r3.txt).
Verdict: nearly adequate; one must-fix (R3-4's validity test), the rest
handled in the implementation; **once R4-1 is in, the sender and the
transport integration can be implemented to this specification.** All
agreed.

| # | finding (short) | disposition |
|---|---|---|
| 1 | `OpenInputDesktop` succeeds in a disconnected session and says nothing about the thread's own desktop | **Agreed.** R4-1 |
| 2 | The handover wait needs a dedicated timestamp for every possibly delivered media command (playback-only cycles, partial batches, uncertain outcomes), taken at completion; unknown startup history waits conservatively; a failed USB push keeps BT revoked | **Agreed.** R4-2 |
| 3 | Invalidate a lock-light frame being assembled when raw HID's media is accepted; "never received" must not lease the first 10 s after boot | **Agreed.** R4-3 (firmware). The second half is already so: `rawhid_media_seen` |
| 4 | 3 s bounds the exit, not a worst-case restore (2 + 0.6 + 2 = 4.6 s) | **Agreed:** 3 s stays a hard exit budget; restoration is attempted only within what is left |
| 5 | Freeze the snapshot before the header, not at the wire's payload section | **Agreed:** selected after warm-up and quiet, before the frame's first write; that attempt's bytes are immutable |
| 6 | R3-6's numbers match the receiver; enforce 100/300 ms on completion against the saved previous completion | **Agreed** |
| 7 | `restore_pending` closes the gap; keep it across unknown locks and reopens; clear only on a fresh repair that reads back right | **Agreed** |

**R4-1. When the user's locks are known.** `user()` returns `Some` only when
all of these hold, checked before each write (cheap calls, no I/O to the
board), and `None` otherwise or when any check fails:
- the daemon's session is **active and unlocked**:
  `WTSQuerySessionInformationW(WTS_CURRENT_SESSION, WTSSessionInfoEx)` gives
  `SessionState == WTSActive` and `SessionFlags == WTS_SESSIONSTATE_UNLOCK`;
- the **input desktop is the thread's desktop**: the names from
  `GetUserObjectInformationW(UOI_NAME)` of `OpenInputDesktop` and
  `GetThreadDesktop(GetCurrentThreadId())` match (`Default` normally;
  `Winlogon` while locked or at a UAC prompt fails it);
- then plain `GetKeyState` for Num, Caps and Scroll.
The soak covers lock and unlock, a UAC prompt, and (if it can be arranged on
gremlin) a disconnect and reconnect.

**R4-2. The handover timestamp.** The daemon keeps one monotonic instant,
`raw_media_last`, set at the **completion** of every raw-HID media request
it sent whose delivery is possible: text, CLEAR and PLAYBACK, over USB or the
radio, answered or not (a timeout counts: the board may have taken it).
The channel's first frame after an acquisition waits until 12 s after it. At
startup there is no history (another process may have pushed): the first
acquisition waits 12 s regardless. A USB push that failed does not grant the
channel: only the opener's `Absent` does.

**R4-3. The board drops a lock-light frame being assembled** when raw HID
accepts a media command (`lockchan_link_lost`, which also drops the LED
baseline; the next report only sets it), so no assembly can straddle a raw-HID
update. The lease at commit stays.

## The macOS writer (the Mac session, 10-20)

`ak820-agent/src/platform/macos/lock_lights.rs`:
- `BleLights` (the `LockLights` writer) and `Mac` (the sender's
  `Platform`);
- the dev commands `ak820 lockchan check | send | clear`.

**The route,** as the probe measured it on 10-18:
- the BLE keyboard `0C45:FEFE`, its usage-1:6 collection, taken from the
  registry with nothing else opened;
- output report 2, sent as `[2, leds]` through
  `SetReportWithCallback`, on the opening thread's run loop, with a 1 s
  deadline (in milliseconds) plus 250 ms of grace;
- P3, with zero spacing, because each write returns at its completion.

**The user's locks:** Caps from `IOHIDGetModifierLockState`, only while
CGSession is on the console and not locked. Num and Scroll read as 0.

**The owner lock:** an `flock` on `/Users/Shared/ak820pro/lockchan.lock`.

**The wake guess:** `HIDIdleTime`, turned into u32 ticks. A 250 ms slack
absorbs read jitter.

**First frame through the Rust stack from the Mac:** 51 writes in 2.76 s,
committed exactly on the board (P3, type 1, 20 bytes).

**Codex** (`review-codex-lockchan-macos-2026-10-20.md`) found no P1. It
confirmed the FFI's ownership, the report layout, the millisecond timeout
and the poison path, and that thread affinity is enforced by the types.
- **P2, the Swift probe and its harness skip the owner lock.**
  **Deferred to the wiring:** the probe takes the lock in its write modes
  before the daemon sends frames. Until then nothing else sends.
- **P2, `Mac::default()` turned the wake guess off.** **Fixed:** `start`
  is not optional, `Default` is `new()`, and both are tested.
- **P2, the CLI exited 0 on failures and released the lock on poison.**
  **Fixed:**
  - only `Sent` with no restore pending is success;
  - on poison the lock and the device are kept for the life of the
    process.
- **P3, a saturated idle made false wakes after ~49.7 days.** **Fixed:**
  both ticks are taken modulo 2^32, with a 60-day test.
- **P3, a malformed screen-lock value counted as unlocked.** **Fixed:**
  absent, boolean and malformed are told apart, and malformed is not
  active. Tested.

### Caps while frames go (resume item 3), 10-20: first look

`scripts/mac_lockchan_caps.py`, with frames sent back to back while JD
tapped Caps twice (`history/lockchan-mac-caps-20261020/`):
- **Caps on:** the sender saw it and abandoned the frame
  (`Abandoned(UserChanged)`) after 3 writes. macOS's own report reached
  the board clean, `0x02` with bits 3-7 clear, and the board ended on Caps
  on.
  - But 3 of our writes went out with the old Caps state after macOS's
    report: the board's Caps showed off for ~1.5 s until the restore. That
    is the known read-then-write race, a few writes wide.
- **Caps off:** the tap landed just before a frame's baseline, so that
  frame went out whole with Caps off. macOS's report fell before the
  board's 52-byte trace.
- **Not answered yet:** a tap while bits 3-7 are set mid-frame. Both taps
  landed at frame edges. Each LED byte reaches the board 3 times (the
  module repeats it).

**The receiver aborted 15 of 40 complete frames for a gap**, a pause over
500 ms between two writes. The Caps script logged only the frames around
JD's taps, so how the sender saw those 15 was not recorded then.

**The gap study** (`scripts/mac_lockchan_gaps.py`,
`history/lockchan-mac-gaps-20261020/run-a.*`): 20 frames back to back,
each frame's outcome, its slowest write, and the board's verdict.
- **15/20 committed, and the sender knew every failure.** All 15 `Sent`
  frames committed. The 5 others were `Abandoned(Deadline)` by the sender
  itself, and the board aborted them or saw nothing. Nothing committed
  wrong.
- **The cause is the Mac's BLE write latency.** The slowest write in each
  frame took 88-428 ms, with 1-5 writes over 100 ms per frame. That is
  past the sender's phase deadlines (100 ms in the preamble, 300 ms for
  data, inside the receiver's 500 ms), so the sender gives the frame up
  rather than risk a gap. On 10-18 the writes took 41-322 ms.
- **Not the 2.4G dongle:** with the cable's data in, the daemon's radio is
  off and never probes it.
- **So a frame goes through about 3 times in 4 on the Mac.** The policy's
  resend 2 s after a change and its 60 s refresh carry the rest. Left
  open: whether the Mac's other Bluetooth devices (a mouse, a trackpad,
  headphones) make the latency worse.

### Codex on the implementation (10-20) and what changed

[Verbatim](review-codex-lockchan-impl-2026-10-20.md), prompt
[`review-prompt-lockchan-impl.txt`](review-prompt-lockchan-impl.txt), on
`7bcc0ea`. Verdict: not for a soak as is; the encoder and the ordinary frame
sound. Eight must-fixes, five should-fixes; **all agreed and done** (the
tests bite on each: 39 lockchan unit tests, 708 parity cases).

| # | finding (short) | disposition |
|---|---|---|
| 1 | `restore_pending` not serviced on its own: revoked, or after a final CLEAR, it could wait forever | **Fixed.** `Core::step` runs `frame::restore_only` every second while one is owed, granted or not; the lights and the lock stay until it confirms |
| 2 | A failed write did not move the recovery clock | **Fixed.** A retired failure sets `last_completion` |
| 3 | The abort and lock checks ran before the spacing wait, not right before submitting | **Fixed.** `Run::write` checks after the wait |
| 4 | The radio revoked the lock lights only after its push; USB evidence from the clock or health only at the loop's next look | **Fixed.** `Radio::before_media` revokes before the first media request; `open_noting` revokes at once |
| 5 | `raw_media_last` missed radio requests that failed or went unanswered | **Fixed.** `Radio::last_media_attempt`, set after every media request, whatever its outcome |
| 6 | A stuck query at open was retried | **Fixed.** `Platform::poisoned()`; the Windows open reports a stuck query, the sender poisons and keeps the lock |
| 7 | `ak820 lockchan send` and the Python harness skipped the owner lock | **Fixed.** Both take `lockchan.lock` |
| 8 | No orderly stop; the stop's restore could not wait with the real clock | **Fixed where there is a path:** any return from the agent loop stops the sender and waits 3 s; the stop's restore waits through the flag (`Time::sleep_through`). **Stated limit:** Task Scheduler's "End" kills the process, and the lights stay as they were until Windows' next LED report |
| 9 | Staleness, the wake guess and the locks' validity sampled only when a frame was due; no resend on an unlock | **Fixed.** Sampled every second while granted; CLEAR scheduled at the staleness; the locks coming back is a resend |
| 10 | Generations from byte equality (A, B, A lost); content captured before the warm-up | **Fixed.** A content epoch in the slot; the newest content picked after the quiet; a completion counts only with the owner unchanged |
| 11 | Handover kept USB's publication memory; a new acquisition kept the old repeat state | **Fixed.** `via` reset when the lock lights are granted; `Policy::acquired` |
| 12 | The 10 s frame limit not checked on completion | **Fixed** |
| 13 | State left at "sending"; the CLI's exit status zero on failure | **Fixed.** "waiting" after a frame, counters published at once, poison published; the CLI fails unless `Sent` with the lights confirmed |
| tests | Lengths 0-48 and every type | **Done:** 708 cases |

**Also from the Mac's gap study (10-20):** a missed phase deadline or a lock
change retries at once without growing the backoff (`Policy::retry`, three
in a row before a failure). **The Caps race, measured on the Mac, is ~3
writes wide:** after a real Caps tap the board can show the old Caps for
~1.5 s, until the restore; it has never committed a wrong frame. A stated
limit.

### Codex's verify of the implementation fixes (10-20) and what changed

[Verbatim](review-codex-lockchan-impl-verify-2026-10-20.md), prompt
[`review-prompt-lockchan-impl-verify.txt`](review-prompt-lockchan-impl-verify.txt),
on `60a7b43`. Two must-fixes, three should-fixes, all agreed and done. It
also, rightly, refuted the claim above that the tests "bite on each" fix:
several passed against the old code. The new ones below were each checked
by planting the old behaviour back and seeing the test fail (A, B, A and the
mid-frame staleness so far; the rest by construction, noted).

| # | finding (short) | disposition |
|---|---|---|
| 1 | No usable stop: the loop only returns for `--once`; a stop with a restore owed skipped it | **Fixed.** A named stop event (`Global\ak820pro-agent-stop`): `ak820 install` and `uninstall` signal it, give the daemon 5 s, then end the task; the loop returns on it and the sender's stop runs one restore through the flag, owed or mid-restore. Test: a stop with a restore owed writes it (fails on the old code by construction) |
| 2 | A repair read the locks before its spacing wait | **Fixed:** the wait, then the read, then the write (on inspection; no test isolates the 16 ms) |
| 3 | The epoch change lost in-flight staleness | **Fixed:** the frame's control compares the slot's effective content (staleness applied) with the picked one; `< STALE_AFTER`. Test: a title going stale mid-frame is cut off for its CLEAR (**bites**) |
| 4 | A completion credited without checking the picked epoch | **Fixed:** credited only with the owner, the epoch and the effective content unchanged |
| 5 | A restore's reopen inherited the payload's backoff | **Fixed:** its own 1 s schedule |
| tests | Several did not detect the old defects | The mid-wait test now counts every write after the change; A, B, A asserts the cut-off and the restore (**bites**); the poison test counts the guard's drops; the radio's `before_media` runs after the probe and before any media request, and a failed media request stamps the attempt |

## Play times and the Mac's latency (the Mac session, 10-20)

The first end-to-end title over BT worked, with two complaints: no play
times (only the time of day), and ~8 s from a play pressed elsewhere to
the board. The ~8 s comes from:
- up to 0.5 s for the loop's expedite;
- ~1 s of recovery and quiet;
- ~2.5 s of writes;
- ~3.5 s for each retry after a frame the sender abandoned.

### Play times: a new frame type 3, the transport

Built first as a bit 7 in type 1, which codex's review
([verbatim](review-codex-lockchan-playback-2026-10-20.md); no P1) showed
breaks R2-9's frozen type: an older board drops the whole frame and its
text with it. So:
- **Type 1 stays frozen** (text and icon, playback off), as does type 2
  (clear, playback off).
- **Type 3 is the transport:** exactly `[icon 0-3][state 0|1][pos_hi]
  [pos_lo][dur_hi][dur_lo]`. The board sets the shown slot's icon alone
  (`display_set_text_icon`, a no-op with no text shown) and the readout
  (`display_set_playback_for`), the position advanced from the frame's
  anchor to its commit, with a 135 s expiry.
- **Firmware:** `mac/lockchan-playback` `81a1860c8c`, on Gremlin's
  `1261ab2fa8`. The readout now runs from a base and the elapsed time
  (`pb_at`), so a stretch in Fn+D or an animation no longer loses time. A
  media key's optimistic freeze ends on its own after 15 s.
- **Shared Rust (`lockchan/mod.rs`, `23457b9`):** `transport()`,
  `Playback::of` (behind raw HID's duration gate), `text_identity` (the
  lines), `transport_identity` (icon, readout, duration), `is_seek`
  (clamped to the duration).
- **The sender (Gremlin's):** two policies, text then type 3; the slot
  holds the position with its own as-of instant, apart from `observed`.

| # | finding (short) | disposition |
|---|---|---|
| 1 | Bit 7 in type 1 breaks the frozen contract | **Fixed:** type 3 |
| 2 | `observed` is not the position's as-of time | **For the sender:** the slot keeps `(pos_s, pos_at)` |
| 3 | `Playback::of` took `pos=0, dur=0` | **Fixed:** behind `Snapshot::playback()`'s gate |
| 4 | `is_seek` ignored the duration's clamp | **Fixed:** clamped, with a test at a track's end |
| 5 | Fn+D or an animation lost elapsed time | **Fixed:** `pb_at` from a base and the elapsed time |
| 6 | An ignored media key froze the readout for the lease | **Fixed:** the freeze's own 15 s |
| 7 | The pick precedes the anchor | **Documented:** the position is the host's at its pick |

**Codex's verify of the redesign**
([verbatim](review-codex-lockchan-playback-verify-2026-10-20.md)): it
clears `81a1860c8c` for an attended dev bench flash, with no P1. The P2s
come before the playback sender is in daily use.

| # | finding (short) | disposition |
|---|---|---|
| 1 | Type 3 renews the text lease: a lost type 1 leaves the old title alive for as long as type 3s land | **To fix:** type 3 leaves `text_stamp` alone and applies only while text is shown |
| 2 | A second media-key press rebases the host's anchor, so an optimistic position becomes permanent | **To fix:** the host's anchor is never touched by a key; the guess is its own state (frozen or resumed) with a 15 s deadline, evaluated before the next press; the icon's guess gets the same deadline |
| 3 | Every type 1 sent must leave a current type 3 owed (repeats, refreshes, wake and acquisition resends), since every type 1 turns playback off | **For the sender (Gremlin):** sent 10-20 |
| 4 | P3: Fn+D's exit paints the cached position, then jumps | **To fix:** playback serviced before the clock band is painted |

**The fixes** (`afc771237c` on `mac/lockchan-pm`, the power modes'
`5df4e0e3b7` with the lock-LED commits cherry-picked on top; the same on
`mac/lockchan-playback` as `4809d01ace`): the readout's state moves to
`graphics/playback.c`, pure, with `scripts/playback_test.c` (codex's two
media-key scenarios, a planted rebase and a missing deadline check each make
it fail). Codex's verify ([verbatim](review-codex-lockchan-playback-fixes-2026-10-20.md))
clears it for an attended bench flash and finds the power-mode combination
sound:

| # | finding (short) | disposition |
|---|---|---|
| 1 | A type 3 was admitted by the cached `text_present`, which only `draw_text_slot` expires, so a spent title could take a readout that outlived it | **Fixed** (`845996c39c`): admitted by `display_text_lease_left()`, from the stamp, and the readout's expiry capped at the lease left |
| 2 | The sender checks "the current text was sent" before its warm-up, not at the transport's pick | **For Gremlin's sender:** sent 10-20 |
| note | An icon guess's rollback bumps the text generation, which in Lights out lights the screen briefly | Accepted: a rollback is a correction worth showing |

Its integration rules for the sender, also sent to Gremlin:
- the position's own as-of instant: on the Mac it is the snapshot's
  creation, not `observed`;
- positions compared at one instant, unsaturated;
- identity kept apart from the payload;
- a CLEAR cancels both policies' work.

### The Mac's retries: its BLE write latency

The interference run (`history/lockchan-mac-interference-20261020/`) took
the radio's lock, so the daemon stopped probing the 2.4G dongle: 14/20
frames went, as with the cable in (15/20). So the dongle is not the cause.
5 of the 6 frames abandoned were a data write of 343-379 ms against the
sender's 300 ms.

- **`LockLights::data_gap()`** (`63bc96d`) bounds the warm-up's return and
  every data write. The default is 300 ms; `RECEIVER_GAP` names the
  board's 500. **The Mac takes 450 ms:** its write completes at the ATT
  response, after the board has the byte, so the board's gap is at most
  450 ms plus that trail.
- **The gap study at 450** (`history/lockchan-mac-gaps-20261020/`,
  `run-d-450-quiet.*`, cable in, on `1261ab2fa8`): **20/20 `Sent`, all 20
  committed, no gap abort.** Six frames had a slowest write of 340-369 ms,
  which under the 300 ms would have lost the frame in the data phase. Twenty
  frames and no write of 400-450 ms, so the margin itself is not yet
  exercised; the long soak will add more.
- ⚠️ **A cable-in test must quiet the daemon's raw HID.** `run-c-450` ran
  beside it: 7 of 20 `Sent` frames never committed and the gap count did not
  move. R4-3 (`1261ab2fa8`) drops a frame being assembled on every raw-HID
  media command, uncounted, and the daemon pushes now-playing over the cable
  every few seconds. Run-d paused it (`kill -STOP`, then `-CONT`; it resyncs
  the clock as after a sleep). In use there is no clash: with the cable in,
  the lock lights are revoked.

**The Mac daemon's orderly stop** (`63bc96d`, `stop.rs`): SIGTERM
(launchd's bootout, run by `ak820 install` and `uninstall`) and SIGINT
set `stop_requested`. The loop then returns and the sender restores what
it owes. Before this, the default action killed the daemon mid-frame.
A second signal kills at once (`SA_RESETHAND`).

### The clock flash at title frames

JD saw the clock for a moment on returning to the board and at a song
change, during the first supervised run over BT
([`BACKLOG.md`](BACKLOG.md), "The clock flashes"). Every type 1 turned the
readout off, and its type 3 lands ~3 s later.
- **The fix** (`dd8391c28d`, `mac/lockchan-pm`, flashed 20:30 10-20, token
  `0x28e58a4c`): `lockchan_media_readout` decides from the slot as it was
  before the frame.
  - **Keep:** the same lines, playing in both the slot and the frame. The
    readout's own expiry stays.
  - **Guess:** a new title over a live one, playing. It counts from 0 at
    the frame's anchor and lasts 10 s at most.
  - **Off:** everything else.
- **Codex** ([verbatim](review-codex-clock-flash-2026-10-20.md)) clears
  it for an attended dev bench flash, with no P1 or P2:

| # | finding (short) | disposition |
|---|---|---|
| 1 | P3: a guess over an active readout repaints only at the band's next second, so the old track's time can sit under the new title for up to ~1 s | **To fix in the next build:** ask for a band update when a guess is installed; the glyph diff handles it without a clear |
| note | A guessed PAUSE icon shown when the host's PLAY refresh lands gives OFF, so that case can still flash | Accepted: the key's guess was wrong, and the correction shows |
| note | Keep can carry raw HID's own readout (untitled) for its remaining <10 s after the 10 s arbitration lease | Accepted: bounded, not renewed, and still under its own 20 s lease |
| note | `ch582_task`'s stack grows 272 to 320 bytes (inlined), within the 2,048-byte PSP | Accepted |

## The wireless clock: design (Gremlin, 10-20, for codex)

Resume item 5, asked for by JD 10-20 once now-playing over BT worked. The
board's own time off USB: the PCF8563 (whole seconds, ~58 ppm, ~5 s a day,
the Mac session's measurement) disciplines the SN32 RTC, which snaps to it at
2 s (`rtc.c`, "Legacy discipline"; `docs/clock.md`). With no host, the board
is seconds out within a day.

**Measured** (`history/lockchan-clock-jitter-20261020/`): Windows over BLE,
host write completion to the board's `anchor_ms`, 40/40 frames: min 7, p10
20, median 38, p90 54, max 147 ms (an earlier 30: max 179). One frame
corrected by a constant lands within about ±15 ms typically, ~100 ms in the
tail: against seconds of drift and a whole-second display, enough. The Mac's
is unmeasured and differs in kind (its write completes at the ATT response,
after the byte reached the board; 88-430 ms writes), so the correction is
per platform.

### C1. Type 4, clock

Payload: exactly 12 bytes, the layout of raw HID's `RTC_SET_TIME_MS` after
its header: `yy mm dd wd hh mi ss ms_lo ms_hi flags bias_lo bias_hi`, local
wall time (the board's RTC is local; the PCF stores year % 100), **meaning
"at the frame's anchor, true local time was this"**. `flags` must be 0 and
`bias` 0x7FFF (the board never feeds a wireless sample to any learner). An
older board rejects type 4 whole (unknown type) and changes nothing.

### C2. The board

On a committed type 4 (`lockchan_media.c` parses, `ch582f_ajazz.c` applies):
1. **Validate** with the same rules as raw HID: `rtc_ms_payload_valid`,
   moved where both can call it, plus flags == 0 and bias == 0x7FFF.
2. **One clock owner:** refused while `usb_mode`, or within 10 minutes of a
   raw-HID clock set (`RTC_SET_TIME` or `RTC_SET_TIME_MS`, by USB or raw81),
   counted from that set's completion. The media lease (10 s) is separate.
3. **Apply** through a new wrapper in `rtc.c` (the Mac session's file; it
   agreed to review it): `rtc_set_local_ms(int64_t local_epoch_ms, uint8_t
   flags, int16_t *off)`, which splits into fields and ms with the board's
   `mktime`/`localtime_r` (year 2026-2098) and calls `rtc_set_time_ms(&t, ms,
   0, 0x7FFF, &off)`. The handler passes the payload's epoch ms **plus
   (now_ms - anchor_ms) at the call**, so the time spent between the anchor
   and the apply is added in epoch space (second, minute and day carries).
   Flags 0: not INTERNAL (it bumps `host_sync_gen`, outranks the boot
   acquisition and a deep sleep's PCF recovery), not SKIP_PCF (the PCF is
   written phase-correct by `rtc_fast_task`, so the 2 s snap does not undo
   it; the phase-correct writer's several transactions and its bracket
   reads, one a pass, per sync, so not per frame; codex's implementation
   review, 13).
4. **RETRY** (the RTC unreadable this pass): kept and retried on a later
   pass with the elapsed time recomputed, for at most 2 s, then dropped.
5. Counters for op 17 sub 8: applied, refused (owner), rejected, retried,
   dropped, and the last offset `off` (**target minus board**, ms, as
   `rtc_set_time_ms` reports it; corrected per codex's implementation review,
   13).

### C3. The host

**Late payload.** The frame executor gains a second kind of payload: the
preamble is written first (it does not depend on the payload: three
transitions of the profile's preamble bits from the user's state), and only
after the third write completes is the payload built, from that completion
instant, encoded whole, its first three writes checked equal to what was
written, and the rest sent. The ordinary pick (R4-5) is unchanged; this is a
separate path used only by type 4. The deadline after the anchor (the
header's first write) holds: encoding is microseconds against a 300 ms data
gap (450 on the Mac).

**The time.** `local time at the anchor's completion + L`, where `L` is the
platform's latency correction: Windows 38 ms (the median above); the Mac's
measured before it sends. Platform-specific, like `data_gap`:
`LockLights::clock_latency() -> Option<Duration>`, `None` meaning "do not
send clock frames" (the default until measured).

**When.** Only with `--clock` (the daemon owns the clock) and the channel
granted (USB Absent, the radio not carrying: raw HID's clock owns the board
then). One clock frame after each acquisition (after the handover wait), then
every 10 minutes; never more often than every 5 minutes. Its own policy
slot, after text and transport in priority (a clock frame never delays a
track change), not superseding anything and not superseded (it carries its
own time). A failed or abandoned clock frame retries after 30 s. The daemon's
clock learner and its status never take a wireless sample; the status file
says when the last clock frame was sent.

**Local time on the host:** the same local-time conversion the USB clock
transaction uses (`clock/`), so DST and the time zone match USB's.

### C4. Limits stated

- One way: the board's offset is never seen by the host (the `off` counter
  is for the bench). A wrong latency constant biases every sync by the same
  amount; the tail (~100 ms) is per frame.
- A sync takes effect through the RTC's slew or step exactly as raw HID's.
- Two hosts on one board: the board's 10-minute rule favours raw HID; two BT
  hosts at once (two machines paired) would fight, as they would for text.
