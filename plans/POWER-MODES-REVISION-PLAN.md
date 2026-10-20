# The power modes, revised: round timings, and a Power saver that differs (2026-10-20)

Status: **revision 2, after codex's plan review (below). Built on branches,
host-tested, not flashed** (see "Implementation"). Drift's cap is decided:
option (c), the simpler one and the less likely to break (firmware step 4).
The discussion behind the plan is in [`BACKLOG.md`](BACKLOG.md), "Balanced and
Power saver: widen the spread, or merge them?". The figures come from
[`../docs/power.md`](../docs/power.md)'s model and the logged drain's
activity log.

## Why

- **Balanced is aggressive for someone at the desk.** It dims after 1 minute
  without a key, which happens while reading or using the mouse. The intent:
  **Balanced works as if plugged in unless you are most likely gone**, or
  watching a movie.
- **Balanced and Power saver hardly differ in runtime.** Modelled at JD's
  logged use they are ~13.3 and ~13.7 days. Both spend long absences asleep,
  so what costs is lit typing, and both type at full brightness.
- **Timings people can remember.** Every mode gets round numbers and at most
  two steps. An absence costs whatever the ladder spends before it finishes,
  so the round timings below were modelled directly, not assumed free.

## The modes as revised

On battery only, as now:

| mode | while typing | dims | screen off and asleep |
|---|---|---|---|
| Always on | as set | never | never |
| Balanced | as set | 10 min | 20 min |
| Power saver | LEDs capped at value 128 | 1 min | 3 min |
| Lights out | dark | -- | 1 min |

- **"Dims"** is today's dim stage: the lighting's value capped at 64.
  - The screen is not dimmed. Capping it at level 4 for Balanced's 10 dim
    minutes would save ~0.1 mWh an absence at a dim screen and ~6 at full:
    comfort, not power, and the screen showing the clock is worth keeping
    readable.
- **"Screen off and asleep"** is today's screen-off stage (LEDs dark,
  backlight off), with the deep sleep wanted at the same moment.
  - Balanced and Power saver lose the LEDs-off-but-awake stage: they go from
    dim straight to dark.
- **Power saver's cap while typing** is a ceiling on the lighting's value, as
  the dim's is: `hsv.v > 128` becomes 128.
  - Under QMK's CIE curve, 128 is about half as bright to the eye, and a
    quarter of the LED power: 43 mW against 189 at full white.
  - A board already at 128 or below is not changed. Halving a dim board again
    could shift its colours (a dim magenta toward red), which is why the cap
    is a fixed ceiling and not half of the user's value.
- **Lights out sleeps after 1 minute instead of 30 s.** That is at most
  0.8 mWh more a pause, and fewer pauses become a wake.
- **Below 15%, the rule stays and now carries the cap.** Every mode takes
  Power saver's lighting, including its half cap while typing, except Lights
  out, which is darker anyway.
  - Balanced sleeps at Power saver's 3 minutes.
  - Always on still never sleeps.
  - The "Saving power" notice and the bulb are as now.
- **Unchanged:**
  - nothing applies on USB;
  - every stage is a cap, never a change to a setting;
  - any key or the knob restores the mode's typing state, and the key types;
  - the mode is persisted as 0-3, so no migration;
  - Balanced is the default;
  - the names and the bulbs stay.

### What it is worth (modelled)

Days a charge at full white with the screen at level 5, before → after.
- **The method:**
  - **the split:** the first 85% of the energy at the mode's own policy, the
    last 15% under the low cap (Saver's stages and cap; Always on awake);
  - **the log:** 48.68 h of JD's activity, the 1.6 h logger outage of 10-17
    excluded;
  - **the counting:** every 5-minute interval with a key counted as fully
    lit.

The figures:
- **JD's logged use** (a key in 11.5% of 5-minute intervals):
  - Always on: 2.6 → 2.7;
  - Balanced: 13.3 → **12.6**;
  - Power saver: 13.7 → **21.2**;
  - Lights out: 26.8 → 26.6.
- **The office worst case** (8 h nonstop, asleep the rest):
  - Always on: 2.4 → 2.6;
  - Balanced: 5.9 → **6.6**;
  - Power saver: 6.0 → **10.7**;
  - Lights out: 14.6.
- **Stop-start** (4 h of typing, twenty 15-minute breaks):
  - Always on: 2.5 → 2.7;
  - Balanced: 8.8 → **7.3**;
  - Power saver: 9.7 → **15.4**;
  - Lights out: 21.9 → 21.4.

Notes on the figures:
- **Balanced gains on the office day.** Its last 15% now types at the half
  cap.
- **Balanced's cost is per absence:** about 10 more minutes at full lights,
  ~50 mWh (0.3% of the pack) each time. That is why the stop-start day loses.
- **The log cannot see pauses of 1-5 minutes**, where today's 1-minute dim
  saves power and the new ladder does not. So the modelled gap between old
  and new Balanced is probably understated. It is not a bound on the new
  figure: the model also counts every interval with a key as fully lit.
- **Above 15% alone,** Balanced is 11.1 days at JD's use, which is the figure
  for a user who recharges before the low cap ever applies.

## Firmware

All in `keyboards/a_jazz/ak820pro/`, on a branch off `ak820pro-jdlien`.

1. **`power.c`, the ladders:**
   - Normal: `{600000, 1200000, 1200000, 1200000}` (dim 10 min; LEDs off,
     screen off and asleep all at 20).
   - Saver: `{60000, 180000, 180000, 180000}`.
   - Minimal: `{0, 0, 0, 60000}`.
   - Full: unchanged.

   With `lights_off_ms == screen_off_ms`, `stage_for()` returns
   `SCREEN_OFF` first (`power.c:178`), so `LIGHTS_OFF` is reached only when
   the bench forces it. It stays in the enum: the stage numbers are on the
   wire (`HC_POWER`, op 14 sub 2) and in the bench scripts. The bench's 30 s
   minimum for its own sleep time stays.
2. **`power.c`, the active ceiling:** add `active_val` to `ladder_t`. It is
   255 for every mode but Saver, which gets 128 (`SAVER_VAL`); Minimal's is
   moot, since it is dark. The contract:
   - **The desired ceiling `want_val`**, set in `power_task()`:
     - **on battery, or with a stage forced:** the mode's `active_val`. A
       forced stage takes it whatever the supply, as the forced stage itself
       ignores the battery-only rule, so `ladder_check.py` keeps working on
       USB.
     - **with the low cap holding** (on battery, level ≤ 150 ‰, a known
       level), Minimal excluded: the lower of that and Saver's.
     - **otherwise** (on USB, nothing forced): 255.
   - **`apply()` uses `want_val` for `POWER_STAGE_ACTIVE`.** DIM stays
     `DIM_VAL` (64), which is lower, and the off stages are unchanged.
   - **Kept apart:** `want_val` (desired) and `val_cap` (applied).
     Comparing Saver's 128 with an applied DIM's 64 would see a change on
     every tick.
3. **`power.c`, re-applying on a ceiling change.** Today `apply()` runs only
   when the stage or Minimal's message light changes. `want_val` also changes
   with no stage change:
   - on `Fn`+`B` into or out of Saver;
   - at a boot with Saver persisted;
   - on battery → USB and USB → battery while typing;
   - at the low cap's entry and exit;
   - on `power_set_mode()`;
   - on a forced stage.

   **The fix:** add the last desired ceiling to `power_task()`'s change check.
   `apply()` already repaints only when an applied cap or scale changes, so an
   effective change re-runs `rgb_matrix_reinit_effect()` once, and an
   unchanged tick runs it never (a `want_val` change under DIM changes
   nothing applied).

   Mode-derived updates go through `power_task()`.
   `power_set_lights_cut()` keeps calling `apply()` directly, which reads the
   current `want_val`.
4. **`rgb_matrix_kb.inc`, Drift and the cap.** **Decided: (c).** It is the
   smaller change and removes the cap from the palette's cache key, a class
   of stale-palette bug; Drift's own LED power is small.
   - **Today:** Drift builds its nine stops through the capped
     `rgb_matrix_hsv_to_rgb()`, so each stop's own value is clipped and
     converted through the CIE curve. Only then does Drift scale the RGB
     linearly by the user's value (`rgb_matrix_kb.inc:149`, `:281`).
   - **What goes wrong:** a dim Drift user's bright stops are clipped by a
     cap the product is already under. Under Saver's 128, a Drift at value
     100 would lose its bright stops' drive from ~99 to ~19.
   - **The same flaw exists today at the dim's 64.**
   - Two fixes; the recommended one first.
   - **(c) Recommended: the cap acts on Drift's own value knob.**
     - **How:** `v_eff = min(v, val_cap)`, and the palette built uncapped
       (plain `hsv_to_rgb`), keyed on hue and saturation only.
     - **What changes:** a Drift at or below the cap is unchanged. Above it,
       Drift looks exactly as the user's own value at the cap would, so the
       rule "a board at or below the cap is left alone" holds for Drift too.
       Nothing changes on USB.
     - **The cost:** Drift's brightness knob is linear in drive (value 128
       drives 128 of 255; other effects drive 48), so its cap saves about half
       its LED power, not three quarters. The gauge counts the real drive
       either way, and Drift's field is dark-biased.
   - **(a) The alternative: Drift's value applied before the conversion**, as
     in every other effect.
     - **How:** stops at `scale8(stop_v, v)` through the capped conversion,
       the final linear scale removed, the palette keyed on the value.
     - **What it buys:** one rule everywhere, and the brightness keys behave
       alike across effects.
     - **The cost:** Drift changes visibly at every value below 255, on USB
       too. At value 128 it would drive 48 instead of 128.
   - **Not taken:** a CIE-aware stop ceiling that keeps today's rendering
     (codex's first option). It still clips a Drift user at 100, whose drive
     is above the cap's 48.
5. **Comments:**
   - `power.h`:
     - the enum's per-mode lines;
     - the stage comment (LIGHTS_OFF is forced only);
     - `power_val_cap()`'s accessor comment, which names only the dim
       (`power.h:56`).
   - `power.c`: the header and the ladder's comment, pointing here.
6. **No change needed elsewhere:**
   - `battery.c`'s energy model reads the committed frames' drive and the
     backlight's duty, so the cap's saving is counted as it happens;
   - `sleep.c` reads `ladders[].sleep_ms` through `power_sleep_wanted()`;
   - the overlay names;
   - the bulbs (`display.c:1861`).

   Only op 14 reports the value cap (its reply's `data[6]`). `HC_POWER`'s
   `data[6]` is the count's state, and its consumers need no change.

Not touched: the factory-test path writes a raw value 20 that bypasses the
conversion (`quantum/rgb_matrix/rgb_matrix.c:247`). It is not a selectable
effect. The GPIO status LEDs bypass it by design.

## Host side

- **`scripts/battery_sim/`** (it builds the firmware's `power.c` verbatim):
  - **`run.sh` gains a dev-hooks build** (`-DAK820_DEV_HOOKS`) for the
    forced-stage and `power_set_mode()` cases, and runs `mutants.py`. New
    scenarios are registered in the dispatcher and the run list.
  - **`ladder.c`, new and changed cases:**
    - **every mode's edges,** at t − 1 ms and t: Balanced 600 / 1200 s,
      Saver 60 / 180 s, Lights out's sleep at 60 s;
    - **Saver while typing:** ACTIVE with the value cap 128;
    - **the transitions,** each with exactly one reinit and none on the next
      unchanged tick:
      - battery → USB and USB → battery in Saver while typing;
      - a boot with Saver persisted;
      - `Fn`+`B` into and out of Saver;
      - `power_set_mode()`;
      - a forced ACTIVE in Saver on USB (128) and in Full (255);
      - a Saver ceiling under DIM, with no repaint;
    - **the low cap,** at exactly 150 and 151 ‰ and at the unknown level,
      entry and exit:
      - Always on: 128 while typing, Saver's stages, no sleep wanted;
      - Balanced: 128 while typing, dims at 60 s, sleep wanted at 180 s;
      - Lights out: unchanged;
    - **LIGHTS_OFF unreachable** without a forced stage;
    - **the old cases that encode the old ladder:** the edge tables, and
      `l_val_cap` (line 118), `l_lights_cut` (134) and `l_minimal_enter`'s
      Saver expectation (202).
  - **`count.c`'s `c_load_step`** (line 138) waits 300 s for LIGHTS_OFF and
    checks an hour at the level-4 backlight cap.
    - It is redesigned around SCREEN_OFF at Balanced's 1200 s: 106.9 mW.
    - The level-4 cap, now forced only, gets its own case in the dev-hooks
      build.
  - **`mutants.py`** today mutates `battery.c` only. It gains `power.c`
    mutants, each of which must be caught:
    - no re-apply on a mode change;
    - the low cap not taking Saver's ceiling;
    - Normal's old `lights_off_ms` restored;
    - the cap as `v / 2` instead of a ceiling;
    - the desired and applied ceilings compared as one.
  - **The colour path:** the simulator's HSV stub cannot see the CIE curve
    (`sim.c:72`). A host test compiles QMK's `quantum/color.c`, with the real
    table, beside `power.c`'s hook, and checks exhaustively, for caps 64 and
    128 and every user value:
    - the drive never exceeds the cap's;
    - a user value at or below the cap is never changed;
    - for Drift, the chosen fix's rule (option c: Drift at `v` in Saver
      equals Drift at `min(v, 128)` uncapped).
- **`scripts/ladder_sleep.py`:** `SLEEP_S = {1: 1200, 2: 180, 3: 60}`, and its
  docstring. Its deadline guard scales with `want`, and its criterion stays
  the script's ±15 s of the mode's time.
- **`scripts/ladder_check.py`:**
  - **Saver passes per effect:** the mode set to Saver and the stage forced
    active, against Full forced active as the reference. On USB, since a
    forced stage takes the mode's ceiling.
  - **Tolerances:** the animated and random effects (the cycles, Jellybean,
    Rainfall) are judged on means over the hold with tolerances, not
    identical samples.
  - **Typing Heatmap** is stimulated, so a zero reading means something.
  - **The criteria:**
    - Saver's drive at or below Full's;
    - solid white at 255 within 5% of value 128's drive;
    - a second pass at a user value of 100, where Saver must match Full for
      every effect, Drift included.
- **`scripts/current_map.py --set stages`:**
  - **A "Saver, active" step** at JD's white, against the model. Expected
    ~161 mW with the bench's USB data attached (~154 untethered).
  - **Above 15% only:** it refuses to run at a level of 150 ‰ or below. A
    forced ACTIVE no longer shuts out the low cap's ceiling, so its comment at
    line 292 changes.
- **Op 14's reply** can carry the old stage and ceiling until the next 10 Hz
  tick. Every script reads again after a tick before judging.
- **`scripts/deep_cycles.py`:** unaffected. It uses op 14 sub 5's bench sleep
  time, which replaces the mode's.
- **`hostagent/ak820battery.py`:** names only, unchanged.

## Documentation

Part of the change, not a follow-up. It lands with the flash's record, and
every line that states a mode's behaviour or a runtime is checked.

- **[`docs/power.md`](../docs/power.md):**
  - **the short version's** "roughly two weeks";
  - **the mode table** and its bullets;
  - **"Modelled: a day of use":** its "well under 1% a day" for the ladder
    (line 131) needs qualifying, since repeated breaks in the new Balanced
    exceed it;
  - **"Modelled: the power modes at JD's use",** recomputed by this plan's
    method (the last 15% included), with the Balanced-against-Power-saver
    paragraph rewritten;
  - **the office and stop-start figures;**
  - **the worked example** (line 227: white at 128, screen 8):
    - each absence is now 10 minutes at 156 mW and 10 dim, ~48 mWh;
    - three absences leave 19 hours asleep, not 20;
    - the total is ~1,021 mWh a day, ~15.3 days;
  - **the R5 forecast** "probably about two weeks" (line 397).
- **[`docs/battery.md`](../docs/battery.md):**
  - **"The idle ladder and the power modes":**
    - the table;
    - the low-battery paragraph;
    - Lights out's "~6 days a charge" line;
    - "restores everything" (line 1315), which means the mode's typing
      state, with the ceiling's repaint described beside the stage's;
  - **the stale "Balanced should roughly double"** (from before deep sleep)
    replaced;
  - **the state note** at the top.
- **[`docs/hardware.md`](../docs/hardware.md):** "Deep sleep", the
  **When.** line. The measured "Balanced slept 421 s (the ladder's 420)"
  stays as a dated record, marked as the old timing.
- **[`docs/display.md`](../docs/display.md):** "At a deep sleep and its wake",
  the screen-off line ("Balanced 7 min, Power saver 3").
- **[`docs/test-bench.md`](../docs/test-bench.md):**
  - `ladder_sleep.py`'s times (Balanced 1200 s, Power saver 180, Lights out
    60);
  - that `deep_cycles.py`'s 30 s is a bench time, not Lights out's own;
  - `ladder_check.py`'s new passes;
  - `current_map.py`'s above-15% rule.
- **[`../README.md`](../README.md):** the battery line, only if a figure in
  it moves. It quotes only the measured hours and the standby weeks, which
  do not.
- **[`../CLAUDE.md`](../CLAUDE.md):**
  - the `Fn`+`B` quick reference, with the timings in a short form;
  - a new state block for the build. The 10-19 lines with the old timings
    stay as dated history.
- **[`current-status.md`](current-status.md):** a dated block.
- **Historical plans** ([`PHASE2-BUILD-PLAN.md`](PHASE2-BUILD-PLAN.md),
  [`DEEP-SLEEP-BUILD-PLAN.md`](DEEP-SLEEP-BUILD-PLAN.md),
  [`POWER-MODE-ICONS-PLAN.md`](POWER-MODE-ICONS-PLAN.md)): one line at their
  ladder tables pointing here. No rewrite: they record what was built then.
- **[`BACKLOG.md`](BACKLOG.md):** the "widen or merge" entry closed as
  decided, pointing here.
- **Code comments that state timings or behaviour:**
  - `power.h` and `power.c` (firmware step 5);
  - `current_map.py:292`;
  - the simulator's case comments.
- **The sweep, before the docs commit.** Every hit must be updated or be a
  dated record:

      grep -rn -E '(Balanced|Power saver|Lights out|ladder|dim)[^.]{0,60}[0-9]+ ?(s|min)\b|420|180 s|30 s' \
        docs README.md CLAUDE.md plans/current-status.md hostagent scripts \
        ak820-agent/*.txt

## Building and testing outside the main clone

The main clone's checkout belongs to the bench session's diagnostics, and
three tools are tied to the main clone:
- **`build.sh`** sources `env.sh`, which resets `QMK_HOME` unconditionally
  (`env.sh:8`);
- **`check_via_sync.py`** hardcodes the main clone's path
  (`check_via_sync.py:13`);
- **the simulator** defaults to it (`run.sh:8`).

So:
- **Implementation and host tests happen in a worktree** of the firmware
  (`git worktree add <scratch>/fw-power-modes -b power-modes-revision
  ak820pro-jdlien`):
  - **compile checks** with `source env.sh; QMK_HOME=<worktree> qmk compile -kb
    a_jazz/ak820pro -km via -e "EXTRAFLAGS=..."` (each flavour's flags as
    `build.sh` sets them);
  - **the simulator** with `BATTERY_SIM_SRC=<worktree>/keyboards/a_jazz/ak820pro`;
  - **the mutants** with `--src` the same.
- **The provenance build** (`./build.sh dev`, whose checks the flash needs)
  runs in the main clone, checked out on this branch with the bench
  session's agreement and put back afterwards, as the icon previews were.
  Teaching `env.sh` to respect a set `QMK_HOME` would remove the dance, but
  it changes every session's build and is not part of this plan.

## Validation

1. **Host:** the simulator's cases including the new ones, every mutant
   caught, the colour-path test, and dev, daily and fpb compiling with no
   warnings.
2. **Codex:** the implementation review, before the flash.
3. **The image itself must sleep.** The deep-sleep regression follows the
   image's layout, not the transport
   ([`DEEP-SLEEP-REGRESSION-2026-10-20.md`](DEEP-SLEEP-REGRESSION-2026-10-20.md)),
   and this build moves code. So the exact artifact runs
   `deep_cycles.py --n 20` on BT and `--n 10` on 2.4G, through the ladder's
   own path. If it fails the way `b2a07e0d37` does, the build waits on the
   regression's fix. A dev artifact's pass says nothing about a daily or fpb
   artifact, which link differently: each release artifact gets its own run
   later.
4. **On the bench, no hands** (relay 2 off unless noted):
   - **the stage map** with the Saver-active step: every step within 2% of
     the model, like for like (USB data attached on both sides);
   - **`ladder_check.py`** with the Saver passes and the value-100 pass, on
     USB;
   - **`ladder_sleep.py --mode 3` and `--mode 2`:**
     - asleep at 60 s and 180 s after the rig's tap, within the script's
       ±15 s;
     - woken by the rig, with the key kept;
     - no pass of 25 ms or more;
   - **`ladder_sleep.py --mode 1` once:** the logger's step from ~300 to
     ~129 mW at 600 s, asleep at 1200 s.
5. **JD, one action** (on battery, `fnb_caps_test.py` running, since it needs
   a person's `Fn`+`B` presses):
   - cycle the modes for a minute while typing;
   - judge whether Power saver's 128 looks right.

   Pass: no new pass of 25 ms or more. A different ceiling is a one-constant
   change.
6. **The low cap on the board:** the simulator covers its selector. Nothing
   on the bench sets the level, and a gauge override is not worth its risk
   for this.
   - R5 runs untethered (USB data blocks the deep sleep) and crosses 15%
     around day 10.
   - Its logger sees the change: typing's draw falls from ~300 to ~154 mW,
     and the idle sleeps move to 3 minutes.
   - The board's RAM log, dumped at the end, carries each 15-minute period's
     LED drive and energy.
   - Neither records the cap or the bulb directly. That is accepted: the
     renderer's own checks are steps 1 and 4.
7. **Then R5:** the logged drain in Balanced at real use, on this ladder.

## Sequencing and coordination

- **The bench** belongs to the deep-sleep session until it says otherwise.
  The main clone is used only with its agreement. The flash is a
  provenance-named `./build.sh dev` artifact, and the session gets its commit
  and token.
- **If the regression's fix lands** on `ak820pro-jdlien` first, this branch
  is rebased onto it, and validation 3 is rerun on the new artifact.
- **R5 waits** for this flash and for validation 3 on the transport it will
  use.

## Optional, later: the host's own view (needs plumbing we do not have)

Two toggles a user could set in the host agent, both off by default. Neither
is in this build.

- **Presence: "I'm here."** The agent knows the system's last input,
  including the mouse (macOS `HIDIdleTime` through IOKit; Windows
  `GetLastInputInfo`). While the host has seen input recently, it sends a
  hint, and the board holds off the dim. It serves someone who reads or
  mouses for long stretches and wants the board lit. A keyboard does not
  need its lights while nobody types, so this is for people who keep them on
  because they like them.
- **Host sleep: "I'm leaving."** When the host sleeps, locks or blanks its
  displays, the agent asks the board to sleep now. The board still goes
  through the ladder's own path and its blockers; the hint never calls the
  deep sleep directly. The waking key works as now.

Constraints on both:
- **Presence is not physical input.** It must not touch QMK's activity
  timestamp, which would postpone the sleep as well.
  - It holds off the dim stages only.
  - At the mode's sleep time, the sleep's policy overrides it and turns the
    lights and the backlight off. A presence-lit board would otherwise trip
    the sleep's `lit` and `rgb` blockers (`sleep.c:261`) and never sleep.
- **Bounded against a sender that keeps renewing:**
  - each hint lapses after a fixed time;
  - hints are rate-limited;
  - the sleep time still applies;
  - the low cap overrides presence;
  - in Always on, which never sleeps, presence changes nothing, so there is
    nothing to bound.
- **State is cleared:**
  - physical input cancels a pending sleep request;
  - every hint is dropped at a wake, a transport or slot change, and a
    disconnect;
  - repeated sleep hints count as one.
- **Transport:**
  - **USB is no help:** on USB power the ladder is off.
  - **2.4G's raw HID** (the daemon's `--radio`) needs the command added to
    raw81's allow-list (`bluetooth/raw81.c:202`).
  - **Over Bluetooth, the lock-LED channel**
    ([`LOCK-LED-CHANNEL-PLAN.md`](LOCK-LED-CHANNEL-PLAN.md), close to done):
    - it is one-way;
    - Windows over BT is untested;
    - macOS refuses its writes once the session is locked (line 38).

    So a lock or sleep notification is best-effort: send before the lock,
    and do not count on it arriving.
- **A sleeping board cannot hear the host:** the deep sleep wakes on a key,
  the knob, a charger's CHRG edge or its backstop, not on the radio.
- **One owner per radio transaction,** as for the clock:
  - the daemon's existing single transaction owner sends the hints;
  - an OS sleep callback enqueues work and never starts an overlapping
    transaction;
  - the locks are flock on macOS and the existing byte-range lock on
    Windows;
  - the lock-LED sender serializes whole frames and cancels around real lock
    changes.

Also later:
- a user-chosen battery brightness instead of the fixed 128 (a VIA custom
  value);
- A8's 1-minute activity column, which would show how often 1-5 minute
  pauses happen and settle the new Balanced's real cost.

## Implementation (10-20)

On two branches, both named `power-modes-revision`, built in worktrees so
the main clone's checkout is untouched (neither pushed):
- **Firmware: `0e322097f4`, then `5df4e0e3b7`** (comments only, codex's
  review) on `ak820pro-jdlien` at `8b06e4dcc4` (the deep sleep's fix had
  landed, so no rebase). `power.c`, `power.h`, `rgb_matrix_kb.inc`,
  `rules.mk`'s comment. Dev, daily and fpb compile with no warnings, and the
  dev build's `ds_deep_wfi` is in SRAM (`0x200014ec`).
- **Host side: `58be856`, `4d00aec` (the docs) and `05a6f60` (codex's
  fixes)** on this repo, rebased onto `main` at `7e2d218`. It is not on
  `main` because `run.sh` runs against the main clone's `power.c`, the one on
  the board, and the new ladder cases fail there until the firmware merges.
  Both merge together.

What it holds, against the plan:
- **The simulator:**
  - every ladder edge at t - 1 ms and t;
  - Saver's 128 while typing;
  - LIGHTS_OFF never reached unforced;
  - each ceiling move repaints once and the next ticks apply nothing (a boot
    in Saver, `Fn`+`B`, the cable both ways, the dims);
  - the low cap at exactly 151 and 150 ‰ and its exit;
  - the unknown level;
  - `c_load_step` at Balanced's dark stage, exactly 106.9 mW;
  - the dev-hooks build: a held stage's ceiling on USB, `power_set_mode()`,
    the ceiling under the dim, LIGHTS_OFF held at 110.2 mW.
- **The colour test** (`colour.c`): QMK's own `color.c` and CIE table, every
  hue, saturation and value under 64 and 128, through `power.c`'s ladder.
  - DRIFT under a ceiling equals DRIFT at `min(v, ceiling)` with none, frame
    for frame;
  - RAINFALL never drives over it.
- **Mutants:** all 8 new ones caught (the 5 the plan names, a held stage's
  ceiling on USB, and Drift's palette and knob).
  - `tail_fw.c` had not linked since Phase 2 (no stub for the LED drive), so
    `mutants.py` could not run at all. Fixed.
  - One older mutant, "the re-seat before RELAX_S", survives on
    `8b06e4dcc4`'s unchanged `battery.c` too. It is listed as a known
    survivor, printed on every run (`BACKLOG.md`).
- **Departures from the plan:**
  - **The low cap's 128 applies in every mode, Lights out included.** Lights
    out never reaches ACTIVE on battery unless the bench holds it, so the
    exclusion bought nothing.
  - **`ladder_check.py` judges harder than "Saver at or below Full":** at
    most 0.35 of Full for the effects that convert at the user's value
    (expected 0.19) and 0.70 for Drift (expected 0.50).
  - **`ladder_check.py` sets Always on for its stage pass.** A held ACTIVE
    now takes the mode's ceiling, so a Saver left set would cap it.

### Codex's implementation review (10-20) and what changed

Verbatim: [`review-codex-power-modes-impl-2026-10-20.md`](review-codex-power-modes-impl-2026-10-20.md),
prompt [`review-prompt-power-modes-impl.txt`](review-prompt-power-modes-impl.txt).
Its verdict: the firmware is correct, DRIFT's option (c) included, with no
change to `0e322097f4` needed. Fix the colour harness before the flash, and
the bench scripts before accepting validation 4.

1. **Must-fix: `colour.c`'s `scale8` was not the board's.** QMK builds
   lib8tion with `FASTLED_SCALE8_FIXED=1`, so the board scales by `i × (1 + s)
   >> 8`. **Fixed** (`05a6f60`); everything passes with it.
2. **Must-fix: `ladder_check.py` passed on all-zero readings.** **Fixed:** a
   reference must read lit (at least 20 ‰ at value 255, 2 at 100, white at
   128 at least 100). It ends INCOMPLETE (exit 2) without `--rig`, since the
   Heatmap is then not judged. A mocked all-zero board now fails 25 rows.
3. **Must-fix: the clean-ups could skip steps.** **Fixed in both scripts:**
   each step runs on its own, a failed one exits 1, and the stage held at the
   start is put back, not released.
4. **Must-fix: `current_map.py` never checked what it measured.** **Fixed:**
   - op 14 is read at each hold's start and end, and a mode, stage or ceiling
     other than the one meant stops the run;
   - the level is checked at every hold, and must be known for the stage map;
   - opens are retried.
5. **Should-fix: the samples could repeat one second, and short windows of an
   animation are not comparable.** **Fixed:**
   - samples 1.2 s apart;
   - every effect at speed 255, so a cycle completes within a sample;
   - the stage pass's fixed 20% documented as such.
6. **Should-fix: the colour build declared its own `rgb_t`/`hsv_t`.**
   **Fixed:** it takes QMK's `color.h` on both sides of the call.
7. **Should-fix: no independent baseline for USB, no warm cache, no chunks.**
   **Fixed:**
   - `k_drift_usb` hashes DRIFT on USB across three speeds and warm-cache
     changes against `8b06e4dcc4`'s own DRIFT, which `run.sh` rebuilds to
     check the constant;
   - a warm-cache stream with the ceiling, hue, saturation and value moving;
   - the board's 81 LEDs in QMK's 5 chunks;
   - the transitions it named (Saver's held ACTIVE released on USB, the low
     cap on a held ACTIVE in Lights out, `Fn`+`B` under the lights cut);
   - the dev-hooks scenarios under the sanitizers;
   - a ninth mutant (DRIFT's cache blind to hue), caught by `k_drift_usb`.
   The LED positions stay a synthetic grid, and the docs no longer claim
   board-frame coverage.
8. **Notes, agreed:** the desired/applied split, the ordering with the lights
   cut, the consumers (sleep, op 14, the bulb, the energy count), and
   applying 128 to a held ACTIVE in Lights out.
9. **Should-fix: comments.**
   - **Fixed** (`5df4e0e3b7`, `05a6f60`): `power.h`'s dim (a ceiling at 64),
     DRIFT's half saving, "the MCU never slept", `ladder.c`'s op 14 sub 1.
   - **Already done:** the documentation it listed as open was on the branch
     (`4d00aec`) after the commit it read.

**Results after the fixes:** `run.sh` passes against `5df4e0e3b7`, and the
mutants are 26 of 27 caught (all 9 new ones, plus the known survivor).

### What is left, handed over (10-20)

JD gave the go to build and flash, and asked for the rest to be handed to
the bench session (`ak820-pro-32`). The board checks are cut to what
matters:
1. **Merge order:**
   - the firmware branch into `ak820pro-jdlien` (a fast-forward from
     `8b06e4dcc4`);
   - the provenance `./build.sh dev` in the main clone;
   - the flash;
   - then `deps.lock` and the host branch into `main`.

   `run.sh` and `ladder_check.py` need the host branch, whose scripts look
   for the venv at the repo root.
2. **Validation 3:** `deep_cycles.py --n 20` on BT and `--n 10` on 2.4G on the
   exact artifact, relay 2 checked on first.
3. **One `ladder_check.py --rig`.**
4. **Not run:** the stage map and the per-mode real-time sleeps. R5 measures
   the timings in use.
5. **Then:** CLAUDE.md's state block, `current-status.md`, `battery.md`'s
   state note with the flash, and the lock-LED handler (`1261ab2fa8`'s)
   rebased onto the revision if it is to stay on the board.

## Codex's review (10-20) and what changed

Verbatim: [`review-codex-power-modes-revision-2026-10-20.md`](review-codex-power-modes-revision-2026-10-20.md),
prompt [`review-prompt-power-modes-revision.txt`](review-prompt-power-modes-revision.txt).
Its verdict: revise before implementation; keep the active-stage design and
the timings.

**Must-fix:**
1. **Drift's formula ignored the CIE curve.** Codex's counterexamples:
   - cap 128 at a user value of 200 reaches drive 54, against the cap's 48;
   - cap 64 at 150 reaches 19, against 12.

   **Taken:** the formula is dropped, and firmware step 4 offers two
   rendering choices, (c) recommended, for JD. The colour-path host test with
   the real CIE table is added.
2. **`count.c:138` depends on Balanced's 5-minute LIGHTS_OFF.** **Taken:** it
   is redesigned around SCREEN_OFF, with the level-4 cap in the dev-hooks
   build.
3. **Builds and tests default to the diagnostic checkout.** **Taken:** "Building and
   testing outside the main clone": the worktree, `BATTERY_SIM_SRC`,
   `--src`, and the provenance build in the main clone by agreement.
4. **The Saver bench conditions were undefined.** **Taken:**
   - a forced stage takes the mode's ceiling whatever the supply, so
     `ladder_check.py` runs on USB with Full as the reference;
   - `current_map.py` refuses at or below 15%;
   - every script reads again after a tick.
5. **The BT workaround was stale:** the regression follows the image.
   **Taken:** validation 3 runs the exact artifact on both transports before
   anything unattended, and release artifacts get their own runs.

**Should-fix:**
6. **The transition contract.** **Taken:** firmware steps 2-3 (`want_val`
   apart from `val_cap`, the trigger list), and the simulator's cases at 150
   and 151 ‰ and the unknown level.
7. **`HC_POWER` does not carry the cap.** **Taken:** corrected; only op 14
   does.
8. **The figures omitted the last 15%.** **Taken:** recomputed with it
   (Balanced at JD's use 12.6 days, not 11.1), and the above-15% figure kept
   as a note.
9. **"11.1 is an upper figure" did not follow.** **Taken:** reworded. The log
   understates the gap between old and new Balanced; it is not a bound.
10. **Unsupported causal claims.** **Taken:** "rounding costs nothing" and
    "half the chance of a wake" are gone.
11. **The worked example's brightness.** **Taken:** ~48 mWh an absence, 19 h
    asleep, ~15.3 days.
12. **Tests as promised were not runnable.** **Taken:**
    - `mutants.py` gains `power.c`, and `run.sh` gains a dev-hooks build and
      runs the mutants;
    - the ±15 s criterion is the script's;
    - `fnb_caps_test.py` needs JD's presses, so it is validation 5;
    - the animated effects get tolerances, and the Heatmap is stimulated;
    - the USB data's 7.1 mW is in the comparison.
13. **How R5 observes the low cap.** **Taken:** validation 6: the logger's
    step and the RAM log, with the renderer checked earlier.
14. **The documentation gaps:**
    - `power.md` lines 131, 234 and 397;
    - `battery.md:1315`;
    - `current_map.py:292`;
    - `count.c`;
    - `power.h:56`.

    **Taken:** all added to "Documentation".
15. **The optional features' constraints:** presence apart from physical
    input; bounds against renewal; clearing rules; the transport limits; one
    radio owner. **Taken:** all in "Optional, later".

**Notes, agreed:**
- the active ceiling folded into ACTIVE keeps the wire numbers and the
  ordering;
- `power_set_lights_cut()` keeps its direct `apply()`;
- no other ordinary effect bypasses the conversion, and the factory test
  does, by design;
- the ladders' values and Minimal's 60 s are sound, and `deep_cycles.py` is
  unaffected;
- the figures reproduce under the stated model;
- the simulator plus R5 is enough for the low cap's selector, with the
  renderer checked on the bench;
- README's figures do not move.
