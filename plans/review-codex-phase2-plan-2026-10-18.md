I would revise the plan before building. **The energy-gauge approach is sound, and I would retain “never rises on battery” for the first build.** The main blockers are the voltage-band calculation, RGB calibration and cap coverage, the 0% boundary, and incomplete validation.

I reproduced A7’s nominal results: R3 −0.1/+0.6 points, drain 2e +0.0/+0.8. Those results support the calibrated, steady-load case—not yet the proposed implementation under the ladder.

1. **Must-fix — The symmetric voltage band does not represent ±12 mV through this nonlinear curve.**

   At 4000 mV, the proposed band’s upper edge is **79.0%**, but `curve(4012)` is **81.7%**. It can therefore ratchet down a correct count even when the voltage error is within the stated allowance.

   Use the transformed endpoints:
   ```
   lower = max(0, curve(V − 12) − 15)
   upper = min(1000, curve(V + 12) + 15)
   ```
   Handle the top clamp separately: where the uncertainty reaches beyond the measured curve, its saturated 900-pm return is not an upper bound. The existing “at least approximately 90%” anchor is reasonable for the calibrated load, but needs checking under reduced load.

   Also, ±12 mV is an empirical allowance from sparse meter comparisons, not a demonstrated bound covering load sag, relaxation, temperature and every short-term estimate. See [plan:66](/Users/jdlien/code/ak820-pro/plans/PHASE2-BUILD-PLAN.md:66), [battery.c:426](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/battery.c:426), and [battery.md:171](/Users/jdlien/code/ak820-pro/docs/battery.md:171).

2. **Must-fix — “0% stays the RGB cut” needs an explicit implementation rule.**

   The band allows its lower edge to become negative around 3480 mV, so an overcounting model can reach zero before the 3400-mV protection threshold. Conversely, at 3400 mV its upper edge remains about 1.6%, allowing a positive level after the cut.

   Today, ordinary `cut_lights()` does **not** set the level to zero; only the critical-zero path does. Leaving protection unchanged therefore does not enforce the proposed endpoint. See [battery.c:977](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/battery.c:977).

   Specify that voltage protection remains authoritative, the latched cut establishes zero, and model exhaustion before that has a defined display policy—such as holding the displayed percentage at 1%. Account for the existing whole-percent rounding. After the cut, show reserve/Low rather than a new runtime estimate calculated from the reduced load. The reserve is outside the calibrated capacity.

3. **Must-fix — Calibrate the RGB table in buffer units, not HSV brightness units.**

   QMK enables CIE conversion for RGB Matrix. For white:
   
   | Configured HSV value | Driver-buffer channel value |
   |---:|---:|
   | 64 | 12 |
   | 128 | 48 |
   | 255 | 255 |

   A7 incorrectly places the white-64/128 measurements at `led_pm` 251/502. With the actual buffer-derived values, its model predicts **3.35/13.41 mW** of RGB draw, versus R1’s **17.9/43.0 mW**. Full-white drains conceal this mistake.

   Tonight’s map can supply the calibration, but map its measurements to the actual post-conversion channel values before constructing `f()`. Use separate channel curves or measured channel gains, and check whether their sum predicts white and mixed colours. Preserve fractional milliwatts until after summing: even a full-white channel averages less than 1 mW.

   Sources: [common_features.mk:480](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/builddefs/common_features.mk:480), [color.c:27](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/quantum/color.c:27), [gauge_replay.py:46](/Users/jdlien/code/ak820-pro/scripts/gauge_replay.py:46), [current_map.py:77](/Users/jdlien/code/ak820-pro/scripts/current_map.py:77).

4. **Must-fix — The HSV hook alone does not implement reliable caps for every effect.**

   **JD’s effect 2 is fine:** ALPHAS_MODS converts both colours through the hook and renders every LED. See [alpha_mods_anim.h:9](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/quantum/rgb_matrix/animations/alpha_mods_anim.h:9).

   The important exceptions are:

   - **Enabled Jellybean Raindrops:** after initialization it updates only selected LEDs. Changing the HSV cap leaves existing LEDs unchanged. Worse, the driver’s zero scale is applied only when `set_color()` runs, so off/cut and wake can also leave old LED values for an extended time. See [jellybean_raindrops_anim.h:21](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/quantum/rgb_matrix/animations/jellybean_raindrops_anim.h:21) and [sn32f2xx.c:873](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/drivers/led/sn32f2xx.c:873).
   - **Enabled DRIFT:** it converts a cached palette, then applies the configured brightness separately. Edits made during this review now invalidate that palette when the cap changes, addressing stale caching. But capping the palette’s authored values still changes output when the user’s configured brightness is already below 64; it is not equivalent to capping that setting. See [rgb_matrix_kb.inc:138](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rgb_matrix_kb.inc:138) and [rgb_matrix_kb.inc:233](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rgb_matrix_kb.inc:233).
   - **QMK generally:** Digital Rain writes RGB directly and bypasses the hook, although it is not enabled here. See [digital_rain_anim.h:48](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/quantum/rgb_matrix/animations/digital_rain_anim.h:48).

   Define a cap-change mechanism that refreshes every affected LED and guarantees immediate darkness independently of an effect’s update pattern. Test actual output, including restoration, rather than just cap variables. Using the lighting’s effective brightness is sensible; claiming it universally preserves physical colour is stronger than the evidence supports.

5. **Must-fix — Once-per-second buffer sampling is adequate for static white, not a general energy meter.**

   `sn32f2xx_led_load()` reads the **render buffer**, while the ISR drives the separately committed buffer. QMK renders in chunks, so a housekeeping sample can contain parts of two frames. Periodic animation can also alias against 1-Hz sampling. See [sn32f2xx.c:847](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/drivers/led/sn32f2xx.c:847), [sn32f2xx.c:924](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/drivers/led/sn32f2xx.c:924).

   Prefer time-weighted power from committed frames, with a cached total or incremental lookup updates. Integrate that total over the time it actually drives the LEDs. Apply the nonlinear lookup **before** temporal averaging; `f(mean brightness)` does not generally equal mean power.

   This need not require another LED-sized buffer. Keep the accounting in the main loop, outside the flush’s interrupt-masked copy.

6. **Must-fix — Specify elapsed-time accounting, accumulator transitions and integer ranges.**

   The conversion is correct: **56,084.4 mW·s per permille**. However, the current “1 Hz” path counts ten housekeeping calls, and housekeeping resets its timestamp to “now”; ten calls need not represent exactly one second. Integrate measured elapsed time and retain fractional remainders. See [ak820pro.c:571](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/ak820pro.c:571), [battery.c:1222](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/battery.c:1222).

   The plan should define:

   - FULL and re-seat resetting/rebasing the fractional count.
   - No accumulated “debt” during a voltage-induced hold that suddenly drains the level when the hold ends.
   - **Separate physical energy logging:** it must continue while the displayed count waits.
   - Consumption during the 30-minute relaxation interval. Keeping the unplug level unchanged loses about **one percentage point at 306 mW** each time. The relaxed band should constrain the charge estimate minus energy consumed since unplug.
   - UNKNOWN/LOST charging starts, brief replugs, handover, stale reports and the cut.

   Numerically, unsigned `L_v − U` can underflow near empty; an unsigned EMA subtraction can wrap while power falls. `1000 × E_PM_MWS` fits 32 bits, but multiplying that numerator by a Q8 scale does not. Use explicit signed intermediates, bounded accumulators and wider arithmetic where needed. Occasional 64-bit arithmetic is reasonable; floating point or division per LED/frame is unnecessary on this M0.

7. **Must-fix — Complete the protocol and log migration beyond the two named host files.**

   `HC_CONN` already occupies all 32 bytes. A version bump creates no room for time left plus two power values. Specify a new page/command or an explicit replacement layout; preserving the existing status reply is the cleaner compatibility choice. See [hid_protocol.c:697](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/hid_protocol.c:697).

   The downstream audit found:

   | Consumer | Required treatment |
   |---|---|
   | `ak820battery.py` | Accept v6 and decode energy instead of `vdd_min`; retain old formats. Its period handling is already correct. [Lines 162, 189](/Users/jdlien/code/ak820-pro/hostagent/ak820battery.py:162) |
   | Simulator CSV reader | Missing `vdd_min` produces index −1, then an unchecked `c[i_vm]` access. Make it optional and explicitly handle the missing evidence. [sim.c:778](/Users/jdlien/code/ak820-pro/scripts/battery_sim/sim.c:778) |
   | Simulator log tests | Hard-code version 5, 600 seconds and waits shorter than the new period. Their expectations cannot remain unchanged. [model.c:315](/Users/jdlien/code/ak820-pro/scripts/battery_sim/model.c:315) |
   | `battery_fit.py` | Does **not** depend on `vdd_min`, but hard-codes 10-minute spans and an old load model. Keep it explicitly historical or generalize it before accepting v6 data. [battery_fit.py:45](/Users/jdlien/code/ak820-pro/scripts/battery_fit.py:45) |
   | Trial | Add power/ETA fields explicitly; preserve the existing machine-parsed level line. Its final 600-second wait no longer guarantees a completed FULL log entry. [bench_trial.py:56](/Users/jdlien/code/ak820-pro/scripts/bench_trial.py:56), [bench_trial.py:213](/Users/jdlien/code/ak820-pro/scripts/bench_trial.py:213) |
   | Guard | No 10-minute-log dependency found. Preserve supply/flag output and the progress watchdog; add observation of the new 15% event if it is part of validation. [trial_guard.py:246](/Users/jdlien/code/ak820-pro/scripts/trial_guard.py:246) |

   Define the energy field as modelled **battery discharge energy**, including its treatment of USB and mixed-supply periods. Otherwise comparison with the logger during charging is ambiguous.

   **Giving up the per-period `vdd_min` is acceptable.** Keep the separate since-boot VDD diagnostics. Fifteen-minute entries provide 180 hours without growing the 12,960-byte ring. At 306 mW, an entry contains **76.5 mWh**, comfortably within the proposed field.

8. **Must-fix — Strengthen validation before treating the replay as firmware evidence.**

   A7 uses floating-point, ten-minute steps, instantaneous downward correction, and carries its count across drain 2e’s reboot. It does not exercise the firmware’s startup, ratchet or report timing. See [gauge_replay.py:20](/Users/jdlien/code/ak820-pro/scripts/gauge_replay.py:20), [gauge_replay.py:89](/Users/jdlien/code/ak820-pro/scripts/gauge_replay.py:89).

   Reconstructing one-second steps from those averages cannot establish accuracy “everywhere” between observations. Add:

   - Correlated/bunched `5C` reports, sustained bias, excursions, stale/recovery and clamp crossings.
   - Load steps through dim/off/wake, particularly near the plateaus and cut.
   - Independent model and capacity errors, partial charges and actual reboot resets.
   - Integer boundary, timer-wrap and fractional-energy tests.
   - Every enabled effect through dim, off, cut and restoration.

   The existing simulator stubs always report zero idle time, ignore the backlight cap and synthesize LED load; a mode-variable sweep alone will miss the rendering defects above. See [sim.c:40](/Users/jdlien/code/ak820-pro/scripts/battery_sim/sim.c:40).

   On hardware, **no ≥25-ms pass is necessary but insufficient to prove no lost keys**. Check relay-sent presses, board presses, host arrivals and UART/TX loss counters while exercising transitions and deferred EEPROM writes. The driver explicitly records losses that occur without such a stall: [ch582f_ajazz.h:88](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.h:88).

9. **Should-fix — Define the ETA’s startup, averaging and display contract.**

   The proposed filter is a **12-hour exponential average**, not a 12-hour window. About 37% of its initial seed remains after 12 hours. Seeding from one startup second can therefore bias most of a drain.

   Feed it each minute’s integrated mean power, not that minute’s last instantaneous sample. Use a faster startup estimate before adopting the long time constant, and track estimate maturity. After boot, show unknown until level and load are valid; an early ETA should be visibly approximate. A battery reboot also inherits the acknowledged voltage-level uncertainty.

   On USB, hide the discharge ETA and explicitly decide whether its battery-use average freezes or is reseeded later. Near zero use `<1h`; at the cut show Low/reserve, without promising time to shutdown.

   **The seven-cell format overflows:** `100% 51h` needs eight cells. Eight 15-pixel cells already fit this panel; alternatively omit the space. Whole-day output is acceptable if deliberately coarse. See [plan:96](/Users/jdlien/code/ak820-pro/plans/PHASE2-BUILD-PLAN.md:96), [display.c:837](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:837).

10. **Should-fix — Treat the load model as calibrated for stated operating conditions.**

    R1’s baseline includes an awake MCU, a linked BT radio, panel operation and attached USB. It does not establish one universal baseline. See [R1 README:32](/Users/jdlien/code/ak820-pro/history/power-bench-2026-10-15/current-map-20261015-2210/README.md:32).

    Missing dimensions worth measuring are BT linked versus reconnecting/pairing/disconnected, 2.4 GHz, USB configured versus suspended/traffic, LCD animation and drawing, effect computation, indicator LEDs, and regulator efficiency across voltage/load. Backlight-off leaves panel drawing and CPU work running, so retaining them in the baseline is correct.

    Temperature affects usable capacity and the voltage relationship as well as draw. The logger’s die temperature is context, not pack temperature.

    A short forced-stage map should cover high/mid/low pack voltage and representative animated/coloured effects. “Within 5% in every state” needs a stated state set. A 5% power-error allowance also does not itself guarantee a ±3-point level error.

11. **Should-fix — R5 is useful field validation, but R3 is not a matched gate-5 control.**

    Different days of “JD’s daily use” have different idle distributions—the very variable Normal exploits. R3 also ran different firmware and suffered a logger/USB outage, with capacity bridged over the gap. Its uncertainty is documented and reasonable, but it is not an exact control. See [R3 README:47](/Users/jdlien/code/ak820-pro/history/battery-2026-10-15-drain-2f/README.md:47).

    Use R5 to validate independent logger energy against predicted energy and gauge level. For a strict gate-5 comparison, use matched settings, firmware, USB conditions and a repeatable activity schedule, or explicitly report the historical comparison as uncontrolled. Record time spent in each stage to explain the result.

    Require a verified FULL start. Define the endpoint from the protection latch: **when Normal already has RGB off, there may be no current step announcing the cut.** The existing trial’s `lights_cut` check is appropriate; quantify its polling uncertainty. Keep R5 held out from fitting until scored.

12. **Should-fix / note — The mode, persistence and keymap direction is good; tighten the details.**

    Separate selected mode from automatic lighting restriction. The 15% rule should remain battery-only, preserve the selected mode, and retain the hard cut through keypresses and RGB toggles. Use a defined re-arm/hysteresis policy and deliver the one-time notice when it can actually be seen; an idle, dark screen can otherwise consume the notification unseen.

    Leaving RGB/backlight settings untouched correctly avoids spurious parameter overlays. Existing queued overlay setters are suitable. However, **`Power: Normal` is 13 characters against a 12-character overlay**, so shorten it. See [display.c:2167](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:2167).

    The concurrent EEPROM edits now use a seventh byte, `mode+1`, version 4 and deferred writes—appropriate choices. Include the settling delay in persistence tests. Appending the keycode and updating both stored Fn bindings is also correct. Read back those bindings after restoration; the existing VIA checker verifies count, not preservation of every old index. See [kb_eeconfig.c:21](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/kb_eeconfig.c:21), [check_via_sync.py:29](/Users/jdlien/code/ak820-pro/scripts/check_via_sync.py:29).

**The replay’s asymmetry is acceptable for a first build**, provided the accuracy claim is scoped honestly. An overestimated draw or underestimated capacity makes the level conservative; adding upward creep from voltage would also respond to load relief and relaxation. Correct the band and transition rules first. Retaining limited persistence and accepting poorer accuracy after a battery reboot is a reasonable tradeoff; it must not be included in an unconditional ±3-point claim.

The charging model can remain, the effective-backlight duty model is well supported, and deferring radio changes and sleep is sensible. Small fixed-point accumulators, flash-resident lookup tables and the existing glyph queue need not threaten the loop budget. The risks are extra work inside interrupt-masked regions, per-frame software arithmetic, synchronous display work and flash-write collisions—not the basic energy-count calculation.

I changed nothing. Files were being edited during this review; the observations above distinguish those edits where relevant. I ran the read-only replay, but did not build firmware or operate the board.