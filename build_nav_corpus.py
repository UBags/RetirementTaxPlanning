# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""build_nav_corpus.py

Harvest twenty years of DAILY NAVs for a spread of real funds into one JSON
file, for swp_dp_model.py to run against real prices instead of a synthetic
compounding curve.

WHY THIS EXISTS

  swp_dp_model.py grows every NAV at a flat modelled rate, so a month of price
  movement is 0.49% on debt and 0.80% on equity. Against a monthly decision
  granularity of 8.33% that is nothing, which is why monthly execution
  measured against synthetic prices changes almost no number. A real equity
  series at 20% annual volatility moves about 5.8% in a month. THAT is what
  makes a monthly engine worth building, and it cannot be tested without real
  prices.

TWO CONSTRAINTS THAT SHAPE THE SELECTION, both discovered rather than assumed

  DIRECT PLANS CANNOT GIVE YOU TWENTY YEARS. They were introduced on 1 January
  2013, so the longest possible Direct history is about thirteen years. A
  twenty-year corpus has to be built from REGULAR plans. That is a real
  difference in expense ratio, roughly 0.5 to 1.0 percent a year, and it means
  the harvested returns are LOWER than a Direct investor would earn. The
  corpus records plan="REGULAR" on every fund so nobody forgets.

  MOST FUNDS ARE YOUNGER THAN TWENTY YEARS. Whole categories -- overnight
  funds, most index funds, anything post-2018 recategorisation -- simply have
  no 2005 history. The tool asks for --years but reports what it actually got
  per category rather than silently returning short series, and --min-years
  lets you accept less where twenty is impossible.

USAGE

    python3 build_nav_corpus.py --list-categories
    python3 build_nav_corpus.py --plan            # choose funds, fetch nothing
    python3 build_nav_corpus.py --out nav_corpus.json
    python3 build_nav_corpus.py --out nav_corpus.json --resume

  --plan is the one to run first. It resolves the categories and picks the
  candidates without touching the NAV API, so a bad selection costs seconds
  rather than an hour.

  Fetching is INCREMENTAL AND RESUMABLE. Every fund is written to the cache as
  it arrives, so a network failure at fund forty costs one fund and not forty.
  --resume skips anything already cached.

OUTPUT

    {
      "generated": "2026-08-04T09:00:00",
      "source": "api.mfapi.in daily NAV, via get_funds_data.fetch_nav_daily",
      "plan": "REGULAR",
      "window": {"start": "2005-08-01", "end": "2025-08-01", "years": 20},
      "funds": [
        {"amfi_code": "100123", "scheme_name": "...", "category": "...",
         "first_date": "2005-04-01", "last_date": "2025-07-31",
         "trading_days": 4988,
         "nav": {"2005-04-01": 12.3456, ...}}
      ]
    }

  Dates are ISO and NAVs are floats, so the file loads without pandas. Roughly
  150KB a fund, so about 8MB for fifty over twenty years.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger("nav_corpus")

# Categories worth a retirement model, in the order they are filled. The names
# are matched case-insensitively as SUBSTRINGS of the AMFI category string,
# because AMFI writes things like "Open Ended Schemes(Debt Scheme - Short
# Duration Fund)" and the useful part is buried in it.
DEFAULT_CATEGORIES = [
    "Overnight",
    "Liquid",
    "Ultra Short",
    "Low Duration",
    "Money Market",
    "Short Duration",
    "Corporate Bond",
    "Banking and PSU",
    "Gilt",
    "Large Cap",
    "Flexi Cap",
    "Mid Cap",
]


def _module():
    """Import get_funds_data, which must be importable from the CWD or path."""
    try:
        import get_funds_data as G
    except ImportError:
        sys.exit(
            "FATAL: cannot import get_funds_data. Run this from the directory "
            "that holds it, or put that directory on PYTHONPATH."
        )
    if not hasattr(G, "fetch_nav_daily"):
        sys.exit(
            "FATAL: get_funds_data has no fetch_nav_daily. Apply "
            "patch_get_funds_data_daily.py first -- fetch_nav resamples to "
            "month end and discards the daily series this tool needs."
        )
    return G


def _is_regular_growth(name: str) -> bool:
    """Regular plan, Growth option.

    Direct is excluded because it cannot reach twenty years. Dividend, IDCW and
    Bonus options are excluded because their NAV series is discontinuous at
    every payout, which would read as a price crash to anything downstream.
    """
    low = name.lower()
    if "direct" in low:
        return False
    if any(w in low for w in ("idcw", "dividend", "bonus", "payout",
                              "reinvest")):
        return False
    return "growth" in low


def choose_funds(G, categories, per_category):
    """Pick candidates per category from the AMFI master, oldest AMFI code
    first.

    A LOW AMFI CODE IS A PROXY FOR AGE, and a rough one. Codes were issued
    roughly in order, so the lowest codes in a category are the likeliest to
    have twenty years behind them -- but it is a heuristic, not a guarantee,
    which is why more candidates are taken than are needed and the short ones
    are dropped after the history arrives.
    """
    master = G._load_amfi_master()
    if master is None or master.empty:
        sys.exit("FATAL: the AMFI master came back empty. Network?")

    picked = defaultdict(list)
    seen_codes = set()
    for _idx, row in master.iterrows():
        name = str(row.get("scheme_name", ""))
        cat = str(row.get("category", ""))
        code = str(row.get("scheme_code", "")).strip()
        if not code or code in seen_codes or not _is_regular_growth(name):
            continue
        for wanted in categories:
            if wanted.lower() in cat.lower():
                picked[wanted].append((code, name, cat))
                seen_codes.add(code)
                break

    out = {}
    for cat in categories:
        cands = sorted(picked.get(cat, []), key=lambda t: int(t[0]) if
                       t[0].isdigit() else 10 ** 9)
        # Three times what is needed, because the age heuristic is loose and
        # short series get dropped once the history is in hand.
        out[cat] = cands[:per_category * 3]
    return out


def harvest(G, plan, years, min_years, per_category, cache_path, resume,
            pause):
    """Fetch daily NAVs fund by fund, writing the cache as each one lands."""
    end = datetime.now().date()
    want_start = end - timedelta(days=int(years * 365.25))
    floor_days = int(min_years * 365.25)

    cache = {}
    if resume and cache_path.exists():
        cache = json.loads(cache_path.read_text())
        log.info("resuming with %d funds already cached", len(cache))

    kept = defaultdict(list)
    for cat, cands in plan.items():
        for code, name, raw_cat in cands:
            if len(kept[cat]) >= per_category:
                break
            if code in cache:
                rec = cache[code]
                if rec.get("category_bucket") == cat:
                    kept[cat].append(rec)
                continue
            try:
                df = G.fetch_nav_daily(code)
            except Exception as exc:                    # noqa: BLE001
                log.warning("%s %s: %s", code, name[:48], exc)
                continue
            if df is None or df.empty:
                continue
            df = df[df["date"].dt.date >= want_start]
            if df.empty:
                continue
            span = (df["date"].iloc[-1] - df["date"].iloc[0]).days
            if span < floor_days:
                log.info("skip %s %s -- only %.1f years",
                         code, name[:44], span / 365.25)
                continue
            rec = {
                "amfi_code": code,
                "scheme_name": name,
                "amfi_category": raw_cat,
                "category_bucket": cat,
                "plan": "REGULAR",
                "first_date": df["date"].iloc[0].date().isoformat(),
                "last_date": df["date"].iloc[-1].date().isoformat(),
                "trading_days": int(len(df)),
                "years": round(span / 365.25, 2),
                "nav": {d.date().isoformat(): float(v)
                        for d, v in zip(df["date"], df["nav"])},
            }
            cache[code] = rec
            cache_path.write_text(json.dumps(cache))
            kept[cat].append(rec)
            log.info("kept %s %-46s %5.1f yr  %5d days",
                     code, name[:46], rec["years"], rec["trading_days"])
            if pause:
                time.sleep(pause)
    return kept, want_start, end


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="nav_corpus.json")
    ap.add_argument("--cache", default=".nav_corpus_cache.json")
    ap.add_argument("--years", type=float, default=20.0)
    ap.add_argument("--min-years", type=float, default=18.0,
                    help="a fund shorter than this is dropped. Below --years "
                         "because a fund launched in mid-2006 is still worth "
                         "having.")
    ap.add_argument("--per-category", type=int, default=5)
    ap.add_argument("--categories", default=None,
                    help="comma-separated overrides for the default list")
    ap.add_argument("--pause", type=float, default=0.4,
                    help="seconds between API calls. Be polite to mfapi.in.")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--plan", action="store_true",
                    help="resolve and print the selection, fetch nothing")
    ap.add_argument("--list-categories", action="store_true",
                    help="print every AMFI category with a fund count")
    a = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    G = _module()

    if a.list_categories:
        master = G._load_amfi_master()
        counts = defaultdict(int)
        for _i, row in master.iterrows():
            if _is_regular_growth(str(row.get("scheme_name", ""))):
                counts[str(row.get("category", ""))] += 1
        for cat, n in sorted(counts.items(), key=lambda t: -t[1]):
            print(f"{n:5d}  {cat}")
        return 0

    cats = ([c.strip() for c in a.categories.split(",")] if a.categories
            else DEFAULT_CATEGORIES)
    plan = choose_funds(G, cats, a.per_category)

    if a.plan:
        print(f"{'category':22s}{'candidates':>12s}   first few")
        print("-" * 78)
        for cat in cats:
            names = ", ".join(n[:26] for _c, n, _r in plan[cat][:2])
            print(f"{cat:22s}{len(plan[cat]):>12d}   {names}")
        print("\nNothing was fetched. Drop --plan to harvest.")
        return 0

    kept, start, end = harvest(G, plan, a.years, a.min_years,
                               a.per_category, Path(a.cache), a.resume,
                               a.pause)

    funds = [r for cat in cats for r in kept.get(cat, [])]
    if not funds:
        sys.exit("FATAL: nothing was kept. Try --min-years 10, or check the "
                 "network.")

    doc = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "source": "api.mfapi.in daily NAV, via "
                  "get_funds_data.fetch_nav_daily",
        "plan": "REGULAR",
        "plan_note": "Regular plans, not Direct. Direct was introduced on "
                     "1 January 2013 and cannot reach twenty years, so these "
                     "returns are NET OF THE REGULAR EXPENSE RATIO and run "
                     "roughly 0.5 to 1.0 percent a year below what a Direct "
                     "investor would have earned.",
        "window": {"start": start.isoformat(), "end": end.isoformat(),
                   "years": a.years},
        "funds": funds,
    }
    Path(a.out).write_text(json.dumps(doc, indent=1))

    print()
    print(f"{'category':22s}{'kept':>6}{'shortest':>10}{'longest':>9}")
    print("-" * 50)
    for cat in cats:
        rs = kept.get(cat, [])
        if not rs:
            print(f"{cat:22s}{0:>6}         -        -   NONE with the history")
            continue
        ys = [r["years"] for r in rs]
        print(f"{cat:22s}{len(rs):>6}{min(ys):>10.1f}{max(ys):>9.1f}")
    total_days = sum(r["trading_days"] for r in funds)
    print(f"\nwrote {a.out} -- {len(funds)} funds, {total_days} daily NAVs, "
          f"{Path(a.out).stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())