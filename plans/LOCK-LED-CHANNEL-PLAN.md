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
