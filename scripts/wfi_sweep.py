#!/usr/bin/env python3
"""The deep sleep's WFI placement, swept on the diagnostic image
(plans/DEEP-SLEEP-REGRESSION-2026-10-20.md; mac/sleep-wfi-diag):

    venv/bin/python scripts/wfi_sweep.py [--variants ff,0,1,...,8] [--n 2] [--out DIR]

For each variant, op 14 sub 7 picks the sleep's WFI sequence, then
`deep_cycles.py --n N --board-only` runs it through the ladder over the
current slider position. Variant k (0-7) puts the WFI at (2k + 6) mod 16 in
a 16-byte line, 8 runs it from SRAM, ff is the built C sequence. Each
variant's verdict and its wake ring's last entries are kept; the sequence is
put back to ff at the end, whatever happened.
"""
import argparse, json, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import deep_sleep as ds  # noqa: E402
import overrun_diag as od  # noqa: E402

cm = ds.cm


def variant(h, v):
    """op 14 sub 7: v 0-8 or 0xFF sets, 0xFE reads; returns the sequence in force."""
    r = cm.xfer(h, [cm.SET_VALUE, cm.HEALTH_CH, cm.HC_DEVHOOK, 14, 7, v] + [0] * 25)
    if len(r) < 32 or r[3] != 14 or r[4] != 7:
        raise SystemExit(f"op 14 sub 7 unhandled ({bytes(r[:8]).hex()}): not the diagnostic image")
    return r[5]


def offset(v):
    return "SRAM" if v == 8 else "as built" if v == 0xFF else f"{(2 * v + 6) % 16:2d} mod 16"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variants", default="ff,0,1,2,3,4,5,6,7,8")
    ap.add_argument("--n", type=int, default=2)
    ap.add_argument("--out", default=os.path.join(HERE, "..", "history", "wfi-sweep-" + time.strftime("%Y%m%d-%H%M")))
    a = ap.parse_args()
    vs = [int(x, 16) for x in a.variants.split(",")]
    if any(v > 8 and v != 0xFF for v in vs):
        sys.exit("--variants: 0-8 or ff")
    res = []
    try:
        for v in vs:
            got = ds.with_dev(lambda h: variant(h, v))
            if got != v:
                raise SystemExit(f"variant {v:#x} read back as {got:#x}")
            out = os.path.join(a.out, f"v{v:02x}")
            r = subprocess.run([sys.executable, os.path.join(HERE, "deep_cycles.py"), "--n", str(a.n),
                                "--board-only", "--out", out], capture_output=True, text=True)
            lines = [ln for ln in r.stdout.splitlines() if "cycle " in ln or "deep cycles" in ln]
            ring = ds.with_dev(od.wakes)[-a.n:]
            ok = r.returncode == 0
            res.append({"variant": v, "wfi": offset(v), "pass": ok, "cycles": lines, "ring": ring})
            print(f"variant {v:#04x} ({offset(v)}): {'PASS' if ok else 'FAIL'}", flush=True)
            for ln in lines:
                print("   ", ln, flush=True)
    finally:
        try:
            print("sequence back to", hex(ds.with_dev(lambda h: variant(h, 0xFF))))
        except SystemExit as e:
            print("⚠️ could not put the sequence back:", e)
        os.makedirs(a.out, exist_ok=True)
        with open(os.path.join(a.out, "sweep.json"), "w") as f:
            json.dump({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "args": vars(a), "results": res}, f, indent=1)
    print("summary:", ", ".join(f"{r['wfi']} {'ok' if r['pass'] else 'FAIL'}" for r in res))


if __name__ == "__main__":
    main()
