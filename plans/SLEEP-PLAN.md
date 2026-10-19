# Sleep: light sleep's sizing build, and a deep-sleep experiment (10-18)

Two rungs below the ladder's lights-off stages (`BATTERY-GAUGE-PLAN.md`,
"A light sleep rung" and "Two kinds of away"):

- **Light sleep** keeps the Bluetooth link and every keypress, so it can run
  whenever the board is dark, in Normal. It goes after the MCU's wasted work.
- **Deep sleep** is for the long away (a night, a weekend). JD's rule: **if a
  keystroke cannot wake it, it is not worth having** -- but test it.

Both are built as dev hooks first, off unless a raw-HID command turns them
on, so the board behaves as today otherwise and tonight's maps and the
baseline (`scripts/sleep_baseline.py`) measure the normal firmware.

## What the board does today, dark

From a read of the code (file:line in the firmware unless noted):

- **The row ISR runs at full rate with the LEDs dark.** CT16B1 (`PWMD1`,
  priority 3), period 256 ticks of 4.8 MHz, re-armed at the end of its own
  body (`drivers/led/sn32f2xx.c:735-749`), so ~3,876/s at ~188 us: **72.8%
  of the CPU**. `sn32f2xx_set_power_scale(0)` only zeroes the colours as they
  are written (`sn32f2xx.c:901-915`); every ISR still disables and re-enables
  15 PWM channels and writes 18 row pins (`update_pwm_channels`,
  `sn32f2xx.c:490-560`). One key row is scanned every third ISR, so each key
  is sampled every ~4.6 ms.
- **The main loop never yields**: ~335 passes/s in the CPU the ISR leaves
  (`quantum/main.c:48-76`, `housekeeping_task_kb`, `ak820pro.c:555-623`).
- **WFI is off** (`config.h:244`, `CORTEX_ENABLE_WFI_IDLE FALSE`, with the
  2026-08-28 history above it: turned off while chasing a hang it did not
  cause). The idle thread exists but never runs. The board's ChibiOS config
  already writes `SN_PMU->CTRL = 4` on idle entry and 0 on leave
  (`platforms/chibios/boards/SN_SN32F290/configs/chconf.h:30-38`).
- **The kernel is tickless** (`CH_CFG_ST_TIMEDELTA` 2, a 16-bit timer at
  187.5 kHz), so nothing like a 1 kHz SysTick has to wake the core. What
  does: the row ISR, the **20 kHz** CT16B3 tick (`indicators.c:132-175`: the
  backlight's software PWM and the indicator LEDs; every 50 us), QMK's timer
  overflow (~175 ms), the CH582F UART (priority 1), USB, the 1 Hz RTC.
- **Measured dark** (lights out, screen dark, USB data up, on the pack):
  27.51 mA (`minimal_check.py`, 17:32 10-18); R1's 27.3 mA with every light
  out. Of that, the MCU's share is unknown: the radio, the regulator and the
  panel's logic are in it too.

## Light sleep: the sizing build

**The question it answers:** how much of the 27.5 mA is the MCU's busy
work, and what each step costs in keypresses. If the whole set saves less
than ~2 mA (~7%), stop: light sleep is not worth its risk, and the work goes
to the long-away tier.

One new dev hook, `HC_DEVHOOK` op 14 **sub 3**: `arg` a bitmask, byte 6 the
yield in ms. Each bit is honoured only while the RGB is dark (the driver's
power scale 0: a dark stage or the lights cut), and dropped at once when it
lights, so a wrong mask can never leave the LEDs dark or garbled.

| bit | step | what it does | risk to a keypress |
|---|---|---|---|
| 0 | **scan-only ISR** | Blank the LED outputs once; lengthen PWMD1's period to 3,000 ticks (`pwmChangePeriodI`, 0.625 ms + a short body, ~1,500/s); each ISR scans ONE key row with its own row counter, so every key is sampled every ~3.9 ms (~250 full scans/s), under the debounce's 5 ms (codex, plan review 8). The LED row counters are left alone, and on exit the period goes back to 256 and the LED field resumes where it was. The flash-write guard and the end-of-ISR re-arm stay. | Sampling every ~3.9 ms against ~4.6 today, so `sym_defer_pk`'s 5 ms always ends on a fresh sample. The ISR body shrinks (no PWM work), so SPI0's DMA (same priority 3) sees shorter blocking. |
| 1 | **yield** | `chThdSleepMilliseconds(n)` at the end of `housekeeping_task_kb`, after `watchdog_kick`, n = byte 6 (1-4). | Adds up to n ms between a scan and its consume. The CH582F's RX queue (128 B) drained 64 B a pass wants a pass every ~5.5 ms at full line rate (codex), so the ceiling is 4 ms; the knob's quadrature is polled at the pass rate (>= 200 Hz at 5 ms). |
| 2 | **WFI** | `CH_CFG_IDLE_LOOP_HOOK` runs `__WFI()` while the bit is set (`config.h`'s global stays off). Only useful with bit 1, which gives the idle thread time. | A wake that never comes would stall the main loop until the next interrupt; the row ISR (bit 0 keeps it at ~1 kHz), UART and QMK's timer bound that. The PMU mode the idle hook selects (`CTRL = 4`) must be Sleep, not Deep-sleep: checked in the manual before the build (below). |
| 3 | **stop the 20 kHz tick** | With the screen dark (`SCREEN_OFF`) and no indicator lit, stop CT16B3 (`gptStopTimer`), leaving the backlight and indicator pins low; restart it (and its MR0RST fix, `indicators.c:174`) the moment either is wanted. | None to keys. A lock LED that should light waits for the restart (one tick). |

**Measured by** `scripts/sleep_sizing.py` (to write), hands-free: relay 2 off,
the stage forced to `SCREEN_OFF`, then for each mask (0, 1, 3, 7, 15, and
bit 1 at 2 and 5 ms): settle 20 s; the logger's current over 60 s (the
charge register); health page 4 (ISR rate and body), page 3 (`rowgap`), the
stall counters; then 200 presses at 40/60 ms and 200 at 25/40 ms through the
wake rig, counted at the board. Mask 0 is the baseline again, on the same
run. Restored in a `finally` (mask 0, the stage, relay 2 on).

**Pass, per mask:** every press counted (`key_presses` = n, `raw_edges` =
2n), `rowgap` <= 6 ms, no stall >= 25 ms, the link up throughout (no new
ACK timeouts beyond the baseline's), no watchdog reset.

**Then**, if it is worth it: the real rung (automatic in the dark stages and
under the lights cut, so Minimal gets it while typing; a key change wakes the
main loop from the ISR instead of a fixed sleep), its own codex review, and
the gate: 10,000 presses, the first press after long idles, latency at the
Mac within a few ms of the baseline.

### What the manual says about WFI here (SN32F299 V1.8)

`PMU_CTRL` (`0x40032040`) selects the mode a `WFI` enters: **4 is Sleep, 2
is Deep-sleep** (pp. 74-75); 0 is a plain Cortex-M0 `WFI` (the core's clock
gated, nothing else). ⚠️ **The USB transceiver turns itself off in Sleep and
Deep-sleep** (p. 245, `USB_CFG.PHY_EN`). The bench reads the board over USB
data while it runs on its pack, so bit 2 is a plain `WFI` with `PMU_CTRL` 0:
the idle hooks that write 4 today (harmless while WFI is off) are replaced
so nothing writes 4 unless asked. A fifth bit, **bit 4, PMU Sleep** (`CTRL`
4 around the `WFI`), applies only while USB is not configured (a user on
battery); it is measured with the data cut (`uhubctl`) and read back after.

### As built (`9f46337b6f`, 10-18)

`sleep.c`/`sleep.h`, dev builds only (a stub otherwise; the daily build has
none of it, and its idle hooks are the platform's). Op 14 **sub 3** sets the
light-sleep mask (reply: the mask, the bits in force, the WFI count,
scan-only in force); **sub 4** arms the deep sleep or reads its record. The
LED driver gained `sn32f2xx_set_scan_only()` and I-class sleep hooks; the
board's idle hooks choose the PMU mode at each WFI (4 only for bit 4, else
0). Measured by `scripts/sleep_sizing.py` (light) and `scripts/deep_sleep.py`
(deep).

## Deep sleep: the experiment

**What the manual gives** (pp. 74-78, 150-152, 263): only Sleep and
Deep-sleep exist (no deep power-down on this part). Deep-sleep stops the
12 MHz IHRC, the PLL, flash, USB, the ADC and every peripheral clock; it
keeps RAM, registers and pin levels; it wakes on a **GPIO edge**, the RTC,
or reset, in ~12.7 us plus the PLL's ~100 us to relock. The ILRC keeps
running when the RTC uses it, which this board's does, so **the watchdog
(ILRC-clocked) must be assumed to count** (the manual does not say). The
datasheet's deep-sleep figure is 2 uA (every oscillator off); the MCU at
48 MHz is ~11 mA. The CH582F has no sleep command and keeps its BLE link on
its own; anything it sends while the SN32 sleeps is lost.

**The board's matrix** is COL2ROW (`keyboard.json:141-144`): 15 columns on
P0.4-7, P2.0-9 and P2.14, with pull-ups; 6 key rows. Drive every key row and
the 18 LED row pins low, release the columns from PWM (inputs, pull-ups),
and a press pulls its column low: a falling edge on P0 (IRQ31) or P2
(IRQ29).

**One dev hook**, `HC_DEVHOOK` op 14 **sub 4**: arm (delay s, cap s), or read
the last run's record. Armed, the main loop waits out the delay, then sleeps
at the first pass where all hold: no key down; USB not configured; the
CH582F's TX queue empty and UART2's transmitter empty (`LS.TEMT`, bit 6); no
glyph or LCD transfer in flight; no flash write pending; the RTC idle (no slew
or reload pending); **the watchdog armed and counting** (not degraded after
three resets: the way back relies on it, codex). Never safe within 60 s: it
gives up and records why. Then, with interrupts locked throughout (PRIMASK set, so a wake
interrupt is never taken; its vectors have no handlers):

1. A watchdog breadcrumb ("deep sleep") with its sacrificial write.
2. Blank the LEDs and disable PWMD1's notification (I-class: no lock call
   anywhere inside, since an unlock re-enables interrupts); disable the
   column PWM channels; the 20 kHz tick stopped with its pins low; UART2's
   receiver off (TX stays enabled, idling high); the ADC off (~100 uA); key
   rows driven low, columns inputs with pull-ups.
3. Save the NVIC enables; clear and disable all; SysTick's alarm off. The
   column pins' interrupts: edge, single, **falling** (IS 0, IBS 0, IEV 1,
   pp. 81-84), cleared after the rows settle, then enabled; only P0, P2 and
   the RTC in the NVIC.
4. A second that came since the lock is counted now. **A column already low
   is a key wake with no sleep** (a press before the clear erased its edge).
5. The system clock to the IHRC (IHRCRDY checked), then the PLL off (never a
   selected PLL, p. 64), then the flash to its slow mode (`LPCTRL`
   `0x5AFA0000`) for <= 24 MHz.
6. Loop: `PMU_CTRL = 2; DSB; WFI`. A second: cleared in the RTC **and** the
   NVIC (no handler runs to do it), the watchdog fed directly, counted; at
   the cap, leave. A column edge: leave, keeping which pins. Anything else
   counts as stray, and 1,000 strays leave.
7. Restore: the flash's speed, the PLL relocked (bounded: a reset if it does
   not lock) and selected; the pin interrupts off and cleared; the temporary
   vectors disabled (`ICER`) **before** the old enables go back (writing
   `ISER` cannot disable one); a second coincident with the wake counted
   once; rows high (the unselected level); the ADC (and its 100 us) and
   UART2's receiver back; SysTick left off and pended, so its handler serves
   the timers and re-arms the next alarm; the PWM notification and the tick
   back. Both software clocks get the slept seconds (`rtc_seconds_count` and
   the LLD's calendar time via `rtc_lld_add_secondsI`, a ChibiOS patch), with
   interrupts still locked. Then unlock, and restart USB (its transceiver was
   off).
8. The system timer (CT16B5) stood still, so `timer_read32()` lost the sleep:
   the ladder's idle, the gauge's energy count and QMK's ~175 ms timer skip
   it, and it ran at a quarter speed in the few microseconds awake at 12 MHz
   each second (accepted for an experiment of minutes; the gauge's model
   would need the asleep draw added in a real build).

The record: the wake reason (key, cap, stray), the column pins that woke it,
seconds asleep, stray wakes, the PLL's lock loops, and `key_presses` before
and after (did the waking key itself register, or was it lost).

**The bench run**, JD at hand (if it never wakes, the slider brings it back):
the pack (relay 2 off), the stage forced dark, arm (20 s, 120 s), `uhubctl`
data off; at ~60 s the wake rig presses F19; data back on, read the record,
the logger's current while asleep. Then a run with no press (the cap wakes
it), and one with the watchdog left unfed for 15 s (a reset at ~12 s means it
counts in Deep-sleep). Pass for "worth having": **every run wakes on the
key**; the link is still up after; and then the question that decides the
design: is the waking key itself lost.

**Risks, worst first:** never waking (the watchdog stays armed and the cap
self-wakes; if neither works, the slider); a wake interrupt taken with no
handler (PRIMASK held throughout); a lock call in the loop re-enabling
interrupts (direct register writes only); a pin frozen driven (an LED row
lit, the backlight, UART TX low: all set before the sleep); the PLL not
relocking (the watchdog, ~12 s); frozen system time confusing a timeout
after the wake (none is armed across it, by the preconditions).

## Codex's plan review (10-18) and what changed

[Verbatim](review-codex-sleep-plan-2026-10-18.md), prompt
[`review-prompt-sleep-plan.txt`](review-prompt-sleep-plan.txt): **six
must-fixes (all deep sleep), three should-fixes, the scan-only design sound.**

| # | finding (short) | disposition |
|---|---|---|
| 1 | PRIMASK must hold through teardown and restore (a non-I lock call unlocks); clear each wake source in the peripheral and the NVIC; disable the temporary vectors before restoring `ISER` | **Done:** I-class calls and register writes only (`sn32f2xx_sleep_enterI`, `pwm_tick_stopI`, `rtc_account_sleepI`); RTC cleared in both; `ICER` before `ISER`. |
| 2 | falling edge is `IEV 1` with `IBS 0`; a press before the clear loses its edge: read the columns before sleeping; count a second coincident with the wake | **Done** (step 4 and 7 above). |
| 3 | the LLD's own calendar time (`rtc_time`, what `rtcGetTime()` reads) also needs the seconds, before interrupts resume; do not restart the RTC | **Done:** `rtc_lld_add_secondsI` (a ChibiOS LLD commit, `27e852d25a`), called with `rtc_seconds_count`'s update under the lock; the SOF count's continuity invalidated. |
| 4 | the clock contract: IHRC ready, select, then the PLL off; flash slow mode at 12 MHz and back before 48; UART2's receiver; the ADC off; CT16B5; SysTick's `VAL` cannot be restored | **Done** but CT16B5: it is not stopped across the few microseconds awake at 12 MHz each second (a quarter-speed tick, accepted and stated). SysTick is left off and pended, not restored. |
| 5 | WFI with `PMU_CTRL` 0 is unverified (the manual names only Disable, Sleep, Deep-sleep); replace both idle hooks; bit 4 must end by itself before USB returns | **Done:** both hooks replaced in dev builds, the mode set at each WFI; bit 4 has its window and the bench waits it out before data returns; bit 2 is measured as a probe (USB continuity is part of its pass). |
| 6 | the watchdog may be off (degraded after three resets): check before arming | **Done:** a precondition (`watchdog_running()` and `CFG.WDTEN`). |
| 7 | note: the scan-only period change, the separate counter and blanking once are sound; do not reuse `sn32f2xx_blank()` | Agreed; not reused. |
| 8 | should fix: 5.2 ms sampling lets debounce accept a stale sample; a 5 ms yield is too long for the RX queue | **Done:** 3,000 ticks (~3.9 ms per key); the yield capped at 4 ms. |
| 9 | should fix: gate the tick on the effective backlight and the wanted indicators; reset `last_out` on restart | **Done** (`display_backlight_effective()`, `indicators_any_lit()`, `ind_last_out`). |
| 10 | should fix: read back the mask in force and WFI entries; per-window counters; a data-off baseline for bit 4; delivery, not only board counts; for deep sleep, verify F19, take the after-count late, test a P0 column by hand | **Done** in the scripts: the applied mask and WFI rate, a health reset per window, the key count 1 s after the wake; bit 4 is left for a data-off run; F19 is in the restored VIA keymap; a P0 column (Esc's) is JD's press. Delivery at the Mac needs it unlocked. |

## Codex's implementation review (10-18) and what changed

[Verbatim](review-codex-sleep-impl-2026-10-18.md), prompt
[`review-prompt-sleep-impl.txt`](review-prompt-sleep-impl.txt), of
`9f46337b6f` and the scripts at `541fed3`: **nothing on the default-off path
that blocks scanning or costs 25 ms; four must-fixes before enabling the
experiments.** Fixed in `1486620cff` (firmware) and `dcd8899` (scripts).

| # | finding (short) | disposition |
|---|---|---|
| 1 | must fix: `restart_usb_driver()`'s 50 ms wait right after the wake holds the main loop while the waking key is down | **Fixed:** the transceiver bit (`USB_CFG.PHY_EN`) is set back under the lock instead, and recorded; a USB restart runs only if the host has not configured it 20 s after the wake. |
| 2 | must fix: UART2's stale receive data and errors, the parser's 3-byte window, and the serial output queue (the module's ACKs bypass the protocol queue) | **Fixed:** the receiver drained (`LS.RDR`, bounded) and its errors read off, the input queue reset, the parser's window reset (`ch582_rx_resync`); the output queue's emptiness is a precondition, rechecked under the lock. |
| 3 | must fix: the WFI probe (bit 2) can run forever if it breaks USB | **Fixed:** the whole mask ends by itself (240 s default, set per call); the transceiver bit is set back when PMU Sleep's window ends. |
| 4 | must fix: a failing cleanup step skips the rest; no way to cancel an armed deep sleep | **Fixed:** each step on its own, bounded (relay 20 s, `uhubctl` 15 s); op 14 sub 4 arg 2 cancels, and the scripts use it. |
| 5 | should fix: the daily build still carries the scan-only branch and the `sleep_task()` call | **Fixed:** `SN32F2XX_SLEEP_HOOKS`, defined only in dev builds; the call under `AK820_DEV_HOOKS`. |
| 6 | should fix: entry/exit transitions can reach ~8 ms of row gap, hidden by resetting after the settle; the period change before the ISR's lock | **Fixed:** the sizing run records the entry transition's gap separately (reported, not gated); the period change moved into the ISR's locked re-arm. |
| 7 | should fix: the LLD's GPT stop leaves a latched match; a deep wake restarts the tick behind light sleep's back; the idle WFI's mode can be lost to a preemption | **Fixed:** CT16B3's flags and NVIC pending cleared on stop; `ls_applied` reconciled after a wake; the idle hook's mode and WFI under one mask. |
| 8 | should fix: every RTC wake counted as a stray; unbounded clock-select waits; PWMD1's latched match on wake | **Fixed:** a second is not a stray; the waits bounded (a reset); PWMD1's flags and NVIC pending cleared before the re-arm. |
| 9 | should fix: also check SPI0/SPI1 and DMA idle, the serial output queue; recheck under the lock | **Fixed:** `STAT.BUSY` on both, SPI0's `DMACTRL.DMAEN`; the radio side rechecked under the lock. |
| 10 | should fix: the length check before reading arguments; the yield in the reply; the echoed op checked; the sizing run's stall baseline after the window; no deep verdict; entry not confirmed; `--cap` unvalidated | **Fixed:** all of them; the deep run waits for the logger's current to fall before pressing, and passes only on a key (press), the cap (no press), or a reset recorded in site 27 (unfed). Delivery at the Mac and a P0 column stay JD's part. |
| — | the locked section, the GPIO wake setup, the RTC and NVIC handling, the clock order, the ADC, SysTick, the RTC accounting, the protocol layouts: fine | Agreed. |

## Codex's verify of the implementation fixes (10-18)

[Verbatim](review-codex-sleep-verify-2026-10-18.md), prompt
[`review-prompt-sleep-verify.txt`](review-prompt-sleep-verify.txt), of
`1486620cff` and `dcd8899`: #2 (UART), 5-9 fixed; three must-fixes still
open. Fixed in `f38bb1e8a4` and the scripts commit after it.

| # | finding (short) | disposition |
|---|---|---|
| 1 | the 20 s USB-restart fallback still blocks 50 ms, later; moving the gap does not remove it | **Fixed:** no USB restart anywhere. The record says whether the host configured USB again within 20 s; if the transceiver bit is not the whole story, a power cycle restores USB (the keyboard types over the radio meanwhile). |
| 2 | the plain-WFI probe's expiry does nothing for USB | **Fixed:** the transceiver bit is set back when either WFI bit ends; the sizing run tries WFI in a 30 s probe (with the yield it needs: the idle thread runs only then) and stops if the board stops answering. |
| 3 | relay 1's in-process call has no deadline; the deep cleanup gives up on USB at once; "restored" is printed on relay 2 alone | **Fixed:** relay 1 in a thread with a 5 s deadline; the deep cleanup waits up to 30 s for USB; every step's result aggregated, and an incomplete cleanup fails the run. |
| — | the unfed verdict reads `site`, but the CLI emits `site_id: 27`; entry judged against the USB-on draw, before the delay ends; sizing has no boot continuity, allows `--n 0`, no final mask check | **Fixed:** `valid` and `site_id`; a data-off awake baseline and samples after the delay; uptime monotonic per entry, `--n` >= 10, the mask read back after the presses. |
| — | notes: the idle hook masks and unmasks even with WFI off; the parser resync check was unguarded in daily; the UART drain's 32-byte ceiling; short waking taps untested | The hook's mask is a few cycles in the idle thread (which does not run while the main loop spins); the resync now dev-only; the FIFO depth is undocumented, 32 covers any plausible one; the deep run now presses a 40 ms tap. |

## Codex's second verify (10-18)

[Verbatim](review-codex-sleep-verify2-2026-10-18.md), prompt
[`review-prompt-sleep-verify2.txt`](review-prompt-sleep-verify2.txt), of
`f38bb1e8a4` and `04e94db`: **no firmware blocker to flashing with sleep off,
or to the attended deep-sleep run; the WFI steps not yet hands-free.**
`f38bb1e8a4` went on at 18:24 10-18.

| # | finding (short) | disposition |
|---|---|---|
| 1 | must fix for hands-free sizing: if setting the transceiver bit back does not restore USB, nothing else can (relay 2 does not power-cycle a board on its pack) | **Accepted as attended:** the WFI steps (bits 2 and 4, the probe included) run with JD at hand, with the deep run. The hands-free run takes only scan-only and the yield, which do not touch USB. |
| 2 | must fix for hands-free sizing: keyboard HID calls in cleanup and the relay calls in the press loops are unbounded | **Fixed (`62b9b17`):** a whole-run deadline in each script, a timer that restores relay 2 (and the data on the deep run), bounded, then ends the process. |
| 3 | should fix: uptime increasing does not prove no reset; ACK timeouts and the timebase ratio only reported | **Fixed:** the uptime must keep pace with the wall clock (0.8 or better) and the consecutive-reset count must not move. ACK timeouts stay reported, judged against mask 0's. |
| — | note: byte 22 renamed; false inside 20 s of the wake can mean pending | **Done** (`usb_back_20s`); the deep run reads the record 8+ s after the press, so its false is read as "not yet" until 20 s. |
| — | verify #1's keystroke loss closed; the UART path, the smaller firmware fixes, the deep script's fixes, default-off and the daily build fine | Agreed. |

## Codex's third verify (10-18), and the board results

[Verbatim](review-codex-sleep-verify3-2026-10-18.md), prompt
[`review-prompt-sleep-verify3.txt`](review-prompt-sleep-verify3.txt), of
`53afebd553` (USB judged by its frame number; the flash blocker for a
program or erase only): **no must-fix; nothing blocks the flash or the
attended run.** Should fix: the wording, "no host" means no recent SOFs and
byte 22 means frames seen again, not configured USB (**accepted**, comment
only, for the next build); notes: the external SPI flash can stay busy after
its transaction (do not upload assets during a run), and the RGB's own dirty
flag is not a blocker (its flush is synchronous in the main loop; accepted).
`53afebd553` went on at 19:12 10-18.

**Measured** (`history/sleep-sizing-20261018-*`, `history/deep-sleep-20261018-*`):
- **Light sleep with WFI saves ~4.7 mA of the ~27.7 mA dark draw (17%)**
  with every bit (scan-only, the yield, WFI, the tick stopped); ~3.5 mA
  without the tick; WFI alone ~0.6 mA. Without WFI the steps cost up to
  ~1 mA. No press lost at any step. ⚠️ 5 UART overruns at mask 6 only.
- **Deep sleep: 3.2 mA asleep against ~27 awake (88% less), wakes on a key on
  either column port, and the waking key registers and reaches the Mac**
  (JD's "a", 19:42). The watchdog does not count asleep.
- At 3.2 mA the pack's ~4,000 mAh would last ~52 days asleep; at 27.5 mA,
  ~6 days (arithmetic, not measured).

## Decided (10-18, after the runs)

- **Deep sleep is the standby; light sleep is optional.** Deep sleep draws
  3.2 mA against light sleep's best 23.0 (the plain dark board ~27.7), keeps
  the waking key, and costs ~28 ms on the first key only. Light sleep
  remains worth ~3.5 mA only in the stage where the screen is still dimly lit
  (the backlight's software PWM needs the 20 kHz tick, which deep sleep
  stops); build it later, if at all.
- **Wake sources: a key, or the slider.** A keypress wakes it; moving the
  slider is a power-source change that resets the MCU anyway. The knob, a
  cable plugged in and the radio's messages do not need to wake it.
- So the build still needs, after a key wake: the radio resynced (its link
  state and the host's lock LEDs, whatever it sent while asleep being lost);
  and USB recovered if a host appeared while asleep (the charger is hardware
  and charges regardless, but the host's enumeration of a sleeping device
  may fail): reconnect the bus if host frames are seen and the device is not
  configured, in steps that never block the main loop.
- **The ladder with deep sleep, a first draft to try in use** (deep sleep
  starts at screen-off: the backlight's dim level needs the 20 kHz tick, and a
  lit screen would freeze):

  | mode | dim | lights off (screen dim) | screen off + deep sleep |
  |---|---|---|---|
  | Full | never | never | never |
  | Normal | 1 min | 5 min | 7 min (was screen off at 15, no sleep) |
  | Saver | 30 s | 2 min | 3 min (was 5) |
  | Minimal | at once | at once | 30 s (dark already; waits out a typing pause) |

  Low battery keeps Saver's timings. Each minute in the dim-screen stage
  instead of asleep costs ~25 mA-min; Normal's 2 min keeps a glance at the
  screen. To be tuned by how it feels in use.
- **Messages and the sleeping screen.** An Fn combo wakes the board with its
  first key (Fn pulls a column low like any key), and the rest is as today:
  in Normal and Saver a keypress resets the idle time, so the stage returns
  to active, screen and lights on; in Minimal the combo's overlay lights the
  screen 4 s, and it sleeps again after its 30 s. Two rules for the build:
  **no deep sleep while the backlight is lit** (a message's 4 s, `Fn`+`D`'s
  pages: the lit screen needs the 20 kHz tick), a hard precondition, not a
  timing accident; and a message that arises while asleep with no key
  ("Battery low", a link lost) waits for the next wake: the battery reading
  comes from the radio, which the sleeping SN32 does not hear.

## Asleep, the RTC's second runs ~13% slow (measured 10-19)

**The 1-hour run's record counted 3139 s for 3600 s asleep** (the logger's
1 s samples: the current fell at 05:44:32-33 and rose at 06:44:32-33):
0.872 of real time. The 10-18 cycle run says the same: the record's seconds against the
dwell over its 50 cycles fit a slope of **0.842**. The awake clock does not
do this (the SOF loop holds it to ms), so the RTC's second, clocked by the
ILRC, comes ~13-16% late while the chip is in Deep-sleep. Why is not known:
the ILRC's frequency depends on supply and temperature, and the manual says
nothing of it in Deep-sleep.

What it explains: each ~20 s soak cycle lost ~3 s, and the PCF discipline
snapped it back at its 2 s threshold, which is the soak's 16 syncs at -1.5 to
+2.1 s (not whole-second rounding, as first guessed). After the hour the
board woke ~461 s behind and the daemon found it 1.6 s off 12 s later, so
something caught it in between, presumably the PCF snap (not checked).

For the build:
- **Take the slept time from the PCF, not the RTC's count.** The PCF keeps
  its crystal running through the sleep; on the wake, the boot's PCF edge
  hunt (<= 1.5 s, +13 ms at a boot, `docs/clock.md`) sets the phase.
- **Keep slept seconds out of the PCF trim's window,** or it reads the ILRC
  as slow and mis-trims the awake period.
- **Everything else that counts slept seconds** (the cap, the ladder's idle,
  the gauge's sleep energy, QMK's timer) takes the corrected figure.
- `deep_cycles.py` now logs each cycle's wall-clock time asleep
  (`asleep_s`) beside the record's seconds.

## Time left with deep sleep (adopted 10-19, not built)

Today's figure (`battery_time_left_min`, `Fn`+`D`'s camera page) is the
level's energy over a 12 h exponential average of the draw. Deep sleep
breaks it twice:

- **The count has no sleep term yet** (on the build's list): the slept
  seconds must enter the energy at ~12 mW (3.2 mA at the pack), accounted
  on the wake, or the count misses the night.
- **With sleep counted, a 12 h average swings with the day.** Simulated, an
  unchanged routine of 8 h a day at JD's settings (~306 mW, the measured
  runtime's mean) and 16 h asleep (~12 mW): the mean is ~110 mW, ~5.9 days
  from full, but the average sits at ~56 mW at the morning's first key and
  ~178 mW at bedtime, so the figure reads ~11.6 days in the morning and ~3.7
  at night.

**Proposed: hours of use.** The level's energy over an average of the
awake minutes only; the slept seconds go into the energy, never the
average. It moves with how the board is used awake (lighting, brightness,
the ladder's idle stages), not with how long it sleeps: full at JD's
settings is ~51 h, and a 16 h night costs ~0.6 h of it. Asleep alone, the
pack lasts ~1,300 h (~7.7 weeks; no self-discharge counted). Open: whether
the bare `51h` reads as use or as wall clock (`100% 51h` fills the 8 cells).
A calendar figure ("~6d at your pace") needs the awake fraction learned
over several days (frozen on USB, like the average) and a second place on
screen; later, if wanted.

**Where it earns its place: deciding whether to plug in.** Today's warning,
"Battery low", fires at 3550 mV, ~2.3 h before the cut: too late to plan a
day around. A sketch, keyed on hours of use, not on the level (the same 20%
is ~10 h at JD's settings and ~28 h with the RGB off):

- **The battery row** shows the hours beside the level once under ~10 h of
  use (`18% 6h`; `No Batt` already fits 7 cells): absent while it does not
  matter, glanceable when it does.
- **A wake after a long sleep** (some hours: the morning, not every pause),
  while under that, lights `6h use left` in the text band for its 4 s (11
  of the overlay's 12 cells).
- Rounded down; the gauge is within 2.5 points below 20% (A1), ~1.3 h at
  JD's settings, before any change in how the next hours are used.

**Grading it in days, not months.** Time left is the energy left over the
draw to come, and each half can be checked on its own without running the
pack flat:

- **The energy:** the count against the INA228 on the pack; any logged
  hour grades it (A1 and R5 do this already).
- **The draw to come:** at each 15-minute entry of a dumped log (v6 keeps
  7.5 days of modelled energy), the average the firmware held then against
  the draw over the next 2, 8 and 24 h. Every dump of ordinary use is a
  test set, and `battery_sim` can replay the average itself. For that the
  log must carry each entry's slept seconds (a field for the build), or the
  awake-only average cannot be rebuilt from it.

Only how it feels (the wording, the moments it appears) needs living with.
