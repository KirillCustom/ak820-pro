#!/usr/bin/env python3
"""The lock-LED channel's Caps check on the Mac (plans/LOCK-LED-CHANNEL-PLAN.md,
resume item 3), with JD tapping Caps Lock while frames go:

    venv/bin/python scripts/mac_lockchan_caps.py [--taps 2] [--secs 180] [--out DIR]

Sends P3 frames back to back through the daemon's own stack (`ak820 lockchan
send`, which takes the channel's owner lock) until JD has changed Caps Lock
--taps times or --secs pass. Around each change it reads the board's LED
trace (devhook.py leds: the last 52 LED bytes the board received) and asks:

- did the sender see the change and give the frame up (Abandoned(UserChanged))?
- the first byte whose Caps differs from the frame's baseline is macOS's own
  report: did it keep bits 3-7 (the board's guard would then drop it, and
  the band would miss the change), or arrive clean?
- does the board end on the Mac's Caps (host_leds against the HID system)?

The board must be on BT, its USB cable in (the devhook reads go over USB).
"""
import argparse, json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
PY = os.path.join(ROOT, "venv", "bin", "python")
AK820 = os.path.join(ROOT, "ak820-agent", "target", "debug", "ak820")


def caps():
    out = subprocess.run([AK820, "lockchan", "check"], capture_output=True, text=True).stdout
    m = re.search(r"caps lock\s+Some\((true|false)\)", out)
    return None if not m else m.group(1) == "true"


def devhook(*a):
    r = subprocess.run([PY, os.path.join(HERE, "devhook.py"), *a], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        sys.exit(f"devhook.py {' '.join(a)}: {(r.stderr or r.stdout).strip()}")
    return json.loads(r.stdout) if a[-1] == "json" else r.stdout


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--taps", type=int, default=2)
    ap.add_argument("--secs", type=int, default=180)
    ap.add_argument("--out", default=os.path.join(ROOT, "history", "lockchan-mac-caps-" + time.strftime("%Y%m%d-%H%M")))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    events, frames, n = [], 0, 0
    t_end = time.time() + a.secs
    print(f"sending frames for up to {a.secs} s: tap Caps Lock {a.taps} times, a few seconds apart", flush=True)
    while time.time() < t_end and len(events) < a.taps:
        base = caps()
        if base is None:
            sys.exit("the HID system's Caps state could not be read")
        devhook("leds", "reset")
        n += 1
        t0 = time.time()
        r = subprocess.run([AK820, "lockchan", "send", "Caps check", f"frame {n}"], capture_output=True, text=True)
        line = (r.stdout + r.stderr).strip()
        after = caps()
        frames += 1
        if "UserChanged" not in line and after == base:
            continue
        trace = devhook("leds", "json")
        seq = trace["seq"]
        flip = next((e for e in seq if bool(e["byte"] & 0x02) != base), None)
        ev = {"frame": n, "at": time.strftime("%H:%M:%S", time.localtime(t0)), "baseline_caps": base,
              "caps_after": after, "sender": line, "host_leds": trace["host_leds"],
              "mac_report": flip, "mac_report_bits_3_7": None if flip is None else flip["byte"] & 0xF8,
              "board_matches_mac": after is not None and bool(trace["host_leds"] & 0x02) == after,
              "trace": seq}
        events.append(ev)
        print(f"frame {n}: Caps {base} -> {after}; {line}", flush=True)
        print(f"    macOS's report on the board: {flip}; bits 3-7 "
              f"{'n/a' if flip is None else hex(flip['byte'] & 0xF8)}; host_leds 0x{trace['host_leds']:02x}, "
              f"{'matches' if ev['board_matches_mac'] else 'DOES NOT MATCH'} the Mac's Caps", flush=True)
    res = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "frames_sent": frames, "events": events,
           "frames_board": devhook("frames", "json")}
    with open(os.path.join(a.out, "caps.json"), "w") as f:
        json.dump(res, f, indent=1)
    ok = len(events) >= a.taps and all(e["mac_report"] and e["mac_report_bits_3_7"] == 0
                                       and e["board_matches_mac"] for e in events)
    print(f"{len(events)} Caps change(s) in {frames} frames: {'PASS' if ok else 'CHECK'} ({a.out})")


if __name__ == "__main__":
    main()
