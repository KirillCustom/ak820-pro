**No P1 implementation defect found. Both diagnostic images preserve the SRAM isolation.** Two review findings:

1. **P2 — A failure would not uniquely identify the offending alignment.** The pad moves the remaining flash image; `wfi28` also moves later RAM code/data. `981631b925` argues against RAM placement explaining the original regression, but cannot exclude a separate effect from these new RAM placements.  
   **Failure scenario:** attributing a failure specifically to the return address or SRAM WFI, then applying the wrong workaround. **Fix:** bracket failures with repeat runs of `8b06e4dcc4`; if `wfi28` fails, follow with a fixed-footprint pair that preserves later RAM addresses while changing WFI placement.

2. **P3 — `wfi28` consumes eight additional RAM bytes, not four.** The helper grows four bytes; veneer alignment adds four more. Three following SRAM functions move four bytes, but the veneers and `.bss` move eight.  
   **Failure scenario:** subsequent diagnostic additions budget against overstated free RAM. **Fix:** record `.data = 2,728 B`, `.bss = 26,816 B`, and **136 B** remaining heap/free gap.

The byte-level checks passed against the [fix ELF](/Users/jdlien/code/ak820-pro/ak820pro-builds/out/via-dev-8b06e4dcc4-20261020-150215.elf):

| Image | Complete accounting of changes |
|---|---|
| [Pad `221ffb103a`](/Users/jdlien/code/ak820-pro/ak820pro-builds/out/via-dev-221ffb103a-20261020-151934.elf) | Four zero bytes at `0x11a1c`; 940 relocated BLs, 287 text literals, 11 vector words, 495 `.rodata` words, 35 `.data` words, and four token instructions. All **895 named RAM symbol entries**, including duplicates and unsized symbols, retain addresses/sizes. |
| [WFI `5a9f7b406d`](/Users/jdlien/code/ak820-pro/ak820pro-builds/out/via-dev-5a9f7b406d-20261020-151907.elf) | Two entry NOPs; four zero alignment bytes before the veneers; 2,384 text literals, 33 `.rodata` words, 14 `.data` words, five SRAM BL relocations, and four token instructions. No unexplained differences. |

All manifest hashes match; complete **262,144-byte BINs**, including gaps, reconstruct exactly from their ELFs. Emitted tokens are **`0x197933d0`** and **`0x37a80aab`**. The fix itself differs from cleared `b3f48e9c5a` only in four token instructions among loadable bytes.

| Check | Pad | WFI28 |
|---|---:|---:|
| Helper start / size | `0x200014ec` / 40 B | `0x200014ec` / 44 B |
| SRAM WFI | `0x200014f6` | `0x200014fa` |
| Dropped-read instruction | `0x20001508` | `0x2000150c` |
| Return into flash | `0x120aa` | `0x120a6` |
| Startup copy destination, end exclusive | `0x20000c00–0x200016a0` | `0x20000c00–0x200016a8` |
| Heap/free gap | 144 B | 136 B |

Both helpers and both literals lie wholly inside the actual startup copy. After WFI, execution remains in SRAM through CTRL=0, the unchanged 260-pass delay and the dropped flash read. There are **no intervening flash instruction fetches, flash-resident literals, calls or stack accesses**. The target flash words still contain `0x5a5aa5a5`; PRIMASK protection remains unchanged.

Both retain **1,024-byte MSP / 2,048-byte PSP** regions. Frames remain **176 B for `deep_sleep`, 48 B for its caller, zero for the helper**. These establish no additional stack demand, not measured whole-program headroom.

Assuming the baseline passes under the same conditions:

- **Both pass:** supports isolation surviving both tested placements; does not prove the stale-block mechanism or production reliability.
- **Pad fails:** flash-layout sensitivity survives the workaround; the delay/read/return protection is insufficient somewhere, without uniquely implicating `0x120aa`.
- **WFI28 fails:** the workaround remains placement-sensitive despite SRAM execution. SRAM execution/entry effects and moved RAM remain candidates; this does not alone refute flash involvement.
- **Both fail:** rejects placement independence for this workaround and calls for capture before the first post-wake flash access.

**Verdict: clear both exact artifacts for an attended diagnostic bench flash, with PMU Sleep disabled and acceptance based on full wake/recovery records; no production clearance implied.**