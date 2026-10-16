# Current status

Updated 2026-10-15 (the newest block is first below; the crash hunt's notes follow it). The crash hunt's section was last updated 2026-09-24, 12:45; its plan is
[`CRASH-HUNT-PLAN.md`](CRASH-HUNT-PLAN.md). **Phase 1b is closed (10-14). The
INA228 power logger is on the pack and recording (10-15)**; next is using it,
below.

## Why

One spontaneous, unexplained watchdog reset on 2026-09-22 at ~13:13
([incident](../history/incident-2026-09-22-wdt/README.md)). The retained
operation breadcrumbs added that afternoon
([WATCHDOG-BREADCRUMBS.md](WATCHDOG-BREADCRUMBS.md)) will name where the main
loop stopped next time. The crash hunt adds what they cannot say (a CPU fault
versus a hang, the PC, stack depth, why blits time out) and tries to provoke
the next reset instead of waiting for it.

## 2026-10-15 — RESUME HERE

**Where things stand: the INA228 power logger is on the pack and recording,
and the keyboard is back in JD's daily use with it attached.** Phase 1b stays
closed (the 10-14 block below). The board is unchanged: flash 2e
(`efd5b0f74a`, a `dev` build). The logger's design and every caveat:
[`../docs/test-bench.md`](../docs/test-bench.md), "The power logger".

**The rig (JD, 10-15): three USB ports for a keyboard built to need none.**
- the keyboard, through the relay's cable (its 5 V on relay 2), on the dock
  hub `0-1.4` port 2 (`ioreg`: `AK820 PRO@00142000`);
- the relay (`USBRelay4`) on port 3;
- the QT Py on `0-1.4.1` port 4 (`QT Py RP2040@00141400`).

The keyboard is reassembled with the pack's lead brought out of the case:
pack → in-line SH socket → INA228 (the pack on Vin+, the keyboard on Vin−,
VBUS bridged to V+) → SH plug → board. − runs into the INA228's GND hole (the
ground tap) and on to the board; NTC runs straight through; the MAX17048's
Bat is on V+. JD re-pinned the new SH ends to the standard colours (red +,
black −, yellow for the pack's white NTC). Still, identify wires by position
anywhere on this harness, never by colour.

**Recording:** `history/power-bench-2026-10-15/power.csv` since 21:01:13
10-15 (the recorder's PID in `power.pid`, its notes in `power.log`). The
swap's first 20 minutes are in `power-unflipped.csv` and `power-flipped.csv`,
with the sign wrong in places (`power.log` says where). The QT Py's LED:
dim green = recording, amber = nothing reads it, red = the INA228 does not
answer.

**Checked 10-15:**
- the board runs and charges through the harness, and draws **74 mA on
  battery** (RGB on, BT linked);
- the relay still switches it (20:53-21:01);
- the MAX17048 answers;
- the charge register matches the mean current with the temperature
  conversion on (ratio 0.990 over 15.8 min).

**Found 10-15:**
- **The shunt was wired backwards.** JD swapped the two + wires at the
  terminal block (~20:59); checked +74 mA on battery and −136 mA charging.
- ⚠️ **The INA228's VBUS reads ~5.1% (~214 mV) low** against both the
  MAX17048 and the board's `5C` clamp. It held at every current, with VBUS on
  either side of the shunt, and with the solder jumper bypassed by a wire. So
  it is not the harness, the jumper or the MAX17048's wiring. **Take the pack
  voltage from `vcell_mv`.** The next drain shows whether the error is a
  ratio or an offset.
- ⚠️ **Unexplained: the LCD read 22% at 20:47:34**, after the swap's power
  cycles, with the pack full (JD's photo); it said FULL, 100%, by 20:51. The
  saved level is written at FULL (100% on 10-14 23:16), so a restore should
  not give 22%. Read the restore path before trusting the level after a pack
  swap.
- The MAX17048's own SOC read 186-203%: its model, on an unknown cell, is
  meaningless here. Its `VCELL` is what counts.
- `uhubctl`: the keyboard is still on `0-1.4` port 2, where the bench doc puts
  it, so the data cut should still reach it. But `uhubctl -l 0-1.4` printed
  nothing within a 10 s limit (10-15). Recheck before relying on it.
- The logger's code changed 10-15 (all in `docs/test-bench.md`): a `temp_c`
  column; the header repeated every minute, since the recorder's unit check
  had never run; the status LED; and in the recorder, escape-code stripping,
  a report of wrong-length rows, a refusal to append to a CSV with other
  columns, and `SHUNT_SIGN`.

### Flash 2f: the backlight from the host (10-15)

JD asked for it (10-15): the per-load current map must step the screen with
nobody at the keyboard. `HC_DEVHOOK` ops 4-6 (dev builds only): set the
backlight level, switch it off or on (as `Fn`+`Home`, without the overlay),
read it. All three reply `[level, max, on, effective, saved+1]`. RAM only.
Host: `scripts/devhook.py lcd [LEVEL | on | off]`. Firmware `523744c30f`
(local on `ak820pro-jdlien`, **not pushed**: JD's call), built as
`via-dev-523744c30f-20261015-215932.bin`, token `0x754f00c1`.

**Codex review** ([verbatim](review-codex-flash2f-2026-10-15.md), prompt
[`review-prompt-flash2f.txt`](review-prompt-flash2f.txt)): no blocking
finding.

| # | finding (short) | disposition |
|---|---|---|
| 1 | every access is inside the 32-byte report on the real dispatcher; `length < 5` would not guard a shorter buffer | **No change:** unreachable here (codex's own evidence: the dispatcher always passes `RAW_EPSIZE`), and op 2's probe has relied on the same since flash 2d. |
| 2 | nothing set by ops 4/5 can reach flash; `Fn`+`PgUp`/`PgDn` afterwards persists the temporary level ±1; the reply's "saved" is the RAM cache, possibly a pending write | Agreed. A key press adopting the level shown is what a press means. Noted under `devhook.py lcd`. |
| 3 | no torn state or stuck pin; "on" writes the pin high for at most one 50 µs tick before the PWM resumes | Agreed, existing behaviour (the key path is the same). |
| 4 | should fix: the raw HID reply path (`send_report`, 100 ms timeout, queue locks) could stall the main loop ≥ 25 ms if the IN endpoint backs up | **Pre-existing, not changed by this flash:** every host tool's reply takes that path. Watched instead: the map reads the stall counters before and after; any `count_ge_25ms` there reopens it. |
| 5 | dev-only, and `build.sh` still rejects a daily or fpb carrying the hook; instrumented has no negative assertion | Agreed; instrumented builds are never released. |
| 6 | should fix: `devhook.py` accepted a reply to another op, crashed on a short report, and let bad arguments through | **Fixed (host only):** a whole 32-byte report with the echoed op, or nothing; usage checked before the device is opened. `current_map.py` checks the echoed op too. |
| 7 | for the map: the PWM is nonlinear (`bkl_duty[]`, ~417 Hz); VIA RGB changes trigger the parameter overlay (2 s plus its restore); "off" is the backlight only; the battery log's `bkl` is the level at the interval's end | Agreed. The map holds 90 s and skips the first 15 s of each state, well past the overlay. |

### Next, in order (10-15)

1. **The per-load current map** (Phase 2's step 2, the "afternoon with the
   meter", now logged, and hands-free for the lighting). On battery (relay
   off), hold each state ~2 min and take its mean from the charge register
   (Δcharge/Δt, gap-free), each state twice in ABBA order:
   - **RGB:** off, JD's effect, static white at full, and a few brightness
     steps. All of them go over VIA's lighting channel (`ak820lighting.py`'s
     wire format) without its SAVE, so RAM only: no flash write, and a reboot
     restores JD's setting. Restore it at the end anyway
     (`ak820lighting.py restore`).
   - **The screen:** no host command sets it. Full, dim and off are
     `Fn`+`PgUp`/`PgDn`/`Home` by JD, and each writes the saved brightness. A
     dev hook (`HC_DEVHOOK`, a new op) would make it hands-free, but that is a
     flash: JD's call, with a codex review first.
   - **BT** linked against idle. The MCU's share needs the light-sleep build
     (Phase 2 step 4), later.
   - **Repeat near the bottom of a drain:** the buck-boost draws more current
     as the pack falls.
   - Measurement only: Phase 2 stays note-taking until JD says build.
2. **A whole cycle with the logger**, the plan's "calibration run"
   ([`BATTERY-GAUGE-PLAN.md`](BATTERY-GAUGE-PLAN.md)): a drain to the cut and
   a charge to FULL, run by `bench_trial.py` and `trial_guard.py` as before,
   with the logger alongside. It gives the pack's mAh (the charge register);
   its voltage curve under a known load (`vcell_mv`); `5C` against the pack
   across the range; the VBUS error's shape; the charge's CC-to-CV knee seen
   directly in the current (task 12, `K_CC` against the level); and
   `CHARGE_IR_MV` and relaxation at unplug. Before it:
   - decide RGB through the cut as before, or a lowered cut (RAM-only `cfg`);
   - fix `bench_trial.py`'s pack regex (about two lines): it matches only
     the `(estimate 5C …)` form, so some rows have an empty `pack_mv`.
3. **The 22% after the swap** (above): read the saved-level restore path. No
   flash without a codex review.
4. **Phase 2, the power ladder:** note-taking until JD says build (the agreed
   order in [`BATTERY-GAUGE-PLAN.md`](BATTERY-GAUGE-PLAN.md); the PPK2 is
   deferred to step 4). Item 1's numbers say what step 3 is worth.
5. **A release, JD's call when.** The current v0.1.1 (09-06) predates the
   gauge and has no `fpb` binary.
   - Tag `vX.Y.Z`; CI builds the agent zip.
   - `scripts/release-firmware.sh` uploads a clean `daily` and `fpb` of the
     pinned commit.
   - `scripts/sandbox-6b.ps1` checks the install on a clean Windows.
   - A macOS build is not released yet (no notarization).
6. **Low priority (`BACKLOG.md`):**
   - the level after a flash (a seed from `flash.sh`, or the provisional
     level);
   - a module command for macOS's percent;
   - quill4gen7's two channels, one Mac evening;
   - the CH582F firmware question, after Phase 2's numbers;
   - the firmware as a standalone repo (someday);
   - whether to commit the logger's CSVs (~6 MB a day at 1 Hz: gzip or
     decimate them first).

## 2026-10-14 (Phase 1b closed; superseded by the block above for what is next)

**Where things stand: Phase 1b
([`BATTERY-GAUGE-REFINE-PLAN.md`](BATTERY-GAUGE-REFINE-PLAN.md)) is CLOSED.**
Every gate is met, merged, pushed and written up. The design and its
measurements: [`../docs/battery.md`](../docs/battery.md) ("The gauge as built,
Phase 1b"), [`../docs/display.md`](../docs/display.md),
[`../docs/clock.md`](../docs/clock.md), [`../docs/leds.md`](../docs/leds.md).
- **Gate 8 met (10-14).** The charge from 0 (10-09), the 24 h battery soak
  (10-10), and every per-event check, **no measured event reaching 25 ms**:
  - plug-in and unplug;
  - the Fn+D page exits and a lighting write, with a track playing;
  - a track appearing;
  - typed text under a plug-in;
  - FULL on flash 2e (01:41 10-12);
  - **"Battery low"** (between 08:35 and 08:40 10-14) and **the RGB cut**
    (between 13:55:17 and 14:00:17 10-14), on the drain below.
- **The drain on flash 2e (11:34:16 10-12 to the cut,
  [`history/battery-2026-10-12-drain-2e/`](../history/battery-2026-10-12-drain-2e/README.md)):**
  - **runtime 50.35-50.43 h** wall clock (51.13 h last cycle, run 2 50.56 h);
  - **accuracy** against run 2's line: below 52% −0.2 to −4.6 points (within
    −2.0 below 30%); the flat top (70-85%) down to −7.2 (−8.3 last cycle);
    never above +0.4;
  - **the board was off twice on 10-14, under ~13 minutes in all,** for a
    harness swap that did not fit (below). The log was dumped first;
  - the recharge from the cut: relay on 14:01:17, the charger done between
    23:06:39 and 23:11:39 (~9.1 h), FULL a 2.6-point step, LOST/OVERRUN/
    self-check never set. The trial ended `done` at 23:26:39 and the guard
    with it.
- **Firmware merged and pushed (10-12, JD):** `ak820pro-jdlien` at `3e18c70a74`
  on `jdlien/qmk_firmware` (29 commits; the fork's default branch is now
  `ak820pro-jdlien`), pinned in `deps.lock`. The board's flash 2e
  (`efd5b0f74a`) is the same firmware but for comments.

**On the board: flash 2e**, `efd5b0f74a`, `via-dev-efd5b0f74a-20261011-192711.bin`,
token `0x71163528`, since 19:30 10-11. A **`dev` build**:
- **the raw-HID bootloader jump**, so `./build.sh dev` then `./flash.sh
  <artifact>` flashes with no hands. `flash.sh` dumps the battery log first
  and refuses on battery after the RGB cut;
- **`HC_DEVHOOK`**: `scripts/devhook.py press|reset|read` presses Fn+D and
  reads or resets the display probe;
- `daily` and `fpb` carry neither, and `build.sh` checks that.

**On the bench now (as of 23:26 10-14):** nothing running against the board.
**Relay 2 is ON** (the trial leaves it on after FULL), so the pack is full
and the board runs on USB; data on. The QT Py is on USB again (since 15:37
10-15) with **nothing on the harness**. Its code changed 10-15: a temperature
column, and a header repeated every minute (see "The logger runs", below). The
first recorder (`power.csv`) was stopped at 15:54 10-15, and a check run
(`power-v2-check.csv`, 15:57-16:13) confirmed the charge register in the new
mode. **Nothing records until the swap.**

### Next, in order (10-14; superseded)

The INA228 harness was rebuilt with 1.0 mm SH ends and swapped onto the pack
on 10-15: see the 10-15 block above for what came of it and what is next.

### The bench: tests without JD's hands ([`../docs/test-bench.md`](../docs/test-bench.md))

- **Charging:** the keyboard's USB cable runs its 5 V through **relay 2** of a
  USB HID relay (`16c0:05df`, on the dock hub `0-1.4` port 3).
  `venv/bin/python scripts/usb_relay.py status | on 2 | off 2`.
- **Confirm every switch with one `ak820battery.py` read** (its `supply` line).
  IORegistry presence cannot show it: the data wires bypass the relay.
- **Data:** `uhubctl -l 0-1.4 -p 2 -a off|on -e` cuts or restores the
  keyboard's data on port 2. Always pass `-l 0-1.4 -p 2 -e`: port 3 is the
  relay itself, port 4 JD's Stream Deck. Check `uhubctl -l 0-1.4` after any
  re-cabling, and run it under a timeout (macOS has no `timeout`:
  `perl -e 'alarm 10; exec @ARGV' uhubctl …`). ⚠️ On 10-15 that status call
  printed nothing within 10 s: recheck before a trial relies on it.
- **The power logger:** the INA228 and MAX17048 on the pack, read by the QT
  Py and recorded by `scripts/power_log.py` (10-15 block above).
- **The states:** relay off and data on reads the board on battery without
  charging it (QMK connects USB without VBUS); both off is truly unplugged
  (~0.2 mA left on D+); both on is charging and readable.
- **`scripts/bench_trial.py`** runs a trial end to end (wait for the cut,
  charge, relax, charge to full), logs every read and turns the relay off on
  any failure. **`plug_events`-style checks** (relay cycles with a health read
  around each) and the display probe make a per-event check unattended.
- ⚠️ **Test display changes with a track playing** (JD starts one): the
  page-exit and overlay stalls of 10-11 needed one, and every idle test missed
  them.
- ⚠️ **The slider must stay on BT or 2.4G.** On cable, a cut is a cold
  power-off, and the RAM log is lost.
- The "Christmas Tree" outlet (`scripts/bench_power.py`, through Home
  Assistant since 10-13) powers the Acasis hub. It is no longer in the
  keyboard's path.

### Results to keep

- **`K_CC` = 133.0 pm/h**, the mean of three trials. Each trial is the mean of
  its curve and runtime methods: 124 from 0%, 133 from 1.8%, 142 from 5%. It
  rises with the start level. **`RELAX_S` 1800 s** is confirmed by three
  relaxations.
- **Accuracy over a whole cycle:**
  - ±2.5 points from 52% to the cut;
  - up to −8.3 on the curve's flat top (4003-4016 mV covers 70-85%);
  - runtime 51.13 h, +1.1% on run 2.
- **No measurable USB-data overhead at full white** (trials 2 and 3 on
  data-only USB, −5.6% and +5.3% off the curve).
- **Small shapes go in a RAM tile:** every `lcd_fill_rect` run pays an 11-byte
  window command sent byte by byte. The bolt and the transport icon were the
  last stalls (`docs/display.md`).
- **macOS's Bluetooth battery is the CH582F's linear `5C`, not our gauge**
  (96% against 66.4% on 10-10; ~27% at the cut). System Settings reads it live,
  and `system_profiler` shows a stale cached value.

### Also

- **The community** (`BACKLOG.md`, "Community"):
  - **fpb** starred `jdlien/ak820-pro` and shipped our upstream items. **Touch
    base once the battery work and the ladder are near done**: lead with the
    gauge, and offer to test his v2 panel builds.
  - **quill4gen7** forked both repos on 09-24. Worth borrowing: the lock-LED
    host-to-board channel and the media-key board-to-host channel (one Mac
    evening for both), and a screensaver whose waking key still types. Their
    raw-HID bootloader jump is built (flash 2b, `dev` builds).
- **The repos:**
  - `jdlien/ak820-pro` (the hub);
  - `jdlien/qmk_firmware` (`ak820pro-jdlien`, a fork of `fpb/qmk_firmware`);
  - `jdlien/ChibiOS-Contrib` (`ak820pro-patches`, a fork of
    `SonixQMK/ChibiOS-Contrib`, the submodule);
  - `jdlien/time-util-ak820pro` (`ak820pro-local`, a fork of fpb's:
    `ak820ctl`);
  - adjacent: `jdlien/streamdeck-now-playing` (the MediaRemote helper's
    source);
  - pinned from fpb: `fpb/SonixFlasherC`.
- **Build:** the board's code lives on `ak820pro-jdlien` now. Build from a
  clean checkout of it (`./build.sh dev` for JD's board, `daily` for a
  release). The `phase1b-flash2` worktree in this session's job directory is
  merged and can go (`git -C qmk_firmware-ak820pro worktree remove <dir>`).
  The simulators take a tree via `BATTERY_SIM_SRC=` (and `DIAG_SIM_SRC=`,
  `CH582_SIM_SRC=<...>/bluetooth`).
- **The task list:** `.taskmaster/tasks/tasks.json` (local, untracked), tasks
  11-15.
- **Working rules these weeks taught:**
  - one hands-on action per message; JD's manual trials are costly, so
    automate them (memory);
  - **never test a command that can act** (10-08: a test of `bench_power.py
    off` switched the outlet off);
  - confirm relay switches by the board's `supply` line;
  - a codex review (gpt-6-astra, xhigh) before every flash, verbatim, every
    finding dispositioned;
  - **measure before fixing** (10-11: the staging guess missed; the probe
    found the stage).

### Phase 1b's flashes (all on `ak820pro-jdlien` now)

| Flash | Commit | What | On the board |
|---|---|---|---|
| 2 | `28f260225f` | the charging model, log v5, the D fix, C2, the camera page | 12:54 10-08 |
| 2b | `f79b31b68e` | the raw-HID bootloader jump (`dev`) | 15:39 10-11 |
| 2c | `9816c09e79` | one battery component a pass; the bolt as one RAM tile (plug-ins 24-26 to 11-12 ms) | 16:45 10-11 |
| 2d | `6e5de5bdaa` | the display probe and `HC_DEVHOOK` | ~19:10 10-11 |
| 2e | `efd5b0f74a` | the transport icon as one tile; no redundant forced clear (page exit 28.3 to 12.7 ms, overlay 23.1 to 12.7 ms) | 19:30 10-11, now |

`3e18c70a74` adds only comments and is the pinned head.

## 2026-10-04, night (superseded by the block above)

**Next: execute [`BATTERY-GAUGE-REFINE-PLAN.md`](BATTERY-GAUGE-REFINE-PLAN.md)
top to bottom, starting at its Step 0.** That is Phase 1b: refit the curve on
both runs, the charging display, the internal-flash writes behind the LED
blink, and the battery-only stalls. It is at revision 7, after six codex
rounds (gpt-6-astra, xhigh); round 6's verdict was "ready to execute". Every
round is verbatim in
[`review-codex-battery-refine-plan-2026-10-04.md`](review-codex-battery-refine-plan-2026-10-04.md),
and the dispositions are at the end of the plan. **Phase 2, the power
ladder, comes after it.** It is note-taking only until JD asks.

- **Run 2, the validation drain, is done.** Unplugged 10-02 11:00:00, the RGB
  cut came at 13:33:25 10-04 (50.56 h), and the board died at 18:34:54 (from
  JD's time-lapse). See [`../history/battery-2026-10-01-drain/`](../history/battery-2026-10-01-drain/)
  and [`../docs/battery.md`](../docs/battery.md).
- **The 10-04 charge from flat** started ~20:02, with a preservation dump at
  21:44. ⚠️ **At 22:37 the board was in the bootloader**: JD entered it before
  bed for an overnight flash, but no firmware was ready, so the log after 21:44
  is lost. **Check the board's state first:** in `ioreg`, product 28992
  (`0x7140`) is the bootloader and 32777 (`0x8009`) is running. Then follow the
  plan's Step 0, which covers both cases.
- **On the board: `759e265796`** (the fitted curve, whole percent), unless
  something else has been flashed since. Rollback artifact and build token:
  the plan's "Firmware, branch, and artifacts".
- ⚠️ **The keymap and lighting backups date from 09-29 13:57.** `flash.sh`
  must take fresh ones from the RUNNING board; see the plan's flash
  procedure.

## 2026-10-01, morning (superseded by the block above)

**The 09-28 drain test is over, and the curve is fitted, committed and NOT yet
flashed.** Plan and dispositions: [`BATTERY-GAUGE-PLAN.md`](BATTERY-GAUGE-PLAN.md).
Evidence: [`../docs/battery.md`](../docs/battery.md). Run records:
[`../history/battery-2026-09-28-drain/`](../history/battery-2026-09-28-drain/)
-- `readings.csv` (every observation, JD's words, corrections), the
`log-*.csv` dumps, and the fit's `fit-output.txt` / `fit-points.csv`.

**On the board since ~20:20 10-01: `759e265796`** (the fitted curve, whole
percent). The charge from flat before it is dumped and analysed in
`history/battery-2026-10-01-charge/`. **A validation run started at the
unplug, 10-02 11:00:00** (not 10-01 20:19: the board stayed on USB
overnight): `history/battery-2026-10-01-drain/`, white at full drive as on
09-28. The cut is due ~51.6 h later, ~14:36 Sunday 10-04. ⚠️ **Dump the
charge log before any flash or slider flip** -- a charge from flat on this
firmware is wanted for the charging side (CHARGE_IR_MV, the CV creep). The
09-28 charge took ~9.5 h; dump when the charge LED goes out (CHRG released).

**How the run ended:** FULL ~19:52 09-28 (log VDD); "Battery low" ~00:00 10-01
(3544 mV); the RGB cut ~02:13 (from the log's led_pm), **54.4 h wall, 51.6 h
equivalent full white**; lights off after that; "Low" from ~04:24 (<3.30 V);
last seen 05:19 at 3222 mV; **found dead 08:45** (the log after the 05:03 dump
died with it). On replug, 5C read a fresh 0 under charge: the cell went well
below 3.16 V before the protector cut it. A probe short at ~20:44 09-30 lost
~45 min of log and needed a slider flip (cable out) to revive -- see the 09-24
shutdown section of docs/battery.md.

**Phase 1 gates: all met.** 2 (protection) on the last night -- warn, cut, the
cut survives the user, 5 s of USB lifts it, it re-cuts on battery. 4 (meter vs
gauge) -- eight points 4.01-3.22 V, −9 to +11 mV, including 3.22 V below the
fitted range.

**The fit** (`scripts/battery_fit.py`): 0% at the cut (3400 mV, JD's choice),
knots at even steps of level, the clamp exit at 89.6% (the 90% anchor stands).
Each USB plug-in is credited with the charge it put back (r = 10.5; durations
from each entry's mean VDD) and the dim start at its lower power; ±20% on
those moves the level at 3.70/3.55/3.50 V by ≤ 0.1 point. Simulator 27/27
after updating three expectations that were the old curve's numbers (343 ->
187 pm at 5C 70; "starts high" ≥ 50). Firmware commit on `ak820pro-jdlien`.

**Next:**
1. ✅ Charged, dumped, flashed (`759e265796`). The validation run: panel
   readings with times are the data (no plug-in needed); dump rarely, and
   again from the 'Battery low' warning on; the security camera from there.
   Then compare the panel against the runtime it actually had left.
2. **The queued fixes** (plan, "Queued"): "Charge" not the 95 guess while
   charging at the clamp with nothing saved; keep the re-seat owed past a
   clamp reading just after unplug; the stalls (`25:6 10:2449`, worst 35 ms
   blit -- measure first); the backlight flashing bright during a flash write;
   the warning's threshold and re-arm (JD to decide); later, drop tenths.
3. Validate the curve on a second run, ideally filmed (plan, ideas).
4. Phase 2 is **not built**; JD is in note-taking mode.

**Traps from this run:**
- `flash.sh` backs up keymap and lighting **only if the board is running** --
  do not enter the bootloader first, or it restores the 09-24 pastel.
- A flash wipes the RAM log and the EEPROM: **dump first**.
- Dump with the slider on **BT**, then unplug within a minute (longer is a
  charging session, which re-seats the level and charges the pack).
- A loose cable charges without enumerating: check `ioreg` for `0C45`.
- ⚠️ **`0C45` alone does not mean running**: the bootloader is `0C45:7140`,
  the board `0C45:8009`. Before a flash, prove it is running with a raw-HID
  read (`ak820battery.py` answers), not the vendor id -- on 10-01 the board
  was already in the bootloader and flash.sh restored the 09-29 backups.
- Use `set -o pipefail` in dump one-liners, or a failed dump prints "saved".
- 09-30 19:57: `hostagent/ak820battery.py` refused ("no repo venv provides
  'hid'") although `venv/bin/python3` imports `hid` fine. Run
  `venv/bin/python3 hostagent/ak820battery.py log ...` directly. Cause:
  `venv/bin/python3` symlinks to pyenv's 3.13.9, which is also what
  `#!/usr/bin/env python3` runs, and `venv_bootstrap._same()` uses
  `os.path.samefile` (follows symlinks), so its exec-loop guard calls the
  venv "the interpreter we already are". Fix (not applied): guard on
  `sys.prefix` against the candidate's venv root instead.
- **Docs pass, 09-30 evening:** `docs/battery.md` restructured (current
  facts first, withdrawn claims in a dated table at the end), the display
  doc's battery row and debug row, firmware comments (comment-only), and
  as-built notes in the plan. `5C` cadence: 2-3 reports per 5 s poll cycle,
  bunched -- the mechanism is unconfirmed.

## 2026-09-28, 14:55 (superseded)

**The battery gauge (Phase 1) is built, not flashed.** Everything is in
[`BATTERY-GAUGE-PLAN.md`](BATTERY-GAUGE-PLAN.md): the plan, codex's review of it
([verbatim](review-codex-battery-gauge-plan-2026-09-28.md)) and a disposition
per finding.

- **Artifact:** `via-daily-f2bcc26f14-20260928-145123.bin` (token
  `0x97fc73c0`), firmware `f2bcc26f14` on `ak820pro-jdlien`. The idle ladder
  is compiled out; the VDD estimator is deleted.
- **Reviewed twice by codex** (plan, then implementation: flash after eight
  fixes, all applied). `scripts/battery_sim/run.sh`: 23 scenarios, 15 mutants
  caught.
- ✅ **Running since 2026-09-29 ~13:58: `deef6053dd`**
  (`via-daily-deef6053dd-20260929-135459.bin`) -- the level from a trimmed mean
  of 64 `5C` reports, the panel in tenths (`BATTERY_SHOW_TENTHS`, for the
  calibration), ~6 saved-level flash writes a cycle (every write blinks the
  LEDs ~7 ms), log v4. Flashed mid-way through the 09-28 drain test after a
  dump; the run continues. Queued fixes: plans/BATTERY-GAUGE-PLAN.md, "Queued".
- (Previously `487cb8e9f0`, from ~15:41 on 09-28: (`via-daily-487cb8e9f0-20260928-153833.bin`)
  -- `27bf8a4f06` plus "Charge" for "Chrg". ✅ **The charger does terminate
  with the board running in BT on USB:** a top-up of a full pack held CHRG
  low ~11 min (VDD 4484-4489 mV), then released at 15:43 with VDD 4530 mV;
  the gauge went to full and 100%, and saved it. (The worry was the 09-28 note
  that the LED went out "with the board off".)
- (Re-flash ~15:36: `27bf8a4f06`)
  (`via-daily-27bf8a4f06-20260928-153336.bin`), which adds the saved level: a
  reboot at the top clamp (every slider flip) restores it instead of guessing
  95. First read after the flash: BT position on USB, charger topping up,
  level 95 -- the guess, because the flash erased the saved level. It goes to
  100 when CHRG releases, and is saved from then on.
- (First flash, ~14:58, `f2bcc26f14`, from the bootloader, so the keymap and
  lighting came from the 09-24 backups (lighting: effect 2, val 137 -- not the
  solid white of the 09-25 run). First read on USB, cable position: supply
  external, state full, level 100%, `5C` 100, VDD 4831 mV, log period 600 s,
  thresholds 3550/3400 pack mV.)
- **Next: the bench checks** (Phase 1 gates 1-7). ⚠️ The meter comparison and
  the protection test need the pack **under the top clamp** (`5C` < 100),
  i.e. some hours of use on battery first.
- `deps.lock` still pins `44e7314e65`; move it when this is released.
- The 09-27/28 meter readings were recovered from a session transcript into
  `history/battery-2026-09-25/readings.csv`. ⚠️ Write readings there as they
  are taken, not only in conversation.

Everything below is the 09-27 state, kept for its findings; where it says the
battery row must stop showing VDD, or to calibrate the estimator, the gauge
has superseded it.

## Installed right now (2026-09-27, 13:00)

**The work is on the battery: read [docs/battery.md](../docs/battery.md)
first.** Task 8 in `.taskmaster/tasks/tasks.json` tracks it.

- **Firmware: `via-daily-b35d8672b3-20260924-205842.bin`**, token
  `0x583b65cf`. Production fixes (`6b60458dd0`) plus the start of the battery
  work: VDD from the SN32 ADC; a once-a-minute RAM log; low-battery warn and
  RGB cut at 3.55 V and 3.40 V on VDD; the voltage on the battery row.

### ⚠️ Three findings that change the plan

1. **VDD is a BUCK-BOOST output. It is useless as a gauge.** With the pack at
   3.81 V the debug page read **VDD 3.903 V** — above its own input, which no
   linear regulator can do. Across the run the pack fell **4.18 → 3.74 V
   (−440 mV)** while VDD moved **+3 mV**. ⚠️ **This kills the prediction that
   VDD would start following the pack below ~3.95 V.** There is no dropout
   coming.
2. ⚠️ **The estimator (`36be68f16a`) is dead on arrival.** It derives a level
   from VDD via a lithium curve, and VDD is a constant. **Do NOT calibrate the
   `CAL` constants in `battery.c`** — the earlier "Next" step in this file said
   to, and that instruction is withdrawn. The input carries no signal.
3. ⚠️ **The low-battery protection may be inert.** Warn (3.55 V) and RGB cut
   (3.40 V) are thresholds **on VDD**, which is pinned at 3.90. They cannot
   fire until the buck-boost collapses, possibly below the pack protection
   circuit's trip point. **Unverified — confirm before relying on it.** Cheap
   test: `ak820battery.py cfg 3950 3900` (RAM-only, resets on reboot) and see
   whether warn and cut fire at a VDD the rail actually reaches.

### ⭐ The module's `5C` value DOES track the pack

The premise that `5C` is pinned at 100 is **withdrawn**. It moves, and the
`Fn`+`D` debug page row 8 shows it (`ch582_get_battery()`, `display.c:878`).
Settled readings against a standard NMC curve:

| pack | 5C | curve | offset |
|---|---|---|---|
| 3.99 | 85 | 75.9% | +9.1 |
| 3.84 | 78 | 59.8% | **+18.2** |
| 3.81 | 74 | 56.2% | **+17.8** |
| 3.74 | 66 | 46.6% | **+19.4** |

**The last three agree within ±1 point.** `5C` moves 120 points/volt where the
curve moves 132 %/V — it tracks the curve's *shape*, which is why the offset
holds. ⚠️ **If it holds below 3.70 V the remap is a subtraction** (`level =
5C − 18`). The 3.99 outlier is unexplained: charge memory, a wrong generic
curve at the top, or real depth-dependence. **Readings below 3.70 V separate
them, and that is the single most valuable thing to collect.**

⚠️ `5C` is **noisy (±3) and useless while charging** — it snaps to 100 the
instant USB appears and decays over hours. Any gauge built on it needs a median
filter, a ratchet that never rises on battery, and a hold-off after charging.

### ⚠️ TODO before any release: the battery row must stop showing VDD

`b35d8672b3` shows **VDD on the battery row**, which replaced the CH582F's
hardcoded 100%. ⚠️ **It is a constant.** VDD is the buck-boost output and it has
moved 3 mV while the pack fell 440 mV, so the row conveys exactly as much as the
fake 100% it replaced — less, arguably, since "3.90 V" is not even in units a
user can misread.

It **earned its place as an instrument**: watching it sit still while the meter
fell is what exposed the buck-boost, and a percentage derived from VDD would
have hidden that behind a plausible-looking number. But it is a diagnostic, not
a feature, and diagnostics survive by inertia unless someone writes them down.

**Replace it with**, once the mapping is calibrated:

1. `V_pack = (5C + 374.5) / 117.8` — exact to ~±3 mV, ~8.5 mV per count
2. that voltage through **this cell's** curve, fitted from
   `history/battery-2026-09-25/readings.csv`, not a generic NMC table
3. displayed as **bars plus a percentage in 5% steps** — the granularity is what
   tells the user it is an estimate; 1% resolution is a claim we cannot back
4. **ratcheted**: never rising while on battery, reset on charge
5. ⚠️ volts stay on the `Fn`+`D` debug page, where an engineer wants them

### The discharge run

- **Started 2026-09-25 10:54.** ⚠️ Interrupted 09-26 13:22 when the slider went
  to `cable` to wake the Mac, which **reset the board and lost the RAM log**;
  ~20 min on the charger, unplugged again 14:03. Running since.
- **Readings: `history/battery-2026-09-25/readings.csv`** — meter, screen, and
  the analysis inline.
- **At 12:54 on 09-27:** pack **3.74 V**, `5C` **66%**, 49.3 h elapsed, ~51%
  consumed at 1.03 %/h. **Projected ~94 h total (3.9 days), ~45 h left.**
  Lighting full pastel to 15:36 on 09-25, **solid white 100%** since.
- **What to do:** a meter + debug-page reading whenever you pass it. That pair
  is the calibration data. Nothing else is needed from the run.

⚠️ **Two traps for the next run.** The RAM log is **720 entries at one a
minute = 12 hours**, then it wraps — a multi-day run keeps only the last 12 h
unless `LOG_PERIOD_MS` goes to 300000 (5 min → 60 h). And **the Mac needs USB
to wake**, which is what ended this run; use `caffeinate -d` or a second
keyboard.

### Hardware decided on, not yet ordered

- ✅ **MAX17048 fuel gauge** — DigiKey `1528-5580-ND` (Adafruit 5580). The
  retrofit. ModelGauge, no sense resistor, ±1-2%.
- ❌ **1.25 mm 3-pin male+female pigtails**, Amazon ~$15/20 sets, for the pack
  tap: **the wrong size** (10-14). The pack is 1.0 mm, most likely JST SH;
  a JST SH kit replaces them (above). ⚠️ Digi-Key's ready-made PicoBlade assemblies are **female-to-female**
  and cannot make a passthrough alone.
- ✅ Keep the **JST SH 4-pin Qwiic cables** already in the cart — they mate
  with the MAX17048's STEMMA QT, so no soldering to the module.
- 🤔 **QT PY RP2040** — optional, only as a bench rig to prove the gauge before
  touching the keyboard. Any spare MCU does.
- ❌ **INA228 dropped.** Its purpose was fitting a load model for a
  software coulomb count; the MAX17048 replaces that in hardware.
- ❌ **PPK2 not now.** Right instrument for the idle-ladder work later
  (task 9), wrong one for this. ~CA$150 at DigiKey, not the CA$255 Amazon ask.

### ⭐ The retrofit is much easier than it looked

**The board already has an I²C bus.** `halconf.h:15`: an external **PCF8563
RTC on P0.14/P0.15** over the ChibiOS software (bit-banged) I²C fallback LLD.
So SDA and SCL are routed, a driver exists and works, and **no free GPIO is
needed**. MAX17048 is `0x36`, PCF8563 is `0x51` — no conflict. Solder targets
become the PCF8563's SO8 pins or its passives, **not** a 0.5 mm QFP leg.

Full plan, wiring, the three electrical checks (logic levels across two
supplies, pull-ups that may not exist as components, bus capacitance) and the
pin audit: **[docs/battery.md](../docs/battery.md)**, "A real fuel gauge,
retrofitted".

⚠️ The bus is **rationed** — one transaction per main-loop pass with the LCD
DMA idle, because RTC I²C on port A glitches the flash SPI1 pins mid-DMA
(`ak820pro.c:585`). A gauge read every 30 s is negligible but must go through
that gate.

### What is achievable — read before building anything

A trustworthy percentage is **not reachable on the stock hardware**: no ADC
path to the pack, `5C` is the only signal and is voltage-derived, and NMC's
curve is flat through the middle. **The objective is a 5-bar / 10% indicator
that never goes backwards, ±10% mid-range and better at the ends, plus a low
warning that actually fires.** The MAX17048 is what buys a real percentage.

- **Committed locally, not flashed, not pushed** (firmware `ak820pro-jdlien`):
  `91d854f2ff` and `b35d8672b3` (the flashed build); `36be68f16a` (the
  estimator — ⚠️ **do not calibrate, see above**); `3b85686ff7` (the idle
  ladder — ⚠️ **do not flash during a calibration run**, it dims the lights and
  changes the load profile mid-measurement).
- **Pushed 2026-09-24 ~14:00:** ChibiOS `a4f8412134`, firmware `6b60458dd0`,
  main `882b322`.
- **Bluetooth test, 2026-09-24:** 57 minutes on BT with the cable in: 9,597
  frames, 0 drops, 0 malformed, 2.9% late. Then, unplugged, the board **went
  dark with charge left** and came back only on USB: unexplained
  (docs/battery.md).
- **Still not done:** the `HC_BLITFAULT` forced-failure tests; a hunt on
  `6b60458dd0` (`deps.lock` still pins `44e7314e65`).
- **Also open:** the unplug glitch (task 8.6), and the RGB colour breakup at a
  215 Hz field rate (task 9) — ⚠️ **not fixable in firmware**: the fringe
  scales linearly with field rate and needs ~4 kHz, past this M0. Colour choice
  is a free 2×, and a single saturated channel has none at all
  (`docs/leds.md`).

## ✅ The overnight hunt on 44e7314e65 passed (2026-09-24 04:57)

Ten hours: 1,964,044 blits, no timeout of any kind, no busy-wait, no
non-flash stall of 25 ms or more, no reset
([evidence](../history/crash-hunt-2026-09-24-final-daily/)).

## ✅ The DMA "never-start" is solved (2026-09-23)

Every "never started" blit timeout on record was a **completed** transfer
whose completion the SPI0 half-transfer handler erased. It read `RIS` and
then cleared every flag, so a DMATCIF raised between those two instructions
was lost.

Only 660-byte clock digits were exposed, and the reason is timing. The
equal-priority LED row ISR delays that handler until 188–258 µs after the
arm, and a 660-byte transfer completes at 220 µs. The fix (ChibiOS
`c57623d0d2`) clears only what was read and re-checks for the completion.
E5 ran **3 hours and 580,060 blits with no timeout**.

Found with `CURCNT` instrumentation. Along the way, `DMACNT` turned out never
to count down, so "never started" was a label, not a diagnosis. A Codex review
(`gpt-6-astra`, reasoning effort high) supplied the equal-priority
correction. It also found the other fixes of the day:
- the USB lock nesting;
- the CH582F pump order;
- the `IC` read-modify-write.

Full analysis and every disposition:
[`FIRMWARE-FINDINGS-2026-09-23.md`](FIRMWARE-FINDINGS-2026-09-23.md).
Evidence:
[`history/crash-hunt-2026-09-23-lost-completion/`](../history/crash-hunt-2026-09-23-lost-completion/).

## ✅ The fault recorder works, after a fix (2026-09-23)

All seven `HC_FAULT` modes were verified on hardware. The first runs of
modes 1 and 3 came back invalid: on this chip **the last SRAM write before a
loop that never writes again is lost at the watchdog reset**, and every
handler commits its record and then spins. So the final `FAULT_MAGIC` store
was lost, every time. `DSB` doesn't help; one sacrificial write after it
does (`commit_terminal()`, firmware `02db293696`). The watchdog also **recovers a
locked-up core** (mode 4), with the record intact. Details:
[`CRASH-HUNT-PLAN.md`](CRASH-HUNT-PLAN.md), "Third result", and
[`docs/hardware.md`](../docs/hardware.md). ⚠️ The v7 daily build lacks the
fix: a fault on it would record as invalid, so a real crash would read
"unavailable".

## ✅ The fix held: ten hours, no reset (second hunt, 23:00 → 09:00)

Same stress that hung v6 in 13 minutes: no reset in 10 h and 1,960,093
blits. The guard caught 7 overlaps, **all 7 alongside a DMA that never
started** -- the predicted mechanism. All 53 blit timeouts were never-starts,
all recovered by one retry. All 8 non-flash stalls ≥ 25 ms (25–26 ms) came
with a never-start recovery. Deepest stack use: interrupt 464/1024, main
680/2048. Settings verified at exit; the agent restored itself at 09:00:15
and is writing v7 rows to `ak820-health.csv`. Evidence:
[`history/crash-hunt-2026-09-23-v7/`](../history/crash-hunt-2026-09-23-v7/).

The first hunt (v6, 21:05) was stopped at 22:03. It ignored `pkill -INT` (a
background job inherits SIGINT as ignored), so it was stopped with SIGTERM
and its settings restored from its backup by hand, verified; the script now
handles both signals. Its evidence is in
[`history/crash-hunt-2026-09-22/`](../history/crash-hunt-2026-09-22/).

**If the keyboard freezes in ordinary use:** leave it connected. The
watchdog brings it back in ~15 s; read `ak820 health --crash` (on v7 a fault
carries its PC: `scripts/symbolize.sh <pc> <token>`). A cold power-off
destroys the record.

## ⚠️ The hunt reproduced the hang at 21:18:52 — and the cause is found

13 minutes in: `lcd_transfer within text`. A synchronous LCD draw re-armed the
flash->LCD DMA while the glyph pump's last transfer was still in flight, which
left SPI0 unable to complete an ordinary `spiSend()`; no timeout, so the
watchdog. Full mechanism and fix in `CRASH-HUNT-PLAN.md` ("First result").
A second codex pass found the same hole in the CPU draws (Caps padlock,
battery fill, icons) and in every external-flash transaction, so the fix is
now `bus_quiesce()` at the start of every CPU transaction on either bus. That
also gives JD's 13:13 crash (typing, light load) a plausible path. A
narrow third codex pass verified the guard covers every runtime transaction
([review](review-codex-crash-hunt-impl3-2026-09-22.md)). Committed as
firmware `1b7f781887`; flashed 22:59 (see above).

## Health v7 (Part B) — daily flashed 2026-09-22 22:59; validated 2026-09-23

Daily: `ak820pro-builds/out/via-daily-1b7f781887-20260922-225845.*` (flashed
until the 2026-09-23 fault tests; lacks the lost-write fix above). A
HardFault or an unhandled vector now
writes a terminal record with the PC and the interrupted context, and a ChibiOS
halt records its caller. The record's consecutive-reset count clears after 10
healthy minutes, so three spread-out crashes can no longer switch the watchdog
off for good. Page 6 adds stack watermarks, the blit-timeout breakdown and a
build token that `scripts/symbolize.sh` resolves to the archived ELF.
Host simulation passes under ASan/UBSan; the Rust side decodes all of it (364
unit tests). Codex reviewed the implementation twice: no High and twelve
Medium/Low issues the first time
([review-codex-crash-hunt-impl-2026-09-22.md](review-codex-crash-hunt-impl-2026-09-22.md)),
two High (the unguarded bus paths above) and four smaller the second
([review-codex-crash-hunt-impl2-2026-09-22.md](review-codex-crash-hunt-impl2-2026-09-22.md)).
All fixed; the third pass left two small hunt-script fixes, also done. The archived
ELFs now carry line info: `scripts/symbolize.sh <pc> <token>` answers with
file:line.

## Next

1. ✅ The overnight hunt passed; `deps.lock` moved to `44e7314e65`.
2. **Built overnight, committed locally, NOT pushed or flashed:** firmware
   `6b60458dd0` on top of `44e7314e65`. It carries:
   - `lcd_blit_wait()` tracking CURCNT and DMAEN (start, progress, stall in
     a few ms, and the class);
   - the dashboard repainting after a blit given up for good;
   - the debug-page pump's recovery;
   - the internal-flash driver refusing to erase a sector behind a caller's
     back (ChibiOS `f247ebc639`);
   - the UART reporting hardware overrun (ChibiOS `a4f8412134`), counted on
     the instrumented console;
   - stale comments corrected.

   Clean builds: `via-instrumented-6b60458dd0-20260924-001130.bin` (token
   `0x2d80c2ff`) and `via-daily-6b60458dd0-20260924-001152.bin` (token
   `0x70bfdc04`).

   **The board's daily has no remote bootloader jump, so it needs
   JD's Fn+Esc once.** Then, on the instrumented build:
   - `HC_BLITFAULT 1`: expect an IRQ-lost timeout and a successful repaint;
   - `HC_BLITFAULT 2`: expect `[display] a blit was given up`, then a
     dashboard repaint;
   - the wireless test, with the console captured: ACK histogram, orphans,
     and UART overrun, framing and parity counts in Bluetooth mode;
   - an hour of hunting;
   - then the daily, and push.
3. **JD's wireless test** can use either build: the CH582F path is
   identical in both. It is the first real test of the serial fix and the
   pump order.
4. **Still open:**
   - FRESET SPI0 at hand-back (hygiene, deferred);
   - UART overrun counting;
   - the LED ISR's thread-class PWM calls;
   - the raw-HID flash-provisioning policy (anyone on the host can rewrite
     the external flash's assets);
   - the ACK deadline in Bluetooth mode;
   - Mac sleep/wake and the reboot test;
   - a tagged release.
5. **Parked idea** (taskmaster task 6): a QR code to the agent installer via
   a jqr.ca redirect.

## Repository notes

All pushed, 2026-09-23 ~19:00. Firmware `ak820pro-jdlien` is at `44e7314e65`;
ChibiOS `ak820pro-patches` is at `c212e20dd2`. That branch carries three
fixes from today: the serial fix `a3fdffe26d`, the SPI0 lost completion
`c57623d0d2` and the USB fix `c212e20dd2`. It also carries the reverted
dispatch attempt `2a17a73b48` and its revert `bf9310ca84`.

**`deps.lock` still pins `02db293696`** (v7 plus the lost-write fix). It moves
to `44e7314e65` once the overnight hunt passes. The recovery bundle in
`ak820pro-builds/` holds the new ChibiOS tip. `.taskmaster/` stays untracked, as
before; it now holds task 6, the QR idea. The hunt's raw output stays in
`~/Library/Logs/ak820pro/crash-hunt/`; a snapshot of the 21:18 evidence is in
[`history/crash-hunt-2026-09-22/`](../history/crash-hunt-2026-09-22/).
