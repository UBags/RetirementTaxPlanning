#!/usr/bin/env python3
# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""make_fund_details.py -- the input list get_funds_data.py reads.

    python3 scripts/make_fund_details.py --out "Fund Details.csv"

WHY THIS EXISTS. get_funds_data.py does not decide which funds to analyse; it
reads a CSV of them. The metrics file in use covers 754 funds against the 1,392
now stored with holdings, and regenerating it against the same input would
produce fresher numbers for the same 754 -- the input list is the constraint,
not the tool.

THE LIST IS DRAWN FROM india.fund, NOT FROM AMFI. Two reasons. It covers
exactly the funds that have holdings, so every scored fund also has a credit
picture and the ranker's "no metrics" and "no credit" counts both go to zero
for the same set. And it carries amfi_scheme_code from the NAVAll Direct-Growth
row, which is the key fund_rank joins on -- supplying it means the join stays
on the integer instead of falling back to a fuzzy name match, which it did for
26 funds last run.

COLUMNS ARE WHAT get_funds_data EXPECTS AND NOTHING ELSE: Fund Type, Fund Name,
Fund Size, AMFI Code. Fund Size is left blank; that script treats it as
optional and nothing downstream of ours reads it.

IT DOES NOT OVERWRITE SILENTLY. The existing list may have been curated by
hand -- KNOWN_CODE_OVERRIDES exists in get_funds_data precisely because some
funds needed pinning -- so an existing file is backed up beside the new one
rather than replaced.
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import pathlib
import shutil
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

DEFAULT_DSN = "dbname=RetirementPlanning"
COLUMNS = ("Fund Type", "Fund Name", "Fund Size", "AMFI Code")


def rows_from_db(conn, require_holdings=True, require_code=True):
    """One row per fund, in category then name order.

    `require_holdings` keeps the metrics list aligned with what has a credit
    picture; without it the ranker gains funds it can score on performance but
    not on paper, which is the weaker half of the answer.
    """
    where = []
    if require_code:
        where.append("f.amfi_scheme_code IS NOT NULL")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    join = ("JOIN LATERAL (SELECT 1 FROM india.fund_holding h "
            "WHERE h.fund_id = f.fund_id LIMIT 1) x ON TRUE"
            if require_holdings else "")
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT COALESCE(f.category, ''), COALESCE(f.amfi_name, f.label),
                   f.amfi_scheme_code
              FROM india.fund f {join}{clause}
             ORDER BY 1, 2
        """)
        return [dict(zip(COLUMNS, (cat, name, "", code)))
                for cat, name, code in cur.fetchall()]


def write(rows, path, backup=True):
    p = pathlib.Path(path)
    if p.exists() and backup:
        # The existing list may have been curated by hand; get_funds_data has
        # a KNOWN_CODE_OVERRIDES dict precisely because some funds needed
        # pinning, and that history should survive a regeneration.
        stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        keep = p.with_name(f"{p.stem}.{stamp}.bak{p.suffix}")
        shutil.copy2(p, keep)
        print(f"backed up existing list to {keep}")
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(COLUMNS))
        w.writeheader()
        w.writerows(rows)
    return p


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dsn", default=DEFAULT_DSN)
    ap.add_argument("--out", default="Fund Details.csv")
    ap.add_argument("--all-funds", action="store_true",
                    help="include funds with no stored holdings")
    ap.add_argument("--no-backup", action="store_true")
    a = ap.parse_args(argv)

    import psycopg2
    conn = psycopg2.connect(a.dsn)
    rows = rows_from_db(conn, require_holdings=not a.all_funds)
    conn.close()

    if not rows:
        raise SystemExit(
            "no funds found. Run scripts/fund_batch.py first -- this list is "
            "built from what is stored, not from AMFI.")

    path = write(rows, a.out, backup=not a.no_backup)
    cats = {r["Fund Type"] for r in rows if r["Fund Type"]}
    print(f"wrote {len(rows):,} funds in {len(cats)} categories to {path}")
    print("\nNext:")
    print(f'  python3 get_funds_data.py --input "{path}" '
          f'--output Fund_Metrics_Output.csv --workers 8')
    return 0


if __name__ == "__main__":
    sys.exit(main())
