**Clear for an attended bench flash.** I found no blocking implementation defect in `466aa04636`. The BIN matches the ELF’s loadable contents; both hashes match the manifest. Two diagnostic limitations remain:

1. **P2 — A passing sweep cannot exclude the original hazard.** [sleep.c:331](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/sleep.c:331) changes register allocation and the wake instruction stream. ASM variants resume with `str`; C resumes with `movs; str`. Variants 0–6 then branch to common handling; variant 7 falls through. Default C also branches, unlike the original failing loop. **Failure scenario:** every variant passes because these changes conceal the fault, despite covering offset 2. **Fix:** follow an inconclusive sweep with layout-only relocations of the original instruction sequence, verifying unchanged register allocation, and capture registers/raw flags into SRAM around WFI.

2. **P3 — The settle is uncalibrated.** [sleep.c:328](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/sleep.c:328) emits a six-instruction loop with three stack accesses per iteration; 300 iterations do not establish a 100 µs delay. **Failure scenario:** variant 8 passes, then a production implementation using an actual 100 µs delay fails. **Fix:** measure the interval and sweep its duration before choosing a production margin.

The placement formula **is guaranteed by construction for this target**, independently of case ordering: alignment + `2k` bytes of NOPs + 2-byte `str` + 4-byte `dsb`. The ELF’s jump table confirms:

| Variant | WFI address | mod 16 |
|---|---:|---:|
| 0 | `0x12346` | 6 |
| 1 | `0x12268` | 8 |
| 2 | `0x123ca` | 10 |
| 3 | `0x123ac` | 12 |
| 4 | `0x1238e` | 14 |
| 5 | `0x12370` | 0 |
| 6 | `0x123f2` | 2 |
| 7 | `0x12144` | 4 |
| 8, SRAM | `0x200014f8` | 8 |
| `0xFF`, C | `0x12440` | 0 |

Other checks passed:

- **ASM:** `"l"` supplies legal Thumb-1 low registers. Inputs remain unchanged; no output or condition-code clobber is missing. `"memory"` supplies compiler ordering; `dsb` supplies hardware ordering. All alignment padding is executable `0x46c0` NOPs, preserving flags.
- **SRAM:** `ds_wfi_ram.constprop.0` remains at `0x200014ec`, including its literal pool. [rules_data.ld:27](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/lib/chibios/os/common/startup/ARMCMx/compilers/GCC/ld/rules_data.ld:27) includes it in the startup copy. The call uses `blx` to Thumb address `0x200014ed`, so distance/`long_call` is handled correctly. It allocates eight stack bytes, preserves callee-saved registers, calls nothing, and fetches no flash until `bx lr`. SRAM execution is valid; naming the output section `.data` does not prohibit it. The address is within the p. 39 SRAM map, and the existing flash driver already executes there. [Reference](/Users/jdlien/code/ak820-pro/docs/sn32f299.md:75), [existing usage](/Users/jdlien/code/ak820-pro/docs/sn32f299.md:1111).
- **Protocol/default:** [hid_protocol.c:1027](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/hid_protocol.c:1027) checks length before arguments, accepts only 0–8/FE/FF, makes FE read-only, and returns `[4]=7, [5]=selected` with the remaining payload zeroed. Initialized SRAM contains FF. That selects the built C sequence, preserving its semantics—not the original binary layout.
- **Recovery:** [sleep.c:466](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/sleep.c:466) retains RTC/GPIO wake enables and shared second-count/cap handling. The cap requires successful execution after wake; it cannot rescue a wedged CPU. The board’s watchdog was measured **not counting during Deep-sleep**, so a failed wake may require power cycling. [Observed behavior](/Users/jdlien/code/ak820-pro/docs/sn32f299.md:454). The diagnostic introduces no flash programming or persistent selector.

A repeatable offset-dependent failure supports the hypothesis. A reliable variant-6 pass rejects **“offset 2 alone always fails,”** but does not exclude an instruction-dependent flash hazard. Corruption captured during the SRAM wake path **before any return to flash**, with entry state intact, would refute immediate post-WFI flash fetching as its cause.

If confirmed, SRAM WFI plus a measured settle is a reasonable workaround. The local reference identifies **no documented flash-ready polling requirement or specified software delay**: p. 75 mentions flash power-down and longer wake; p. 78 says flash is disabled, without identifying its control or recovery interval. [Reference](/Users/jdlien/code/ak820-pro/docs/sn32f299.md:343). The documented requirements already appear satisfied: PMU mode 2 before WFI (pp. 75, 79), and LPCTRL `0x5AFA0000` at 12 MHz, restoring the faster setting before selecting PLL (p. 255). [PMU](/Users/jdlien/code/ak820-pro/docs/sn32f299.md:312), [LPCTRL](/Users/jdlien/code/ak820-pro/docs/sn32f299.md:189). The p. 58 **100 µs figure concerns PLL settling**, not flash readiness.