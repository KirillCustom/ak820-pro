**The plan needs revision before implementation.** The basic ladder design is sound; the main problems are Drift’s proposed formula and gaps in the build/test procedure.

I reviewed only files inside the repository. Firmware references below refer to `ak820pro-jdlien` at `b2a07e0d37`, under `keyboards/a_jazz/ak820pro/`, unless otherwise specified. No files were changed or hardware tests run.

1. **Firmware design and consumers**

   **Note — `active_val` belongs in ACTIVE.** Keeping the existing enum avoids breaking wire values and the `saver > want` ordering. The intended composition is correct: on battery, choose the darker stage and the lower active ceiling; DIM remains 64. FULL must retain its explicit never-sleep exception. MINIMAL remains dark. The existing bulb and committed-frame energy accounting need no functional changes (`power.c:196`, `power.c:269`, `power.c:288`, `battery.c:672`, `battery.c:742`, `graphics/display.c:1861`).

   **Should-fix — make the transition contract and tests explicit.** [Plan:103](/Users/jdlien/code/ak820-pro/plans/POWER-MODES-REVISION-PLAN.md:103) should cover battery→USB and USB→battery while ACTIVE, persisted Saver at boot, low-cap entry **and exit**, `power_set_mode()`, and changes while ACTIVE is forced. Initialize the desired active ceiling to 255 outside the battery branch; a forced stage should not accidentally discard the battery-derived ceiling. Test exactly 150 versus 151 permille and unknown level.

   No mode-setting path bypasses the eventual `power_task()` check: Fn+B, initialization and the dev setter all change state that housekeeping subsequently consumes. However, `power_set_lights_cut()` already calls `apply()` directly (`power.c:153`), so “one path owns apply” should mean ownership of **mode-derived updates**, not literally its only caller.

   Keep the desired active ceiling distinct from the currently applied stage ceiling: comparing Saver’s 128 against DIM’s applied 64 would otherwise continually detect a change. Require one repaint for an effective cap change and none on subsequent unchanged ticks.

   **Should-fix — correct the protocol claim.** [Plan:131](/Users/jdlien/code/ak820-pro/plans/POWER-MODES-REVISION-PLAN.md:131) incorrectly says HC_POWER reports the value cap in `data[6]`. That byte is the **battery count state** (`hid_protocol.c:834`). Only devhook op 14 reports `power_val_cap()` there (`hid_protocol.c:1101`). Existing HC_POWER consumers remain compatible: [ak820battery.py:162](/Users/jdlien/code/ak820-pro/hostagent/ak820battery.py:162) interprets it correctly. Also, an op-14 mutation’s immediate reply can contain the old stage/cap until housekeeping runs; tests should read again after the tick.

2. **Drift and other LED paths**

   **Must-fix — the diagnosis is correct, but the proposed formula is not correct for this renderer.** [Plan:113](/Users/jdlien/code/ak820-pro/plans/POWER-MODES-REVISION-PLAN.md:113).

   Drift currently clips each HSV stop, converts it through **CIE**, interpolates RGB, then scales those RGB channels by the user’s value (`rgb_matrix_kb.inc:149`, `rgb_matrix_kb.inc:274`, `rgb_matrix_kb.inc:281`). The proposed formula treats these operations as linear HSV multiplication. They are not: `quantum/color.c:46` applies the CIE table before Drift’s final scale.

   Concrete counterexamples using the actual table and `scale8()`:

   | Cap | User value | Stop value | Proposed stop ceiling | Resulting maximum channel | Channel at capped HSV value |
   |---:|---:|---:|---:|---:|---:|
   | 128 | 200 | 150 | 163 | 54 | 48 |
   | 64 | 150 | 150 | 108 | 19 | 12 |
   | 64 | 64 | 150 | 255 | 17 | 12 |

   Thus the proposal can exceed both the active and dim ceilings in their existing CIE meaning.

   Resolve the rendering semantics explicitly: either apply user value and cap before HSV conversion, accepting a change to uncapped Drift’s appearance, or preserve the existing rendering and derive a CIE-aware bound. Add deterministic colour-path tests; the battery simulator’s HSV stub cannot catch this ([sim.c:72](/Users/jdlien/code/ak820-pro/scripts/battery_sim/sim.c:72)). Continuing to call the existing capped `rgb_matrix_hsv_to_rgb()` after computing a higher effective ceiling would also retain the original clipping bug.

   **Note — the arithmetic edges alone are otherwise straightforward.** Branch before dividing: `v <= cap` handles zero safely; `cap == 255` leaves the palette unrestricted. `v == cap` gives 255 algebraically, but the table above shows why that does not establish renderer correctness. Use a wide intermediate for `cap * 255`. QMK’s actual scale is `floor(x * (v + 1) / 256)`, so `/255` is not its exact inverse; even in a linear pipeline the proposed floor can underfill by one. Keying the palette on both cap and value is necessary.

   **Note — no additional ordinary enabled effect bypass was found.** Rainfall and Typing Heatmap scale HSV value before conversion; the other enabled effects use the conversion hook directly or through runners. The factory-test path does bypass it, writing raw channel value 20 (`quantum/rgb_matrix/rgb_matrix.c:247`): below Saver’s CIE drive ceiling, but above DIM’s 12. It is outside the ordinary selectable effects. The separate GPIO status indicators also bypass RGB conversion intentionally.

3. **Timings and overlooked dependencies**

   **Note — the proposed ladders are correct.** SCREEN_OFF wins equal thresholds because it is checked first (`power.c:178`). LIGHTS_OFF becomes naturally unreachable while remaining forceable. MINIMAL’s 60-second sleep introduces no conflict with sleep admission; the separate 30-second minimum for bench overrides should remain. `deep_cycles.py` is correctly described as unaffected.

   **Must-fix — add `battery_sim/count.c` to the change list.** [count.c:138](/Users/jdlien/code/ak820-pro/scripts/battery_sim/count.c:138) explicitly sets `idle_ms = 300000`, expects LIGHTS_OFF, then checks an hour’s energy at the level-4 backlight cap. New Balanced is still ACTIVE then, and never naturally reaches LIGHTS_OFF. Preserve this test by forcing that stage in a dev-capable harness, or redesign it around SCREEN_OFF and its different expected power.

   Within the already-listed `ladder.c`, update more than the obvious edge tables: `l_val_cap` at line 118, `l_lights_cut` at line 134, and Saver’s ACTIVE expectation in `l_minimal_enter` at line 202 also depend on old behavior. I found no further production timing dependency outside the listed power files.

4. **Modelled figures and reasoning**

   **Note — the runtime table is reproducible under its stated state-draw model.** Excluding the 5,791-second logging outage, the CSV contains about 48.68 hours, with active intervals occupying 11.47%. Using the documented rounded draws reproduces Balanced **13.21→11.07**, Saver **13.66→21.19**, and Lights out **26.80→26.64 days**. The office and stop-start results agree to rounding. Document the outage exclusion so the calculation is reproducible.

   **Should-fix — distinguish fixed-policy estimates from complete-charge predictions.** [Plan:62](/Users/jdlien/code/ak820-pro/plans/POWER-MODES-REVISION-PLAN.md:62) omits the final low-battery period, when Balanced takes Saver’s stages and active cap. A simple 85%/15% energy split gives approximately **12.6 days**, rather than 11.1, for new Balanced under the same repeated activity model. FULL’s quoted 2.2 days similarly ignores its low-battery lighting reduction. Either incorporate that transition or label the figures as extrapolations of the above-15% policy.

   **Should-fix — “11.1 days is an upper figure” does not follow.** [Plan:73](/Users/jdlien/code/ak820-pro/plans/POWER-MODES-REVISION-PLAN.md:73). Treating every interval containing a key as fully active overestimates energy within those intervals. Missing short pauses can understate the **difference between old and new Balanced**, but does not establish an upper bound on new Balanced’s absolute runtime.

   **Should-fix — remove unsupported causal claims.** [Plan:18](/Users/jdlien/code/ak820-pro/plans/POWER-MODES-REVISION-PLAN.md:18): absence cost depends on duration until the ladder completes; rounding is not inherently unmeasurable. The roughly **50–51 mWh extra per long absence** is reasonable. [Plan:45](/Users/jdlien/code/ak820-pro/plans/POWER-MODES-REVISION-PLAN.md:45)’s additional **0.8 mWh** for MINIMAL is also correct, but doubling the timeout does not establish “half the chance” of a wake without a pause-duration distribution.

   **Should-fix — the worked-example update uses the wrong brightness.** [Plan:184](/Users/jdlien/code/ak820-pro/plans/POWER-MODES-REVISION-PLAN.md:184) proposes ~70 mWh per absence, appropriate to full white. The example in [power.md:227](/Users/jdlien/code/ak820-pro/docs/power.md:227) uses **white at 128, screen level 8**: ten minutes active plus ten dim costs approximately **48 mWh**. Recompute its entire total, and subtract the ladder time from asleep hours; four typing hours plus three 20-minute ladders leaves **19**, not 20, asleep.

5. **Validation and flashing**

   **Must-fix — explicitly route builds and tests to the implementation worktree.** The commands at [plan:223](/Users/jdlien/code/ak820-pro/plans/POWER-MODES-REVISION-PLAN.md:223) currently default to the diagnostic checkout. [run.sh:8](/Users/jdlien/code/ak820-pro/scripts/battery_sim/run.sh:8) needs `BATTERY_SIM_SRC`; mutants need that variable or `--src`. [build.sh:45](/Users/jdlien/code/ak820-pro/build.sh:45) sources [env.sh:8](/Users/jdlien/code/ak820-pro/env.sh:8), which unconditionally resets `QMK_HOME`. Merely invoking it from the worktree or setting `QMK_HOME` beforehand does not fix this. The VIA checker also hardcodes the main clone ([check_via_sync.py:13](/Users/jdlien/code/ak820-pro/scripts/check_via_sync.py:13)).

   **Must-fix — define the Saver bench conditions.** [ladder_check.py:15](/Users/jdlien/code/ak820-pro/scripts/ladder_check.py:15) currently runs on USB power. Under the proposed battery-only rule, forcing ACTIVE there must still give 255. Run the Saver comparison on battery, verify the supply and op-14 cap, and explicitly set FULL for the uncapped reference. Require level above 150 permille: forcing ACTIVE no longer defeats the low-battery active ceiling. The existing claim that forcing ACTIVE prevents all low-cap interference in [current_map.py:292](/Users/jdlien/code/ak820-pro/scripts/current_map.py:292) becomes false.

   **Must-fix — replace the BT-workaround assumption.** [Plan:228](/Users/jdlien/code/ak820-pro/plans/POWER-MODES-REVISION-PLAN.md:228) is stale. The [regression investigation:106](/Users/jdlien/code/ak820-pro/plans/DEEP-SLEEP-REGRESSION-2026-10-20.md:106) identifies an image-dependent failure, not an established 2.4G-only failure; BT was not demonstrated safe on the failing image. Require sleep/wake checks on the exact artifact and intended transport before unattended R5. A passing dev artifact does not validate differently linked daily/fpb artifacts.

   **Should-fix — make the promised tests executable and meaningful.** `mutants.py` currently mutates only `battery.c` ([line 126](/Users/jdlien/code/ak820-pro/scripts/battery_sim/mutants.py:126)); extend it to target `power.c`. `run.sh` does not invoke mutants, and does not enable dev hooks for forced-stage/set-mode tests. Register new scenarios in both the dispatcher and run list.

   The bench criteria also need adjustment: `ladder_sleep.py` currently accepts **±15 seconds**, not ±1 ([line 169](/Users/jdlien/code/ak820-pro/scripts/ladder_sleep.py:169)); `fnb_caps_test.py` requires human Fn+B presses and checks stalls, not cap repainting ([line 102](/Users/jdlien/code/ak820-pro/scripts/fnb_caps_test.py:102)). Animated effects cannot reliably produce identical independent drive samples: reinitialization resets animation/history and Jellybean/Rainfall use randomness. Use deterministic renderer checks or suitable sampling/tolerances, and stimulate Heatmap so a zero-output pass is meaningful. Also account for USB data’s additional 7.1 mW when comparing the stage map with the untethered ~154 mW figure.

   **Note — simulator coverage plus later R5 is acceptable for the low-battery selector**, provided the transition tests above pass and real Saver rendering is checked before R5. A gauge override is unnecessary for this change. However, specify how R5 will observe the claimed cap/bulb/three-minute transition: existing HC_POWER/`bench_trial.py` logs do not record the cap or bulb, and live USB data blocks deep sleep. R5 cannot substitute for the missing renderer tests.

6. **Documentation sweep**

   **Should-fix — add these specifically uncovered updates.** I ran the timing sweep and broader mode/runtime searches across all requested locations.

   | Location | Uncovered issue |
   |---|---|
   | [docs/power.md:131](/Users/jdlien/code/ak820-pro/docs/power.md:131) | “Well under 1% a day” for ladder overhead needs qualification; repeated new Balanced breaks can greatly exceed it. |
   | [docs/power.md:234](/Users/jdlien/code/ak820-pro/docs/power.md:234) | Recompute asleep hours and the example’s final runtime, not just its ladder line. |
   | [docs/power.md:397](/Users/jdlien/code/ak820-pro/docs/power.md:397) | Another “probably about two weeks” R5 forecast, outside the listed short-version update. |
   | [docs/battery.md:1315](/Users/jdlien/code/ak820-pro/docs/battery.md:1315) | “Restores everything” needs to mean the selected mode’s active state; describe cap-change repainting as well as stage changes. |
   | [scripts/current_map.py:292](/Users/jdlien/code/ak820-pro/scripts/current_map.py:292) | Forced-ACTIVE claim no longer excludes the low cap. |
   | [scripts/battery_sim/count.c:138](/Users/jdlien/code/ak820-pro/scripts/battery_sim/count.c:138) | Unlisted old-timing dependency described above. |
   | Firmware `power.h:56` | Accessor comment describes only the dim ceiling; add the active ceiling. |

   **Note — the remaining relevant timing hits are covered or historical.** The dated CLAUDE/current-status measurements should remain dated records. The bench’s 30-second override, clock polling periods, protection delays and unrelated numeric matches should remain unchanged. I found no additional mode-timing/runtime statement in `ak820-agent/*.txt` or host-agent code requiring revision. README’s measured-hours and standby figures do not change solely because of these ladders.

7. **Optional host presence/sleep hints**

   **Should-fix before implementing that later feature — separate physical inactivity from host presence.** [Plan:266](/Users/jdlien/code/ak820-pro/plans/POWER-MODES-REVISION-PLAN.md:266). Updating QMK’s existing activity timestamp would postpone sleep too. Merely leaving `power_sleep_wanted()` unchanged is insufficient: a presence hint holding RGB/backlight on would trip `sleep.c:261`’s blockers. At the physical sleep deadline, sleep policy must override presence and make both dark.

   **Should-fix — TTL alone does not bound a continuously renewing sender.** [Plan:286](/Users/jdlien/code/ak820-pro/plans/POWER-MODES-REVISION-PLAN.md:286). FULL never sleeps, so the proposed sleep bound does not cover it. Define mode precedence, renewal limits/rate limits, and handling of repeated sleep hints. Physical input should cancel stale sleep requests; clear pending hints on wake, transport/slot changes and disconnects. Preserve ordinary sleep blockers rather than calling deep sleep directly.

   **Should-fix — add the known transport limitations.** The lock-LED plan records that **macOS refuses writes once locked** ([line 38](/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:38)), Windows BT is untested, and the channel is one-way. Lock/suspend notifications therefore offer best-effort delivery, not guaranteed immediate sleep. Raw81 also needs a deliberately added command/allow-list entry (`bluetooth/raw81.c:202`).

   Keep the daemon’s existing single transaction owner; an OS sleep callback must enqueue work, not start an overlapping transaction. macOS uses flock, while Windows uses its existing byte-range lock. The lock-LED sender additionally needs whole-frame serialization and cancellation around real lock changes.

   **Note — the no-radio-wake constraint is correct.** The board cannot receive presence while deeply asleep. Charger wake specifically needs a CHRG edge; plugging in a charger with a full pack need not generate one.

**Verdict: revise before implementation.** Keep the active-stage design and proposed timings. Correct Drift’s rendering math, include the missed simulator dependency, and make worktree provenance and bench validation explicit before treating the plan as ready.