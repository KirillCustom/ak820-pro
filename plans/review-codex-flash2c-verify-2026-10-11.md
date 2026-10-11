Two gaps remain:

1. **The per-pass limit has a forced-repaint exception.** The deferred dashboard callback draws the outline before housekeeping ([main.c:72](/Users/jdlien/.claude/jobs/dbe817d8/tmp/qmk-flash2/quantum/main.c:72)). Housekeeping then resets the flag. With the clock off and queue empty, a due housekeeping tick can paint another component in that same pass.

2. **Shortening still combines clearing and three glyphs.** `Charge` → `99%` performs a synchronous 30×23 clear, then three glyphs ([display.c:1766](/Users/jdlien/.claude/jobs/dbe817d8/tmp/qmk-flash2/keyboards/a_jazz/ak820pro/graphics/display.c:1766)). That can exceed ~10 ms using the recorded timings; the clear needs a separate stage or a smaller accompanying chunk. This is an estimate, not a new hardware measurement. Cross chunking resolves its original issue.

**3 is resolved.** Fill-based invalidation handles the 0% sliver correctly.

No persistent stale cells found: shorter, longer, empty, forced-clear interruptions, and `100%` → `Charge` → `99%` converge correctly, also checked with an in-memory cell model. No flag-induced starvation: skipped calls preserve pending work, and each housekeeping pass resets the flag.

Read-only; no files modified.

fix 2 first