# Do lock-light frames end the deep sleep? No (2026-10-20)

The question came from the power-modes flash's BT soak
([`../power-modes-20261020/`](../power-modes-20261020/README.md)). One cycle
ended its sleep on `strays: 1000`, and two counted a UART overrun, beside the
daemon's own lock-light frames.

**The build:** `mac/lockchan-pm` `6fda6cd1c2`, a `dev` build, token
`0xa647bcb6`. It adds the stray diagnostic (op 24, slot 8) and the readout
tied to its title. Slider on BT, the daemon paused for both runs
(`--pause-daemon`).

| run | sleeps | lock-light frames into them | result | strays | overruns | Mac latency, median |
|---|---|---|---|---|---|---|
| `frames` (`--frames 8`) | 10 | 80, all `Sent` (49-51 writes each) | 10/10 | 0 | 0 | 42 ms |
| `control` (`--dwell 30`) | 10 | none | 10/10 | 0 | 0 | 66 ms |

- **Frames into a sleeping board are harmless:** no stray return and no
  overrun in 80 frames over 10 sleeps of about 30 s.
- **So the soak's failures had another cause.**
  - **The overruns:** in the soak the daemon ran. At every data-on it synced
    the clock, and a sync writes the PCF: the 2.6 ms masked stretch known to
    drop UART bytes (`BACKLOG.md`, "UART overruns").
  - **The stray:** still unexplained, one in about 50 sleeps today on this
    image family. The diagnostic now names its source when one comes.
- **Correction:** the stray cycle's 39 ms tap-to-Mac latency was read as a
  board already awake. This run shows 28-54 ms is ordinary when the BLE link
  carries traffic just before; the control's quiet link gave 52-83.
