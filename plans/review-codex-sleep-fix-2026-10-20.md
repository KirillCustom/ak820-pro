**No P1 implementation defect found in either fix ELF.** The SRAM workaround is sound for an attended bench test. The evidence strongly supports a layout-sensitive wake fault, but the proposed **single-word replay does not fully explain the recorded state**.

1. **P2 — The stated mechanism predicts the wrong P0 result.** In b2a0, immediately before WFI, `r6` still holds `ds_rows = 0x241e4`. Replacing only `0x120d8–0x120db` skips `mov r3,fp; ldr r6,[r3,#28]`. The subsequent `ands r6,r3` at `0x120e0` then computes:
   
   `0x241e4 & p0w(0x4f0) = 0xe0`
   
   That predicts a **key wake with P0 `0xe0`**, not the observed charge wake with P0 zero. The corresponding predictions for pads 16, 8 and 64 are also nonzero. Additional instruction/data corruption could reconcile this, but it is absent from the proposed chain.

   **Failure scenario:** the [comment’s asserted explanation](../ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/sleep.c:306) becomes justification for a shorter delay or a branch-only workaround that misses the actual fault. **Fix:** describe the replay as a hypothesis; capture `r1`, `r6`, `r7`, masks and raw RIS on a placement that still reproduces. A replay affecting another word is worth investigating, not assuming.

2. **P2 — Dev PMU Sleep remains outside this protection.** The [idle hook](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/chconf.h:43) still executes WFI from flash with CTRL=4 when requested. The MCU notes say flash is disabled in Sleep too.

   **Failure scenario:** enabling that experiment encounters a related wake-fetch fault despite Deep-sleep being fixed. **Fix:** keep PMU Sleep disabled during this validation; before promoting it, provide and validate its own SRAM wake path with timing appropriate to its running clock. It defaults off, so this does not block the proposed flash. Plain WFI with CTRL=0 needs separate validation because its hardware behavior is undocumented.

3. **P2 — `deep_cycles` PASS alone is insufficient acceptance evidence.** Its [success expression](/Users/jdlien/code/ak820-pro/scripts/deep_cycles.py:255) does not require an empty `timed_out` list or check the wake ring/P1 consistency.

   **Failure scenario:** recovery clears its pending work through timeouts, or the key arrives with corrupt diagnostic state, yet the run passes. **Fix:** explicitly gate on the complete record, ring, recovery outcomes and reset/stall evidence described below.

4. **P3 — The emitted delay is now countable, but the claimed hazard duration is unmeasured.** The loop really is approximately 1,300 cycles. Passing branch-based variants and later data reads do **not** establish a universal four-to-six-cycle recovery bound.

   **Failure scenario:** a future change reduces the wait based on that inferred bound and reintroduces corruption. **Fix:** retain the conservative delay, soften the timing claim, and measure/sweep first-access delays before reducing it. This is a production characterization follow-up, not a pre-flash blocker.

**The mechanism checks out in several important details.** In the [b2a0 ELF](/Users/jdlien/code/ak820-pro/ak820pro-builds/out/via-dev-b2a07e0d37-20261020-005529.elf):

- `0x120d0` contains `0xbf308f4f`; decoding independently at its lower halfword gives exactly `ldrh r7,[r1,#58]`.
- `r1` receives `0x5afa0000` from literal `0x122a8`, stores it to LPCTRL at `0x120b4`, and retains it through the first WFI. The hypothetical load address is therefore `0x5afa003a`.
- Flash at `0xbf30` contains exactly `0xbd102000`.
- The wake store at `0x1212c` uses `r7`; the ring reads through it at `0x11f8e/0x11f92`. Thus `r7=0xbf30` would explain the lost SRAM update and the ring’s code bytes.
- Every saved failing pad ring has that P1 value, including its final key wake; the separately read record has P1 zero.

However, **neither the reserved-address read returning `0xbf30`, nor `0x120d8` being the first post-wake flash fetch, is established by an ELF**. Also, `0xbd102000` occurs at 21 halfword-aligned locations in b2a0: the ring value alone does not uniquely identify `r7`.

The critical original instruction sequence is byte-identical at its relocated address in every pad ELF. Their BIN/ELF hashes and complete BIN reconstruction also match.

| Pad | Commit | WFI | mod 8 | Latest saved evidence |
|---:|---|---:|---:|---|
| 0 | b2a07e0d37 | `0x120d2` | 2 | Regression |
| 16 | b2b5b57377 | `0x120e2` | 2 | Regression |
| 4 | eba26f6c6e | `0x120d6` | 6 | Holds; first cycle missing logger edge |
| 8 | 8268166476 | `0x120da` | 2 | Regression |
| 12 | 462cb9244b | `0x120de` | 6 | Holds; strict PASS |
| 64 | 674ac4308e | `0x12112` | 2 | Regression |
| 228 | dd0944df49 | `0x121b6` | 6 | Both sleeps hold; one ≥25 ms stall |

**The series advanced while I reviewed it.** Pad 228 recorded normal key wakes after 8 and 49 seconds, with P1 zero, but its first cycle had `new_ge25=1`; the runner stopped. The closing b2a0 repeat is absent from the saved results. The stall remains unexplained. [Latest sweep log](/Users/jdlien/code/ak820-pro/history/uart-latency-20261020/pad-sweep.log)

The other passes fit a qualified layout hypothesis:

- `981631b925`: WFI `0x121b6`, same critical sequence, identical SRAM symbol layout; armed/stopped/armed all passed.
- `83d8768c64` and `7387d18947`: WFI `0xf9a4` and `0x10064`, both **4 mod 8**, with different register allocation. They do not contradict a failure specific to b2a0’s sequence at 2 mod 8.
- The `7387` hour was **not entirely clean**: it contained an unexplained wake at 3,556 seconds, followed by another sleep. It excludes the deterministic one-second failure, not every possible wake defect. [Hour record](/Users/jdlien/code/ak820-pro/history/ladder-sleep-20261019-hour2/README.md)
- Sweep variants 2 and 6 are 2 mod 8, but execute `str; b` after WFI. Their passes reject an offset-only rule; they remain compatible with instruction-stream/prefetch dependence.
- The SRAM probe’s first data load follows five instructions, nominally at least seven execution cycles before the load itself, excluding bus waits. Its 102 clean wakes do not reproduce immediate flash instruction fetching or establish readiness timing.

My assessment: **strong evidence for layout dependence; substantial circumstantial evidence for a flash-fetch problem; an intriguing but incomplete stale-word reconstruction.** Reproducible opposite results at the same controlled placements would refute the simple modulo rule. Corruption captured in SRAM before any post-wake flash access would refute immediate flash fetching as the sole cause.

**Both fix artifacts implement the intended isolation.** I checked the [dev ELF](/Users/jdlien/code/ak820-pro/ak820pro-builds/out/via-dev-b3f48e9c5a-20261020-145038.elf) and [daily ELF](/Users/jdlien/code/ak820-pro/ak820pro-builds/out/via-daily-b3f48e9c5a-20261020-145102.elf), their manifests, tokens and complete BIN load images.

| Item | Dev | Daily |
|---|---:|---:|
| Helper start; size | `0x200014ec`; 40 B | `0x200014dc`; 40 B |
| Helper’s flash load address | `0x26014` | `0x21da4` |
| WFI | `0x200014f6` | `0x200014e6` |
| Dropped flash load instruction | `0x20001508` | `0x200014f8` |
| `bx lr` | `0x2000150a` | `0x200014fa` |
| Return into flash | `0x120a6` | `0xf148` |
| Flash word read | `0x2416c` | `0x1ff0c` |

Both target words contain `0x5a5aa5a5`. The **address literals** are in SRAM; the target word is intentionally in flash.

Startup copies all of `.data`, including the entire helper and both literals:

- Dev: flash `0x25728` → SRAM `0x20000c00–0x200016a0`.
- Daily: flash `0x214c8` → SRAM `0x20000c00–0x20001690`.

The linker explicitly includes [`.ramtext` in that copy](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios/os/common/startup/ARMCMx/compilers/GCC/ld/rules_data.ld:27). Actual startup instructions use those bounds.

Both helpers execute CTRL=2, DSB, WFI, CTRL=0, the complete delay, the volatile flash load, then `bx lr`. There are no intervening flash fetches, flash-resident literal loads, calls or stack accesses. Only `r2`, `r3` and flags change; callee-saved registers and LR remain intact. Calls use valid Thumb SRAM addresses through `blx`.

PRIMASK remains set across this sequence and clock/peripheral restoration. I found no interrupt restore in the locked callees. GPIO sampling, RTC consumption, watchdog feeding, cap/stray handling and wake recording use the newly compiled register allocation correctly. Nothing relies on the removed inline sequence’s register leftovers. Daily contains the helper unconditionally; its `deep_sleep` body is inlined into `sleep_task`.

**The wait is reasonable for this bench fix.** `subs; cmp; bne` takes nominally `259×5 + 3 = 1,298` cycles; counter initialization adds two. That is **108.3 µs at 12 MHz**, with surrounding instructions and bus waits adding time. This resolves the old uncertainty about what loop was actually emitted. It does not establish a specified flash recovery requirement.

The dropped read is harmless on the normal path: an aligned, side-effect-free read whose result is discarded. It can absorb a bad data response, but it is neither a readiness check nor a documented instruction-buffer flush. A faulting or stalled read would still fail the wake.

SRAM and register retention are documented. No additional SRAM enable or wake initialization is specified. Under the stated 12 MHz IHRC condition, the delay does not depend on the stopped ChibiOS timer. Preserve the existing clock order: IHRC before lowering flash wait states; restored wait states before selecting PLL. The manual’s 100 µs PLL settling figure is a separate requirement.

**RAM fits, with little dev heap margin.**

| Build | `.data` | `.bss` | Heap/free gap |
|---|---:|---:|---|
| b2a0 dev | 2,680 B | 26,816 B | 184 B |
| Fix dev | 2,720 B | 26,816 B | `0x20007f60–0x20007ff0`: **144 B** |
| Fix daily | 2,704 B | 24,784 B | `0x20007760–0x20007ff0`: **2,192 B** |

Both retain separate **1,024-byte MSP** and **2,048-byte PSP** regions. The heap gap is not stack headroom.

Dev `deep_sleep`’s own frame grows **160 → 176 bytes**; its `sleep_task` caller uses 48 bytes. Daily’s combined `sleep_task` frame is 160 bytes. The SRAM leaf uses zero. These are frame sizes, not whole-program stack maxima; recheck watermarks after validation. No allocation is introduced, and the existing allocator bounds-checks failure.

The linked WFI inventory is exhaustive: daily has only the SRAM helper; dev additionally has the idle hook at `0x1c8d0`. The flash driver’s SRAM routines do not enter PMU sleep. They wait for BUSY, mask program windows, and leave erase interrupts unmasked. That is a separate flash-access exposure, not another demonstrated Deep-sleep wake path. Test persistence writes after waking, but this evidence does not justify adding the sleep delay to that driver.

**For validation, I would require:**

- Resolve/classify pad 228’s stall and obtain the closing failing control before calling the placement series complete.
- After JD’s go, verify dev token `0x57aead4a`; start with a few cycles on each transport, then **100 or more per transport**, including actual host key delivery rather than only `--board-only`.
- Require one sleep per intended cycle; correct key/knob/charge cause and RIS; consistent record/ring values; no code-valued P1, unexpected wakes, resets, strays, PLL ceiling, recovery timeouts, new stalls or UART errors.
- Run an uninterrupted hour with continuous power logging and retain every teardown’s reason. The earlier unexplained hour wake makes “eventually woke correctly” insufficient.
- Test P0 and P2 keys, knob, charge wake, repeated re-entry, and settings persistence after wake. Smoke-test daily independently.
- Test padded caller/return placements. **Also move the SRAM WFI to 2 mod 8:** both current helpers happen to place it at 6 mod 8. That is a stronger check against merely choosing another favorable alignment.
- Follow with an overnight soak and a backstop test. Measure/sweep the delay separately before shortening it.

**Verdict: clear `b3f48e9c5a` for an attended dev bench flash on JD’s go; the SRAM implementation is sound, while the exact replay mechanism and production reliability remain unproven.**