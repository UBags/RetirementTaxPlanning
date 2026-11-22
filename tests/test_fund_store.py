# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""test_fund_store.py -- holdings, allocations and the manual queue.

THE BANDING AND TAX TESTS NEED NO DATABASE. The rest do, and skip without
RTP_TEST_DSN.

THE FOUR THAT CARRY THE DESIGN:

  test_a_sold_holding_simply_has_no_row_next_month  -- extinguishing, with no
      delete, no tombstone and no is_active flag to keep consistent
  test_a_restated_month_is_replaced_whole          -- a half-updated month
      sums to something other than 100%
  test_eight_fetches_a_month_produce_one_write     -- the point of the hash
  test_an_override_never_outranks_a_rating_agency  -- a stale hand entry must
      not quietly replace NSDL's own data
"""
from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ingest import fund_store as F  # noqa: E402

DSN = os.environ.get("RTP_TEST_DSN")
JUN, JUL, AUG = dt.date(2026, 6, 30), dt.date(2026, 7, 31), dt.date(2026, 8, 6)
FUND = 44228


def _h(cid, pct, code=3, company=None, rating="AAA", klass="debt", **kw):
    d = dict(company_id=cid, instrument_code=code,
             company=company or f"ISSUER {cid} LTD", sector="Financial",
             instrument_type="Debenture", asset_class=klass, percent=pct,
             value=pct * 1e7, is_issuer=True, expected_in_nsdl=True,
             is_securitised=False, seniority=None, rating=rating,
             rating_confidence=0.99, borrower_category="NBFC", gap=None)
    d.update(kw)
    return d


# ── banding, no database ────────────────────────────────────────────────────

@pytest.mark.parametrize("grade,band", [
    ("AAA", F.BAND_AAA), ("AAA(CE)", F.BAND_AAA), ("SOV", F.BAND_AAA),
    ("A1+", F.BAND_AAA),
    ("AA+", F.BAND_AA), ("AA", F.BAND_AA), ("AA-", F.BAND_AA),
    ("A1", F.BAND_AA),
    ("A+", F.BAND_OTHER), ("BBB", F.BAND_OTHER), ("D", F.BAND_OTHER),
    ("A2", F.BAND_OTHER),
])
def test_the_three_bands_cover_both_agency_scales(grade, band):
    """The short-term scale is mapped onto the long-term one because a fund
    holds both and a split that keeps them apart cannot be summed. A
    CONVENTION, NOT AN IDENTITY: A1+ says an issuer will meet a twelve-month
    obligation, AAA says a ten-year one, and an issuer can hold the first
    while losing the second."""
    assert F.rating_band(grade) == band


@pytest.mark.parametrize("junk", ["Cash & Call Money", "Others",
                                  "Term Deposits", None, ""])
def test_things_that_are_not_ratings_are_unrated_not_other(junk):
    """Banding cash as Other would put it in the same bucket as a BBB
    borrower."""
    assert F.rating_band(junk) == F.BAND_UNRATED


@pytest.mark.parametrize("equity,bucket", [
    (0, F.TAX_SPECIFIED_DEBT), (34.99, F.TAX_SPECIFIED_DEBT),
    (35, F.TAX_OLD_DEBT), (64.99, F.TAX_OLD_DEBT),
    (65, F.TAX_EQUITY), (70.77, F.TAX_EQUITY), (98.15, F.TAX_EQUITY),
])
def test_the_tax_bucket_turns_on_sixty_five_and_thirty_five(equity, bucket):
    """A fund drifting across 65% changes its own tax treatment: equity bucket
    above, old debt bucket between, s.50AA slab rate below."""
    assert F.tax_bucket(equity) == bucket


def test_the_band_split_excludes_equity():
    """Equity has no rating and never will, so including it would make the
    bands sum to something that is neither the debt book nor the fund."""
    rows = [_h(1, 60.0, klass="equity", rating=None), _h(2, 40.0)]
    assert F.band_allocation(rows) == {F.BAND_AAA: 40.0}


def test_the_band_split_excludes_cash():
    rows = [_h(1, 90.0), _h(2, 10.0, is_issuer=False, rating=None)]
    assert F.band_allocation(rows) == {F.BAND_AAA: 90.0}


def test_the_snapshot_hash_ignores_row_order_and_load_date():
    a = [_h(1, 10.0), _h(2, 20.0)]
    assert F.snapshot_hash(a) == F.snapshot_hash(list(reversed(a)))
    assert F.snapshot_hash(a) != F.snapshot_hash([_h(1, 10.0), _h(2, 20.1)])


# ── the database ────────────────────────────────────────────────────────────

pytestmark_db = pytest.mark.skipif(not DSN, reason="set RTP_TEST_DSN")


@pytest.fixture()
def conn(dsn):
    import psycopg2

    from ingest import store as S
    c = psycopg2.connect(dsn)
    with c.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS india CASCADE")
    c.commit()
    S.apply_schema(c)
    F.apply_schema(c)
    F.upsert_fund(c, FUND, "Bajaj Finserv Banking and PSU Fund", as_on=AUG)
    yield c
    c.close()


def _count(conn, as_on=None):
    with conn.cursor() as cur:
        if as_on:
            cur.execute("SELECT COUNT(*) FROM india.fund_holding WHERE "
                        "fund_id=%s AND as_on=%s", (FUND, as_on))
        else:
            cur.execute("SELECT COUNT(*) FROM india.fund_holding")
        return cur.fetchone()[0]


def test_a_month_loads_once(conn):
    assert F.load_month(conn, FUND, JUL, [_h(1, 60.0), _h(2, 40.0)],
                        today=AUG) == "inserted"
    assert _count(conn, JUL) == 2


def test_eight_fetches_a_month_produce_one_write(conn):
    """THE POINT OF THE HASH. Month-end holdings change once a month; a
    twice-weekly job otherwise rewrites the same rows eight times."""
    rows = [_h(1, 60.0), _h(2, 40.0)]
    assert F.load_month(conn, FUND, JUL, rows, today=AUG) == "inserted"
    for _ in range(7):
        assert F.load_month(conn, FUND, JUL, rows, today=AUG) == "unchanged"
    assert _count(conn, JUL) == 2


def test_a_sold_holding_simply_has_no_row_next_month(conn):
    """EXTINGUISHING, WITH NO MACHINERY. No delete, no tombstone, no is_active
    flag to keep consistent -- the holding is absent from the following
    month's snapshot and "what did this fund hold in June" is one select."""
    F.load_month(conn, FUND, JUN, [_h(1, 60.0), _h(2, 40.0)], today=AUG)
    F.load_month(conn, FUND, JUL, [_h(1, 100.0)], today=AUG)
    assert _count(conn, JUN) == 2
    assert _count(conn, JUL) == 1
    with conn.cursor() as cur:
        cur.execute("SELECT company_id FROM india.fund_holding WHERE "
                    "fund_id=%s AND as_on=%s", (FUND, JUL))
        assert [r[0] for r in cur.fetchall()] == [1]


def test_a_restated_month_is_replaced_whole(conn):
    """A half-updated month sums to something other than 100%, and a portfolio
    that does not sum to 100% is worse than one a week out of date."""
    F.load_month(conn, FUND, JUL, [_h(1, 60.0), _h(2, 40.0)], today=AUG)
    assert F.load_month(conn, FUND, JUL, [_h(1, 70.0), _h(3, 30.0)],
                        today=AUG) == "restated"
    with conn.cursor() as cur:
        cur.execute("SELECT company_id, percent FROM india.fund_holding "
                    "WHERE fund_id=%s AND as_on=%s ORDER BY company_id",
                    (FUND, JUL))
        assert cur.fetchall() == [(1, 70.0), (3, 30.0)]
        cur.execute("SELECT restatements, percent_total FROM "
                    "india.fund_holding_snapshot WHERE fund_id=%s AND as_on=%s",
                    (FUND, JUL))
        assert cur.fetchone() == (1, 100.0)


def test_a_restatement_does_not_touch_other_months(conn):
    F.load_month(conn, FUND, JUN, [_h(1, 100.0)], today=AUG)
    F.load_month(conn, FUND, JUL, [_h(1, 50.0), _h(2, 50.0)], today=AUG)
    F.load_month(conn, FUND, JUL, [_h(1, 100.0)], today=AUG)
    assert _count(conn, JUN) == 1 and _count(conn, JUL) == 1


def test_the_rating_band_is_stored_with_each_holding(conn):
    F.load_month(conn, FUND, JUL, [_h(1, 50.0, rating="AA+"),
                                   _h(2, 50.0, rating="AAA")], today=AUG)
    with conn.cursor() as cur:
        cur.execute("SELECT rating, rating_band FROM india.fund_holding "
                    "WHERE fund_id=%s ORDER BY company_id", (FUND,))
        assert cur.fetchall() == [("AA+", "AA"), ("AAA", "AAA")]


# ── allocations ─────────────────────────────────────────────────────────────

def test_the_asset_allocation_stores_its_tax_bucket(conn):
    F.load_asset_allocation(conn, FUND, JUL,
                            {"equity": 70.77, "debt": 27.68, "other": 1.57},
                            today=AUG)
    with conn.cursor() as cur:
        cur.execute("SELECT equity_percent, tax_bucket FROM "
                    "india.fund_asset_allocation WHERE fund_id=%s", (FUND,))
        eq, bucket = cur.fetchone()
        assert float(eq) == 70.77 and bucket == F.TAX_EQUITY


def test_reloading_an_allocation_updates_rather_than_duplicates(conn):
    for eq in (70.77, 61.0):
        F.load_asset_allocation(conn, FUND, JUL,
                                {"equity": eq, "debt": 100 - eq, "other": 0},
                                today=AUG)
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*), MAX(tax_bucket) FROM "
                    "india.fund_asset_allocation WHERE fund_id=%s", (FUND,))
        assert cur.fetchone() == (1, F.TAX_OLD_DEBT)


def test_both_rating_sources_are_stored_side_by_side(conn):
    """KEEPING THEM IN ONE TABLE MAKES THE COMPARISON A QUERY rather than a
    report, so a fund where the two drift apart is findable across the whole
    universe instead of one at a time."""
    rows = [_h(1, 77.31, rating="AAA"), _h(2, 8.69, rating=None)]
    F.load_rating_allocation(conn, FUND, JUL, F.band_allocation(rows),
                             "derived", today=AUG)
    F.load_rating_allocation(
        conn, FUND, JUL,
        F.etmoney_band_allocation({"AAA": 77.31, "A1+": 7.87, "SOV": 0.82}),
        "etmoney", today=AUG)
    with conn.cursor() as cur:
        cur.execute("SELECT source, rating_band, percent FROM "
                    "india.fund_rating_allocation WHERE fund_id=%s "
                    "ORDER BY source, rating_band", (FUND,))
        got = {(s, b): float(p) for s, b, p in cur.fetchall()}
    assert got[("derived", "AAA")] == 77.31
    assert got[("etmoney", "AAA")] == pytest.approx(86.0, abs=0.01)


def test_etmoney_short_term_grades_land_in_the_aaa_band():
    """A1+ is ETMoney's own label for the CDs, and it bands with AAA."""
    assert F.etmoney_band_allocation({"AAA": 77.31, "A1+": 7.87})[
        F.BAND_AAA] == pytest.approx(85.18, abs=0.01)


# ── the manual queue ────────────────────────────────────────────────────────

def test_an_unresolved_holding_appears_in_the_queue(conn):
    F.load_month(conn, FUND, JUL, [
        _h(1, 60.0), _h(2, 7.0, company="INDIA UNIVERSAL TRUST AL1",
                        rating=None, gap="securitised", is_securitised=True),
    ], today=AUG)
    q = F.pending_reviews(conn)
    assert [r["company"] for r in q] == ["INDIA UNIVERSAL TRUST AL1"]
    assert q[0]["funds_affected"] == 1


def test_the_queue_is_ordered_by_exposure(conn):
    F.load_month(conn, FUND, JUL, [
        _h(1, 0.5, company="SMALL TRUST", rating=None, gap="securitised"),
        _h(2, 9.0, company="BIG TRUST", rating=None, gap="securitised"),
    ], today=AUG)
    assert [r["company"] for r in F.pending_reviews(conn)] == \
        ["BIG TRUST", "SMALL TRUST"]


def test_answering_once_clears_the_name_everywhere(conn):
    """KEYED ON THE NAME, NOT ON THE FUND. That is the difference between a
    queue that shrinks and one that repeats."""
    F.load_month(conn, FUND, JUL, [
        _h(1, 9.0, company="SHIVSHAKTI SECURITISATION TRUST", rating=None,
           gap="securitised")], today=AUG)
    assert len(F.pending_reviews(conn)) == 1
    F.set_override(conn, "Shivshakti Securitisation Trust", rating="AA",
                   source_note="CRISIL website, 2026-08-07",
                   entered_by="uddipan")
    assert F.pending_reviews(conn) == []


def test_an_override_needs_a_source_or_it_is_refused(conn):
    """A rating with no provenance cannot be checked later and will be
    re-entered by someone else."""
    with pytest.raises(ValueError):
        F.set_override(conn, "Some Trust", rating="AA", source_note="")


def test_an_override_expires(conn):
    """A rating looked up by hand in August is not evidence about the
    following August."""
    F.set_override(conn, "Some Trust", rating="AA", source_note="CARE",
                   review_days=-1)
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM india.stale_rating_override")
        assert cur.fetchone()[0] == 1


def test_a_name_a_person_found_unratable_stops_returning(conn):
    """Otherwise the same trust comes back every week and the queue never
    shrinks."""
    F.load_month(conn, FUND, JUL, [
        _h(1, 9.0, company="OBSCURE TRUST", rating=None, gap="securitised")],
        today=AUG)
    F.set_override(conn, "Obscure Trust", rating=None, unratable=True,
                   source_note="looked; no public rating exists")
    assert F.pending_reviews(conn) == []


def test_an_override_never_outranks_a_rating_agency(conn):
    """An override is a HAND answer for something automation could not reach.
    If it replaced NSDL's own data, a stale hand entry would quietly outrank a
    rating agency."""
    F.set_override(conn, "ISSUER 1 LTD", rating="BBB", source_note="a guess")
    rows = [_h(1, 50.0, rating="AAA"), _h(2, 50.0, rating=None,
                                          company="ISSUER 1 LTD")]
    out = F.apply_overrides(rows, F.overrides(conn))
    assert out[0]["rating"] == "AAA", "the agency's rating must stand"
    assert out[1]["rating"] == "BBB"
    assert out[1]["rating_source"] == "override"


def test_an_unratable_override_does_not_invent_a_rating(conn):
    F.set_override(conn, "ISSUER 9 LTD", rating=None, unratable=True,
                   source_note="none exists")
    out = F.apply_overrides(
        [_h(9, 5.0, rating=None, gap="securitised", company="ISSUER 9 LTD")],
        F.overrides(conn))
    assert out[0]["rating"] is None and out[0]["gap"] == "securitised"


def test_one_issuer_can_hold_two_lines_of_the_same_instrument(conn):
    """FOUND ON REAL DATA, NOT IN REVIEW. HUDCO is in July 2026 twice under
    Debenture, at 6.74% and 2.78% -- two different bonds that ETMoney
    separates only by its own row id. Without an ordinal the second insert
    violates the primary key."""
    rows = [_h(1, 6.74, company="HUDCO"), _h(1, 2.78, company="HUDCO")]
    assert F.load_month(conn, FUND, JUL, rows, today=AUG) == "inserted"
    with conn.cursor() as cur:
        cur.execute("SELECT line_no, percent FROM india.fund_holding "
                    "WHERE fund_id=%s ORDER BY line_no", (FUND,))
        assert [(n, float(p)) for n, p in cur.fetchall()] == \
            [(0, 6.74), (1, 2.78)]


def test_the_ordinal_is_stable_across_reloads(conn):
    """Assigned from the row CONTENTS, sorted, not from arrival order -- so a
    month reloaded produces the same keys and the hash stays comparable."""
    rows = [_h(1, 2.78, company="HUDCO"), _h(1, 6.74, company="HUDCO")]
    assert F.load_month(conn, FUND, JUL, rows, today=AUG) == "inserted"
    assert F.load_month(conn, FUND, JUL, list(reversed(rows)),
                        today=AUG) == "unchanged"
    assert F.snapshot_hash(F.assign_line_numbers(rows)) == \
        F.snapshot_hash(F.assign_line_numbers(list(reversed(rows))))


def test_the_bigger_line_is_numbered_first(conn):
    """Largest first, so line_no 0 is the one that matters if only one is
    ever read."""
    out = F.assign_line_numbers([_h(1, 1.0), _h(1, 9.0), _h(2, 5.0)])
    first = [r for r in out if r["company_id"] == 1 and r["line_no"] == 0]
    assert first and first[0]["percent"] == 9.0
