# Deep sleep, the build: the ladder's last stage

Status: **revision 1, for codex's plan review (10-19). Not built.** The
experiment (`SLEEP-PLAN.md`, "Deep sleep: the experiment") proved the
mechanism over 10-18/19; this makes it the board's standby, on by default in
Normal, Saver and Minimal, in every build. It carries the decisions in
`SLEEP-PLAN.md` ("Decided", "Asleep, the RTC's second runs ~13% slow", "Time
left with deep sleep"). Every flash gets a codex review first.

## What the measurements say (10-18, 10-19)

- **Asleep: 13.35 mW at the pack** (3.24 mA at 4.12 V, the logger's 1 Hz
  samples over 05:46-06:43 10-19, inside the hour asleep), against ~114 mW
  awake and dark (27.5 mA). The pack's 15,579 mWh would last ~1,170 h
  (~7 weeks) asleep (arithmetic).
- **It wakes on a key, and the key types.** 247 of 247 soak cycles, the hour,
  the 10-18 runs: every one woke on the rig's key (a P2 column) and the
  waking key registered; a P0 column by hand reached the Mac (10-18). No stray
  wakes; the PLL back in ~127 loops.
- **The first key after a sleep reaches the Mac ~28 ms later** than an awake
  key (10-18). Unexplained: the radio retries as often awake as after a
  sleep (the awake control, 10-19), so the module dozing is not supported.
- **The watchdog does not count asleep** (10-18).
- **Asleep, the RTC's second runs ~13% slow:** 3139 counted for 3600 s
  (0.872); the 10-18 cycles fit 0.842. The PCF8563's crystal keeps time
  through it.
- **USB:** the transceiver is off after a deep sleep; setting its bit back
  was enough on every bench wake. But in every one the host's data was cut
  through the sleep and restored after the wake, so the host enumerated a
  board already awake. **A host that appears while the board sleeps is
  untested.**
- **The radio:** the link stayed up through every bench sleep (the Mac
  close by). What the module sends while the board sleeps is lost, and there
  is no way to ask it its state (`docs/wireless.md`). The battery reports
  (`5C`) only answer the board's own poll (`A6 53`, every 5 s), so none are
  sent while it sleeps.

## The design (revision 1)

### 1. When it sleeps (power.c, sleep.c)

The ladder gets a sleep time per mode, `SLEEP-PLAN.md`'s first draft, to tune
in use:

| mode | dim | lights off (screen dim) | screen off | deep sleep |
|---|---|---|---|---|
| Full | never | never | never | never |
| Normal | 1 min | 5 min | **7 min** (was 15) | **7 min** |
| Saver | 30 s | 2 min | **3 min** (was 5) | **3 min** |
| Minimal | at once | at once | at once | **30 s** |

Low battery takes Saver's times where they are shorter, as it does for the
stages now. **On battery only:** with the cable in every mode is Full, and
nothing sleeps.

`power.c` says when a sleep is wanted (`power_sleep_wanted()`: on battery,
the idle time past the mode's sleep time); `sleep.c` sleeps at the first
main-loop pass where every precondition holds, and keeps checking each pass
while it is wanted (the experiment's 60 s give-up goes; the request stands
until input). The preconditions, the experiment's (`sleep.c:226-246`) and
new ones:

- **Kept:** no key down; no USB host (the frame number still for 100 ms);
  the radio's side quiet (its queue, the serial output queue, UART2's
  transmitter empty), rechecked under the lock; the display bus and both
  SPIs idle; no flash program or erase and no deferred eeconfig write; the
  RTC idle (no slew or reload); every column on P0 or P2; the watchdog
  running.
- **New:**
  - **The backlight off** (`display_backlight_effective() == 0`): a hard
    rule, so a message's 4 s in Minimal, or `Fn`+`D`'s pages there, hold it
    awake by rule, not by timing. The lit screen needs the 20 kHz tick, which
    a sleep stops.
  - The RGB dark (`power_rgb_dark()`).
  - Not pairing (`ch582_is_pairing()`): pairing is interactive, and its
    retries run on the timer.
  - The PCF idle: no write pending (`pcf_pending`), its write machine idle,
    and no acquisition running.
  - The knob at rest: its two pins read the same before the wake edges are
    armed and after, as the columns' check does (a turn caught between the
    two is a wake with no sleep).
- **Not blockers:**
  - The link's state. LINKING or failed, the module keeps trying on its own.
  - An overlay or alert in its hold. It resumes after the wake, its timer
    having stood still. A message that arises while asleep waits for the
    wake (`SLEEP-PLAN.md`, "Decided").
  - A lock LED lit. It goes dark asleep, because the 20 kHz tick drives it,
    and it comes back at the wake.

### 2. The sleep itself (sleep.c)

The experiment's locked sequence stays as codex cleared it (`sleep.c:258-421`):
PRIMASK throughout, I-class calls and register writes only, each wake source
cleared in the peripheral and the NVIC, the clock contract (the IHRC, then
the PLL off, the flash's slow mode; back in the reverse order with a bounded
relock), the UART drain and the parser's resync. What changes:

- **In every build.** Today `sleep.c` is dev-only (stubs otherwise) and the
  LED driver's sleep hooks need `SN32F2XX_SLEEP_HOOKS`, defined only in dev
  builds (`config.h:20-22`). The guard splits:
  - **All builds:** deep sleep, and `sn32f2xx_sleep_enterI()`/`_exitI()`
    under a new `SN32F2XX_DEEP_SLEEP`.
  - **Dev only:** light sleep's bits (scan-only, the yield, WFI, PMU Sleep),
    the idle hooks in `chconf.h`, and op 14.
- **Wake sources:**
  - **A key:** the 15 columns, falling edges, as now.
  - **The knob:** both encoder pins, both edges. A is `B2` (P1.2, IRQ30) and
    B is `A10` (P0.10, IRQ31) (`docs/sn32f299.md`). Music's volume then works
    without a key first. The first detent of a turn may be lost, and a
    backward step is possible in principle; both are checked on the board.
    This reverses 10-18's "the knob need not wake it": a board asleep after
    7 minutes of listening would otherwise ignore the knob until a key.
  - **The charger:** CHRG (`B16`, P1.16, IRQ30, an input with a pull-up,
    `ak820pro.c:68`), falling: charging began. A cable plugged in wakes the
    board within microseconds, well inside USB's 100 ms attach debounce, so
    the host finds an awake device. A full pack may not start charging, so
    CHRG may not fall; the fallbacks are section 4's re-attach and the daily
    wake.
  - **The NVIC:** P0, P1, P2 and the RTC. A pin already in its wake state at
    the check is a wake with no sleep, the columns' rule extended to the new
    pins.
- **The RTC's second** still wakes the core each second (microseconds at
  12 MHz) to be counted. Feeding the watchdog stays: it is harmless, and the
  watchdog does not count asleep anyway.
  - **A 24 h backstop:** counted seconds, so ~27 h real. It is a wake with no
    input, and the board goes back to sleep once its housekeeping is done:
    the battery poll answered, the log caught up.
  - It bounds an undetected wake failure, or a full pack plugged in, to a
    day. On USB the board then stays awake, as every mode is Full there.
- **The record** (op 14 sub 4, dev) gains the wake source (key, knob,
  charge, backstop, strays), the PCF's slept seconds and the acquisition's
  step. The same figures go to `HC_POWER` page 1 in every build (section 7).
  - The arm stays, for the bench's forced sleeps.
  - **A new sub sets a short sleep time** (not persisted), so the soak runs
    through the ladder's own path rather than the arm.

### 3. Time across a sleep

QMK's timer (CT16B5, ChibiOS's system time) stands still asleep. **It stays
that way: a sleep is an instant to everything driven by the timer,** as it
was through the experiment's clean cycles. Two reasons not to advance it:

- The slept time comes from the PCF after the wake, so the timer could not be
  advanced before anything runs.
- A jump of hours would land under a key that is down and debouncing: the
  waking key, or a mod-tap read as a long hold. The stall accounting
  (`loop_acct.c`, on the system time) would also see a pass of hours.

What needs the true slept time gets it explicitly:

- **The slept time comes from the PCF.** One `pcf_read()` just before the
  lock, when the display bus is idle by the preconditions. One at the first
  pass after the wake: the same read the PCF discipline makes every minute
  on battery (`rtc.c:917-1045`), a few ms, with no blit in flight.
  - Slept = after − before, in whole seconds (±1 s).
  - If either read fails, the RTC's count stands in, ~13% short, and the
    record says so.
- **The calendar:** at that wake read, the board's calendar is set to the
  PCF's whole seconds, as the discipline's snap does. This happens before
  the backlight comes up (power_task lifts the cap within one 10 Hz tick of
  the key), so a wake never shows the slow time.
  - Then **the boot acquisition is re-armed** for the phase: the PCF edge
    hunt, ≤ 1.5 s, ±~20 ms (`rtc.c:1442-1500`; +13 ms measured at boots,
    `docs/clock.md`).
  - It aborts on a host sync, as now, but a sleep clears `host_synced`
    first. A sync from before the sleep no longer holds, and no host can
    sync during one, since the board sleeps only without a host.
- **The software seconds:**
  - Under the lock, `rtc_seconds_count` and the LLD's calendar get the
    counted seconds, as now (`rtc_account_sleepI`), so no served second lands
    between.
  - After the PCF read, `rtc_seconds_count` gets the difference, so it stays
    seconds since boot.
- **The trim window restarts at every sleep** (`rtc_cal_valid = false`).
  Otherwise it would read the slow asleep seconds as the awake ILRC's and
  mis-trim the period. The SOF window is already invalidated
  (`rtc_account_sleepI`).
- **What else spans a sleep, and what each does:**
  - **The gauge's energy and the log's 15-minute cadence:** section 5.
  - **`5C`'s freshness** (20 s by the timer, `C5_STALE_MS`): the ring is
    forgotten at the wake (`c5_forget()`) and a battery poll sent at once,
    so reports from before the sleep are never taken as fresh. A new
    estimate takes ~10 s (5 reports).
  - **The lock-LED receiver** (dev): reset at the wake. A frame in progress
    was lost with the radio's bytes anyway.
  - **The ladder's idle:** the waking key or knob resets it. A charge or
    backstop wake leaves it past the sleep time, so on battery the board
    sleeps again as soon as it can.
  - **Overlays and alerts:** each resumes its hold after the wake.
  - **The radio's timers** (the battery poll, the pair retry, the 5A
    promotion window): pairing blocks the sleep, and the rest are periodic
    or gated on a state.

  The review is asked what else.

### 4. The radio and USB after a wake

- **The radio:** the experiment's UART drain and parser resync, then the
  battery poll at once. The link's state cannot be asked
  (`docs/wireless.md`), so a drop or a reconnect while asleep stays unknown
  until the module says otherwise. A missed `5B 32` is recovered by the 5A
  promotion, as today. Accepted.
- **USB, when a host appeared while the board slept** (a cable to a
  computer; the charge wake covers most cases):
  - The transceiver bit is set back, as now.
  - Then, **if no host's frames arrive within 3 s of the wake, one
    re-attach**, in steps that never block: the D+ pull-up off
    (`usbDisconnectBus`), then at least 100 ms later by the timer, on again
    (`usbConnectBus`). A host that gave up enumerating a sleeping device then
    sees a fresh attach.
  - Never `restart_usb_driver()`, whose 50 ms wait loses a tap.
  - With no host, this toggles a pull-up nobody sees.
  - **Open:** whether the pull-up alone is enough, or the driver also needs
    `usbStop`/`usbStart`, and whether those block on SN32.
  - **A bench-only case:** on its pack with data and the host suspended (no
    SOFs), the board sleeps, and the re-attach then disconnects a suspended
    host's device. Real use never has data without VBUS, because on USB
    nothing sleeps. Accepted for the bench, and noted.

### 5. The gauge: the sleep term, hours of use, the log v7

- **The sleep term.** At the wake, `P_SLEEP_UW` × the slept ms goes in:
  - `P_SLEEP_UW` is 13,350, constant power assumed, as awake.
  - It goes into `e_used`, the level's count and the log (below), and never
    into the minute's energy.
  - The band applies as usual. With `5C` forgotten there is no hold until a
    fresh estimate.
  - Time left is recomputed then, so the wake's message (section 6) uses the
    level after the sleep.
- **Time left is hours of use, with no new arithmetic.** The average is
  already built from awake minutes only: `energy_tick()`'s minute mean
  (`battery.c:729-743`) never sees slept time, because the timer stands
  still. So `battery_time_left_min()` already means minutes of awake use at
  the recent awake draw, to the cut.
  - The 12 h constant is now 12 awake hours, ~1.5 days at 8 h of use a day.
  - The maturity rule, the freeze on USB and the strings stay as Phase 2
    built them.
- **The log, v7.** `vdd_avg` gives way to **the period's slept seconds**
  (u16, 0-900). On battery `vdd_avg` reads the buck-boost's 3.90 V, and the
  supply is already in `flags` and `flags_any`.
  - **A sleep spanning periods** is laid onto the periods it covered, at the
    wake:
    - the open period gets its share;
    - each whole period becomes one entry: slept 900, its energy at
      `P_SLEEP_UW`, no `5C`;
    - the remainder opens the next period.
  - The entries close one per 10 Hz call (a 16 h night is 64 entries in
    6.4 s). The cadence holds, so the host's dating (newest minus k × 15 min)
    stays right.
  - **A sleep longer than the log** (180 h, 7.5 days) overwrites it with
    slept entries. Accepted.
  - **Consumers:**
    - `ak820battery.py` decodes v7, with slept seconds in place of
      `vdd_avg`, and keeps v1-v6.
    - `scripts/battery_sim`'s replay reader takes the supply from the flags
      when a CSV has no `vdd_avg`.
    - `battery_fit.py` and `battery_charge.py` stay historical: they read
      v3-v5, noted in their docstrings.
  - With the slept seconds in each entry, the awake-only average can be
    rebuilt from any dump and graded against the draw that followed
    (`SLEEP-PLAN.md`, "Grading it in days").

### 6. The display

- **The battery row** shows the hours beside the level once time left is
  under 10 h and the average is mature (60 awake minutes or more): `18% 6h`,
  `9% <1h`.
  - That is at most 6 of the slot's 7 cells (`graphics/display.c:1676-1708`).
  - Each glyph is one synchronous blit, ~10 ms at most, painted one component
    per call as now.
  - Hidden on USB, after the cut, and with the level unknown.
- **A wake after 2 h or more asleep**, on battery and under 10 h, posts
  `6h use left` (or `<1h use left`) in the band for the overlay's hold. In
  Minimal its paint lights the screen for 4 s, like any message. Rounded
  down.
- **The camera page** stays as it is (`58% 31h`), now reading as hours of
  use.

### 7. `HC_POWER` page 1

The reply uses 27 of its 29 bytes (`hid_protocol.c:765-787`).

- **A page byte:** the request's `data[3]`. 0 is today's reply, so older
  hosts are unaffected.
- **Page 1 is the sleep**, in every build:
  - sleeps since boot, and slept seconds since boot (from the PCF);
  - the last sleep's wake source, its PCF and counted seconds, the
    acquisition's step (ms), its PLL loops and strays;
  - whether the host's frames came back within 20 s, and whether the
    re-attach ran.
- `ak820battery.py` prints it, and the trial logs it.

### 8. Riding along

- **The per-colour RGB model** (`history/power-bench-2026-10-15/rgb-curve-20261019/`):
  - the driver's committed-frame accounting keeps three channel sums instead
    of one (`drivers/led/sn32f2xx.c`, a core file already carrying our
    commits);
  - `model_nj()` weights them: B red 3,636, green 2,533, blue 2,294 nW per
    unit;
  - **A is refitted against the model's own RGB-off draw.** The fit's
    7.71 mW is against the measured RGB-off, 0.9 mW above the model's, so
    expect ~8.6.
  - On the map, every step is within 0.6% and purple within 0.2%.
- **The sleep.c comment wording** (codex, sleep verify 3): "no host" means no
  recent SOFs, and the record's byte 22 means frames seen again, not
  configured USB.
- **Optional:** `raw_edges` counted inside the lock (`BACKLOG.md`).

### 9. Not in this build

- **Light sleep:** optional (`SLEEP-PLAN.md`, "Decided").
- **A battery check while asleep:** the radio is the only voltmeter, and
  nothing could cut the 13 mW anyway.
  - A board asleep at the cut has about two days before the pack's own
    protection (an estimate: run 2's 5 h of reserve at the dark draw, over
    13 mW).
- **The first key's ~28 ms:** measured again by the stage test, not fixed
  here.
- **The wireless clock:** lock-LED item 5.
- **Sleep figures on `Fn`+`D`.**

## Validation

**On the host, before the flash** (`scripts/battery_sim`, building
`battery.c` and `power.c` on the Mac):

- **The sleep term:**
  - a night's energy at `P_SLEEP_UW` goes into `e_used` and the level, and
    never into the average;
  - time left after the night moves only by the level;
  - the count's 1% floor and the cut's 0 still hold;
  - a sleep during a band hold.
- **The log v7:**
  - a sleep within one period, across a boundary, across many, and longer
    than the log;
  - each entry's slept seconds and energy add up to the sleep's;
  - the dating holds;
  - the round trip through `ak820battery.py`.
- **power.c:** each mode's sleep time, low battery's, Full never, and never
  on USB.
- **The strings:** the row's `18% 6h` and `<1h`, and the wake message's rule
  (2 h or more asleep, under 10 h, rounded down).

**On the board, after the flash** (the dev build):

1. **The soak again** (`deep_cycles.py`, 100+ cycles), through the ladder's
   own path at a short sleep time:
   - every cycle sleeps, wakes on the rig's key, and the key registers;
   - the PCF's slept seconds within 1 s of the logger's `asleep_s`;
   - the daemon's first sync after each wake finds the clock within ~50 ms
     (today −1.5 to +2.1 s);
   - no pass ≥ 25 ms, and no radio give-ups.
2. **An hour asleep again:**
   - slept seconds 3600 ± 1;
   - `e_used` up by 13.3 mWh ± 5%;
   - four v7 entries of ~900 slept seconds;
   - the clock within ~50 ms at the daemon's sync.
3. **The wake sources:**
   - **The knob** (JD turns it one detent while the board sleeps, one
     hands-on step): the volume moves, at most one detent lost.
   - **The charge** (relay 2 on while asleep): the board wakes at once (the
     logger), and with data on, USB enumerates.
   - **A host appearing while asleep** (data on through `uhubctl`, relay 2
     off), then a key: USB back within ~5 s, through the re-attach.
   - **The backstop,** with a short cap set through the dev hook.
4. **The preconditions:** a message in Minimal holds it awake for its 4 s;
   pairing holds it; USB data holds it.
5. **The stage test** (the Mac unlocked, the keyboard untouched ~10 min): no
   lost keys at any transition, sleep included, and the first key's latency
   after a sleep.
6. **The daily build:** the symbol diff shows deep sleep in and no dev hooks;
   one sleep and wake on it.
7. **R5 with sleep:** a drain in Normal at JD's use, read from the board's v7
   log and the logger:
   - the gauge within ±3 points;
   - the sleep energy per entry against the logger, at the top of the pack,
     mid-drain and low (constant power is assumed);
   - time left graded as `SLEEP-PLAN.md` describes.

## Questions put to the review

1. A sleep as an instant to QMK's timer: what breaks, beyond section 3's
   list?
2. The slept time from two PCF reads (±1 s), and the acquisition for the
   phase: sound? Can anything in the PCF discipline or the persisted period
   still be corrupted by a sleep?
3. The new wake pins (P1.2 and P0.10 on both edges, P1.16 falling) and IRQ30
   in the NVIC: any conflict with the PAL (callbacks are off), the encoder
   driver's state, or CHRG's no-pack pulse?
4. The USB re-attach: is it enough, safe, and non-blocking?
5. Deep sleep in daily builds: what in the experiment must not ship, or must?
6. Log v7 taking `vdd_avg`, and the per-period accounting of a long sleep.
7. The 24 h backstop: worth having?
8. Anything that could cost a keystroke, stall the main loop, or strand the
   board.
