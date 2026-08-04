# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""extract_nav_sample.py

Pull a small, wide, self-describing CSV out of nav_corpus.json -- a handful of
real funds with twenty years of daily NAVs, small enough to hand around and to
commit.

WHY WIDE AND NOT LONG

  Long format -- one row per fund per day -- repeats the scheme name fifty
  thousand times and comes to several megabytes. Wide is one row per DATE and
  one column per fund, which for ten funds over twenty years is about 700KB and
  loads with a single read_csv.

  The column header carries the identity, so the file needs no companion:

      date,102885|Liquid|Aditya Birla Sun Life Liquid Fund,...

  code | category | scheme name, pipe-separated. Split on "|" to recover all
  three; nothing else in an AMFI scheme name uses a pipe.

WHAT IT DOES ABOUT MISSING DAYS

  Funds do not share a calendar. A liquid fund prices on days an equity fund
  does not, schemes suspend, feeds drop days. So:

    - the date axis is the UNION of every selected fund's dates
    - within a fund's own span, a gap is FORWARD-FILLED, because a missing
      NAV means "no new price published", not "worthless"
    - OUTSIDE a fund's span the cell is EMPTY, never filled, because a fund
      that did not exist must not read as a flat line. A model that treats a
      pre-launch blank as a real price would show a fund with zero volatility
      for years and rate it the safest thing in the corpus.

  The summary prints how many cells were filled per fund. A fund needing a lot
  of filling is one to be suspicious of.

USAGE

    python3 extract_nav_sample.py --corpus nav_corpus.json --out nav_sample.csv
    python3 extract_nav_sample.py --spec "Liquid:3,Overnight:3,Large Cap:2,Mid Cap:2"
    python3 extract_nav_sample.py --list

  The default spec is three liquid, three overnight, two large cap and two mid
  cap: ten funds, enough to give the model a real debt ladder and a real equity
  sleeve without the file becoming unwieldy.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

DEFAULT_SPEC = "Liquid:3,Overnight:3,Large Cap:2,Mid Cap:2"


def _parse_spec(text):
    out = []
    for part in text.split(","):
        if ":" not in part:
            sys.exit(f"FATAL: --spec wants CATEGORY:N, got {part!r}")
        cat, n = part.rsplit(":", 1)
        try:
            out.append((cat.strip(), int(n)))
        except ValueError:
            sys.exit(f"FATAL: --spec wants CATEGORY:N, got {part!r}")
    return out


def _shortname(name):
    """Trim the plan and option suffix; the corpus is all Regular Growth."""
    for cut in (" - Regular Plan", " - Regular", "-Regular", " - Growth",
                "-Growth", " - REGULAR PLAN"):
        i = name.upper().find(cut.upper())
        if i > 0:
            name = name[:i]
    return " ".join(name.split())[:56]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus", default="nav_corpus.json")
    ap.add_argument("--out", default="nav_sample.csv")
    ap.add_argument("--spec", default=DEFAULT_SPEC)
    ap.add_argument("--list", action="store_true",
                    help="print what the corpus holds and stop")
    a = ap.parse_args()

    path = Path(a.corpus)
    if not path.exists():
        sys.exit(f"FATAL: {path} not found. Run build_nav_corpus.py first.")
    doc = json.loads(path.read_text())
    funds = doc["funds"]

    by_cat = defaultdict(list)
    for f in funds:
        by_cat[f["category_bucket"]].append(f)
    for cat in by_cat:
        by_cat[cat].sort(key=lambda f: -f["years"])

    if a.list:
        print(f"{'category':20s}{'funds':>7}{'longest':>9}{'shortest':>10}")
        print("-" * 48)
        for cat, fs in sorted(by_cat.items()):
            ys = [f["years"] for f in fs]
            print(f"{cat:20s}{len(fs):>7}{max(ys):>9.1f}{min(ys):>10.1f}")
        return 0

    chosen = []
    for cat, want in _parse_spec(a.spec):
        have = by_cat.get(cat, [])
        if len(have) < want:
            print(f"WARNING: {cat} has {len(have)} funds, {want} asked for")
        chosen.extend(have[:want])
    if not chosen:
        sys.exit("FATAL: the spec selected nothing. Try --list.")

    # union of every date, as a dense daily axis so a consumer can index by
    # date without a calendar
    all_dates = set()
    for f in chosen:
        all_dates.update(f["nav"].keys())
    lo = min(all_dates)
    hi = max(all_dates)
    d0 = date.fromisoformat(lo)
    d1 = date.fromisoformat(hi)
    axis = []
    d = d0
    while d <= d1:
        axis.append(d.isoformat())
        d += timedelta(days=1)

    cols, filled, header = [], [], ["date"]
    for f in chosen:
        nav = f["nav"]
        first, last = f["first_date"], f["last_date"]
        series, carried, last_val = [], 0, None
        for iso in axis:
            if iso < first or iso > last:
                series.append("")            # outside the fund's life: EMPTY
                continue
            if iso in nav:
                last_val = nav[iso]
            elif last_val is not None:
                carried += 1
            series.append(f"{last_val:.4f}" if last_val is not None else "")
        cols.append(series)
        filled.append(carried)
        header.append(f"{f['amfi_code']}|{f['category_bucket']}|"
                      f"{_shortname(f['scheme_name'])}")

    with open(a.out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for i, iso in enumerate(axis):
            w.writerow([iso] + [c[i] for c in cols])

    print(f"{'fund':58s}{'years':>7}{'days':>8}{'filled':>8}")
    print("-" * 82)
    for f, carr in zip(chosen, filled):
        print(f"{_shortname(f['scheme_name']):58s}{f['years']:>7.1f}"
              f"{f['trading_days']:>8}{carr:>8}")
    print(f"\nwrote {a.out} -- {len(axis)} dates, {len(chosen)} funds, "
          f"{lo} to {hi}, {Path(a.out).stat().st_size / 1e6:.2f} MB")
    print("Header is code|category|name. Blank means the fund did not exist "
          "on that date;")
    print("a repeated value means no new NAV was published that day.")
    return 0


if __name__ == "__main__":
    sys.exit(main())