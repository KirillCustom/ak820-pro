"""HC_DEVHOOK, dev builds only (hid_protocol.c; build.sh dev): press Fn+D,
read or reset the display probe (graphics/display.c, DPROBE), and set or read
the panel backlight.

    scripts/devhook.py press | reset | read
    scripts/devhook.py lcd [LEVEL | on | off]
    scripts/devhook.py leds [reset | json]

`lcd` alone reads the backlight; a LEVEL or on/off sets it (a LEVEL above the
maximum is clamped by the firmware, and the reply says what was set). Setting
is RAM only: nothing persists, and a reboot restores the level the keys last
saved. "off" is what Fn+Home does, without its overlay. Needs flash 2f or later
(the older dev builds answer "unhandled").

`leds` reads the inbound 5A trace (flash 2g or later): every checksum-valid
`5A` frame the module sent, i.e. what reached the board of the host's LED
report, as frame number, byte and ms since the previous frame (the ring keeps
the last 52). `leds reset` clears it; `leds json` prints the same as one JSON
object, for scripts/ledchannel_test.py.
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
USAGE = "usage: devhook.py press | reset | read | lcd [LEVEL | on | off] | leds [reset | json]"
def xfer(h, op, arg=0):
    h.write(bytes([0, SET_VALUE, CH, HC, op, arg]) + bytes(27))   # report id + 32 bytes
    for _ in range(5):
        r = h.read(32, 1000)
        # A whole report answering this op: channel, command and the echoed op
        # (codex, flash 2f: an op-2 reply was otherwise taken for LCD state).
        if r and len(r) == 32 and r[1] == CH and r[2] == HC and r[3] == op:
            if r[0] != SET_VALUE: sys.exit(f"unhandled: {bytes(r[:8]).hex()} (flash 2f or later for lcd, 2g for leds)")
            return bytes(r)
    sys.exit("no reply")
args = sys.argv[1:]
ok = (len(args) == 1 and args[0] in ("press", "reset", "read", "lcd", "leds")) or (
    len(args) == 2 and args[0] == "lcd" and (args[1] in ("on", "off") or
                                           (args[1].isdigit() and int(args[1]) <= 255))) or (
    len(args) == 2 and args[0] == "leds" and args[1] in ("reset", "json"))
if not ok:
    sys.exit(USAGE)
h = open_device()
cmd = args[0]
if cmd == "press": xfer(h, 1)
elif cmd == "reset": xfer(h, 3)
elif cmd == "lcd":
    arg = args[1] if len(args) > 1 else None
    if arg is None: r = xfer(h, 6)
    elif arg in ("on", "off"): r = xfer(h, 5, 1 if arg == "on" else 0)
    else: r = xfer(h, 4, int(arg))
    level, top, on, eff, saved = r[4:9]
    print(f"level {level}/{top} {'on' if on else 'off'}, effective {eff}, "
          f"saved {saved - 1 if saved else 'none'}")
elif cmd == "leds" and args[1:] == ["reset"]: xfer(h, 9)
elif cmd == "leds":
    u32 = lambda b: int.from_bytes(b, "little")
    # Header, pages, header again: consistent only if the count held still
    # between them (codex, flash 2g). Each page echoes its number.
    for _ in range(5):
        r = xfer(h, 7)
        frames, accepted, leds, last, age, size = (u32(r[4:8]), u32(r[8:12]), r[12], r[13],
                                                   u32(r[14:18]), r[18])
        if size != 52: sys.exit(f"unexpected ring size {size} (flash 2g)")
        pages = [xfer(h, 8, pg) for pg in range(4)]
        if any(p[4] != pg or p[5] != 0 for pg, p in enumerate(pages)):
            sys.exit("a ring page did not echo its number")
        if u32(xfer(h, 7)[4:8]) == frames: break
    else:
        sys.exit("the trace kept moving while it was read")
    slots = b"".join(p[6:32] for p in pages)
    seq = [(n, slots[2 * (n % size)], slots[2 * (n % size) + 1])
           for n in range(max(0, frames - size), frames)]
    if args[1:] == ["json"]:
        import json
        print(json.dumps({"frames": frames, "accepted": accepted, "host_leds": leds, "last": last,
                          "age_ms": None if age == 0xFFFFFFFF else age,
                          "seq": [{"n": n, "byte": b, "gap_ms": None if g == 255 else g}
                                  for n, b, g in seq]}))
    else:
        print(f"{frames} frames, {accepted} accepted; host_leds 0x{leds:02x}, last 0x{last:02x}, "
              f"{'none yet' if age == 0xFFFFFFFF else f'{age} ms ago'}")
        for n, b, g in seq:
            print(f"  #{n:<5} 0x{b:02x}  {'>254' if g == 255 else g:>4} ms")
elif cmd == "read":
    vals = []
    for pg in (0, 1):
        r = xfer(h, 2, pg)
        vals += [r[4 + 2 * k] | (r[5 + 2 * k] << 8) for k in range(14)]
    for n, v in zip(NAMES, vals):
        if v: print(f"{v / 1000:7.2f} ms  {n}")
h.close()
