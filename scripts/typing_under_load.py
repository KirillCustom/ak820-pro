#!/usr/bin/env python3
"""Typing through the wake rig while the 2.4G link carries a request stream
(plans/UART-LATENCY-PLAN.md, the release matrix):

    venv/bin/python scripts/typing_under_load.py [--n 200] [--secs 120] [--json OUT]

Stage 2's load (mac_raw81_s2.py load) runs in a child; meanwhile relay 1
presses the rig's key --n times, 60 ms down, gaps of 120-400 ms, as typing
goes. The board's counters before and after:
- key_presses (after debounce) must rise by exactly --n;
- the link's giveups, replaced and queue_full must not move (a key report
  given up or displaced is a lost press at the host); its sent, timeouts and
  uart_overrun are reported;
- no pass >= 25 ms.
Seeing each press at the Mac would need Input Monitoring consent; the board's
own counts stand in for it. The slider on 2.4G, the Mac's dongle, relay 2 on.
"""
import argparse, json, os, random, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import deep_sleep as ds  # noqa: E402
import overrun_diag as od  # noqa: E402
import usb_relay as ur  # noqa: E402

cm = ds.cm
import ak820health as health  # noqa: E402  (on the path through deep_sleep)
import hid  # noqa: E402


def board():
    def rd(h):
        s = health.read_stalls(h)
        return {"key_presses": s["key_presses"], "ge25": s["count_ge_25ms"], "ge10": s["count_ge_10ms"],
                **{f"link_{k}": v for k, v in health.read_link(h).items()}}
    return ds.with_dev(rd)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--secs", type=float, default=120)
    ap.add_argument("--json")
    a = ap.parse_args()
    devs = hid.enumerate(ur.VID, ur.PID)
    if len(devs) != 1:
        sys.exit(f"expected one USB relay, found {len(devs)}")
    relay = hid.Device(path=devs[0]["path"])
    st = ur.read_state(relay)
    if st & 1 or not st & 2:
        sys.exit(f"the bench is not ready: {ur.describe(st)} (relay 1 must be open, relay 2 on)")
    b0 = board()
    load = od.Load(a.secs)
    presses = []
    try:
        load.wait_ready()
        time.sleep(2)
        for i in range(a.n):
            t = time.time()
            ur.switch(relay, 1, True)
            time.sleep(0.06)
            ur.switch(relay, 1, False)
            presses.append(round(t, 3))
            time.sleep(random.uniform(0.12, 0.40))
        L = load.finish()
    finally:
        try:
            ur.switch(relay, 1, False)
        finally:
            load.close()
    time.sleep(1)
    b1 = board()
    d = {k: b1[k] - b0[k] for k in b0}
    checks = [
        (f"key presses +{d['key_presses']} of {a.n}", d["key_presses"] == a.n),
        *[(f"link {k} +{d['link_' + k]}", d["link_" + k] == 0) for k in ("giveups", "replaced", "queue_full")],
        (f"no pass >= 25 ms (+{d['ge25']})", d["ge25"] == 0),
        (f"the load: {L['ok']} ok, {L['lost']} lost of {L['requests']}", L["requests"] > 0 and not L["other"]),
    ]
    for name, ok in checks:
        print(f"{'ok  ' if ok else 'FAIL'} {name}", flush=True)
    print(f"link sent +{d['link_sent']}, timeouts +{d['link_timeouts']}, uart_overrun +{d['link_uart_overrun']}; "
          f"passes >= 10 ms +{d['ge10']}")
    if a.json:
        os.makedirs(os.path.dirname(os.path.abspath(a.json)), exist_ok=True)
        json.dump({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "args": vars(a), "before": b0, "after": b1,
                   "delta": d, "presses": presses, "load": {k: v for k, v in L.items() if k != "blocks"},
                   "checks": [{"check": n, "ok": ok} for n, ok in checks]}, open(a.json, "w"), indent=1)
    sys.exit(0 if all(ok for _, ok in checks) else 1)


if __name__ == "__main__":
    main()
