Three findings:

1. **Two components can still run in one main-loop pass.** `second_edge` calls housekeeping, then the pump can consume the sole queued clock glyph, then the due 10 Hz housekeeping reaches the new [pending branch](/Users/jdlien/.claude/jobs/dbe817d8/tmp/qmk-flash2/keyboards/a_jazz/ak820pro/graphics/display.c:2784). Bolt + text can bunch together again. Enforce one battery component per main-loop pass.

2. **One component can exceed ~10 ms.** [Text drawing](/Users/jdlien/.claude/jobs/dbe817d8/tmp/qmk-flash2/keyboards/a_jazz/ak820pro/graphics/display.c:1584) still synchronously paints every glyph: `Charge` ≈15 ms and `No Batt` ≈17.5 ms using the [recorded timings](/Users/jdlien/code/ak820-pro/docs/display.md:125). The cross also retains 24 synchronous rectangles. These need finer staging; estimates aren’t new hardware measurements.

3. **Inherited “Low” transition bug.** At unchanged `batt == 0`, plugging/unplugging changes `Low` ↔ `0%`, but [bar invalidation](/Users/jdlien/.claude/jobs/dbe817d8/tmp/qmk-flash2/keyboards/a_jazz/ak820pro/graphics/display.c:1670) ignores the supply state. The red sliver remains or fails to appear. Dirty the bar when the effective fill changes.

Other requested transitions converge correctly: pending marks preserve updates, forced repaints reset the physical bookkeeping, and `bolt_clean` is sound. The ownership gates and band boundaries prevent intervening battery-strip corruption on the reviewed paths.

Read-only; no files modified.

fix 3 first