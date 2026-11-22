#!/usr/bin/env python3
# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""fund_load.py -- fetch a fund, resolve its credit, store all of it.

    python3 scripts/fund_load.py "Bajaj Finserv Banking and PSU Fund"
    python3 scripts/fund_load.py --file data/etmoney/portfolio_44228.html
    python3 scripts/fund_load.py --queue          # what needs a person
    python3 scripts/fund_load.py --override "India Universal Trust AL1" \
        --rating AA --note "CRISIL website 2026-08-07" --by uddipan

SAFE TO RUN TWICE A WEEK. Month-end holdings change once a month, and the
snapshot hash means the other seven fetches compare and stop. Only months whose
content actually differs are rewritten, and a month is rewritten whole.

WHAT GETS STORED, per (fund, month):
    holdings, with rating, band, borrower category and why anything is missing
    issuer exposure, summed across instrument lines, with cap breaches marked
    asset allocation, with the tax bucket it implies
    rating bands twice -- ours and ETMoney's -- so the two can be compared in
    SQL across the whole universe rather than one fund at a time
"""
from __future__ import annotations

import argparse
import datetime as _dt
import gzip
import pathlib
import sys
import urllib.request

_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from swp_planner.ingest import etmoney as E  # noqa: E402
from swp_planner.ingest import etmoney_portfolio as P  # noqa: E402
from swp_planner.ingest import fund_store as F  # noqa: E402
from swp_planner.ingest import store as S  # noqa: E402
from swp_planner.lookup import portfolio_credit as PC  # noqa: E402
from swp_planner.lookup.api import CreditLookup  # noqa: E402

DEFAULT_DSN = "dbname=RetirementPlanning"


def fetch_html(url, timeout=45):
    req = urllib.request.Request(url, headers={
        "accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "accept-encoding": "gzip", "user-agent": E.HEADERS["user-agent"]})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", errors="replace")


def load_fund(conn, payload, fund_id, label, lookup, months=None,
              amfi_name=None, seo_web_url=None, score=None, today=None,
              category=None, scheme_type=None, amc=None,
              amfi_scheme_code=None):
    """Every month of one fund. Returns a per-month verdict."""
    today = today or _dt.date.today()
    F.upsert_fund(conn, fund_id, label, amfi_name=amfi_name,
                  seo_web_url=seo_web_url, match_score=score, as_on=today,
                  category=category, scheme_type=scheme_type, amc=amc,
                  amfi_scheme_code=amfi_scheme_code)
    overrides = F.overrides(conn)

    available = sorted(payload.get("mfHoldingMap") or {})
    wanted = [m for m in available if not months or m in months]
    et_bands = F.etmoney_band_allocation(P.rating_buckets(payload))

    out = {}
    for month in wanted:
        rows = PC.enrich(
            P.holdings(payload, as_on=month, fund_category=category),
            lookup)
        rows = F.apply_overrides(rows, overrides)
        as_on = rows[0]["as_on"] if rows else _dt.date.fromisoformat(month)
        verdict = F.load_month(conn, fund_id, as_on, rows, today=today)
        F.load_asset_allocation(conn, fund_id, as_on,
                                P.allocation_of(rows), today=today)
        F.load_rating_allocation(conn, fund_id, as_on, F.band_allocation(rows),
                                 "derived", today=today)
        # Concentration is stored, not recomputed at read time: "which funds
        # breach 3.25% on one borrower" is asked across 2,347 funds and 25
        # months each, and the cap that was applied is a fact about the run.
        F.load_issuer_exposures(conn, fund_id, as_on, rows, today=today)
        # ETMoney publishes ONE rating split, for the latest month only, so it
        # is stored against that month alone rather than repeated backwards as
        # though it had been published each time.
        if month == available[-1] and et_bands:
            F.load_rating_allocation(conn, fund_id, as_on, et_bands,
                                     "etmoney", today=today)
        out[month] = verdict
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("name", nargs="?")
    ap.add_argument("--file")
    ap.add_argument("--dsn", default=DEFAULT_DSN)
    ap.add_argument("--latest-only", action="store_true",
                    help="store only the newest month")
    ap.add_argument("--queue", action="store_true",
                    help="print what needs a person, and exit")
    ap.add_argument("--override", metavar="NAME")
    ap.add_argument("--rating", default=None)
    ap.add_argument("--category", default=None)
    ap.add_argument("--note", default="")
    ap.add_argument("--by", default="manual")
    ap.add_argument("--unratable", action="store_true")
    a = ap.parse_args(argv)

    import psycopg2
    conn = psycopg2.connect(a.dsn)
    S.apply_schema(conn)
    F.apply_schema(conn)

    if a.override:
        F.set_override(conn, a.override, rating=a.rating,
                       borrower_category=a.category, source_note=a.note,
                       entered_by=a.by, unratable=a.unratable)
        print(f"recorded {a.override!r} -> {a.rating or 'unratable'}")
        return 0

    if a.queue:
        q = F.pending_reviews(conn)
        print(f"{len(q)} names need a person, largest exposure first\n")
        print(f"{'largest%':>9} {'funds':>6}  {'why':<13}name")
        for r in q:
            print(f"{r['largest_percent']:>9} {r['funds_affected']:>6}  "
                  f"{r['gap']:<13}{r['company']}")
        return 0

    if not a.name and not a.file:
        raise SystemExit("give a fund name, or --file, or --queue")

    if a.file:
        html = pathlib.Path(a.file).read_text(errors="replace")
        fund_id = int(pathlib.Path(a.file).stem.split("_")[-1])
        label, seo, score = f"fund {fund_id}", None, None
        print(f"reading   {a.file}")
    else:
        m = E.resolve(a.name)
        if not m["found"]:
            raise SystemExit(f"could not resolve {a.name!r}: {m['reason']}")
        fund_id, label = m["fund_id"], m["label"]
        seo, score = m["seo_web_url"], m["score"]
        print(f"resolved  {label}  id {fund_id}  score {score}")
        html = fetch_html(m["portfolio_url"])

    payload = P.extract_payload(html)
    lookup = CreditLookup(conn)
    months = [max(payload["mfHoldingMap"])] if a.latest_only else None
    verdicts = load_fund(conn, payload, fund_id, label, lookup, months=months,
                         amfi_name=a.name, seo_web_url=seo, score=score)

    counts = {}
    for v in verdicts.values():
        counts[v] = counts.get(v, 0) + 1
    print(f"stored    {len(verdicts)} months: "
          + ", ".join(f"{n} {k}" for k, n in sorted(counts.items())))
    print()
    print(PC.report(payload, lookup))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
