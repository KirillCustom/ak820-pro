#!/usr/bin/env python3
"""Phase 2's ladder on the board (plans/PHASE2-BUILD-PLAN.md, validation):
every enabled lighting effect through every stage, forced over HC_DEVHOOK op
14, with the board's own LED drive read back (the committed frames,
ak820battery's `led drive`). Codex's implementation review, must-fix 1: an
effect that rewrites only some LEDs a frame (Jellybean Raindrops) must still
go dark at once on the off stages, and come back whole on the wake.

    venv/bin/python scripts/ladder_check.py [--out DIR]

Per effect: active, dim, off, dark, active again, each held SETTLE s. Pass:
off and dark read exactly 0.0% within SETTLE s; dim below active (or both 0);
the second active within 20% of the first (animated effects move). JD's
lighting, the stage and the mode are restored in a `finally`. Needs a dev
build with op 14 and USB (the cable): a forced stage ignores the battery-only
rule, so the relay can stay on. The LEDs change visibly for ~2 minutes.
"""
import argparse, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import current_map as cm  # noqa: E402  (set_rgb, read_rgb, force_stage, power_mode, led_drive)

EFFECTS = {1: "solid", 2: "alphas mods (JD's)", 3: "cycle all", 4: "cycle left-right", 5: "cycle up-down",
           6: "jellybean raindrops", 7: "typing heatmap", 8: "rainfall (custom)", 9: "drift (custom)"}
STAGES = ["active", "dim", "off", "dark", "active"]
SETTLE = 3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "..", "history", "phase2-ladder-check-" + time.strftime("%Y%m%d-%H%M")))
    a = ap.parse_args()
    h = cm.ak820text.open_device()
    try:
        jd_rgb, jd_mode = cm.read_rgb(h), cm.power_mode(h)
    finally:
        h.close()
    if jd_mode is None:
        sys.exit("no HC_DEVHOOK op 14 on this firmware: flash Phase 2's dev build first")
    print(f"start: lighting {jd_rgb}, power mode {jd_mode}")
    rows, bad = [], 0
    try:
        for eff, name in EFFECTS.items():
            got = []
            for k, stage in enumerate(STAGES):
                h = cm.ak820text.open_device()
                try:
                    if k == 0:
                        cm.set_rgb(h, {"effect": eff, "h": 191, "s": 255, "v": 255})
                    cm.force_stage(h, STAGES.index(stage) if stage != "active" else 0)
                finally:
                    h.close()
                time.sleep(SETTLE)
                got.append(cm.led_drive())   # per mille
            act, dim, off, dark, back = got
            ok = (off == 0 and dark == 0 and (dim < act or act == 0)
                  and (act == 0 or abs(back - act) <= max(20, act * 0.2)))
            bad += not ok
            rows.append({"effect": eff, "name": name, "active": act, "dim": dim, "off": off, "dark": dark,
                         "wake": back, "ok": ok})
            print(f"{eff} {name:22} active {act / 10:5.1f}%  dim {dim / 10:5.1f}%  off {off / 10:4.1f}%  "
                  f"dark {dark / 10:4.1f}%  wake {back / 10:5.1f}%  {'ok' if ok else 'FAIL'}")
    finally:
        h = cm.ak820text.open_device()
        try:
            cm.force_stage(h, None)
            cm.set_rgb(h, dict(jd_rgb))
            cm.power_mode(h, jd_mode)
            print(f"restored: lighting {cm.read_rgb(h)}, power mode {cm.power_mode(h)}")
        finally:
            h.close()
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "ladder-check.json"), "w") as f:
        json.dump({"rows": rows, "at": time.strftime("%Y-%m-%d %H:%M:%S")}, f, indent=1)
    print("ladder check:", "all effects pass" if not bad else f"{bad} FAILED")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
