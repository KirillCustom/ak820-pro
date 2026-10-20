**I would clear all six firmware images for attended bench use, but would fix the runner before using the proposed `--final` sweep.** The firmware differences are fully accounted for; the runner has an unsafe interruption path and several misleading verdict paths.

1. **P1 — Interrupting a flash can start another flash while the first writer survives.** [pad_sweep.py:98](/Users/jdlien/code/ak820-pro/scripts/pad_sweep.py:98) unconditionally flashes `--final` from `finally`. [flash.sh:224](/Users/jdlien/code/ak820-pro/flash.sh:224) deliberately launches the Sonix writer with `nohup` in the background.

   **Scenario:** JD presses Ctrl-C during programming; the Python call unwinds, but the detached writer continues. The runner immediately attempts the final flash against the same bootloader. An in-memory simulation confirmed that interrupting the first `flash()` immediately calls `flash(final)`.

   **Fix:** defer interruption until the active writer has finished, and prohibit another flash until its termination is established. Track the actual writer, not just the shell. Stream flash progress to the attendant rather than hiding it until completion.

2. **P2 — FAIL does not identify this regression, and the control is not a gate.** [pad_sweep.py:86](/Users/jdlien/code/ak820-pro/scripts/pad_sweep.py:86) uses only the child’s exit status. [deep_cycles.py:255](/Users/jdlien/code/ak820-pro/scripts/deep_cycles.py:255) also gates on logger edges, PCF agreement, transport errors, stalls and recovery.

   **Scenario:** b2a0 sleeps normally but misses a logger edge, exactly as probe variant 9 did. The runner prints FAIL and continues, falsely appearing to have reproduced the control. Conversely, a clean b2a0 PASS also continues through every pad.

   **Fix:** retain the strict cycle verdict, but separately classify **target regression reproduced / normal sleep observed / inconclusive / other failure**, using `deep-cycles.json` and the wake ring. Require b2a0’s characteristic short charge wakes and contradictory board evidence before continuing. Keep all eight ring entries, not only `[-n:]`: the failing image can produce many sleeps during one host cycle.

   Stop the experiment if the control passes or is inconclusive. Retry a logger-only failure on the same image. Stop on hangs, resets, lost HID or incomplete cleanup and preserve evidence before proceeding.

3. **P2 — `layout_diff.py` misses substantial executable and initialized content.** [layout_diff.py:55](/Users/jdlien/code/ak820-pro/scripts/layout_diff.py:55) uses `objdump -d`; its line parser requires leading whitespace.

   **Scenario:** SRAM disassembly addresses print as `2000xxxx:` without leading whitespace, so their contents are skipped. I confirmed empty comparisons for `battery_min`, `func`, `__memcpy_veneer` and `__flashGetSectorOffset_veneer`. `.rodata` is also omitted entirely. A broken RAM flash-driver instruction, initializer, or switch table can therefore pass.

   The reported **1369 “functions” are disassembly labels; 102 have empty comparison lists**. Section/load addresses, alignment, `.flag`, BIN contents and hashes are not comprehensively checked.

   **Fix:** compare every allocated section and its load image, including gaps, using raw bytes and explicit relocation allowances. Check section metadata, function extents, SRAM contents and BIN/ELF correspondence. Accept zero leading whitespace, but do not rely on disassembly text as complete coverage.

4. **P2 — The comparator accepts materially wrong literal values.** [layout_diff.py:45](/Users/jdlien/code/ak820-pro/scripts/layout_diff.py:45) clears bit zero when naming values; [layout_diff.py:98](/Users/jdlien/code/ak820-pro/scripts/layout_diff.py:98) accepts unchanged raw words or an inferred regional shift.

   I exercised its actual comparison functions against these ELFs. For pad 16, all three were accepted:

   - Leaving the NMI vector at stale address `0x1cc71`.
   - Changing it to `0x1cc80`, the relocated handler **without its required Thumb bit**.
   - Increasing an ordinary numeric literal by 16 because its value falls within the inferred region.

   **Fix:** distinguish pointers from integers through relocation evidence or an audited location list; preserve pointer low bits and exact offsets; require pointers to moved targets to move. Bound relocation regions. Replace the blanket `health_fill6` exemption with checks of the four token-generating instructions and the resulting manifest token.

5. **P2 — Final restoration is neither enforced nor reliably recorded.** [pad_sweep.py:99](/Users/jdlien/code/ak820-pro/scripts/pad_sweep.py:99) merely prints the final token; it does not require a match. Exceptions there prevent `sweep.json` from being written. Ordinary flash failure and final-token mismatch can both end with process exit status zero; simulations confirmed these paths.

   **Scenario:** the sweep finishes with the wrong image, or recovery fails and the collected summary is lost. Also, `relay_ready()` runs only **after** each experimental flash and never before the final flash.

   **Fix:** establish power/data readiness before every flash, including recovery. Save progress after every image and before recovery; protect saving with a separate `finally`. Record and enforce final flash/token success. Return a distinct nonzero status for incomplete execution or failed recovery, without treating the expected control regression as a runner error.

6. **P3 — Preflight validates only that JSON can be loaded.** [pad_sweep.py:62](/Users/jdlien/code/ak820-pro/scripts/pad_sweep.py:62).

   **Scenario:** `--n 0` still flashes images before the child rejects it, or a modified BIN retains its original manifest/token.

   **Fix:** validate `1 <= n <= 500`, resolve artifact paths, verify required manifest fields and BIN hashes—including `--final`—before the first flash. These particular files’ hashes passed my independent check.

The independent firmware verification supports the intended experiment:

- All seven reference/pad ELF and BIN hashes match their manifests.
- Reconstructing each BIN from ELF loadable sections, including zero-filled gaps, matched the **entire 262,144-byte BIN**.
- After removing the pad, every `.text` byte difference is accounted for by **940 relocated BL instructions, 286 relocated literal words, and four token-generating instructions** per build. The token computations match their manifests.
- All function symbol addresses/sizes follow the expected relocation; SRAM symbol addresses/sizes remain unchanged.
- The original `0x120c6–0x12135` sequence, including WFI and the suspicious store through r7, is **byte-identical at its relocated address**.
- Outside `.text`, each build changes exactly **11 vector words, 495 `.rodata` words and 34 `.data` initializer words** by the expected relocation. `.ARM.exidx` and `.flag` contents remain unchanged. No unexplained differences remain.

Thus these are **b2a0’s instruction stream relocated, with necessary address fixups and different build-token code**. They are not literally unchanged binaries plus zero bytes.

The pad occupies `[0x11a1c, 0x11a1c + DS_PAD)` in every build. It follows `loop_acct_fill`’s literal pool and precedes sleep.o’s first function, `__NVIC_SystemReset`.

| Pad | Commit | First sleep.o function | `deep_sleep` | WFI | WFI mod 16 |
|---:|---|---:|---:|---:|---:|
| 0 | b2a07e0d37 | `0x11a1c` | `0x11aec` | `0x120d2` | 2 |
| 16 | b2b5b57377 | `0x11a2c` | `0x11afc` | `0x120e2` | 2 |
| 4 | eba26f6c6e | `0x11a20` | `0x11af0` | `0x120d6` | 6 |
| 8 | 8268166476 | `0x11a24` | `0x11af4` | `0x120da` | 10 |
| 32 | 41c954873a | `0x11a3c` | `0x11b0c` | `0x120f2` | 2 |
| 64 | 674ac4308e | `0x11a5c` | `0x11b2c` | `0x12112` | 2 |
| 228 | dd0944df49 | `0x11b00` | `0x11bd0` | `0x121b6` | 6 |

**Nothing before the insertion moves in address, but its bytes do change:** 579 calls and 154 literals there require relocation, plus the four token instructions. `.rodata` and `.data`’s flash load image move too, including constants belonging to earlier objects. SRAM addresses and the fixed end-of-flash flag stay put.

There is **no normal control-flow path into the pad**: no fall-through from the preceding function and no branch/vector/pointer target found there. The pad-4 word attributed to `loop_acct_fill` is outside its declared size. The zeros are executable encodings, not protective NOPs; corrupted execution could enter them, but the intended program never does.

Control first is correct. Define “fails” below as **the original board-level regression**, not merely a nonzero child exit:

| Result | Supported interpretation and next step |
|---|---|
| b2a0 fails; +16 fails; +4 passes | Consistent with sensitivity to low address bits changed by +4 and preserved by +16. It does not establish a fetch-line width. Test +8/+12 and repeat another placement congruent to +4, then confirm with b2a0 → passing pad → b2a0. |
| b2a0 fails; +16 passes | Rejects WFI-mod-16 alone as the explanation for the original instruction stream. Continue +32/+64. A +64 pass also rejects an explanation depending only on the low six address bits. Then isolate which relocated region matters. |
| b2a0 fails; every pad passes | Strong evidence that the original placement matters, subject to repeatability. Reflash b2a0, then a passing pad. Do not promote padding as the fix. |
| +4 and +228 differ | They have the same WFI offset modulo 32. That rejects a modulo-32-only rule. Investigate wider placement/context effects. |
| +228 fails although 981631b925 passed | The shared WFI address `0x121b6` is insufficient. Earlier code layout and other differences in 9816 remain relevant. Capture registers on a still-failing placement. |
| b2a0 and every pad fail | These shifts did not remove the trigger; flash-fetch trouble remains possible. Prioritize the register/raw-flag capture over further blind padding. |
| b2a0 passes, including “everything passes” | No valid failing control today. Stop and re-establish the original conditions before attributing anything to padding. |
| Mixed repeats, logger-only failures, hangs or resets | Separate these outcomes. Repeat inconclusive measurements; preserve and investigate new failure modes. Two cycles establish a useful diagnostic observation, not reliability. |

For six pad builds, I slightly prefer **4, 8, 12, 16, 64, 228**, replacing +32 with +12. That covers all four attainable WFI offsets modulo 16 while retaining a placement preserving the low six bits and the known-address comparison. Adaptively spending a remaining flash on a b2a0 repeat after finding a passing pad may be more valuable than completing every placement.

The clean probe’s 102 wakes support only that its sampled post-wake **data reads** were correct; they do not settle instruction fetching.

**Verdict: clear all six firmware artifacts for attended diagnostic flashes; fix the runner’s interruption path before the sweep, and its classification/recovery checks before trusting the results.**