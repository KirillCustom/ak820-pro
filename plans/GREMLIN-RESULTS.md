# Gremlin results (Windows 11, the second AK820 Pro), from 2026-10-19

The Windows half of [`GREMLIN-HANDOFF-2026-10-19.md`](GREMLIN-HANDOFF-2026-10-19.md):
what this board and this PC measured, in the order it was done. Gremlin's
session writes this file; the Mac's session gets each result as it lands.

## 0. Bringing the board up to `de5450e487` (10-19)

Flashed 16:55, the **dev** build (`via-dev-de5450e4-20261019-165306.bin`,
token `0xa3e3804c`), from `8608c4f6-dirty` (09-05). Fixes in `9880f47` and
`30de86b`.

- **`setup.sh`: clean** from a real MinGW64 shell. Launched from Git Bash,
  MSYS2's bash loses `USERPROFILE`/`LOCALAPPDATA` and warns that `venv-win`
  cannot be made: an artifact of nesting one MSYS runtime in another, not the
  script. Launch MSYS2 from PowerShell or the Start menu.
- **`build.sh daily` and `dev`: clean.**
- **`flash.sh`, three snags, each fixed or documented:**
  1. Under MSYS2 `$HOME` is `C:\msys64\home\<user>`, with no `Documents`: the
     keymap dump crashed. Backups now go to the Windows profile's Documents.
  2. With the daemon running, the keymap backup failed ("unexpected reply 0x07
     to command 0x12": the daemon's `07 12 04` echo on our handle). The
     keymap tool's `xfer` now matches the request's first four bytes.
  3. A board on pre-battery firmware cannot answer the battery-log dump ("no
     reply to 0x0a"): the first flash from there needs `--no-battlog`
     (README).
- **No HID enumeration on Windows.** `ak820text.open_device` lists the board's
  path from the Configuration Manager (opens nothing) and opens only it;
  `ak820health.open_device` delegates there, so `ak820battery.py`,
  `ak820bootloader.py` and the health scripts are safe on Windows. (Before,
  `ak820text` cached the path per process: every one-shot tool still
  enumerated once.)
- **One clock owner:** `flash.sh` leaves the post-flash sync to a live daemon
  that owns the clock. Verified: one `sync (enumerated)`, -25.2 ms before,
  7 s after the board came back.
- **After the flash:** keymap and lighting restored and read back; no pass
  >= 25 ms; power mode reads Balanced.
- **LCD variant: the default** (not `fpb`). JD: the panel looks as the Mac's.
- ⚠️ **An older VIA keymap hides the power modes.** VIA's stored keymap
  overrides `keymap.c`, and JD's (saved before `PWR_MODE` existed) had
  `KC_TRNS` at `Fn`+`B` on both Fn layers, so the modes were unreachable.
  Bound by hand (`0x7E16` at row 4 col 6, layers 1 and 3) and the backup
  re-dumped. Any owner upgrading keeps their old map: worth a README line
  or a one-shot migration.

## The 2.4 GHz dongle: what it is (10-19)

`0C45:FDFD` (Sonix), five interfaces. Read with each collection opened at
access 0 (`HidP_GetCaps`), then a `GENERIC_WRITE` open closed unused:

| collection | usage page / usage | in / out / feature bytes | write-open |
|---|---|---|---|
| `MI_00` | `0x01`/`0x06` boot keyboard (`\KBD`) | 9 / **2** / 0 | **allowed** |
| `MI_01` | `0x01`/`0x02` mouse | 6 / 0 / 0 | allowed |
| `MI_02&Col01` | `0x0C`/`0x01` consumer | 3 / 0 / 0 | allowed |
| `MI_02&Col02` | `0x01`/`0x80` system control | 2 / 0 / 0 | allowed |
| `MI_02&Col03` | `0x01`/`0x06` NKRO keyboard (`\KBD`) | 16 / 0 / 0 | allowed |
| `MI_02&Col04` | `0x01`/`0x0E` | 9 / 0 / 0 | allowed |
| `MI_02&Col05` | `0xFFFF`/`0x01` | 4 / 0 / 0 | allowed |
| **`MI_03`** | **`0xFF60`/`0x61`, QMK raw HID's usage** | 33 / 33 / 0 | allowed |
| `MI_04` | `0xFF59`/`0x61` vendor | 65 / 65 / 0 | allowed |

- **Windows lets a user process open the dongle's boot keyboard for
  writing**, and its only output report is the LED byte (report id 0 plus one
  byte), but the open is all it gets: the writes are refused (section 2).
- **`MI_03` has QMK raw HID's usage on the dongle.** Probably the dongle's own
  port, not a tunnel: fpb's `CH582F_PROTOCOL.md` lists only `5A`/`5B`/`5C`
  from the module to the SN32, so a raw-HID payload has no frame to ride in.
  Listened 10 s on `MI_03` and `MI_04` (opened for read, nothing written): no
  unsolicited report on either. Nothing has been sent to them.

## 1. Deep sleep in 2.4G, on battery (10-19, ~17:00-17:20)

Slider on 2.4G, cable out. Read afterwards over the cable
(`ak820battery.py sleep`): **5 sleeps since the flash, 222 s asleep in all, no
blocker recorded, recovery done every time.**

| # | mode | woke on | notes |
|---|---|---|---|
| 1 | Balanced | key (Esc) | ~7 min idle, board dark; 146 s by the PCF, 130 counted; the key typed |
| 2 | Lights out | key | a burst, every character registered |
| 3 | Lights out | knob | the waking turn changed the volume |
| 4 | Lights out | key | a burst, all registered |
| 5 | Lights out | **charge** | the cable plugged in; 21 s by the PCF, 19 counted |

The dongle link was back for the first key every time. The RTC's ~10-13%
slow second asleep shows here too, and the PCF's count is the one kept.
JD thought the lights flashed at the first Lights-out wake; not seen at the
second, not confirmed. Lights out lights the screen 4 s for a band message
(`MESSAGE_WAKE_MS`), the likely candidate if it recurs.

## 2. Lock lights over 2.4G (10-19)

- **Real lock changes reach the board over 2.4G.** Scroll Lock tapped on and
  off from software (`keybd_event`): the board's 5A trace shows each change
  **three times, ~0-5 ms apart** (`0x05` x3, then `0x01` x3, >254 ms between).
  The dongle repeats every LED report; the receiver ignores repeats (a digit is
  a transition), so they cost nothing but UART time.
- **A user process cannot write the LED report through the dongle's keyboard
  collection.** `MI_00` opens with `GENERIC_WRITE`, but both `WriteFile` and
  `HidD_SetOutputReport` fail with error 1 (`ERROR_INVALID_FUNCTION`): the
  keyboard driver owns the collection's output. Nothing reached the board;
  the user's locks never moved (`scripts/win_ledwrite_probe.py`).
- ⚠️ **Toggling the real locks is out on JD's PC regardless:** an installed
  utility (Logitech's, JD thinks) shows every lock change on screen, so the
  plan's last-resort route would flash it per symbol. Settles codex's
  finding 19 against that route, for this machine at least.
- ✅ **The route that works: `IOCTL_KEYBOARD_SET_INDICATORS` on the
  dongle's keyboard-class interface**, unprivileged. The interface comes from
  the Configuration Manager's `GUID_DEVINTERFACE_KEYBOARD` list
  (`\\?\HID#VID_0C45&PID_FDFD&MI_00#...#{884b96c3-...}`), opened with access
  0 (read is refused, error 5). Setting Scroll reached the board (`0x05` x3,
  then `0x01` x3) and **Windows' lock state never moved** (`GetKeyState`
  before and after), so nothing watching the system's locks sees it. It is
  per device: only the dongle's unit (5) is addressed.
  - `IOCTL_KEYBOARD_QUERY_INDICATORS` answers `0x0000` with Num on: the class
    driver's copy is stale, so a sender must build the flags from
    `GetKeyState`, never from the query, or it turns the user's Num off.
  - **Kana is refused**, NTSTATUS `0xC0110004` (`HIDP_STATUS_USAGE_NOT_FOUND`):
    the dongle's descriptor has no Kana LED. Padding bits cannot be expressed
    in `LedFlags` at all. **So Windows over 2.4G has Num and Scroll: profile
    P0** (Caps never).
  - **Speed** (16 Scroll changes a run, the 5A ring's 52 frames read after):
    issued back to back (each call returns in ~0.1 ms) they merge, 6 of 16
    arrived; **spaced 20, 12, 8, 5 or 3 ms, 16 of 16 every run**, two runs
    each, change to change 5-10 ms on the board at the tighter spacings, so
    **~125 changes/s**, about 4x BLE's 32/s. P0 at that rate is ~200 bit/s, a
    30-character title in ~1.5 s. Each change still arrives 3x (~375 UART
    frames/s at the top rate): afterwards no pass >= 25 ms, no UART overrun,
    framing or parity error.
  - Not yet: frames through the receiver (`ledchannel_test.py frames`'s P0
    on this route), the 20-ms-and-under error rate over thousands of
    changes, and what the user's own Num/Scroll press does mid-frame.
- **The 2.4G link's ACK timeouts so far:** 109 in 5,009 frames (0.022 a
  frame), typing included, against BT's 0.042 on record. Not yet a controlled
  burst.

## 3. P0 frames through the receiver, from Windows over 2.4G (10-19)

`scripts/ledchannel_test.py frames` with its Windows sender (the keyboard-class
IOCTL, P0 only, the user's real locks as the baseline, the warm-up on Num):
six frames a run, 20-37-byte texts, 178 writes for the longest, each checked
byte for byte against what `lockchan.c` committed. Results in
`history/ledchannel-2026-10-19-gremlin*/`.

| write spacing | committed exactly | anchor to commit | UART overruns |
|---|---|---|---|
| 3 ms | 0/6 (gap aborts) | - | +1 |
| 5 ms | 0/6 (gap aborts) | - | +0 |
| 6 ms | 0/6 | - | +0 |
| **7 ms** | **6/6** | 1141-1655 ms | +0 |
| 8 ms (twice) | 6/6, 6/6 | 1217-1768 ms | +1, +2 |
| 10 ms | 6/6 | 1515-2194 ms | +0 |
| 12 ms | 6/6 | 1821-2616 ms | +1 |

- **A cliff between 6 and 7 ms:** closer than that the dongle merges changes,
  a merged change is a lost digit, and the frame dies on the 500 ms gap.
  (Scroll alone alternating survived 3 ms in section 2: one bit flipping
  cannot show a merge as anything but a missing pair.) **A sender should use
  10 ms** for margin: a 30-character title in ~2 s, against P0's 4.4-5.6 s
  over BLE on the Mac and P3's 1.5-2.2 s there.
- No pass >= 25 ms in any run.
- ⚠️ **UART overruns: +0 to +2 a run** (~1 minute each), well above the
  link's ~1-2 an hour. Each change arrives three times, so a frame is ~530
  module frames in ~2 s; the overruns did not cost a frame here (the
  repeats cover a lost copy), but they are the link's known weakness and a
  long run should count them.

## 4. The RX tap (firmware branch `gremlin/rx-tap`, 10-19)

The parser's 3-byte window drops any byte outside a 5A/5B/5C frame or an ACK
without a trace, so whether the dongle's `FF60` interface relays to the board
could not be told from "no reply". The tap: a dev-only 260-byte ring of every
raw module byte while armed, `HC_DEVHOOK` ops 15-16, `scripts/devhook.py rxtap
arm | stop | json` (the read splits frames, ACKs and the rest).

**Two codex reviews of `f2e22aae`**, the same prompt
([`review-prompt-rx-tap.txt`](review-prompt-rx-tap.txt)): the Mac's
([verbatim](review-codex-rx-tap-2026-10-19.md)) and gremlin's
([verbatim](review-codex-rx-tap-2026-10-19-gremlin.md)). **Neither has a
must-fix.** Dispositions:

| finding | both reviews | disposition |
|---|---|---|
| The count's 2^32 rollover skips slots 256-259 (2^32 % 260 = 256) | should-fix | **Fixed in `4ee56290`:** the slot is its own index, wrapping at 260; the count is only a total. It also drops the M0's software modulo per byte. |
| Injected bytes reach the tap in a build with both `WDT_TEST_HOOKS` and `AK820_DEV_HOOKS` | Mac: should-fix; gremlin: note (no standard flavour combines them) | **Fixed in `4ee56290`:** the tap sits in the real, unmuted read's branch only. |
| Zero added overruns cannot be certified statically; the ring holds ~231 ms at the burst rate | note | **Agreed.** Captures stop before reading, and the probes are short and spaced; overruns are counted beside every capture. |

Bounds, header layout, placement (each real byte once, before the parser),
main-loop-only access and the dev-only guards: passed by both.

## 5. ⚡ The dongle relays raw HID to the board: module frame `81` (10-19, 17:45)

Flashed `via-dev-4ee56290` (the RX tap, hands-free through the dev build's
jump). Control first: two Scroll changes through the keyboard-class route
showed in the tap as `5a 05 5f` x3, `5a 01 5b` x3, beside an ACK and a `5c`.

Then, the tap armed, one 32-byte output report on the dongle's `MI_03`
(`FF60`/`61`) each, 2 s of baseline before:

```
sent 01 + 31 zeros        -> board: 81 01 00 .. 00 82            (34 bytes)
sent 07 13 01 + 29 zeros  -> board: 81 07 13 01 00 .. 00 9c      (34 bytes)
```

**A new module-to-SN32 frame: `81`, the 32-byte report verbatim, then a sum
by the same rule as `5A`/`5B`/`5C`** (`(0x81 + Σ payload) & 0xFF`). Both
arrived intact; the parser's 3-byte window drops them, which is why it was
never seen. No reply reached the host (our firmware does not handle `81`).
No overrun, no stall.

So **host-to-board raw HID over 2.4G exists**, with the full 32-byte report.
Unknown: the reverse (a board-to-module frame the dongle returns on `MI_03`'s
input report: look in the stock firmware first), the latency and loss of `81`,
and whether BLE has an equivalent.

## 6. Raw HID over 2.4G, stage 2: two-way, dispatched, passing (10-19, ~22:30)

The board now answers allow-listed raw-HID requests over the dongle (text,
health reads) with the same reply it gives on USB plus a CRC-checked receipt.
On gremlin, from Windows: every text write read back from the board's stored
state, every health read matching USB, every NAK provoked, and 999 of 1000 in
a back-to-back stream (the one loss the UART-overrun taint the board counted),
RTT median 26 ms, no pass >= 25 ms, no keyboard frame lost. Design, five plan
revisions through codex, the implementation review and the numbers:
`plans/RAWHID-24G-PLAN.md`, from "Stage 2: dispatch, as designed".
Follow-up: ~1 in 4 transactions costs a 10 ms pass (latency, not loss).
