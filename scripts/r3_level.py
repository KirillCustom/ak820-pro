#!/usr/bin/env python3
"""A1/A2 (plans/POWER-CAMPAIGN-PLAN.md): the gauge's real accuracy on R3, the
logged drain of 10-15/18, and the pack's capacity.

R3 ran at JD's settings from FULL (relay 2 off 23:37:59.44 10-15) to the RGB
cut (02:01:22-23 10-18), with the INA228 + MAX17048 logger on the pack at
1 Hz. Three truths for "how much is left", each 100% at the start and 0% at
the cut:
  time    1 - t/T. The gauge's own definition: the share of runtime left at
          the run's (nearly constant) load.
  energy  mWh left over mWh to the cut (current x vcell). Load-independent:
          what a runtime estimate divides by a power.
  charge  mAh left over mAh to the cut, from the INA228's charge register.
They differ because the board's buck-boost draws more current as the pack
falls (73 mA at 4.17 V, 90 at 3.39). Against them: the board's displayed
level (the trial's 5-minute reads; the board's RAM log over the outage, when
the trial could not read) and the MAX17048's own SOC guess.

The logger's hole (15:42:34-16:57:12 10-17, the dock's outage; the board ran
on through it) is bridged at the current either side: BRIDGE_MA, an
estimate (history/battery-2026-10-15-drain-2f/README.md).

    python3 scripts/r3_level.py [--write]

--write saves the per-point table and the summary into the run's folder.
"""
import csv, os, sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN = os.path.join(ROOT, "history", "battery-2026-10-15-drain-2f")
POWER = os.path.join(ROOT, "history", "power-bench-2026-10-15", "power.csv")

T0 = 1792129079.44                      # relay 2 off, 23:37:59.44 10-15
REG0 = -2.561231                        # the charge register then (mAh)
HOLE = ("2026-10-17 15:42:34", "2026-10-17 16:57:12")
BRIDGE_MA = 81.2                        # 81.9 (10 min before) and 80.5 (after): their mean
CUT_LAST_ON = "2026-10-18 02:01:22"     # the last second with the RGB on


def epoch(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").timestamp()


def load_power():
    """(epoch, current mA, charge register mAh, vcell mV, soc %), relay off to the cut."""
    rows, t_end = [], epoch(CUT_LAST_ON) + 0.999
    with open(POWER) as f:
        for x in csv.reader(f):
            if not x or x[0] == "time":
                continue
            try:
                t = float(x[1])
                r = (t, float(x[5]), float(x[6]), float(x[8]), float(x[9]))
            except (ValueError, IndexError):
                continue
            if T0 <= t <= t_end:
                rows.append(r)
    return rows


def integrate(rows):
    """Cumulative mAh and mWh used at each row. mAh from the register (it
    restarted at 0 at the hole's end); across the hole, BRIDGE_MA from the
    last row before it to the restart. mWh is the 1 Hz sum of I x V, and
    across the hole step that step's mAh at the mean vcell either side."""
    h0, h1 = epoch(HOLE[0]), epoch(HOLE[1])
    out, mwh, base, prev = [], 0.0, None, None
    for t, i, reg, v, soc in rows:
        if t <= h0 + 1:
            mah = reg - REG0
        else:
            if base is None:                     # the first row after the hole
                base = out[-1][1] + BRIDGE_MA * (h1 - out[-1][0]) / 3600
            mah = base + reg
        if prev is not None:
            dt = t - prev[0]
            if dt > 30:
                mwh += (mah - prev[1]) * (v + prev[3]) / 2 / 1000
            else:
                mwh += (i + prev[5]) / 2 * (v + prev[3]) / 2 / 1000 * dt / 3600
        prev = (t, mah, mwh, v, soc, i)
        out.append(prev)
    return out


def board_levels():
    """(epoch, displayed level %) from the trial's reads, plus the RAM log's
    600 s entries inside the trial's blind spell."""
    pts = []
    for name in ("drain.csv", "drain-resumed-1.csv"):
        with open(os.path.join(RUN, name)) as f:
            for r in csv.DictReader(f):
                if r["phase"] == "wait-cut" and r["level"] and r["supply"] == "battery":
                    pts.append((float(r["epoch"]), float(r["level"].rstrip(" %")), "trial"))
    have = sorted(p[0] for p in pts)
    with open(os.path.join(RUN, "log-20261018-0205-rgbcut.csv")) as f:
        for r in csv.DictReader(f):
            t = datetime.strptime(r["time"], "%Y-%m-%d %H:%M").timestamp()
            if r["level"] and r["on_batt"] == "1" and T0 < t and not any(abs(t - h) < 600 for h in have):
                pts.append((t, float(r["level"]), "ramlog"))
    return sorted(pts)


def at(series, t):
    """Linear interpolation of (t, ...) rows on t."""
    lo, hi = 0, len(series) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if series[mid][0] <= t: lo = mid
        else: hi = mid
    a, b = series[lo], series[hi]
    if b[0] == a[0]:
        return a
    w = (t - a[0]) / (b[0] - a[0])
    return tuple(x + (y - x) * w for x, y in zip(a, b))


def main():
    rows = integrate(load_power())
    t_cut = epoch(CUT_LAST_ON) + 0.5
    end = rows[-1]
    T, Q, E = t_cut - T0, end[1], end[2]
    lines = [f"R3: relay off to the RGB cut {T / 3600:.2f} h; {Q:.1f} mAh; {E:.0f} mWh "
             f"({E / Q * 1000:.0f} mV mean); mean draw {Q / (T / 3600):.1f} mA, "
             f"{E / (T / 3600):.0f} mW",
             f"the hole bridged at {BRIDGE_MA} mA: {BRIDGE_MA * (epoch(HOLE[1]) - epoch(HOLE[0])) / 3600:.1f} mAh (estimate)"]
    pts = []
    for t, lvl, src in board_levels():
        if t >= t_cut:
            continue
        _, mah, mwh, v, soc, _ = at(rows, t)
        tt = 100 * (1 - (t - T0) / T)
        te = 100 * (1 - mwh / E)
        tq = 100 * (1 - mah / Q)
        pts.append(dict(time=datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S"), src=src,
                        board=lvl, time_left=round(tt, 2), energy_left=round(te, 2),
                        charge_left=round(tq, 2), max17048=round(soc, 2), vcell=round(v, 1)))
    lines.append(f"{len(pts)} board readings ({sum(p['src'] == 'ramlog' for p in pts)} from the RAM log)")
    lines.append("")
    lines.append("error = board - truth, in points, by the truth's decile (energy left):")
    lines.append(f"{'band':>9} {'n':>4} {'vs time':>16} {'vs energy':>16} {'vs charge':>16} {'MAX17048 vs energy':>20}")
    for lo in range(90, -10, -10):
        sel = [p for p in pts if lo <= p["energy_left"] < lo + 10 or (lo == 90 and p["energy_left"] >= 100)]
        if not sel:
            continue
        def rng(k):
            e = [p["board"] - p[k] for p in sel]
            return f"{sum(e) / len(e):+5.1f} [{min(e):+5.1f},{max(e):+5.1f}]"
        m = [p["max17048"] - p["energy_left"] for p in sel]
        lines.append(f"{lo:>3}-{lo + 10:<3}%  {len(sel):>4} {rng('time_left'):>16} {rng('energy_left'):>16} "
                     f"{rng('charge_left'):>16} {sum(m) / len(m):+8.1f} [{min(m):+5.1f},{max(m):+5.1f}]")
    for k in ("time_left", "energy_left", "charge_left"):
        e = [p["board"] - p[k] for p in pts]
        lines.append(f"overall vs {k:12}: mean {sum(e) / len(e):+.2f}, worst {min(e):+.1f} / {max(e):+.1f}, "
                     f"within 3 points {100 * sum(abs(x) <= 3 for x in e) / len(e):.0f}%")
    e = [p["max17048"] - p["energy_left"] for p in pts]
    lines.append(f"MAX17048 vs energy_left  : mean {sum(e) / len(e):+.2f}, worst {min(e):+.1f} / {max(e):+.1f}")
    print("\n".join(lines))
    if "--write" in sys.argv:
        with open(os.path.join(RUN, "a1-points.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(pts[0].keys()))
            w.writeheader()
            w.writerows(pts)
        with open(os.path.join(RUN, "a1-summary.txt"), "w") as f:
            f.write("\n".join(lines) + "\n")
        print(f"wrote {RUN}/a1-points.csv and a1-summary.txt")


if __name__ == "__main__":
    main()
