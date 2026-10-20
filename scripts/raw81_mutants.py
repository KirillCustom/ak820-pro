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
    ("no CRC check", "p[0] != RAW81_MAGIC || crc != (uint16_t)((p[2] << 8) | p[3])", "p[0] != RAW81_MAGIC"),
    ("no magic check", "p[0] != RAW81_MAGIC || crc", "crc"),
    ("duplicate seq fresh", "d == 0 || d > 127u", "d > 127u"),
    ("freshness window 255", "d == 0 || d > 127u", "d == 0"),
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
    ("receipt crc off", "out[5]     = (uint8_t)c;", "out[5]     = (uint8_t)(c + 1);"),
    ("link loss keeps freshness", "void raw81_link_lost(raw81_rx_t *rx) {\n    rx->have_last = false;",
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
