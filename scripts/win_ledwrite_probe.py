#!/usr/bin/env python3
"""Can a Windows process write the AK820 Pro's LED output report through the
2.4 GHz dongle, with the lock-LED channel's bits, without moving the user's
locks? (plans/LOCK-LED-CHANNEL-PLAN.md; plans/GREMLIN-RESULTS.md.)

Windows only. Finds the dongle's boot-keyboard collection (0C45:FDFD MI_00)
from the Configuration Manager's list -- which opens nothing -- and opens only
that collection. Never hid_enumerate (CLAUDE.md).

Every report carries the user's CURRENT Num, Caps and Scroll (read with
GetKeyState just before), so only the extra bits move; the last write restores
the plain lock byte. Each step prints the write's result and the lock state
read back from Windows afterwards, which must not change.

    win_ledwrite_probe.py [--bits 0x08,0x10,0x20,0x40,0x80] [--gap-ms 200]
                          [--via write|setoutput]

What reached the board is read separately over the cable on a dev build:
scripts/devhook.py leds   (the inbound 5A trace; `leds reset` first).
"""
import argparse, ctypes, sys, time
from ctypes import wintypes

DONGLE = ("vid_0c45", "pid_fdfd")
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
hid = ctypes.WinDLL("hid", use_last_error=True)
user32 = ctypes.WinDLL("user32")
k32.CreateFileW.restype = wintypes.HANDLE
k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
k32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                          ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
hid.HidD_SetOutputReport.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
INVALID = wintypes.HANDLE(-1).value


class GUID(ctypes.Structure):
    _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD),
                ("d", ctypes.c_ubyte * 8)]


def listed_hid_paths():
    """Present HID interface paths, from the Configuration Manager: no opens."""
    g = GUID(0x4D1E55B2, 0xF16F, 0x11CF, (ctypes.c_ubyte * 8)(0x88, 0xCB, 0, 0x11, 0x11, 0, 0, 0x30))
    cm = ctypes.windll.cfgmgr32
    for _ in range(4):
        n = wintypes.ULONG()
        if cm.CM_Get_Device_Interface_List_SizeW(ctypes.byref(n), ctypes.byref(g), None, 0):
            raise OSError("CM_Get_Device_Interface_List_SizeW failed")
        buf = ctypes.create_unicode_buffer(n.value)
        cr = cm.CM_Get_Device_Interface_ListW(ctypes.byref(g), None, buf, n, 0)
        if cr == 0x1A:          # CR_BUFFER_SMALL: the list grew; ask again
            continue
        if cr:
            raise OSError(f"CM_Get_Device_Interface_ListW: CONFIGRET {cr}")
        return [p for p in buf[:n.value].split("\0") if p]
    raise OSError("the HID interface list kept growing")


def dongle_keyboard_path():
    """MI_00 of the dongle, matched on the hardware-id field only (the second
    of the path's '#' fields), as ak820text.raw_hid_paths does."""
    hits = []
    for p in listed_hid_paths():
        f = p.lower().split("#")
        if len(f) != 4 or f[0] != r"\\?\hid":
            continue
        tokens = f[1].split("&")
        if tuple(tokens[:2]) == DONGLE and tokens[2:] == ["mi_00"]:
            hits.append(p)
    if len(hits) != 1:
        sys.exit(f"expected one dongle MI_00, found {len(hits)}: {hits}")
    return hits[0]


def locks():
    """The user's lock state as the HID LED byte: Num 0x01, Caps 0x02, Scroll 0x04."""
    on = lambda vk: user32.GetKeyState(vk) & 1
    return on(0x90) | (on(0x14) << 1) | (on(0x91) << 2)


def send(h, via, byte):
    buf = (ctypes.c_ubyte * 2)(0x00, byte)      # report id 0 (none declared), the LED byte
    if via == "setoutput":
        ok = hid.HidD_SetOutputReport(h, buf, 2)
    else:
        done = wintypes.DWORD()
        ok = k32.WriteFile(h, buf, 2, ctypes.byref(done), None)
    return "ok" if ok else f"FAILED (error {ctypes.get_last_error()})"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bits", default="0x08,0x10,0x20,0x40,0x80")
    ap.add_argument("--gap-ms", type=int, default=200)
    ap.add_argument("--via", choices=["write", "setoutput"], default="write")
    a = ap.parse_args()
    bits = [int(b, 0) for b in a.bits.split(",")]
    if any(b & 0x07 for b in bits):
        sys.exit("refusing: a bit in 0x07 would move the user's Num, Caps or Scroll")

    path = dongle_keyboard_path()
    h = k32.CreateFileW(path, 0x40000000, 3, None, 3, 0, None)      # GENERIC_WRITE, share r/w
    if h in (None, INVALID):
        sys.exit(f"write-open refused: error {ctypes.get_last_error()} on {path}")
    try:
        base = locks()
        print(f"dongle MI_00: {path}\nuser's locks: 0x{base:02X} (Num 0x01, Caps 0x02, Scroll 0x04)")
        for b in bits:
            base = locks()                       # re-read: the user may press a lock
            r1 = send(h, a.via, base | b)
            time.sleep(a.gap_ms / 1000)
            r2 = send(h, a.via, base)
            time.sleep(a.gap_ms / 1000)
            after = locks()
            note = "" if after == base else f"  <-- LOCKS MOVED to 0x{after:02X}"
            print(f"0x{base | b:02X} {r1:>4}, back to 0x{base:02X} {r2:>4}; locks after 0x{after:02X}{note}")
    finally:
        k32.CloseHandle(h)


if __name__ == "__main__":
    main()
