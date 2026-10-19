#!/usr/bin/env python3
"""Fn+B with the lock band changing in the same pass (codex, the power-mode
icons' review, note 5): the overlay's synchronous clears, the bulb's tile and
a Caps or Fn repaint can share one housekeeping pass, and a 20-22 ms Caps
paint leaves little margin under 25 ms.

    venv/bin/python scripts/fnb_caps_test.py [--control] [--secs 90] [--every-ms 150]

On the pack (the bulb shows on battery only): relay 2 off, then the Mac's
Caps Lock toggled every --every-ms for --secs (IOKit's modifier lock state,
so the Mac sends the LED to every keyboard; the AK820's band repaints CAPS
each time). Without --control, JD presses Fn+B 15-20 times meanwhile,
cycling the four modes; JD's Caps key is his Fn, so Fn's band label changes
around every press too. --control is the same with nobody at the board: the
Caps paint's own cost.

Reads the board's stall counters before and after: pass if no new pass of
25 ms or more (count_ge_25ms). ⚠️ While it runs every keyboard on the Mac
types in capitals: do not type. Restored in a `finally`: the Caps Lock state
it found, relay 2 on.
"""
import argparse, ctypes, ctypes.util, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sleep_baseline as sb  # noqa: E402
import deep_sleep as ds  # noqa: E402

cm = sb.cm
_iokit = ctypes.cdll.LoadLibrary(ctypes.util.find_library("IOKit"))
_cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
_libc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("c"))
_iokit.IOServiceMatching.restype = ctypes.c_void_p
_iokit.IOServiceMatching.argtypes = [ctypes.c_char_p]
_iokit.IOServiceGetMatchingService.restype = ctypes.c_uint32
_iokit.IOServiceGetMatchingService.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
_iokit.IOServiceOpen.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
_iokit.IOHIDGetModifierLockState.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.POINTER(ctypes.c_bool)]
_iokit.IOHIDSetModifierLockState.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_bool]
_iokit.IOServiceClose.argtypes = [ctypes.c_uint32]
_libc.mach_task_self.restype = ctypes.c_uint32
CAPS = 1                 # kIOHIDCapsLockState
PARAM_CONNECT = 1        # kIOHIDParamConnectType


def caps_connect():
    svc = _iokit.IOServiceGetMatchingService(0, _iokit.IOServiceMatching(b"IOHIDSystem"))
    if not svc:
        raise SystemExit("no IOHIDSystem service")
    conn = ctypes.c_uint32()
    rc = _iokit.IOServiceOpen(svc, _libc.mach_task_self(), PARAM_CONNECT, ctypes.byref(conn))
    if rc:
        raise SystemExit(f"IOServiceOpen failed: {rc:#x}")
    return conn.value


def caps_get(conn):
    v = ctypes.c_bool()
    rc = _iokit.IOHIDGetModifierLockState(conn, CAPS, ctypes.byref(v))
    if rc:
        raise SystemExit(f"IOHIDGetModifierLockState failed: {rc:#x}")
    return v.value


def caps_set(conn, on):
    rc = _iokit.IOHIDSetModifierLockState(conn, CAPS, bool(on))
    if rc:
        raise SystemExit(f"IOHIDSetModifierLockState failed: {rc:#x}")


def stalls():
    st = ds.with_dev(lambda h: sb.health.read_stalls(h))
    return {k: st[k] for k in ("count_ge_10ms", "count_ge_25ms", "count_ge_25ms_nonflash", "key_presses")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--control", action="store_true", help="nobody at the board: the Caps paint alone")
    ap.add_argument("--secs", type=float, default=90.0)
    ap.add_argument("--every-ms", type=int, default=150)
    ap.add_argument("--out", default=os.path.join(HERE, "..", "history", "fnb-caps-" + time.strftime("%Y%m%d-%H%M")))
    a = ap.parse_args()
    if not 10 <= a.secs <= 300 or not 50 <= a.every_ms <= 2000:
        sys.exit("--secs 10-300, --every-ms 50-2000")
    conn = caps_connect()
    caps0 = caps_get(conn)
    res = {"args": vars(a), "caps_before": caps0}
    ok = False
    try:
        mode0, _ = ds.with_dev(ds.power_op)
        res["mode_before"] = mode0
        sb.relay2("off")
        deadline = time.time() + 30
        while not sb.on_battery():
            if time.time() > deadline:
                raise SystemExit("the board never reported the pack")
            time.sleep(1)
        time.sleep(3)
        s0 = stalls()
        res["before"] = s0
        cm.say(f"on the pack; Caps toggling every {a.every_ms} ms for {a.secs:.0f} s"
               + ("" if a.control else ": press Fn+B now, 15-20 times"))
        n, t_end, state = 0, time.time() + a.secs, caps0
        while time.time() < t_end:
            state = not state
            caps_set(conn, state)
            n += 1
            time.sleep(a.every_ms / 1000)
        caps_set(conn, caps0)
        time.sleep(2)
        s1 = stalls()
        res["after"] = s1
        res["toggles"] = n
        res["delta"] = {k: (s1[k] - s0[k]) & 0xFFFF for k in s0}
        res["mode_after"], _ = ds.with_dev(ds.power_op)
        ok = res["delta"]["count_ge_25ms"] == 0
        cm.say(f"{n} toggles; new passes: >=10 ms {res['delta']['count_ge_10ms']}, >=25 ms "
               f"{res['delta']['count_ge_25ms']} ({res['delta']['count_ge_25ms_nonflash']} not flash); "
               f"key presses {res['delta']['key_presses']}; mode {mode0} -> {res['mode_after']}")
    finally:
        sb.attempt("Caps Lock put back", caps_set, conn, caps0)
        sb.attempt("relay 2 on", sb.relay2, "on")
        _iokit.IOServiceClose(conn)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "fnb-caps.json"), "w") as f:
        json.dump(res, f, indent=1, default=str)
    print("fn+b / caps:", "PASS" if ok else "FAIL", json.dumps(res.get("delta", {})))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
