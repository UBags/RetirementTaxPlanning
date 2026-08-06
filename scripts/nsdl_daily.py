#!/usr/bin/env python3
# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""nsdl_daily.py -- the daily job: fetch, clean, categorise, store what moved.

    python3 scripts/nsdl_daily.py --dsn "dbname=RetirementPlanning"

Idempotent. Running it twice in one day fetches once (the workbook is reused
from data/) and writes nothing the second time, because nothing moved between
the two runs. That is the same mechanism that makes the daily job cheap, so
running it twice is a live test of it.

--lookup ISIN and --name TEXT answer a single question and exit without
touching the fetch, which is what you want when checking one holding.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fund_filters import borrower as _borrower  # noqa: E402
from ingest import nsdl_securities as _nsdl  # noqa: E402
from ingest import store as _store  # noqa: E402
from lookup.api import CreditLookup  # noqa: E402

DEFAULT_DSN = "dbname=RetirementPlanning"
DEFAULT_DIR = "data"


def _connect(dsn):
    import psycopg2
    return psycopg2.connect(dsn)


def _fetch(dest_dir, as_on, refetch=False):
    d = pathlib.Path(dest_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"nsdl_active_{as_on.isoformat()}.xlsx"
    if path.exists() and not refetch:
        return path, True
    tmp = path.with_suffix(".xlsx.part")
    _nsdl.fetch(tmp)
    tmp.replace(path)
    return path, False


def _show(res):
    print(f"  issuer     {res['issuer_name']}")
    print(f"  rating     {res['rating']}")
    print(f"  category   {res['borrower_category']}")
    print(f"  source     {res['source']}  confidence {res['confidence']}")
    if res.get("runner_up"):
        print(f"  runner-up  {res['runner_up']}  margin {res['margin']}"
              f"{'   AMBIGUOUS' if res.get('ambiguous') else ''}")
    if res.get("not_cooperating"):
        print("  WARNING    issuer not cooperating with a rating agency")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dsn", default=DEFAULT_DSN)
    ap.add_argument("--dir", default=DEFAULT_DIR)
    ap.add_argument("--file", default=None,
                    help="load an existing workbook and do not fetch")
    ap.add_argument("--refetch", action="store_true")
    ap.add_argument("--schema-only", action="store_true",
                    help="create the schema and exit")
    ap.add_argument("--lookup", metavar="ISIN")
    ap.add_argument("--name", metavar="TEXT")
    a = ap.parse_args(argv)

    conn = _connect(a.dsn)

    if a.lookup or a.name:
        lk = CreditLookup(conn)
        if a.lookup:
            print(f"ISIN {a.lookup}")
            _show(lk.by_isin(a.lookup))
        if a.name:
            print(f"name {a.name!r}")
            _show(lk.by_name(a.name))
        return 0

    _store.apply_schema(conn)
    print("schema applied")
    if a.schema_only:
        return 0

    today = _dt.date.today()
    if a.file:
        path, reused = pathlib.Path(a.file), True
    else:
        path, reused = _fetch(a.dir, today, a.refetch)
    print(f"{'reused' if reused else 'fetched'}   {path} "
          f"({path.stat().st_size:,} bytes)")

    records = _nsdl.parse_workbook(path)
    print(f"parsed    {len(records):,} rows")

    records = _borrower.categorise_all(records)
    for cat, n in _borrower.summarise(records).items():
        print(f"  {cat:<18}{n:>8,}")

    print()
    print(_store.format_report(
        _store.load_daily(conn, records, as_on=today)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
