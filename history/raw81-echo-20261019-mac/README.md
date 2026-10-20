# Raw HID over 2.4G, stage 1's echo, from the Mac (2026-10-19, 18:33-18:35)

`venv/bin/python scripts/mac_raw81_echo.py N [--gap-ms MS] --json ...` on
`7387d18947` (`gremlin/raw81`, a `dev` build, token `0x19f958d5`, flashed
18:32), the Mac's own dongle (0C45:FDFD, its FF60/61 collection), the slider
on 2.4G, the USB cable in for the counters (HC_DEVHOOK op 17). Each request is
32 random bytes, byte 0 `0xE0 | seq`; the board answers each with the same 32
bytes as a `91` frame, and the dongle returns them as an input report on the
same collection. The plan is [`plans/RAWHID-24G-PLAN.md`](../../plans/RAWHID-24G-PLAN.md).

**macOS gets the `91`s.** Matched by content:

| run | echoed | round trip (ms): min / median / p95 / max | board |
|---|---|---|---|
| 10, 20 ms gaps | 10 / 10 | 17.8 / 20.1 / 21.2 / 21.2 | committed +10, sent_91 +10 |
| 100, 20 ms gaps | 100 / 100 | 16.0 / 19.9 / 22.9 / 25.1 | committed +100, sent_91 +100 |
| 300, back to back | 299 / 300 | 16.9 / 20.0 / 22.4 / 32.8 | committed +299, uart_taint +1, quarantined 33 bytes |
| 20, 100 ms gaps | 20 / 20 | 16.9 / 19.9 / 29.2 / 29.2 | committed +20, sent_91 +20 |

- **No stray reports and no late replies** in any run.
- **The one loss was by design:** a UART error under the collection tainted
  it, and the board quarantined its 33 bytes. HC_LINK counted that one
  overrun.
- **No pass of 25 ms or more.** The board did not reset; its uptime runs
  from the slider's move at 18:33:22.
- **One request in flight at a time**, at ~20 ms a round trip: ~50 a second,
  ~1.6 KB/s each way.

**⚠️ Every echo costs one main-loop pass of 10 ms or more.**
- `count_ge_10ms` rose by 413 across the first three runs' 410 echoes.
- It rose by exactly 20 across the last run's 20 echoes.
- Between runs it did not move.

The stall page's max fields read 0, so where between 10 and 25 ms each pass
falls is not known. The stage-1 code has no blocking wait that would explain
it: the ACK window and the lifetime are checks that return, and the
34-byte `sdWrite` fits the 128-byte queue. A key pressed during such a pass
waits for it, but is not lost below 25 ms. For Gremlin, before stage 2 puts
real traffic on the path.
