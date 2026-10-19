# Handoff to the Gremlin session (Windows 11, its own AK820 Pro), 2026-10-19

From the Mac's session (`ak820-pro-32`), at JD's request. Three areas
are Gremlin's: **the lock-light channel, the 2.4 GHz dongle, and Windows
testing of work already done.** Everything below is pushed; pull first.

## Where things stand

- **Firmware:** `ak820pro-jdlien` at `de5450e487`, pushed and pinned in
  `deps.lock` (ChibiOS-Contrib's `ak820pro-patches` at `27e852d25a`), so a
  fresh `./setup.sh` builds it. It carries Phase 2's gauge and power ladder,
  **deep sleep** ([`DEEP-SLEEP-BUILD-PLAN.md`](DEEP-SLEEP-BUILD-PLAN.md);
  [`docs/hardware.md`](../docs/hardware.md), "Deep sleep"), and **the power
  modes' new names and bulb icons** ([`POWER-MODE-ICONS-PLAN.md`](POWER-MODE-ICONS-PLAN.md)).
  The Mac's board runs `83d8768c64`, the same code. Codex reviewed all of
  it before it was flashed.
- **Measured on the Mac's board (10-19):** a 100-cycle soak through the
  ladder passed 97 (the 3 were the link's long-standing UART overruns, ~1-2
  an hour, not the sleep's; `BACKLOG.md`); Balanced's real 7-minute sleep;
  the 32-bit widths; Fn+B with no pass of 25 ms or more. Results in
  `history/deep-cycles-20261019-icons/` and `history/ladder-sleep-20261019-*`.
- **Gremlin's board** was last known on `8608c4f6-dirty` (flashed
  2026-09-05). Confirm before relying on it.

## Ground rules

1. ⚠️ **Never enumerate HID on Windows.** `ak820ctl` and the Python
   timekeeper still enumerate; use the Rust `ak820` CLI. (This line said
   `ak820text.open_device` did not; it did, once per process, its cache
   lasting one process. Fixed by the Gremlin session in `9880f47`: on
   Windows it lists the board's interface by hardware id, and
   `ak820health.open_device` delegates to it.)
2. **One clock owner per board:** Gremlin's daemon owns Gremlin's board.
   `flash.sh` ends with `ak820ctl clock` beside the daemon, which is two
   owners (`BACKLOG.md`, "flash.sh syncs the clock beside the daemon"): stop
   the daemon's task around a flash, or skip that step and let the daemon
   sync.
3. **Branches and pushes:** work on your own branches (`gremlin/...`) in
   both repos; push freely after a scrub for family or household details
   (the repos are public). Tags, releases and repo settings: ask JD.
4. **Flashes:** the reviewed commit (`de5450e487`) can go on as it is. Any
   new firmware code gets a codex review first, saved verbatim to `plans/`,
   every finding dispositioned
   (`codex exec -m gpt-6-astra -c model_reasoning_effort=xhigh -s read-only -C <repo> -o <out> "$(cat prompt)" < /dev/null`;
   tell it to read only inside the repo and that there is no AGENTS.md).
   Flash only with JD's go. ⚠️ On a dev build, raw HID can drop the board
   into its bootloader, which looks dead (USB id `0x7140`).
5. **Files:** leave `plans/current-status.md`, CLAUDE.md's "Current state"
   section and `DEEP-SLEEP-BUILD-PLAN.md` to the Mac's session; send results
   to `ak820-pro-32` or write them in a results doc of your own.
6. JD is often busy: **one hands-on action per message**, in plain words.
7. Windows builds: the **MSYS2 MinGW 64-bit shell** only, and the four
   traps in [`docs/hardware.md`](../docs/hardware.md#building-and-flashing-on-windows-msys2).

## The work, in a suggested order

**0. Bring Gremlin's board up to `de5450e487`**, the cold way: pull,
`./setup.sh`, `./build.sh dev` (or `daily`, what releases ship), `./flash.sh`.
Write down every snag a stranger would hit: it feeds the owners' README
(`BACKLOG.md`, "Next phase"). After it: `ak820 health` (no stall of 25 ms or
more), and the panel's bulb in the bolt's slot on battery. Which LCD variant
the board needs (default or `fpb`) is itself worth recording.

**1. Windows testing of work already done, on a second unit:**
- **Deep sleep** (on battery only: unplug, slider on BT): Balanced should
  sleep after 7 idle minutes (screen and lights off, ~3 mA), a key wakes it
  and the waking key types once, the knob wakes it. With the cable in it never
  sleeps. The sleep's figures are `HC_POWER` page 1 (`hostagent/ak820battery.py
  sleep` decodes it, but enumerates HID as it stands: give it
  `ak820text.open_device`, a small fix worth making).
- **The gauge on another pack:** whether the level is plausible over a day
  (the curve was fitted on JD's Mac board alone; `docs/battery.md`).
- **The daemon with the new firmware:** its syncs and status file unchanged
  in shape; the open items in [`AK820-AGENT-PLAN.md`](AK820-AGENT-PLAN.md),
  "What is left" (the stall counters after a quiet night; suspend/resume's
  `sync (wake)` line, never run live).
- **The release path's clean install:** Windows Sandbox may be uninstalled;
  JD's Parallels VM on the Mac is a possible stand-in (ARM64, so the x64 zip
  runs emulated: note that if used).

**2. The 2.4 GHz dongle** (`docs/wireless.md`; `Fn`+`R`): pairing and the
slot UX; typing over it (`HC_LINK`: ACK timeouts per frame against BT's,
give-ups, overruns); `5C` battery reports over 2.4G; **deep sleep in 2.4G**
(the link and the first key after a wake); and whether lock-LED reports reach
the board over 2.4G at all, which the lock-light channel needs.

**3. The lock-light channel on Windows**
([`LOCK-LED-CHANNEL-PLAN.md`](LOCK-LED-CHANNEL-PLAN.md): the wire format,
built 10-18; its transport proven on the Mac, 24 of 24 frames). The open
question for Windows comes first: **can a Windows process write the
keyboard's LED output report with the channel's bits** (the padding bits
the lights never show), to a Bluetooth or 2.4G keyboard, without toggling
the user's Caps, Num or Scroll Lock? Windows owns keyboard collections; if
there is no route, the channel is macOS-only on Windows' side. Then the
plan's resume items 3-5 (a Caps change with padding bits set; the daemon's
sender; the wireless clock, later). Windows also sets Num and Scroll Lock,
which the Mac never does: the band's Scroll hold (`lockchan_holds_scroll`)
is worth watching against a real Scroll Lock there.
