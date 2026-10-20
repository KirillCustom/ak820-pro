**The firmware implementation looks correct, including DRIFT option (c), but I would fix the colour harness before flashing and the bench checks before accepting validation 4.** I found no firmware defect requiring a change to `0e322097f4`.

I reviewed both branches without checking out or changing files. References below use branch line numbers; firmware paths are relative to `qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/`. I did not rerun the compiled suite or operate hardware.

1. **Must-fix — the colour harness uses the wrong `scale8()` formula.**

   `scripts/battery_sim/colour.c:19` says `FASTLED_SCALE8_FIXED` is unset, and line 23 implements `i * s >> 8`. The board’s RGB-matrix configuration enables lib8tion at QMK `builddefs/common_features.mk:479`, which adds `-DFASTLED_SCALE8_FIXED=1` at line 743. Consequently, `lib/lib8tion/scale8.h:24` uses:

   ```c
   ((uint16_t)i * (1 + (uint16_t)s)) >> 8
   ```

   For example, `scale8(255,255)` is **255 on the board, 254 in the test**. This affects DRIFT’s saturation, noise bias and final brightness, and RAINFALL’s drop values. Comparing two executions using the same incorrect stub can still pass.

   Correct the stub or use the actual configured helper, then rerun the colour scenarios and DRIFT mutants. This is a validation defect, not evidence that the firmware’s cap formula is wrong.

2. **Must-fix before bench acceptance — all-zero lighting passes every bench check.**

   `scripts/ladder_check.py:121`, `:149`, `:155`, and `:165` permit zero reference and result readings. I executed the branch’s check functions against an in-memory mock returning correct op-14 metadata and zero drive everywhere: **all 28 result rows passed, including with the rig marked present**.

   Thus a failed renderer, ineffective Heatmap stimulation, or stuck-zero telemetry can produce “all pass.” Require positive reference drive for effects expected to light, a meaningful white-reference magnitude, and positive stimulated Heatmap activity. Without `--rig`, report Heatmap as incomplete rather than treating it as a completed validation.

3. **Must-fix before bench use — cleanup does not guarantee independent restoration attempts.**

   At `scripts/ladder_check.py:199`, failure to release the relay prevents closing its handle and skips all keyboard restoration. At `:204`, failure to release the forced stage prevents restoring RGB and the persisted mode. Similarly, `scripts/current_map.py:358` performs RGB, LCD, stage and mode restoration sequentially: one failure skips the remaining keyboard steps.

   Save the initial `forced` value as well: both scripts currently release it unconditionally instead of restoring an existing hold (`ladder_check.py:179`, `:204`; `current_map.py:280`, `:361`).

   Make cleanup steps independent, retry opening the board where appropriate, and report incomplete restoration as failure. The existing pattern in `scripts/deep_sleep.py:196` and `scripts/sleep_baseline.py:172` already supports this.

   **The normal `--rig` handle usage is correct:** `ladder_check.py:192` opens one relay handle, and `:195` passes it directly to `sleep_baseline.press()`. There is no competing relay subprocess during the holds.

4. **Must-fix before accepting the current map — verify the state being measured throughout the run.**

   `scripts/current_map.py:310` checks the level only once and permits an unread/unknown result. A run starting just above 150‰ can cross the threshold during its approximately 39-minute default stage map. Subsequent forced ACTIVE references then become capped.

   More importantly, the new `power_state()` at `:185` is never called by this script. After setting mode and stage at `:330`, it waits but never verifies their applied values. An old image can therefore produce an uncapped “jd saver” row while its measured draw still agrees with its own model.

   For the revised stage validation, require working dev hooks and a known level above 150‰; read and record mode, forced stage, applied stage and cap after settling; recheck eligibility during the run. A model comparison alone cannot establish that the intended load was selected.

5. **Should-fix — the animated sampling does not justify the sharper thresholds yet.**

   `scripts/ladder_check.py:93` sleeps once, then takes five readings without spacing them. The underlying drive statistic updates once per second (`battery.c:735`), so those calls can repeatedly sample the same interval.

   The 0.35 and 0.70 thresholds are reasonable targets for comparable animation averages, but independent short windows need not be comparable. For example, saturated Cycle All can move between a primary colour and a two-channel colour: QMK gives channel sums of 255 for full red and 95 for capped yellow, a ratio of approximately **0.373**, exceeding 0.35 despite correct capping. The value-100 equality check has the same phase problem.

   Space samples across distinct updates, choose a documented speed/window, and establish tolerances from sufficiently long or matched measurements. Also reconcile the stage pass’s fixed 20% tolerance at `:122` with the docstring’s claim that animated checks use `ANIM_TOL`.

6. **Should-fix — use compatible colour types across translation units.**

   `scripts/battery_sim/stubs/quantum.h:37` declares anonymous, unpacked structs. QMK `quantum/color.h:77` and `:87` declare tagged, packed `struct rgb_t` and `struct hsv_t`.

   Their three-byte layouts coincide on the current host, which explains why the reported build can work, but the declarations are not compatible C types across translation units. Use QMK’s actual declarations for the real-colour build. Layout equality alone is insufficient assurance for the `hsv_to_rgb()` call boundary.

7. **Should-fix — add independent USB-baseline and warm-cache checks.**

   `scripts/battery_sim/colour.c:120` initializes DRIFT afresh for every tested value. At `:138`, capped and uncapped results both come from the revised renderer. This checks the cap identity well, but does not establish unchanged USB rendering or cache correctness across changes without initialization.

   A mutation that removes hue rotation, for example, can affect both sides identically and pass this comparison. Removing a cache-key comparison can also escape because initialization invalidates the palette first.

   Add an independent baseline for uncapped rendering, plus changes of hue, saturation, value and cap while the cache is warm. Cover representative multi-chunk frames too. For power transitions, add explicit tests for releasing forced Saver ACTIVE on USB, the chosen low-battery Minimal forced-ACTIVE behavior, and lights-cut calls surrounding a ceiling change (`scripts/battery_sim/ladder.c:175`, `:496`).

8. **Note — the firmware’s desired/applied ceiling separation and ordering are sound.**

   `power.c:208` starts uncapped; `:215` selects the battery mode’s ceiling; `:219` applies the low ceiling; and `:240` supplies the forced stage’s mode ceiling on USB. The change check at `:248` compares against `want_val`, not DIM’s applied `val_cap`.

   `apply()` at `:117` uses the desired ceiling only for ACTIVE, preserves DIM at 64, and repaints only when the applied ceiling or driver scale changes. Changing the desired ACTIVE ceiling under DIM therefore causes no repaint or repeated application.

   `power_set_lights_cut()` at `:169` correctly retains its direct `apply()`. Battery housekeeping runs before `power_task()` (`ak820pro.c:595`, `:605`), so a direct application using the preceding desired ceiling is reconciled during the same housekeeping pass.

   The downstream consumers remain correct:

   - Sleep checks actual darkness through `power_rgb_dark()`, independently of the cap (`sleep.c:261`).
   - Op 14 reports the applied cap, potentially one tick after mutation (`hid_protocol.c:1098`). `Bench.state()` correctly waits before rereading.
   - The bulb continues to reflect Saver under the low cap (`power.c:316`; `graphics/display.c:1861`).
   - Battery accounting uses committed RGB drive and effective backlight duty, so DRIFT’s different savings are counted correctly (`battery.c:672`, `:742`).

   Applying 128 to Minimal’s desired ACTIVE ceiling is an acceptable deliberate departure: ordinary Minimal remains SCREEN_OFF from zero idle. It changes the forced-ACTIVE diagnostic case only. Always on correctly acquires the low ceiling and Saver lighting stages while retaining its never-sleep exception (`power.c:298`).

9. **Note — DRIFT option (c) is implemented correctly; other selectable effects remain capped.**

   At `rgb_matrix_kb.inc:150`, plain `hsv_to_rgb()` still applies CIE. With the old USB cap of 255, none of the palette stops was clipped, so this produces **exactly the previous USB palette**.

   At `:239`, `min(user_value, power_val_cap())` precedes the unchanged final scaling. This implements the chosen rule at every value, including zero and equality with the cap. Removing the cap from the palette key is correct because the palette no longer depends on it. Initialization still invalidates the palette and resets the field (`:203`), as before.

   RAINFALL still converts its scaled drop value through the capped hook (`:63`). Solid, Alphas/Mods, the cycles, Jellybean and Heatmap likewise retain that hook. The factory-test raw RGB path remains the documented exception.

10. **Note — the principal simulator scenarios are meaningful, with narrower coverage than some comments suggest.**

    The boundary checks, persisted-Saver boot, cable transitions, 150/151‰ entry, unknown level, held USB ceiling and desired-versus-applied DIM checks contain concrete assertions. The count scenarios correctly distinguish SCREEN_OFF’s 106.9 mW from forced LIGHTS_OFF’s level-4 backlight cost (`scripts/battery_sim/count.c:138`, `:155`).

    `k_hook` exhaustively checks HSV inputs and the real CIE table. The synthetic DRIFT grid and six frames reach all eight ramp cells; its positive-output assertion prevents an entirely black comparison. However, this is an **82-point synthetic grid**, while the board has 81 mapped LEDs (`config.h:32`), and it does not reproduce board chunking. That is acceptable for the algebraic identity test, but should not be described as exact board-frame coverage.

    RAINFALL checks its stated upper bound at nine selected values, not exhaustive unchanged-below-cap behavior (`colour.c:165`). The sanitizer build in `run.sh:61` also excludes the separate dev-only and colour scenarios.

    The eight new mutants target useful regressions. Keeping the independently established battery mutant as a visibly reported known survivor is acceptable (`mutants.py:117`); describe the result as “all eight new mutants caught,” not “every mutant caught.”

11. **Should-fix with the flash record — finish documentation and correct misleading comments.**

    The old timings remain in `docs/power.md:244`, `docs/battery.md:1298`, `docs/hardware.md:453`, `docs/display.md:472`, and `docs/test-bench.md:552`. The old Saver runtime comparison remains at `docs/power.md:164`. This is consistent with the plan’s explicit flash-record sequencing, but remains unfinished work.

    Firmware comments also need small corrections: `power.h:16` says DIM quarters the user’s value rather than imposing a ceiling; `power.c:55` makes the quarter-power statement without DRIFT’s exception; `power.c:10` says the MCU never sleeps. `scripts/battery_sim/ladder.c:519` incorrectly calls mode-setting op 14 sub **0**, rather than sub **1**.

    I found no additional executable dependency on the old ladder timings. `ladder_sleep.py:37` has the correct 1200/180/60-second values, its deadline scales correctly, and its ±15-second criterion remains intact. Run the Balanced timing check above 150‰; below that, a healthy board should sleep at 180 seconds. `deep_cycles.py` correctly retains its separate bench timeout.

    Keeping the host changes on their branch is appropriate. The new tests intentionally require the matching firmware sources; `run.sh:8` still needs the explicit source override until the firmware checkout changes.

**Verdict: fix the colour harness, then proceed to validation 3 on the exact provenance-named dev artifact: 20 BT cycles and 10 on 2.4G. Fix the bench false-pass, restoration and state-verification issues before accepting validation 4. The firmware itself appears ready; the current host results do not yet justify an unconditional go-ahead through the remaining validation.**