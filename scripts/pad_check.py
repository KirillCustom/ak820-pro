#!/usr/bin/env python3
"""One image of the padding series, checked; it flashes nothing
(plans/DEEP-SLEEP-REGRESSION-2026-10-20.md; branch mac/sleep-pad):

    venv/bin/python scripts/pad_check.py --expect ARTIFACT [--control] [--n 2] [--out DIR]

The operator flashes ARTIFACT with flash.sh first (codex, pad verify: a
runner that flashes needs a lock and signal handling it did not have, so the
flashing stays with a person or a session that watches it). This checks the
board runs that image (its manifest's token), runs `deep_cycles.py --n N
--board-only`, and gives two verdicts:

  board    regression  b2a07e0d37's signature, in a cycle's record or in the
                       wake ring: a wake that slept, called "charge", after
                       <= 2 counted seconds, without CHRG's bit (B16) in its
                       p1 (the record reads 0, the ring a stray word: the
                       store through a corrupted r7);
           normal      every cycle slept >= 10 counted s and woke on the
                       rig's key, and every ring entry is a key wake that
                       slept;
           other       anything else.
  advance  the run completed (N cycles, not stopped, no reset, recovery
           closed, cleanup done, deep_cycles exited 0 or 1); every cycle
           kept its key, had no pass >= 25 ms and no gated link error; the
           board is regression (for --control, only regression) or normal;
           and every failed cycle is explained: on a board showing the
           regression, by its symptoms alone (never seen asleep, more than one
           sleep in the cycle, a charge wake's record, no sleep edges: the
           sleeps last a second, and its signature can sit in the ring rather
           than the cycle's record); otherwise by the power log's missing
           sleep edges alone (asleep_s None and pcf_ok False, nothing else:
           flash-probe v09).
  (codex, pad-check verify, P2s 1-4.)

Exit 0 to advance, 2 not to, 4 if the board runs another image, 5 if
relay 2 is off (deep_cycles starts from charging; put it on, then rerun).
"""
import argparse, hashlib, json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import deep_sleep as ds  # noqa: E402
import overrun_diag as od  # noqa: E402

cm = ds.cm
AK820 = os.path.expanduser("~/Library/Application Support/ak820pro/bin/ak820")
RIG_P2 = 0x100       # the rig's key column (deep_cycles.py)
SLEPT = 0x40         # SLEEP_V_SLEPT (sleep.h)
CHRG = 1 << 16       # B16, CHRG's bit in P1 (config.h)
LINK_GATED = ("giveups", "queue_full", "replaced", "uart_overrun", "uart_framing", "uart_parity")
LOGGER = {"asleep_s", "pcf_ok"}
SYMPTOMS = {"asleep", "fresh", "asleep_s", "pcf_ok", "record.reason", "record.p2"}   # the regression's, in a cycle


def preflight(art):
    art = os.path.abspath(art)
    if not art.endswith(".bin") or not os.path.isfile(art):
        sys.exit(f"not a .bin: {art}")
    with open(art[:-4] + ".json") as f:
        m = json.load(f)
    if not (isinstance(m.get("token"), str) and re.fullmatch(r"0x[0-9a-f]{8}", m["token"])):
        sys.exit(f"{art}: manifest token {m.get('token')!r}")
    if not (isinstance(m.get("git_head"), str) and re.fullmatch(r"[0-9a-f]{40}", m["git_head"])):
        sys.exit(f"{art}: manifest git_head {m.get('git_head')!r}")
    if m.get("bin") != os.path.basename(art) or m.get("flavor") != "dev":
        sys.exit(f"{art}: manifest names {m.get('bin')}, flavour {m.get('flavor')}")
    with open(art, "rb") as f:
        if hashlib.sha256(f.read()).hexdigest() != m.get("bin_sha256"):
            sys.exit(f"{art}: its hash is not the manifest's")
    return art, m


def token():
    r = subprocess.run([AK820, "health"], capture_output=True, text=True)
    m = re.search(r"build_token\s+(0x[0-9a-f]{8})", r.stdout)
    return m.group(1) if m else None


def rec_signature(r):
    return (bool(r.get("slept")) and r.get("reason") == "charge" and r.get("secs", 99) <= 2
            and not (r.get("p1", CHRG) & CHRG))


def ring_signature(e):
    return (e.get("reason") == "charge" and bool(e.get("flags", 0) & SLEPT) and e.get("counted_s", 99) <= 2
            and not (int(str(e.get("p1", "0x10000")), 16) & CHRG))


def board_verdict(cycles, ring):
    recs = [c.get("record") or {} for c in cycles]
    if any(rec_signature(r) for r in recs) or any(ring_signature(e) for e in ring):
        return "regression"
    if (cycles and all(r.get("slept") and r.get("reason") == "key" and r.get("secs", 0) >= 10
                       and r.get("p2", 0) & RIG_P2 and r.get("strays", 1) == 0 for r in recs)
            and all(e.get("reason") == "key" and e.get("flags", 0) & SLEPT for e in ring)):
        return "normal"
    return "other"


def failing_checks(c):
    """Every check in deep_cycles's `good` expression a cycle failed (--board-only: no Mac timing)."""
    rec = c.get("record") or {}
    bad = {k for k, v in {"asleep": True, "awake_seen": True, "fresh": True, "no_reset": True,
                          "recovered": True, "pcf_ok": True, "key_gain": 1, "new_ge25": 0}.items() if c.get(k) != v}
    if c.get("asleep_s") is None:
        bad.add("asleep_s")
    bad |= {f"record.{k}" for k, ok in (("slept", bool(rec.get("slept"))), ("reason", rec.get("reason") == "key"),
                                        ("p2", bool(rec.get("p2", 0) & RIG_P2)), ("strays", rec.get("strays") == 0))
            if not ok}
    bad |= {f"link.{k}" for k in LINK_GATED if (c.get("link") or {}).get(k, 1) != 0}
    return bad


def explained(c, f, board):
    """A failed cycle is explained by the regression's symptoms on a board showing it, or by the power
    log's edges alone. The gates (keys, stalls, link, resets, recovery) are judged apart."""
    if board == "regression":
        return f <= SYMPTOMS
    return f == LOGGER and c.get("asleep_s") is None and c.get("pcf_ok") is False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--expect", required=True)
    ap.add_argument("--control", action="store_true", help="b2a07e0d37 itself: advance only on the regression")
    ap.add_argument("--n", type=int, default=2)
    ap.add_argument("--out", default=os.path.join(ROOT, "history", "pad-sweep-" + time.strftime("%Y%m%d")))
    a = ap.parse_args()
    if not 1 <= a.n <= 500:
        sys.exit("--n: 1-500")
    art, m = preflight(a.expect)
    tag = m["git_head"][:10]
    if a.control and not tag.startswith("b2a07e0d37"):
        sys.exit("--control is b2a07e0d37's artifact")
    got = token()
    if got != m["token"]:
        print(f"{tag}: the board's token is {got}, not {m['token']}: another image")
        sys.exit(4)
    if not cm.relay_on():
        print(f"{tag}: relay 2 is off")
        sys.exit(5)
    d = os.path.join(a.out, f"{tag}-{time.strftime('%H%M%S')}")
    os.makedirs(d, exist_ok=True)
    r = subprocess.run([sys.executable, os.path.join(HERE, "deep_cycles.py"), "--n", str(a.n),
                        "--board-only", "--out", d], capture_output=True, text=True)
    with open(os.path.join(d, "deep-cycles.log"), "w") as f:
        f.write(r.stdout + r.stderr)
    try:
        with open(os.path.join(d, "deep-cycles.json")) as f:
            dc = json.load(f)
    except (OSError, ValueError):
        dc = {}
    ring = ds.with_dev(od.wakes)
    res = judge(dc, ring, r.returncode, r.stdout, a.n, a.control)
    res.update({"artifact": os.path.basename(art), "head": m["git_head"], "token": m["token"],
                "lines": [ln for ln in r.stdout.splitlines() if "cycle " in ln or "deep cycles" in ln]})
    with open(os.path.join(d, "check.json"), "w") as f:
        json.dump(res, f, indent=1)
    report(tag, res)
    sys.exit(0 if res["advance"] else 2)


def judge(dc, ring, rc, stdout, n, control):
    cycles, summary = dc.get("cycles", []), dc.get("summary", {})
    board = board_verdict(cycles, ring)
    complete = (rc in (0, 1) and summary.get("cycles") == n and summary.get("stopped") is None
                and all(c.get("no_reset") and c.get("recovered") for c in cycles)
                and "cleanup incomplete" not in stdout)
    gates = all(c.get("key_gain") == 1 and c.get("new_ge25") == 0
                and all((c.get("link") or {}).get(k, 1) == 0 for k in LINK_GATED) for c in cycles)
    failed = [(c, failing_checks(c)) for c in cycles if not c.get("ok")]
    unexplained = [sorted(f) for c, f in failed if not f or not explained(c, f, board)]
    advance = (complete and gates and not unexplained
               and (board == "regression" if control else board in ("regression", "normal")))
    return {"control": control, "board": board, "complete": complete, "gates": gates, "strict_pass": rc == 0,
            "failing": [sorted(f) for _, f in failed], "unexplained": unexplained, "advance": advance,
            "summary": summary, "ring": ring}


def report(tag, res):
    print(f"{tag}: board {res['board']}, {'complete' if res['complete'] else 'INCOMPLETE'}, "
          f"gates {'ok' if res['gates'] else 'FAILED'}, deep_cycles {'PASS' if res['strict_pass'] else 'FAIL'}"
          + (f", unexplained {res['unexplained']}" if res["unexplained"] else "")
          + f" -> {'ADVANCE' if res['advance'] else 'STOP'}", flush=True)
    for ln in res.get("lines", []):
        print("   ", ln)
    for e in res["ring"]:
        print("    ring:", json.dumps(e))


if __name__ == "__main__":
    main()
