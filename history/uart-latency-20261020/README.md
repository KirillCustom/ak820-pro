# The UART-latency build, measured: the night of 10-19/20

The plan is [`plans/UART-LATENCY-PLAN.md`](../../plans/UART-LATENCY-PLAN.md).
The question: does releasing the clock chip's I2C transfer from the
interrupt lock, and raising UART2 above USB, stop the radio link's
receive overruns? UART2 holds about one byte
([`../overrun-depth-20261019/`](../overrun-depth-20261019/)).

**The builds:**
- `ce70c8da41` (dev, token `0x24743048`), flashed 23:59 10-19. Everything
  below up to the ge10 section ran on it.
- `b2a07e0d37` (token `0x398a2e17`), flashed 01:04 10-20. The same build
  with the pass budget compared in ticks (see "ge10" below). The release
  matrix and the daemon's cable-out test ran on it.

The bench throughout: the slider on 2.4G, the Mac's dongle, the cable's
data on, relay 2 on.

## Stage 2's acceptance on the build (`s2-all.{json,log}`)

`mac_raw81_s2.py all`, the Mac port of Gremlin's `win_raw81_s2.py`, passes
every check:
- **text, health, the long clock, expiry, every NAK:** all pass. The long
  clock counted 3.500 s of a 3.5 s stall.
- **the stream:** 998 of 1000 ok. The 2 lost were both counted by the board
  as taint (UART overruns). p95 29.9 ms; no pass >= 25 ms.

## The A/B (`ab.{json,log}`; `ab-attempt1.log`)

`overrun_diag.py ab --secs 150 --n 1500`: 20 arms in ABBA order under stage
2's load (health reads back to back over the dongle). Each arm's settings
were set and read back, and the board's figures and the RX tap were zeroed
at its start.

| arm | lock | UART2 | PCF burst | OEs | requests lost |
|---|---|---|---|---|---|
| tb | transaction (old) | 1 | 1500 reads | 199, 205 | 356, 357 of 4,600 (7.7%) |
| ub | unlocked | 1 | 1500 reads | 14, 12 | 14, 12 of 5,600 |
| tn | transaction | 1 | none | 9, 10 | 8, 10 of 5,800 |
| un | unlocked | 1 | none | 10, 9, 15, 14 | the same, of 5,800 |
| **pn** | **unlocked** | **0** | none | **3, 5, 2, 5** | **the same, of 5,800** |
| **pb** | **unlocked** | **0** | 1500 reads | **3, 6** | **the same, of 5,600** |
| mn | unlocked, monitor on | 0 | none | 2, 5 | the same, of 5,600 |
| mb | unlocked, monitor on | 0 | 1500 reads | 3, 6 | the same, of 5,600 |

**The defaults are pn and pb:** unlocked, UART2 at 0.

- **The unlocked transfer removes the PCF from the overruns.** Under the
  burst, the old lock lost 7.7% of requests; unlocked, about 0.2%. No OE
  in an unlocked arm coincided with a PCF transaction. The PCF's longest
  masked span is now 5 µs, from 2.63 ms.
- **UART2 above USB removes about two thirds of the rest:** 2-5 OEs an arm
  at priority 0, against 9-15 at 1.
- **One OE is one lost request.** The board counts each as taint, and the
  host's count of lost requests matches it. The RX tap counts 34.05 bytes
  per request in every arm but tb.
- **The gap monitor costs no OEs.** It saw at most 2 gaps of 128 µs or more
  an arm, the longest 0.219 ms.
  - So the OEs left are not from long masked stretches. A one-byte
    receiver would also lose bytes to stretches of ~90-128 µs, which the
    monitor's first threshold does not count. That is the lead for what is
    left.
- The first attempt (`ab-attempt1.log`) stopped in its second arm. The two
  scripts' raw-HID opens collided, and `ak820text` then locked the cable
  out for its 60 s enumeration backoff. Fixed in `deep_sleep.open_dev`
  (`30e8605`).
- Cosmetic: each arm's log printed the last burst's "done" count, because
  settling stops a burst without zeroing its tally. The tool now prints it
  for burst arms only.

## ge10: the passes of 10 ms or more under radio load

Gremlin saw this build raise them from +11 to +39 per 1000-request stream
(0ae4cf8c80 against ce70c8da41, on Gremlin's board). The cause found:
`rtc_fast_task`'s new admission check called `sleep_pass_us()` every pass,
a `TIME_I2US`, which is a 64-bit software divide on the M0.
`b2a07e0d37` compares ticks against `TIME_US2I(RTC_ADMIT_US)` instead,
which folds to 1,875 at compile time
([codex: no findings](../../plans/review-codex-pass-ticks-2026-10-20.md)).

**HC_ACCT** (`ge10-*-acct.json`):

| build | rtc_fast, all passes | per pass |
|---|---|---|
| ce70 | 1,494 ms over 7,207 passes | 0.207 ms |
| b2a0 | 338 ms over 5,570 passes | 0.061 ms |

**The 1000-streams (`ge10-*.json`):**

| build | when | ge10 | RTT median |
|---|---|---|---|
| ce70 | ~1 h after boot | +77, +84 | 25.6, 25.1 ms |
| b2a0 | 1 min after boot | +69, +62 | 27.0, 27.1 ms |
| b2a0 | 9.5 min after boot | +58, +77 | 27.0, 27.3 ms |
| b2a0 | 1 min after a second boot | +73, +77 | 27.5, 27.0 ms |
| b2a0 | 56 min after that boot | +86, +64 | 26.9, 27.4 ms |

**What it says:**
- **The fix removes a real 0.15 ms a pass,** but ge10 moves within noise.
- **The slow passes are 10-12 ms, built by stacking:** ch582 1-4 ms (one
  34-byte frame drained at wire speed: Gremlin's profile) + ~4 ms of QMK
  core outside the scopes + one housekeeping slot. So ge10 counts passes
  just over a threshold. None reaches 25 ms.
- **The RTT here is not uptime.** b2a0 reads ~27 ms at 1, 9.5 and 56 min
  up and across a reboot (`ge10-b2a0-1h-*.json`). ce70's one 25 ms reading
  came straight after the hour-long A/B, and stays unexplained.
- **Gremlin's board is the clean comparison:** one quiet desk, separating
  by build (`../raw81-v2-gremlin-*.json`):

  | build | median RTT | ge10 per 1000 |
  |---|---|---|
  | 0ae4 | 25.0 ms | +11 |
  | ce70 | 26.9 ms, flat over 27 min | +35 to +39 |
  | b2a0 | 25.9 ms | +17 to +24 |

  So the fix took back about half of what ce70 added. The likely rest is
  the always-on dev UART hooks (`plans/BACKLOG.md`).

## The release matrix, on `b2a07e0d37`

- **The stuck bus (`stuck.json`):** op 23 sub 5, twenty times. Each read
  failed, as it must, in 10-11 ms whole with the restart. No pass >= 25 ms.
  Five ordinary reads afterwards all succeeded.
- **The staged restore (`restore.json`):** 20 cycles of three Fn+D taps (op
  1), through the restore that the animation's stop and the boot redraw now
  share. No pass >= 25 ms. The longest blit since boot was 19 ms, boot
  redraw included.
  - With no animation provisioned, ANIM_TOG is a one-byte read and a return
    (`lcd_bus.c`), so no key press can reach its stop path; op 1 exercises
    the same restore.
- **Typing under load (`typing.json`):** relay 1 pressed the rig's key 200
  times (60 ms down, 120-400 ms apart) during stage 2's request stream.
  - **All 200 were registered by the board,** and no pass reached 25 ms.
  - **The link lost nothing:** giveups, replaced and queue_full +0, and no
    timeouts.
  - The stream lost 6 of 4,400 (the six overruns).
  - Seeing each press at the Mac would need Input Monitoring consent; the
    board's counts stand in.
- **A sync's phase errors:** not run separately. The daemon's syncs since
  the flash log after-errors of −1.6 to +2.8 ms (its log).

## The daemon's radio, cable out and back (`radio-cableout.{json,log}`)

The daemon `v0.1.1-628-g5a9bac1` was installed `--clock --radio` at 01:06.
Its macOS radio half is `b2b85f3` plus codex's fixes;
[`plans/RAWHID-24G-DAEMON-PLAN.md`](../../plans/RAWHID-24G-DAEMON-PLAN.md)
has the reviews. `mac_radio_cableout.py` ran with Music paused:
- **The data cut at 01:06:57:** radio=qualified and transport=radio 6.6 s
  later (the 3 s quiet, then the next tick). Held 60 s: a keepalive push,
  no failures.
- **The data back at 01:08:04:** the radio stood down 3.6 s later (one
  tick), and the transport was usb at once.
- **The band, read back over op 18:** "Lucy In Disguise" / "Passing
  Mirrors" with the pause icon, as the daemon sent it.
- **After the stand-down:** no radio traffic, and op 17's stale, busy,
  taint, drops and resp_expired +0.
- **Left for the morning, with JD:**
  - a track change while on the radio (driving Music from the shell could
    raise a consent dialog);
  - the deep sleep's backoff on the radio.

## The morning of 10-20, with JD

- **2.4G with the cable out:** play/pause reached the board over the radio.
  The track change ("Pure Carbon") showed 4 s after the radio qualified.
  The handback to USB was clean.
- **BT with the data cut (uhubctl):** BT carries no raw HID, so the radio
  went silent. The probes backed off, and the lock was released and
  re-taken about every 34 s, two log lines a cycle. The handback was clean.
- **Gremlin's pause fix (`28dc876`):** installed on the Mac at 08:17
  (`v0.1.1-644-g28dc876-dirty`, `--clock --radio`). A pause shows on the
  board after a small lag, which JD accepts.
- **The deep sleep's backoff on the radio** found the deep-sleep
  regression instead:
  [`plans/DEEP-SLEEP-REGRESSION-2026-10-20.md`](../../plans/DEEP-SLEEP-REGRESSION-2026-10-20.md).
  The runs are in `radio-sleep*/`, `deep-cycles-*/`, `vt-ab-*/`,
  `b2a0-after-clean-boot/` and `wfi-sweep/`.
