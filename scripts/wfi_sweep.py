#!/usr/bin/env python3
"""The deep sleep's WFI placement, swept on the diagnostic image
(plans/DEEP-SLEEP-REGRESSION-2026-10-20.md; mac/sleep-wfi-diag):

    venv/bin/python scripts/wfi_sweep.py [--variants ff,0,1,...,7] [--n 2] [--out DIR]
    venv/bin/python scripts/wfi_sweep.py --variants 9,a    # the flash probe (mac/sleep-flash-probe)

For each variant, op 14 sub 7 picks the sleep's WFI sequence, then
`deep_cycles.py --n N --board-only` runs it through the ladder over the
current slider position. Variant k (0-7) puts the WFI at (2k + 6) mod 16 in
a 16-byte line, 8 ran it from SRAM (466aa04636 only), ff is the built C
sequence. 9 and 10 are the flash probe: the WFI from SRAM, then known flash
words read back; op 14 sub 8's tallies are cleared before each and kept.
Each variant's verdict and its wake ring's last entries are kept; the
sequence is put back to ff at the end, whatever happened.
"""
import argparse, json, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import deep_sleep as ds  # noqa: E402
import overrun_diag as od  # noqa: E402

cm = ds.cm


def variant(h, v):
    """op 14 sub 7: v 0-7, 9, 10 or 0xFF sets (8 on 466aa04636 only), 0xFE reads; returns the sequence in force."""
    r = cm.xfer(h, [cm.SET_VALUE, cm.HEALTH_CH, cm.HC_DEVHOOK, 14, 7, v] + [0] * 25)
    if len(r) < 32 or r[3] != 14 or r[4] != 7:
        raise SystemExit(f"op 14 sub 7 unhandled ({bytes(r[:8]).hex()}): not the diagnostic image")
    return r[5]


def probe(h, clear):
    """op 14 sub 8: the flash probe's tallies (sleep.h); clear zeroes them after the read."""
    r = cm.xfer(h, [cm.SET_VALUE, cm.HEALTH_CH, cm.HC_DEVHOOK, 14, 8, 1 if clear else 0] + [0] * 25)
    if len(r) < 32 or r[3] != 14 or r[4] != 8:
        raise SystemExit(f"op 14 sub 8 unhandled ({bytes(r[:8]).hex()}): not the probe image")
    t = bytes(r[5:26])
    u16 = lambda o: int.from_bytes(t[o:o + 2], "little")
    u32 = lambda o: int.from_bytes(t[o:o + 4], "little")
    return {"wakes": u16(0), "bad_wakes": u16(2), "bad_pos": f"{u16(4):#06x}", "as_last": u16(6),
            "as_ones": u16(8), "as_zero": u16(10), "first_bad": f"{u32(12):#010x}",
            "first_want": f"{u32(16):#010x}", "first_pos": t[20]}


def offset(v):
    return {8: "SRAM", 9: "probe, far first", 10: "probe, near first", 0xFF: "as built"}.get(
        v, f"{(2 * v + 6) % 16:2d} mod 16")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variants", default="ff,0,1,2,3,4,5,6,7")
    ap.add_argument("--n", type=int, default=2)
    ap.add_argument("--out", default=os.path.join(HERE, "..", "history", "wfi-sweep-" + time.strftime("%Y%m%d-%H%M")))
    a = ap.parse_args()
    vs = [int(x, 16) for x in a.variants.split(",")]
    if any(v > 10 and v != 0xFF for v in vs):
        sys.exit("--variants: 0-7, 8 (466aa04636 only), 9, a or ff")
    res = []
    try:
        for v in vs:
            got = ds.with_dev(lambda h: variant(h, v))
            if got != v:
                raise SystemExit(f"variant {v:#x} read back as {got:#x}")
            if v in (9, 10):
                ds.with_dev(lambda h: probe(h, True))
            out = os.path.join(a.out, f"v{v:02x}")
            r = subprocess.run([sys.executable, os.path.join(HERE, "deep_cycles.py"), "--n", str(a.n),
                                "--board-only", "--out", out], capture_output=True, text=True)
            lines = [ln for ln in r.stdout.splitlines() if "cycle " in ln or "deep cycles" in ln]
            ring = ds.with_dev(od.wakes)[-a.n:]
            slept = r.returncode == 0
            row = {"variant": v, "wfi": offset(v), "slept": slept, "cycles": lines, "ring": ring}
            if not slept:   # its own refusal or traceback, which the line filter drops
                row["error"] = (r.stdout + r.stderr).strip().splitlines()[-3:]
                for ln in row["error"]:
                    print("    !", ln, flush=True)
            verdict = "PASS" if slept else "FAIL"
            if v in (9, 10):
                # codex (flash-probe review, P2): the probe has its own verdict. A
                # clean probe needs wakes probed and none wrong, and says only
                # that these data reads came back right after the wake.
                pr = row["probe"] = ds.with_dev(lambda h: probe(h, False))
                row["probe_verdict"] = ("NO WAKES" if not pr["wakes"] else
                                        "BAD READS" if pr["bad_wakes"] else "CLEAN")
                verdict = f"sleep {verdict}, probe {row['probe_verdict']}"
                slept = slept and row["probe_verdict"] == "CLEAN"
            row["pass"] = slept
            res.append(row)
            print(f"variant {v:#04x} ({offset(v)}): {verdict}", flush=True)
            if "probe" in row:
                print("    probe:", json.dumps(row["probe"]), flush=True)
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
    print("summary:", ", ".join(f"{r['wfi']} {'ok' if r['pass'] else 'FAIL'}"
                                + (f" (probe {r['probe_verdict']})" if "probe_verdict" in r else "") for r in res))


if __name__ == "__main__":
    main()
