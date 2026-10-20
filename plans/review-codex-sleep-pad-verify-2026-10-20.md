**The artifacts and proposed series are sound; the runner is not yet cleared.** I reproduced the remaining runner failures without touching hardware or modifying files.

1. **P1 — An interrupt during flashing is forgotten; an interrupt during power setup can still start a flash.** [pad_sweep.py:75](/Users/jdlien/code/ak820-pro/scripts/pad_sweep.py:75)

   `SIG_IGN` protects the child, but discards the parent’s signal too. Sending SIGINT during the simulated flash returned success with `interrupted == False`; subsequent images and `--final` remain eligible. Sending it during the ten-second relay settle set `interrupted == True`, yet `flash.sh` still launched because the check precedes that wait.

   **Fix:** keep a recording handler in the parent and arrange ignored signals specifically for the child/writer. Recheck cancellation immediately before launch, then finish any active writer before stopping. Stream flash output while retaining its log: `capture_output=True` still hides progress and the possible Fn+Esc prompt.

2. **P1 — Exceptions bypass the “flash/token failure means no further flash” rule.** [pad_sweep.py:175](/Users/jdlien/code/ak820-pro/scripts/pad_sweep.py:175)

   `flash_ok` starts true and is updated only when `run_image()` returns. If token reading raises—for example, the health executable cannot launch—`finally` attempts `--final`. My simulation confirmed that path, with no result row saved for the affected image.

   Exceptions during final flashing/token reading also bypass the last `save()` and the advertised exit handling. A simulated final-token exception left `"final": null` on disk and escaped as an exception.

   **Fix:** mark each operation incomplete before entering it; explicitly record flash/token exceptions and prohibit recovery flashing after them. Append the current result before doing work, and protect final-status persistence with a separate `finally`. Map runtime failures to the documented exit statuses.

3. **P1 — The writer check does not guarantee exclusive flashing.** [pad_sweep.py:71](/Users/jdlien/code/ak820-pro/scripts/pad_sweep.py:71)

   A writer starting after `pgrep`—during relay settling or `flash.sh`’s backups—is missed. Additionally, `pgrep` statuses 2 and 3 are treated as “no writer”; I confirmed both are accepted.

   **Fix:** fail closed on process-check errors and serialize flashing through a shared lock covering setup through writer completion, enforced by the flashing entry points. Recheck existing writers under that lock.

4. **P2 — Ring evidence can falsely establish the regression, and abnormal ring entries can be ignored.** [pad_sweep.py:99](/Users/jdlien/code/ak820-pro/scripts/pad_sweep.py:99)

   The record path checks `p1 == 0`; the ring path does not. A legitimate one-second charge wake with nonzero P1 therefore yields `regression` and can admit the control. Conversely, normal cycle records plus a backstop entry in the ring still yield `normal`.

   **Fix:** normalize the ring’s hexadecimal GPIO strings and require the contradictory P1 evidence there too. Check the sleep flag (`flags & 0x40` in ring entries), and account for other abnormal wakes before declaring normal. Retaining all eight entries is correct; printing only four does not lose the saved evidence.

5. **P2 — Fatal cycle failures do not stop advancement when the board classification looks acceptable.** [pad_sweep.py:137](/Users/jdlien/code/ak820-pro/scripts/pad_sweep.py:137)

   The runner discards `summary.stopped`, never checks the completed count against `--n`, and ignores reset/recovery/cleanup failures when deciding whether to continue. One good cycle followed by a child failure can classify as normal; even a cycle explicitly containing `no_reset=False` and `recovered=False` does so.

   **Fix:** separate the board’s sleep classification from permission to advance. Require complete execution, no reset, closed recovery and confirmed cleanup. Preserve structured failure reasons and complete child logs. Allow a narrowly identified logger-only discrepancy such as v09 without treating every strict failure as harmless.

6. **P3 — Manifest fields are required but not fully validated.** [pad_sweep.py:51](/Users/jdlien/code/ak820-pro/scripts/pad_sweep.py:51)

   I confirmed that a manifest with `token="broken"` or integer `git_head` passes preflight. The former is discovered after flashing; the latter can crash when a later image begins.

   **Fix:** validate field types and formats before any flash, particularly the eight-digit hexadecimal token and full commit hash. The `--n` range, filename agreement, BIN hash, dev flavour, and inclusion of `--final` are correctly checked. All selected artifacts passed those checks and my additional ELF-hash checks.

7. **P3 — The layout screen’s disclaimer is broadly accurate, but “Every RAM symbol” overstates its coverage.** [layout_diff.py:33](/Users/jdlien/code/ak820-pro/scripts/layout_diff.py:33)

   `ram[name]` overwrites duplicate local names. These ELFs contain twelve duplicated RAM names, including `passes` and `ring`; changing the earlier instance can escape comparison. Twenty-six RAM symbols without printed sizes are skipped too.

   **Fix:** either preserve symbol identities/multiplicity and unsized symbols, or narrow the wording. Also describe `.data` as **not comprehensively checked**: its FUNC bodies and veneer literals now are checked.

   The intended repairs work: SRAM instructions parse, the two veneers have seven comparison entries each, and `battery_min`/`func` are correctly excluded as objects. The tested Thumb-bit loss is rejected. Stale pointers still pass raw equality, and arbitrary `health_fill6` changes remain exempt; explicitly naming those limits would strengthen the disclaimer.

The historical classifications match your report: `b2a0-after-clean-boot` and all three `deep-cycles-2g4*` runs classify as regression; all three `vt-ab-*` runs and flash-probe v09/v0a classify as normal. The saved probe rings agree. v09’s strict failure remains separate, as intended. Ordinary returned flash failure, final-token mismatch, and normal-control stop also produced the expected exit 3, 3, and 2 in simulations.

**DS_PAD 12 passes the independent byte-level verification.** The requested commit diff changes only `DS_PAD 64` to `12`; relative to b2a0, the source change is the pad insertion.

- Both manifests’ BIN/ELF hashes match, and reconstruction from ELF load segments—including zero-filled gaps—matches each complete **262,144-byte BIN**.
- After accounting for the twelve-byte insertion, every `.text` difference is explained by **940 relocated BL instructions, 286 relocated literal words, and four token instructions**. The latter compute **`0x7e5a8f85`**.
- Outside `.text`: exactly **11 vector words, 495 `.rodata` words and 34 `.data` words** receive the expected relocation. `.ARM.exidx` and `.flag` contents remain unchanged.
- The pad occupies **`[0x11a1c, 0x11a28)`**, after `loop_acct_fill`’s literal pool and before `__NVIC_SystemReset`. No normal fall-through, branch or checked pointer target enters it.
- `deep_sleep` moves to **`0x11af8`**; WFI moves to **`0x120de`**, offset **14 modulo 16**. The entire original `0x120c6–0x12135` sequence is byte-identical at `0x120d2–0x12141`.
- RAM layout and symbol addresses/sizes remain unchanged. SRAM instruction bytes remain unchanged; its initial image necessarily contains relocated flash pointers, including two veneer literals.

Your order **b2a0 → 16 → 4 → 8 → 12 → 64 → 228 → b2a0**, followed by the specified `466aa04636` final image, is appropriate. Omitting 32 retains all four attainable WFI offsets modulo 16, the low-six-bit-preserving +64 comparison, and +228’s known-address comparison. Require the closing b2a0 repeat to reproduce before treating the series as stable evidence; two cycles remain diagnostic observations.

**Verdict: clear DS_PAD 12 and the proposed image set/order for attended diagnostics; fix the runner’s P1s before running and its P2s before trusting sweep conclusions.**