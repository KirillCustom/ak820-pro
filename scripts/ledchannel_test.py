"""The lock-LED channel's hands-free test (plans/BACKLOG.md, quill4gen7 item 1):
what reaches the board of the LED bytes this Mac writes over Bluetooth.

    venv/bin/python scripts/ledchannel_test.py [--out DIR] [TEST ...]

TEST is width, high, repeats, speed, random, caps or frames (default: the
first six, in that order; frames needs flash 2h). Each one resets the board's 5A trace (`scripts/devhook.py leds reset`,
over the cable), has the LED probe write a sequence over BLE (`seq` mode), then
reads the trace back and compares:
  width    0x01..0x1F then 0x00, 200 ms apart: which of the five bits arrive
  high     0x20, 0x40, 0x80, 0xE0, 0x24, each then 0x00: does the module mask
           the three padding bits, or pass them for the board to reject
  repeats  each value three times, 200 ms apart: is a repeat forwarded
  speed    Scroll Lock toggled 40 times, each write waiting for the last and
           then 40, 30, 20, 10 or 0 ms more (a write itself takes ~30 ms, so
           the starts are ~70 down to ~30 ms apart: the report gives the
           measured spacing), then the 40 issued at once without waiting, the
           fastest this Mac can send: do frames merge
  random   44 values over Num, Scroll and bits 3-4, each unlike the last,
           back to back: the channel's error rate at full speed
  caps     bits 3-4 set (0x18), then this Mac's own Caps Lock switched on and
           off in software, as a key press would: does the LED report the Mac
           sends for Caps keep bits 3-4 (0x1a, 0x18) or zero them (0x02,
           0x00)? Caps Lock is really on for ~0.6 s, so not mid-typing
  frames   whole frames from scripts/lockchan.py, six per profile (P1, P2,
           P0), text payloads of 20-37 bytes, each frame's writes back to back
           (the frames themselves are seconds apart), each after the sender's
           warm-up (bit 3 on and off, then the quiet): does the board's
           receiver (lockchan.c, flash 2h) commit each one exactly, and how
           long does it take. Pass/fail: exits 1 if any frame is wrong
Needs flash 2g (the trace; the slider on BT, linked), the probe app
(scripts/build-led-probe.sh) and the Mac unlocked (locked, it refuses every
write). Caps (0x02) shows CAPS on the LCD while it is set. The board's stall
and link-error counters are read before and after each test (ak820health.py),
since a missing frame can also be a UART overrun or a checksum reject. Writes a
JSON per test to DIR (default history/ledchannel-<date>/).
"""
import json, os, random, subprocess, sys, time

ROOT = "/Users/jdlien/code/ak820-pro"
APP = os.path.expanduser("~/Applications/AK820 LED Probe.app")
LOG = os.path.expanduser("~/Library/Logs/ak820pro/ledprobe.log")
PY = os.path.join(ROOT, "venv/bin/python")
RING = 52


def devhook(*args):
    r = subprocess.run([PY, os.path.join(ROOT, "scripts/devhook.py"), "leds", *args],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        sys.exit(f"devhook.py leds {' '.join(args)}: {(r.stderr or r.stdout).strip()}")
    return json.loads(r.stdout) if args == ("json",) else None


def health():
    r = subprocess.run([PY, os.path.join(ROOT, "hostagent/ak820health.py"), "--stalls", "--json"],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        sys.exit(f"ak820health.py: {(r.stderr or r.stdout).strip()}")
    h = json.loads(r.stdout)
    return {"count_ge_25ms": h["count_ge_25ms"], "loop_gap_max_ms": h["loop_gap_max_ms"],
            "blit_gap_max_ms": h["blit_gap_max_ms"], "rx_malformed": h["rx_malformed"],
            "uart_overrun": h["link"]["uart_overrun"], "uart_framing": h["link"]["uart_framing"]}


def send(gap_ms, vals):
    """Run the probe (seq, or aseq when gap_ms is None); return each write's
    start time for seq or completion time for aseq, in epoch seconds."""
    start = os.path.getsize(LOG) if os.path.exists(LOG) else 0
    mode = ["aseq"] if gap_ms is None else ["seq", str(gap_ms)]
    subprocess.run(["open", "-n", "-W", "-a", APP, "--args", *mode,
                    *[f"0x{v:02x}" for v in vals]], check=True, timeout=600)
    with open(LOG) as f:
        f.seek(start)
        lines = f.read().splitlines()
    if gap_ms is None:
        writes = [l for l in lines if " aseq " in l and "->" in l]
        t0 = next(float(l.split()[0]) for l in lines if "aseq: " in l)
        times = sorted(t0 + float(l.rsplit(" at ", 1)[1].split()[0]) / 1000 for l in writes)
    else:
        writes = [l for l in lines if ": seq " in l and "->" in l]
        times = [float(l.split()[0]) - float(l.rsplit(" in ", 1)[1].split()[0]) / 1000 for l in writes]
    bad = [l for l in writes if "-> 0x00000000" not in l]
    if len(writes) != len(vals) or bad:
        sys.exit(f"the probe wrote {len(writes)} of {len(vals)}, {len(bad)} refused:\n" +
                 "\n".join((bad or lines)[-5:]))
    return times


def dedupe(vals):
    return [v for i, v in enumerate(vals) if i == 0 or v != vals[i - 1]]


def run(name, gap_ms, vals, out):
    if len(vals) > RING:
        sys.exit(f"{name}: {len(vals)} writes, more than the ring's {RING}")
    before = health()
    devhook("reset")
    times = send(gap_ms, vals)
    time.sleep(0.5)   # any frame still in flight
    tr = devhook("json")
    after = health()
    delta = {k: after[k] - before[k] for k in ("count_ge_25ms", "rx_malformed", "uart_overrun", "uart_framing")}
    iv = sorted(round((b - a) * 1000, 1) for a, b in zip(times, times[1:]))
    got = [e["byte"] for e in tr["seq"]]
    gaps = [e["gap_ms"] for e in tr["seq"][1:] if e["gap_ms"] is not None]
    # Which model fits what arrived: every write; only changes; only the low
    # three bits (and then only changes, since masking makes repeats).
    low3 = [v & 0x07 for v in vals]
    fits = [m for m, want in (("every write", vals), ("changes only", dedupe(vals)),
                              ("low 3 bits", low3), ("low 3 bits, changes only", dedupe(low3)),
                              ("low 5 bits, changes only", dedupe([v & 0x1F for v in vals])))
            if got == want]
    res = {"test": name, "gap_ms": gap_ms, "sent": vals, "received": got,
           "frames": tr["frames"], "accepted": tr["accepted"], "fits": fits,
           "rx_gap_ms": {"min": min(gaps, default=None), "median": sorted(gaps)[len(gaps) // 2] if gaps else None,
                         "max": max(gaps, default=None)},
           "host_interval_ms": {"min": iv[0], "median": iv[len(iv) // 2], "max": iv[-1]} if iv else None,
           "health_before": before, "health_after": after, "health_delta": delta,
           "trace": tr, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    with open(os.path.join(out, f"{name}.json"), "w") as f:
        json.dump(res, f, indent=1)
    print(f"{name:14} sent {len(vals):2}, received {tr['frames']:2} (accepted {tr['accepted']:2}); "
          f"fits: {', '.join(fits) or 'NONE'}; rx gaps {res['rx_gap_ms']['min']}-{res['rx_gap_ms']['max']} ms "
          f"(median {res['rx_gap_ms']['median']}), sent every {iv[len(iv) // 2] if iv else '-'} ms (median); "
          f"stalls +{delta['count_ge_25ms']}, overruns +{delta['uart_overrun']}, malformed +{delta['rx_malformed']}")
    if not fits:
        print(f"{'':14} sent     {' '.join(f'{v:02x}' for v in vals)}\n{'':14} received {' '.join(f'{v:02x}' for v in got)}")
    time.sleep(2)   # let the band's settled Scroll Lock catch up between tests
    return res


def caps_test(out):
    before = health()
    devhook("reset")
    start = os.path.getsize(LOG) if os.path.exists(LOG) else 0
    subprocess.run(["open", "-n", "-W", "-a", APP, "--args", "capsmix"], check=True, timeout=120)
    with open(LOG) as f:
        f.seek(start)
        lines = f.read().splitlines()
    if not any("capsmix: done" in l for l in lines):
        sys.exit("capsmix did not finish:\n" + "\n".join(lines[-5:]))
    time.sleep(0.5)
    tr = devhook("json")
    after = health()
    got = [e["byte"] for e in tr["seq"]]
    verdict = ("kept" if 0x1A in got else "zeroed" if 0x02 in got else "no Caps report seen")
    res = {"test": "caps", "received": got, "bits_3_4_on_caps": verdict, "probe_log": lines,
           "health_before": before, "health_after": after, "trace": tr,
           "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    with open(os.path.join(out, "caps.json"), "w") as f:
        json.dump(res, f, indent=1)
    print(f"{'caps':14} received {' '.join(f'{v:02x}' for v in got)}: bits 3-4 {verdict} "
          f"by the Mac's Caps report; stalls +{after['count_ge_25ms'] - before['count_ge_25ms']}")


def frames(*args):
    r = subprocess.run([PY, os.path.join(ROOT, "scripts/devhook.py"), "frames", *args],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        sys.exit(f"devhook.py frames {' '.join(args)}: {(r.stderr or r.stdout).strip()}")
    return json.loads(r.stdout) if args == ("json",) else None


def frames_test(out):
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    from lockchan import encode, P0, P1, P2
    rng = random.Random(8202)
    words = "the a of and to in love night light song blue heart home road dream fire rain".split()
    results, health_log, stalled = [], [], False
    for prof, name in ((P1, "P1"), (P2, "P2"), (P0, "P0")):
        before = health()
        frames("reset")
        ok = 0
        for i in range(6):
            text = " ".join(rng.choice(words).title() for _ in range(12))[:rng.randint(20, 37)].encode()
            seq = (prof * 16 + i) & 0xFF
            typ = 1 + i % 3
            w = encode(text, type=typ, seq=seq, profile=prof, user=0)
            n0 = frames("json")["frames"]
            send(0, [0x08, 0x00])   # the warm-up, so the board has the baseline,
            time.sleep(0.3)         # then the quiet (the probe exits once its last write completes)
            t0 = time.time()
            send(0, w)
            host_s = time.time() - t0
            time.sleep(0.6)
            st = frames("json")
            l = st["last"]
            good = (st["frames"] == n0 + 1 and not st["busy"] and l["seq"] == seq and l["type"] == typ
                    and l["profile"] == prof and l["len"] == len(text)
                    and bytes.fromhex(l["payload"]) == text)
            ok += good
            results.append({"profile": name, "seq": seq, "text": text.decode(), "writes": len(w),
                            "ok": good, "host_s": round(host_s, 2),
                            "board_anchor_to_commit_ms": (l["commit_ms"] - l["anchor_ms"]) & 0xFFFFFFFF if good else None,
                            "receiver": st})
            if not good:
                print(f"{'':14} {name} frame {i} NOT received: frames {st['frames']}, aborts "
                      + ", ".join(f"{k} {v}" for k, v in st["aborts"].items() if v))
        after = health()
        stalls = after["count_ge_25ms"] - before["count_ge_25ms"]
        stalled = stalled or stalls > 0
        health_log.append({"profile": name, "health_before": before, "health_after": after})
        mine = [r for r in results if r["profile"] == name]
        span = sorted(r["board_anchor_to_commit_ms"] for r in mine if r["ok"])
        print(f"frames-{name:8} {ok}/6 committed exactly; {mine[-1]['writes']} writes for the last; "
              f"anchor to commit {span[0] if span else '-'}-{span[-1] if span else '-'} ms; "
              f"stalls +{stalls}, overruns +{after['uart_overrun'] - before['uart_overrun']}")
    with open(os.path.join(out, "frames.json"), "w") as f:
        json.dump({"frames": results, "health": health_log}, f, indent=1)
    # Two verdicts, kept apart: did every frame decode, and did the board stay
    # inside its 25 ms budget meanwhile (codex, flash 2h verify).
    return all(r["ok"] for r in results), not stalled


def main():
    args = sys.argv[1:]
    out = os.path.join(ROOT, "history", "ledchannel-" + time.strftime("%Y-%m-%d"))
    if args[:1] == ["--out"]:
        if len(args) < 2: sys.exit(__doc__)
        out, args = args[1], args[2:]
    names = ("width", "high", "repeats", "speed", "random", "caps", "frames")
    tests = args or list(names[:6])
    if any(t not in names for t in tests):
        sys.exit(__doc__)
    os.makedirs(out, exist_ok=True)
    failed = False
    for t in tests:
        if t == "width":
            run("width", 200, list(range(1, 32)) + [0], out)
        elif t == "high":
            run("high", 200, [x for v in (0x20, 0x40, 0x80, 0xE0, 0x24) for x in (v, 0)], out)
        elif t == "repeats":
            run("repeats", 200, [x for v in (0x04, 0x00, 0x01, 0x00, 0x08, 0x00) for x in (v, v, v)], out)
        elif t == "speed":
            toggles = [0x04 if i % 2 == 0 else 0x00 for i in range(40)]
            for gap in (40, 30, 20, 10, 0):
                run(f"speed-{gap}", gap, toggles, out)
            run("speed-async", None, toggles, out)
        elif t == "random":
            rng, usable, vals = random.Random(820), (0x01, 0x04, 0x08, 0x10), []
            while len(vals) < 43:
                v = sum(b for b in usable if rng.random() < 0.5)
                if not vals or v != vals[-1]: vals.append(v)
            run("random", 0, vals + ([0] if vals[-1] else []), out)
        elif t == "caps":
            caps_test(out)
        elif t == "frames":
            decoded, in_budget = frames_test(out)
            if not decoded:
                failed = True
                print("frames: DECODING FAILED (frames.json has each frame)")
            if not in_budget:
                failed = True
                print("frames: a pass >= 25 ms during the frames (the 25 ms budget)")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
