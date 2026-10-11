Both gaps are resolved in this build. Deferred redraws retain the flag through housekeeping; shortening text [returns immediately after clearing](/Users/jdlien/.claude/jobs/dbe817d8/tmp/qmk-flash2/keyboards/a_jazz/ak820pro/graphics/display.c:1777), leaving glyphs for a later pass.

One ordering caveat: core [calls `housekeeping_task_user()` again after the keyboard hook](/Users/jdlien/.claude/jobs/dbe817d8/tmp/qmk-flash2/quantum/keyboard.c:423), therefore after the reset. I checked the supplied ELF: that hook is empty (`bx lr`), so **nothing paints a battery component after the reset in this build**. A future drawing override would reopen that gap; the duplicate hook call is inherited.

No new blockers found. Read-only; no files modified.

flash