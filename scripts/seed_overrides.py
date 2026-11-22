#!/usr/bin/env python3
# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""seed_overrides.py -- issuer ratings supplied by hand, in one place.

    python3 scripts/seed_overrides.py --by uddipan
    python3 scripts/seed_overrides.py --by uddipan --dry-run
    python3 scripts/seed_overrides.py --list

WHY THESE ARE OVERRIDES AND NOT GROUPS. A group-implied rating INFERS from a
rated member of the same group. Barclays and Titan are already in the group
registry and it did not help them: neither group has an entity in NSDL, so
there is nothing to infer from. What is supplied below is the rating itself,
which is a different mechanism -- and the better one, because an override
carries a source, a date and an expiry, and one entry clears every fund
holding that name.

LONG-TERM GRADES ARE ENTERED, NOT SHORT-TERM ONES. `reapply_overrides`
converts to A1+/A1/A2+ on certificates of deposit and commercial paper using
the same agency linkage the automatic path uses, so an issuer holding both
kinds of paper gets the right grade on each from a single entry. Entering A1+
here would put A1+ on that issuer's debentures too.

EVERY ENTRY EXPIRES. A rating supplied in August is not evidence about the
following August, and india.stale_rating_override lists what has gone past its
review date. Re-running this script refreshes them.

ONE ENTRY COVERS EVERY SPELLING. Overrides are matched on the NORMALISED name,
so "TVS Motor Company Ltd" also answers "TVS Motor Company Ltd - Pref. Shares"
and "Axis Bank Ltd." also answers "Axis Bank Ltd. Warrant". Entering the plain
company name is therefore right; entering each variant is not needed and would
be a queue that never shrinks.

THE SOURCE NOTE IS THE POINT. A rating with no provenance cannot be checked
later and will be re-entered by someone else. `set_override` refuses an empty
one, and the notes below say where each came from.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ingest import fund_store as F  # noqa: E402

DEFAULT_DSN = "dbname=RetirementPlanning"

# (issuer as the source writes it, long-term grade, borrower category, note)
#
# Grades are as supplied on 2026-08-07 and are LONG-TERM. The securitisation
# trusts are rated on the pool, not on the originator, which is why they need
# a person at all -- there is no issuer to look up.
SEEDS = (
    ("Karur Vysya Bank Ltd.", "AAA", "Pvt Bank",
     "user-supplied 2026-08-07"),
    ("Barclays Investments & Loans Ltd.", "AAA", "NBFC",
     "user-supplied 2026-08-07"),
    ("Toyota Financial Services Ltd.", "AAA", "NBFC",
     "user-supplied 2026-08-07"),
    ("Citi Corp Finance Ltd", "AAA", "NBFC",
     "user-supplied 2026-08-07"),
    ("Titan Company Ltd.", "AAA", "Large Corporate",
     "user-supplied 2026-08-07"),
    ("Mirae Asset Financial Services (India) Pvt Ltd.", "AA", "NBFC",
     "user-supplied 2026-08-07"),
    ("Siddhivinayak Securitisation Trust", "AAA", "Other Corporate",
     "user-supplied 2026-08-07; rated on the pool"),
    ("Shivshakti Securitisation Trust", "AAA", "Other Corporate",
     "user-supplied 2026-08-07; rated on the pool"),
    ("Radhakrishna Securitisation Trust", "AAA", "Other Corporate",
     "user-supplied 2026-08-07; rated on the pool"),

    # Foreign bank branches and their Indian NBFC arms. None is in NSDL: they
    # fund through CP and CDs, not listed bonds.
    ("HSBC InvestDirect Financial Services India Ltd.", "AAA", "NBFC",
     "user-supplied 2026-08-08"),
    ("DBS Bank Ltd.", "AAA", "Pvt Bank", "user-supplied 2026-08-08"),
    ("Development Bank of Singapore", "AAA", "Pvt Bank",
     "user-supplied 2026-08-08"),
    ("Julius Baer Capital India Pvt Ltd.", "AAA", "NBFC",
     "user-supplied 2026-08-08"),

    # State-owned. PGCIL and NHAI are already exempt from the concentration
    # cap as quasi-sovereign; that is the CAP, and this is the RATING, which
    # is a separate fact and was still missing.
    ("Power Grid Corporation of India Ltd.", "AAA", "Large Corporate",
     "user-supplied 2026-08-08"),
    ("National Highways Infra Trust", "AAA", "Large Corporate",
     "user-supplied 2026-08-08"),

    ("Bharat Heavy Electricals Ltd.", "AA", "Large Corporate",
     "user-supplied 2026-08-08"),
    ("TVS Motor Company Ltd", "AA", "Large Corporate",
     "user-supplied 2026-08-08"),
)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dsn", default=DEFAULT_DSN)
    ap.add_argument("--by", default=None,
                    help="who is vouching for these; recorded per entry")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--list", action="store_true",
                    help="print what would be seeded and exit")
    ap.add_argument("--no-reapply", action="store_true",
                    help="record the overrides but do not fill holdings")
    a = ap.parse_args(argv)

    if a.list or a.dry_run:
        print(f"{'issuer':<50}{'grade':<7}{'category':<18}note")
        for name, grade, cat, note in SEEDS:
            print(f"{name[:48]:<50}{grade:<7}{cat:<18}{note}")
        if a.list:
            return 0

    if not a.by:
        raise SystemExit(
            "give --by: an override records who vouched for it, and 'manual' "
            "six months from now tells nobody anything")

    import psycopg2
    conn = psycopg2.connect(a.dsn)
    F.apply_schema(conn)

    if a.dry_run:
        print("\nnothing written (--dry-run).")
        return 0

    for name, grade, cat, note in SEEDS:
        F.set_override(conn, name, rating=grade, borrower_category=cat,
                       source_note=note, entered_by=a.by)
    print(f"recorded {len(SEEDS)} overrides")

    if not a.no_reapply:
        n = F.reapply_overrides(conn)
        print(f"filled {n:,} holdings")
        q = F.pending_reviews(conn, limit=10)
        print(f"\n{len(q)} names still need a person (top 10):")
        for r in q:
            print(f"  {r['largest_percent']:>8}  {r['funds_affected']:>4} "
                  f"funds  {r['gap']:<13}{r['company']}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
