# Power mode icons and names — on the board (2026-10-19)

An icon for the power mode in the battery row, and friendlier mode names.
Designed with pixel-accurate mockups (the firmware's own geometry and the
shipped Iosevka 20 px atlas). The colours are starting values to try on the
panel, not decisions.

## Status

**On the board since 13:18 10-19: `26fcafaeca`** (dev, token `0x47773c72`),
cleared by codex (`61a425a721`, the follow-up `6926a17c80` and a comment
fix, on `7df8a08e96`). The deep-sleep session fast-forwarded
`ak820pro-jdlien` to it, flashed it and soaked it: 97 of 100 deep-sleep
cycles through the ladder, the three failures UART overruns that are the
deep-sleep session's
([`../history/deep-cycles-20261019-icons/`](../history/deep-cycles-20261019-icons/README.md)).
It carries `7df8a08e96`, battery.c's guard against a historical debit for a
sleep on USB (codex's own fix, reviewed with this branch). The docs are
updated (display.md's battery row; battery.md, hardware.md, test-bench.md
and CLAUDE.md's Fn line name the modes). **Next: the bulb's shape and
colours on the panel** ("On the panel", below).

- Compiles clean, dev, daily and fpb: +1,016 bytes (the art is 784).
- `scripts/battery_sim` passes against both firmware trees (`ladder.c`
  takes the names from `POWER_HAS_BULB`). Through `BATTERY_SIM_SRC` both
  give 146, the same list line for line; a plain run gives 150 because it
  adds the four pinned-baseline checks (flash 1's `battery.c`, which never
  reads the tree under test), and the variable skips them. New checks
  in `l_modes` and `l_low_cap` catch three mutants of `power_lights_mode()`
  (low ignored, everything Saver when low, Lights out taken to Saver).
- `hostagent/ak820battery.py` prints the new names (display only; the
  indices are unchanged).

Done: codex's review and verify (no must-fix, below); the flash; the gate's
Fn+B part on the pack (below); the docs. Left: the shape and colours on the
panel ("On the panel", below), then a flash of whichever wins.

## The names

| Now | New | Cells | On battery |
|---|---|---|---|
| Full power | Always on | 9 | never dims, never sleeps |
| Normal power | Balanced | 8 | dim 1 min, lights off 5, screen off and asleep 7 |
| Power saver | Power saver | 11 | dim 30 s, lights off 2 min, screen off and asleep 3 |
| Minimal mode | Lights out | 10 | lights and screen off even typing; asleep after a 30 s pause |

All fit the overlay's 11 cells (`TEXT_BIG_MAX`). The names live in:
`power.c` (`mode_names`); `hostagent/ak820battery.py` (`POWER_MODES`); the
overlay checks in `scripts/battery_sim/ladder.c` ("Normal power", "Minimal
mode"); `via.json`'s Fn+B title, which still says "Full / Normal / Saver" and
misses Minimal; the `PWR_MODE` comment in `ak820pro.h`; the docs (CLAUDE.md's
Fn table, battery.md, display.md). The enum identifiers can stay.

## Where the icon goes: the bolt's slot, on battery only

- The slot right of the battery (`draw_bolt_slot`, 12×14 at x 35, y 109) only
  ever holds the charging bolt or the no-pack cross. Both need the cable, so
  **on battery the slot is always empty**.
- **The modes only act on battery** (`power_task`: with the cable in, every
  mode is Always on). An icon on USB would describe something the board is not
  doing.
- So they share the slot: **on battery the mode's icon; on USB the bolt while
  charging, else blank.** Nothing new goes on the screen.
- The mode can still be changed on USB: `power_cycle_mode()` has no battery
  check; it saves the mode and names it in the overlay. The icon appears at
  unplug.
- **Balanced shows its icon.** A blank default would break the ladder of
  bulbs, and blank already means USB.
- **Low battery shows Power saver's icon**, whatever the mode, while the
  lights follow Saver's timings (`LOW_CAP_PM`): the icon says what the board
  is doing.

## The glyphs: one bulb losing its light

13×14 frame, placed at (35, 108): the rays reach y 108 and x 47, the bulb
sits at y 111-121, bottom-aligned with the battery (y 110-121). `#` is the
main colour, `*` the dimmed glass, `+` the screw base.

```
Always on       Balanced        Power saver     Lights out
......#......   .............   .............   .............
.#.........#.   .............   .............   .............
..#.......#..   .............   .............   .............
.....###.....   .....###.....   .....###.....   .....###.....
...#######...   ...#######...   ...##***##...   ...##...##...
##.#######.##   ...#######...   ...#*****#...   ...#.....#...
..#########..   ..#########..   ..#*******#..   ..#.......#..
..#########..   ..#########..   ..#*******#..   ..#.......#..
..#########..   ..#########..   ..#*******#..   ..#.......#..
...#######...   ...#######...   ...#*****#...   ...#.....#...
....#####....   ....#####....   ....#***#....   ....#...#....
.....+++.....   .....+++.....   .....+++.....   .....+++.....
.....+++.....   .....+++.....   .....+++.....   .....+++.....
......+......   ......+......   ......+......   ......+......
```

Starting colours, RGB565:

| | `#` | `*` | `+` |
|---|---|---|---|
| Always on, Balanced | white `0xFFFF` | — | grey `0x8C71` |
| Power saver | white `0xFFFF` | mid grey `0x8410` | dark grey `0x4228` |
| Lights out | light grey, to try | — | dark grey `0x4228` |

Power saver's white outline over grey glass reads as dimmed, and grey already
means "not in use" on the connection icons. Its base is darker than the
others so the grey glass does not run into it at the neck.

## Colours to try on the panel

- ⚠️ **Mid grey `0x8410` was "near-illegible" as 5×7 text on this panel**
  (`COL_AMPM`'s comment, `graphics/display.c`). The glass is a filled area and
  should survive, but try `0x8410` against `0xA514` side by side.
- **Lights out's outline is the riskier one**: a 1 px stroke, the kind that
  failed. Use a light neutral grey (`0xA514` or `0xC618`), not the mockup's
  slate: slate would be the only blue on the screen, and the missing fill
  already tells it from Power saver.
- **Keep yellow, red and amber out of the slot.** Yellow is the bolt's colour
  in the bolt's place; red and amber mean a low battery on this row.
- **No green tint on Power saver's glass.** Green already means a healthy
  battery (the fill beside it) and playing (the transport icon).

## On the panel (first look, 10-19)

On the board the bulb reads very small, and **the screw base barely shows**:
it needs to be lighter, at least Power saver's glass grey. Viewed at an
angle, the screen cover's bezel clips the slot a little; not one for
software yet.

Why the base vanishes: it is 7 pixels (3 wide, two rows and a tip) under
56 of white glass, grey, and touching the glass, so it reads as the glass's
shading. Under the dimmed and unlit bulbs it is `0x4228`, near black. A
lighter grey alone would not fix it: Power saver's glass runs straight into
the base at the neck (`....#***#....` over `.....+++.....`), so a base in the
glass's grey merges with it. One-colour icon sets solve this with a blank
line between glass and base, and the base in the outline's colour.

Two versions, each with that black row and a 5-wide base in the outline's
colour (white; `0xA514` under Lights out):

- **Separated base, 13×14 as now:** the glass gives up its third 9-wide
  row. No change to the tile.
- **Wider bulb, 15×15 at (35, 107) (recommended):** the glass keeps its 8
  rows and grows from 9 to 11 wide, about a fifth more glass; the tile grows
  from 13×15 to 15×16 at (35, 107) (480 bytes against 390, ~30 µs more on
  the wire). Its right edge, x 49, leaves 4 columns before time left's
  furthest reach (x 54). It moves no lower; its top rises a row.

```
Always on (wider)  Balanced           Power saver        Lights out
.......#.......    ...............    ...............    ...............
.#...........#.    ...............    ...............    ...............
..#.........#..    ...............    ...............    ...............
.....#####.....    .....#####.....    .....#####.....    .....#####.....
##.#########.##    ...#########...    ...##*****##...    ...##.....##...
..###########..    ..###########..    ..#*********#..    ..#.........#..
..###########..    ..###########..    ..#*********#..    ..#.........#..
..###########..    ..###########..    ..#*********#..    ..#.........#..
...#########...    ...#########...    ...#*******#...    ...#.......#...
....#######....    ....#######....    ....#*****#....    ....#.....#....
.....#####.....    .....#####.....    .....#####.....    .....#####.....
...............    ...............    ...............    ...............
.....+++++.....    .....+++++.....    .....+++++.....    .....+++++.....
.....+++++.....    .....+++++.....    .....+++++.....    .....+++++.....
......+++......    ......+++......    ......+++......    ......+++......
```

The wider tile changes the rect codex checked (its item 2: nothing else
draws into x 35-49, y 107-122, and every clear of the band repaints the
slot). That needs re-checking before it flashes; the separated base does
not, since its rect is unchanged.

## Drawing it without a stall

Painting this band has stalled before ("Not every band is queued", and the
bolt's 19 ms runs, display.md), so:

- **The icon is another state of the same RAM tile**, sent with the same one
  `lcd_blit_ram()` as the bolt and the cross. Never runs, never a separate
  clear. Cost should be the bolt's: 11-12 ms passes at a plug-in on flash 2c.
- **The tile grows from 12×14 at (35, 109) to 13×15 at (35, 108)** for the
  rays: 195 pixels against 168. The bolt and the cross move down a row inside
  it to stay where they are.
- **Its triggers mark `BATT_DIRTY_BOLT` and nothing else**: the mode, on
  battery or not, the low cap, plus charging and presence as now. It stays one
  component of `draw_battery()`'s one-per-pass painting, so the Fn+D exit's
  staged restore and the wake's repaint hold need no change.
- **Fn+B paints twice**: the overlay's name in the text band and the slot.
  ⚠️ **They can share a pass** (codex): the battery flag separates battery
  components only. The overlay's glyphs are queued, but its clears (a
  retiring or shortening text) are synchronous, and a lock change can land in
  the same pass too. The tile should cost a few ms (the 12×12 transport icon
  measured 3.4 ms; 390 bytes are ~134 µs on the wire); it is not measured
  yet, and a coincident 20-22 ms Caps paint has little margin.
- **The gate**, with a track playing (display.md): about 20 Fn+B cycles,
  some with Caps and Fn changing in the same moment, plug/unplug cycles on
  the relay, a Fn+D exit and a deep-sleep wake. `count_ge_25ms` stays 0, and
  note `blit_gap_max`. The bench cannot press Fn+B (op 14 sub 1 sets a mode
  without the overlay), so the Fn+B part waits for hands; the soak covers the
  wakes and the plug cycles.
- **Measured on `26fcafaeca`, on the pack (10-19,
  [`../history/fnb-caps-20261019-jd/`](../history/fnb-caps-20261019-jd/README.md)):**
  30 Fn+B presses by hand, the band's FN label changing around each (JD's Fn
  sits where Caps Lock was): **no pass of 25 ms or more**, the worst 20-21 ms
  (`display_hk` 13). Caps Lock toggled from the Mac ~600 times, no Fn+B:
  one pass of exactly 25 ms, on a blit (`blit_gap_max` 25), the CAPS paint
  the lock band's notes already suspected; a control of 199 toggles had none.
  So Fn+B and a Caps change in the same pass were not separated from the
  CAPS paint's own 25 ms; that paint is its own item (current-status.md).
  The deep-sleep soak on this build covered the wakes.

## Codex's review (2026-10-19)

[`review-codex-power-icons-2026-10-19.md`](review-codex-power-icons-2026-10-19.md),
of `61a425a721` with `7df8a08e96`: **no must-fix.** Dispositions:

1. Tile geometry, bounds, colours, the transfer: fine. The bolt and the
   cross land on the same pixels as before.
2. Overlap: fine. Nothing clears only the old 12×14 rect.
3. Note, taken: **a mode change reached the slot at the next RTC second**,
   up to a second after Fn+B's overlay. Fixed in `6926a17c80`: the 10 Hz
   tick calls `draw_battery()` when a bulb is up and `power_lights_mode()`
   has moved (`batt_bulb_stale`). Only a mode change, so supply and CHRG
   edges keep their 1 Hz cadence and an unsmoothed CHRG repaints no faster
   than before. The same fix shrinks the wake's case (the low cap crossed
   after the wake's repaint released the hold) to one tick after the release;
   the hold itself is not extended. CHRG chatter alternating bolt and bulb
   before the supply qualifies: as for bolt and blank before, unchanged.
4. `power_lights_mode()` against `power_task()`: fine; no flapping at the
   low cap, since a known level only holds or falls on battery.
5. Note, taken: the plan's "separate passes" was wrong; corrected above, and
   the gate now includes Caps and Fn changing with Fn+B.
6. Names, VIA, host: fine.
7. `7df8a08e96` is the fix codex proposed and is complete. **Should-fix: a
   battery_sim scenario** that banks a RELAX remainder at the floor, raises
   the level on USB and sleeps across log boundaries, asserting unchanged
   history and zero sleep energy (`s_usb` would pass without the guard).
   Done by the deep-sleep session (outer `4bbf561`): `s_usb_relax_banked`
   banks ~8 pm at the floor, a replug's MODEL session raises the level to 41
   pm, then 2 h asleep on USB; every slept entry keeps the level at the sleep
   and the sleep costs no energy. Passes on `7df8a08e96`; with the guard
   removed all 7 slept entries fail.
8. Build flavours, recoverability: fine.

**The verify**
([`review-codex-power-icons-verify-2026-10-19.md`](review-codex-power-icons-verify-2026-10-19.md)),
of `6926a17c80` and these dispositions: no must-fix. Redraw converges (one
tile per mode change, no stale loop); ownership, Minimal and boot fine.
Notes taken: the comment's "supply and CHRG edges keep 1 Hz" was too
absolute, since a plug-in at the low cap clears it and triggers the 10 Hz
path once (comment made exact in `26fcafaeca`); the wake's case is "the next
eligible tick", gates allowing; **disposition 5 stands, and the Fn+B part of
the gate must be on battery**, with music and Caps/Fn changing, which the
relay soak cannot replace.

## Beside the time left

The deep-sleep build adds hours of use to the row under 10 hours (`18% 6h`,
`99% <1h`), **at most 7 cells, so the text starts at x 54 or later**; the
icon ends at x 47. Keep the row's text to 7 cells on battery: an 8th would
start at x 44, over the icon.

## Considered and dropped

- **A letter in the bulb** (A/B/S/L): a 3×5 letter in a 10 px bulb is a smudge
  at arm's length.
- **Stacked bars**: beside a battery they read as a second battery gauge, and
  do not say whether more bars means more light or more saving.
- **A moon for Lights out**: says the board or the computer is asleep; in
  Lights out you are often typing in the dark.
- **A leaf for Power saver**: the common power-saving icon, but outside the
  bulb family, and once the grey bulb was distinct it had no job left.
- **A fill circle** (full, half, crescent, ring): quiet but abstract.
- **The icon on USB during Fn+B's overlay**, to show what unplugging will
  give: two more slot paints a press, and it would push the bolt out while
  charging. The overlay's name is enough.
