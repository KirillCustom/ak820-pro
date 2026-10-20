# The power modes' flash, validated (2026-10-20)

**The image:** `mac/lockchan-pm` `845996c39c`, a `dev` build, token
`0x2e279a39`, flashed 17:11. It is `5df4e0e3b7` (the power modes revised:
[`plans/POWER-MODES-REVISION-PLAN.md`](../../plans/POWER-MODES-REVISION-PLAN.md))
with the lock-LED handler, the play times and codex's fixes cherry-picked on
top. `ds_deep_wfi` is in SRAM at `0x2000150c`, and 96 bytes of RAM are free.

`5df4e0e3b7` itself, now `ak820pro-jdlien` and `deps.lock`'s pin, was
host-tested only: `run.sh` with no override, every case, mutants 26 of 27
with the known survivor. Its behaviour is validated here, in the image that
carries it.

## Validation 3: the image sleeps (`deep_cycles.py`)

| run | result | the failures |
|---|---|---|
| BT, 20 (`deep-bt*`) | 17/20 | cycle 5 `strays: 1000`; cycles 14 and 19 a UART overrun |
| 2.4G, 10 (`deep-24g*`) | 9/10 | cycle 10, one pass of 25 ms or more |

- **Every cycle slept, and woke on its key, with the key kept.** None failed
  the way `b2a07e0d37` did.
- **The BT failures came beside the daemon's own lock-light frames.**
  `--lockchan` took BT whenever the cycle cut the cable's data, and sent
  about 45 frames across the run. On 2.4G there were no frames, and no
  strays or overruns.
  - First read as "frames end the sleep", then corrected: one stray wake in
    about 45 frames.
  - Open as "A stray-wake storm in a soak beside lock-light frames"
    ([`plans/BACKLOG.md`](../../plans/BACKLOG.md)), with a diagnostic built
    for it.
- **The 2.4G failure is the known paint of 26-28 ms around some wakes.**
- **Mac latency:** median 77.5 ms on BT, 51.1 on 2.4G.

## `ladder_check.py --rig` (`ladder*`)

Every effect's stages, Power saver's ceiling at 255 and 100, and white at
128 against Saver at 255 (18.8% each) pass. The exceptions are two Typing
Heatmap rows, which failed only the "reference lit" floor.
- The Heatmap lights only the rig's one tapped key of 81: 6.7 ‰ over a
  stage's holds (the floor was 20) and 1.9 ‰ at value 100 (the floor was 2).
- Its behaviour was right: active 0.7%, dim 0.1%, off and dark 0, and the
  wake 0.8%. Saver gave 0.20 of Always on at 255 (expected 0.19) and the
  same at 100.
- **Fixed in the script:** the Heatmap's own floor, 4 ‰ at 255 and 1 at 100.
- **Re-judged from `ladder/ladder-check.json` with it: all three Heatmap
  rows pass.** Not rerun on the board.

**Not run, by JD's agreement:** the stage map (`current_map --set stages`)
and the per-mode real-time sleeps (`ladder_sleep`). R5 measures those in
use.
