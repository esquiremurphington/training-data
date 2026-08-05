#!/usr/bin/env python3
"""forecast.py v2.0 — regenerates the FORECASTING.md §3 snapshot from the repo.

Method (FORECASTING §2, v3.0):
  - PMC from the STORED `tss` column (one TSS definition, computed once in
    master_sync). Only recomputed from np_calculated when --ftp differs from the
    manifest FTP, i.e. when deliberately re-baselining.
  - Weight from a TRUE 7-day rolling mean of INDEX_SCALE readings only, with a
    >=5-reading validity gate. Rate measured from the window midpoint.
  - Recovery: 4 weekly windows, slope AND correlation. No slope claim below 3
    points (FORECASTING §2 slope-evidence rule).
  - Countdown block printed FIRST, including LAPSED and PARTIAL deliverables.

Runs from any directory: fetches the CSVs it needs from the repo raw URLs into
~/fc-data, falling back to the current directory if the network is unavailable.

  python3 forecast.py [--ftp 315] [--load 85] [--taper-start 2026-08-20] [--local]

CHANGES FROM v1.0 — each fixed a defect found on 2026-07-30:
  1. Decision queue filtered `d >= today`, so LAPSED deliverables were invisible.
     This script is supposed to be the enforcement mechanism for the session-open
     countdown (CHANGELOG §2 item 4) and it hid exactly what it exists to surface.
  2. Recovery slope was a 2-point week-over-week difference. It reported
     "RHR slope +1.7/wk" on 30 Jul; across 4 weekly points RHR is flat
     (+0.06/wk, r=+0.09). The tool generated a false trend claim.
  3. Weight used the last 7 ENTRIES (spanning 12 days on 30 Jul), applied no
     INDEX_SCALE filter, had no reading-count gate, listed the retired 70 kg
     target, and measured the rate from today rather than the window midpoint.
  4. TSS was recomputed from duration_min while master_sync uses recording
     seconds -> a systematic -5.4% (CTL 77.7 vs 78.9). Two TSS definitions.
  5. Weekly TSS bucketed by ISO week, so the current partial week printed as a
     load collapse (273 TSS on a Thursday).
  Plus: SGT dates (UTC today() is a day behind for early-morning runs), manifest
  read with staleness check, multi-scenario projection, W' 72h check, crash guard
  in recovery() when a prior window is empty.
"""
import argparse, csv, math, os, statistics, sys, urllib.request
from datetime import date, datetime, timedelta, timezone

SCRIPT_VERSION = "2.1"
SGT = timezone(timedelta(hours=8))
RAW = "https://raw.githubusercontent.com/esquiremurphington/training-data/main/"
NEEDED = ["cycling_deep.csv", "daily_health.csv", "nutrition.csv", "manifest.json"]
# Optional: absent until the first run is synced. Fetched best-effort, never a
# hard requirement — a missing running.csv must not trip the cwd fallback in
# load_data() and silently switch the whole script to stale local files.
OPTIONAL = ["running.csv"]
CACHE = os.path.expanduser("~/fc-data")
NISEKO = date(2026, 8, 30)
KC, KA = 1 - math.exp(-1 / 42), 1 - math.exp(-1 / 7)

# ── Deliverables ──────────────────────────────────────────────────────────────
# LAPSED and PARTIAL items are printed EVERY run until their status is changed
# here. Silence is not evidence of completion.
OPEN_DELIVERABLES = [
    (date(2026, 7, 12), "CP test DROP-DEAD",
     "PARTIAL — 3 of 4 efforts done (3' 435W + 12' 335W on 8 Jul; 5' 381W PB on 11 Jul). "
     "8' attempted and ABANDONED at 3:50/365W. Re-placed to Tue 4 Aug."),
    (date(2026, 7, 12), "Niseko tier rebuild", "BLOCKED on the 8' anchor. GOALS section 1 still LEGACY."),
    (date(2026, 7, 12), "Milestone audit", "MISSED — not run."),
    (date(2026, 7, 15), "Heat-acclimation go/no-go", "CLOSED BY LAPSE 30 Jul — window shut (TRAINING_PROTOCOLS section 8)."),
    (date(2026, 7, 20), "Milestone audit", "MISSED — not run."),
]
MILESTONES = [
    (date(2026, 8, 1),  "Downgrade Sat QRA to Z2 (anchor lead-in)"),
    (date(2026, 8, 4),  "8' CP ANCHOR (TSB >= +5 required)"),
    (date(2026, 8, 8),  "Anchor backstop -> adopt CP 301 / Genting 2 go-no-go"),
    (date(2026, 8, 9),  "Milestone audit — 3 wk pre-Niseko"),
    (date(2026, 8, 10), "HRV governor check (weekly < 31?)"),
    (date(2026, 8, 16), "Genting 2 / block CTL peak"),
    (date(2026, 8, 20), "Niseko taper start"),
    (date(2026, 8, 27), "Niseko ITT — UNRESOLVED (no TT bike)"),
    (NISEKO,            "NISEKO"),
    (date(2026, 9, 15), "Chiang Mai GPX sourced"),
    (date(2026, 9, 25), "Milestone audit — 3 wk pre-Chiang Mai"),
    (date(2026, 10, 16), "Chiang Mai"),
]
WEIGHT_TARGETS = (75.5, 75.0, 74.5, 73.5)   # 70 kg retired 6 Jul 2026
RATE_PRESCRIBED, RATE_CEILING = 0.47, 0.60


def today_sgt():
    return datetime.now(SGT).date()


def fnum(r, k):
    try:
        v = float(r.get(k, ""))
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def load_data(local):
    """Fetch from the repo into ~/fc-data, or read the cwd with --local."""
    if local:
        return "."
    os.makedirs(CACHE, exist_ok=True)
    for f in NEEDED:
        try:
            urllib.request.urlretrieve(RAW + f, os.path.join(CACHE, f))
        except Exception as e:
            print(f"  ! fetch failed for {f} ({e}) — falling back to cwd")
            return "."
    for f in OPTIONAL:
        try:
            urllib.request.urlretrieve(RAW + f, os.path.join(CACHE, f))
        except Exception:
            pass  # not yet in the repo — rows_optional() handles the absence
    return CACHE


def manifest(root):
    import json
    try:
        return json.load(open(os.path.join(root, "manifest.json")))
    except Exception:
        return {}


def rows(root, name):
    return list(csv.DictReader(open(os.path.join(root, name))))


def rows_optional(root, name):
    """Like rows(), but [] if absent. running.csv does not exist until the first
    run is synced, and its absence is normal rather than an error."""
    p = os.path.join(root, name)
    if not os.path.exists(p):
        return []
    try:
        return list(csv.DictReader(open(p)))
    except Exception:
        return []


# ── 1. COUNTDOWN — printed first, always ──────────────────────────────────────
def countdown(root, mf, deep):
    t = today_sgt()
    print(f"COUNTDOWN BLOCK — {t} SGT   (forecast.py v{SCRIPT_VERSION})")

    sync = mf.get("last_sync_sgt", "unknown")
    print(f"  repo: last sync {sync} | schema v{mf.get('schema_version','?')} | "
          f"FTP {mf.get('ftp')} CP {mf.get('cp')} W' {mf.get('w_prime')} tau {mf.get('w_prime_tau')}")
    if mf.get("cp") and mf.get("ftp") and mf["cp"] != mf["ftp"]:
        print(f"    note: W' balance depletes above CP ({mf['cp']}W), not FTP — SETTLED READ-RULES")

    # auto-close the anchor if an 8-minute maximal effort has appeared
    anchor_done = [r for r in deep
                   if r["date"] >= "2026-07-12" and (fnum(r, "p8min") or 0) >= 340]
    print()
    print("  LAPSED / PARTIAL DELIVERABLES:")
    if not OPEN_DELIVERABLES:
        print("    none")
    for d, name, status in OPEN_DELIVERABLES:
        if "8'" in status and anchor_done:
            best = max(anchor_done, key=lambda r: fnum(r, "p8min"))
            print(f"    [{(t-d).days:+4}d] {name:<26} LIKELY RESOLVED — p8min "
                  f"{fnum(best,'p8min'):.0f}W on {best['date']}; verify and close in this file")
            continue
        print(f"    [{(t-d).days:+4}d] {name:<26} {status}")

    print()
    print("  UPCOMING:")
    nxt = [(d, n) for d, n in MILESTONES if d >= t]
    for d, n in nxt[:6]:
        print(f"    T-{(d-t).days:<3}d  {d}  {n}")
    if nxt:
        print(f"  >> nearest decision: {nxt[0][1]} in {(nxt[0][0]-t).days} day(s)")


# ── 2. PMC ────────────────────────────────────────────────────────────────────
def pmc(root, mf, ftp, scenarios, taper_start, taper_load):
    deep = rows(root, "cycling_deep.csv")
    mftp = mf.get("ftp")
    recompute = mftp is not None and int(ftp) != int(mftp)

    daily = {}
    for r in deep:
        d = r["date"][:10]
        if recompute:
            np_, dur = fnum(r, "np_calculated"), fnum(r, "duration_min")
            v = (dur / 60) * (np_ / ftp) ** 2 * 100 if (np_ and dur) else 0
        else:
            v = fnum(r, "tss") or 0
        if v:
            daily[d] = daily.get(d, 0) + v
    src = (f"recomputed from np_calculated at FTP {ftp} (manifest says {mftp} — deliberate re-baseline)"
           if recompute else f"stored `tss` column (FTP {mftp}) — one definition, computed in master_sync")
    print(f"TSS source (cycling): {src}")

    # ── Running load ─────────────────────────────────────────────────────────
    # hrTSS from running.csv, folded into the SAME daily series. Different
    # computation (heart rate, not power), same scale — so the sum is valid.
    # Running is a FATIGUE input only: it never touches CP/W'/FTP/MLSS/VO2 or
    # the power curve, because those read cycling_deep.csv and this does not
    # write there. See the isolation contract in master_sync.sync_garmin_running.
    runs = rows_optional(root, "running.csv")
    run_daily, run_total, provisional, no_hr = {}, 0.0, False, 0
    for r in runs:
        v = fnum(r, "hr_tss") or 0
        if v:
            d = r["date"][:10]
            run_daily[d] = run_daily.get(d, 0) + v
            daily[d] = daily.get(d, 0) + v
            run_total += v
        else:
            no_hr += 1
        if str(r.get("provisional", "")).strip() == "1":
            provisional = True

    if runs:
        note = "  [PROVISIONAL lthr_run — ±15% band]" if provisional else ""
        print(f"TSS source (running): hrTSS at lthr_run {mf.get('lthr_run')} — "
              f"{len(runs)} run(s), {run_total:.0f} TSS{note}")
        if no_hr:
            print(f"  ! {no_hr} run(s) had no usable avg HR — contributing 0 to CTL/ATL")
    else:
        print("TSS source (running): no running.csv — cycling only")

    start = min(date.fromisoformat(k) for k in daily)
    end = max(date.fromisoformat(k) for k in daily)
    ctl = atl = 0.0
    d = start
    while d <= end:
        t = daily.get(d.isoformat(), 0.0)
        ctl += (t - ctl) * KC
        atl += (t - atl) * KA
        d += timedelta(days=1)
    print(f"PMC as of {end}:  CTL {ctl:.1f} | ATL {atl:.1f} | TSB {ctl-atl:+.1f}")
    if run_daily:
        lo = (end - timedelta(days=6)).isoformat()
        r7 = sum(v for k, v in run_daily.items() if lo <= k <= end.isoformat())
        c7 = sum(v for k, v in daily.items() if lo <= k <= end.isoformat())
        print(f"  running share of trailing 7-day load: {r7:.0f} of {c7:.0f} TSS "
              f"({(r7 / c7 * 100) if c7 else 0:.0f}%)")

    # trailing 7-day windows (not ISO weeks — a partial ISO week reads as a collapse)
    print("  trailing 7-day load:")
    for i in range(4):
        b = end - timedelta(days=7 * i)
        a = b - timedelta(days=6)
        tot = sum(v for k, v in daily.items() if a.isoformat() <= k <= b.isoformat())
        print(f"    {a} .. {b}: {tot:5.0f} TSS")
    print("    (band for this block: 596-677)")

    print(f"  projection — taper from {taper_start} at {taper_load} TSS/day:")
    KEY = [d for d, _ in MILESTONES if d in
           (date(2026, 8, 4), date(2026, 8, 16), date(2026, 8, 20), NISEKO) and d > end]
    hdr = "    " + f"{'scenario':<14}" + "".join(f"{k.strftime('%d %b'):>16}" for k in KEY)
    print(hdr)
    for load in scenarios:
        c, a, d, cells = ctl, atl, end, {}
        while d < NISEKO:
            d += timedelta(days=1)
            t = load if d < taper_start else taper_load
            c += (t - c) * KC
            a += (t - a) * KA
            if d in KEY:
                cells[d] = (c, c - a)
        row = f"    {load:>3.0f} TSS/day  " + "".join(
            f"{cells[k][0]:>8.1f}/{cells[k][1]:+5.1f}" if k in cells else f"{'—':>16}" for k in KEY)
        print(row)
    print("    (CTL / TSB)   4 Aug = anchor · 16 Aug = block peak · 20 Aug = taper · 30 Aug = NISEKO")
    print("    Niseko target (FORECASTING v3.0): CTL 74-77, TSB +8 to +12. "
          "CTL >=80 is NOT reachable — do not chase it.")
    return ctl, atl, daily, end


def project_tsb(ctl, atl, daily_end, plan):
    """plan = {date: tss}. Returns {date: (ctl, atl, tsb)} for each planned day."""
    c, a, d, out = ctl, atl, daily_end, {}
    for k in sorted(plan):
        while d < k:
            d += timedelta(days=1)
            t = plan.get(d, 0.0)
            c += (t - c) * KC
            a += (t - a) * KA
        out[k] = (c, a, c - a)
    return out


# ── 3. Weight ─────────────────────────────────────────────────────────────────
def weight(root):
    h = rows(root, "daily_health.csv")
    ws = [(date.fromisoformat(r["date"]), fnum(r, "weight_kg"))
          for r in h if fnum(r, "weight_kg") and r.get("weight_source") == "INDEX_SCALE"]
    if not ws:
        print("Weight: no INDEX_SCALE readings")
        return
    ws.sort()
    last = ws[-1][0]

    def window(end_d):
        a = end_d - timedelta(days=6)
        v = [w for d, w in ws if a <= d <= end_d]
        return (statistics.mean(v), len(v), a, end_d) if v else None

    print("Weight — 7-DAY windows, INDEX_SCALE only (>=5 readings required):")
    valid = None
    for i in range(4):
        w = window(last - timedelta(days=7 * i))
        if not w:
            continue
        m, n, a, b = w
        ok = n >= 5
        print(f"  {a} .. {b}: {m:6.2f} kg  n={n}  {'valid' if ok else 'BELOW GATE — no mean'}")
        if ok and valid is None:
            valid = (m, a, b)
    if valid is None:
        print("  -> no window meets the >=5-reading gate. No rate quoted. Measurement is broken.")
        return
    m, a, b = valid
    mid = a + (b - a) / 2
    wks = (NISEKO - mid).days / 7
    print(f"  -> last valid mean {m:.2f} kg, window midpoint {mid}, {wks:.2f} wk to Niseko")
    print(f"  required rates (prescribed {RATE_PRESCRIBED} kg/wk, ceiling {RATE_CEILING}):")
    for tgt in WEIGHT_TARGETS:
        rate = (m - tgt) / wks
        tag = ("at/below prescribed" if rate <= RATE_PRESCRIBED + 0.03 else
               "acceptable" if rate <= RATE_CEILING else "ABOVE CEILING")
        print(f"    to {tgt:.1f} kg: {rate:5.2f} kg/wk  [{tag}]")
    print(f"  projection at prescribed rate: {m - RATE_PRESCRIBED*wks:.1f} kg at Niseko")


# ── 4. Recovery ───────────────────────────────────────────────────────────────
def slope_r(y):
    n = len(y)
    if n < 3:
        return None, None
    x = list(range(n))
    mx, my = sum(x)/n, sum(y)/n
    sxy = sum((xi-mx)*(yi-my) for xi, yi in zip(x, y))
    sxx = sum((xi-mx)**2 for xi in x)
    syy = sum((yi-my)**2 for yi in y)
    if sxx == 0 or syy == 0:
        return None, None
    return sxy/sxx, sxy/math.sqrt(sxx*syy)


def recovery(root):
    h = rows(root, "daily_health.csv")
    last = date.fromisoformat(h[-1]["date"])
    print("Recovery — 4 weekly windows (slope requires >=3 points, FORECASTING section 2):")
    for key, label, note in (("hrv_last_night", "HRV nightly", "gate >=30 nightly / >=31 weekly"),
                             ("resting_hr_bpm", "RHR", "target <=50, red >=54")):
        series, labels = [], []
        for i in range(3, -1, -1):
            b = last - timedelta(days=7*i)
            a = b - timedelta(days=6)
            v = [fnum(r, key) for r in h
                 if a.isoformat() <= r["date"] <= b.isoformat() and fnum(r, key)]
            if v:
                series.append(statistics.mean(v))
                labels.append(f"{statistics.mean(v):.1f}(n={len(v)})")
        print(f"  {label:<12} " + " -> ".join(labels) + f"   [{note}]")
        s, r = slope_r(series)
        if s is None:
            print(f"    slope: not claimed ({len(series)} points)")
        elif abs(r) < 0.5:
            print(f"    slope {s:+.2f}/wk but r={r:+.2f} — FLAT, not a trend")
        else:
            print(f"    slope {s:+.2f}/wk, r={r:+.2f} — real trend")
            if key == "hrv_last_night" and s < 0 and series[-1] > 31:
                print(f"    lead-time to the WEIGHT section 7 governor (<31): "
                      f"{(series[-1]-31)/abs(s):.1f} weeks")
    l = h[-1]
    print(f"  today: HRV {l.get('hrv_last_night')} | weekly {l.get('hrv_weekly_avg')} "
          f"{l.get('hrv_status')} | RHR {l.get('resting_hr_bpm')}   <- spot values, not the trend")


# ── 5. Fuelling + W' 72h check ────────────────────────────────────────────────
def fuelling(root):
    n = rows(root, "nutrition.csv")
    recent = n[-7:]
    zero = [r["date"] for r in recent if not fnum(r, "logged_calories")]
    complete = [r for r in recent if (fnum(r, "logged_calories") or 0) >= 0.5 * (fnum(r, "goal_calories") or 1e9)]
    print(f"Fuelling: {len(complete)}/7 days at >=50% of goal | zero-log days: {len(zero)}")
    if zero:
        print(f"  zero-log: {', '.join(d[5:] for d in zero)}")
    if len(complete) < 5:
        print("  -> logging FAILS the >=5/7 precondition. WEIGHT section 2: the cut pauses at "
              "maintenance. No EA figure may be quoted in either direction.")
    prot = [(r["date"], fnum(r, "logged_protein_g")) for r in n[-14:] if fnum(r, "logged_protein_g")]
    if prot:
        cleared = sum(1 for _, p in prot if p >= 150)
        print(f"  protein floor (150 g): cleared {cleared}/{len(prot)} logged days "
              f"(best {max(p for _, p in prot):.0f} g)")


def wprime_72h(root):
    deep = rows(root, "cycling_deep.csv")
    if not deep:
        return
    last = date.fromisoformat(deep[-1]["date"])
    cutoff = (last - timedelta(days=2)).isoformat()
    tot = sum(fnum(r, "wprime_critical_min") or 0 for r in deep if r["date"] >= cutoff)
    print(f"W' load, last 72h: {tot:.1f} min below 25% W'")
    if tot > 0:
        print("  CP_TEST_PROTOCOL section D: no session driving W' below 25% in the 72h "
              "before a maximal test. TRAINING_PROTOCOLS section 1a: both Tue Faber and "
              "Thu 30/15s do this by construction.")


# ── 2b. RUNNING — eccentric load ──────────────────────────────────────────────
def running_risk(root):
    """Surface recent unaccustomed running.

    hrTSS is a metabolic measure and cannot see eccentric muscle damage: a 30-min
    easy run may score ~25 TSS while costing days of compromised force production
    at the contraction speeds climbing uses. Inflating the TSS to compensate would
    corrupt the shared load scale and give the PMC two meanings, so load and risk
    are kept as separate signals — the number stays honest, the risk gets printed.
    """
    runs = rows_optional(root, "running.csv")
    if not runs:
        return
    t = today_sgt()
    recent = []
    for r in runs:
        try:
            d = date.fromisoformat(r["date"][:10])
        except (ValueError, KeyError):
            continue
        if (t - d).days <= 4:
            recent.append((d, r))
    if not recent:
        return

    print("\nRUNNING — ECCENTRIC LOAD")
    for d, r in sorted(recent):
        age = (t - d).days
        gap = r.get("days_since_prev_run") or "n/a"
        flag = str(r.get("eccentric_flag", "")).strip() == "1"
        print(f"  {r['date']}  {r.get('duration_min')} min  "
              f"{r.get('hr_tss')} hrTSS  (prev run {gap} d before, {age} d ago)"
              + ("   ** UNACCUSTOMED **" if flag else ""))
        if flag and age <= 3:
            print("     DOMS window OPEN. Force production at climbing cadence may be "
                  "impaired.")
            print("     Do not schedule a maximal effort or a CP test inside 72 h.")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ftp", type=int, default=None, help="override; default = manifest FTP")
    p.add_argument("--load", type=float, nargs="*", default=[95, 85, 70], help="build TSS/day scenarios")
    p.add_argument("--taper-load", type=float, default=62)
    p.add_argument("--taper-start", type=lambda s: date.fromisoformat(s), default=date(2026, 8, 20))
    p.add_argument("--local", action="store_true", help="read CSVs from cwd instead of fetching")
    a = p.parse_args()

    root = load_data(a.local)
    mf = manifest(root)
    deep = rows(root, "cycling_deep.csv")
    ftp = a.ftp if a.ftp else (mf.get("ftp") or 315)

    print("=" * 78)
    countdown(root, mf, deep)
    print("-" * 78)
    ctl, atl, daily, end = pmc(root, mf, ftp, a.load, a.taper_start, a.taper_load)
    running_risk(root)
    print("-" * 78)
    weight(root)
    print("-" * 78)
    recovery(root)
    print("-" * 78)
    fuelling(root)
    wprime_72h(root)
    print("=" * 78)
    print("Snapshot is a SCENARIO SET, not a prediction. Paste the refreshed section 3 "
          "into FORECASTING.md as a STATE HANDOFF, and log it in CHANGELOG.md.")


if __name__ == "__main__":
    main()
