#!/bin/sh
# The mainline's overnight soak, 10-20/21 (7ae8575c1a, dev, token 0x350912aa),
# on BT with the daemon running normally (--clock --radio --lockchan). Each
# step restores the bench in its own finally (relay 2 on, data on, the mode
# put back), so a failed step leaves the next its starting state.
cd "$(dirname "$0")/../.." || exit 1
H=history/mainline-soak-20261020
note() { echo "$(date '+%F %T') $*" >> "$H/chain.log"; }

note "start"
# 1. Deep sleeps through the ladder, each reconnect proving its sync and PCF
#    write (--rtc-gate; codex, the mainline review, P2 1).
venv/bin/python scripts/deep_cycles.py --board-only --n 150 --rtc-gate --out "$H/cycles" > "$H/cycles.log" 2>&1
note "cycles exit $?"
# 2. Awake on the pack with the cable's data cut for 15 min: the legacy
#    discipline's reference window, judged by the daemon's offset before the
#    SET at the reconnect and the period before and after (P2 2).
venv/bin/python scripts/deep_cycles.py --board-only --awake --n 1 --dwell 900 --rtc-gate --out "$H/awake-window" > "$H/awake-window.log" 2>&1
note "awake window exit $?"
# 3. An hour asleep, as on 10-19 (history/deep-sleep-20261019-1h/).
venv/bin/python scripts/deep_sleep.py --cap 3700 --wait 3600 --out "$H/hour" > "$H/hour.log" 2>&1
note "hour exit $?"
note "done"
