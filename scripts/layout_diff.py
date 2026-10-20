#!/usr/bin/env python3
"""Do two firmware images differ only in where their code sits?

    venv/bin/python scripts/layout_diff.py A.elf B.elf [--fn deep_sleep ...]

For the deep-sleep regression (plans/DEEP-SLEEP-REGRESSION-2026-10-20.md):
a padded build must hold b2a07e0d37's instructions unchanged, only moved.
Each function's disassembly is compared with its addresses written as
symbol + offset: the leading address dropped, branch targets kept as
<sym+off>, and a literal word that points into the flash (a function's or a
constant's address) named the same way; a word matches if its raw value or
its name does (a plain number can look like a code address), or if it
moved by exactly its region's shift (an unnamed constant, a string, after
the code). health_fill6 always differs: the build token's bytes are
immediates there, and it is exempt whatever changes in it. RAM symbols keep
their address and size, compared by name (a repeated local name, such as
`ring`, keeps only its last instance; unsized symbols are skipped).
Prints each function that differs, the code shift per function, and exits
1 on any difference.

A SCREEN, not a proof (codex, pad review, P2, and its verify, P3): it reads
disassembly, so .rodata, the vector table, the gaps between functions and
.data's initial image (beyond its FUNC bodies) go unchecked; a number that
happens to move by its region's shift passes as a pointer, and a pointer
left stale passes by raw equality. For the 10-20 pad builds, codex's byte-level
check (every BIN byte rebuilt from the ELF; every difference a relocated
BL, literal, vector, .rodata or .data word, or the token) is the
verification of record: plans/review-codex-sleep-pad-2026-10-20.md.
"""
import argparse, bisect, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(HERE, "..", "xpack-arm-none-eabi-gcc-13.3.1-1.1", "bin", "arm-none-eabi-")


def syms(elf):
    out = subprocess.run([BIN + "nm", "-n", "-S", elf], capture_output=True, text=True, check=True).stdout
    code, ram, flash = [], {}, []
    for ln in out.splitlines():
        p = ln.split()
        if len(p) == 4:
            addr, size, kind, name = int(p[0], 16), int(p[1], 16), p[2], p[3]
            if addr < 0x10000000:
                flash.append((addr, size, name))
                if kind in "tTwW":
                    code.append((addr, size, name))
            elif addr >= 0x20000000:
                ram[name] = (addr, size)
    return code, ram, flash


def funcs(elf):
    """The FUNC symbols' names (readelf): nm calls .data's objects 't' too, .data being
    executable here for .ramtext."""
    out = subprocess.run([BIN + "readelf", "-sW", elf], capture_output=True, text=True, check=True).stdout
    return {p[7] for p in (ln.split() for ln in out.splitlines()) if len(p) >= 8 and p[3] == "FUNC"}


def namer(code):
    starts = [c[0] for c in code]

    def name(v):   # the Thumb bit kept: a pointer that loses it names another offset
        i = bisect.bisect_right(starts, v) - 1
        if i >= 0 and v < code[i][0] + max(code[i][1], 1):
            return f"<{code[i][2]}+{v - code[i][0]:#x}>"
        return None
    return name


def functions(elf, flash):
    name = namer(flash)
    out = subprocess.run([BIN + "objdump", "-d", "--no-show-raw-insn", elf],
                         capture_output=True, text=True, check=True).stdout
    fns, cur = {}, None
    for ln in out.splitlines():
        m = re.match(r"^[0-9a-f]+ <([^>]+)>:$", ln)
        if m:
            cur = fns.setdefault(m.group(1), [])
            continue
        m = re.match(r"^\s*[0-9a-f]+:\s+(.*)$", ln)   # SRAM code prints with no indent
        if not m or cur is None:
            continue
        ins = m.group(1).split("@")[0].strip()
        w = re.match(r"^\.word\s+0x([0-9a-f]+)$", ins)
        if w:
            v = int(w.group(1), 16)
            cur.append((ins, name(v), v))
        else:
            ins = re.sub(r"\b[0-9a-f]+ (<[^>]+>)", r"\1", ins)   # "bl 11a1c <f>" -> "bl <f>"
            ins = re.sub(r"\s+", " ", ins)
            cur.append((ins, ins, None))
    return fns

# Always differs: the build token's bytes are its immediates.
EXPECTED = {"health_fill6": "the build token"}


def shift_of(fla, flb):
    """addr in A -> how far that region moved in B, from the symbols in both."""
    from collections import Counter
    na, nb = Counter(n for _, _, n in fla), Counter(n for _, _, n in flb)
    b = {n: s for s, _, n in flb if nb[n] == 1}   # local names (CSWTCH.5) repeat across files
    pts = sorted((s, b[n] - s) for s, _, n in fla if na[n] == 1 and n in b)
    starts = [p[0] for p in pts]

    def at(v):
        i = bisect.bisect_right(starts, v) - 1
        return pts[i][1] if i >= 0 else 0
    return at


def same(x, y, shift):
    """Two disassemblies match line by line: raw text, name, or a word moved by its region's shift."""
    def one(a, b):
        if a[0] == b[0] or (a[1] is not None and a[1] == b[1]):
            return True
        return a[2] is not None and b[2] is not None and a[2] < 0x10000000 and b[2] == a[2] + shift(a[2])
    return len(x) == len(y) and all(one(a, b) for a, b in zip(x, y))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--fn", nargs="*", default=["deep_sleep"], help="functions to show the shift of")
    a = ap.parse_args()
    ca, ra, fla = syms(a.a)
    cb, rb, flb = syms(a.b)
    bad = 0
    if ra != rb:
        diff = sorted(k for k in set(ra) | set(rb) if ra.get(k) != rb.get(k))
        print(f"RAM differs: {len(diff)} symbols, e.g. {diff[:8]}")
        bad += 1
    else:
        print(f"RAM: all {len(ra)} symbols at the same address and size")
    fa, fb = functions(a.a, fla), functions(a.b, flb)
    fn = funcs(a.a) | funcs(a.b)   # .data's objects disassemble too: skip them
    fa = {k: v for k, v in fa.items() if k in fn}
    fb = {k: v for k, v in fb.items() if k in fn}
    only = sorted(set(fa) ^ set(fb))
    if only:
        print("functions in one image only:", only[:20])
        bad += 1
    shift = shift_of(fla, flb)
    changed = [f for f in sorted(set(fa) & set(fb)) if not same(fa[f], fb[f], shift)]
    for f in changed:
        print(f"differs: {f}" + (f" (expected: {EXPECTED[f]})" if f in EXPECTED else ""))
        for x, y in zip(fa[f], fb[f]):
            if not same([x], [y], shift):
                print(f"    {x[0]}  {x[1] or ''}\n    {y[0]}  {y[1] or ''}")
                break
        bad += f not in EXPECTED
    if not [f for f in changed if f not in EXPECTED]:
        print(f"code: all {len(set(fa) & set(fb))} common functions identical as symbol + offset"
              + (f", but {', '.join(changed)} as expected" if changed else ""))
    aa = {n: s for s, _, n in ca}
    ab = {n: s for s, _, n in cb}
    for f in a.fn:
        if f in aa and f in ab:
            print(f"{f}: {aa[f]:#x} -> {ab[f]:#x} ({ab[f] - aa[f]:+#x})")
    shifts = {}
    for n in set(aa) & set(ab):
        shifts.setdefault(ab[n] - aa[n], []).append(aa[n])
    for d, at in sorted(shifts.items(), key=lambda kv: min(kv[1])):
        print(f"shift {d:+#x}: {len(at)} functions, from {min(at):#x} to {max(at):#x}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
