No new blockers. Read-only; no files modified.

- **Pixels match:** all 168 pixels agree for bolt, cross and blank, including stroke clamping, `CROSS_Y_OFF=2`, yellow/red RGB565 and black background. Slot **x=35–46, y=109–122** clears everything without touching the nub (x=29–30) or longest text (starts x=54).
- **Bus handling is safe:** the explicit wait drains or recovers outstanding DMA. `lcd_blit_ram()` additionally calls `bus_quiesce()`, then synchronously sends the 336-byte tile in one pixel-data chunk. The main-loop pump cannot interleave; static-buffer reuse is safe. The one-component-per-pass guard remains intact.
- **Minor comment nit:** [“does not wait on its own”](/Users/jdlien/.claude/jobs/dbe817d8/tmp/qmk-flash2/keyboards/a_jazz/ak820pro/graphics/display.c:1579) is inaccurate because of `bus_quiesce()`. The redundant wait is harmless.

This removes the repeated window-command cost; actual latency still needs board measurement.

flash