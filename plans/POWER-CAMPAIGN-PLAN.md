# The logged power campaign — plan (2026-10-15)

The goal: **an accurate battery level, a calibrated charge, every
feature's draw, a power ladder designed on numbers, and a runtime estimate.**
This plan is the measurement half of that, run on the bench with no hands but
JD using the keyboard as usual. It replaces inference with measurement in
[`BATTERY-GAUGE-PLAN.md`](BATTERY-GAUGE-PLAN.md) (the calibration run, Phase
2's step 2, gate 5) and does not change that plan's agreed order: **nothing in
Phase 2 is built until JD says build**, and every flash gets a codex review
first.

## What the bench can measure now

- **The pack's current and charge** (INA228 on the + lead; the charge
  register is gap-free between QT Py restarts, its scale the shunt's 1%),
  **its voltage** (MAX17048 `vcell_mv`, ±7.5 mV; the INA228's VBUS reads ~5%
  low), and the INA228's die temperature (`temp_c`, context only), at 1 Hz: `scripts/power_log.py`,
  [`../docs/test-bench.md`](../docs/test-bench.md), "The power logger".
- **The board's own view**: `ak820battery.py` (supply, level, `5C`, flags) and
  its RAM log (one row per 600 s, with the backlight and RGB drive).
- **From the Mac, hands-free**:
  - the RGB lighting (VIA's channel, `_noeeprom`, so RAM only);
  - the screen's backlight (flash 2f's `HC_DEVHOOK` ops 4-6, RAM only);
  - USB power (relay 2) and USB data (`uhubctl -l 0-1.4 -p 2`; it takes
    ~15 s, so give it a 40 s limit);
  - the health counters (stalls, key presses).
- **Not yet**: the BT/2.4G link states (only test builds' `HC_DRIVE` moves
  them), the MCU's sleep (no light-sleep build exists), standby's µA floor (the
  PPK2, deferred), and JD's idle pattern finer than 5 minutes.

## The runs, in order

**R1, the per-load map at the top (10-15, 22:10-22:41).**
`scripts/current_map.py`: on battery, ten states forward then backward, 90 s
each, the first 15 s skipped. RGB: JD's own, off, white at 255/128/64. Screen
(RGB off): max, 12, 4, 1, off. Results
([`../history/power-bench-2026-10-15/current-map-20261015-2210/`](../history/power-bench-2026-10-15/current-map-20261015-2210/README.md)),
at 4.17 V: **JD's settings 73.5 mA; RGB off 28.3; every light out 27.3.**
The RGB is 45 mA of JD's 73.5 (62%), and at brightness 128 only 10 mA; the
screen's backlight is ~9.4 mA at full, linear in its duty, ~1 mA at JD's
level 5. So the ladder's lever is the RGB; the 27 mA floor needs the MCU or
the radio.

**R2, USB data attached against untethered (10-15, after R1).** On battery:
data on, off, on (5 min each), via `uhubctl`. Every bench drain ran with data
attached, which JD's real use never has; R2 is the offset that turns a bench
runtime into a real one. **Result: ~1.7 mA** (73.9 attached against 72.15
untethered, 2.3%), the link and the daemon's traffic together
([`../history/power-bench-2026-10-15/usb-data-20261015/`](../history/power-bench-2026-10-15/usb-data-20261015/README.md)).

**R3, the logged cycle (from 10-15 night, ~2.5 days).** As drain 2e:
`bench_trial.py --wait-cut --poll 300 --full`, guarded by `trial_guard.py`, at
JD's settings and normal use, to the RGB cut, then a charge to FULL. The
logger runs throughout. New in the guard: a health row after every poll
(`guard-activity.csv`: key presses, stalls).
- **Mid-drain maps (from 10-16):** a short map (six states, 60 s holds, both
  passes, ~13 min; the list is under codex 6 below) at about 60%, 30% and 12%,
  between 00:00 and 06:00 so JD does not see his lights change, or with his
  say-so outside that window. It needs a small scheduler (to build
  10-16); a missed window is skipped, not forced. It shifts R3's load by well
  under 1% of its charge.

**USB data during the runs.** R3 keeps data attached:
comparable with drains 2c-2e, and it carries the live watch (the trial's
reads, the guard's progress deadline) and the activity rows. Runtimes are
corrected by the measured current (R2), recomputed, never by subtracting
hours. Data goes **off** wherever 1.7 mA is large: the ladder's dark rungs and
standby (6% of the 27 mA floor, and it would swamp a ~1 mA standby). Flipping
it on for each read is not worth it: each flip is a re-enumeration (~15 s of
`uhubctl` each way, the daemon reconnecting, a clock sync), ~290 a day at
5-min polls. **An untethered drain** is the shape for R5: the logger alone
sees the RGB cut (a ~45 mA step at ~3.40 V), then data on, dump the board's
RAM log (which holds its view of the whole drain at 600 s), relay on.

**R4, the charge from the cut** is R3's last phase: the whole current curve
from 0% to FULL.

**R5, a second logged drain at another load (JD's call when).** RGB off and
the screen dim, so ~2 days with his lights changed. It is the validation run:
the refit gauge (A1) and the runtime model (A7) are checked on data they were
not fitted on, and it shows how the voltage curve moves with load.
- **It could run on to the death.** Run 2 (10-04) timed the reserve,
  5 h 01 min from the cut, but had no logger. The pack log would add the
  reserve's mAh and the pack's voltage at the death (whichever goes first,
  the board's regulator or the pack's protection; not known which). It is
  below `5C`'s ~3.16 V clamp, so the board is blind for the last ~45-55 min.
  It costs ~5 h more and a brownout (the trial already dumps the RAM log at
  the cut). The trial needs a mode that keeps relay 2 off past the cut and
  calls the death from the logger's current, then turns the relay on. Not a
  run of its own, and nothing waits on it.

**R7, standby: the research now, the building later.**
For "Two kinds of away" (`BATTERY-GAUGE-PLAN.md`): the long tier's savings
are the largest that never touch typing in use (a 14 h night is ~1,030 mAh
today, ~380 with the lights out, single to tens of mAh in a real standby).
- **During R3/R4, no board time:**
  - the SN32F248B's sleep modes (which pins wake it, RAM retention, wake
    time), since key scanning lives in the row ISR (design question 2);
  - the CH582F's commands (`CH582F_PROTOCOL.md`): a sleep or slower
    connection timing, or only a dropped link (a reconnect of 1-15 s);
  - **a wake rig:** the USB relay's spare, relay 1 (it has two relays,
    whatever its "USBRelay4" name says), wired across one key's switch
    contacts, so the Mac presses a key
    at a stamped moment and times its arrival over BT. Unattended, it
    counts lag and lost wake keys over hundreds of sleeps. JD's hands: one
    small wiring job (a spare switch with two leads to relay 1's NO and COM,
    in a hot-swap socket JD never uses, that key remapped to F19 in VIA on
    both base layers; the Mac's listener needs Input Monitoring).
  - **The same rig counts dropped keystrokes**: presses sent
    (the relay, stamped) against presses the board saw (`key_presses`)
    against keys the Mac got. A gap between the first two is the scan or
    debounce; between the last two, the link. Run under plug-ins, display
    events with a track playing, low battery and sleep. Limits: one key at
    ~10 presses/s (no rollover or held-modifier cases), and the relay's
    contact life. If it finds something, the step up is optocouplers on
    the QT Py's spare GPIO: several keys, silent, µs timing.
  - ⚠️ **When the monitors sleep, the Mac locks, and the listener then
    registers no keypresses.** Wake tests use the board's counters and
    macOS's power log (`pmset -g log` names the AK820 as the source of the
    first activity after idle, even locked); drop and latency tests need the
    session unlocked (`keydrop_test.py` refuses otherwise and keeps the
    displays awake while it runs).
- **After the refit gauge and Phase 2's steps 3-4** (the lighting modes,
  light sleep; light sleep keeps the link, so it wakes instantly): a
  measurement build that sleeps on command and wakes on a key, timed by the
  rig; then the radio's states. It reuses light sleep's pieces and the
  reserve's machinery. It moves earlier if the research finds the wake path
  easy.
- **The PPK2** (deferred since 10-09, ~$182 CAD): µA floors, and logic inputs
  to stamp the rig's press against the radio's wake. JD's call when to order.

## The analyses

- **A1, the true level.** From R3's charge register: the mAh from FULL to the
  cut is the usable capacity, and at every moment `level_true` = (capacity −
  mAh used) / capacity. The board's displayed level against `level_true` is
  the gauge's real accuracy curve (until now it was judged against run 2's
  straight line). Then refit the voltage-to-level table on `level_true`, with
  an I×R term from the maps' ΔV/ΔI. The flat top (70-85%, read up to 8 points
  low) is the target.
  - **Done on R3 (10-18):** the gauge reads low throughout, up to 13.7
    points at 70-80% (the plateau: 15 mV from 85% to 70%), within 7 below
    60% and 2.5 below 20%; time and energy left agree within 0.1 point at
    JD's load. A table refit cannot fix the plateau
    ([`../history/battery-2026-10-15-drain-2f/README.md`](../history/battery-2026-10-15-drain-2f/README.md),
    "A1/A2").
- **A2, capacity**, in mAh and mWh, at the logged temperature, against
  drains 2c-2e's runtimes. **R3: 4011.6 mAh, 15,579 mWh to the cut** at
  JD's settings (309 mW mean).
- **A3, the charge.** The constant-current level, where the constant-voltage
  taper starts (the knee, task 12), the termination current, mAh in against
  mAh out. Refit `K_CC` and the charge model's knee from the current itself.
- **A4, the load model.** Each state's power (mA × pack V) from R1 and the
  mid-drain maps, so the draw at any pack voltage follows (the buck-boost
  draws more current as the pack falls).
- **A5, the bench's USB offset** (R2), applied to every bench runtime.
- **A6, the VBUS error's shape** (a ratio or an offset) against `vcell_mv` and
  `5C` below the clamp.
- **A7, the runtime model.** Remaining capacity (A1) over the modelled draw
  (A4 at the current settings) is a time left, for any settings. Check it
  against R3's real runtime, then R5's.
- **A8, the ladder's sizing.** From `guard-activity.csv`, the share of time
  JD's keyboard sits idle 5, 15 and 30 minutes or more. Times A4's savings
  per rung (lights dimmed or off, screen dimmed or off), that predicts each
  rung's runtime gain before anything is built. Gate 5 (the matched runtime
  run) still measures it after. The 1-minute dim rung needs finer data: a
  battery-log column of key presses per interval would give it, in a later
  flash.
  - **Gaps by length, per day** (for "Two kinds of away" in
    `BATTERY-GAUGE-PLAN.md`, whose ~2 h deep-standby line is open): how many
    idle gaps of 1-3 h and of over 3 h there are, and how many of each end in
    typing rather than in the end of the record or a reboot. Each one that
    ends in typing is a waking key that deep standby could cost, so the count
    above a candidate threshold is the price of putting the line there. At 5
    min resolution a gap's length is known to ±5 min.

## What follows in firmware (each a flash, codex first)

1. **The refit gauge** (A1, A3): a new voltage-to-level table, the I×R term,
   the charge model's knee. Phase 1b's machinery stays; its constants change.
2. **A time-left readout** (A7): the board knows its own settings, so with
   A4's model it can estimate its draw, and with the level its time left.
   Shown on the camera page first, then wherever JD wants it.
3. **The power ladder** (Phase 2's agreed order, steps 3-5), sized by A8,
   only when JD says build.
4. Optional: activity in the battery log (A8's 1-minute rung); a dev hook for
   the BT link states.

## Codex's review (10-15) and what changed

[Verbatim](review-codex-power-campaign-2026-10-15.md), prompt
[`review-prompt-power-campaign.txt`](review-prompt-power-campaign.txt). Its
verdict: three must-fix items before R3, all host-side.

| # | finding (short) | disposition |
|---|---|---|
| 1 | **must-fix:** the guard watched the trial's survival, not its progress: failed reads retry forever, so the pack could drain through the reserve with nothing charging it; the trial's `fail()` turns the relay OFF and the guard stops on FAIL | **Fixed in `trial_guard.py`:** before the cut, 45 min without a successful read stops the trial and turns relay 2 ON; a FAIL before FULL-ON also ends with the relay ON; 16 h after FULL-ON without `done` stops the trial, relay left on. The Mac never sleeps (`pmset` sleep 0), and a `caffeinate` is tied to the guard as well. |
| 2 | **must-fix:** the new activity read ran on any read row, including the cut's, colliding with the trial's dump; `--stalls --json` is ~25 transactions, not one | **Fixed:** only after a successful `wait-cut` poll without `lights_cut`, and one transaction (`read_stalls`), with a 30 s limit. A failed read is a blank row, an unknown interval. |
| 3 | **must-fix:** R3 needs a verified FULL start; R1 and R2 took charge out | **Done this way:** after R2, `bench_trial.py --full` tops the pack up to the charger's termination. R3 starts from there with the relay switched off at a logged moment, and the charge register's value at that moment is recorded. The end is the RGB cut as the logger sees it (the RGB current's step), not the trial's next poll. |
| 4 | A1: the count is a sound reference at ~C/50, but "level" changes meaning (today's gauge is remaining full-white runtime; A1 is remaining charge); one I×R term cannot remove hysteresis and polarization; it needs load steps at the same charge, relaxation, and R5 kept for validation; `CHARGE_IR_MV` no longer exists (Phase 1b) | **Accepted.** The refit decides the definition first (charge or runtime) and keeps the table, the clamp countdown and the charge model consistent with it. Load steps come from the mid-drain maps (each state's first seconds kept for the step); relaxation after unplug becomes a run of its own (R6, below); R5 stays held out. |
| 5 | the register is gap-free but its scale is the shunt's 1%; a QT Py power loss restarts it, and a reconnect does not mark that; `temp_c` is the INA228's die; VCELL is ±7.5 mV, not truth | **Accepted.** Analyses split the log at every restart and never difference across one; A2's mAh carry a ±1% scale; `temp_c` is context only; A6 is relative until a meter checks a few voltages. |
| 6 | the map: fine for steady power; the two passes should be compared before averaging; white at 64 is not JD's effect dimmed; "LCD off" is the backlight only; a 20-hold map at 60 s is 20 min | **Accepted.** The mid-drain map's list: JD's, RGB off, JD's effect at 64, white 255, LCD max and off: six states, 60 s, both passes, ~13 min. "Screen off" means the backlight throughout. |
| 7 | R2 measures tethering plus host traffic, not a universal offset; repeat it, and model a current difference, never subtract hours | **Accepted.** R2 runs with the daemon as usual (the traffic R3 has); it is repeated at mid-drain if the first result is not small. |
| 8 | A3: the taper alone does not prove CV; CHRG high can be a fault; correct for the discharge between the cut and relay-on | **Accepted:** CV onset is the taper together with the voltage's plateau; termination is checked against the board's own FULL and the current. |
| 9 | A7 needs an integral over the falling voltage (current rises as V falls), a defined endpoint, and held-out validation | **Accepted:** time left is the integral of dQ/I(Q, state) to the RGB cut (the reserve's ~5 h is separate), validated on R5. |
| 10 | A8: 5-minute counts bound long idles but cannot place them; 16-bit counter; the battery log's 600 s is coarser | **Accepted:** A8 reports lower and upper bounds for 5/15/30 min. The fine data needs firmware that accumulates time beyond each idle threshold, not counts per log row. |
| 11 | risks: restoration not guaranteed if a process dies; the map checks battery only if it switched the relay; uhubctl takes ~15 s and its timeout is an unknown result | Noted. `current_map.py` gets an explicit supply check before measuring (before the mid-drain maps); every `uhubctl` call has a 40 s limit and is checked against `ioreg`. |

**R6, added: relaxation and partial charges** (codex 4 and 8). After R4's
FULL, and later from a few partial charges: unplug at a logged moment and log
the pack's voltage for 2 h under the board's own load. That is what the
gauge's re-seat sees after a real unplug.

## Hands

None for R1-R4: JD uses the keyboard as usual and leaves the slider on BT.
⚠️ A pack swap, a slider flip to cable, or unplugging the logger's QT Py
breaks the run (the last restarts the charge register). R5 changes JD's
lighting, so it starts when he says.
