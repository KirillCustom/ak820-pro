**Must-fix-before-R3: bound failed-read recovery and prevent the guard’s new activity reads from colliding with the cut-time dump. Also make a verified FULL start explicit.** I would not leave the present scripts unattended for several days unchanged.

I only read files and reference documentation. I did not execute bench scripts, modify files, or contact any device.

1. **Must-fix-before-R3 — the guard watches process survival, not measurement progress.**

   During `wait-cut`, a failed battery read returns `None`; the trial simply sleeps and tries again, indefinitely. The guard sees a live process and does nothing. Thus losing USB data or repeatedly failing HID reads can let the keyboard drain through the RGB cut and reserve without ever starting recharge. A single failed read is tolerable; an unlimited sequence is not. The charging loop likewise has no overall deadline. See [trial polling](/Users/jdlien/code/ak820-pro/scripts/bench_trial.py:162), [charge completion](/Users/jdlien/code/ak820-pro/scripts/bench_trial.py:205), and [guard recovery](/Users/jdlien/code/ak820-pro/scripts/trial_guard.py:222).

   Before tonight, require bounded retries plus a deadline since the last **successful** battery observation, with a defined recovery that ends the measurement and restores normal USB power when possible. Simply calling the existing `fail()` is insufficient protection against continued discharge: it turns the relay **off**, and the guard explicitly stops on `FAIL`. Also ensure the Mac remains awake and monitor fresh logger rows; detaching processes does not make them run through system sleep. The bench documentation already identifies loss of the trial as a route to exhausting the reserve and losing the RAM log. [Failure action](/Users/jdlien/code/ak820-pro/scripts/bench_trial.py:131), [bench warning](/Users/jdlien/code/ak820-pro/docs/test-bench.md:271).

2. **Must-fix-before-R3 — “after the trial’s read” is not sufficient HID coordination.**

   The guard avoids its old warning/probe reads on a cut-bearing `wait-cut` row, but the new `activity()` call runs on **any last row containing `supply`**, including `CUT`. The trial immediately follows `CUT` with a potentially long RAM-log dump. Consequently the guard can seize raw HID while that dump is running. Its subprocess timeout is also 150 seconds, longer than the trial’s 60-second post-dump pause. [Guard dispatch](/Users/jdlien/code/ak820-pro/scripts/trial_guard.py:203), [trial cut sequence](/Users/jdlien/code/ak820-pro/scripts/bench_trial.py:163).

   Moreover, `--stalls --json` is not one counter read: on protocol 8 it also fetches link diagnostics, profiling, flash statistics, and 22 accounting pages. [Health command implementation](/Users/jdlien/code/ak820-pro/hostagent/ak820health.py:448).

   For tonight, either disable the new recurring reads or restrict them to successful, non-cut drain polls with bounded execution and explicit coordination. Prefer a small activity-only read. Serialize future map commands with trial/guard access too. A failed activity read should remain an **unknown interval**, as the current blank row largely permits, never become “zero presses.”

3. **Must-fix-before-R3 — explicitly establish the charge-count boundaries.**

   `bench_trial.py` checks that its starting supply is battery; it does **not** establish that the pack began at FULL, switch off the relay, or record the actual unplug boundary. R1/R2 have already consumed charge. Either recharge to verified termination before R3, or identify an earlier continuously logged FULL boundary and account for every intervening discharge/top-up. Record the counter value, settings, firmware, and cut threshold at the chosen start. [Start checks](/Users/jdlien/code/ak820-pro/scripts/bench_trial.py:150), [A1 definition](/Users/jdlien/code/ak820-pro/plans/POWER-CAMPAIGN-PLAN.md:65).

   Define the endpoint as the actual RGB cut, rather than the later poll or relay-on time. The five-minute polling interval, dump, and extra minute leave additional reserve discharge before charging. The current trace can help locate the cut when RGB was on; R5 with RGB already off needs another endpoint observation.

As written, the five goals are covered only partially:

| Analysis | What the measurements support | Missing measurement or limitation |
|---|---|---|
| **A1 — accurate level** | A counted, load-specific usable-charge reference; a discharge-table refit | Relaxation and charge/discharge history; defensible resistance identification; independent validation; treatment of the upper clamp |
| **A2 — capacity** | Usable mAh and mWh from verified FULL to the defined cut | An uncertainty budget and temperature qualification. This does not measure the reserve or the pack’s nameplate capacity |
| **A3 — calibrated charge** | Net battery charging current, accumulated charge, taper and termination behaviour | Post-unplug relaxation, partial-charge validation, and confirmation that the apparent knee is actually CV onset |
| **A4 — every feature’s draw** | Selected RGB states and backlight levels | Radio/link states, MCU sleep contribution, display rendering/animation, different RGB colours/effects/speeds, and the intended dimmed version of JD’s effect |
| **A5 — USB correction** | One local attached/detached comparison | Whether the difference changes with voltage, lighting, host traffic, radio state, or host sleep |
| **A6 — voltage-channel discrepancy** | Relative INA228-versus-VCELL error across voltage | An independent voltage reference to establish which channel is wrong and by how much |
| **A7 — time left** | A preliminary estimate for measured states | Integration over falling voltage, a defined endpoint, future activity assumptions, and held-out validation |
| **A8 — ladder sizing** | Coarse evidence of long periods without new key presses | Exact idle occupancy, short-rung timing, complete input activity, transition costs, and representative longer-term use |

The feature exclusions are acknowledged in the [campaign’s capabilities](/Users/jdlien/code/ak820-pro/plans/POWER-CAMPAIGN-PLAN.md:27), but they mean this campaign cannot yet deliver **every feature’s draw**. The agreed Phase 2 measurement explicitly includes BT linked/idle and eventually a forced light-sleep build. [Agreed order](/Users/jdlien/code/ak820-pro/plans/BATTERY-GAUGE-PLAN.md:511).

4. **Should-fix — A1’s coulomb reference is sound; the proposed universal voltage correction is not yet identified.**

   At roughly 74–80 mA from the labelled 4000 mAh pack, this is approximately C/50. Counting charge is a good reference at that rate. With positive discharge current,

   \[
   Q_\text{usable}=Q_\text{cut}-Q_\text{start},\qquad
   L_Q(t)=1-\frac{Q(t)-Q_\text{start}}{Q_\text{usable}}.
   \]

   That measures the fraction of **usable charge remaining to this run’s cut**, not chemical SOC to complete depletion. Changing loads during the run does not invalidate the count, although it changes voltage history and potentially the charge available before the voltage-triggered endpoint.

   There is also a definition change: today’s gauge represents **remaining full-white runtime**, whereas A1 represents remaining charge. Those differ behind the buck-boost. The table, clamp anchor/countdown, charge-model units, and runtime estimator must be made consistent; “only its constants change” understates this. [Existing level definition](/Users/jdlien/code/ak820-pro/docs/battery.md:182), [upper-clamp countdown](/Users/jdlien/code/ak820-pro/docs/battery.md:355).

   A C/50 discharge curve can be a useful empirical loaded-voltage curve. It is not automatically an equilibrium OCV curve. NMC research measures both overvoltage and hysteresis even at C/50; a single \(I R\) correction cannot remove all history dependence. [Experimental NMC study](https://www.nature.com/articles/s41598-019-51474-5).

   The missing observations are:

   - Repeated load steps at nearly the same charge level, with pre-step, early-step, and later voltage measurements. A difference between widely separated hold averages mixes resistance, falling SOC, and polarization.
   - Relaxation after load reductions and after charge, including partial charges at several starting levels. The earlier plan explicitly requested 0/5/15/30/60/120-minute observations; R4 currently ends with the relay **on**. [Original calibration requirements](/Users/jdlien/code/ak820-pro/plans/BATTERY-GAUGE-PLAN.md:338), [R4 implementation](/Users/jdlien/code/ak820-pro/scripts/bench_trial.py:200).
   - The second load, R5, retained as genuinely held-out validation. If its voltage shift is used to fit the correction, it is no longer an independent validation run.

   Use a consistent convention: correcting to OCV requires adding \(IR\) on discharge; correcting to an already loaded reference curve requires the **difference from its reference load**, not another full \(IR\). The 1 Hz logger and averaged sensors can identify an effective, seconds-scale response, not an instantaneous ohmic resistance.

   Also, `CHARGE_IR_MV` was removed in Phase 1b. Measuring unplug steps remains useful, but there is no current constant by that name simply awaiting replacement. [Phase 1b correction](/Users/jdlien/code/ak820-pro/docs/battery.md:369).

5. **Should-fix — “gap-free” does not mean “exact,” and temperature/reference claims need narrowing.**

   The continuous charge accumulator is the correct primary integrator. However:

   - The shunt is specified as **1%**, not independently calibrated; agreement between sample current and accumulated current checks consistency, not absolute current scale. The INA228 timebase also has finite accuracy. Common scale error largely cancels in A1’s normalized fraction, but not in absolute capacity or feature-current claims. [Bench calibration caveats](/Users/jdlien/code/ak820-pro/docs/test-bench.md:310), [INA228 datasheet](https://www.ti.com/lit/ds/symlink/ina228.pdf).
   - QT Py power loss clears the accumulator. `power_log.py` reconnects and appends without creating a new accumulation segment. Never difference charge across a reset; preserve segments and flag the unmeasured gap. A software restart alone does **not** clear it, despite the map’s comment equating a restarted `qt_ms` with a reset accumulator. [Recorder reconnect](/Users/jdlien/code/ak820-pro/scripts/power_log.py:115), [QT Py initialization](/Users/jdlien/code/ak820-pro/scripts/qtpy/code.py:72), [map restart check](/Users/jdlien/code/ak820-pro/scripts/current_map.py:152).
   - `temp_c` is **INA228 die temperature**, not pack temperature and not a calibrated room measurement. Treat it as context; charging and board self-heating can separate it from the cell. A single falling-pack trace also confounds SOC with time-of-day temperature. [Temperature caveat](/Users/jdlien/code/ak820-pro/docs/test-bench.md:321).
   - VCELL is the sensible working voltage channel, but not laboratory truth. Its specified error is ±7.5 mV at 3.6 V/25 °C, with broader limits elsewhere. That matters on a plateau where the repo reports roughly 1.15 percentage points per mV. Its printed decimal precision is not accuracy. [MAX17048 datasheet](https://www.analog.com/media/en/technical-documentation/data-sheets/max17048-max17049.pdf), [plateau sensitivity](/Users/jdlien/code/ak820-pro/docs/battery.md:213).

   A6 can fit a relative slope/intercept now. An independent meter check at several voltages is needed before attributing the entire discrepancy to INA228 or claiming an absolute calibration. Align `5C` with its roughly two-minute filtering window and exclude clamped values. The MAX’s implausible SOC readings are not a reference for A1. [Filtering](/Users/jdlien/code/ak820-pro/docs/battery.md:123), [observed MAX SOC](/Users/jdlien/code/ak820-pro/plans/current-status.md:69).

6. **Should-fix — the map is reasonable for steady power, weaker for resistance and arbitrary settings.**

   Ninety-second holds with 15 seconds discarded leave 75 seconds for measurement. That is plausibly enough for static lighting power and is comfortably beyond the two-second VIA parameter overlay and its restoration. The backlight dev hook itself does not draw that overlay. This part is sensible. [Overlay behaviour](/Users/jdlien/code/ak820-pro/plans/current-status.md:102).

   Forward then backward is also sensible: symmetric timing cancels an approximately **linear additive drift**. It does not cancel nonlinear voltage decline, state-dependent converter efficiency, thermal history, cell relaxation, or changes in typing/media activity. Treat the two passes separately first; disagreement is information, not merely something to average away. Preserve transition traces for A1 while excluding them from steady-power estimates.

   Specific coverage problems remain:

   - White at 64 is not generally equivalent to JD’s effect dimmed to 25%. Colour, effect, speed, and rendering cost matter.
   - “LCD off” switches the **backlight**, not the panel/controller or drawing tasks. It does not measure total screen power. [Backlight implementation](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:283).
   - Four nominal pack levels cannot justify “any pack voltage” without checking interpolation. The target 70–85% plateau particularly deserves paired load observations.
   - The midnight-only scheduler may skip every useful low-voltage map. That is acceptable for JD’s use, but then A4’s voltage dependence remains unmeasured.
   - The existing 20-hold map at 60 seconds takes **20 minutes plus commands**, not approximately 15. A shortened state list must be specified. [State list and ordering](/Users/jdlien/code/ak820-pro/scripts/current_map.py:54).

   The “well under 1%” claim should be calculated from logged charge relative to baseline. Even a small charge disturbance can produce a larger temporary—or ratchet-retained—level error on the plateau.

7. **Should-fix — R2 measures a combined tethering effect, not a universal runtime offset.**

   Data attached adds USB activity **and** enables the daemon’s media, clock, and health traffic. The daemon sends media keepalives, performs clock transactions, and samples health every five minutes; R3 adds trial polls and guard reads. R2 must reproduce that traffic if it is supposed to correct R3. [Daemon scheduling](/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:7), [media keepalives](/Users/jdlien/code/ak820-pro/ak820-agent/src/media.rs:1).

   Successful telemetry consumes processing/USB energy; media and clock changes can cause display work. Conversely, exclusive Python reads can temporarily suppress daemon traffic. A five-minute block includes very few slow polling events and reconnection can add a transient, so discard settling and repeat paired comparisons. Ideally separate **data attached with telemetry quiet**, **attached with normal telemetry**, and **data disabled**, at more than one load/voltage.

   Do not subtract an offset in **hours** from every runtime. Model a measured power/current difference, then recompute runtime. Even `uhubctl` data-off is not electrically identical to removing the cable: the bench records approximately 0.2 mA through the D+ pull-up/pull-down path. [Data-off caveat](/Users/jdlien/code/ak820-pro/docs/test-bench.md:130).

   The board does **not** currently have the proposed idle ladder enabled, and the MCU’s WFI idle is disabled. Thus it would be wrong to assume these reads repeatedly wake a sleeping MCU or reset the ladder timer. Input activity comes from matrix/encoder events, not these health commands. Radio-side idle behaviour remains something to measure. [Current power behaviour](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/power.c:3), [input timers](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/quantum/keyboard.c:155).

8. **Should-fix — A3 is the right measurement, but current taper alone does not prove a CV knee.**

   The shunt measures **net current into the pack**, which is the useful quantity for level increase. Identify CV onset from sustained current taper **together with pack voltage approaching a stable regulation plateau**. USB supply droop, charger thermal regulation, changed board load, contact problems, or charger faults can also reduce current.

   Normalize the accumulated charge using the chosen usable-charge definition to fit `K_CC` and knee level; also refit/check the tail. Today’s tail duration and shape were inferred, and changing only the knee and slope need not describe the measured taper. [Existing charge model](/Users/jdlien/code/ak820-pro/docs/battery.md:541).

   `bench_trial.py --full` accepts CHRG high on external power twice; it does not require the firmware’s FULL/clamp condition. The charger documentation explicitly says CHRG high can also indicate a fault. Verify termination with voltage, current history, and board state together. [Charger status meanings](/Users/jdlien/code/ak820-pro/docs/battery.md:854).

   Finally, correct mAh-in versus mAh-out for the discharge between the cut and relay-on, and use matched endpoints. One charge from the cut cannot establish partial-charge restart accuracy or post-unplug reseating. Those can be measured later without interrupting tonight’s baseline.

9. **Should-fix — A7 needs an integral, not remaining mAh divided by present current.**

   Under approximately constant power, current rises as voltage falls. Therefore `remaining_mAh / current_now` generally overestimates remaining time. Use either

   \[
   t_\text{left}=\int \frac{dQ}{I(Q,\text{state})},
   \]

   or remaining **deliverable energy** divided by power when the constant-power approximation is adequate. Remaining energy is not simply remaining mAh times today’s voltage.

   Include converter-efficiency variation, the chosen USB condition, and the load-dependent voltage cut. State whether “time left” means **to RGB cut** or **to eventual shutdown**: this campaign measures the former, while the documented reserve can last another five hours. [Reserve behaviour](/Users/jdlien/code/ak820-pro/docs/battery.md:706).

   R3 provides fitting and consistency checks; R5 provides independent validation at another load. Neither supports “any settings” without the missing A4 measurements. An estimate “at these settings” is achievable; a prediction of JD’s future usage requires an activity model.

10. **Should-fix — A8 supports bounds on long idle periods, not exact rung occupancy.**

   An unchanged counter over five minutes establishes no **new counted presses** during that interval. It does not establish that all five minutes would have been spent in the five-minute rung. Conversely, one press in each adjacent bin can conceal a gap approaching ten minutes.

   Consecutive empty bins can identify long absences and bound savings, but the last press before them and first press afterward are only localized to their respective bins. Calculate lower/upper occupancy bounds for the 5/15/30-minute thresholds rather than a precise percentage.

   Further limitations:

   - The counter is **16-bit**, and health reset clears it. Handle wrapping, resets, reboot detection, missed reads, and actual timestamps. [Counter implementation](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/health.c:37).
   - Press counts do not exactly represent the ladder’s matrix/encoder activity timer, including releases and knob activity.
   - Five-minute counts cannot resolve 30-second, one-minute, or two-minute rungs.
   - Adding counts to the existing **600-second** battery-log interval would be coarser, not the finer data claimed in A8. Use shorter intervals or, preferably, firmware accumulation of time beyond each idle threshold. [Battery-log interval](/Users/jdlien/code/ak820-pro/docs/battery.md:465).
   - Avoid double-counting overlapping rungs. Occupancy times savings predicts energy saved over the observed period; runtime gain then follows from the new average power, with transition costs and representative usage accounted for.

11. **Note — physical and daily-use risks are limited but specific.**

   R1’s RAM-only settings and `finally` restoration are good choices. However, restoration is not guaranteed after a killed process, failed HID reopen, or host power loss. Relay-status failure is currently interpreted as “off,” and the map verifies battery supply only when it thought the relay was initially on. Require an explicit successful supply check before measuring. [Map setup/restoration](/Users/jdlien/code/ak820-pro/scripts/current_map.py:197).

   A midnight window does not guarantee JD is absent. The scheduler should skip or stop when he uses the keyboard, and restore promptly. A brightness key pressed during a temporary setting can persist that temporary level; this interaction was already noted in the flash-2f review. [Review disposition](/Users/jdlien/code/ak820-pro/plans/current-status.md:97).

   The new health reads do not themselves alter settings or flash. They can interrupt daemon access, and the firmware reply path can wait up to 100 ms if its endpoint backs up, potentially causing visible/input stalls. That warrants lightweight reads and watching counter deltas, not an assumption that polling is free. [macOS transport behaviour](/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/macos/device.rs:16), [reply timeout](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/tmk_core/protocol/chibios/usb_main.c:392).

   The reversed shunt was corrected and was not itself electrically harmful. The relay’s normally-open wiring stops charging on power loss, but does not stop battery discharge. `uhubctl` must retain the explicit keyboard port and a bounded timeout; approximately 15 seconds of execution needs the proposed 40-second allowance. A timeout is an **unknown result**, so verify data restoration before R3. Do not substitute a whole-hub power cycle: that can also reset the logger. [Shunt sign](/Users/jdlien/code/ak820-pro/docs/test-bench.md:341), [relay behaviour](/Users/jdlien/code/ak820-pro/docs/test-bench.md:47), [hub timeout history](/Users/jdlien/code/ak820-pro/docs/test-bench.md:147).

Tonight’s necessary work is host-side reliability, coordinated HID access, an awake/logging host, and explicit FULL-to-cut boundaries. The uninterrupted R3/R4 trace remains valuable without a new firmware flash. Relaxation trials, broader feature maps, temperature characterization, a validated resistance model, finer activity logging, and R5 can follow; they must precede claims that all five goals are complete.