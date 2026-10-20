"""How exactly does a lock-LED frame's anchor land on the board, in time?
(plans/LOCK-LED-CHANNEL-PLAN.md, resume item 5: the wireless clock.)

Windows, over BLE (the board on BT, the cable in only for raw HID readback;
the daemon stopped or not holding lockchan.lock). For each of N short P0
frames, the host notes when the anchor write (the preamble's 3rd transition)
completed, by perf_counter; the board records when that transition arrived
(lockchan's anchor_ms, its timer_read32). Board time is mapped onto host time
through raw HID (devhook op 10's board_ms, at the midpoint of the request,
the lowest-RTT samples of a burst, at the start and the end, for the offset
and the drift). The spread of (board anchor - host anchor completion) is the
jitter a clock frame would carry.

    python scripts/win_lockchan_anchor_jitter.py [N] [--gap MS]
"""
import ctypes
import json
import os
import statistics
import sys
import time
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "hostagent"))
from ak820health import open_device  # noqa: E402
from lockchan import encode, P0  # noqa: E402
import ledchannel_test as L  # noqa: E402

BLE = "{00001812-0000-1000-8000-00805f9b34fb}_dev_vid&010c45_pid&fefe_rev&0138_cc405fca52bc&col01"
SET_VALUE, CH, HC = 0x07, 0x13, 0x76


def xfer(h, op, arg=0):
    h.write(bytes([0, SET_VALUE, CH, HC, op, arg]) + bytes(27))
    for _ in range(5):
        r = h.read(32, 1000)
        if r and len(r) == 32 and r[1] == CH and r[2] == HC and r[3] == op:
            return bytes(r)
    sys.exit("no reply")


def u32(b):
    return int.from_bytes(b, "little")


def board_sample(h):
    """(host midpoint s, board ms, rtt s)."""
    t1 = time.perf_counter()
    r = xfer(h, 10)
    t2 = time.perf_counter()
    return (t1 + t2) / 2, u32(r[25:29]), t2 - t1


def best(h, n=40):
    """The lowest-RTT sample of a burst: the tightest host-board pair."""
    return min((board_sample(h) for _ in range(n)), key=lambda s: s[2])


class Sender:
    """SET_INDICATORS on the BLE keyboard, one handle, completion times."""

    def __init__(self, gap_ms):
        L.win_take_lock()
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        k32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                        ctypes.c_void_p]

        class GUID(ctypes.Structure):
            _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]

        class KIP(ctypes.Structure):
            _fields_ = [("UnitId", wintypes.USHORT), ("LedFlags", wintypes.USHORT)]

        self.KIP, self.k32 = KIP, k32
        kbd = GUID(0x884B96C3, 0x56EF, 0x11D1, (ctypes.c_ubyte * 8)(0xBC, 0x8C, 0, 0xA0, 0xC9, 0x14, 0x05, 0xDD))
        cm, n = ctypes.windll.cfgmgr32, wintypes.ULONG()
        cm.CM_Get_Device_Interface_List_SizeW(ctypes.byref(n), ctypes.byref(kbd), None, 0)
        buf = ctypes.create_unicode_buffer(n.value)
        cm.CM_Get_Device_Interface_ListW(ctypes.byref(kbd), None, buf, n, 0)
        paths = [p for p in buf[:n.value].split("\0") if p and len(p.split("#")) == 4 and p.lower().split("#")[1] == BLE]
        if len(paths) != 1:
            sys.exit(f"the BLE keyboard: found {paths}")
        self.h = k32.CreateFileW(paths[0], 0, 3, None, 3, 0, None)
        self.got = wintypes.DWORD()
        q = KIP()
        if not k32.DeviceIoControl(self.h, 0x000B0040, None, 0, ctypes.byref(q), 4, ctypes.byref(self.got), None):
            sys.exit(f"query: error {ctypes.get_last_error()}")
        self.unit, self.gap = q.UnitId, gap_ms / 1000
        self.last = 0.0

    def write(self, v):
        flags = ((v & 0x01) << 1) | ((v & 0x02) << 1) | ((v & 0x04) >> 2)
        wait = self.last + self.gap - time.perf_counter()
        if wait > 0:
            time.sleep(wait)
        p = self.KIP(self.unit, flags)
        if not self.k32.DeviceIoControl(self.h, 0x000B0008, ctypes.byref(p), 4, None, 0, ctypes.byref(self.got), None):
            sys.exit(f"set: error {ctypes.get_last_error()}")
        self.last = time.perf_counter()
        return self.last


def main():
    args = sys.argv[1:]
    gap = 16
    if "--gap" in args:
        i = args.index("--gap")
        gap = int(args[i + 1])
        del args[i:i + 2]
    n = int(args[0]) if args else 20
    h = open_device()
    s = Sender(gap)
    user = L.win_user_leds()
    first = best(h)
    samples = []
    for i in range(n):
        time.sleep(max(0.0, 0.7 - (time.perf_counter() - s.last)))
        s.write(user ^ 0x01)
        s.write(user)
        time.sleep(0.3)
        w = encode(bytes([i & 0xFF, 1, 2, 3, 4]), type=9, seq=i & 0xFF, profile=P0, user=user)
        before = u32(xfer(h, 10)[4:8])
        times = [s.write(v) for v in w]
        time.sleep(0.6)
        r = xfer(h, 10)
        frames, anchor_ms, seq = u32(r[4:8]), u32(r[17:21]), r[15]
        if frames != before + 1 or seq != i & 0xFF:
            print(f"frame {i}: not committed (frames {before} -> {frames}, seq {seq})")
            continue
        samples.append((times[2], anchor_ms))
    last = best(h)
    # host s -> board ms, linear between the two tight pairs
    rate = (last[1] - first[1]) / (last[0] - first[0])     # board ms per host s
    to_board = lambda t: first[1] + (t - first[0]) * rate
    lat = [((a - to_board(t)) + 2**31) % 2**32 - 2**31 for t, a in samples]
    out = {"frames": n, "committed": len(samples), "gap_ms": gap,
           "rate_ms_per_s": rate, "sync_rtt_ms": [first[2] * 1000, last[2] * 1000],
           "latency_ms": lat}
    if lat:
        out.update({"mean": statistics.mean(lat), "sd": statistics.pstdev(lat),
                    "min": min(lat), "max": max(lat), "spread": max(lat) - min(lat)})
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
