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

from swp_planner.paths import NAV_DIR, ensure_parent

log = logging.getLogger("nav_corpus")

# Categories worth a retirement model, in the order they are filled. The names
# are matched case-insensitively as SUBSTRINGS of the AMFI category string,
# because AMFI writes things like "Open Ended Schemes(Debt Scheme - Short
# Duration Fund)" and the useful part is buried in it.
# EXACT AMFI sub-category names, not substrings. Substring matching put two
# LARGE & MID CAP funds into a "Mid Cap" bucket, because "Mid Cap" is inside
# "Large & Mid Cap Fund" -- a different category with a different risk
# profile, and their volatility sat alongside the large caps rather than above
# them. The whole point of picking by category is that the categories mean
# something.
DEFAULT_CATEGORIES = [
    "Overnight Fund",
    "Liquid Fund",
    "Ultra Short Duration Fund",
    "Low Duration Fund",
    "Money Market Fund",
    "Short Duration Fund",
    "Corporate Bond Fund",
    "Banking and PSU Fund",
    "Gilt Fund",
    "Large Cap Fund",
    "Flexi Cap Fund",
    "Mid Cap Fund",
]

# A twenty-year daily series has to look like what it claims to be. These are
# deliberately WIDE -- they are not a quality filter, they are a corruption
# detector. SBI Overnight came out of a harvest at 34% CAGR with 2226%
# volatility because a zero NAV shielded a 100x consolidation; a screen this
# loose would still have caught it, whatever the cause.
#
# (min CAGR, max CAGR, max annualised volatility), all as fractions.
PLAUSIBLE = {
    "Overnight Fund":            (0.02, 0.12, 0.03),
    "Liquid Fund":               (0.02, 0.12, 0.03),
    "Ultra Short Duration Fund": (0.02, 0.13, 0.05),
    "Low Duration Fund":         (0.02, 0.13, 0.05),
    "Money Market Fund":         (0.02, 0.13, 0.05),
    "Short Duration Fund":       (0.02, 0.14, 0.10),
    "Corporate Bond Fund":       (0.02, 0.14, 0.10),
    "Banking and PSU Fund":      (0.02, 0.14, 0.10),
    "Gilt Fund":                 (0.02, 0.16, 0.15),
    "Large Cap Fund":            (0.00, 0.30, 0.40),
    "Flexi Cap Fund":            (0.00, 0.32, 0.45),
    "Mid Cap Fund":              (0.00, 0.35, 0.50),
}
PLAUSIBLE_DEFAULT = (-0.05, 0.40, 0.60)

# A series that stops well before everything else has stopped is a fund that
# no longer exists. Franklin India Short-Term Income Plan was one of the six
# debt schemes wound up on 23 April 2020; its NAV runs on through the
# five-year run-off and then simply ends. Over its whole life it shows 7.96%
# CAGR at 4.15% volatility -- both entirely reasonable -- so it sailed past
# the plausibility screen, past the twenty-year screen, and into the corpus,
# where it truncated EVERY other fund's usable history to its own end date.
#
# No statistic catches this, because the statistics are fine. Only the end
# date gives it away.
DEAD_FUND_DAYS = 120


def _subcategory(amfi_category):
    """Pull "Liquid Fund" out of "Open Ended Schemes(Debt Scheme - Liquid
    Fund)". Returns the whole string unchanged if it does not parse."""
    inner = amfi_category
    if "(" in inner and ")" in inner:
        inner = inner[inner.index("(") + 1:inner.rindex(")")]
    return inner.rsplit(" - ", 1)[-1].strip()


_PLAN_WORDS = ("regular plan", "direct plan", "super institutional",
               "institutional plan", "institutional", "retail plan", "retail",
               "growth option", "growth plan", "growth", "regular", "direct",
               "option", "plan", "-", "(", ")")


def _base_fund_key(name):
    """Collapse share classes of one scheme to a single key.

    100234 "JM Liquid Fund" and 100247 "JM Liquid Fund (Regular) - Super
    Institutional Plan" are the SAME fund in two share classes: both returned
    7.0% at 0.5% volatility and their correlation is essentially one. Taking
    both is not diversification, it is the same series twice, and it crowds out
    a genuinely different fund.
    """
    low = " " + " ".join(name.lower().split()) + " "
    for w in _PLAN_WORDS:
        low = low.replace(w, " ")
    return " ".join(low.split())


def _annualised(nav_by_date):
    """(CAGR, annualised volatility) from a daily series, via month ends."""
    keys = sorted(nav_by_date)
    if len(keys) < 24:
        return None, None
    months = {}
    for k in keys:
        months[k[:7]] = nav_by_date[k]
    mk = sorted(months)
    first, last = months[mk[0]], months[mk[-1]]
    years = len(mk) / 12.0
    if first <= 0 or last <= 0 or years < 1:
        return None, None
    cagr = (last / first) ** (1.0 / years) - 1.0
    rets = [months[b] / months[a] - 1.0 for a, b in zip(mk, mk[1:])
            if months[a] > 0]
    if len(rets) < 12:
        return cagr, None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    return cagr, (var ** 0.5) * (12 ** 0.5)


def _tune_network(G, connect, read, inner_retries):
    """Make get_funds_data patient enough for a hundred-call harvest.

    ITS DEFAULTS ARE TUNED FOR THE OPPOSITE JOB. REQUEST_TIMEOUT is (8, 30) --
    eight seconds to CONNECT -- with MAX_RETRIES 2 and a 1.5s backoff, so the
    whole budget before giving up is about seventeen seconds. That is the right
    shape for an interactive tool that should fail fast on an unreachable host.
    It is the wrong shape here: mfapi.in is slow when asked for twenty years of
    daily NAVs, and an eight-second TLS handshake budget loses funds to nothing
    worse than a busy server.

    Set on the module rather than edited in it, so downstream callers keep the
    fast-fail defaults they were given.
    """
    G.REQUEST_TIMEOUT = (connect, read)
    G.MAX_RETRIES = inner_retries
    return G


def _fetch_with_backoff(G, code, attempts, first_wait):
    """Fetch one fund, distinguishing a bad fund from a bad moment.

    THE ORIGINAL VERSION OF THIS SWALLOWED BOTH. A transient timeout and a
    scheme with no data both hit `except Exception: continue`, so a network
    blip in the middle of a hundred-fund harvest would quietly drop good funds
    and the summary would report it as "no fund had the history" -- a wrong
    conclusion that looks exactly like a right one.

    Transport failures are retried with a long backoff. Anything else -- a 404,
    an empty series, unparseable NAVs -- is permanent and returns immediately.
    """
    import requests
    wait = first_wait
    for attempt in range(1, attempts + 1):
        try:
            return G.fetch_nav_daily(code), None
        except requests.RequestException as exc:
            if attempt == attempts:
                return None, f"network, {attempts} attempts: {exc}"
            log.info("    %s attempt %d/%d failed (%s) -- waiting %.0fs",
                     code, attempt, attempts, type(exc).__name__, wait)
            time.sleep(wait)
            wait *= 3
        except Exception as exc:                        # noqa: BLE001
            return None, f"permanent: {exc}"
    return None, "unreachable"


def _module():
    """Import get_funds_data, which must be importable from the CWD or path."""
    try:
        from swp_planner.funddata import get_funds_data as G
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
    seen_codes, seen_funds = set(), set()
    for _idx, row in master.iterrows():
        name = str(row.get("scheme_name", ""))
        cat = str(row.get("category", ""))
        code = str(row.get("scheme_code", "")).strip()
        if not code or code in seen_codes or not _is_regular_growth(name):
            continue
        sub = _subcategory(cat)
        if sub not in categories:
            continue
        key = _base_fund_key(name)
        if key in seen_funds:
            continue                 # another share class of one already taken
        picked[sub].append((code, name, cat))
        seen_codes.add(code)
        seen_funds.add(key)

    out = {}
    for cat in categories:
        cands = sorted(picked.get(cat, []), key=lambda t: int(t[0]) if
                       t[0].isdigit() else 10 ** 9)
        # Three times what is needed, because the age heuristic is loose and
        # short series get dropped once the history is in hand.
        out[cat] = cands[:per_category * 3]
    return out


def harvest(G, plan, years, min_years, per_category, cache_path, resume,
            pause, attempts, first_wait, failures, rejects, end=None):
    """Fetch daily NAVs fund by fund, writing the cache as each one lands."""
    end = end or datetime.now().date()
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
            df, err = _fetch_with_backoff(G, code, attempts, first_wait)
            if err:
                log.warning("FAILED %s %-44s %s", code, name[:44], err)
                failures.append((cat, code, name, err))
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
            nav_map = {d.date().isoformat(): float(v)
                       for d, v in zip(df["date"], df["nav"])}
            cagr, vol = _annualised(nav_map)
            lo_c, hi_c, hi_v = PLAUSIBLE.get(cat, PLAUSIBLE_DEFAULT)
            if (end - df["date"].iloc[-1].date()).days > DEAD_FUND_DAYS:
                log.warning("DEAD   %s %-42s last priced %s -- wound up or "
                            "merged", code, name[:42],
                            df["date"].iloc[-1].date())
                rejects.append((cat, code, name, None, None))
                continue
            if cagr is None or not (lo_c <= cagr <= hi_c) or (
                    vol is not None and vol > hi_v):
                log.warning("REJECT %s %-42s CAGR %7.1f%% vol %8.1f%% -- "
                            "outside %s", code, name[:42],
                            (cagr or 0) * 100, (vol or 0) * 100, cat)
                rejects.append((cat, code, name, cagr, vol))
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
                "cagr": round(cagr, 6),
                "vol": round(vol, 6) if vol is not None else None,
                "nav": nav_map,
            }
            cache[code] = rec
            ensure_parent(cache_path).write_text(json.dumps(cache))
            kept[cat].append(rec)
            log.info("kept %s %-46s %5.1f yr  %5d days",
                     code, name[:46], rec["years"], rec["trading_days"])
            if pause:
                time.sleep(pause)
    return kept, want_start, end


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(NAV_DIR / "nav_corpus.json"))
    ap.add_argument("--cache", default=str(NAV_DIR / ".nav_corpus_cache.json"))
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
    ap.add_argument("--connect-timeout", type=float, default=20.0,
                    help="seconds to establish the TLS connection. The "
                         "library default of 8 is tuned for fail-fast "
                         "interactive use and loses funds to a busy server.")
    ap.add_argument("--read-timeout", type=float, default=90.0,
                    help="seconds for the body. Twenty years of daily NAVs is "
                         "a large JSON.")
    ap.add_argument("--attempts", type=int, default=4,
                    help="tries per fund on a TRANSPORT failure, with a "
                         "tripling backoff. A 404 or an empty series is "
                         "permanent and is not retried.")
    ap.add_argument("--first-wait", type=float, default=5.0,
                    help="seconds before the second attempt; tripled after")
    ap.add_argument("--allow-shrink", action="store_true",
                    help="write the output even when it would hold fewer "
                         "funds than the cache. Only with --resume, and only "
                         "when dropping categories is deliberate.")
    ap.add_argument("--resume", action="store_true",
                    help="skip anything already cached. Always safe, and the "
                         "right first move after any failure.")
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

    failures, rejects = [], []
    _tune_network(G, a.connect_timeout, a.read_timeout, 2)
    kept, start, end = harvest(G, plan, a.years, a.min_years,
                               a.per_category, Path(a.cache), a.resume,
                               a.pause, a.attempts, a.first_wait, failures,
                               rejects)

    funds = [r for cat in cats for r in kept.get(cat, [])]

    # THE OVERWRITE TRAP. --resume skips re-FETCHING, but the document written
    # at the end is assembled only from the categories named in THIS run. Ask
    # for two categories with --resume and the output silently loses the other
    # nine, which is exactly what happened: a 51-fund corpus became an 8-fund
    # one and read like success. The cache still held everything, so nothing
    # was lost, but only because someone noticed.
    _cached = 0
    if Path(a.cache).exists():
        try:
            _cached = len(json.loads(Path(a.cache).read_text()))
        except (ValueError, OSError):
            _cached = 0
    if _cached > len(funds):
        print(f"\nREFUSING TO WRITE {a.out}: it would hold {len(funds)} funds "
              f"while the cache holds {_cached}.")
        print("--resume skips re-fetching, but the OUTPUT is built only from "
              "the categories named")
        print("in this run, so the others would be dropped. Name every "
              "category you want kept:")
        print(f"  python3 {Path(__file__).name} --out {a.out} --resume "
              f"--per-category {a.per_category} \\")
        print("      --categories \"" + ",".join(DEFAULT_CATEGORIES) + "\"")
        print("Or pass --allow-shrink if dropping them is what you meant.")
        if not a.allow_shrink:
            return 1

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
    ensure_parent(a.out).write_text(json.dumps(doc, indent=1))

    print()
    print(f"{'category':26s}{'kept':>6}{'yrs':>7}{'CAGR':>16}{'vol':>16}")
    print("-" * 74)
    for cat in cats:
        rs = kept.get(cat, [])
        if not rs:
            print(f"{cat:26s}{0:>6}      -               -               -"
                  f"   NONE")
            continue
        ys = [r["years"] for r in rs]
        cs = [r["cagr"] for r in rs]
        vs = [r["vol"] for r in rs if r["vol"] is not None]
        print(f"{cat:26s}{len(rs):>6}{min(ys):>7.1f}"
              f"{min(cs) * 100:>8.1f}%-{max(cs) * 100:.1f}%"
              f"{(min(vs) if vs else 0) * 100:>8.1f}%-"
              f"{(max(vs) if vs else 0) * 100:.1f}%")
    if rejects:
        print(f"\n{len(rejects)} fund(s) REJECTED as implausible. A screen "
              f"this loose is a corruption")
        print("detector, not a quality filter -- anything it catches is bad "
              "data, not a bad fund.")
        for cat, code, name, cagr, vol in rejects[:10]:
            print(f"  {code:>9}  {name[:40]:42s}CAGR {(cagr or 0) * 100:7.1f}%"
                  f"  vol {(vol or 0) * 100:8.1f}%")
    if failures:
        print(f"\n{len(failures)} fund(s) FAILED on the network and were not "
              f"assessed. These are NOT")
        print("funds without the history -- they were never reached. Re-run "
              "with --resume.")
        for cat, code, name, err in failures[:12]:
            print(f"  {cat:16s}{code:>9}  {name[:38]:40s}{err[:28]}")
        if len(failures) > 12:
            print(f"  ... and {len(failures) - 12} more")
    total_days = sum(r["trading_days"] for r in funds)
    print(f"\nwrote {a.out} -- {len(funds)} funds, {total_days} daily NAVs, "
          f"{Path(a.out).stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
