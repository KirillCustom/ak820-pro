"""The 2.4G soak re-judged with the logger's edge at 22 mA (README.md). Run from the repo root."""
import json, sys, time
sys.path.insert(0, 'scripts')
import deep_sleep as ds
d = json.load(open('history/deep-cycles-20261019-2g4/deep-cycles.json'))
cs = d['cycles']
rows = [r for r in ds.tail_rows('history/power-bench-2026-10-15/power.csv', kb=2048) if r[0] >= time.mktime(time.strptime('2026-10-19 17:47:30', '%Y-%m-%d %H:%M:%S'))]
pairs, t = [], rows[0][0]
while True:
    e = ds.edges(rows, t, rows[-1][0], thr=22.0)
    if not e: break
    pairs.append(e); t = e[1] + 1
print(len(cs), 'cycles,', len(pairs), 'sleeps at 22 mA')
for c, (f, r) in zip(cs, pairs):
    w = [x for x in rows if f < x[0] < r]
    tail15 = sum(1 for x in w if x[1] >= 10)
    tail39 = sum(1 for x in w if 3.6 <= x[1] < 10)
    win = r - f
    others = (c['asleep'] and c['awake_seen'] and c['recovered'] and c['key_gain'] == 1 and c['new_ge25'] == 0
              and c['no_reset'] and c['fresh'] and c['record']['reason'] == 'key' and c['record']['strays'] == 0
              and not c['timed_out'] and c['mac_latency_ms'] is not None and c['mac_latency_ms'] < 1000
              and all(c['link'][k] == 0 for k in ('giveups', 'queue_full', 'replaced', 'uart_overrun', 'uart_framing', 'uart_parity')))
    print(f"cycle {c['i']:2}: 22 mA window {win:5.1f} s, PCF {c['page']['pcf_s']:3} (diff {c['page']['pcf_s']-win:+.1f}); "
          f"15 mA tail {tail15} s, 3.9 mA tail {tail39} s; Mac {c['mac_latency_ms']:.0f} ms; other checks {'ok' if others else 'FAIL'}")
