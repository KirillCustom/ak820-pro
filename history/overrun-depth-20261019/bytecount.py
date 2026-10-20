"""Are the OEs real byte losses? The RX tap counts every byte the driver
reads; with N echo frames of 34 bytes, a deficit of about one byte per OE
says real, none says spurious. Two runs: probes on (many OEs), probes off."""
import json, os, subprocess, sys, time
sys.path.insert(0, "/Users/jdlien/code/ak820-pro/scripts")
import overrun_diag as od
ds, cm = od.ds, od.cm

def tap(h, arg):
    b = od.op(h, 15, [arg])
    return {"armed": b[0], "bytes": od.U32(b, 1)}

def r81(h):
    b = od.op(h, 17, [0])
    names = ["committed", "bad_sum", "lifetime", "uart_taint", "sent_91", "dropped", "acks_in_window"]
    return {n: od.U32(b, 4 * i) for i, n in enumerate(names)}

def run(mask_us, n_echo=1000):
    def prep(h):
        od.burst(h, 2); od.summary(h, 2); od.summary(h, 1)
        tap(h, 1)
        return r81(h)
    r0 = ds.with_dev(prep)
    e = od.Echo(n_echo)
    try:
        e.wait_ready()
        if mask_us:
            ds.with_dev(lambda h: od.burst(h, 3, 500, 20, 40, mask_us))
        res = e.finish()
    finally:
        e.close()
    time.sleep(1)
    def after(h):
        t = tap(h, 0); s = od.summary(h); r = r81(h); b = od.burst(h)
        od.burst(h, 2)
        return t, s, r, b
    t, s, r1, b = ds.with_dev(after)
    d81 = {k: r1[k] - r0[k] for k in r0}
    frames = d81["committed"] + d81["bad_sum"] + d81["lifetime"] + d81["uart_taint"]
    out = {"mask_us": mask_us, "echo_sent": res["n"], "echo_replied": res["replied"], "tap_bytes": t["bytes"],
           "frames_seen": frames, "r81": d81, "oe": s["oe_obs"], "oe_drain": s["oe_drain"], "probe_done": b["done"],
           "deficit_vs_sent": 34 * res["n"] - t["bytes"]}
    print(json.dumps(out), flush=True)
    return out

if __name__ == "__main__":
    outs = [run(0), run(1500), run(0), run(1500)]
    json.dump(outs, open(sys.argv[1], "w"), indent=1)
