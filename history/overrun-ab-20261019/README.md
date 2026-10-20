# The UART overrun A/B, 10-19 21:48-22:04

`c9d1b8b4e7` (dev, token `0xfc6e78de`, flashed 21:47), the slider on 2.4G,
the cable's data on, relay 2 on. The radio load is the 2.4G echo, back to
back (`mac_raw81_echo.py --gap-ms 0`, the Mac's dongle).
`scripts/overrun_diag.py ab --arms abcddcba --secs 90 --n 1000`: `ab.json`,
`run.log`; and a smoke run of one burst arm (`smoke.json`). The plan:
[`plans/OVERRUN-CAPS-PLAN.md`](../../plans/OVERRUN-CAPS-PLAN.md).

**The PCF's masked spans, per transaction (as before the patch):**

| kind | wire bytes | longest masked | longest whole call |
|---|---|---|---|
| 7-byte calendar read | 10 | **2.629 ms** (1000 of 1000 over 1.4 ms) | 3.18 ms |
| 8-byte time write | 9 | **2.197 ms** | 2.68 ms |
| 1-byte register read | 4 | 1.072 ms (none over 1.4) | 1.52 ms |
| 1-byte register write | 3 | 0.763 ms | 1.01 ms |

**Per byte (the patch):** the longest masked span is **0.304 ms** for every
kind. But the 7-byte read's whole call grows to **5.44 ms** (the row ISR
runs in the gaps), past the deep sleep's 5 ms short deadline. None of the
2,000 reads failed, because the deadline is checked only at byte starts, so
there is no margin. The 8-byte write: 4.52 ms.

**The arms** (each twice, in the order a b c d d c b a):

| arm | lock span | burst | OEs seen (+ in the drain) | with a PCF transaction | with nothing | echoes lost | load |
|---|---|---|---|---|---|---|---|
| a | per transaction | none | 17 (+9), 21 (+2) | 0, 1 | 26, 22 | 17, 20 of 5000 | 92-94 s |
| b | per transaction | 1000 reads | 104 (+5), 90 (+5) | 92, 80 | 17, 15 | 220, 211 of 3000 | 104-106 s |
| c | per byte | 1000 reads | **798 (+4), 745 (+3)** | **790, 737** | 12, 11 | 167, 162 of 3000 | 93-94 s |
| d | per byte | none | 11 (+3), 23 (+6) | 0, 7 | 14, 22 | 11, 19 of 5000 | 92-94 s |

- **No byte was ever refused by the input queue,** so every overrun is the
  hardware's (`LS.OE`).
- **The handlers are short:** the USB handler's longest run was 0.048 ms and
  the system timer's 0.021 ms, in every arm.
- **The receive pins aren't involved:** UART2 is on P1.6/P1.7, port B, while
  the I2C's mode writes are on port A.

**What it says:**
1. **A long masked PCF read does cause overruns under radio load:** ~9% of
   the per-transaction reads, against none-context rates of ~0.2 a second.
2. **Releasing the lock per byte made overruns more frequent, not less:**
   ~8x as many, about one per 0.3 ms locked stretch that overlaps arriving
   bytes.
   - **Why that matters:** with a 16-byte FIFO (~1.4 ms at 115200), no
     0.3 ms stretch (~3.5 bytes) could overrun. So the receiver holds far
     less than 16 bytes, perhaps only one (87 us).
   - **The losses moved the other way:** fewer echoes were lost per byte
     than per transaction (~165 against ~215 of 3000), which fits more
     overruns each costing fewer bytes.
3. **Overruns with nothing in progress continue,** ~12-26 an arm, under
   load alone and in either mode. With a shallow receiver, any masked
   stretch longer than ~87 us while bytes arrive would do it. So the PCF is
   one cause among several, and probably not the main one in normal use,
   where it runs ~12 times an hour.

**So the per-byte lock is not the fix the plan expected.** It cuts the
masked span 8.6x but leaves the overruns. It lengthens every PCF
transaction to about twice its time, and it breaks the short deadline's
margin.

**Next:**
- **Measure the receiver's depth directly:** a masked span of chosen
  length, under the same load.
- **Then the fixes that depth allows:**
  - a bit-level release (~30 us masks);
  - starting a PCF transaction only on a quiet line;
  - finding the other masked stretches over ~87 us.
- **Until then:** per byte stays off in the boot default, and the daily
  build should not force it.
