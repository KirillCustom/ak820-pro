#!/usr/bin/env python3
"""Raw HID over the 2.4G dongle, stage 2: the Windows acceptance run
(plans/RAWHID-24G-PLAN.md, revisions 2-5; the host protocol is
scripts/raw81_proto.py). Needs the gremlin/raw81 stage-2 dev build, the slider
on 2.4G with the link up, and the USB cable (the board's own counters and its
stored text are read over it).

    win_raw81_s2.py [all | text | health | nak | stream N | load SECONDS] [--json OUT]

  text    each text command over the radio, the band's stored state read back
          over USB (op 18) and compared
  health  each allow-listed health read over the radio, compared with the
          same read over USB (counters move, so shape and the stable fields)
  nak     a forbidden command (2), a stale seq (3), a corrupted envelope (1),
          and a second session inside the idle time (4)
  stream  N health reads back to back: loss, latency to the receipt, and the
          board's counters (drops, deferral overruns, short writes must be 0)
  load    health reads at full rate for SECONDS, logging each loss with its
          time, for the overrun A/B (the Mac session's request)

The owner lock and the session: one owner per dongle on this machine (a lock
file held for the run); the session id is persisted beside it, incremented on
every open (skipping 0), and a new open waits until 1 s after the previous
one (R5-2). Never hid_enumerate: the dongle and the board are found in the
Configuration Manager's list (CLAUDE.md).
"""
import ctypes, json, msvcrt, os, struct, sys, time
from ctypes import wintypes
import hid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "hostagent"))
import raw81_proto as P   # noqa: E402

STATE = os.path.join(os.environ.get("LOCALAPPDATA", HERE), "ak820pro", "raw81-session.json")
LOCK = STATE + ".lock"
DEADLINE_S = 0.100
RTT_TRIPWIRE_MS = 250.0
ROTATE_SPAN = 16384


def cm_paths(pred):
    class GUID(ctypes.Structure):
        _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]
    g = GUID(0x4D1E55B2, 0xF16F, 0x11CF, (ctypes.c_ubyte * 8)(0x88, 0xCB, 0, 0x11, 0x11, 0, 0, 0x30))
    cm, n = ctypes.windll.cfgmgr32, wintypes.ULONG()
    cm.CM_Get_Device_Interface_List_SizeW(ctypes.byref(n), ctypes.byref(g), None, 0)
    buf = ctypes.create_unicode_buffer(n.value)
    cm.CM_Get_Device_Interface_ListW(ctypes.byref(g), None, buf, n, 0)
    return [p for p in buf[:n.value].split("\0") if p and len(p.split("#")) == 4 and pred(p.lower().split("#")[1])]


class Owner:
    """The lock and the persisted session (R5-2)."""

    def __init__(self):
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        self.lf = open(LOCK, "a+")
        try:
            msvcrt.locking(self.lf.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            sys.exit("another raw81 owner holds the dongle on this machine")
        st = {"session": 0, "opened": 0.0}
        try:
            st = json.load(open(STATE))
        except Exception:
            pass
        wait = st.get("opened", 0.0) + 1.0 - time.time()
        if wait > 0:
            time.sleep(wait)               # no reuse inside the 1 s lifetime
        self.session = (st.get("session", 0) % 255) + 1
        self.opened = time.time()
        json.dump({"session": self.session, "opened": self.opened}, open(STATE, "w"))   # before any send
        self.seq = 0
        self.issued = 0

    def next(self):
        if self.issued >= ROTATE_SPAN:
            raise RuntimeError("session span used up: reopen (rotation)")
        self.seq = (self.seq + 1) & 0xFFFF
        self.issued += 1
        return self.seq


class Radio:
    def __init__(self):
        ps = cm_paths(lambda h: h == "vid_0c45&pid_fdfd&mi_03")
        if len(ps) != 1:
            sys.exit(f"dongle FF60: {ps}")
        self.d = hid.Device(path=ps[0].encode())
        self.owner = Owner()
        self.max_rtt = 0.0

    def drain(self):
        while self.d.read(33, 0):
            pass

    def xact(self, inner, raw=None, session=None, seq=None):
        """One transaction; returns (Transaction, rtt_ms). raw overrides the
        32-byte request (for corrupted-envelope tests)."""
        session = self.owner.session if session is None else session
        seq = self.owner.next() if seq is None else seq
        req = raw if raw is not None else P.envelope(session, seq, inner)
        t = P.Transaction(session, seq, inner)
        self.drain()
        t0 = time.perf_counter()
        self.d.write(bytes([0]) + req)
        while not t.feed(self.d.read(33, max(1, int((DEADLINE_S - (time.perf_counter() - t0)) * 1000))) or b""):
            if time.perf_counter() - t0 >= DEADLINE_S:
                t.timeout()
                break
        rtt = (time.perf_counter() - t0) * 1000
        if t.result in ("ok", "nak", "incomplete"):
            self.max_rtt = max(self.max_rtt, rtt)
        return t, rtt


def usb_open():
    import ak820text
    return ak820text.open_device()


def usb_xfer(h, payload, match=3):
    h.write(bytes([0]) + bytes(payload) + bytes(32 - len(payload)))
    for _ in range(10):
        r = h.read(32, 500)
        if r and bytes(r[1:match]) == bytes(payload[1:match]):
            return bytes(r)
    raise RuntimeError(f"no USB reply to {bytes(payload).hex()}")


def devhook(h, op, arg):
    r = usb_xfer(h, [0x07, 0x13, 0x76, op, arg], match=4)
    if r[0] != 0x07:
        sys.exit(f"op {op} unhandled: not the stage-2 build")
    return r


def counters(h):
    p0, p1, p2 = (devhook(h, 17, a) for a in (0, 1, 2))
    u = lambda b, i: struct.unpack_from("<I", b, 4 + 4 * i)[0]
    c = dict(zip(["ok", "stale", "forbidden", "badenv", "badsum", "taint", "lifetime"], [u(p0, i) for i in range(7)]))
    c.update(zip(["busy", "qbytes", "drops", "dispatched", "sent91", "acks_in_window", "defer_overrun"],
                 [u(p1, i) for i in range(7)]))
    c.update(state=p2[4], session_live=p2[10], session=p2[11], last_seq=struct.unpack_from("<H", p2, 12)[0],
             short_writes=struct.unpack_from("<I", p2, 14)[0])
    return c


def stalls(h):
    import ak820health
    s = ak820health.read_stalls(h)
    link = ak820health.read_link(h) if hasattr(ak820health, "read_link") else {}
    return {"ge25": s["count_ge_25ms"], "ge10": s["count_ge_10ms"],
            **{k: link.get(k) for k in ("giveups", "replaced", "queue_full", "uart_overrun") if k in link}}


FAILS = []


def check(name, ok, detail=""):
    print(f"{'ok  ' if ok else 'FAIL'} {name}{'' if ok else '  ' + str(detail)}")
    if not ok:
        FAILS.append(name)


def test_text(r, h):
    cases = [
        ("set line 0", b"\x07\x12\x03\x00\x01Radio title", (0, b"Radio title", 1)),
        ("set line 1", b"\x07\x12\x03\x01\x00By the dongle", (1, b"By the dongle", None)),
        ("set (legacy)", b"\x07\x12\x01\x02Paused", (0, b"Paused", 2)),
        ("clear", b"\x07\x12\x02", (0, b"", 0)),
    ]
    for name, inner, (line, want, icon) in cases:
        t, rtt = r.xact(inner)
        st = devhook(h, 18, line)
        got = st[7:7 + st[4]]
        check(f"text {name}: {t.result} in {rtt:.1f} ms", t.result == "ok" and got == want and
              (icon is None or st[5] == icon), (t.result, got, st[5]))
    t, _ = r.xact(b"\x07\x12\x04\x01\x00\x10\x00\xf0")
    pb = devhook(h, 18, 2)
    check("text playback", t.result == "ok" and pb[4] == 1 and struct.unpack_from("<HH", pb, 6) == (0x10, 0xF0),
          (t.result, pb[4:10].hex()))
    r.xact(b"\x07\x12\x04\x00")
    r.xact(b"\x07\x12\x02")


def test_health(r, h):
    for inner in (b"\x07\x13\x01", b"\x07\x13\x02", b"\x07\x13\x04", b"\x07\x13\x06", b"\x07\x13\x07",
                  b"\x07\x13\x0e\x00", b"\x07\x13\x0e\x21", b"\x07\x13\x0e\x2a"):
        t, rtt = r.xact(inner)
        u = usb_xfer(h, list(inner), match=len(inner))
        same_shape = t.reply is not None and t.reply[:4] == u[:4]
        check(f"health {inner.hex()}: {t.result} in {rtt:.1f} ms", t.result == "ok" and same_shape,
              (t.result, t.reply[:6].hex() if t.reply else None, u[:6].hex()))


def test_nak(r, h):
    t, _ = r.xact(b"\x07\x13\x05")                         # HC_RESET: not allowed
    check("forbidden -> NAK 2", t.result == "nak" and t.status == 2, (t.result, t.status))
    t, _ = r.xact(b"\x07\x13\x79BOOT")                     # the bootloader jump
    check("bootloader jump -> NAK 2", t.result == "nak" and t.status == 2, (t.result, t.status))
    s = r.owner.seq
    t, _ = r.xact(b"\x07\x13\x01", seq=s)                  # the last admitted seq again
    check("repeated seq -> NAK 3", t.result == "nak" and t.status == 3, (t.result, t.status))
    bad = bytearray(P.envelope(r.owner.session, r.owner.next(), b"\x07\x13\x01")); bad[10] ^= 0x55
    t, _ = r.xact(b"\x07\x13\x01", raw=bytes(bad), seq=(bad[2] << 8) | bad[3])
    check("corrupted envelope -> NAK 1", t.result == "nak" and t.status == 1, (t.result, t.status))
    time.sleep(0.05)                                      # the quarantine ends on silence
    other = (r.owner.session % 255) + 1
    t, _ = r.xact(b"\x07\x13\x01", session=other, seq=1)
    check("another session inside 1 s -> NAK 4", t.result == "nak" and t.status == 4, (t.result, t.status))
    t, _ = r.xact(b"\x07\x13\x01")
    check("own session still served", t.result == "ok", t.result)


def test_stream(r, h, n, log=None):
    c0, s0 = counters(h), stalls(h)
    res, rtts, losses = {}, [], []
    t_start = time.time()
    for i in range(n):
        t, rtt = r.xact(b"\x07\x13\x01")
        res[t.result] = res.get(t.result, 0) + 1
        if t.result == "ok":
            rtts.append(rtt)
        else:
            losses.append({"i": i, "at": round(time.time() - t_start, 3), "result": t.result, "status": t.status})
    c1, s1 = counters(h), stalls(h)
    rtts.sort()
    q = lambda f: round(rtts[min(len(rtts) - 1, int(f * len(rtts)))], 2) if rtts else None
    d = {k: c1[k] - c0[k] for k in c0 if isinstance(c0[k], int) and k not in ("state", "session_live", "session", "last_seq")}
    ds = {k: (s1[k] - s0[k]) if s0.get(k) is not None else None for k in s1}
    out = {"n": n, "results": res, "rtt_ms": {"min": q(0), "median": q(0.5), "p95": q(0.95), "max": q(1.0)},
           "board_delta": d, "stall_link_delta": ds, "losses": losses, "max_rtt_ms": round(r.max_rtt, 2)}
    print(f"stream {n}: {res}; rtt median {q(0.5)} p95 {q(0.95)} max {q(1.0)} ms; board "
          + ", ".join(f"{k} +{v}" for k, v in d.items() if v) + "; " + ", ".join(f"{k} +{v}" for k, v in ds.items() if v))
    check("stream: every transaction ok", res.get("ok", 0) == n, res)
    check("stream: p95 under 30 ms", rtts and q(0.95) < 30, q(0.95))
    for k in ("drops", "defer_overrun", "short_writes", "badsum", "taint", "lifetime", "badenv"):
        check(f"stream: board {k} +0", d.get(k, 0) == 0, d.get(k))
    check("stream: no pass >= 25 ms", ds.get("ge25") in (0, None), ds.get("ge25"))
    for k in ("giveups", "replaced", "queue_full"):
        if ds.get(k) is not None:
            check(f"stream: link {k} +0", ds[k] == 0, ds[k])
    return out


def main():
    args = sys.argv[1:]
    out = None
    if "--json" in args:
        out = args[args.index("--json") + 1]
        args = args[:args.index("--json")]
    what = args[0] if args else "all"
    r, h = Radio(), usb_open()
    report = {"session": r.owner.session, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    print(f"session {r.owner.session}; board counters before: {counters(h)}")
    if what in ("all", "text"):
        test_text(r, h)
    if what in ("all", "health"):
        test_health(r, h)
    if what in ("all", "nak"):
        test_nak(r, h)
    if what == "all":
        report["stream"] = test_stream(r, h, 1000)
    if what == "stream":
        report["stream"] = test_stream(r, h, int(args[1]))
    if what == "load":
        end = time.time() + float(args[1])
        n = 0
        while time.time() < end:
            report.setdefault("load", []).append(test_stream(r, h, 200))
            n += 200
    check(f"max RTT under the {RTT_TRIPWIRE_MS:.0f} ms tripwire", r.max_rtt < RTT_TRIPWIRE_MS, r.max_rtt)
    report["max_rtt_ms"] = round(r.max_rtt, 2)
    report["fails"] = FAILS
    print(f"board counters after: {counters(h)}")
    if out:
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        json.dump(report, open(out, "w"), indent=1)
    print(f"{len(FAILS)} FAILED" if FAILS else "all checks pass")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
