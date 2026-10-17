#!/usr/bin/env bash
# resume_bench.sh -- restart R3's bench processes after the Mac rebooted (or
# anything else killed them) mid-drain. Restarts only what is not running.
#
#   scripts/resume_bench.sh
#
# A reboot kills the recorder (power_log.py), the trial (bench_trial.py), its
# guard (trial_guard.py), caffeinate and the F19 listener; none of them is a
# LaunchAgent. The keyboard runs on through it on its pack, and its RAM log
# keeps a row every 600 s. If the trial stays dead past the RGB cut, nothing
# charges the pack, so run this as soon as the Mac is back.
#
# It refuses once the drain has reached the cut (a CUT, done or FAIL row): the
# charge phase needs a person's look first. A restarted trial writes
# drain-resumed-N.csv (the guard's own naming) and checks the board is on
# battery first.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
D=history/battery-2026-10-15-drain-2f
P=history/power-bench-2026-10-15
PY=venv/bin/python
say() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$D/resume.log"; }
running() {   # pidfile, command fragment
  [ -f "$1" ] && ps -p "$(cat "$1")" -o command= 2>/dev/null | grep -q "$2"
}

if running "$P/power.pid" power_log.py; then
  say "recorder running (pid $(cat "$P/power.pid"))"
else
  $PY scripts/power_log.py --out "$P/power.csv" --detach >/dev/null && sleep 3
  say "recorder started (pid $(cat "$P/power.pid")); $(tail -1 "$P/power.log")"
fi

if grep -q -E '^[^,]*,[^,]*,(CUT|done|FAIL),' "$D"/drain*.csv; then
  say "the drain has reached the cut or ended: the trial is not restarted; look first"
  exit 1
fi

if running "$D/trial.pid" bench_trial.py; then
  say "trial running (pid $(cat "$D/trial.pid"))"
else
  supply=$($PY hostagent/ak820battery.py 2>&1 | awk '/^supply/{print $2}')
  if [ "$supply" != battery ]; then
    say "the board's supply reads '${supply:-no reply}', not battery: not restarting the trial"
    exit 1
  fi
  n=1; while [ -e "$D/drain-resumed-$n.csv" ]; do n=$((n + 1)); done
  out="$D/drain-resumed-$n.csv"
  $PY - "$ROOT" "$D" "$out" <<'EOF'
import os, subprocess, sys, time
root, d, out = sys.argv[1:]
if os.fork():
    time.sleep(1.5)
    sys.exit(0)
os.setsid()
if os.fork():
    os._exit(0)
log = open(os.path.join(d, out.split("/")[-1][:-4] + ".log"), "a")
p = subprocess.Popen(["venv/bin/python", "scripts/bench_trial.py", "--wait-cut", "--poll", "300", "--full",
                      "--out", os.path.abspath(out)], cwd=root, stdout=log, stderr=subprocess.STDOUT,
                     stdin=subprocess.DEVNULL)
open(os.path.join(d, "trial.pid"), "w").write(f"{p.pid}\n")
os._exit(0)
EOF
  say "trial restarted (pid $(cat "$D/trial.pid")) into $out"
fi

if running "$D/guard.pid" trial_guard.py; then
  say "guard running (pid $(cat "$D/guard.pid"))"
else
  csv=$(ls -t "$D"/drain*.csv | head -1)
  $PY scripts/trial_guard.py --pid "$(cat "$D/trial.pid")" --csv "$csv" >/dev/null && sleep 2
  nohup caffeinate -i -s -w "$(cat "$D/guard.pid")" >/dev/null 2>&1 &
  say "guard started (pid $(cat "$D/guard.pid")) on $csv, with caffeinate"
fi

if pgrep -x ak820-f19-listener >/dev/null; then
  say "F19 listener running"
else
  open -a "$HOME/Applications/AK820 F19 Listener.app" && say "F19 listener started"
fi
