# Power and battery

How long the AK820 Pro lasts on this firmware, what each feature costs, how
to work out your own figure, and how all of it was measured. This is the
reader's summary. The lab notebook behind it, with every run, every
correction and the claims later withdrawn, is [battery.md](battery.md). The
bench is described in [test-bench.md](test-bench.md).

**Where the numbers come from.** One keyboard (JD's), one pack (made
2025-12), a room at 21-24 °C. Each figure is marked:
- **measured**: on the bench, against a current logger wired into the pack's
  lead;
- **modelled**: worked out from measured parts, but not yet checked as a
  whole.

Another unit, an older pack or a cold room will differ ("What we don't know",
below).

## The short version

There are really two numbers, and real use falls between them:

- **Hours of use** (awake, lights as you set them):
  - **50-52 h** with every LED at full white and the screen dim (measured,
    five drains);
  - ~46 h with everything at maximum (modelled);
  - ~100 h with the LEDs at half brightness (modelled);
  - ~6 days with every light off (modelled).
- **Days of standby** (asleep, untouched): **about 7 weeks** (modelled from
  a measured 3.2 mA asleep).

What a day costs is your hours awake at the first rate plus your hours asleep
at the second:

- **The office worst case:** 8 hours of nonstop typing a day at full white,
  asleep the rest. That is about 6 days on a charge, or a charge a week
  counting the weekend asleep (modelled).
- **JD's own logged use:** about 2¾ hours a day with a key pressed, in the
  default mode. That comes to roughly **two weeks** a charge (modelled, from
  two days of logged typing; not yet run as a drain).

The rest of the short version:

- **The LEDs are most of the cost:** 62% of the draw at full white. At half
  brightness they cost a quarter as much.
- **The pack holds 15.6 Wh** to the point where the lights go out (measured),
  a little more than its label's 14.8 Wh.
- **When the lights go out (0%), it still types for about 5 more hours**
  (measured once).
- **A full charge takes 9-10 hours** (measured).

⚠️ **The standby figures assume deep sleep works.** On the newest dev builds
it does not over 2.4G ("Known issues", below).

## The battery

- **The cell:** `JKJ606090` from Shenzhen Jiekejie, date code `202512.30`.
  - **Label:** 3.7 V, 4000 mAh, 14.8 Wh, 72.9 g.
  - **Chemistry:** NMC (Li/Ni/Co/Mn with a graphite anode), per the label.
- **Measured from full to the RGB cut: 4,011.6 mAh and 15,579 mWh**, over
  50.39 h at JD's settings (one logged drain, 10-15 to 10-18). That is more
  than the label claims, before counting the reserve.
- **The voltages that matter:**
  - **Full:** ~4.19 V when the charger stops.
  - **"Battery low":** 3.68 V.
  - **0%, the RGB cut:** 3.40 V. The LEDs go off; typing goes on.
  - **The end:** about 5 h after the cut, somewhere below ~3.16 V. The board
    cannot see below that, and the voltage it dies at is unknown.
- **The connector:** 3 pins, 1.0 mm pitch, JST SH or compatible (+, −, and
  the cell's NTC thermistor).
- **The charger:** an ASC4056, a TP4056-class linear charger
  ([datasheet, in Chinese](https://github.com/fpb/ajazz-ak820-pro/blob/main/docs/ASC4056.pdf)).
  On this board it charges at ~560 mA.
- **The protection board:** not identified. Other makers' 606090 4000 mAh
  packs cut off at 2.75 V or 3.0 V
  ([one example](https://www.lipobattery.us/wp-content/uploads/2021/04/LP606090-4000mAh.pdf)),
  but that describes the genre, not this part.

**Why the keyboard needs its radio to read its own battery.** The MCU (a
Sonix SN32F299,
[manual](https://github.com/fpb/ajazz-ak820-pro/blob/main/docs/SN32F299_V1.8_EN.pdf))
runs from a buck-boost converter that holds 3.90 V whatever the pack does,
and no analogue pin reaches the pack. The Bluetooth module (a WCH CH582F,
[product page](https://www.wch-ic.com/products/CH583.html)) sits on the pack's
own rail. It reports a byte the stock firmware showed as a percentage, which
turns out to be a linear voltmeter, good to ~10 mV against a meter.
[battery.md](battery.md) has the whole story.

## How long it lasts

### Measured: full white, from full to the cut

All at full white (value 255, saturation 0) with the screen at level 5 of 23,
on Bluetooth, and with JD typing as usual:
- **51.55 h** (09-28 to 10-01, run 1, in full-white equivalent):
  [`history/battery-2026-09-28-drain/`](../history/battery-2026-09-28-drain/).
- **50.56 h**, then **5 h 01 min** of lights-off typing to the end (10-02 to
  10-04, run 2): battery.md, "The second run".
- **51.13 h** (10-09 to 10-11): battery.md, "Accuracy over a whole cycle".
- **50.1-50.4 h** (10-12 to 10-14):
  [`history/battery-2026-10-12-drain-2e/`](../history/battery-2026-10-12-drain-2e/).
- **50.39 h**, logged to the milliamp-hour (10-15 to 10-18):
  [`history/battery-2026-10-15-drain-2f/`](../history/battery-2026-10-15-drain-2f/README.md).

The last two had the bench's USB data cable attached, which costs ~2.3%.
Untethered, the logged drain would have run ~51.5 h.

### Modelled: one state, held

Each figure is that state's measured draw over the measured 15,579 mWh, run to
the RGB cut, with no USB data attached:

| state | draw | per hour | runtime |
|---|---|---|---|
| Everything at max: full white, screen full, 2.4G | 340 mW | 2.2% | ~46 h |
| Full white, screen at level 5 | 300 mW | 1.9% | ~52 h |
| White at value 128 | 154 mW | 1.0% | ~4 days |
| White at value 64 | 129 mW | 0.8% | ~5 days |
| LEDs off, screen at level 5 | 111 mW | 0.7% | ~6 days |
| Every light off, awake | 107 mW | 0.7% | ~6 days |
| Asleep | 13.4 mW | 0.09% | ~49 days |

"Everything at max" adds 2.4G's ~5 mW, which is one reading of the board with
its lights off ("Where the power goes", below). 2.4G with the lights on has
not been measured.

### Modelled: a day of use

**Days per charge = 15,579 mWh ÷ (awake mW × awake hours + 13.35 mW × asleep
hours).** The minutes the board spends dimming before each sleep add well
under 1% a day.

The office worst case, 8 h of nonstop typing a day and asleep the other 16:
- **full white, screen dim:** ~16.8% a day, **~6 days**. A five-day week plus
  a weekend asleep uses ~88%: a charge a week;
- **everything at max:** ~18.8% a day, ~5.3 days;
- **white at value 128:** ~9.3% a day, ~11 days;
- **Lights out mode** (dark while typing): ~6.9% a day, ~15 days;
- **Always on mode** never sleeps, so a day is 24 lit hours: ~2.2 days at
  full white.

### Modelled: the power modes at JD's use

From two days of JD's logged typing (10-15 to 10-18: a key in 11.5% of the
5-minute intervals, about 2¾ h a day; six of the nine idle stretches lasted
2 h or more), at full white with the screen at level 5:
- **Always on:** ~300 mW on average, ~2.2 days.
- **Balanced** (the default): ~49 mW, **~13 days**.
- **Power saver:** ~47 mW, ~14 days.
- **Lights out:** ~24 mW, ~27 days.

**The method.** Every 5-minute interval with a key in it counts as fully lit,
which charges short pauses at full draw (pessimistic). Each idle stretch then
steps through the mode's stages at their measured draws. Moving every
stretch's length by ±5 minutes (the log's resolution) changes the result by
about 1%. The logged drain in Balanced (R5,
[`plans/POWER-CAMPAIGN-PLAN.md`](../plans/POWER-CAMPAIGN-PLAN.md)) will
measure what this models.

**Balanced and Power saver come out within ~4% of each other.** Nearly all of
JD's idle time is in stretches of hours, and both modes spend those asleep.
What costs is the lit typing. Power saver dims and sleeps sooner, which saves
~9 mWh an idle stretch (0.06% of the pack). That shows only with many short
breaks: at 4 h of typing and twenty 15-minute breaks a day, Power saver lasts
~9.7 days against Balanced's ~8.8, about 10% longer.

## Where the power goes

Measured on the pack at ~4.17 V. Each figure is added to the base, the board
awake with every light off:

- **The base, awake and dark, BT linked: 27.3 mA, ~107 mW.**
  - It comes from the MCU, the radio, the panel's logic and the regulator,
    and nobody has split it further ("What we don't know").
  - The MCU never idles while awake: the LED matrix's row interrupt takes 73%
    of its time, dark or lit.
- **The LEDs at full white: +45.2 mA, ~189 mW.** That is 62% of the total at
  full white.
  - **At value 128: +10.3 mA; at value 64: +4.3 mA.** QMK's CIE 1931 curve
    maps value 128 to a drive of 48 of 255, and 64 to 12. The draw follows
    the drive, not the value, so the top of the brightness range is where the
    power goes.
  - **Colour matters.** Per unit of drive, red costs 1.29× the average, green
    0.90× and blue 0.81×. A saturated purple at full brightness drew 212 mW
    in all, against white's 303.
  - **An animated effect costs what its frames average.** An animated colour
    cycle drew 215 mW.
- **The screen's backlight: +9.4 mA (~39 mW) at full**, linear in its duty
  (level 23 is 48 of 48; level 5 is 5 of 48, ~1 mA). At a dim level the screen
  is ~1% of the total. Its drawing, as distinct from the light, sits in the
  base.
- **USB data attached** (the cable in, on battery): +1.7 mA, ~7 mW. That
  happens only on a bench: in use, a cable in means the board runs from USB.
- **2.4G instead of Bluetooth: about +1.2 mA** awake and dark (one reading
  each). After each sleep starts, the 2.4G module draws ~15 mA for ~5 s more.
- **Deep sleep: 3.2 mA on BT (13.25 mW), 3.3 mA on 2.4G (13.8 mW)**, an
  hour of each.

**It is a constant-power load.** Behind the buck-boost the board draws the
same milliwatts at any pack voltage, so its current rises as the pack falls:
73.6 mA at 4.16 V, ~90 mA just before the cut. Count in milliwatts and
milliwatt-hours, not milliamps.

## Working it out yourself

The firmware carries this model to count the battery down. Against the logger
it was within 2% at all 24 steps of the ladder's stages, and within 0.6% at
every step of the LED brightness and colour sweep:

- **base: 106.9 mW**, awake (deep sleep: 13.35 mW in all);
- **plus the backlight: 39.1 mW × duty / 48** (the duty for each level is in
  [display.md](display.md), "Backlight");
- **plus, while any LED is lit, 8.64 mW, and for each LED:**
  - **3.636 µW per unit of red drive,**
  - **2.533 µW per unit of green,**
  - **2.294 µW per unit of blue.**

  Drive is the value after QMK's CIE curve, 0-255 a channel.

For white at one brightness, the LEDs come to about **8.6 + 0.71 × drive
mW**: 189 mW at drive 255, 43 at 48 (value 128), 17 at 12 (value 64).

**A worked example:** white at value 128, the screen at level 8, the default
mode, 4 h of typing a day:
- **awake:** 106.9 + 6.5 (backlight, 8 of 48) + 42.7 (LEDs) = 156 mW, × 4 h
  = 624 mWh;
- **the ladder before each sleep,** say three a day: ~7 min at ~125 mW each,
  ~44 mWh;
- **asleep:** 20 h × 13.35 mW = 267 mWh;
- **total:** ~935 mWh a day, so 15,579 ÷ 935 = **~16.7 days**.

## The power modes

`Fn`+`B` cycles four modes, shown on battery as a bulb on the battery row.
They apply on battery only; on USB the board stays fully on.

| | Always on | Balanced | Power saver | Lights out |
|---|---|---|---|---|
| LEDs dimmed (value capped at 64) | never | 1 min idle | 30 s | -- |
| LEDs off, screen capped at level 4 | never | 5 min | 2 min | always |
| screen backlight off | never | 7 min | 3 min | always |
| deep sleep | never | 7 min | 3 min | 30 s |

- **Balanced is the default.**
- **Below 15%, the lighting follows Power saver's stages in every mode**
  (Lights out stays darker), and Balanced sleeps at Power saver's 3 minutes.
  Always on still never sleeps.
- **Lights out keeps the LEDs and the backlight off even while typing.** It
  lights the screen for 4 s when a message appears.
- **A key or the knob brings everything back** within a tenth of a second,
  and the key types. Each stage only caps the lighting; your settings are
  never changed.
- **Waking from deep sleep keeps the waking key.** It was never lost in over
  350 measured wakes. The key reached the Mac a median 64 ms after the press on
  BT (worst 115 ms), and 48 ms on 2.4G (worst 56).
- **What wakes it:** a key, the knob, a charger plugged in, or 24 hours
  untouched.
- **What keeps it awake:** any light still lit, charging, USB power, a key
  held down, the radio pairing.

The design and its measurements: [battery.md](battery.md), "The idle ladder
and the power modes", and [hardware.md](hardware.md), "Deep sleep".

## Charging

- **Constant current at ~560 mA** for the first ~2.9 h (about 40% of the
  charge), then a long taper: 486 mA at 3.9 h, 283 mA at 7.9 h, 131 mA at
  9.4 h. The charger stops at ~80 mA.
- **9-10 hours from empty:**
  - 9.4-9.5 h from flat (09-28 and 10-01);
  - 9.1 h from the cut (10-09);
  - 9.90 h from the cut, logged (10-18): 3,972 mAh in, against ~4,012 out.
    The two are equal within the measurement's error.
- **It charges in any slider position with the cable in.** The board runs
  from USB while the pack charges.
- **The percentage while charging comes from a model,** bounded by the pack's
  voltage. The charge current inflates the voltage, so it cannot be read
  directly. Full is when the charger stops.
- **After flashing firmware** the level reads "Charge" on USB until the pack
  is full or has been unplugged for 30 minutes. A flash erases the saved
  level.

## The gauge on the screen

- **How it works.** The firmware counts its own modelled draw off the level,
  second by second. The pack's voltage, from the radio's voltmeter, keeps the
  count inside a band. On the flat middle of the curve, 15 mV spans 15
  points, so the voltage alone read up to 13.7 points low there.
- **How good it is.** Replayed through two logged drains, the count stays
  within ~2 points at every second (modelled). It has not yet been checked
  live across a drain with the modes stepping the load (R5).
- **Time left means hours of use** at your recent awake draw, not calendar
  time.
  - The battery row shows it under 10 hours (`18% 6h`).
  - A wake after 2 h or more asleep says `6h use left` once.
  - `Fn`+`D`'s camera page shows it always.
- **An aged pack reads high.** The firmware assumes 15,579 mWh. A pack holding
  13% less would read up to ~6 points high late in a charge (simulated).
- **macOS's Bluetooth battery percentage is not this gauge.** It shows the
  radio's own linear reading, which runs far higher (96% against 66% on
  10-10).
- **The messages:**
  - "Battery low" at 3.68 V;
  - "Saving power" once a discharge, below 15%;
  - 0% at the RGB cut, where the LEDs go off.

  `flash.sh` refuses to flash on battery below the cut.

## Standby, storage and off

- **There is no off position.** The slider is BT / 2.4G / cable. In the
  wireless positions the pack stays connected, and with the cable out it
  powers the board ([hardware.md](hardware.md), "The mode slider").
- **Left alone on battery, it sleeps** after its mode's idle time, at ~13 mW:
  about 7 weeks from full, waking once a day on its own (modelled; Always on
  never sleeps).
- **Truly off:** slider to cable with the cable out. The board is then
  unpowered, but the pack still feeds its protection board and the charger
  chip, and what they draw has not been measured.

## Known issues

- ⚠️ **Deep sleep over 2.4G fails on the newest dev builds** (`b2a07e0d37`,
  found 10-20). Each sleep ends after about a second, and the board sleeps
  again ~3 s later, so idle draw would be ~75 mW rather than ~13 (an
  estimate). At JD's use that roughly halves Balanced's runtime.
  - It is being chased in
    [`plans/DEEP-SLEEP-REGRESSION-2026-10-20.md`](../plans/DEEP-SLEEP-REGRESSION-2026-10-20.md).
  - The sleep figures in this document come from the builds before it
    (`83d8768c64`, `7387d18947`), where sleep held on BT and 2.4G alike.
- **One unexplained shutdown (09-24).** After a few hours on battery the board
  went dark and came back only with the cable, though the pack was far from
  flat. It has not happened since.
  - A short at the pack connector does the same: the pack's protection trips.
    A slider flip to cable and back, with no cable, brought the board back
    that time.
  - battery.md, "The shutdown of 2026-09-24".
- **One unexplained wake in an hour asleep (10-19):** ~3 s awake, then asleep
  again. It cost nothing measurable, but the cause is not known.

## What we don't know

### With a PPK2

The bench's INA228 is good to ~1% on milliamp loads and averages over
~200 ms. It cannot see microamp floors or millisecond bursts. Nordic's
[Power Profiler Kit II](https://www.nordicsemi.com/Products/Development-hardware/Power-Profiler-Kit-2)
resolves well under a microamp, samples fast enough to see the radio's
bursts, and can stand in for the pack at a set voltage. It measures current
one way only, so charging work stays on the INA228.

- **What "off" costs.** Slider at cable with no cable leaves the pack feeding
  its protection board and the charger chip. That is probably microamps, and
  it sets the shelf life.
- **What the 3.2 mA asleep is made of.** The candidates:
  - the MCU in Deep-sleep;
  - the radio holding its link (connection events, which are bursts the
    INA228 averages away);
  - the buck-boost's own quiescent current;
  - the panel's controller;
  - the matrix's pull-ups.

  Which one dominates decides the next step for long absences, for example
  dropping the link overnight (a guessed 0.1-3 mA).
- **The radio's other states.** Only "linked and idle" is measured, not:
  - advertising;
  - reconnecting;
  - a host that is asleep or out of range;
  - BT against 2.4G in each state;
  - what the connection interval costs.
- **A wake, at microsecond scale:** the energy of one wake, and its timing,
  with a logic input stamping the key press against the radio's first
  packet.
- **Peak currents:** the inrush at a wake, and the LED matrix's pulses. These
  matter if the pack's over-current protection is what tripped on 09-24 (a
  hypothesis).
- **The low end in minutes instead of days.** With the PPK2 standing in for
  the pack at set voltages:
  - the buck-boost's efficiency across the pack's range;
  - the voltage where the board browns out;
  - the radio's voltmeter near its floor.

### With bench time only

- **R5, a drain in Balanced at real use.** It measures the modes' runtimes
  modelled above, the sleep's energy across a whole drain, and the gauge live
  under a changing load. At JD's use, probably about two weeks.
- **2.4G with the lights on,** and everything at maximum measured directly: a
  load map of about 15 minutes.
- **What ends the reserve:** whether the board's regulator or the pack's
  protection gives out first, and at what voltage. This is R5 run on past the
  cut, about 5 h more.
- **The awake base's split.** The 27 mA is shared between the MCU, the radio,
  the panel and the regulator. A light-sleep test measured the MCU's share at
  ~4.7 mA, about 17%; light sleep is not built.
- **Typing's own cost:** the radio's energy per keystroke is inside the
  drains' averages, not separated.

### With time, or other hardware

- **Temperature.** Every run was at 21-24 °C. Cold costs capacity and moves
  the voltage curve.
- **Ageing.** The capacity is one fresh pack's. A logged drain every few months
  would show the fade, and tell the firmware's fixed capacity how far off it
  has drifted.
- **Other units and packs.** The voltmeter's line, the capacity and the curve
  all come from one keyboard and one pack.
- **The protection board's chip.** It is under the tape at the pack's lead
  end, and its markings would give the trip voltages and currents.
- **Self-discharge** over weeks of storage.
- **The stock firmware's draw**, for comparison: never measured.

## Appendix A: how it was measured

### The bench

- **The pack logger** ([test-bench.md](test-bench.md), "The power logger"):
  - **Current:** an INA228
    ([datasheet](https://www.ti.com/lit/ds/symlink/ina228.pdf);
    [Adafruit 5832](https://www.adafruit.com/product/5832)) high-side on the
    pack's + lead. Its 15 mΩ shunt is a 1% part, and it resolves 5 µA. Its
    charge register integrates without gaps between samples, so mAh comes
    from the register, not from summed samples.
  - **The pack's voltage:** a MAX17048
    ([product page](https://www.analog.com/en/products/max17048.html);
    [Adafruit 5580](https://www.adafruit.com/product/5580)), good to
    ±7.5 mV. The INA228's own bus voltage reads ~5% low and is not used.
  - A QT Py RP2040 ([Adafruit 4900](https://www.adafruit.com/product/4900))
    streams both to the Mac at 1 Hz (`scripts/power_log.py`).
- **Power and data, from the Mac:**
  - relay 2 cuts the keyboard's USB 5 V while the data stays connected, so
    the Mac can read the board on battery;
  - `uhubctl` cuts the data;
  - relay 1 presses a key, the wake rig.
- **The error budget:**
  - current scale ±1% (the shunt);
  - the zero −0.064 mA with nothing flowing;
  - the voltage ±7.5 mV;
  - the radio's voltmeter ~10 mV.

### Load maps

- **The method** (`scripts/current_map.py`, on battery):
  - each state held 60-90 s, the first 15 s skipped;
  - stepped forward, then backward;
  - each state's current from the charge register;
  - the lighting set in RAM only and the backlight through a dev hook, both
    restored after.

  The two passes agree within 0.03-0.6 mA.
- **The maps:**
  - **the first map, R1, 10-15:** ten states;
    [`history/power-bench-2026-10-15/current-map-20261015-2210/`](../history/power-bench-2026-10-15/current-map-20261015-2210/README.md);
  - **USB data on and off, R2:**
    [`usb-data-20261015/`](../history/power-bench-2026-10-15/usb-data-20261015/);
  - **the LED curve, 10-19:** white at 16 values, then red, green and blue at
    three;
    [`rgb-curve-20261019/`](../history/power-bench-2026-10-15/rgb-curve-20261019/README.md);
  - **the ladder's stages, 10-19:** 24 steps;
    [`stages-20261019/`](../history/power-bench-2026-10-15/stages-20261019/README.md).

### Drains and charges

- **`bench_trial.py`, with `trial_guard.py` watching it:**
  - from a verified full (the charger's own stop), relay 2 off at a logged
    moment;
  - the board read every 5 minutes;
  - to the RGB cut as the logger sees it, a ~50 mA step;
  - then the relay on and a charge to full.

  The guard restores power if the trial stops making progress.
- **The drains before 10-15 had no logger.** The board's own readings timed
  each cut, and run 2's end was timed from a phone's time-lapse of the
  screen.
- **The logged drain lost 1.2 h of the logger's data** to a mains outage, but
  the keyboard ran on through it. ~101 mAh was bridged at the measured
  current, an estimate worth ~2.5% of the total.

### The firmware's model, checked

- **Stages:** within 2% at all 24 steps; LEDs per colour within 0.6%.
- **The gauge:** `scripts/battery_sim` replays the gauge's own firmware on
  the host, at one-second steps, through both logged drains (148 cases).
- **Sleep:**
  - an hour asleep on BT drew 13.25 mW against the model's 13.35;
  - the board's own count of its slept seconds came within 1.5 s of the
    logger's over an hour
    ([`history/ladder-sleep-20261019-hour2/`](../history/ladder-sleep-20261019-hour2/README.md)).

## Appendix B: where each number comes from

- **Capacity, energy, the logged runtime, the charge:**
  [`history/battery-2026-10-15-drain-2f/`](../history/battery-2026-10-15-drain-2f/README.md).
- **The earlier runtimes and the reserve:** battery.md, "The second run",
  "Accuracy over a whole cycle" and "The reserve below 0%".
- **Each feature's draw:** the load maps above.
- **Deep sleep:**
  - [`history/deep-sleep-20261019-1h/`](../history/deep-sleep-20261019-1h/README.md)
    (BT);
  - [`history/ladder-sleep-20261019-hour2/`](../history/ladder-sleep-20261019-hour2/README.md)
    (2.4G).
- **2.4G awake against BT:**
  [`history/deep-cycles-20261019-2g4/`](../history/deep-cycles-20261019-2g4/README.md).
- **Light sleep's possible saving:**
  [`history/sleep-sizing-20261018-wfi/`](../history/sleep-sizing-20261018-wfi/README.md).
- **JD's idle pattern:** `guard-activity.csv` in the logged drain's folder
  (the power campaign's A8).
- **The model's constants:** `battery.c` in the firmware ("The energy
  count").
- **The charger's pins and the timed charges:** battery.md, "The charger".
- **Claims made and withdrawn:** battery.md, "Claims this document made and
  withdrew".
