**I would fix one remaining firmware must-fix before flashing `383a5ed578`.** The reported reproductions are fixed, but “0% only at the cut” still fails across a USB-to-battery transition. The dispositions also need qualifications around clamp persistence and simulator coverage.

I made no changes. I inspected the source and commits, checked arithmetic and the host parser in memory, and verified both archived builds’ hashes. I did **not** rerun `battery_sim`: its runner creates temporary files and binaries.

1. **Must-fix — A known level carried from USB bypasses the battery floor.**

   A concrete path remains:

   - Boot on idle USB with `5C=30`: 3424 mV → **4 pm**, adopted directly.
   - Unplug without a qualified charging session: no re-seat is owed, and the supply transition does not normalize the level.
   - On battery, the level is already known, so the first-adoption floor is skipped. It lies inside the voltage band, and the counter only decrements values **above** 10 pm.
   - The display therefore remains **0% above the cut**, potentially indefinitely.

   The relevant paths are [battery.c:1126](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/battery.c:1126), [battery.c:1141](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/battery.c:1141), [battery.c:1080](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/battery.c:1080), and [battery.c:701](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/battery.c:701).

   Similarly, clearing a cut through brief external power can leave a known zero behind; a low charging-model level can also enter relaxation below the floor. Normalize **every known level on entry to battery operation**, preserving UNKNOWN and giving the cut latch precedence. Add USB→battery regressions; `c_floor_boot` covers only a direct battery boot.

   **The latch half of the fix is correct:** `level_report()` checks it before re-seating, `energy_tick()` enforces zero, `adopt_relaxed()` respects it on battery, and ETA checks the latch directly. I found no remaining battery-side adoption that overrides an active latch. [battery.c:1053](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/battery.c:1053), [battery.c:683](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/battery.c:683), [battery.c:1032](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/battery.c:1032), [battery.c:739](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/battery.c:739).

2. **Should-fix — Clamp persistence still assumes the old 90–100% range.**

   Removing the running floor is correct: a clamped count can now cross 905 and continue toward the ordinary 10 pm floor without discarding its consumption. A known clamped re-seat is also correctly **kept, not lifted**. [battery.c:690](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/battery.c:690), [battery.c:1021](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/battery.c:1021).

   Boot/persistence was not updated:

   | Count before saving | Saved value | Restored at a clamped boot |
   |---:|---:|---:|
   | 905 | 900 | 900 |
   | 899 | 890 marker | **950** |
   | 800 | 890 marker | **950** |

   Thus a legitimate light-load count below 90% can jump to 95% after a slider-induced reboot while still clamped. The comments that the clamp means “90..100” and that the fallback occurs only after erased history are now misleading. [battery.c:461](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/battery.c:461), [battery.c:493](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/battery.c:493), [battery.c:504](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/battery.c:504).

   This is outside the explicitly excluded reboot-accuracy target, so I would not make it a dev-flash blocker. It does need an explicit persistence policy and regression. It does **not** introduce frequent saves below 90%: the marker stays constant, and the setter ignores unchanged values. [kb_eeconfig.c:125](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/kb_eeconfig.c:125).

3. **Should-fix — The RGB restart works, but exposes two smaller edge cases.**

   **The core mechanism is correct.** `STARTING` resets `iter`; invalidating `rgb_last_effect` keeps `params->init` true throughout all render chunks, because the last-effect tracker changes only at flush. Jellybean therefore repaints every eligible LED. Solid, Alphas Mods, the three Cycle effects, Heatmap, RAINFALL and DRIFT also cover their output across the frame. Restarting mid-render discards the partial render and begins again before committing it. It does not change configuration or EEPROM. [rgb_matrix.c:319](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/quantum/rgb_matrix/rgb_matrix.c:319), [rgb_matrix.c:344](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/quantum/rgb_matrix/rgb_matrix.c:344), [rgb_matrix.c:419](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/quantum/rgb_matrix/rgb_matrix.c:419), [jellybean_raindrops_anim.h:22](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/quantum/rgb_matrix/animations/jellybean_raindrops_anim.h:22).

   Two qualifications:

   - **Custom global initialization repeats per chunk.** RAINFALL clears its entire brightness array and countdown on every init chunk, erasing the drop seeded during chunk zero. DRIFT resets coordinates and invalidates/rebuilds its nine-color palette on every chunk; only chunk zero advances coordinates. Both still repaint and obey darkness, but their first frame is inconsistent and DRIFT performs redundant conversions. Gate those global resets on `params->init && params->iter == 0`; keep `init` true for the complete frame. This was already present on ordinary effect entry, but now happens at cap changes too. [rgb_matrix_kb.inc:34](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rgb_matrix_kb.inc:34), [rgb_matrix_kb.inc:200](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rgb_matrix_kb.inc:200).
   - **A rapid disable→cap-change→enable can miss invalidation.** `apply()` skips the hook while disabled. If re-enabled before the NONE frame flushes, the previous effect/enable trackers can still match, leaving Jellybean without an init pass. Calling the invalidation hook on every cap change, including while disabled, closes this edge. [power.c:116](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/power.c:116), [rgb_matrix.c:586](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/quantum/rgb_matrix/rgb_matrix.c:586).

   Normal settled-disabled operation is fine. Suspension selects NONE, clears the output, and effect restoration triggers initialization. Darkness takes effect at the completed render/flush, rather than synchronously inside `apply()`.

4. **Should-fix — Validation coverage is improved, but narrower than the disposition suggests.**

   The per-second hook is correctly installed and called after every ten simulated ticks. This fixes the original endpoint-only scoring defect. The three orderings preserve the reconstructed report multiset. [count.c:318](/Users/jdlien/code/ak820-pro/scripts/battery_sim/count.c:318), [count.c:349](/Users/jdlien/code/ak820-pro/scripts/battery_sim/count.c:349), [sim.c:975](/Users/jdlien/code/ak820-pro/scripts/battery_sim/sim.c:975).

   Remaining qualifications:

   - The ramp’s assertion allows **4 points**, while the plan says no second outside 3. Those reported results might be true, but the runner does not enforce that claim for the ramp. [count.c:372](/Users/jdlien/code/ak820-pro/scripts/battery_sim/count.c:372), [plan:299](/Users/jdlien/code/ak820-pro/plans/PHASE2-BUILD-PLAN.md:299).
   - A fixed-seed shuffle does not cover **correlated bursts**. Reports remain evenly timed; all three orderings share one synthesized distribution. [sim.c:981](/Users/jdlien/code/ak820-pro/scripts/battery_sim/sim.c:981), [sim.c:995](/Users/jdlien/code/ak820-pro/scripts/battery_sim/sim.c:995).
   - R3’s input ends at **02:00**, approximately 83 seconds before its configured reference cut. Its final approach/cut is not replayed. The 2e input ends at **09:42**, before the reboot and remaining discharge, as now acknowledged. [R3 CSV:312](/Users/jdlien/code/ak820-pro/history/battery-2026-10-15-drain-2f/log-20261018-0205-rgbcut.csv:312), [2e CSV:374](/Users/jdlien/code/ak820-pro/history/battery-2026-10-12-drain-2e/log-20261014-0950-preswap.csv:374).
   - The simulator cannot validate fractional lit-time accounting: its stub supplies either the whole interval or zero lit milliseconds. Its `display_backlight_effective()` stub also ignores the cap, so it does not faithfully exercise the new screen-visibility condition. [sim.c:64](/Users/jdlien/code/ak820-pro/scripts/battery_sim/sim.c:64), [sim.c:83](/Users/jdlien/code/ak820-pro/scripts/battery_sim/sim.c:83).

5. **Should-fix — Resuming an old trial CSV silently changes its row schema.**

   The new power fields parse correctly, but `Log` writes a header only for a nonexistent file. Reusing an existing pre-change `--out` appends the longer rows under the old header, mislabelling columns. Validate the existing header and reject or rotate incompatible files. [bench_trial.py:43](/Users/jdlien/code/ak820-pro/scripts/bench_trial.py:43), [bench_trial.py:97](/Users/jdlien/code/ak820-pro/scripts/bench_trial.py:97).

The remaining fixes are sound:

| Finding | Assessment |
|---|---|
| **#2 — upper band** | **Fixed.** `V+25 >= 4036` returns 1000, including equality and values beyond the knot. Both counting and `band_clamp()` use it. The widened comparison occurs before narrowing. [battery.c:606](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/battery.c:606) |
| **#4 — stale hold** | **Fixed.** Missing/stale estimates clear the hold before deciding whether to discard count energy. Physical energy remains counted. [battery.c:675](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/battery.c:675) |
| **#5 — idle USB** | **Fixed for the intended policy.** Unknown boot levels still adopt the curve; known levels move only toward a violated band edge. Modeled pauses remain held, handover adoption still runs once, and FULL retains precedence. No new stuck charging state found. The battery-entry floor omission above remains. [battery.c:1104](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/battery.c:1104) |
| **#7 — RGB intercept** | **Fixed.** The driver integrates lit milliseconds over committed-frame intervals and clears them with the drive accumulator. `model_nj()` charges the intercept only for that duration. Main-thread ownership and unsigned timer subtraction remain appropriate. [sn32f2xx.c:855](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/drivers/led/sn32f2xx.c:855), [battery.c:645](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/battery.c:645) |
| **#8 — low notice** | **Fixed.** Session-count changes re-arm it, including charges remaining below 20%. USB clears an owed notice before delivery. The real effective-backlight accessor accounts for cap, brightness zero and display power. Counter wrap is harmless with the inequality comparison. [battery.c:244](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/battery.c:244), [power.c:181](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/power.c:181), [display.c:267](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:267) |
| **#9 — tools** | Trial power parsing, stage-summary iteration, settled LCD read and forced stages are fixed. Keeping the guard health-only is an explicit scope change, not implementation of the earlier promise to add power rows there. [bench_trial.py:73](/Users/jdlien/code/ak820-pro/scripts/bench_trial.py:73), [current_map.py:288](/Users/jdlien/code/ak820-pro/scripts/current_map.py:288), [current_map.py:303](/Users/jdlien/code/ak820-pro/scripts/current_map.py:303), [current_map.py:347](/Users/jdlien/code/ak820-pro/scripts/current_map.py:347) |
| **#10 — binding/restore** | Both default Fn+B positions contain `PWR_MODE`; restore compares the complete keymap and encoders read back from the device. **Source fix is correct.** I cannot independently confirm the claimed prior board-side binding verification from this read-only review. [keymap.c:45](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/keymaps/via/keymap.c:45), [keymap.c:61](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/keymaps/via/keymap.c:61), [ak820keymap.py:163](/Users/jdlien/code/ak820-pro/hostagent/ak820keymap.py:163) |

The new simulator regressions generally test their named bugs:

| Test | What it establishes |
|---|---|
| `c_band_top` | Detects the erroneous 915 ceiling at `5C=98` through an independently expected consumption rate. Add an exact-boundary case for 4011 mV. [count.c:234](/Users/jdlien/code/ak820-pro/scripts/battery_sim/count.c:234) |
| `c_floor_boot` | Correctly tests the direct battery-boot floor; misses inherited USB levels. [count.c:248](/Users/jdlien/code/ak820-pro/scripts/battery_sim/count.c:248) |
| `c_cut_reseat` | Detects the original persistent post-cut lift. Endpoint assertions do not establish that no transient lift occurred between reports and energy ticks. [count.c:257](/Users/jdlien/code/ak820-pro/scripts/battery_sim/count.c:257) |
| `c_hold_stale` | Detects the old indefinitely frozen hold. Its lower-only allowance is loose: roughly 3.75 pm suffices where about 9.75 pm is expected. [count.c:275](/Users/jdlien/code/ak820-pro/scripts/battery_sim/count.c:275) |
| `c_handover_stays` | Exercises subsequent idle reports for an hour and detects walking toward the curve. It does not independently verify the first adoption’s value. [count.c:290](/Users/jdlien/code/ak820-pro/scripts/battery_sim/count.c:290) |

The rewritten expectations follow the design:

- **Discharge:** 5.3 hours at 303.8 mW consumes approximately **103.35 pm**, leaving **896.65 pm**. Expecting about 897 is justified. [sim.c:244](/Users/jdlien/code/ak820-pro/scripts/battery_sim/sim.c:244)
- **Relaxation drop:** 1790 seconds consumes **9.696 pm**. The new 10±1 assertion is materially better. [model.c:750](/Users/jdlien/code/ak820-pro/scripts/battery_sim/model.c:750)
- **`m_reseat_clamp`:** keeping the counted level follows directly from removing clamp bounds. [model.c:784](/Users/jdlien/code/ak820-pro/scripts/battery_sim/model.c:784)
- **`m_pause_relaxing`:** the settled 400-worth band contains the held level; the earlier 600-worth band would lift it. The changed expectation is justified. [model.c:608](/Users/jdlien/code/ak820-pro/scripts/battery_sim/model.c:608)
- **`m_pause_missing`:** the expected band result is justified, but **the test can pass without adoption occurring**, because the held number already lies inside that band. Resume charging and check that an adopted MODEL start is available, or return reports whose band requires a visible move. [model.c:629](/Users/jdlien/code/ak820-pro/scripts/battery_sim/model.c:629)

**Note — Cost and build readiness.** I found no new unbounded main-loop work or ordinary-range integer overflow. The lit-time addition is small; the RGB work remains chunked, with the redundant custom initialization noted above. The archived dev and daily BIN/ELF hashes match their clean-commit manifests. The dev ELF has **2,280 bytes of heap space**, with separate **1,024-byte ISR and 2,048-byte process stacks**; the disposition’s 2,288-byte figure belongs to the earlier build. [dev manifest:5](/Users/jdlien/code/ak820-pro/ak820pro-builds/out/via-dev-383a5ed578-20261018-163901.json:5)

After fixing the battery-entry floor, this is a reasonable **dev build for the planned board checks**: every effect through transitions, stage power measurements, wake/key-loss testing, stack watermarks and deferred writes, followed by R5. Those checks remain necessary; the replay does not establish accuracy under Normal’s changing load. [plan:233](/Users/jdlien/code/ak820-pro/plans/PHASE2-BUILD-PLAN.md:233)