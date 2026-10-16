# The wake rig's first sweep (2026-10-16, 16:15:04-16:15:18)

JD's rig: a stripped switch housing with two leads to relay 1 (NO, COM) of
the bench's USB relay, in the play/pause key's socket (next to F12).
`scripts/relay_press_test.py`: 10 presses per batch, timed by the Mac, against
the board's `raw_edges` (before debounce) and `key_presses` (after it;
`sym_defer_pk`, 5 ms per key). During R3's drain, between the trial's reads;
JD's hands off. A first sweep at 16:10 measured a broken switch (the relay
clicked, the matrix saw nothing) and was discarded; the script now checks the
rig with one slow press first.

| dwell / gap (ms) | relay on (ms, Mac) | raw edges | presses |
|---|---|---|---|
| 100 / 100 | 104.9 | 19/20 | 10/10 |
| 60 / 60 | 66.5 | 20/20 | 10/10 |
| 40 / 40 | 46.9 | 20/20 | 10/10 |
| 25 / 40 | 31.7 | 20/20 | 10/10 |
| 15 / 40 | 23.9 | 20/20 | 10/10 |
| 10 / 40 | 14.5 | 20/20 | 7/10 |
| 7 / 40 | 10.3 | 20/20 | 4/10 |
| 5 / 40 | 9.0 | 20/20 | 2/10 |
| 3 / 40 | 6.1 | 16/20 | 0/10 |
| 25 / 25 | 30.6 | 20/20 | 10/10 |
| 25 / 15 | 30.5 | 20/20 | 10/10 |
| 25 / 10 | 32.0 | 20/20 | **10/10 (~24 presses/s)** |
| 15 / 10 | 20.1 | 20/20 | 7/10 |

- The relay closes its contact every time down to a 5 ms command: it is not
  the limit.
- The debounce is: closures under ~10 ms, and gaps that short, fail its 5 ms
  stability rule, as designed (human presses dwell 50-150 ms).
- **Reliable to ~24 presses/s** at 25 ms dwell and 10 ms gap. For drop tests,
  40 ms dwell at ~10/s mimics fast typing with margin.
- Unexplained: 19 of 20 edges in the first batch, all 10 presses counted;
  perhaps an edge between two consumes. Watch for it in the drop tests.

## The remap (16:2x 10-16, at JD's go)

Written over VIA's dynamic-keymap commands from a script between the trial's
reads (not VIA itself, whose session the daemon's traffic can derail), each
key read back:
- (0,13), the rig's socket: play/pause (`0x00AE`) → **F19** (`0x006E`) on
  both base layers (0 and 2). F19 rides the keyboard report like any letter,
  and macOS gives it no action.
- (0,14), the knob's press: mute (`0x00A8`) → **play/pause** on both base
  layers. Mute stays on `Fn` + knob press (layer 3) on the Mac.
- The Fn layers are unchanged ((0,13) is next track there).

Backups: the keymap before, `~/Documents/ak820pro-keymap-before-f19-20261016.json`;
`flash.sh`'s backup (`~/Documents/ak820pro-keymap.json`) re-dumped after, so
the next flash restores the new map. To watch a test type, the socket can be
remapped to a visible key the same way, and back. The firmware's default
keymap (`keymap_to_c.py`) still has the old map; regenerate it when
convenient.
