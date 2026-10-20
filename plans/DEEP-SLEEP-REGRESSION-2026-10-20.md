# Deep sleep broken on the dev builds since the 2.4G work (found 10-20)

## What happens

On `b2a07e0d37` (dev, token `0x398a2e17`), over 2.4G, on the pack, every
deep sleep ends after **exactly one counted second**. The record calls it
a **charge** wake (`SLEEP_WAKE_CHARGE`), and the ladder sleeps again about
3 s later: ~1 s asleep, ~2 s awake, about 25 times a minute.
- Found with the daemon's radio test (`scripts/deep_sleep.py --hold-after`)
  at 08:17 10-20, then `deep_cycles.py --n 2 --board-only` (ladder path):
  `history/uart-latency-20261020/deep-cycles-2g4*/`,
  `radio-sleep*/`.
- The record (op 14 sub 4) reads `p1 = 0`, with `strays 0`. With intact
  masks a charge wake must store the CHRG bit in `ds_p1_ris`
  (`sleep.c:466-470`), so the record contradicts its own path.
- The dev wake ring (op 24) holds `p1 = 00 20 10 bd` in every entry,
  including a key wake. Those bytes are Thumb code
  (`movs r0,#0; pop {r4,pc}`). In the image, the ring reads p1 through
  `r7`, which holds `&ds_p1_ris`. So `r7` holds a code address by the
  write, and the wake's own store through it went astray. That explains
  the zero.

## Ruled out (10-20, at run time)

| switch | result |
|---|---|
| The daemon's radio held off (its flock held; `radio: contended`) | same |
| UART2 back at priority 1 (op 20 sub 3) | same |
| The PCF transfer under the old transaction lock (op 20 sub 2) | same |
| CHRG (B16) watched at 10 Hz for 30 s on the pack | high throughout |

## Where it came in

- **Passed:** an hour asleep over 2.4G on `7387d18947` (10-19,
  `history/ladder-sleep-20261019-hour2/`). 10 of 10 2.4G cycles on
  `83d8768c64` (`history/deep-cycles-20261019-2g4/`).
- **Since then:** `e7f6890e06` (stage 1's pause fix, the overrun/Caps
  build), raw81 stage 2, `145a8234bf` (the long clock's virtual timer),
  `0ae4cf8c80`, the UART-latency build (`ce70c8da41`), and `b2a07e0d37`.
- **Gremlin's suspect:** `r81_long_vt` (`bluetooth/ch582f_ajazz.c`). It
  re-arms every half wrap (~175 ms) from the first task pass on every dev
  build since `145a8234bf`, radio traffic or not.

## Codex (`review-codex-sleep-vt-diag-2026-10-20.md`)

- **The diagnostic `0d765b362d` passes the gate** for a short bench test.
  It is `b2a07e0d37` with the timer never armed (branch
  `mac/sleep-vt-diag`, local; `via-dev-0d765b362d-20261020-083839.bin`,
  token `0xa28d67b8`).
- **P1, the evidence points to corrupted execution state, which ordinary
  VT operation does not explain.** Entry stops SysTick and CT16B5; a
  pending interrupt under PRIMASK would read as a stray, not charge. No
  hidden unlock in the locked section. The ARMv6-M context switch
  preserves r4-r11; the HardFault handler is terminal. Codex's next step:
  capture r7, SP/MSP/PSP, PRIMASK and the raw GPIO flags into fixed SRAM
  just before and after the WFI, and around the final unlock.
  **Taken** as the next build if the bisect does not settle it.
- **P2, the diagnostic breaks raw81 expiry across stalls over one wrap**
  (349.5 ms): a 3 s stall reads as 204 ms. **Accepted for the bench test
  only.** The diagnostic never goes in a merge.
- **Codex's caveat, which matters:** removing the timer also moves the
  code. A pass on the diagnostic implicates the timer *or* the layout.
  - The way to separate them: one image with a run-time switch for the
    timer (a dev op that stops and re-arms it), A/B in the same layout.

## The plan (each flash on JD's go)

**The run-time switch replaces the layout-moving diagnostic.** Gremlin
wrote it in its own file: `981631b925` on `gremlin/vt-switch`, which is
`b2a07e0d37` plus op 17 sub 6 (`[5]` 1 arms, 2 stops with `chVTReset`, 0
reads; reply `[5]` armed). The timer boots armed, and nothing re-arms it
after a stop. Built here as `via-dev-981631b925-20261020-085445.bin`
(token `0x2e634654`); codex's gate is Gremlin's,
`review-codex-vt-switch-2026-10-20.md`.

1. Flash `981631b925` once codex clears it.
2. Run `deep_cycles.py --n 2 --board-only` over 2.4G with the timer armed,
   then stopped, then armed again: an ABA in one layout.
   - It fails armed and passes stopped: the timer is the cause, and
     Gremlin's fix (the timer stops after 1 s of raw81 idle and re-arms
     on the next frame) goes through codex.
   - It fails both ways: not the timer. Build codex's register capture.
   - It passes both ways: the layout moved it. The register capture, and
     the flash timing around the wake, are the lead.
3. Put `b2a07e0d37` back, or the fix. `e7f6890e06` (no timer, no stage 2)
   stays available as a control if the answer is unclear.

## Results (10-20, 09:26-09:36): it follows the image, not the timer

`981631b925` flashed 09:2x (built here, token `0x2e634654`), the slider on
2.4G, `deep_cycles.py --n 2 --board-only` each:

| run | the timer | result |
|---|---|---|
| `vt-ab-1-armed` | armed (as it boots) | 2/2, asleep 41 and 45 s, woke on the key |
| `vt-ab-2-stopped` | stopped (op 17 sub 6, 2) | 2/2, 32 and 30 s |
| `vt-ab-3-armed` | armed again | 2/2, 32 and 37 s; the ring's p1 `0x0` |

Then `b2a07e0d37` flashed back, so a clean reset from the bootloader and
not a brown-out boot (`b2a0-after-clean-boot`): **2/2 fail at once**, the
1 s charge wake, and the ring's p1 `0xbd102000` again.

- **The timer is cleared.** Gremlin's fix is not needed.
- **The boot is cleared.** The morning's failures followed the slider's
  brown-out reset; this one followed a clean reset.
- **The image decides.** `981631b925` is `b2a07e0d37` plus 37 lines in
  `ch582f_ajazz.c` and `hid_protocol.c`, which only move the layout. The
  instructions after the WFI are identical between the two, only shifted:

  | image | WFI | mod 16 | sleep |
  |---|---|---|---|
  | `b2a07e0d37` | `0x120d2` | 2 | fails |
  | `981631b925` | `0x121b6` | 6 | holds |
  | `7387d18947` | `0x10064` | 4 | held (10-19) |
  | `83d8768c64` | `0xf9a4` | 4 | held (10-19) |

- **The lead:** a flash fetch on waking from Deep-sleep that depends on
  where the code sits. It is not proven.
  - The test: the WFI pinned at each 2-byte offset within a 16-byte line
    (NOPs before the DSB), one build each, or one build with several
    copies chosen at run time.
  - The fix, once the failing offsets are known: a wake path that does
    not depend on the layout, codex's register capture if the offsets
    don't separate, and the manual's Deep-sleep wake requirements for the
    flash (`docs/sn32f299.md`).
- **Every dev or daily build is a lottery until then:** a change anywhere
  below `sleep.c` in the link can move the WFI. `b2a07e0d37` is back on
  the board, with this bug.
