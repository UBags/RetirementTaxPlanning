# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""fund_store.py -- persist what a fund holds, and what it is worth.

SNAPSHOT PER MONTH, NOT A CHANGE LOG, WHICH IS THE OPPOSITE OF ingest/store.py.
The two sources behave differently and copying that pattern here would be
wrong. NSDL publishes a CURRENT picture, so history has to be constructed by
recording each change. ETMoney publishes twenty-five months of month-end
holdings in one page, so history is RECEIVED and the key is (fund, month).

WHICH IS ALSO HOW EXTINGUISHING IS RECORDED: a holding sold has no row in the
following month. No delete, no tombstone, no is_active flag to keep consistent.

RESTATEMENTS ARE THE CASE THAT NEEDS WORK. ETMoney can republish a past month
with different numbers. Each (fund, month) carries a content hash of its rows,
and when that differs the month is deleted and reinserted AS A UNIT -- because
a half-updated month sums to something other than 100%, and a portfolio that
does not sum to 100% is worse than one that is a week out of date.

TWICE-WEEKLY FETCHES WRITE ALMOST NOTHING. Month-end holdings change once a
month; eight fetches produce one write, and the other seven compare a hash and
stop.

THE RATING BANDS ARE THREE, AND THE MAPPING IS THE PART TO ARGUE WITH. AAA and
its equivalents, AA and its equivalents, everything else. The short-term scale
is mapped onto the long-term one because a fund holds both and a portfolio
split that keeps them apart cannot be summed -- A1+ sits with AAA, A1 with AA.
That is a convention, not an identity: A1+ says an issuer will meet a
twelve-month obligation and AAA says it will meet a ten-year one, and an issuer
can hold the first while losing the second.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import pathlib

from fund_filters import ratings as _ratings

SCHEMA_FILE = pathlib.Path(__file__).resolve().parent.parent / "db" \
    / "india_fund_schema.sql"

BAND_AAA = "AAA"
BAND_AA = "AA"
BAND_OTHER = "Other"
BAND_UNRATED = "Unrated"

# Grades outside the two agency scales that still have to land somewhere.
# SOV is government paper: no credit risk to speak of, so it sits with AAA and
# is named here rather than silently falling through to Other.
_EXTRA_AAA = {"SOV", "SOVEREIGN", "A1+", "AAA(CE)", "AAA (CE)", "AAA(SO)"}
_EXTRA_AA = {"A1", "AA(CE)", "AA (CE)"}

# Buckets ETMoney publishes that are not ratings at all.
_NOT_A_RATING = {"CASH & CALL MONEY", "CASH AND CALL MONEY", "TERM DEPOSITS",
                 "OTHERS", "NET CURRENT ASSETS", "UNRATED"}

# The statutory thresholds. See db/india_fund_schema.sql for what each bucket
# means; these are the numbers, in one place.
EQUITY_BUCKET_FLOOR = 65.0
DEBT_BUCKET_FLOOR = 35.0

TAX_EQUITY = "equity"
TAX_OLD_DEBT = "old_debt"
TAX_SPECIFIED_DEBT = "specified_debt_50AA"

# How long a hand-entered rating is believed. A NAMED CHOICE: a rating looked
# up by hand in August is not evidence about the following August.
OVERRIDE_REVIEW_DAYS = 365


def apply_schema(conn, path=None):
    with conn.cursor() as cur:
        cur.execute(pathlib.Path(path or SCHEMA_FILE).read_text())
    conn.commit()


def known_grades():
    """Every grade the bander recognises, long scale first then short.

    Offered to a UI so a person picks from a list rather than typing, because
    `rating_band` returns Other for anything it does not recognise -- so a
    typo like "AAa" or "AA1" would be stored, banded as Other, and look like a
    deliberate answer forever after.
    """
    return (list(_ratings.LONG_TERM_SCALE)
            + [g for g in _ratings.SHORT_TERM_SCALE
               if g not in _ratings.RANK]
            + ["SOV"])


def valid_rating(grade):
    """Is this a grade the bander actually understands?"""
    if grade is None or str(grade).strip() == "":
        return True          # absence is valid; it means unrated
    g = " ".join(str(grade).upper().split())
    return (g in _ratings.RANK or g in _ratings.SHORT_RANK
            or g in _EXTRA_AAA or g in _EXTRA_AA)


def rating_band(grade):
    """AAA / AA / Other / Unrated, from a grade on either agency scale.

    Returns BAND_UNRATED for anything that is not a rating -- "Cash & Call
    Money" is a holding type, not a credit opinion, and banding it as Other
    would put cash in the same bucket as a BBB borrower.
    """
    if not grade:
        return BAND_UNRATED
    g = " ".join(str(grade).upper().split())
    if g in _NOT_A_RATING:
        return BAND_UNRATED
    if g in _EXTRA_AAA:
        return BAND_AAA
    if g in _EXTRA_AA:
        return BAND_AA
    if g in _ratings.RANK:
        return BAND_AAA if _ratings.RANK[g] <= _ratings.RANK["AAA"] else (
            BAND_AA if _ratings.RANK[g] <= _ratings.RANK["AA-"]
            else BAND_OTHER)
    if g in _ratings.SHORT_RANK:
        return BAND_AA if _ratings.SHORT_RANK[g] <= _ratings.SHORT_RANK["A1"] \
            else BAND_OTHER
    return BAND_OTHER


def tax_bucket(equity_percent):
    """Which of the three tax treatments a fund falls into.

    THE NUMBER IS AN UPPER BOUND. The statutory test is on DOMESTIC equity and
    the source does not separate overseas holdings, so a fund reading 66% here
    could be below the line in fact. Near the boundary this is a prompt to
    check, not an answer.
    """
    e = float(equity_percent or 0.0)
    if e >= EQUITY_BUCKET_FLOOR:
        return TAX_EQUITY
    if e >= DEBT_BUCKET_FLOOR:
        return TAX_OLD_DEBT
    return TAX_SPECIFIED_DEBT


def snapshot_hash(rows):
    """A hash over one month's holdings, stable against row order.

    Only the fields that would make a restatement meaningful are included.
    `last_updated_date` is not, or every load would look like a change.
    """
    payload = sorted(
        (r.get("company_id"), r.get("instrument_code"), r.get("line_no"),
         None if r.get("percent") is None else round(float(r["percent"]), 4),
         None if r.get("value") is None else round(float(r["value"]), 2),
         r.get("company"), r.get("instrument_type"), r.get("asset_class"))
        for r in rows)
    return hashlib.sha256(
        json.dumps(payload, default=str).encode()).hexdigest()


def upsert_fund(conn, fund_id, label, amfi_name=None, seo_web_url=None,
                match_score=None, as_on=None, category=None,
                scheme_type=None, amc=None, amfi_scheme_code=None):
    """Register the fund. COALESCE throughout, so a later call that knows less
    than an earlier one does not erase what it knew -- `--file` loads have no
    AMFI name or category and must not blank them."""
    as_on = as_on or _dt.date.today()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO india.fund (fund_id, label, amfi_name, seo_web_url,"
            " match_score, category, scheme_type, amc, amfi_scheme_code,"
            " first_seen_date, last_fetched_date) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (fund_id) DO UPDATE SET "
            "  label = EXCLUDED.label,"
            "  amfi_name = COALESCE(EXCLUDED.amfi_name, india.fund.amfi_name),"
            "  seo_web_url = COALESCE(EXCLUDED.seo_web_url,"
            "                         india.fund.seo_web_url),"
            "  match_score = COALESCE(EXCLUDED.match_score,"
            "                         india.fund.match_score),"
            "  category = COALESCE(EXCLUDED.category, india.fund.category),"
            "  scheme_type = COALESCE(EXCLUDED.scheme_type,"
            "                         india.fund.scheme_type),"
            "  amc = COALESCE(EXCLUDED.amc, india.fund.amc),"
            "  amfi_scheme_code = COALESCE(EXCLUDED.amfi_scheme_code,"
            "                              india.fund.amfi_scheme_code),"
            "  last_fetched_date = EXCLUDED.last_fetched_date",
            (fund_id, label, amfi_name, seo_web_url, match_score, category,
             scheme_type, amc, amfi_scheme_code, as_on, as_on))
    conn.commit()


def assign_line_numbers(rows):
    """An ordinal per (company, instrument) within a month.

    ONE ISSUER CAN HOLD TWO LINES OF THE SAME INSTRUMENT TYPE. HUDCO is in
    July 2026 twice under Debenture, at 6.74% and 2.78% -- two different bonds
    that ETMoney separates only by its own row id. Without an ordinal the
    second insert violates the primary key, which is how this was found: on
    real data, not in review.

    Assigned from the row CONTENTS, sorted, rather than from arrival order, so
    the same month reloaded produces the same keys and the snapshot hash stays
    comparable. ETMoney's own row id is stored alongside but not used as the
    key: it is a database identifier on their side and nothing promises it
    survives a restatement.
    """
    groups = {}
    out = []
    for r in sorted(rows, key=lambda h: (
            h.get("company_id") or 0, h.get("instrument_code") or 0,
            -(float(h.get("percent") or 0.0)), str(h.get("source_id") or ""))):
        k = (r.get("company_id"), r.get("instrument_code"))
        n = groups.get(k, 0)
        groups[k] = n + 1
        d = dict(r)
        d["line_no"] = n
        out.append(d)
    return out


_HOLDING_COLUMNS = (
    "fund_id", "as_on", "company_id", "instrument_code", "line_no",
    "source_id", "rating_source", "company", "sector",
    "instrument_type", "asset_class", "percent", "value", "is_issuer",
    "expected_in_nsdl", "is_securitised", "seniority", "rating",
    "rating_band", "rating_confidence", "borrower_category", "gap",
    "last_updated_date")


def _holding_row(fund_id, as_on, h, today):
    return (fund_id, as_on, h.get("company_id"), h.get("instrument_code"),
            h.get("line_no", 0), h.get("source_id"), h.get("rating_source"),
            h.get("company"), h.get("sector"), h.get("instrument_type"),
            h.get("asset_class") or "other", h.get("percent"), h.get("value"),
            bool(h.get("is_issuer", True)),
            bool(h.get("expected_in_nsdl", False)),
            bool(h.get("is_securitised", False)), h.get("seniority"),
            h.get("rating"), rating_band(h.get("rating")),
            h.get("rating_confidence"), h.get("borrower_category"),
            h.get("gap"), today)


def load_month(conn, fund_id, as_on, rows, today=None):
    """One (fund, month) snapshot. Replaces the month whole, or writes nothing.

    Returns 'inserted' | 'restated' | 'unchanged'.
    """
    from psycopg2.extras import execute_values

    today = today or _dt.date.today()
    rows = assign_line_numbers(rows)
    digest = snapshot_hash(rows)
    total = round(sum(float(r.get("percent") or 0.0) for r in rows), 4)

    with conn.cursor() as cur:
        cur.execute("SELECT content_hash, restatements FROM "
                    "india.fund_holding_snapshot WHERE fund_id=%s AND as_on=%s",
                    (fund_id, as_on))
        prior = cur.fetchone()

        if prior and prior[0] == digest:
            cur.execute("UPDATE india.fund_holding_snapshot "
                        "SET last_updated_date=%s WHERE fund_id=%s AND "
                        "as_on=%s", (today, fund_id, as_on))
            conn.commit()
            return "unchanged"

        # A month is replaced WHOLE. A half-updated month sums to something
        # other than 100%, and a portfolio that does not sum to 100% is worse
        # than one that is a week out of date.
        cur.execute("DELETE FROM india.fund_holding WHERE fund_id=%s AND "
                    "as_on=%s", (fund_id, as_on))
        if rows:
            execute_values(
                cur,
                f"INSERT INTO india.fund_holding "
                f"({', '.join(_HOLDING_COLUMNS)}) VALUES %s",
                [_holding_row(fund_id, as_on, h, today) for h in rows])
        cur.execute(
            "INSERT INTO india.fund_holding_snapshot (fund_id, as_on,"
            " content_hash, holding_count, percent_total, first_loaded_date,"
            " last_updated_date, restatements) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (fund_id, as_on) DO UPDATE SET"
            "  content_hash=EXCLUDED.content_hash,"
            "  holding_count=EXCLUDED.holding_count,"
            "  percent_total=EXCLUDED.percent_total,"
            "  last_updated_date=EXCLUDED.last_updated_date,"
            "  restatements=india.fund_holding_snapshot.restatements + 1",
            (fund_id, as_on, digest, len(rows), total, today, today, 0))
    conn.commit()
    return "restated" if prior else "inserted"


def load_asset_allocation(conn, fund_id, as_on, allocation, today=None):
    today = today or _dt.date.today()
    eq = float(allocation.get("equity") or 0.0)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO india.fund_asset_allocation (fund_id, as_on,"
            " equity_percent, debt_percent, other_percent, tax_bucket,"
            " last_updated_date) VALUES (%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (fund_id, as_on) DO UPDATE SET"
            "  equity_percent=EXCLUDED.equity_percent,"
            "  debt_percent=EXCLUDED.debt_percent,"
            "  other_percent=EXCLUDED.other_percent,"
            "  tax_bucket=EXCLUDED.tax_bucket,"
            "  last_updated_date=EXCLUDED.last_updated_date",
            (fund_id, as_on, eq, float(allocation.get("debt") or 0.0),
             float(allocation.get("other") or 0.0), tax_bucket(eq), today))
    conn.commit()


def band_allocation(rows):
    """{band -> percent} from enriched holdings. Debt only.

    Equity has no rating and never will, so including it would make the bands
    sum to something that is not the debt book and is not the fund either.
    """
    out = {}
    for h in rows:
        if h.get("asset_class") == "equity" or not h.get("is_issuer", True):
            continue
        b = rating_band(h.get("rating"))
        out[b] = round(out.get(b, 0.0) + float(h.get("percent") or 0.0), 4)
    return out


def etmoney_band_allocation(buckets):
    """{band -> percent} from ETMoney's own published rating split."""
    out = {}
    for grade, pct in (buckets or {}).items():
        b = rating_band(grade)
        out[b] = round(out.get(b, 0.0) + float(pct or 0.0), 4)
    return out


def load_rating_allocation(conn, fund_id, as_on, bands, source, today=None):
    from psycopg2.extras import execute_values

    today = today or _dt.date.today()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM india.fund_rating_allocation WHERE fund_id=%s"
                    " AND as_on=%s AND source=%s", (fund_id, as_on, source))
        if bands:
            execute_values(
                cur,
                "INSERT INTO india.fund_rating_allocation (fund_id, as_on,"
                " source, rating_band, percent, last_updated_date) VALUES %s",
                [(fund_id, as_on, source, b, p, today)
                 for b, p in bands.items()])
    conn.commit()


# ── the manual queue ────────────────────────────────────────────────────────

def override_key(name):
    return " ".join(str(name or "").upper().split())


def pending_reviews(conn, limit=100):
    with conn.cursor() as cur:
        cur.execute("SELECT company, instrument_type, gap, funds_affected,"
                    " largest_percent, total_percent, latest_as_on "
                    "FROM india.pending_rating_review LIMIT %s", (limit,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def set_override(conn, display_name, rating=None, borrower_category=None,
                 seniority=None, source_note="", entered_by="manual",
                 review_days=OVERRIDE_REVIEW_DAYS, unratable=False):
    """Record a hand-found rating. Keyed on the NAME, so it answers for every
    fund holding that issuer rather than repeating per fund."""
    key = override_key(display_name)
    if not key:
        raise ValueError("an override needs a name")
    if not valid_rating(rating):
        # Stored unchecked, a typo bands as Other and looks like a deliberate
        # answer forever after.
        raise ValueError(
            f"{rating!r} is not a grade this code recognises; "
            f"expected one of {', '.join(known_grades()[:8])}, ...")
    if not source_note:
        raise ValueError(
            "an override needs a source_note: a rating with no provenance "
            "cannot be checked later and will be re-entered by someone else")
    # Normalised on the way in. valid_rating() upper-cases before checking, so
    # "AAa" passes -- and storing it as typed would leave one issuer reading
    # differently from every other in the table.
    rating = (" ".join(str(rating).upper().split()) or None) if rating else None
    review_by = _dt.date.today() + _dt.timedelta(days=review_days)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO india.issuer_rating_override (issuer_key,"
            " display_name, rating, rating_band, borrower_category, seniority,"
            " source_note, entered_by, review_by, is_unratable)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (issuer_key) DO UPDATE SET"
            "  display_name=EXCLUDED.display_name, rating=EXCLUDED.rating,"
            "  rating_band=EXCLUDED.rating_band,"
            "  borrower_category=EXCLUDED.borrower_category,"
            "  seniority=EXCLUDED.seniority,"
            "  source_note=EXCLUDED.source_note,"
            "  entered_by=EXCLUDED.entered_by, entered_at=NOW(),"
            "  review_by=EXCLUDED.review_by,"
            "  is_unratable=EXCLUDED.is_unratable",
            (key, display_name, rating, rating_band(rating) if rating else None,
             borrower_category, seniority, source_note, entered_by, review_by,
             unratable))
    conn.commit()
    return key


def overrides(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT issuer_key, display_name, rating, rating_band,"
                    " borrower_category, seniority, is_unratable, review_by"
                    " FROM india.issuer_rating_override")
        cols = [d[0] for d in cur.description]
        return {r[0]: dict(zip(cols, r)) for r in cur.fetchall()}


def apply_overrides(rows, overrides_by_key):
    """Fill gaps from the override table. Never overwrites a resolved rating.

    An override is a HAND answer for something automation could not reach. If
    it started replacing NSDL's own data, a stale hand entry would quietly
    outrank a rating agency.
    """
    out = []
    for h in rows:
        d = dict(h)
        if d.get("rating") is None and d.get("company"):
            o = overrides_by_key.get(override_key(d["company"]))
            if o and not o["is_unratable"]:
                d.update(rating=o["rating"], rating_source="override",
                         rating_confidence=1.0, rating_trusted=True,
                         gap=None if o["rating"] else d.get("gap"))
                if o["borrower_category"]:
                    d["borrower_category"] = o["borrower_category"]
                if o["seniority"]:
                    d["seniority"] = o["seniority"]
        out.append(d)
    return out


# ── the batch log ───────────────────────────────────────────────────────────

def log_attempt(conn, amfi_name, status, on=None, fund_id=None,
                match_score=None, months_written=0, reason=None):
    """Record one fund's outcome. Failures are ROWS, not exceptions.

    One fund that will not resolve must not lose the fourteen hundred that
    did, so every outcome lands here and the run continues.
    """
    on = on or _dt.date.today()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO india.fund_load_log (amfi_name, attempted_on,"
            " status, fund_id, match_score, months_written, reason)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (amfi_name, attempted_on) DO UPDATE SET"
            "  status=EXCLUDED.status, fund_id=EXCLUDED.fund_id,"
            "  match_score=EXCLUDED.match_score,"
            "  months_written=EXCLUDED.months_written,"
            "  reason=EXCLUDED.reason, attempted_at=NOW()",
            (amfi_name, on, status, fund_id, match_score, months_written,
             reason))
    conn.commit()


def claimed_by(conn, fund_id, on=None):
    """The AMFI name that already resolved to this ETMoney id today, if any.

    ONE ETMONEY FUND IS ONE AMFI FUND. Two different schemes resolving to the
    same id means at least one of them is wrong, and the second silently
    overwrites nothing -- it re-loads months already stored, which is why the
    symptom was a fund reporting "0mo" next to a neighbour reporting "25mo".
    """
    with conn.cursor() as cur:
        cur.execute("SELECT amfi_name FROM india.fund_load_log WHERE"
                    " attempted_on=%s AND fund_id=%s AND status='ok' LIMIT 1",
                    (on or _dt.date.today(), fund_id))
        row = cur.fetchone()
        return row[0] if row else None


def already_done(conn, on=None, statuses=("ok", "no_match", "conflict")):
    """Names not worth attempting again today.

    'no_match' is included because a fund ETMoney does not list today will not
    start being listed this afternoon; 'error' is NOT, because a timeout may
    succeed on the next pass. That distinction is the whole reason status has
    more than two values.
    """
    on = on or _dt.date.today()
    with conn.cursor() as cur:
        cur.execute("SELECT amfi_name FROM india.fund_load_log WHERE"
                    " attempted_on=%s AND status = ANY(%s)",
                    (on, list(statuses)))
        return {r[0] for r in cur.fetchall()}


def run_summary(conn, on=None):
    on = on or _dt.date.today()
    with conn.cursor() as cur:
        cur.execute("SELECT status, COUNT(*), COALESCE(SUM(months_written),0)"
                    " FROM india.fund_load_log WHERE attempted_on=%s"
                    " GROUP BY status ORDER BY COUNT(*) DESC", (on,))
        return [dict(status=s, funds=n, months=m) for s, n, m in cur.fetchall()]


def failures(conn, on=None, limit=40):
    on = on or _dt.date.today()
    with conn.cursor() as cur:
        cur.execute("SELECT amfi_name, status, reason FROM"
                    " india.fund_load_log WHERE attempted_on=%s AND"
                    " status <> 'ok' ORDER BY status, amfi_name LIMIT %s",
                    (on, limit))
        return [dict(amfi_name=a, status=s, reason=r)
                for a, s, r in cur.fetchall()]


def reapply_overrides(conn, on=None):
    """Fill stored holdings from the override table, without refetching.

    THE GAP THIS CLOSES. Overrides were only consulted at load time, so a
    rating answered in the review queue did nothing until that fund was
    fetched again -- which on a twice-weekly schedule is days, and on a fund
    whose month has not changed is never, because the snapshot hash correctly
    declines to rewrite it.

    MATCHED ON THE NORMALISED NAME, NOT THE RAW ONE. The source writes the
    same issuer several ways -- "TVS Motor Company Ltd" and "TVS Motor Company
    Ltd - Pref. Shares", "Axis Bank Ltd." and "Axis Bank Ltd. Warrant" -- and
    an exact-string key means one entry per spelling, which is a queue that
    never shrinks. `lookup.api.normalise` already collapses them for the
    automatic path; using it here makes one entry cover every variant.

    THE SCALE STILL APPLIES. An override of AAA on a certificate of deposit
    must land as A1+, for the same reason NSDL's long-term grade does: copying
    it across is the category error that produced 86.0% AAA against a
    published 77.31%.

    Only fills where the rating is NULL. An override is a hand answer for
    something automation could not reach; if it overwrote a resolved rating, a
    stale hand entry would quietly outrank a rating agency.
    """
    from psycopg2.extras import execute_values

    from lookup.api import normalise as _norm
    from lookup.portfolio_credit import SHORT_TERM_FROM_LONG

    on = on or _dt.date.today()
    with conn.cursor() as cur:
        cur.execute("SELECT display_name, rating, borrower_category, seniority"
                    "  FROM india.issuer_rating_override"
                    " WHERE is_unratable = FALSE AND rating IS NOT NULL")
        overrides = cur.fetchall()
        cur.execute("SELECT DISTINCT company FROM india.fund_holding"
                    " WHERE company IS NOT NULL AND rating IS NULL")
        companies = [r[0] for r in cur.fetchall()]
    if not overrides or not companies:
        return 0

    # Normalised name -> the override that answers it. A later entry wins,
    # which matches the table's own ON CONFLICT behaviour.
    by_norm = {}
    for display, rating, cat, seniority in overrides:
        key = _norm(display)
        if key:
            by_norm[key] = (rating, cat, seniority)

    staged = []
    for company in companies:
        hit = by_norm.get(_norm(company))
        if not hit:
            continue
        rating, cat, seniority = hit
        short = SHORT_TERM_FROM_LONG.get(
            " ".join(str(rating).upper().split()))
        staged.append((company, rating, rating_band(rating), cat, seniority,
                       short, rating_band(short) if short else None))
    if not staged:
        return 0

    with conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE _ovr (company TEXT PRIMARY KEY,"
                    " rating TEXT, band TEXT, category TEXT, seniority TEXT,"
                    " short_rating TEXT, short_band TEXT) ON COMMIT DROP")
        execute_values(cur, "INSERT INTO _ovr VALUES %s", staged)
        cur.execute(
            "UPDATE india.fund_holding h SET"
            "  rating = CASE WHEN o.short_rating IS NOT NULL AND"
            "                h.instrument_type ~* '(CERTIFICATE[[:space:]]+OF"
            "[[:space:]]+DEPOSIT|COMMERCIAL[[:space:]]+PAPER)'"
            "                THEN o.short_rating ELSE o.rating END,"
            "  rating_band = CASE WHEN o.short_rating IS NOT NULL AND"
            "                h.instrument_type ~* '(CERTIFICATE[[:space:]]+OF"
            "[[:space:]]+DEPOSIT|COMMERCIAL[[:space:]]+PAPER)'"
            "                THEN o.short_band ELSE o.band END,"
            "  rating_confidence = 1.0,"
            "  rating_source = 'override',"
            "  borrower_category = COALESCE(o.category, h.borrower_category),"
            "  seniority = COALESCE(o.seniority, h.seniority),"
            "  gap = NULL,"
            "  last_updated_date = %s"
            " FROM _ovr o"
            " WHERE h.company = o.company AND h.rating IS NULL",
            (on,))
        n = cur.rowcount
    conn.commit()
    return n


# ── issuer concentration ────────────────────────────────────────────────────

# The corporate single-issuer ceiling actually applied. Deliberately a quarter
# point above the round number, so a fund sitting at exactly 3.00% by design is
# not flagged every week.
ISSUER_CAP = 3.25


def issuer_exposures(rows, cap=ISSUER_CAP):
    """Sum a month's holdings to one row per borrower.

    ROWS ARE PER INSTRUMENT LINE UPSTREAM -- NABARD is three lines in one
    month under Bonds and Debenture, HUDCO is two -- and a cap breached across
    three lines is still breached.

    Non-issuers are dropped: cash, repo and net receivables are not borrowers,
    and a concentration screen that ranks "Net Receivables" among the top
    exposures is measuring the fund's plumbing.

    The WORST rating and the WORST seniority across the lines are kept, not
    the largest line's. An issuer with a senior bond and a perpetual in the
    same fund is not a senior-only exposure.
    """
    from fund_filters import quasi_sovereign as _qs

    agg = {}
    for h in rows:
        if not h.get("is_issuer", True) or not h.get("company"):
            continue
        d = agg.setdefault(h["company"], dict(
            company=h["company"], percent=0.0, value=0.0, lines=0,
            asset_class=h.get("asset_class"),
            borrower_category=h.get("borrower_category"),
            rating=h.get("rating"), worst_seniority=None))
        d["percent"] += float(h.get("percent") or 0.0)
        d["value"] += float(h.get("value") or 0.0)
        d["lines"] += 1
        if h.get("seniority") == "subordinated":
            d["worst_seniority"] = "subordinated"
        # Worst rating wins: rating_band orders AAA, AA, Other, Unrated.
        if _band_rank(rating_band(h.get("rating"))) > \
                _band_rank(rating_band(d["rating"])):
            d["rating"] = h.get("rating")
    out = []
    for d in agg.values():
        d["percent"] = round(d["percent"], 4)
        d["rating_band"] = rating_band(d["rating"])
        d["cap_applied"] = cap
        # THE CAP DOES NOT BIND ON A BORROWER THE SOVEREIGN WOULD NOT LET
        # FAIL. NABARD, REC and NHAI do not default independently of the
        # government, so 9% in NABARD is not 9% in a private NBFC even though
        # the arithmetic is identical. On the first real fund, four of the
        # eight flagged issuers were government entities -- which buries the
        # breaches that matter under the ones that do not.
        exempt, reason = _qs.cap_exemption(d["company"],
                                           d.get("borrower_category"))
        d["cap_exempt"] = exempt
        d["exempt_reason"] = reason
        d["excess"] = 0.0 if exempt else round(max(0.0, d["percent"] - cap), 4)
        d["over_cap"] = d["excess"] > 0
        out.append(d)
    return sorted(out, key=lambda d: -d["percent"])


_BAND_ORDER = {BAND_AAA: 0, BAND_AA: 1, BAND_OTHER: 2, BAND_UNRATED: 3}


def _band_rank(band):
    return _BAND_ORDER.get(band, 3)


_EXPOSURE_COLUMNS = ("fund_id", "as_on", "company", "percent", "value",
                     "lines", "asset_class", "borrower_category", "rating",
                     "rating_band", "worst_seniority", "cap_applied",
                     "cap_exempt", "exempt_reason", "over_cap", "excess",
                     "last_updated_date")


def load_issuer_exposures(conn, fund_id, as_on, rows, cap=ISSUER_CAP,
                          today=None):
    """Replace this fund-month's exposures. Returns how many were written.

    Replaced whole for the same reason a holdings month is: a half-updated
    concentration picture is worse than a stale one, because it sums to
    something that is not the fund.
    """
    from psycopg2.extras import execute_values

    today = today or _dt.date.today()
    exposures = issuer_exposures(rows, cap=cap)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM india.fund_issuer_exposure WHERE fund_id=%s"
                    " AND as_on=%s", (fund_id, as_on))
        if exposures:
            execute_values(
                cur,
                f"INSERT INTO india.fund_issuer_exposure "
                f"({', '.join(_EXPOSURE_COLUMNS)}) VALUES %s",
                [(fund_id, as_on, d["company"], d["percent"], d["value"],
                  d["lines"], d["asset_class"], d["borrower_category"],
                  d["rating"], d["rating_band"], d["worst_seniority"],
                  d["cap_applied"], d["cap_exempt"], d["exempt_reason"],
                  d["over_cap"], d["excess"], today)
                 for d in exposures])
    conn.commit()
    return len(exposures)


def cap_breaches(conn, limit=50):
    with conn.cursor() as cur:
        cur.execute("SELECT fund_id, company, percent, excess, rating_band,"
                    " borrower_category FROM india.issuer_cap_breach LIMIT %s",
                    (limit,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def reclassify_non_issuers(conn, on=None):
    """Mark stored holdings that later rules say are not borrowers.

    WHY STORED ROWS NEED THIS AT ALL. A month is only rewritten when its
    CONTENT changes, which is exactly right for a twice-weekly job and exactly
    wrong when the classification RULES change: 31,291 months written before
    fund-of-fund holdings were recognised keep is_issuer TRUE, and the review
    queue asks a person for the credit rating of a gold ETF.

    The patterns are deliberately duplicated from etmoney_portfolio in SQL
    rather than re-parsing every stored payload -- one UPDATE against an index
    versus refetching 1,392 pages. `tests/test_fund_batch.py` pins the two
    against each other so they cannot drift.
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE india.fund_holding SET is_issuer = FALSE,"
            "  expected_in_nsdl = FALSE, gap = 'not_an_issuer',"
            "  last_updated_date = %s"
            " WHERE is_issuer"
            "   AND company IS NOT NULL"
            "   AND (UPPER(TRIM(company)) IN ('OTHERS','OTHER','OTHERS NET',"
            "                                 'NET CURRENT ASSETS','CASH',"
            "                                 'TREPS')"
            "        OR company ~* '\\y(ETF|FOF|UCITS|SICAV|BeES|REIT"
            "|InvIT|FUNDS?)\\y'"
            "        OR company ~* '\\yFUND OF FUNDS?\\y'"
            "        OR company ~* '[-[:space:]](GROWTH|IDCW|DIVIDEND)[[:space:]]*$')"
            "   AND NOT is_securitised",
            (on or _dt.date.today(),))
        n = cur.rowcount
        # AND INSTRUMENTS WITH NO BORROWER AT ALL. An interest rate swap is
        # a contract with a counterparty, not a loan to an issuer; InvIT and
        # REIT units are equity in a trust. Twenty-two swaps sat in the review
        # queue asking for a credit rating.
        cur.execute(
            "UPDATE india.fund_holding SET is_issuer = FALSE,"
            "  expected_in_nsdl = FALSE, gap = 'not_an_issuer',"
            "  last_updated_date = %s"
            " WHERE is_issuer AND instrument_type IS NOT NULL"
            "   AND instrument_type ~* '(INTEREST[[:space:]]+RATE"
            "[[:space:]]+SWAP|\\yIRS\\y|INFRASTRUCTURE[[:space:]]+INVESTMENT"
            "[[:space:]]+TRUST|\\yInvIT\\y|REAL[[:space:]]+ESTATE"
            "[[:space:]]+INVESTMENT[[:space:]]+TRUST)'",
            (on or _dt.date.today(),))
        n += cur.rowcount

        # AND EVERY HOLDING OF A FUND OF FUNDS, whatever it is called. The
        # holder settles what the name cannot: "Julius Baer Global Excellence
        # Equity USD C" carries no ETF, UCITS or FUND marker, and the fund
        # holding it at 98.8% is an FoF.
        cur.execute(
            "UPDATE india.fund_holding h SET is_issuer = FALSE,"
            "  expected_in_nsdl = FALSE, gap = 'not_an_issuer',"
            "  last_updated_date = %s"
            " FROM india.fund f"
            " WHERE f.fund_id = h.fund_id AND h.is_issuer"
            "   AND NOT h.is_securitised"
            "   AND f.category ~* '(\\yFoF\\y|FUND[[:space:]]+OF[[:space:]]+FUNDS?)'",
            (on or _dt.date.today(),))
        n += cur.rowcount
        # The exposures are derived from the holdings, so they have to follow.
        cur.execute(
            "DELETE FROM india.fund_issuer_exposure e"
            " USING india.fund_holding h"
            " WHERE e.fund_id = h.fund_id AND e.as_on = h.as_on"
            "   AND e.company = h.company AND NOT h.is_issuer")
    conn.commit()
    return n


def reclassify_sovereign(conn, on=None):
    """Answer government paper by rule on rows already stored.

    "GOI" sat in the review queue at 76.58% across 27 funds as `unresolved`,
    because the sovereign rule read the instrument type and its holdings carry
    a type that rule does not name. A person was being asked to confirm that
    the Government of India is sovereign.

    Anchored the same way as lookup.portfolio_credit.SOVEREIGN_COMPANIES: "GOI"
    alone is the borrower, "GOI Bonds Trading Ltd" is not.
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE india.fund_holding SET rating = 'SOV', rating_band = %s,"
            "  rating_confidence = 1.0, borrower_category = 'GoI',"
            "  gap = NULL, last_updated_date = %s"
            " WHERE rating IS NULL AND is_issuer AND company IS NOT NULL"
            "   AND (company ~* '^[[:space:]]*GOI[[:space:]]*$'"
            "        OR company ~* 'GOVERNMENT[[:space:]]+OF[[:space:]]+INDIA'"
            "        OR company ~* '^[[:space:]]*GOVT\\.?[[:space:]]+OF"
            "[[:space:]]+INDIA[[:space:]]*$'"
            "        OR company ~* 'RESERVE[[:space:]]+BANK[[:space:]]+OF"
            "[[:space:]]+INDIA')",
            (BAND_AAA, on or _dt.date.today()))
        n = cur.rowcount
    conn.commit()
    return n


# ── re-enrichment ───────────────────────────────────────────────────────────

# The classification columns, which are OURS and not the source's.
_CLASSIFICATION_KEY = ("company", "instrument_type", "asset_class",
                       "is_issuer", "is_securitised", "expected_in_nsdl",
                       "seniority")


def reenrich(conn, enrich, lookup, on=None, progress=None):
    """Re-run the credit rules over holdings already stored.

    WHY THIS IS NECESSARY AT ALL, AND IT IS THE THIRD TIME. `load_month`
    rewrites a month only when its CONTENT hash changes, and that hash covers
    the SOURCE data -- company, instrument, percent, value. It deliberately
    does not cover rating or gap, because those are ours and a change in them
    is not a change in what the fund holds.

    Which is right for a twice-weekly job and wrong every time a RULE changes.
    A full re-run does not help: the months come back unchanged and keep their
    old classification. It has bitten three times -- fund units, the sovereign
    rule, and short-term derivation for bank CDs, where Canara Bank stayed in
    the review queue across 112 funds after the rule that answers it shipped.

    IT NEVER TOUCHES A HAND-ENTERED RATING. rating_source = 'override' is
    skipped, because the rules cannot know what a person looked up -- and
    running them over the top silently undid seventeen answers once already,
    returning Karur Vysya, Titan, DBS and Julius Baer to the queue the same
    afternoon they were cleared.

    ONE UPDATE THROUGH A TEMP TABLE, NOT ONE PER KEY. The first version issued
    a statement per distinct classification, each with IS NOT DISTINCT FROM on
    nullable columns -- which no index serves, so it was a few thousand
    sequential scans of seven hundred thousand rows. It ran for minutes
    without finishing. The rules are computed in Python, staged, and applied
    in a single join.

    STILL NOT FAST, AND THE NUMBER IS MEASURED: 200,000 rows took 139 seconds
    on a first pass and 60 seconds when nothing changed, so seven hundred
    thousand is about eight minutes. Most of that is writing rows and their
    three indexes rather than the join. That is the right trade for a step
    that runs after a rule change and replaces a two-hour refetch.

    `enrich` is passed in rather than imported, because ingest must not depend
    on lookup -- the dependency runs the other way everywhere else.
    """
    from psycopg2.extras import execute_values

    on = on or _dt.date.today()
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT DISTINCT {', '.join(_CLASSIFICATION_KEY)} "
            f"FROM india.fund_holding")
        keys = cur.fetchall()
    if progress:
        progress(f"{len(keys):,} distinct classifications")

    rows = [dict(zip(_CLASSIFICATION_KEY, k), company_id=0,
                 instrument_code=0, percent=0.0, value=0.0) for k in keys]
    enriched = enrich(rows, lookup)
    if progress:
        progress("rules applied; staging")

    staged = [tuple(k) + (e.get("rating"), rating_band(e.get("rating")),
                          e.get("rating_confidence"),
                          e.get("borrower_category"), e.get("gap"),
                          e.get("seniority"), e.get("rating_source"))
              for k, e in zip(keys, enriched)]

    with conn.cursor() as cur:
        cur.execute("""
            CREATE TEMP TABLE _reenrich (
                company TEXT, instrument_type TEXT, asset_class TEXT,
                is_issuer BOOLEAN, is_securitised BOOLEAN,
                expected_in_nsdl BOOLEAN, seniority_in TEXT,
                new_rating TEXT, new_band TEXT,
                new_confidence NUMERIC(6, 4), new_category TEXT,
                new_gap TEXT, new_seniority TEXT, new_source TEXT
            ) ON COMMIT DROP
        """)
        execute_values(cur, "INSERT INTO _reenrich VALUES %s", staged)
        # An index on the temp table so the join is a hash rather than a
        # nested loop over the fact table.
        cur.execute("ANALYZE _reenrich")
        if progress:
            # MEASURED, NOT GUESSED: 200,000 rows took 139s on a cold table
            # and 60s when nothing changed, so seven hundred thousand is
            # minutes rather than seconds. Most of it is the row writes and
            # the three indexes on fund_holding, not the join.
            progress("updating (minutes, not seconds -- ~8 min at 700k rows)")
        cur.execute("""
            UPDATE india.fund_holding h
               SET rating = r.new_rating,
                   rating_band = r.new_band,
                   rating_confidence = r.new_confidence,
                   borrower_category = r.new_category,
                   gap = r.new_gap,
                   seniority = r.new_seniority,
                   rating_source = r.new_source,
                   last_updated_date = %s
              FROM _reenrich r
             WHERE h.rating_source IS DISTINCT FROM 'override'
               AND h.company IS NOT DISTINCT FROM r.company
               AND h.instrument_type IS NOT DISTINCT FROM r.instrument_type
               AND h.asset_class IS NOT DISTINCT FROM r.asset_class
               AND h.is_issuer = r.is_issuer
               AND h.is_securitised = r.is_securitised
               AND h.expected_in_nsdl = r.expected_in_nsdl
               AND h.seniority IS NOT DISTINCT FROM r.seniority_in
               AND (h.rating IS DISTINCT FROM r.new_rating
                    OR h.gap IS DISTINCT FROM r.new_gap
                    OR h.borrower_category IS DISTINCT FROM r.new_category)
        """, (on,))
        changed = cur.rowcount
    conn.commit()
    return dict(keys=len(keys), rows_changed=changed)


def backfill_company_names(conn, on=None):
    """Fill holdings whose company name is missing, from the same cId elsewhere.

    THE LARGEST REMAINING GAP AND IT NEVER REACHED THE QUEUE. ETMoney's
    `mfCompanyDTOMap` does not always name every cId a fund holds, so those
    rows land with company NULL -- 15 funds carry an unnamed certificate of
    deposit at up to 8.94%, and `pending_rating_review` filters on
    `company IS NOT NULL`, so nobody was ever asked about them. Silently
    unrated is worse than queued.

    THE NAME IS RECOVERABLE BECAUSE cId IS ETMONEY-WIDE. The same company id
    appears across funds, and a fund whose page did name it supplies the name
    for one whose page did not. This reads that from what is already stored;
    no refetch.

    A cId RESOLVING TO TWO DIFFERENT NAMES IS NOT BACKFILLED. That would mean
    the id is per-fund rather than global, and guessing which name is right
    would attach one company's rating to another's paper -- the failure this
    line has already had once. Those are counted and left alone.
    """
    on = on or _dt.date.today()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT company_id, COUNT(DISTINCT company) AS names,
                   MIN(company) AS name
              FROM india.fund_holding
             WHERE company IS NOT NULL
             GROUP BY company_id
        """)
        rows = cur.fetchall()
    unique = {cid: name for cid, n, name in rows if n == 1}
    ambiguous = [cid for cid, n, _ in rows if n > 1]

    filled = 0
    if unique:
        from psycopg2.extras import execute_values
        with conn.cursor() as cur:
            cur.execute("CREATE TEMP TABLE _names (company_id INTEGER "
                        "PRIMARY KEY, company TEXT) ON COMMIT DROP")
            execute_values(cur, "INSERT INTO _names VALUES %s",
                           list(unique.items()))
            cur.execute(
                "UPDATE india.fund_holding h SET company = n.company,"
                "  last_updated_date = %s"
                " FROM _names n"
                " WHERE h.company_id = n.company_id AND h.company IS NULL",
                (on,))
            filled = cur.rowcount
        conn.commit()
    return dict(known_ids=len(unique), ambiguous_ids=len(ambiguous),
                rows_filled=filled)
