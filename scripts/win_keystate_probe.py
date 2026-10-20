"""How fresh is GetKeyState's toggle bit on a thread that is not the one
receiving input? (plans/LOCK-LED-CHANNEL-PLAN.md, R2-2.) Windows only.

Toggles Scroll Lock with SendInput from the main thread, and on a second
thread reads its toggle bit every 5 ms in one of three ways:
  plain   GetKeyState on a thread with no message queue
  pump    PeekMessage (PM_NOREMOVE) before each GetKeyState
  kbstate GetKeyboardState after a PeekMessage
and reports how long each took to see the change. Puts Scroll Lock back.
Touches no HID device; the board sees ordinary lock changes.

    python scripts/win_keystate_probe.py [rounds]
"""
import ctypes
import sys
import threading
import time
from ctypes import wintypes

u32 = ctypes.WinDLL("user32", use_last_error=True)
VK_SCROLL = 0x91


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def tap_scroll():
    ins = (INPUT * 2)()
    for i, flags in enumerate((0, 2)):   # down, KEYEVENTF_KEYUP
        ins[i].type = 1                  # INPUT_KEYBOARD
        ins[i].ki = KEYBDINPUT(VK_SCROLL, 0, flags, 0, 0)
    if u32.SendInput(2, ins, ctypes.sizeof(INPUT)) != 2:
        sys.exit(f"SendInput: error {ctypes.get_last_error()}")


def reader(mode, out, stop):
    msg = wintypes.MSG()
    buf = (ctypes.c_ubyte * 256)()

    def read():
        if mode != "plain":
            u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)   # PM_NOREMOVE
        if mode == "kbstate":
            u32.GetKeyboardState(buf)
            return buf[VK_SCROLL] & 1
        return u32.GetKeyState(VK_SCROLL) & 1

    while not stop.is_set():
        out.append((time.perf_counter(), read()))
        time.sleep(0.005)


def round_(mode):
    out, stop = [], threading.Event()
    t = threading.Thread(target=reader, args=(mode, out, stop))
    t.start()
    time.sleep(0.2)
    before = out[-1][1]
    t0 = time.perf_counter()
    tap_scroll()
    time.sleep(1.5)
    stop.set()
    t.join()
    seen = next((ts - t0 for ts, v in out if ts > t0 and v != before), None)
    return before, seen


def watch(seconds):
    """For a person's presses: a second thread, plain GetKeyState, every
    change of Num/Caps/Scroll printed with its time."""
    stop, last = threading.Event(), [None]

    def run():
        t0 = time.perf_counter()
        while not stop.is_set():
            v = tuple(u32.GetKeyState(k) & 1 for k in (0x90, 0x14, 0x91))
            if v != last[0]:
                print(f"{time.perf_counter() - t0:7.3f}s  num {v[0]} caps {v[1]} scroll {v[2]}", flush=True)
                last[0] = v
            time.sleep(0.005)

    t = threading.Thread(target=run)
    t.start()
    time.sleep(seconds)
    stop.set()
    t.join()


def main():
    if sys.argv[1:2] == ["watch"]:
        return watch(float(sys.argv[2]) if len(sys.argv) > 2 else 60)
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    start = u32.GetKeyState(VK_SCROLL) & 1
    for mode in ("plain", "pump", "kbstate"):
        for _ in range(rounds):
            before, seen = round_(mode)
            print(f"{mode:8} before {before} -> seen after "
                  + (f"{seen * 1000:.0f} ms" if seen is not None else "NEVER (1.5 s)"))
            time.sleep(0.3)
    # Back to how it was, by the main thread's own (fresh after SendInput) view.
    time.sleep(0.2)
    if (u32.GetKeyState(VK_SCROLL) & 1) != start:
        tap_scroll()
    print("scroll lock restored to", start)


if __name__ == "__main__":
    main()
