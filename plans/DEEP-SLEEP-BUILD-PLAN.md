# Deep sleep, the build: the ladder's last stage

Status: **revision 2, after codex's plan review (10-19; every finding
dispositioned at the end). For codex's verify, then the build.**

The experiment (`SLEEP-PLAN.md`, "Deep sleep: the experiment") proved the
mechanism over 10-18/19. This plan makes it the board's standby: on by
default on battery, in every build. It carries the decisions in
`SLEEP-PLAN.md` ("Decided", "Asleep, the RTC's second runs ~13% slow", "Time
left with deep sleep"). Every flash gets a codex review first.

## What the measurements say (10-18, 10-19)

- **Asleep: 13.35 mW at the pack**, against ~114 mW awake and dark
  (27.5 mA). The asleep figure is 3.24 mA at 4.12 V, from the logger's 1 Hz
  samples over 05:46-06:43 10-19, inside the hour asleep. At that rate the
  pack's 15,579 mWh would last ~1,170 h (~7 weeks), by arithmetic.
- **It wakes on a key, and the key registers at the board.**
  - 247 of 247 soak cycles, board-only, all on one P2 column (the rig's F19),
    with sleeps of 7-30 s.
  - The 1 h sleep and the 10-18 runs.
  - No stray wakes and no radio give-ups. The PLL relocked in ~127 loops.
  - Delivery at the host was checked only on 10-18: a P0 key pressed by
    hand reached the Mac.
- **The first key after a sleep reaches the Mac ~28 ms later** than an awake
  key (10-18). It is unexplained: the radio retries as often awake as after
  a sleep (the awake control, 10-19), so that rules out its retries.
- **The watchdog does not count asleep** (10-18).
- **Asleep, the RTC's second runs ~13% slow.** The hour counted 3139 s for
  3600 (0.872), and the 10-18 cycles fit 0.842. The PCF8563's crystal keeps
  time through it.
- **USB:** the transceiver is off after a deep sleep, and setting its bit
  back was enough on every bench wake. But each time the host's data was
  cut through the sleep and restored after the wake, so the host always
  enumerated a board that was already awake. **A host that appears while
  the board sleeps is untested.**
- **The radio:** the link stayed up through every bench sleep, with the Mac
  close by.
  - What the module sends while the board sleeps is lost, and there is no
    way to ask it its state (`docs/wireless.md`).
  - The battery reports (`5C`) answer only the board's own poll (`A6 53`,
    every 5 s), so none are sent while it sleeps.

## The design (revision 2)

### 1. When it sleeps (power.c, sleep.c)

The ladder gets a sleep time per mode, `SLEEP-PLAN.md`'s first draft, to tune
in use:

| mode | dim | lights off (screen dim) | screen off | deep sleep |
|---|---|---|---|---|
| Full | never | never | never | **never** |
| Normal | 1 min | 5 min | **7 min** (was 15) | **7 min** |
| Saver | 30 s | 2 min | **3 min** (was 5) | **3 min** |
| Minimal | at once | at once | at once | **30 s** |

- **Low battery** shortens Normal's sleep to Saver's 3 minutes. Minimal's is
  already shorter.
- **Full never sleeps, even at low battery.** In Full the low cap stays a
  lighting cap: `power_task()` gives Full Saver's stages when low, and those
  stages keep their effect on the lights, but sleep is not one of them.
- **On battery only.** With the cable in, every mode is Full and nothing
  sleeps.

**Who decides.** `power.c` says when a sleep is wanted
(`power_sleep_wanted()`: on battery, not Full, the idle time past the sleep
time). `sleep.c` sleeps at the first main-loop pass where every
precondition holds, and it keeps checking while the sleep is wanted. This
production path shares nothing with the experiment's arm: no 60 s give-up,
and no terminal `DS_DONE` state.

**Preconditions.** The experiment's (`sleep.c:226-246`) and new ones:

- **Kept from the experiment:**
  - no key down;
  - no USB host (the frame number still for 100 ms);
  - the radio's side quiet (its queue, the serial output queue, UART2's
    transmitter empty), rechecked under the lock;
  - the display bus and both SPIs idle;
  - no flash program or erase, and no deferred eeconfig write;
  - every column on P0 or P2;
  - the watchdog running.
- **The RTC fully idle:**
  - no slew or reload;
  - no PCF write pending (`pcf_pending`) and its write machine idle
    (`ps_state == PS_IDLE`), which also covers its verification and STOP
    recovery;
  - no acquisition running.
- **The backlight off** (`display_backlight_effective() == 0`). This is a
  hard rule: the lit screen needs the 20 kHz tick, which a sleep stops. So a
  message's 4 s in Minimal, or `Fn`+`D`'s pages there, keep the board awake
  by rule.
- **The RGB dark** (`power_rgb_dark()`).
- **Not pairing** (`ch582_is_pairing()`).
- **The last wake's recovery finished** (section 4), including any USB
  re-attach.
- **The knob at rest.** The encoder's two-bit state must equal the
  decoder's stored state. After the wake edges are armed and cleared, a
  second read must match and no edge be latched (section 2).

**Not blockers:**

- **The link's state** (LINKING or failed): the module keeps trying on its
  own.
- **An overlay or alert in its hold:** it resumes after the wake.
- **A lock LED lit:** it goes dark asleep and comes back at the wake.

### 2. The sleep itself (sleep.c)

The experiment's locked sequence stays as codex cleared it
(`sleep.c:258-421`):

- PRIMASK held through the clock restore;
- I-class calls and register writes only;
- each wake source cleared in the peripheral and in the NVIC, with the
  temporary vectors disabled before the old enables are restored;
- the clock contract: the IHRC, then the PLL off, then the flash's slow
  mode, and back in reverse with a bounded relock.

What changes:

- **The system time is frozen for real.**
  - ChibiOS's counter, CT16B5 (`SN32_ST_TIM`, 16-bit at 187.5 kHz), runs on
    HCLK. So today it keeps counting through the core's microseconds awake
    each second, at a quarter rate on the IHRC, and through the entry and the
    exit.
  - Over a week that is more than one wrap (350 ms) unobserved, which breaks
    QMK's wrap extension (`platforms/chibios/timer.c`).
  - The fix: CT16B5 is stopped (`TMRCTRL.CEN` off) under the lock before the
    clock switch, and started again after the restore, with its prescale and
    match registers untouched. The SysTick alarm is handled as now (off, then
    pended at the end).
- **In every build: the production path.**
  - Automatic admission and its repeated checks.
  - The locked sequence, its bounded clock restore and the watchdog feed.
  - The LED driver's `sn32f2xx_sleep_enterI()`/`_exitI()`, under a new
    `SN32F2XX_DEEP_SLEEP`. Its definitions and declarations are split
    consistently, keeping the I-class calls, the PWM pending clear and the
    synchronized counter re-arm (`sn32f2xx.c:797-830`). The 20 kHz tick's
    hooks are already outside the dev guard.
  - The UART drain **and the parser's resync**: the 3-byte window reset is
    under `AK820_DEV_HOOKS` today (`ch582f_ajazz.c:1047`), and its request
    and its consumer both move into every build.
  - The wake's recovery (section 4), and `HC_POWER` page 1 (section 8).
- **Dev-only:** light sleep's bits (scan-only, the yield, WFI, PMU Sleep),
  the idle hooks in `chconf.h`, and the arm and cancel. Also:
  - a short sleep time for the bench, not persisted, and never under 30 s,
    so physical holds and short release deadlines still finish first;
  - the backstop's preset (section 2);
  - `ds_no_feed`, the forced stages, and every op 14 mutation.
- **Wake sources:**
  - **A key:** the 15 columns, falling edges, as now.
  - **The knob:** both encoder pins, both edges (encoder bits only). A is
    `B2` (P1.2, IRQ30) and B is `A10` (P0.10, IRQ31) (`docs/sn32f299.md`).
    This reverses 10-18's "the knob need not wake it": after 7 minutes of
    listening the board would otherwise ignore the knob until a key.
  - **The charger:** CHRG (`B16`, P1.16, sharing IRQ30, an input with a
    pull-up, `ak820pro.c:68`), falling, meaning charging began. A cable
    plugged in wakes the board within microseconds, well inside USB's 100 ms
    attach debounce, so the host finds an awake device.
    - A full pack may not start charging, so CHRG may not fall. That
      insertion waits for a key, the knob or the backstop: the USB
      re-attach (section 5) cannot wake a sleeping board.
- **What sleeps with the board:**
  - **The NVIC:** P0, P1, P2 and the RTC. P1 joins both the peripheral's
    cleanup and the NVIC's disable and pending clear before the old enables
    are restored.
  - **The knob check:** pins read; edges armed (both edges); cleared after
    the pins settle; read again and the latched edges checked. A changed
    state, or an edge, is a wake with no sleep. The columns are checked as
    now, and CHRG already low is a wake too.
- **The RTC's second** still wakes the core each second, to be counted and
  to feed the watchdog. Feeding it is harmless: the watchdog does not count
  asleep.
  - **A 24 h backstop:** counted seconds, so ~27.5 h real at the hour's
    rate. It is a wake with no input. The recovery runs, and then the board
    sleeps again.
  - It bounds a missed wake (a full pack plugged in) to about a day, and
    finds a supply change. It is no failsafe against an RTC that stops,
    since nothing else wakes the board then and the watchdog does not
    count.
  - **Every second count is 32-bit:** the cap, the loop's local count, the
    record, and the status reply. Today they are 16-bit
    (`sleep.c:140,157,184,310`): 86,400 assigned to the cap would become
    20,864.
- **The dev record** (op 14 sub 4) becomes version 2 at the new widths:
  - the sleep's sequence number;
  - the wake source (key, knob, charge, backstop, strays);
  - the PCF's slept seconds, the counted seconds, and the recovery's
    results.

### 3. Time across a sleep: the timer stands still

**QMK's timer and ChibiOS's system time stand still across a sleep**, now
exactly (section 2): a sleep is an instant to everything driven by the timer.
Two reasons not to advance it instead:

- The true slept time comes from the PCF after the wake, so the timer could
  not be advanced before anything runs.
- A jump of hours would land under a key that is down and debouncing, and
  the stall accounting would see a pass of hours.

**Codex's audit** of every timer consumer (its review, section 1) finds
freezing correct for nearly all of them, at production thresholds:

- debounce, tap and hold, Space Cadet, the radio's ACKs and retries, holds,
  overlays, the LED drive accounting, RGB animation, deferred execution,
  persistence, the health and loop statistics;
- uptime and the loop statistics now mean awake time; `ak820health.py` says
  so.

The exceptions, each handled in the wake's recovery:

| What | Why it cannot just stand still | Handled by |
|---|---|---|
| the gauge's energy | the pack drains while asleep | the sleep term (section 6) |
| the battery log's cadence and dating | the host dates entries by elapsed time | the log's own time base (section 6) |
| the 30 min relaxation after an unplug (`battery.c:858-878`) | the pack relaxes physically while asleep | aged by the slept time, then a fresh `5C` |
| `5C`'s freshness, and the radio's cached report age (`ch582f_ajazz.c:987,1348`) | reports from before the sleep would pass for fresh | the ring forgotten, the age marked stale |
| the ladder's idle | a lost first detent decodes nothing, so QMK sees no activity | the key or knob wake triggers it itself |
| host text (3 min) and the playback heartbeat (20 s) (`display.c:419,441`) | a text could reappear hours later | both expired at the wake |
| the lock-LED receiver (dev) | a frame in progress cannot finish | reset; its diagnostics kept |

### 4. The wake's recovery: one owner, in steps

`sleep.c` owns the wake and holds the board awake until every step has
ended. **One step per main-loop pass at most**, each bounded, so no pass
carries two of them. Each step's state and result are kept under the
sleep's sequence number (section 8).

0. **In the locked exit, as now:**
   - the counted seconds go to both software clocks (`rtc_account_sleepI`);
   - the SOF reference is broken completely: its epoch bumped, the streaks
     and qualification reset, outside the `seconds == 0` early return, since
     a sub-second sleep breaks continuity too;
   - the trim window is invalidated, and **the 60 s PCF discipline is
     suspended** until step 3 ends;
   - the host-sync generation is noted.
   - `battery_task()` pauses until step 4, so nothing from after the wake
     lands in the periods before it.
1. **The first pass** runs the matrix and the encoder as usual. The waking
   key is scanned, debounced and reported before any of the recovery's
   I/O. A key or knob wake triggers QMK's activity itself
   (`last_matrix_activity_trigger()` / `last_encoder_activity_trigger()`).
2. **The coarse clock:** one `pcf_read()`.
   - Slept seconds = after − before, signed and checked against the counted
     seconds.
   - `rtc_seconds_count` takes the difference atomically. The calendar is
     set to the PCF's whole seconds, which supersedes the coarse count rather
     than adding to it.
   - **A host sync since the wake wins:** if the generation moved, the
     calendar is left alone. In practice none can arrive, because USB must
     re-enumerate first, but the rule makes it certain. The old sync's
     `host_synced` is cleared once, here, and a new one is never cleared.
   - If a read fails, the counted seconds stand, flagged.
3. **The phase:** the boot acquisition is re-armed.
   - Every field is reset: the failure count, the previous edge, the timeout
     origin, the uncertainty, the result.
   - Done or aborted, it ends the step; an aborted one never holds the board
     awake. With `rtc_valid` false (no PCF ever read), it is skipped.
   - It aborts on a new host sync, as now. The 60 s discipline resumes
     afterward, with a fresh trim window.
   - The whole-second set does not fix the phase; this step does, to
     ~±20 ms at boot. Under wake load that is measured, not assumed.
4. **The gauge and the log** (section 6):
   - the sleep energy, then time left recomputed;
   - the relaxation aged;
   - the log's entries for the sleep written;
   - `5C`'s ring forgotten and the radio's battery age marked stale;
   - then `battery_task()` resumes.
5. **The radio:** the battery poll (`A6 53`), sent after the waking input's
   first report has left (the TX queue empty, or 100 ms). The step waits for
   the reply, or 2 s.
6. **Host content:** host text and the playback heartbeat expired.
7. **The supply:** 1.5 s of fresh samples since the wake (`supply_tick`
   needs 0.5-1 s), so a charge wake is classified. On EXTERNAL the board
   stays awake, since the ladder is off on USB.
8. **USB:** the re-attach (section 5), when no SOF has arrived since the
   wake, through to D+ restored.
9. **The display:**
   - the backlight is held dark from the wake until the clock face has been
     repainted after step 2, so the screen never shows the slow time. The
     clock's paint sets a flag, the queue is pumped as usual, and nothing
     drains it synchronously;
   - then the wake's message (section 7).

**The budget.** The steps with I/O:

- step 2: one PCF read, a few ms, or the 20 ms I2C timeout on failure;
- step 3: the acquisition's edge pass, several transactions, as at boot;
- step 4: up to 720 log entries written, a few ms.

Each runs in a pass of its own. The validation counts passes of 25 ms or
more through every step, failures included: a failed PCF read before the
sleep, a failed one after, an aborted acquisition.

### 5. The radio and USB after a wake

- **The radio:**
  - the UART drain and the parser's resync (now in every build);
  - the battery poll, after the waking input (step 5).
  - The link's state cannot be asked, so a drop or a reconnect while asleep
    stays unknown until the module says otherwise. A missed `5B 32` is
    recovered by the 5A promotion. Accepted.
- **USB, when a host appeared while the board slept.** The charge wake
  covers most cases. Otherwise:
  - **One re-attach**, if no SOF has arrived within 3 s of the wake. Its
    states are pending, done, or not needed, under the sleep's sequence
    number.
  - **D+ off, then on.** The pull-up goes off: `usbDisconnectBus`, which
    clears `CFG.DPPU`, read-modify-written under the lock against the USB
    ISR's own `CFG` writes. At least 100 ms later, by a state deadline and
    never a wait, it goes on again (`usbConnectBus`).
  - **The latch:** whether any SOF arrived after this wake, from the frame
    number moving since the wake, not "seen recently".
  - **Why it is enough:** on this LLD both calls only flip `DPPU`, with no
    delay (`hal_usb_lld.h:376-390`). The host's bus reset then runs
    `_usb_reset()` (`hal_usb_lld.c:234`).
  - Never `restart_usb_driver()`, whose 50 ms wait loses a tap.
  - The recovery holds the board awake until D+ is restored.
- **A suspended host** (on the pack with data, the host asleep, no SOFs) is
  indistinguishable from an absent one. The re-attach then turns a
  resumable device into a detached one, and the remote-wakeup relationship
  is lost. Real use never has data without VBUS, since on USB nothing
  sleeps, so this is a bench-only cost. Accepted.

### 6. The gauge and the log

- **The sleep term** (step 4):
  - `P_SLEEP_UW` 13,350 × the slept ms, 64-bit from the first multiply,
    with the remainder kept;
  - stale `band_hold` cleared first, since `c5_forget()` alone does not;
  - into `e_used`, the level's count (the 1% floor, the cut's 0, an unknown
    level and a pending re-seat all as now) and the log; never into the
    minute's energy;
  - then time left recomputed at once. Today it changes only on a minute
    boundary.
- **Time left is hours of use, with no new arithmetic.** The average is
  built from awake minutes only (`battery.c:729-743`), so
  `battery_time_left_min()` already means minutes of awake use at the
  recent awake draw, to the cut.
  - The 12 h constant is now 12 awake hours, ~1.5 days at 8 h of use a day.
  - The maturity rule, the freeze on USB and the strings stay as Phase 2
    built them.
- **The relaxation:** `relax_from` is moved back by the slept time
  (saturating at done). The re-seat then waits for the first fresh estimate
  after the wake, since the ring was forgotten.
- **The log's own time base.**
  - Log time = QMK's timer + the slept ms accounted so far (u32, modular,
    like the timer). The period's start, the newest entry's end and
    `battery_log_read()`'s age (`battery.c:1394`) all read it, so the host's
    dating (`ak820battery.py:245`) stays right.
  - The calendar is never used for this, since a host sync would move the
    boundaries.
- **The log, v7.** `vdd_avg` gives way to **the period's slept seconds**
  (u16, 0-900). On battery `vdd_avg` reads the buck-boost's 3.90 V, and the
  supply is already in `flags` and `flags_any`.
- **The sleep's entries, written at once in step 4,** from the state frozen
  at the sleep's entry (`battery_task()` was paused):
  - **The open period** closes with its awake part as it stood, plus its
    slept share and that share's energy. Its level, flags and charging state
    are as at entry.
  - **Each whole slept period** becomes one entry: 900 s slept, its energy
    at `P_SLEEP_UW`, no `5C` reports, a dark load (`led_pm` 0, `bkl` 0),
    the flags as at entry, and the level stepped down by the sleep's energy
    up to that boundary.
  - **The remainder** opens the new period, with its slept share and that
    share's energy. Live accumulation starts there.
  - **At most `LOG_LEN` entries are written**, since older ones would be
    overwritten anyway. `log_written` advances by the full count.
  - **Written synchronously**, so a dump never meets a catch-up that is
    still moving.
- **Consumers:**
  - **`ak820battery.py`:** decodes v7, with slept seconds in place of
    `vdd_avg`, and keeps v1-v6.
  - **The simulator's CSV reader:** stops indexing `vdd_avg`
    unconditionally (`sim.c:861`).
  - **The simulator's replay** of a v7 entry plays its awake part
    (900 − slept) at 10 Hz with its reports, then the slept part through
    the production sleep accounting. The order within a mixed entry, awake
    first, is an approximation the aggregate cannot avoid; it is declared
    and tested.
  - **`battery_fit.py`, `battery_charge.py`:** stay historical (v3-v5),
    noted in their docstrings.
- **Grading time left from dumps** (`SLEEP-PLAN.md`, "Grading it in days"),
  stated as an approximation:
  - each entry's awake draw is (energy − `P_SLEEP_UW` × slept) / awake,
    skipping wholly slept entries;
  - the 12 h average is rebuilt from those, to a stated tolerance, not
    exactly: the firmware's minute state and its starting point are not in
    the log.

### 7. The display

- **The battery row** shows the hours beside the level once time left is
  under 10 h and the average is mature (60 awake minutes or more): `18% 6h`,
  `99% <1h`.
  - That is at most 7 cells, the slot's 7, in the 8-byte buffer
    (`display.c:1667-1708`).
  - With `BATTERY_SHOW_TENTHS` (the calibration view) there are no hours.
  - The painting stays as it is: `BATT_TXT_CHUNK` 3, the clear separate,
    one component per call.
  - Hidden on USB, after the cut, with no pack, and with the level unknown.
- **A wake after 2 h or more asleep**, under the same conditions as the row,
  posts `6h use left` (`<1h use left` fills all 12 cells).
  - It goes through the owed-notice gate that "Saving power" uses: a free
    band and a lit screen. So it waits behind an alert rather than expiring
    unseen.
  - In Minimal its paint lights the screen for 4 s, like any message.
  - Rounded down.
- **The camera page** stays as it is (`58% 31h`), now reading as hours of
  use.
- **The clock after a wake:** the backlight is held until the corrected
  face is painted (step 9).

### 8. `HC_POWER` page 1

- **The request's `data[3]` selects the page.** It is read before the
  existing `memset` (`hid_protocol.c:765-787`). Page 0 stays byte for byte,
  so older hosts are unaffected.
- **Page 1 identifies itself.** Its reply's first byte is 0x81, and an older
  firmware's page-0 reply starts with 1, so it cannot be misread as sleep
  data.
- **What it carries**, about 26 of 29 bytes, snapshotted together, in every
  build:
  - sleeps since boot (u32), and slept seconds since boot (u32, from the
    PCF);
  - the last sleep's sequence number, wake source, PCF seconds and counted
    seconds (u32 each), the acquisition's step (i16, ms), PLL loops and
    strays (u16);
  - validity bits for the PCF figure, the acquisition and the USB
    observation, since otherwise 0 is ambiguous;
  - the recovery's state.
- The payload's size is asserted. `ak820battery.py` prints the page, and the
  trial logs it.

### 9. Riding along

- **The per-colour RGB model**
  (`history/power-bench-2026-10-15/rgb-curve-20261019/`):
  - the driver's committed-frame accounting keeps three channel sums instead
    of one (`drivers/led/sn32f2xx.c`);
  - `model_nj()` weights them: B red 3,636, green 2,533, blue 2,294 nW per
    unit;
  - A is refitted against the model's own RGB-off draw. The fit's 7.71 mW is
    against the measured RGB-off, 0.9 mW above the model's, so expect ~8.6.
  - On the map, every step is within 0.6% and purple within 0.2%. The log's
    single `led_pm` cannot recover the colour split, but the log's energy
    field carries the model's total, which is what grading needs.
- **The sleep.c comment wording** (codex, sleep verify 3).
- **Optional:** `raw_edges` counted inside the lock (`BACKLOG.md`).

### 10. Not in this build

- **Light sleep:** optional.
- **A battery check while asleep:** the radio is the only voltmeter, and
  nothing could cut the 13 mW anyway.
  - A board asleep at the cut has about two days before the pack's own
    protection. That is an estimate: run 2's 5 h of reserve at the dark
    draw, over 13 mW.
- **The first key's ~28 ms:** measured again, not fixed here.
- **The wireless clock:** lock-LED item 5.
- **Sleep figures on `Fn`+`D`.**

## Validation (revision 2)

**On the host, before the flash** (`scripts/battery_sim`, building
`battery.c` and `power.c` on the Mac):

- **The sleep term:**
  - a night into `e_used` and the level, and never into the average;
  - time left moves only by the level;
  - the floor, the cut's 0, an unknown level and a pending re-seat;
  - `band_hold` cleared;
  - the remainder carried across sleeps.
- **The relaxation:** aged by a sleep shorter than it and by one longer.
- **The log v7:**
  - sleeps within a period, across one boundary, across many, longer than
    the log;
  - entry and exit not aligned to a boundary, and repeated partial sleeps
    in one period;
  - each entry's slept seconds and energy add up to the sleep's, with the
    levels stepped right;
  - the dating through `battery_log_read()`'s age;
  - the PCF-failure fallback;
  - the round trip through `ak820battery.py`;
  - a dump between two sleeps.
- **The replay:** a v7 log with sleeps, through the sleep semantics; a CSV
  without `vdd_avg` is read; the mixed-entry approximation's error stated.
- **power.c:** each mode's sleep time, low battery's (Normal at 3 min, Full
  never), never on USB.
- **The strings:** the row up to 7 cells, the tenths view without hours, and
  the wake message's rule (2 h or more asleep, the row's conditions,
  rounded down).

**On the board, after the flash** (the dev build):

1. **The soak again** (`deep_cycles.py`, 100+ cycles), through the ladder's
   own path at the bench's short sleep time (30 s or more).
   - **Each cycle passes only if:**
     - **exactly one press** at the board (`key_gain == 1`), with a fresh
       sleep sequence number;
     - **no reset** (uptime continuity), no new pass of 25 ms or more,
       recovery included;
     - **no new transport errors:** the queue's full and replaced counts and
       UART errors unchanged;
     - **the slept time agrees:** the PCF's slept seconds against the
       logger's fall and rise edges, within ±2 s (1 s from each). Today
       `asleep_s` starts at the 3-sample confirmation; it moves to the
       edges.
   - **The clock:** the daemon's offset before its first SET after each
     wake, which comes after the acquisition (today −1.5 to +2.1 s; the
     target is ~50 ms).
2. **Real thresholds, the default mode:** Normal sleeps after 7 idle
   minutes, once, and the rig wakes it.
3. **An hour asleep:**
   - the slept seconds against the logger's edges;
   - the sleep's energy summed across whatever entries it spans, with the
     awake energy after the wake kept apart;
   - the clock's offset before the first SET.
4. **The clock without the daemon:**
   - the daemon paused, and the board's clock read with `ak820 clock` after
     a sleep;
   - then host syncs injected at 0.2, 1 and 3 s after a wake (data on
     through `uhubctl`): the host's time wins;
   - the awake period (`P_nom`) and the persistence candidate unchanged by
     the sleep (`HC_RTC`).
5. **The wake sources:**
   - the rig's key (P2) with 25 ms taps, and presses timed around the
     admission;
   - a P0 key and a chord, by hand (JD, one action);
   - **the knob,** by hand: both resting states, both directions, a
     reversal, a wiggle with no detent, a fast turn. **Pass: no step in the
     wrong direction, at most one detent lost;**
   - optionally, the slider while asleep (it resets the MCU).
6. **Charge and USB:**
   - relay 2 on while asleep: the board wakes at once (the logger), with
     data on and off;
   - a full-pack insertion with no CHRG edge: it waits for the backstop or
     a key;
   - a host appearing while asleep (`uhubctl` on, relay 2 off), then a key:
     raw HID answers within ~5 s, which checks HID traffic, not SOFs alone;
   - host frames timed around the re-attach's 3 s and 100 ms deadlines.
7. **The backstop:**
   - the 32-bit widths, with the dev preset of the counted seconds near
     65,535;
   - the real length once, over a weekend: two autonomous cycles with no
     input.
8. **The builds:**
   - daily, fpb, instrumented and dev all build;
   - `build.sh`'s structural checks extended: the production sleep path
     present in every flavour, no dev mutation handler in daily or fpb;
   - one daily build on the board before a release, with JD's `Fn`+`Esc`
     back to dev: it sleeps by default and rejects `HC_DEVHOOK`.
9. **The stage test** (the Mac unlocked, the keyboard untouched ~10 min):
   - no lost keys at any transition, sleep included;
   - the first key's latency, and short bursts after a sleep (the battery
     poll waits for them).
10. **R5 with sleep:** a drain in Normal at JD's use, from the board's v7 log
    and the logger.
    - the gauge within ±3 points;
    - the sleep energy against the logger at the top of the pack, mid-drain
      and low (constant power is assumed);
    - time left graded per section 6.

## Questions for the verify

1. Is section 4's order complete and safe? Is anything left that one pass
   could carry beyond 25 ms?
2. Stopping CT16B5 under the lock: any ChibiOS or QMK assumption it breaks
   (the VT list, `update_timer`, SysTick's pended alarm)?
3. Is the log's own time base, with entries written synchronously from a
   state frozen at entry, right against `log_task()` and
   `battery_log_read()`?

## Codex's plan review (10-19) and what changed

[Verbatim](review-codex-deep-sleep-plan-2026-10-19.md), prompt
[`review-prompt-deep-sleep-plan.txt`](review-prompt-deep-sleep-plan.txt).
"Revise before building": the approach sound, freezing QMK's timer right,
the locked sequence largely sound. Every finding was taken.

| # | finding (short) | disposition |
|---|---|---|
| 1 | **must fix:** the SysTick alarm off does not freeze CT16B5, which counts through each second's wake and the entry/exit; a week passes QMK's wrap | **Fixed:** CT16B5 stopped under the lock, restarted after the restore (section 2). |
| 2 | **must fix:** the 30 min relaxation after an unplug must include sleep | **Fixed:** aged by the slept time, then a fresh `5C` (section 6). |
| 3 | the log needs its own time base and live/backlog separation | **Fixed** (13, 14 below). |
| 4 | a lost first detent decodes nothing, so QMK's activity is not reset; charge and backstop wakes need their own hold | **Fixed:** the wake triggers the activity itself; the recovery holds the board awake (section 4). |
| 5 | the radio's cached battery age stays fresh | **Fixed:** marked stale at the wake. |
| 6 | should fix: host text (3 min) can reappear hours later; the playback heartbeat too | **Fixed:** both expired at the wake. |
| 7 | the production path must not depend on the dev arm's give-up and `DS_DONE`; USB recovery's timers must run to the end | **Fixed** (sections 1, 4). |
| 8 | **must fix:** one owner and an order for the RTC's recovery: `ps_state` idle at admission; the trim window invalid before any discipline call; the 60 s discipline suspended through recovery; a signed atomic correction, the calendar set superseding the coarse add | **Fixed** (sections 1, 4: steps 0-3). |
| 9 | **must fix:** a host sync can come before the recovery's pass | **Fixed:** a generation rule; the newer host time wins; the old `host_synced` cleared once (step 2). |
| 10 | should fix: reset the SOF continuity fully, also for a sub-second sleep | **Fixed** (step 0). |
| 11 | the acquisition reused: reset all its state, aborted ends the hold, a defined case for no valid RTC | **Fixed** (step 3). |
| 12 | note: the whole-second set gives no phase; the boot's accuracy is not guaranteed under wake load | **Agreed;** measured (validation 1, 3, 4). |
| 13 | **must fix:** the catch-up cannot reuse the live accumulator (awake energy and the waking level in the sleep's entries; current observations erased) | **Fixed:** `battery_task()` paused from the wake to step 4; the entries written from the state frozen at entry, each with its own level and a dark load (section 6). |
| 14 | **must fix:** the log needs its own elapsed time for dating | **Fixed:** log time = the timer + accounted sleep; never the calendar. |
| 15 | should fix: a dump during a catch-up that moves every 100 ms can exhaust the host's retries | **Fixed:** the entries are written synchronously. |
| 16 | **must fix:** the replay needs sleep semantics, and the CSV reader indexes `vdd_avg` unconditionally | **Fixed** (section 6, validation). |
| 17 | should fix: "rebuilt from any dump" is too strong | **Fixed:** an approximation with a stated tolerance; per entry, the awake draw. |
| 18 | **must fix:** the knob's A == B check misses 00 → 11 | **Fixed:** compared with the decoder's stored state, and latched edges checked after arming. |
| 19 | **must fix:** a recovery hold independent of decoded input (the RTC, the battery reply, the log, the supply, USB, the first input) | **Fixed** (section 4). |
| 20 | should fix: "no wrong-direction step" as the knob's pass | **Fixed** (validation 5). |
| 21 | **must fix (USB):** a state deadline, awake until D+ is back, `CFG` under the lock, a latch for SOFs since the wake, states per sleep | **Fixed** (section 5). |
| 22 | the re-attach cannot wake a sleeping board; a full pack with no CHRG edge waits | **Fixed** (wording, sections 2, 5). |
| 23 | note: the suspended host's cost | **Accepted,** bench-only (section 5). |
| 24 | **must fix:** the parser's resync is dev-only (`ch582f_ajazz.c:1047`) | **Fixed:** in every build (section 2). |
| 25 | the production and dev-only lists | **Taken** as codex gave them (section 2). |
| 26 | should fix: structural checks on every flavour; the production path present, `HC_DEVHOOK` rejected on daily/fpb | **Fixed** (validation 8). |
| 27 | the gauge: 64-bit from the first multiply, the remainder kept, `band_hold` cleared, time left at once | **Fixed** (section 6). |
| 28 | **must fix:** the backstop's counts are 16-bit (86,400 becomes 20,864) | **Fixed:** 32-bit throughout; the dev record versioned. |
| 29 | note: ~27.5 h; no failsafe against the RTC stopping; test two autonomous cycles | **Agreed** (section 2, validation 7). |
| 30 | the display: up to 7 cells, not 6; the tenths view; keep the chunking | **Fixed** (section 7). |
| 31 | should fix: the wake message under the row's conditions, queued rather than lost | **Fixed:** the owed-notice gate. |
| 32 | should fix: the calendar set before the backlight is not the panel painted | **Fixed:** the backlight held until the clock's repaint (step 9). |
| 33 | **must fix:** `HC_POWER` page 1: read `data[3]` before the `memset`, page 0 unchanged, a discriminator, validity bits, one snapshot | **Fixed** (section 8). |
| 34 | **must fix:** budget the whole wake pass, failure paths included | **Fixed:** one step per pass; validation counts long passes through every step and failure (section 4). |
| 35 | should fix: the battery poll can delay the waking input; measure bursts | **Fixed:** the poll after the first report (step 5); bursts in the stage test. |
| 36 | note: never batch encoder events into QMK's queue | **Agreed;** nothing is injected. |
| 37 | the validation's gaps: `asleep_s` from the edges; exactly one press and the transport counters gated; the ladder's own path; the daemon concealing a failed correction; the widths; more wake inputs; CHRG and USB cases; nonaligned log cases; Full at low battery | **Fixed:** the validation rewritten; Full never sleeps (section 1). |
| 38 | note: the evidence's scope (board-only, one P2 column, 7-30 s) | **Fixed** (the measurements' wording). |
