#!/usr/bin/env python3
"""Plant one bug at a time in a scratch copy of raw81.c and check that
scripts/raw81_test.py fails each (a suite that passes a planted bug does not
test that rule). Prints one line per mutant; exits 1 if any survives.

    python scripts/raw81_mutants.py [--cc gcc]
"""
import os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "qmk_firmware-ak820pro", "keyboards", "a_jazz", "ak820pro",
                   "bluetooth", "raw81.c")

MUTANTS = [
    ("start on any 0x81", "if (rx->boundary && c == 0x81)", "if (c == 0x81)"),
    ("no CRC check", "p[0] != RAW81_MAGIC || p[1] == 0 || crc != (uint16_t)((p[4] << 8) | p[5])",
     "p[0] != RAW81_MAGIC || p[1] == 0"),
    ("no magic check", "p[0] != RAW81_MAGIC || p[1] == 0 ||", "p[1] == 0 ||"),
    ("session 0 allowed", "p[0] != RAW81_MAGIC || p[1] == 0 ||", "p[0] != RAW81_MAGIC ||"),
    ("CRC skips the seq", "uint16_t crc     = raw81_crc16(p, 4, 0xFFFFu);", "uint16_t crc     = raw81_crc16(p, 2, 0xFFFFu);"),
    ("duplicate seq fresh", "d == 0 || d > 32767u", "d > 32767u"),
    ("freshness window 8-bit", "d == 0 || d > 32767u", "d == 0 || d > 127u"),
    ("freshness window 65535", "d == 0 || d > 32767u", "d == 0"),
    ("other session never busy", "} else if (rx->have_session && now_ms - rx->admitted_ms < RAW81_SESSION_IDLE_MS) {",
     "} else if (0 && now_ms - rx->admitted_ms < RAW81_SESSION_IDLE_MS) {"),
    ("session idle off by one", "now_ms - rx->admitted_ms < RAW81_SESSION_IDLE_MS", "now_ms - rx->admitted_ms <= RAW81_SESSION_IDLE_MS"),
    ("admit forgets the session", "    rx->session      = session;\n", "    (void)session;\n"),
    ("commit advances freshness", "    memcpy(rx->inner, &p[RAW81_HEAD], RAW81_INNER);",
     "    rx->have_session = true; rx->session = rx->c_session; rx->last_seq = rx->c_seq;\n    memcpy(rx->inner, &p[RAW81_HEAD], RAW81_INNER);"),
    ("reset keeps the session", "    rx->tainted      = false;\n    rx->have_session = false;", "    rx->tainted      = false;"),
    ("receipt own CRC short", "uint16_t c = raw81_crc16(out, 9, 0xFFFFu);", "uint16_t c = raw81_crc16(out, 8, 0xFFFFu);"),
    ("no quarantine on badenv", "abort_to(rx, &rx->badenv);", "rx->badenv++; rx->state = RAW81_IDLE;"),
    ("boundary sticks", "rx->boundary = matched;", "rx->boundary = rx->boundary || matched;"),
    ("taint ignored at commit", "    if (rx->tainted) {\n        abort_to(rx, &rx->taint);\n        return RAW81_V_TAINT;",
     "    if (0) {\n        abort_to(rx, &rx->taint);\n        return RAW81_V_TAINT;"),
    ("taint not latched while idle", "if (uart_err) rx->tainted = true;   /* remembered", "if (0) rx->tainted = true;   /* remembered"),
    ("allow HC_RESET", "case 0x07: /* HC_GET4 */", "case 0x07: /* HC_GET4 */\n            case 0x05:"),
    ("allow any HC_LINK page", "return in[3] == 0x00 || (in[3] >= 0x21 && in[3] <= 0x2A);", "return true;"),
    ("quarantine never ends", "bool ended    = rx->state == RAW81_QUARANTINE;",
     "if (rx->state == RAW81_QUARANTINE) return false;\n    bool ended = false;"),
    ("start not reported (window kept)", "        return RAW81_STARTED;", "        return RAW81_TAKEN;"),
    ("no lifetime at commit", "    if (now_ms - rx->start_ms >= lifetime_ms) {\n        abort_to(rx, &rx->lifetime);\n        return RAW81_V_LIFETIME;",
     "    if (0 && now_ms - rx->start_ms >= lifetime_ms) {\n        abort_to(rx, &rx->lifetime);\n        return RAW81_V_LIFETIME;"),
    ("receipt reply crc off", "out[8]     = (uint8_t)c;", "out[8]     = (uint8_t)(c + 1);"),
    ("link loss keeps the session", "void raw81_link_lost(raw81_rx_t *rx) {\n    rx->have_session = false;",
     "void raw81_link_lost(raw81_rx_t *rx) {"),
]


def main():
    cc = sys.argv[sys.argv.index("--cc") + 1] if "--cc" in sys.argv else ("gcc" if os.name == "nt" else "cc")
    orig = open(SRC, encoding="utf-8").read()
    d = tempfile.mkdtemp(prefix="raw81_mut_")
    shutil.copy(os.path.join(os.path.dirname(SRC), "raw81.h"), d)
    survived = 0
    for name, a, b in MUTANTS:
        if a not in orig:
            print(f"?? {name}: pattern not found")
            survived += 1
            continue
        path = os.path.join(d, "raw81.c")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(orig.replace(a, b, 1))
        r = subprocess.run([sys.executable, os.path.join(HERE, "raw81_test.py"), "--cc", cc],
                           env=dict(os.environ, RAW81_SRC=path), capture_output=True, text=True)
        last = (r.stdout.strip().splitlines() or [r.stderr.strip()[-120:]])[-1]
        caught = r.returncode != 0 and "FAILED" in last
        survived += not caught
        print(f"{'caught  ' if caught else 'SURVIVED'} {name}: {last}")
    print(f"{len(MUTANTS) - survived}/{len(MUTANTS)} caught")
    sys.exit(1 if survived else 0)


if __name__ == "__main__":
    main()
