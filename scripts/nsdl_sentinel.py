#!/usr/bin/env python3
# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""nsdl_sentinel.py -- the perpetual flag that is structured, not textual.

I GOT THIS WRONG ONCE AND THE CORRECTION IS THE WHOLE POINT. The seniority
audit reported zero rows with a blank redemption date, and I concluded the
maturity column was populated for every row and therefore carried no signal.
It is populated for every row. The perpetuals carry `31-12-9999`.

That is a SENTINEL, not a maturity, and it is a far better flag than any word:
it is exact, it is emitted by the issuer's own filing rather than by a
description writer, and it cannot be defeated by an abbreviation. Every
PERPETUAL row in the sample carried it -- KLM Axiva, REC, Bank of Baroda,
HDFC Bank, SBI, Arvind Smart Textiles -- across four different description
spellings including PERPT and PERPATUAL, both of which the marker table misses.

The test was wrong, not the idea: I looked for "no date" when I should have
looked for "not a date".

WHAT THIS MEASURES, AND IT ADOPTS NOTHING.

  1. Every redemption year beyond 2100, so a second sentinel does not hide
     behind the first. 31-12-9999 is the one seen; 01-01-2999 or a blank would
     be others, and guessing which are in use is what put this script here.

  2. The sentinel set against the words, four ways: both, sentinel-only,
     words-only, neither. Sentinel-only is what the words MISS -- the number
     that decides whether the text rule should be replaced or merely
     supplemented. Words-only is Tier 2 and sub-debt, which are DATED and
     genuinely subordinated, plus any false positives.

  3. How many flags come from the ISSUER NAME rather than the instrument. The
     description begins with the borrower's name -- "SADBHAV HYBRID ANNUITY
     PROJECTS LIMITED NCD 16JN28 FVRS1LAC" -- so every marker is being matched
     against the company as well as the bond. A bare HYBRID would have fired on
     Sadbhav and on three Adani Hybrid Energy entities, none of which is hybrid
     capital. The guard in the marker table should already prevent that; this
     counts whether it does.
"""
from __future__ import annotations

import argparse
import collections
import datetime as _dt
import pathlib
import re
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from swp_planner.fund_filters import instruments as I  # noqa: E402
from swp_planner.ingest import nsdl_securities as N  # noqa: E402

PERPETUAL_SENTINEL = _dt.date(9999, 12, 31)
SENTINEL_YEAR_FLOOR = 2100      # beyond this, a date is a placeholder

_SUFFIX_RE = re.compile(r"\b(LIMITED|LTD|PRIVATE|PVT|CORPORATION)\b", re.I)


def instrument_tail(rec):
    """The description with the issuer's name removed from the front.

    Exact prefix match first; where the two spellings differ, everything up to
    and including the LAST corporate suffix is dropped. Both are heuristics,
    which is why this script COUNTS the difference rather than applying it.
    """
    desc = rec.get("description") or ""
    issuer = rec.get("issuer") or ""
    if issuer and desc.upper().startswith(issuer.upper()):
        return desc[len(issuer):]
    last = None
    for m in _SUFFIX_RE.finditer(desc):
        last = m
    return desc[last.end():] if last else desc


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("workbook")
    ap.add_argument("--sample", type=int, default=20)
    a = ap.parse_args(argv)

    recs = N.parse_workbook(a.workbook)
    print(f"{len(recs):,} rows\n")

    far = collections.Counter(
        r["redemption"].year for r in recs
        if r["redemption"] and r["redemption"].year > SENTINEL_YEAR_FLOOR)
    print(f"redemption years beyond {SENTINEL_YEAR_FLOOR} "
          f"-- a placeholder, not a maturity")
    for y, n in sorted(far.items()):
        print(f"  {y:<10}{n:>8,}")
    print(f"  {'(no date)':<10}"
          f"{sum(1 for r in recs if r['redemption'] is None):>8,}")

    sent = {r["isin"] for r in recs if r["redemption"] == PERPETUAL_SENTINEL}
    flag = {r["isin"] for r in recs if r["tier"] == I.SUBORDINATED}
    print(f"\n  sentinel 31-12-9999          {len(sent):>8,}")
    print(f"  flagged by the words         {len(flag):>8,}")
    print(f"  both                         {len(sent & flag):>8,}")
    print(f"  sentinel, words MISSED       {len(sent - flag):>8,}  "
          f"<- what replacing the rule would gain")
    print(f"  words only, not sentinel     {len(flag - sent):>8,}  "
          f"<- Tier 2 / sub-debt, and any false positives")

    print("\n-- perpetuals the words missed --")
    for r in [r for r in recs
              if r["isin"] in (sent - flag)][:a.sample]:
        print(f"  {r['isin']}  {(r['description'] or '')[:74]}")

    print("\n-- flagged but not sentinel: dated subordination, or wrong --")
    for r in [r for r in recs if r["isin"] in (flag - sent)][:a.sample]:
        print(f"  {r['isin']}  {(r['description'] or '')[:74]}")

    name_only = [
        r for r in recs
        if I.subordinated_text(
            f"{r.get('instrument_type') or ''} {r.get('description') or ''}")
        and not I.subordinated_text(
            f"{r.get('instrument_type') or ''} {instrument_tail(r)}")]
    print(f"\nflags coming from the ISSUER NAME rather than the instrument: "
          f"{len(name_only):,}")
    for r in name_only[:a.sample]:
        print(f"  {r['isin']}  {(r['description'] or '')[:74]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
