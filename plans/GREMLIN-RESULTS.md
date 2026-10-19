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
  byte). So the lock-light channel's first Windows question likely has a yes
  over 2.4G, without touching the system's lock state. Not yet written:
  pending the experiment below.
- **`MI_03` has QMK raw HID's usage on the dongle.** Whether it reaches the
  keyboard (a two-way channel over 2.4G) or is the dongle's own is unknown;
  nothing has been sent to it, since an unknown vendor command could change
  the dongle's state.

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
- Left: `IOCTL_KEYBOARD_SET_INDICATORS` through the keyboard class driver
  (Num/Caps/Scroll/Kana only, so no padding bits; does Kana arrive?), and
  whether the dongle's `MI_03` raw-HID-usage interface relays to the board.
