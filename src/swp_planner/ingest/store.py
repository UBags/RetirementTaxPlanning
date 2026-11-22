# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""store.py -- write the daily pull, but only where something moved.

THE RULE, STATED ONCE. For each ISIN, compare the incoming record against the
LATEST row already stored. If the watched fields are identical, write nothing.
Otherwise insert one row dated today. An ISIN never seen before is always an
insert, dated today, with first_seen_date set to today.

WHAT IS WATCHED, AND WHY IT IS NOT EVERYTHING. WATCHED_FIELDS is the rating,
the borrower category and the issuer key. Coupon, face value and issue size are
stored but NOT watched: they are static for the life of an instrument, and if
NSDL ever restates one, recording a rating-history row for it would put a fake
event in a history whose entire purpose is rating events. Add a field here and
it starts creating history; that is the only thing this constant does.

THE COMPARISON IS DONE IN PYTHON, DELIBERATELY. Twenty-eight thousand rows is
one SELECT and a dict, and doing it in the database would need either 28,745
round trips or a temp table and a join whose semantics are harder to read than
this loop. The cost of the whole operation is one query and one execute_values.

TRANSACTIONAL. Either the whole day lands or none of it does. A partial day is
worse than no day, because the next run would compare against a half-written
picture and conclude nothing had changed.
"""
from __future__ import annotations

import datetime as _dt
import pathlib

from swp_planner.paths import DB_DIR

# The fields whose movement creates a new historical row. See the module
# docstring before adding to this.
WATCHED_FIELDS = ("raw_rating", "rating_grade", "borrower_category",
                  "issuer_key")

SCHEMA_FILE = DB_DIR / "india_debt_schema.sql"

TABLE = "india.debt_security"

_INSERT_COLUMNS = (
    "isin", "last_updated_date", "issuer_name", "issuer_key", "description",
    "instrument_type", "tier", "borrower_category", "sector", "ownership",
    "raw_rating", "rating_grade", "rating_rank", "enhanced",
    "not_cooperating", "coupon", "face_value", "issue_size", "allotment",
    "redemption", "status", "first_seen_date", "source",
)


def apply_schema(conn, path=None):
    """Create the schema, table, indexes and views. Safe to re-run."""
    sql = pathlib.Path(path or SCHEMA_FILE).read_text()
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()


def _row(rec):
    """One record flattened to the column order above, minus the dates that
    the caller supplies."""
    return dict(
        isin=(rec.get("isin") or "").strip().upper(),
        issuer_name=rec.get("issuer") or "",
        issuer_key=rec.get("issuer_key") or "",
        description=rec.get("description") or None,
        instrument_type=rec.get("instrument_type") or None,
        tier=rec.get("tier"),
        borrower_category=rec.get("borrower_category"),
        sector=rec.get("sector") or None,
        ownership=rec.get("ownership") or None,
        raw_rating=rec.get("raw_rating") or None,
        rating_grade=rec.get("worst_grade"),
        rating_rank=rec.get("worst_rank"),
        enhanced=bool(rec.get("enhanced")),
        not_cooperating=bool(rec.get("not_cooperating")),
        coupon=rec.get("coupon"),
        face_value=rec.get("face_value"),
        issue_size=rec.get("issue_size"),
        allotment=rec.get("allotment"),
        redemption=rec.get("redemption"),
        status=rec.get("status") or None,
    )


def fingerprint(row):
    """What must differ for a new historical row to be written."""
    return tuple(row.get(f) for f in WATCHED_FIELDS)


def current_state(conn):
    """{isin: (fingerprint, first_seen_date)} for every ISIN already stored.

    One query. The view already reduces to the latest row per ISIN.
    """
    cols = ", ".join(WATCHED_FIELDS)
    with conn.cursor() as cur:
        cur.execute(f"SELECT isin, {cols}, first_seen_date "
                    f"FROM india.debt_security_current")
        return {r[0].strip(): (tuple(r[1:-1]), r[-1]) for r in cur.fetchall()}


def load_daily(conn, records, as_on=None, source="nsdl_active", dedupe=True):
    """Insert only what moved. Returns a report dict.

    `records` must already carry `borrower_category` -- call
    `fund_filters.borrower.categorise_all` first. Passing uncategorised records
    would store NULL in a NOT NULL column, which is the right place to fail.

    `dedupe` keeps the FIRST row for a repeated ISIN and counts the rest. The
    NSDL file has repeated ISINs, and (isin, last_updated_date) is the primary
    key, so a repeat inside one day is a constraint violation rather than a
    second row. Dropping silently would be wrong, so the count is reported.
    """
    from psycopg2.extras import execute_values

    as_on = as_on or _dt.date.today()
    state = current_state(conn)

    seen, duplicates = set(), []
    inserted, unchanged, new_isins = [], 0, 0

    for rec in records:
        row = _row(rec)
        isin = row["isin"]
        if not isin:
            continue
        if dedupe and isin in seen:
            duplicates.append(isin)
            continue
        seen.add(isin)

        prior = state.get(isin)
        if prior is not None and prior[0] == fingerprint(row):
            unchanged += 1
            continue
        if prior is None:
            new_isins += 1
        row["last_updated_date"] = as_on
        row["first_seen_date"] = prior[1] if prior else as_on
        row["source"] = source
        inserted.append(tuple(row[c] for c in _INSERT_COLUMNS))

    if inserted:
        with conn.cursor() as cur:
            execute_values(
                cur,
                f"INSERT INTO {TABLE} ({', '.join(_INSERT_COLUMNS)}) "
                f"VALUES %s ON CONFLICT (isin, last_updated_date) "
                f"DO NOTHING",
                inserted)
    conn.commit()

    return dict(as_on=as_on, seen=len(seen), inserted=len(inserted),
                unchanged=unchanged, new_isins=new_isins,
                changed=len(inserted) - new_isins,
                duplicate_isins=len(duplicates),
                duplicate_examples=sorted(set(duplicates))[:10])


def format_report(rep):
    lines = [
        f"daily load {rep['as_on']}",
        f"  distinct ISINs seen   {rep['seen']:>8,}",
        f"  new instruments       {rep['new_isins']:>8,}",
        f"  rating/category moved {rep['changed']:>8,}",
        f"  unchanged, not written{rep['unchanged']:>8,}",
        f"  rows inserted         {rep['inserted']:>8,}",
    ]
    if rep["duplicate_isins"]:
        lines.append(f"  repeated ISINs in file{rep['duplicate_isins']:>8,}  "
                     f"first kept: {', '.join(rep['duplicate_examples'][:5])}")
    return "\n".join(lines)
