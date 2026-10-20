#!/usr/bin/env python3
"""Phase 2's ladder on the board (plans/PHASE2-BUILD-PLAN.md, validation), and
Power saver's ceiling (plans/POWER-MODES-REVISION-PLAN.md, validation 4):
every enabled lighting effect, its stages and its ceiling forced over
HC_DEVHOOK op 14, with the board's own LED drive read back (the committed
frames, ak820battery's `led drive`).

    venv/bin/python scripts/ladder_check.py [--rig] [--out DIR]

Two parts, on USB (the cable): a held stage ignores the battery-only rule, and
so does the ceiling it takes, so the relay can stay on.

1. **The stages**, in Always on at hue 191, sat 255, value 255: active, dim,
   off, dark, active again. Codex's implementation review, must-fix 1: an
   effect that rewrites only some LEDs a frame (Jellybean Raindrops) must
   still go dark at once on the off stages, and come back whole on the wake.
   Pass: off and dark read exactly 0.0%; dim below active (or both 0); the
   second active within 20% of the first (animated effects move).
2. **Power saver's ceiling:** ACTIVE held in Always on (the reference) and in
   Power saver, at value 255 and at 100. Pass:
   - at 255, Saver at or below Always on, and for the effects that convert
     at the user's value (solid, Alphas/Mods, the cycles, Jellybean) at most
     CAPPED_MAX of it (QMK's CIE curve: 128 drives 48 of 255's 255); DRIFT,
     whose knob is linear in drive, at most DRIFT_MAX;
   - at 100, under the ceiling, Saver matches Always on for every effect,
     DRIFT included;
   - solid white at 255 in Saver within 5% of solid white at 128 in Always on.

Every check also reads op 14 back after a tick: the stage held, and the
ceiling the firmware says it applies (255, the dim's 64, Saver's 128).

Drive is the mean of SAMPLES readings across a hold, and the animated and
random effects (the cycles, Jellybean, the Heatmap, Rainfall, DRIFT) are
judged with ANIM_TOL, not on one sample. Typing Heatmap lights only keys that
are pressed: with --rig the relay's key (relay 1, F19 to the Mac) is tapped
through its holds, so its readings mean something; without, they are printed
and not judged.

JD's lighting, the stage and the mode are restored in a `finally`. Needs a dev
build with op 14. The LEDs change visibly for ~8 minutes.
"""
import argparse, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import current_map as cm  # noqa: E402  (set_rgb, read_rgb, force_stage, power_mode, power_state, led_drive)

EFFECTS = {1: "solid", 2: "alphas mods (JD's)", 3: "cycle all", 4: "cycle left-right", 5: "cycle up-down",
           6: "jellybean raindrops", 7: "typing heatmap", 8: "rainfall (custom)", 9: "drift (custom)"}
ANIMATED = {3, 4, 5, 6, 7, 8, 9}
CONVERTED = {1, 2, 3, 4, 5, 6}   # every LED at the user's value, so the CIE ratio holds
HEATMAP, DRIFT = 7, 9
FULL, SAVER = 0, 2               # power.c's POWER_MODE_FULL, POWER_MODE_SAVER
STAGES = ["active", "dim", "off", "dark", "active"]
STAGE_CAP = {"active": 255, "dim": 64, "off": 255, "dark": 255}   # op 14's value ceiling, in Always on
SETTLE = 3
SAMPLES = 5
ANIM_TOL = 0.25                  # and 15 per mille: two holds of a moving or random field
CAPPED_MAX = 0.35                # expected 48 / 255 = 0.19
DRIFT_MAX = 0.70                 # expected 128 / 255 = 0.50


def tol(eff, ref):
    return max(15, ref * ANIM_TOL) if eff in ANIMATED else max(2, ref * 0.03)


class Bench:
    def __init__(self, press=None):
        self.rig = press   # a key tap through relay 1, or None

    def set(self, rgb=None, mode=None, stage=0):
        h = cm.ak820text.open_device()
        try:
            if rgb is not None:
                cm.set_rgb(h, rgb)
            if mode is not None:
                cm.power_mode(h, mode)
            cm.force_stage(h, stage)
        finally:
            h.close()

    def state(self):
        time.sleep(0.3)   # op 14's set lands at power.c's next 10 Hz tick
        h = cm.ak820text.open_device()
        try:
            return cm.power_state(h)
        finally:
            h.close()

    def drive(self, eff):
        """The hold: SETTLE s, then SAMPLES readings, per mille; the Heatmap's
        key tapped first if there is a rig."""
        time.sleep(SETTLE)
        got = []
        for _ in range(SAMPLES):
            if self.rig is not None and eff == HEATMAP:
                for _ in range(4):
                    self.rig()
            got.append(cm.led_drive())
        if None in got:
            raise SystemExit("the board's LED drive did not read back")
        return sum(got) / len(got)


def judged(eff, rig):
    return eff != HEATMAP or rig is not None


def stages_pass(b, rows):
    bad = 0
    for eff, name in EFFECTS.items():
        got, caps = [], []
        for k, stage in enumerate(STAGES):
            b.set({"effect": eff, "h": 191, "s": 255, "v": 255} if k == 0 else None,
                  FULL if k == 0 else None, STAGES.index(stage) if stage != "active" else 0)
            st = b.state()
            caps.append(st["stage"] == (STAGES.index(stage) if stage != "active" else 0)
                        and st["val_cap"] == STAGE_CAP[stage])
            got.append(b.drive(eff))
        act, dim, off, dark, back = got
        drive_ok = (off == 0 and dark == 0 and (dim < act or act == 0)
                    and (act == 0 or abs(back - act) <= max(20, act * 0.2)))
        ok = all(caps) and (drive_ok or not judged(eff, b.rig))
        bad += not ok
        rows.append({"part": "stages", "effect": eff, "name": name, "active": act, "dim": dim, "off": off,
                     "dark": dark, "wake": back, "op14": all(caps), "ok": ok})
        print(f"{eff} {name:22} active {act / 10:5.1f}%  dim {dim / 10:5.1f}%  off {off / 10:4.1f}%  "
              f"dark {dark / 10:4.1f}%  wake {back / 10:5.1f}%  op 14 {'ok' if all(caps) else 'WRONG'}  "
              f"{'ok' if ok else 'FAIL'}{'' if judged(eff, b.rig) else ' (not judged: no --rig)'}")
    return bad


def held(b, rgb, mode, want_cap):
    """ACTIVE held in `mode` at `rgb`: (drive, op 14 agrees)."""
    b.set(rgb, mode, 0)
    st = b.state()
    return b.drive(rgb["effect"]), st["mode"] == mode and st["stage"] == 0 and st["val_cap"] == want_cap


def saver_pass(b, rows):
    bad = 0
    for v in (255, 100):
        for eff, name in EFFECTS.items():
            rgb = {"effect": eff, "h": 191, "s": 255, "v": v}
            ref, ok1 = held(b, rgb, FULL, 255)
            sav, ok2 = held(b, rgb, SAVER, 128)
            t = tol(eff, ref)
            if v == 255:
                drive_ok = sav <= ref + t
                if eff in CONVERTED:
                    drive_ok = drive_ok and sav <= ref * CAPPED_MAX + 2
                if eff == DRIFT:
                    drive_ok = drive_ok and sav <= ref * DRIFT_MAX + 2
            else:
                drive_ok = abs(sav - ref) <= t
            ok = ok1 and ok2 and (drive_ok or not judged(eff, b.rig))
            bad += not ok
            rows.append({"part": "saver", "value": v, "effect": eff, "name": name, "full": ref, "saver": sav,
                         "op14": ok1 and ok2, "ok": ok})
            print(f"v{v:<3} {eff} {name:22} Always on {ref / 10:5.1f}%  Power saver {sav / 10:5.1f}%"
                  f"  ({sav / ref if ref else 0:4.2f})  op 14 {'ok' if ok1 and ok2 else 'WRONG'}  "
                  f"{'ok' if ok else 'FAIL'}{'' if judged(eff, b.rig) else ' (not judged: no --rig)'}")
    w128, ok1 = held(b, {"effect": 1, "h": 0, "s": 0, "v": 128}, FULL, 255)
    w255, ok2 = held(b, {"effect": 1, "h": 0, "s": 0, "v": 255}, SAVER, 128)
    ok = ok1 and ok2 and abs(w255 - w128) <= w128 * 0.05
    bad += not ok
    rows.append({"part": "white", "full_128": w128, "saver_255": w255, "op14": ok1 and ok2, "ok": ok})
    print(f"white: Always on at 128 {w128 / 10:5.1f}%, Power saver at 255 {w255 / 10:5.1f}%  {'ok' if ok else 'FAIL'}")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rig", action="store_true", help="tap the relay's key through the Heatmap's holds")
    ap.add_argument("--out", default=os.path.join(HERE, "..", "history", "ladder-check-" + time.strftime("%Y%m%d-%H%M")))
    a = ap.parse_args()
    h = cm.ak820text.open_device()
    try:
        jd_rgb, jd_mode = cm.read_rgb(h), cm.power_mode(h)
    finally:
        h.close()
    if jd_mode is None:
        sys.exit("no HC_DEVHOOK op 14 on this firmware: flash a dev build first")
    print(f"start: lighting {jd_rgb}, power mode {jd_mode}")
    relay = sb = None
    if a.rig:
        import hid
        import sleep_baseline as sb   # the relay's own helpers (macOS opens it exclusively: one handle)
        devs = hid.enumerate(sb.VID, sb.PID)
        if len(devs) != 1:
            sys.exit(f"expected one USB relay, found {len(devs)}")
        relay = hid.Device(path=devs[0]["path"])
    rows, bad = [], 0
    try:
        b = Bench((lambda: sb.press(relay, 40, 120)) if relay is not None else None)
        bad += stages_pass(b, rows)
        bad += saver_pass(b, rows)
    finally:
        if relay is not None:
            sb.relay1(relay, False)
            relay.close()
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
        json.dump({"rows": rows, "rig": a.rig, "at": time.strftime("%Y-%m-%d %H:%M:%S")}, f, indent=1)
    print("ladder check:", "all pass" if not bad else f"{bad} FAILED")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
