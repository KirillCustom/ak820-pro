**No P1 defect found in the firmware.** The [reviewed ELF](/Users/jdlien/code/ak820-pro/ak820pro-builds/out/via-dev-83371a49f2-20261020-135325.elf) and BIN hashes match the manifest, and every loadable section matches the BIN. Token `0xb86f33e3` is present.

1. **P2 — A clean result cannot clear the instruction-fetch hypothesis or establish a readiness delay.** The probe tests data loads from new addresses after several SRAM instructions. It does not reproduce the original flash-resident WFI’s instruction-fetch/prefetch state. The M0’s unified bus still distinguishes instruction and data accesses through `HPROT[0]`; a flash-controller buffer could treat them differently. Its existence and behavior here remain undocumented.

   **Failure scenario:** every sample is correct, and that is taken to mean the failing instruction stream is safe—or that flash necessarily stalled until ready. Flash could instead already be ready by the first load, serve buffered data, or fail only for instruction fetches. Earlier probe loads can also affect subsequent samples.

   **Fix:** describe a pass as “these post-wake data reads returned correct values.” For readiness timing, vary the delay **before the first flash access on separate wakes**, and measure elapsed time. See [probe implementation](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/sleep.c:324).

2. **P2 — The script reports PASS even when the probe detects corruption.** [`pass` depends only on `deep_cycles.py`’s exit status](/Users/jdlien/code/ak820-pro/scripts/wfi_sweep.py:75).

   **Failure scenario:** both sleeps hold, `bad_wakes > 0`, but the console and JSON still say PASS. Zero probe wakes can likewise appear successful.

   **Fix:** report separate sleep and probe verdicts. Require `wakes > 0` and no bad reads for a clean probe result; make the overall verdict incorporate both.

3. **P3 — The default sweep still selects refused variant 8.** [The default and validation](/Users/jdlien/code/ak820-pro/scripts/wfi_sweep.py:55) retain it.

   **Failure scenario:** the default run completes FF and 0–7, then aborts at 8; it never tests 9/A. The shared transport rejects the error reply, so this does **not** falsely select 8.

   **Fix:** use `--variants 9,a` for this bench run; update the default, accepted set, and stale `variant()` docstring.

4. **P3 — “8-byte line” overstates what the reference establishes.** The [MCU notes](/Users/jdlien/code/ak820-pro/docs/sn32f299.md:1076) establish an 8-byte **programming unit/alignment**, not a documented read or prefetch-line width.

   **Failure scenario:** a near/far difference is attributed specifically to an 8-byte fetch buffer, or a clean far read is assumed to have missed every possible buffer.

   **Fix:** call these “same aligned 8-byte block” and “different aligned 8/16/32-byte blocks.” Add a target in another flash page if testing broader address separation.

The ELF supports the intended SRAM isolation:

| Operation | Built address |
|---|---:|
| Load word 0 from flash | `0x200014f4` |
| Load PMU address from **SRAM literal pool** | `0x200014f6` |
| Write CTRL=2 | `0x200014f8` |
| DSB / WFI | `0x200014fa` / `0x200014fe` |
| Write CTRL=0 | `0x20001502` |
| First post-wake flash load | `0x2000150a` |
| Return to flash, after all 16 samples | `0x20001534`, `pop {r4,r5,pc}` |

`ds_wfi_probe.constprop.0` occupies `0x200014ec–0x20001543`, including both literals at `0x2000153c` and `0x20001540`. Those contain `0x245c0` and `0x40032040`; loading those addresses does not access flash. Startup copies this entire region with `.data`.

There are no calls, flash instruction fetches, or flash-resident literal loads between word 0 and the samples. Both sampling loops finish before the return to `0x11af8`. The fifth argument is correctly recovered from the stack; all 16 stores stay inside the 64-byte buffer, and callee-saved registers are preserved.

**Word 0 is the last explicit CPU flash access before sleep.** PRIMASK stays set. Admission checks exclude an active SPI transfer/DMA or flash program/erase. The documented DMA is SPI-FIFO-to-SPI-FIFO, not an internal-flash reader; I found no enabled autonomous internal-flash reader on this path. An ELF cannot establish undocumented controller-internal prefetch activity. Fault/NMI vectoring would also invalidate the normal-path isolation.

The actual words are:

| Word | Address | Value | Relationship |
|---|---:|---:|---|
| 0 | `0x245c0` | `0x6b8b4567` | Priming read |
| 1 | `0x245c4` | `0x327b23c6` | Same aligned 8-byte block |
| 15 | `0x245fc` | `0x7545e146` | Different 8/16/32-byte blocks |

All three remain in the same aligned 64-byte block. The jump table confirms variant 9 passes far-first and 10 near-first.

The delay loop really is `subs; cmp; bne`: nominally five cycles for each taken iteration and three for the final iteration. Forty passes take **198 cycles**, or **16.5 µs**, plus counter initialization. Thus “~17 µs” is reasonable for the loop. It excludes sampling/loop overhead and bus waits; nominal accounting puts the last sample around **146 µs**, rather than precisely 140 µs. The first eight reads have intervening SRAM stores and loop instructions, but no deliberate delay.

**RAM is adequate for this attended diagnostic.** The heap is exactly `0x20007fa8–0x20007ff0`, 72 bytes. The built `chCoreAllocFromBase` checks both the upper bound and overflow; `_sbrk_r` returns ENOMEM on failure. The linked allocation paths agree with your audit:

- With TZ unset, `_tzset_unlocked_r` bypasses allocation.
- `__submore` initially requests **1024 bytes**, checks allocation failure, and therefore already could not succeed with either 128 or 184 bytes.
- The probe introduces no allocation.

The PSP stack remains a separate 2048-byte region at `0x20000400–0x20000c00`; the 72-byte heap gap is not its headroom. `ds_probe_run` uses **160 bytes**, and its SRAM callee adds **12**. `deep_sleep` itself shrank from 168 to 160 bytes, making the additional depth versus the old C variant approximately **164 bytes**. That is comfortably below the reported 1280-byte watermark. The [watermark implementation](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/health.c:390) can overstate headroom for unwritten reservations, so recheck it after exercising both variants.

**The firmware protocol and host field decoding agree.** The ELF confirms the length check precedes argument reads, sub 7 accepts 0–7/9/10/FE/FF and rejects 8, FE only reads, and sub 8 accepts only 0/1. Sub 8 returns:

- `[4] = 8`
- `[5..16]`: six little-endian `u16` fields
- `[17..20]`: first bad value
- `[21..24]`: expected value
- `[25]`: first bad position
- `[26..31]`: zero

Arg 1 returns the **pre-clear snapshot**, then clears all 24 bytes of the backing struct under the lock. This matches [sleep.h](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/sleep.h:107) and the script; a synthetic reply decoded correctly. `wakes` counts individual WFI returns, including RTC-second wakes, not just completed deep-sleep sessions.

Interpret the results as follows:

| Outcome | Supported conclusion |
|---|---|
| Early bad samples, later correct | A transient post-wake observation failure; consistent with flash recovery, but not uniquely diagnostic of it |
| Values equal word 0 | Supports stale-data/buffer behavior; does not identify its mechanism |
| Near/far or order dependence | Supports address/history dependence; compare position 0 across variants |
| Errors through position 15 | No demonstrated recovery within this sampling sequence; also investigate pointers, SRAM/core state, and the comparison path |
| All correct, with positive wake count | These data accesses succeeded; instruction-fetch hazard remains possible |
| Hang/reset/no completed tally | Inconclusive: a load, execution state, or eventual return to flash could have failed |

A bad sample is useful evidence, but the buffer and comparison path are also part of the measurement. SRAM-only pre/post register snapshots and an awake control would strengthen attribution.

For the **same one flash**, a stronger probe would preload pointers and expectations, capture registers immediately after WFI before any flash access, and select one first-access delay per wake. Separate runtime variants could test data reads and execution of small flash stubs returning checked signatures. Even those stubs would not reproduce the original flash-resident WFI’s prefetched continuation exactly.

**I would prioritize moving b2a0’s original instruction stream next.** `nm` confirms identical addresses and sizes for all **884 SRAM symbols** between b2a0 and 9816; both have `.data` ending at `0x20001678` and `.bss` ending at `0x20007f38`. Their corresponding wake instructions match, with WFI shifted by `0xe4`, from `0x120d2` to `0x121b6`. This excludes simple `.data/.bss` relocation as the explanation, though relocated code-pointer values and timing can still matter.

Use layout-only shifts of the original object, preserving register allocation and RAM addresses: the planned +2/+16/+32/+64, with **+0xe4** as a useful known-good-address comparison. Verify the actual linked shifts and instruction stream. Follow a still-failing placement with the register capture. The successful sweep already makes another substitute WFI sequence less discriminating than this controlled relocation.

**Verdict: clear `83371a49f2` for an attended bench flash; fix the host verdict reporting before relying on its PASS, and treat clean probe data as limited evidence.**