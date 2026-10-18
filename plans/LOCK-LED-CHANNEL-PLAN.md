# The lock-LED channel: a design to review

Status: **a draft for codex to take apart (10-18)**. Nothing here is built
except the test tools (`hostagent/ledprobe/`, `scripts/ledchannel_test.py`)
and flash 2g's 5A trace. The research, with every measurement, is
`plans/BACKLOG.md`, "Community", quill4gen7 item 1.

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
