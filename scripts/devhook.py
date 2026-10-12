"""HC_DEVHOOK, dev builds only (hid_protocol.c; build.sh dev): press Fn+D, and
read or reset the display probe (graphics/display.c, DPROBE).

    scripts/devhook.py press | reset | read
"""
import sys
sys.path.insert(0, "/Users/jdlien/code/ak820-pro/hostagent")
from ak820health import open_device
SET_VALUE, CH, HC = 0x07, 0x13, 0x76
NAMES = [f"restore clear band {i}" for i in range(1, 9)] + [
    "restore: OS icon", "restore: conn row + clock", "restore: conn digit",
    "restore: battery (outline)", "restore: lock band", "restore: text slot",
    "hk: text slot", "hk: status overlay", "hk: lock band", "hk: clock", "hk: battery",
    "hk: conn digit", "text icon", "band clear", "debug page compose", "debug glyph pump"]
def xfer(h, op, arg=0):
    h.write(bytes([0, SET_VALUE, CH, HC, op, arg]) + bytes(27))   # report id + 32 bytes
    for _ in range(5):
        r = h.read(32, 1000)
        if r and r[1] == CH and r[2] == HC:
            if r[0] != SET_VALUE: sys.exit(f"unhandled: {bytes(r[:8]).hex()}")
            return bytes(r)
    sys.exit("no reply")
h = open_device()
cmd = sys.argv[1]
if cmd == "press": xfer(h, 1)
elif cmd == "reset": xfer(h, 3)
elif cmd == "read":
    vals = []
    for pg in (0, 1):
        r = xfer(h, 2, pg)
        vals += [r[4 + 2 * k] | (r[5 + 2 * k] << 8) for k in range(14)]
    for n, v in zip(NAMES, vals):
        if v: print(f"{v / 1000:7.2f} ms  {n}")
h.close()
