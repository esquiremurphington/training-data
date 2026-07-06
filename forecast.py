#!/usr/bin/env python3
"""forecast.py — regenerates the FORECASTING.md §3 snapshot from the repo CSVs.

Method (per FORECASTING §2, corrected 6 Jul 2026):
  - PMC from np_calculated at ONE uniform FTP constant (re-baseline after CP test).
  - Weight from the 7-day rolling mean trend.
  - Recovery: 7-day means + week-over-week slope for RHR and nightly HRV.
Run from the repo root:  python3 forecast.py [--ftp 315] [--load 95] [--taper-start 2026-08-20]
"""
import csv, math, argparse, statistics
from datetime import date, timedelta

NISEKO = date(2026, 8, 30)
MILESTONES = [
    (date(2026, 7, 8),  "CP test Session 1"),
    (date(2026, 7, 11), "CP test Session 2"),
    (date(2026, 7, 12), "CP test DROP-DEAD"),
    (date(2026, 7, 15), "Heat-acclimation go/no-go"),
    (date(2026, 7, 26), "Genting 1"),
    (date(2026, 8, 16), "Genting 2 (Block 2 peak)"),
    (date(2026, 8, 20), "Niseko taper start"),
    (NISEKO,            "NISEKO"),
    (date(2026, 10, 16), "Chiang Mai"),
]
KC, KA = 1 - math.exp(-1 / 42), 1 - math.exp(-1 / 7)


def fnum(r, k):
    try:
        v = float(r.get(k, ""))
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def pmc(ftp, taper_start, load, taper_load):
    rows = list(csv.DictReader(open("cycling_deep.csv")))
    daily = {}
    for r in rows:
        np_, dur = fnum(r, "np_calculated"), fnum(r, "duration_min")
        if not np_ or not dur:
            continue
        daily[r["date"][:10]] = daily.get(r["date"][:10], 0) + (dur / 60) * (np_ / ftp) ** 2 * 100
    d = min(date.fromisoformat(k) for k in daily)
    end = max(date.fromisoformat(k) for k in daily)
    ctl = atl = 0.0
    while d <= end:
        t = daily.get(d.isoformat(), 0.0)
        ctl += (t - ctl) * KC
        atl += (t - atl) * KA
        d += timedelta(days=1)
    print(f"PMC as of {end}:  CTL {ctl:.1f} | ATL {atl:.1f} | TSB {ctl-atl:+.1f}  (np_calculated, FTP {ftp})")
    # 4-week weekly TSS
    wk = {}
    for k, v in daily.items():
        dd = date.fromisoformat(k)
        wk.setdefault((dd - timedelta(days=dd.weekday())).isoformat(), 0)
        wk[(dd - timedelta(days=dd.weekday())).isoformat()] += v
    for k in sorted(wk)[-4:]:
        print(f"  week {k}: {wk[k]:.0f} TSS")
    # projection
    print(f"Projection (assumption: {load} TSS/day, taper from {taper_start} at {taper_load}/day):")
    c, a, d = ctl, atl, end
    marks = {m[0]: m[1] for m in MILESTONES if end < m[0] <= NISEKO}
    while d < NISEKO:
        d += timedelta(days=1)
        t = load if d < taper_start else taper_load
        c += (t - c) * KC
        a += (t - a) * KA
        if d in marks:
            print(f"  {d} {marks[d]:<28} CTL {c:5.1f}  TSB {c-a:+5.1f}")
    return end


def weight():
    h = list(csv.DictReader(open("daily_health.csv")))
    ws = [(r["date"], fnum(r, "weight_kg")) for r in h if fnum(r, "weight_kg")]
    if len(ws) < 7:
        print("Weight: insufficient data")
        return
    m7 = statistics.mean(w for _, w in ws[-7:])
    prev = [w for d, w in ws[-14:-7]]
    trend = m7 - statistics.mean(prev) if prev else float("nan")
    wks = (NISEKO - date.today()).days / 7
    print(f"Weight 7-entry mean: {m7:.1f} kg  (vs prior 7: {trend:+.2f})  | weeks to Niseko: {wks:.1f}")
    for tgt in (75.0, 74.0, 73.0, 72.0, 70.0):
        rate = (m7 - tgt) / wks
        tag = "SAFE" if rate <= 0.6 else ("governor-watched" if rate <= 0.75 else "REJECT")
        print(f"  to {tgt:.0f} kg: {rate:.2f} kg/wk  [{tag}]")


def recovery():
    h = list(csv.DictReader(open("daily_health.csv")))
    def m(key, rows):
        v = [fnum(r, key) for r in rows if fnum(r, key)]
        return statistics.mean(v) if v else None
    for key, label, note in (("resting_hr_bpm", "RHR", "target ≤50, red ≥54"),
                             ("hrv_last_night", "HRV nightly", "gate ≥30, green ≥32 wkly")):
        cur, prev = m(key, h[-7:]), m(key, h[-14:-7])
        print(f"{label}: 7d {cur:.1f} (prev {prev:.1f}, slope {cur-prev:+.1f}/wk)  [{note}]")
    last = h[-1]
    print(f"Today's gates: HRV {last.get('hrv_last_night')} | RHR {last.get('resting_hr_bpm')} | weekly {last.get('hrv_weekly_avg')} {last.get('hrv_status')}")


def queue():
    today = date.today()
    print("Decision queue:")
    for d, name in MILESTONES:
        if d >= today:
            print(f"  {d}  {name:<28} T-{(d - today).days}d")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--ftp", type=int, default=315)
    p.add_argument("--load", type=float, default=95, help="assumed build TSS/day")
    p.add_argument("--taper-load", type=float, default=62)
    p.add_argument("--taper-start", type=lambda s: date.fromisoformat(s), default=date(2026, 8, 20))
    a = p.parse_args()
    print("=" * 72)
    pmc(a.ftp, a.taper_start, a.load, a.taper_load)
    print("-" * 72)
    weight()
    print("-" * 72)
    recovery()
    print("-" * 72)
    queue()
    print("=" * 72)
