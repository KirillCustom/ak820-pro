#!/usr/bin/env python3
"""Deep sleep, many times over (plans/DEEP-SLEEP-BUILD-PLAN.md, validation
1): does every cycle sleep, wake on a key, keep exactly the waking key,
deliver it to the Mac, and come back with nothing else lost.

    venv/bin/python scripts/deep_cycles.py [--n 100] [--out DIR] [--board-only]
                                           [--arm | --awake] [--sleep-s 30] [--tap-ms 40]

By default the board sleeps through the ladder's own path: Minimal mode
(dark at once, so only the idle time holds the sleep back), the bench's
sleep time (op 14 sub 5, --sleep-s, at least 30 s) and no stage forced. The
mode is read first and put back at the end (op 14 sub 1 persists it).

--arm is the experiment's path instead: the stage forced dark and op 14 sub
4 armed each cycle (a 6 s delay, a 60 s cap).

--awake is the control: the stage forced dark (which also keeps the ladder
from sleeping), so the board sits awake through the same silence. A cycle
passes when the logger shows it awake before the tap, the tap counts exactly
one press, and the link gives nothing up. Against a sleeping run it says
which ACK timeouts the wake causes (the 10-19 soak resent ~60% of its
frames; typing, ~4%).

Relay 2 off (the pack) for the whole run; per cycle: data off (`uhubctl`;
a live USB bus is one of the sleep's preconditions); the logger shows it
asleep; a random 5-25 s; the wake rig taps F19; the logger shows it awake;
the F19 listener's log gives the Mac's key-down and its latency from the
relay command; data on, and the board's figures.

A sleeping cycle passes only if:
  - the logger saw it asleep and awake again;
  - the record says it woke on a key, the rig's column (P2.8), no strays;
  - exactly one press (key_presses +1) and a fresh sleep (the sequence +1);
  - no reset (uptime grew by no more than the wall clock) and no new pass of
    25 ms or more (count_ge_25ms, the recovery included);
  - no new transport errors (HC_LINK: giveups, queue_full, replaced, UART);
  - the PCF's slept seconds (HC_POWER page 1) within 2 s of the logger's
    fall and rise edges (each the midpoint of its ~1 s sample gap);
  - the Mac's key-down within 1 s (unless --board-only);
  - and it is judged after the wake's recovery has closed (every step ended).
Kept, not gated: the counted seconds, PLL loops, ACK timeouts, the recovery's
steps that ended on a timeout (page 1's last byte), and the daemon's first
clock offset after the wake (its "before" in the agent's log; validation 1's
clock check, today -1.5 to +2.1 s, the target ~50 ms).

Needs the Mac UNLOCKED (the listener sees nothing at the lock screen;
checked at the start and per cycle) and the F19 listener running; the
displays are held awake. ⚠️ Hands off the keyboard. Restored in a
`finally`, each step on its own, under a whole-run deadline: relay 1 open,
relay 2 on (on USB power the ladder never sleeps, and a charge start wakes a
sleeping board), data on, the board back on USB (a rig tap if it does not
come: a full pack gives no charge edge), then the arm cancelled, the bench
sleep time cleared, the mode put back, the stage released.
"""
import argparse, json, os, random, re, signal, subprocess, sys, time

import hid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sleep_baseline as sb  # noqa: E402
import deep_sleep as ds  # noqa: E402  (uhub, need_bus, deep, bench_sleep, power_op, with_dev, light_off, tail_ma, tail_rows, edges)
import keydrop_test as kd  # noqa: E402  (mac_locked, listener_events, LOG)
import ak820battery as ba  # noqa: E402  (sleep_page; hostagent/ is on the path through sleep_baseline)

cm = sb.cm
DELAY_S, CAP_S = 6, 60   # --arm
RIG_P2 = 1 << 8          # the wake rig's column, P2.8 (matrix (0,13))
MINIMAL = 3              # POWER_MODE_MINIMAL
AGENT_LOG = os.path.expanduser("~/Library/Logs/ak820pro/ak820-agent.log")
AK820 = os.path.join(HERE, "..", "ak820-agent", "target", "debug", "ak820")
LINK_GATED = ("giveups", "queue_full", "replaced", "uart_overrun", "uart_framing", "uart_parity")


def stray_diag(h):
    """op 24 slot 8 (a dev build from a5a176bef4's successor): the last sleep
    with a stray. Its first stray's NVIC pending and ICSR, whether any pin
    outside the wake masks had IE (or RIS & IE), whether the NVIC's enabled
    set was the wake set, how many strays came within that counted second, and
    the pending state at the latest stray (plans/BACKLOG.md, "A stray-wake
    storm"). Only on a build from 6fda6cd1c2, checked by its token: an
    older build exits in cm.xfer (unhandled), and a5a176bef4's slot 8 had
    another layout this cannot tell apart (codex, the verify, P3)."""
    r = cm.xfer(h, [cm.SET_VALUE, cm.HEALTH_CH, cm.HC_DEVHOOK, 24, 8] + [0] * 27)
    if len(r) < 32 or r[3] != 24:
        return None
    b = bytes(r[4:32])
    u16 = lambda i: int.from_bytes(b[i:i + 2], "little")
    u32 = lambda i: int.from_bytes(b[i:i + 4], "little")
    icsr = lambda v: {"icsr": hex(v), "vectpending": (v >> 12) & 0x3F, "pendsv": bool(v & (1 << 28)),
                      "pendst": bool(v & (1 << 26))}
    f = u16(16)
    return {"seq": u16(0), "same_s": u16(2), "first_s": u32(4), "ispr": hex(u32(8)), **icsr(u32(12)),
            "ie_outside": [p for p in range(3) if f & (1 << p)], "mis_outside": [p for p in range(3) if f & (8 << p)],
            "iser_not_wake_set": bool(f & 64), "last_n": u16(18), "last_ispr": hex(u32(20)),
            "last": icsr(u32(24))}


HC_RTC = 0x03
SYNC_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) sync \(([^)]*)\): (.*)$")
BEFORE_RE = re.compile(r"before ([+-]?[\d.]+) ms")
SYNC_BEFORE_MAX_MS = 2000   # a second-scale error before the SET fails the cycle (codex, the mainline review)


def rtc_state(h):
    """HC_RTC pages 1 and 4 (hostagent/rtc_phase0.py decodes them whole): the
    period and the reference, and the PCF writer's state, its completed runs
    and the stale flag (page 4 [24], the RTC fixes). None if either page
    does not answer as itself."""
    p1 = cm.xfer(h, [cm.SET_VALUE, cm.HEALTH_CH, HC_RTC, 1] + [0] * 28)
    p4 = cm.xfer(h, [cm.SET_VALUE, cm.HEALTH_CH, HC_RTC, 4] + [0] * 28)
    if len(p1) < 32 or len(p4) < 32 or p1[3] != 1 or p4[3] != 4:
        return None
    u16 = lambda b, i: int.from_bytes(b[i:i + 2], "little")
    return {"period_active": u16(p1, 9), "period_nominal": u16(p1, 11), "ref_state": p1[18],
            "ps": p4[4], "stop": p4[5], "runs_ok": u16(p4, 7), "restarts": u16(p4, 9),
            "maybe_stopped": p4[11], "pending": p4[18], "slews": u16(p4, 20), "stale": p4[28]}


def wait_sync(log_off, limit_s=60.0):
    """The daemon's first clock sync logged past log_off (the agent log's size
    at data-on): its reason, its offset before the SET, and its line, or None
    within limit_s. The log only, no HID, so it cannot hold the board from
    the daemon's own open (codex, the mainline review, P2 1)."""
    deadline = time.time() + limit_s
    while True:
        try:
            with open(AGENT_LOG, errors="replace") as f:
                f.seek(log_off)
                for ln in f:
                    m = SYNC_RE.match(ln)
                    if m:
                        b = BEFORE_RE.search(m.group(3))
                        return {"why": m.group(2), "before_ms": float(b.group(1)) if b else None,
                                "line": ln.strip()[:200]}
        except OSError:
            pass
        if time.time() > deadline:
            return None
        time.sleep(1)


def rtc_settled(r0, limit_s=90.0):
    """After a sync: the PCF write it asks for done (runs_ok past r0's) and the
    writer at rest: idle, nothing pending (a slew finishes first), STOP and
    maybe-stopped clear, stale clear. Polled every 3 s through the retrying
    opener. The last state read, and whether it settled."""
    deadline = time.time() + limit_s
    while True:
        st = ds.with_dev(rtc_state)
        done = (st is not None and r0 is not None and ((st["runs_ok"] - r0["runs_ok"]) & 0xFFFF) >= 1
                and st["ps"] == 0 and not st["pending"] and not st["stop"] and not st["maybe_stopped"]
                and not st["stale"])
        if done or time.time() > deadline:
            return st, bool(done)
        time.sleep(3)


def rtc_head(c):
    """The cycle line's RTC part: the sync's offset before the SET, the PCF
    runs it took, and whatever kept the writer from rest."""
    s, st, r0 = c.get("sync"), c.get("rtc"), c.get("rtc0")
    if not s:
        return ", NO SYNC"
    out = f", sync {s['why']} before {s['before_ms']} ms"
    if st and r0:
        out += f", PCF runs +{(st['runs_ok'] - r0['runs_ok']) & 0xFFFF}, period {st['period_active']}"
    if not c.get("rtc_settled"):
        out += f", NOT AT REST {st}"
    return out


def rtc_summary(cycles):
    """The run's clock: the offsets before each SET (signed and absolute), the
    cycles whose PCF write came to rest, and the periods seen."""
    b = [c["sync"]["before_ms"] for c in cycles if c.get("sync") and c["sync"]["before_ms"] is not None]
    per = [c["rtc"]["period_active"] for c in cycles if c.get("rtc")]
    if not b:
        return None
    ab = sorted(abs(x) for x in b)
    return {"synced": len(b), "at_rest": sum(1 for c in cycles if c.get("rtc_settled")),
            "rtc_ok": sum(1 for c in cycles if c.get("rtc_ok")),
            "before_ms": {"min": min(b), "max": max(b), "median_abs": ab[len(ab) // 2], "max_abs": ab[-1]},
            "period_active": {"min": min(per), "max": max(per)} if per else None}


def frames_now(n, tag):
    """n lock-light frames over BT through the daemon's own stack, waited for:
    the deliberate traffic for the stray test. The outcome line, or the error."""
    r = subprocess.run([AK820, "lockchan", "send", "--repeat", str(n), "Stray test", tag],
                       capture_output=True, text=True, timeout=n * 20 + 30)
    return (r.stdout + r.stderr).strip().splitlines()[-1:] or [""]


def snap(h):
    """What a cycle is judged by, read in one open: the link, the stall and
    press counters, the uptime, and the sleep page."""
    st = sb.health.read_stalls(h)
    return {"link": sb.health.read_link(h), "presses": st["key_presses"], "ge25": st["count_ge_25ms"],
            "uptime_ms": sb.health.read_isr(h)["uptime_ms"], "page": ba.sleep_page(h)}


def recovered(limit_s=12.0):
    """The sleep page once the wake's recovery has closed (every step ended;
    its bound is 10 s), or the last page read if it never did: a cycle is
    judged on the board after the recovery, not in it (codex, deep-sleep build
    review 9)."""
    deadline = time.time() + limit_s
    while True:
        pg = ds.with_dev(ba.sleep_page)
        if pg is None or not pg["open"] or time.time() > deadline:
            return pg
        time.sleep(0.5)


def wait_current(log, pred, after, limit_s):
    """The first time three logger samples taken after `after` satisfy pred."""
    deadline = time.time() + limit_s
    while time.time() < deadline:
        cur = [c for t, c in ds.tail_ma(log, 3) if t > after]
        if len(cur) == 3 and all(pred(c) for c in cur):
            return time.time()
        time.sleep(0.5)
    return None


def clock_offsets(cycles):
    """Each cycle's first daemon sync after its data came back: the reason and
    the board's offset before the SET (the agent's log, local time)."""
    pat = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) sync \(([^)]*)\): .*?before ([+-]?[\d.]+) ms")
    syncs = []
    try:
        with open(AGENT_LOG, errors="replace") as f:
            for ln in f:
                m = pat.match(ln)
                if m:
                    syncs.append((time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")),
                                  m.group(2), float(m.group(3))))
    except OSError:
        return
    for c in cycles:
        t_on = c.get("t_data_on")
        if t_on is None:
            continue
        hit = next((s for s in syncs if t_on - 1 <= s[0] <= t_on + 60), None)
        c["clock"] = {"after_s": round(hit[0] - t_on), "why": hit[1], "before_ms": hit[2]} if hit else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--board-only", action="store_true",
                    help="no Mac-side timing (runs with the Mac locked): pass on the board's figures alone")
    how = ap.add_mutually_exclusive_group()
    how.add_argument("--arm", action="store_true", help="the experiment's path: op 14 sub 4 each cycle, stage forced")
    how.add_argument("--awake", action="store_true",
                     help="the control: the stage forced dark, so the board stays awake through the silence")
    ap.add_argument("--sleep-s", type=int, default=30, help="the ladder's bench sleep time (at least 30 s)")
    ap.add_argument("--tap-ms", type=int, default=40, help="the rig's press (validation 5: 25 ms taps)")
    ap.add_argument("--dwell", type=float, help="a fixed dwell asleep before the tap, seconds (default random 5-25)")
    ap.add_argument("--frames", type=int, default=0,
                    help="lock-light frames sent over BT while asleep, waited for, as the dwell (the stray test; "
                         "the slider on BT)")
    ap.add_argument("--pause-daemon", action="store_true",
                    help="SIGSTOP the daemon for the run (its own lock-light frames and raw HID kept out), "
                         "SIGCONT at the end")
    ap.add_argument("--rtc-gate", action="store_true",
                    help="each data-on waits for the daemon's sync and the PCF write it asks for, then the "
                         "writer at rest (stale clear), before the counters; the cycle fails without them or "
                         f"with an offset before the SET over {SYNC_BEFORE_MAX_MS} ms")
    ap.add_argument("--log", default=os.path.join(HERE, "..", "history", "power-bench-2026-10-15", "power.csv"))
    ap.add_argument("--out", default=os.path.join(HERE, "..", "history", "deep-cycles-" + time.strftime("%Y%m%d-%H%M")))
    a = ap.parse_args()
    ladder = not (a.arm or a.awake)
    if not 1 <= a.n <= 500:
        sys.exit("--n: 1-500")
    if not 30 <= a.sleep_s <= 600:
        sys.exit("--sleep-s: 30-600")
    if not 20 <= a.tap_ms <= 200:
        sys.exit("--tap-ms: 20-200")
    if not cm.relay_on():
        sys.exit("relay 2 is off: start from the charging state")
    if not a.board_only:
        if kd.mac_locked():
            sys.exit("the Mac is locked: the F19 listener would see nothing (or --board-only)")
        if subprocess.run(["pgrep", "-x", "ak820-f19-listener"], capture_output=True).returncode:
            sys.exit("the F19 listener is not running")
    if not os.path.exists(a.log):
        sys.exit("the power logger's CSV is missing")
    if a.frames and not os.path.exists(AK820):
        sys.exit(f"--frames needs {AK820} (cargo build)")
    if a.frames and a.arm:
        sys.exit("--frames runs on the ladder's path, not --arm's 60 s cap")
    paused = []   # the daemon's pid while SIGSTOPped: resumed by the finally, or first by the guard
    if ds.with_dev(ba.sleep_page) is None:
        sys.exit("no deep-sleep page (HC_POWER page 1): flash the deep-sleep build")
    mode0, _ = ds.with_dev(ds.power_op)
    ds.with_dev(ds.deep, 2)   # an arm left from another run would sleep on its own terms, not the ladder's
    caf = subprocess.Popen(["caffeinate", "-i"] if a.board_only else ["caffeinate", "-d"])   # the displays may sleep board-only
    devs = hid.enumerate(sb.VID, sb.PID)
    if len(devs) != 1:
        sys.exit(f"expected one USB relay, found {len(devs)}")
    cycles, ok, stopped = [], True, None
    controls = [("arm cancelled", ds.deep, 2), ("bench sleep time cleared", ds.bench_sleep, 0),
                (f"mode {mode0} put back", ds.power_op, 1, mode0), ("stage released", cm.force_stage, None)]
    with hid.Device(path=devs[0]["path"]) as dev:
        sb.hold_relay(dev)
        resume = lambda: [os.kill(p, signal.SIGCONT) for p in paused]
        per_cycle = a.sleep_s + 145 + a.frames * 20 + (a.dwell or 0) + (160 if a.rtc_gate else 0)
        guard = sb.deadline_guard(a.n * per_cycle + 300,
                                  lambda: (resume(), ds.restore(dev, *controls)))
        try:
            if a.pause_daemon:   # inside the try: the finally resumes it, whatever stops the run
                r = subprocess.run(["pgrep", "-x", "ak820-agent"], capture_output=True, text=True)
                if r.returncode == 0:
                    paused.append(int(r.stdout.split()[0]))
                    os.kill(paused[0], signal.SIGSTOP)
                    cm.say(f"the daemon (pid {paused[0]}) paused for the run")
            path = ("the ladder: Minimal, the bench sleep time " + f"{a.sleep_s} s" if ladder else
                    "the arm (op 14 sub 4), stage forced to SCREEN_OFF" if a.arm else
                    "the awake control: stage forced to SCREEN_OFF, never asleep")
            cm.say(f"{a.n} cycles through {path}; relay 2 off; light sleep off; the mode was {mode0}")
            sb.relay2("off")
            deadline = time.time() + 30
            while not sb.on_battery():
                if time.time() > deadline:
                    raise SystemExit("the board never reported the pack")
                time.sleep(1)
            ds.with_dev(ds.light_off)
            if ladder:
                ds.with_dev(ds.power_op, 1, MINIMAL)
                ds.with_dev(ds.bench_sleep, a.sleep_s * 1000)
                ds.with_dev(cm.force_stage, None)
            else:
                ds.with_dev(cm.force_stage, sb.SCREEN_OFF)
            time.sleep(5)
            for i in range(a.n):
                c = {"i": i + 1}
                if not a.board_only and kd.mac_locked():
                    cm.say("the Mac locked: stopping (the listener cannot see past the lock screen)")
                    c["stopped"] = "mac locked"
                    cycles.append(c)
                    ok = False
                    break
                s0 = ds.with_dev(snap)
                r0 = ds.with_dev(rtc_state) if a.rtc_gate else None
                w0 = time.time()
                if a.arm:
                    ds.with_dev(ds.deep, 1, DELAY_S, CAP_S, False)
                t_off = time.time()
                ds.uhub("off")
                dwell = a.dwell if a.dwell is not None else random.uniform(5, 25)
                c["dwell_s"] = round(dwell, 1)
                t_sleep = None
                if a.awake:
                    time.sleep(DELAY_S + 2 + dwell)   # the silence an armed cycle keeps: the delay, seen asleep, the dwell
                    cur = [x for _, x in ds.tail_ma(a.log, 3)]
                    c["stayed_awake"] = len(cur) == 3 and all(x > 15.0 for x in cur)
                else:
                    limit = DELAY_S + 25 if a.arm else a.sleep_s + 40   # the ladder counts from the last tap
                    t_sleep = wait_current(a.log, lambda x: x < 10.0, t_off + (DELAY_S if a.arm else 0), limit)
                    c["asleep"] = t_sleep is not None
                    if t_sleep and a.frames:
                        t_f = time.time()
                        c["frames"] = frames_now(a.frames, f"cycle {i + 1}")
                        c["dwell_s"] = round(time.time() - t_f, 1)
                    elif t_sleep:
                        time.sleep(dwell)
                off = os.path.getsize(kd.LOG)
                t_press = time.time()
                sb.press(dev, a.tap_ms, 200)
                t_wake = wait_current(a.log, lambda x: x > 15.0, t_press - 0.5, 10)
                c["awake_seen"] = t_wake is not None
                time.sleep(1.0)
                downs = [t for t, k in kd.listener_events(off) if k == "down"]
                c["mac_latency_ms"] = round((downs[0] - t_press) * 1000, 1) if downs else None
                log_off = os.path.getsize(AGENT_LOG) if a.rtc_gate else 0
                ds.uhub("on")
                c["t_data_on"] = time.time()
                ds.need_bus(30)
                if not a.awake:
                    pg_end = recovered()
                    c["recovered"] = pg_end is not None and not pg_end["open"]
                if a.rtc_gate:
                    # The sync, then its PCF write at rest, then the counters, so
                    # they include the write (codex, the mainline review, P2 1).
                    c["sync"] = wait_sync(log_off)
                    st, settled = rtc_settled(r0) if c["sync"] else (ds.with_dev(rtc_state), False)
                    c["rtc0"], c["rtc"], c["rtc_settled"] = r0, st, settled
                    b = c["sync"]["before_ms"] if c["sync"] else None
                    c["rtc_ok"] = bool(settled and b is not None and abs(b) <= SYNC_BEFORE_MAX_MS)
                s1 = ds.with_dev(snap)
                w1 = time.time()
                c["key_gain"] = (s1["presses"] - s0["presses"]) & 0xFFFF
                c["new_ge25"] = (s1["ge25"] - s0["ge25"]) & 0xFFFF
                up = s1["uptime_ms"] - s0["uptime_ms"]
                c["no_reset"] = 0 < up <= (w1 - w0) * 1000 + 5000
                c["link"] = {k: s1["link"][k] - s0["link"][k] for k in ("sent", "timeouts") + LINK_GATED}
                link_ok = all(c["link"][k] == 0 for k in LINK_GATED)
                mac_ok = a.board_only or (c["mac_latency_ms"] is not None and c["mac_latency_ms"] < 1000)
                rtc_ok = not a.rtc_gate or c["rtc_ok"]
                if a.awake:
                    good = (c["stayed_awake"] and c["key_gain"] == 1 and c["no_reset"] and c["new_ge25"] == 0
                            and link_ok and mac_ok and rtc_ok)
                    head = f"stayed awake {c['stayed_awake']}, {dwell + DELAY_S + 2:4.1f} s silent"
                else:
                    rec = ds.with_dev(ds.deep)
                    pg = s1["page"]
                    c["record"], c["page"] = rec, pg
                    c["fresh"] = pg["sleeps"] == s0["page"]["sleeps"] + 1
                    # From a sample before the cut: a board already idle past the
                    # sleep time (the first cycle) falls asleep within a sample of it.
                    ed = ds.edges(ds.tail_rows(a.log), t_off - 1.5, time.time())
                    c["asleep_s"] = round(ed[1] - ed[0], 1) if ed else None
                    c["pcf_ok"] = ("pcf" in pg["valid"] and c["asleep_s"] is not None
                                   and abs(pg["pcf_s"] - c["asleep_s"]) <= 2.0)
                    c["timed_out"] = pg["timed_out"]
                    if rec["strays"]:
                        c["stray_diag"] = ds.with_dev(stray_diag)
                    good = (c["asleep"] and c["awake_seen"] and rec["slept"] and rec["reason"] == "key"
                            and rec["p2"] & RIG_P2 and rec["strays"] == 0 and c["key_gain"] == 1 and c["fresh"]
                            and c["no_reset"] and c["new_ge25"] == 0 and link_ok and c["pcf_ok"] and mac_ok
                            and c["recovered"] and rtc_ok)
                    head = (f"asleep {c['asleep_s']} s (PCF {pg['pcf_s']}, counted {pg['counted_s']}), "
                            f"woke {rec['reason']}, p2 {rec['p2']:#x}, seq {pg['sleeps']}"
                            + (f", timed out {' '.join(pg['timed_out'])}" if pg["timed_out"] else "")
                            + ("" if c["recovered"] else ", RECOVERY OPEN"))
                c["ok"] = bool(good)
                ok &= c["ok"]
                cycles.append(c)
                cm.say(f"cycle {i + 1:3d}: {head}, key {c['key_gain']}, Mac "
                       + (f"{c['mac_latency_ms']:.0f} ms" if c["mac_latency_ms"] is not None else "nothing")
                       + f", link sent {c['link']['sent']} timeouts {c['link']['timeouts']}"
                       + ("" if link_ok else f" ERRORS {[k for k in LINK_GATED if c['link'][k]]}")
                       + ("" if c["no_reset"] else " RESET") + (f" ge25 +{c['new_ge25']}" if c["new_ge25"] else "")
                       + (f", frames {c['frames'][0][-60:]}" if c.get("frames") else "")
                       + (f", STRAY {c['stray_diag']}" if c.get("stray_diag") else "")
                       + (rtc_head(c) if a.rtc_gate else "")
                       + f"  {'ok' if c['ok'] else 'FAIL'}")
        except (Exception, SystemExit) as e:   # a stop mid-run keeps the cycles so far (the 10-19 soak lost its JSON)
            ok = False
            stopped = f"{type(e).__name__}: {e}"
            cm.say(f"stopped: {stopped}")
        finally:
            # Power first: stopped while the board slept, data alone cannot
            # wake it (ds.restore; codex, deep-sleep build review 8).
            done = ds.restore(dev, *controls)
            guard.cancel()
            caf.terminate()
            if paused:
                resume()
                cm.say(f"the daemon (pid {paused[0]}) resumed")
            if done:
                cm.say("restored: relay 1 open, relay 2 on, data on, the arm cancelled, the bench sleep time "
                       "cleared, the mode put back, stage released")
            else:
                ok = False
                cm.say("⚠️ cleanup incomplete: see above")
    clock_offsets(cycles)
    lat = sorted(c["mac_latency_ms"] for c in cycles if c.get("mac_latency_ms") is not None)
    offs = sorted(abs(c["clock"]["before_ms"]) for c in cycles if c.get("clock"))
    summary = {"cycles": len(cycles), "passed": sum(1 for c in cycles if c.get("ok")), "stopped": stopped,
               "path": "ladder" if ladder else "arm" if a.arm else "awake",
               "mac_latency_ms": {"median": lat[len(lat) // 2], "max": lat[-1]} if lat else None,
               "clock_before_abs_ms": {"median": offs[len(offs) // 2], "max": offs[-1]} if offs else None,
               "rtc": rtc_summary(cycles) if a.rtc_gate else None,
               "timed_out": {st: sum(1 for c in cycles if st in c.get("timed_out", []))
                             for st in ds.RC_STEPS if any(st in c.get("timed_out", []) for c in cycles)}}
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "deep-cycles.json"), "w") as f:
        json.dump({"summary": summary, "args": vars(a), "cycles": cycles, "at": time.strftime("%Y-%m-%d %H:%M:%S")},
                  f, indent=1, default=str)
    print("deep cycles:", "PASS" if ok else "FAIL", json.dumps(summary))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
