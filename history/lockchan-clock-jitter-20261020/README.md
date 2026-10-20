# The lock-LED anchor's arrival time over BLE, from Windows (10-20)

`scripts/win_lockchan_anchor_jitter.py 40`, gremlin, board on `81a1860c8c`
(dev), BT slot 1, the cable in for raw-HID readback only, the daemon stopped.
Each frame a 5-byte P0 type 9 (unused type: decoded, not applied), 16 ms
spacing. The host notes when the anchor write (the preamble's third
transition) completed (`perf_counter`); the board its `anchor_ms`. Board time
is mapped onto host time by the lowest-RTT of 40 raw-HID `board_ms` reads at
the start and at the end (RTT 4.0 ms each): offset and drift (the board's
timer runs at 999.81 ms per host second).

**40 of 40 committed. Latency, host write completion to the board's anchor:**

| min | p10 | p25 | median | p75 | p90 | max |
|---|---|---|---|---|---|---|
| 7.1 | 20.0 | 25.9 | 38.1 | 46.3 | 53.7 | 147.4 ms |

An earlier run of 30: mean 59, sd 34, min 20, max 179 ms.

So a clock frame corrected by the median would land within about ±15 ms
typically and ~100 ms in the tail; the PCF it would correct drifts ~5 s a day
(the Mac session's measurement). Raw data: `ble-p0-16ms.json`.
