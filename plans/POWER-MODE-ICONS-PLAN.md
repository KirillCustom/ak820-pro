# Power mode icons and names — designed, not built (2026-10-19)

An icon for the power mode in the battery row, and friendlier mode names.
Designed with pixel-accurate mockups (the firmware's own geometry and the
shipped Iosevka 20 px atlas); nothing is in the firmware yet. The colours are
starting values to try on the panel, not decisions.

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
  The one-component-per-pass flag should keep them in separate passes; check.
- **The gate**, with a track playing (display.md): about 20 Fn+B cycles,
  plug/unplug cycles on the relay, a Fn+D exit and a deep-sleep wake.
  `count_ge_25ms` stays 0, and note `blit_gap_max`.

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
