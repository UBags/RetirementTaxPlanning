# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""test_credit_db.py -- the store and the two APIs, against a real database.

RUN AGAINST POSTGRESQL OR NOT AT ALL. DISTINCT ON, MODE() WITHIN GROUP and
LEFT(isin, 7) as an index expression are all PostgreSQL, and a SQLite stand-in
would test a different query than the one that ships. Set RTP_TEST_DSN, or
these skip.

THE FOUR THAT CARRY THE DESIGN:

  test_an_unchanged_rating_writes_nothing         the whole point of the table
  test_a_changed_rating_appends_and_keeps_history it must not overwrite
  test_an_unrated_isin_triangulates_from_the_issuer
  test_an_unknown_isin_resolves_on_its_issuer_prefix
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

import lookup.api as A  # noqa: E402
from fund_filters import borrower as B  # noqa: E402
from ingest import store as S  # noqa: E402
from lookup.api import CreditLookup, normalise  # noqa: E402

DSN = os.environ.get("RTP_TEST_DSN")
pytestmark = pytest.mark.skipif(
    not DSN, reason="set RTP_TEST_DSN to a PostgreSQL database to run these")


def _worker_dsn(dsn):
    """A DATABASE PER XDIST WORKER, because these tests are not pure.

    Every other test in this repo is, so `pytest -n 8` is the normal way to
    run and nobody should have to remember that one file is different. But
    the fixture below drops and rebuilds the `india` schema per test, and
    eight workers doing that against ONE database drop it out from under each
    other: pg_namespace unique violations, deadlocks, and a
    debt_security_current that vanishes mid-query.

    A schema per worker would be the lighter fix, but `india` is hardcoded in
    the SQL and in every query in store.py and api.py -- making it
    configurable would be changing production code to suit the tests. A
    database per worker changes nothing outside this function.

    The databases persist between runs by design: creating them is the slow
    part, and they are dropped and rebuilt schema-first by every test anyway.
    Under serial pytest there is no worker id and the DSN is used unchanged.
    """
    wid = os.environ.get("PYTEST_XDIST_WORKER")
    if not wid or "://" in dsn:
        return dsn
    try:
        base = dict(kv.split("=", 1) for kv in dsn.split())
    except ValueError:
        return dsn
    target = f"{base.get('dbname', 'postgres')}_{wid}"

    import psycopg2
    admin = psycopg2.connect(
        " ".join(f"{k}={v}" for k, v in dict(base, dbname="postgres").items()))
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s",
                        (target,))
            if not cur.fetchone():
                try:
                    cur.execute(f'CREATE DATABASE "{target}"')
                except psycopg2.errors.DuplicateDatabase:
                    pass
    finally:
        admin.close()
    return " ".join(f"{k}={v}"
                    for k, v in dict(base, dbname=target).items())

D1 = dt.date(2026, 8, 1)
D2 = dt.date(2026, 8, 2)
D3 = dt.date(2026, 8, 3)


@pytest.fixture(scope="session")
def dsn():
    return _worker_dsn(DSN)


@pytest.fixture()
def conn(dsn):
    import psycopg2
    c = psycopg2.connect(dsn)
    with c.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS india CASCADE")
    c.commit()
    S.apply_schema(c)
    yield c
    c.close()


def _rec(isin, issuer, grade="AAA", raw=None, sector="", ownership="",
         tier="corporate", size=1e9, desc=""):
    return dict(isin=isin, issuer=issuer,
                issuer_key=issuer.upper().replace(" LIMITED", ""),
                tier=tier, description=desc or f"{issuer} NCD",
                instrument_type="Debentures", sector=sector,
                ownership=ownership, status="Active", allotment=None,
                redemption=dt.date(2030, 1, 1), coupon=7.5, face_value=1e6,
                issue_size=size,
                raw_rating=raw or (f"CRISIL {grade}/Stable" if grade else ""),
                worst_grade=grade, worst_rank=0 if grade == "AAA" else 5,
                enhanced=False, not_cooperating=False)


def _load(conn, recs, as_on):
    return S.load_daily(conn, B.categorise_all(recs), as_on=as_on)


# ── the change-only rule ────────────────────────────────────────────────────

def test_the_first_load_inserts_everything(conn):
    rep = _load(conn, [_rec("INE001A08001", "ALPHA LIMITED"),
                       _rec("INE002A08001", "BETA LIMITED")], D1)
    assert rep["inserted"] == 2 and rep["new_isins"] == 2
    assert rep["unchanged"] == 0


def test_an_unchanged_rating_writes_nothing(conn):
    """THE WHOLE POINT OF THE TABLE. A daily snapshot of 28,745 rows would add
    ten million rows a year and answer no question this does not."""
    recs = [_rec("INE001A08001", "ALPHA LIMITED"),
            _rec("INE002A08001", "BETA LIMITED")]
    _load(conn, recs, D1)
    rep = _load(conn, recs, D2)
    assert rep["inserted"] == 0
    assert rep["unchanged"] == 2
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM india.debt_security")
        assert cur.fetchone()[0] == 2


def test_a_changed_rating_appends_and_keeps_history(conn):
    """It must APPEND, not overwrite. The old rating is the history."""
    _load(conn, [_rec("INE001A08001", "ALPHA LIMITED", "AAA")], D1)
    _load(conn, [_rec("INE001A08001", "ALPHA LIMITED", "AA")], D2)
    with conn.cursor() as cur:
        cur.execute("SELECT last_updated_date, rating_grade "
                    "FROM india.debt_security WHERE isin = 'INE001A08001' "
                    "ORDER BY last_updated_date")
        assert cur.fetchall() == [(D1, "AAA"), (D2, "AA")]


def test_the_current_view_shows_only_the_latest_row(conn):
    _load(conn, [_rec("INE001A08001", "ALPHA LIMITED", "AAA")], D1)
    _load(conn, [_rec("INE001A08001", "ALPHA LIMITED", "AA")], D2)
    _load(conn, [_rec("INE001A08001", "ALPHA LIMITED", "A")], D3)
    with conn.cursor() as cur:
        cur.execute("SELECT rating_grade, COUNT(*) OVER () "
                    "FROM india.debt_security_current")
        assert cur.fetchone() == ("A", 1)


def test_the_rating_on_a_past_date_is_recoverable(conn):
    """History is the reason for the table, so reading it back is a test."""
    _load(conn, [_rec("INE001A08001", "ALPHA LIMITED", "AAA")], D1)
    _load(conn, [_rec("INE001A08001", "ALPHA LIMITED", "AA")], D3)
    with conn.cursor() as cur:
        cur.execute("SELECT rating_grade FROM india.debt_security "
                    "WHERE isin = 'INE001A08001' AND last_updated_date <= %s "
                    "ORDER BY last_updated_date DESC LIMIT 1", (D2,))
        assert cur.fetchone()[0] == "AAA"


def test_first_seen_date_survives_a_rating_change(conn):
    _load(conn, [_rec("INE001A08001", "ALPHA LIMITED", "AAA")], D1)
    _load(conn, [_rec("INE001A08001", "ALPHA LIMITED", "AA")], D2)
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT first_seen_date FROM india.debt_security")
        assert cur.fetchall() == [(D1,)]


def test_a_category_change_also_creates_history(conn):
    """WATCHED_FIELDS is rating AND category. A borrower moving from NBFC to
    Pvt Bank is a credit event even when the grade holds."""
    a = _rec("INE001A08001", "ALPHA LIMITED", sector="")
    b = _rec("INE001A08001", "ALPHA LIMITED", sector="Private Sector Bank")
    _load(conn, [a], D1)
    assert _load(conn, [b], D2)["inserted"] == 1


def test_a_coupon_restatement_does_not_create_history(conn):
    """Static fields are stored but not watched. Recording a rating-history
    row for a restated coupon would put a fake event in the history."""
    a = _rec("INE001A08001", "ALPHA LIMITED")
    b = dict(a, coupon=9.99, face_value=5e5)
    _load(conn, [a], D1)
    assert _load(conn, [b], D2)["inserted"] == 0


def test_a_repeated_isin_in_one_file_is_kept_once_and_counted(conn):
    """(isin, last_updated_date) is the primary key, so a repeat inside one
    day is a constraint violation rather than a second row. The NSDL file has
    repeated ISINs, so dropping silently would hide a real data question."""
    rep = _load(conn, [_rec("INE001A08001", "ALPHA LIMITED", "AAA"),
                       _rec("INE001A08001", "ALPHA LIMITED", "AA")], D1)
    assert rep["inserted"] == 1
    assert rep["duplicate_isins"] == 1


# ── API 1: by ISIN ──────────────────────────────────────────────────────────

def test_a_rated_isin_returns_its_own_rating(conn):
    _load(conn, [_rec("INE001A08001", "ALPHA LIMITED", "AA+")], D1)
    r = CreditLookup(conn).by_isin("INE001A08001")
    assert r.found and r.rating == "AA+" and r.source == "isin"
    assert r.confidence == 1.0


def test_an_unrated_isin_triangulates_from_the_issuer(conn):
    """Unrated private placements are common. The issuer's other paper is the
    evidence, and the WORST of it is the honest read."""
    _load(conn, [_rec("INE001A08001", "ALPHA LIMITED", "AAA"),
                 _rec("INE001A08002", "ALPHA LIMITED", "A"),
                 _rec("INE001A08003", "ALPHA LIMITED", None, raw="")], D1)
    r = CreditLookup(conn).by_isin("INE001A08003")
    assert r.found and r.source == "issuer"
    assert r.rating == "A", "must take the WORST, not the best or the latest"


def test_an_unknown_isin_resolves_on_its_issuer_prefix(conn):
    """Characters 1-7 identify the issuer. INE062A is State Bank of India
    whatever the last five say, so a bond issued yesterday still resolves."""
    _load(conn, [_rec("INE062A08265", "STATE BANK OF INDIA", "AAA",
                      sector="Public Sector Bank", ownership="PSU")], D1)
    r = CreditLookup(conn).by_isin("INE062A08ZZZ")
    assert r.found and r.source == "isin_prefix"
    assert r.borrower_category == B.PSU_BANK
    assert r.confidence < 1.0


def test_a_completely_unknown_isin_returns_other_corporate_not_an_error(conn):
    """A caller sizing a portfolio needs an answer for every line. An absent
    answer that looks like an error gets skipped, which is the same as
    treating the holding as riskless."""
    _load(conn, [_rec("INE001A08001", "ALPHA LIMITED")], D1)
    r = CreditLookup(conn).by_isin("INE999Z08999")
    assert r.found is False
    assert r.rating is None
    assert r.borrower_category == B.OTHER_CORPORATE
    assert r.confidence == 0.0


@pytest.mark.parametrize("junk", ["", None, "   ", "not an isin"])
def test_rubbish_input_still_returns_the_default_shape(conn, junk):
    _load(conn, [_rec("INE001A08001", "ALPHA LIMITED")], D1)
    r = CreditLookup(conn).by_isin(junk)
    assert r.found is False and r.borrower_category == B.OTHER_CORPORATE


# ── API 2: by vague name ────────────────────────────────────────────────────

def _portfolio(conn):
    _load(conn, [
        _rec("INE040A08419", "HDFC BANK LIMITED", "AAA",
             sector="Private Sector Bank", ownership="Non PSU", size=2e10),
        _rec("INE062A08265", "STATE BANK OF INDIA", "AAA",
             sector="Public Sector Bank", ownership="PSU", size=5e10),
        _rec("INE121A08OI5", "CHOLAMANDALAM INVESTMENT AND FINANCE COMPANY "
             "LIMITED", "AA+",
             sector="Non-Banking Financial Company (NBFC)", size=1e10),
        _rec("INE002A08001", "RELIANCE INDUSTRIES LIMITED", "AAA",
             sector="Refineries", size=8e10),
        _rec("INE121H08149", "IL&FS FINANCIAL SERVICES LIMITED", "D",
             sector="Non-Banking Financial Company (NBFC)", size=2e9),
    ], D1)
    return CreditLookup(conn)


@pytest.mark.parametrize("line,expect_rating", [
    ("7.90% HDFC Bank Ltd. 2028", "AAA"),
    ("HDFC BANK LIMITED **", "AAA"),
    ("State Bank of India (AT1)", "AAA"),
    ("Cholamandalam Investment & Finance Co Ltd", "AA+"),
    ("Reliance Industries Ltd 9.05% NCD 17OT28", "AAA"),
    ("IL&FS Financial Services Limited", "D"),
])
def test_a_factsheet_line_resolves_to_the_right_borrower(conn, line,
                                                         expect_rating):
    r = _portfolio(conn).by_name(line)
    assert r.found, f"{line!r} did not resolve"
    assert r.rating == expect_rating


def test_an_isin_in_the_string_wins_over_the_fuzzy_match(conn):
    """Factsheets often carry both. An exact key should never lose to a fuzzy
    match on the same row."""
    r = _portfolio(conn).by_name("Some Bank Ltd INE062A08265 8.2% 2033")
    assert r.source == "isin" and r.issuer_name == "STATE BANK OF INDIA"
    assert r.confidence == 1.0


def test_a_name_that_matches_nothing_returns_other_corporate(conn):
    r = _portfolio(conn).by_name("Zzyzx Trading Company of Nowhere")
    assert r.found is False
    assert r.borrower_category == B.OTHER_CORPORATE


def test_the_borrower_category_comes_back_with_the_rating(conn):
    lk = _portfolio(conn)
    assert lk.by_name("HDFC Bank Ltd").borrower_category == B.LARGE_PVT_BANK
    assert lk.by_name("State Bank of India").borrower_category == B.PSU_BANK
    assert lk.by_name("Cholamandalam Investment").borrower_category == B.NBFC
    assert lk.by_name("Reliance Industries").borrower_category == \
        B.LARGE_CORPORATE


def test_a_close_second_place_is_reported_as_ambiguous(conn):
    """Fund factsheets write HDFC Bank Ltd, HDFC Ltd and HDB Financial
    Services -- different borrowers with overlapping names. A single best
    match hides that the runner-up was one point behind."""
    _load(conn, [
        _rec("INE001A08001", "ALPHA FINANCE LIMITED", "AAA"),
        _rec("INE002A08001", "ALPHA FINANCE HOLDINGS LIMITED", "BB"),
    ], D1)
    r = CreditLookup(conn).by_name("Alpha Finance")
    assert r.runner_up is not None
    assert r.margin is not None


def test_the_score_travels_with_the_answer(conn):
    r = _portfolio(conn).by_name("HDFC Bank Ltd")
    assert 0.0 < r.confidence <= 1.0 and r.source == "name"


def test_by_names_returns_one_result_per_input_in_order(conn):
    out = _portfolio(conn).by_names(["HDFC Bank Ltd", "Nonsense Corp",
                                     "State Bank of India"])
    assert len(out) == 3
    assert [r.found for r in out] == [True, False, True]


# ── normalisation, which decides what the matcher ever sees ─────────────────

@pytest.mark.parametrize("raw,expected_in", [
    ("RELIANCE INDUSTRIES LIMITED SR P 7.79 NCD 10NV33 FVRS1LAC", "RELIANCE"),
    ("7.90% HDFC Bank Ltd. 2028", "HDFC BANK"),
    ("STATE BANK OF INDIA SR I 7.72 BD PERPETUAL FVRS1CR", "STATE BANK"),
])
def test_instrument_grammar_is_stripped_before_matching(raw, expected_in):
    """SR, NCD, FVRS1LAC and the coupon appear in every description and say
    nothing about who borrowed. Leaving them in lets two unrelated bonds score
    high on their shared boilerplate."""
    n = normalise(raw)
    assert expected_in in n
    for noise in ("NCD", "FVRS", "SR ", "7.79", "10NV33"):
        assert noise.strip() not in n.split()


def test_normalisation_is_applied_to_both_sides():
    assert normalise("HDFC Bank Ltd.") == normalise("HDFC BANK LIMITED")


# ── the two matcher bugs that were found by measurement, not reasoning ──────

def test_ilfs_does_not_resolve_to_hdb_financial_services(conn):
    """FOUND BY MEASURING, NOT BY REASONING. The roman-numeral stripper was a
    character class, which also matches "IL" -- so IL&FS Financial Services
    normalised to "&FS" and token_sort_ratio scored HDB Financial Services
    81.8 against it. A defaulted borrower read as a live one, confidently."""
    _load(conn, [_rec("INE121H08149", "IL&FS FINANCIAL SERVICES LIMITED", "D"),
                 _rec("INE756I08001", "HDB FINANCIAL SERVICES LIMITED",
                      "AAA")], D1)
    lk = CreditLookup(conn)
    assert lk.by_name("IL&FS Financial Services Limited").rating == "D"
    assert lk.by_name("HDB Financial Services Ltd").rating == "AAA"
    assert "IL" in normalise("IL&FS FINANCIAL SERVICES")


def test_a_long_junk_name_does_not_match_a_short_one_on_a_shared_word(conn):
    """WRatio falls back to a PARTIAL match when the lengths differ a lot, and
    scored "Zzyzx Trading Company of Nowhere" 85.5 against "Bank of India" on
    the word "of". That is above MIN_SCORE and would have resolved."""
    _load(conn, [_rec("INE084A08169", "BANK OF INDIA", "AA",
                      sector="Public Sector Bank", ownership="PSU")], D1)
    assert CreditLookup(conn).by_name(
        "Zzyzx Trading Company of Nowhere").found is False


def test_hdfc_does_not_resolve_to_hdfc_bank(conn):
    """They were different borrowers with different ratings for most of the
    period this model covers."""
    _load(conn, [_rec("INE040A08419", "HDFC BANK LIMITED", "AAA",
                      sector="Private Sector Bank"),
                 _rec("INE001A07TT9", "HDFC LIMITED", "AA+",
                      sector="Non-Banking Financial Company (NBFC)")], D1)
    lk = CreditLookup(conn)
    assert lk.by_name("HDFC Bank Ltd").issuer_name == "HDFC BANK LIMITED"
    assert lk.by_name("HDFC Ltd").issuer_name == "HDFC LIMITED"


def test_bank_of_india_is_not_state_bank_of_india(conn):
    """INDIA is deliberately NOT stripped as a corporate suffix: doing so
    reduces "Bank of India" to "BANK OF"."""
    _load(conn, [_rec("INE062A08265", "STATE BANK OF INDIA", "AAA",
                      sector="Public Sector Bank", ownership="PSU"),
                 _rec("INE084A08169", "BANK OF INDIA", "AA",
                      sector="Public Sector Bank", ownership="PSU")], D1)
    lk = CreditLookup(conn)
    assert lk.by_name("Bank of India Ltd").rating == "AA"
    assert lk.by_name("State Bank of India (AT1)").rating == "AAA"


# ── seniority: senior, subordinated, or unknown ─────────────────────────────

def _sub(isin, issuer, grade="AAA"):
    r = _rec(isin, issuer, grade)
    r["tier"] = "subordinated"
    return r


def test_the_isin_path_returns_the_instruments_seniority(conn):
    """THE REASON THE SENIORITY WORK EXISTS. A perpetual that never triggers
    prices like the senior paper of the same issuer, so its historical sigma
    says nothing about the loss it carries. Computing the tier and then not
    returning it makes the whole chain inert at the last step."""
    _load(conn, [_rec("INE001A08001", "ALPHA LIMITED", "AAA"),
                 _sub("INE001A08002", "ALPHA LIMITED", "AAA")], D1)
    lk = CreditLookup(conn)
    assert lk.by_isin("INE001A08001").seniority == A.SENIOR
    assert lk.by_isin("INE001A08002").seniority == A.SUBORDINATED


def test_a_sovereign_is_senior_not_sovereign(conn):
    """THE CATEGORY ERROR THE FIRST VERSION MADE. It returned classify()'s
    tier straight out, so a government bond came back with seniority
    "sovereign" -- but sovereign is a kind of BORROWER, which is already
    reported separately, and it is not a rank in a capital structure."""
    r = _rec("IN0020060045", "GOVERNMENT OF INDIA", "AAA")
    r["tier"] = "sovereign"
    _load(conn, [r, _sub("INE001A08002", "ALPHA LIMITED")], D1)
    res = CreditLookup(conn).by_isin("IN0020060045")
    assert res.seniority == A.SENIOR
    assert res.borrower_category == B.GOI


def test_seniority_survives_rating_triangulation(conn):
    """Borrowing the issuer's RATING is a judgement about the borrower.
    Seniority is a fact about the bond and is already known, so it must not be
    replaced by an issuer-level answer."""
    _load(conn, [_rec("INE001A08001", "ALPHA LIMITED", "A"),
                 dict(_sub("INE001A08002", "ALPHA LIMITED", None),
                      raw_rating="", worst_grade=None)], D1)
    r = CreditLookup(conn).by_isin("INE001A08002")
    assert r.source == "issuer" and r.rating == "A"
    assert r.seniority == A.SUBORDINATED


# ── unknown, which is the answer far more often than it looks ───────────────

def test_a_holding_with_no_isin_reports_unknown_not_senior(conn):
    """MUTUAL FUND HOLDINGS OFTEN CARRY NO ISIN. A factsheet line naming a
    borrower does not say which of their instruments is held, and defaulting
    to senior would report a perpetual as senior paper."""
    _load(conn, [_rec("INE001A08001", "ALPHA LIMITED", "AAA"),
                 _sub("INE001A08002", "ALPHA LIMITED", "AAA")], D1)
    r = CreditLookup(conn).by_name("Alpha Limited")
    assert r.found
    assert r.seniority == A.SENIORITY_UNKNOWN
    assert r.issuer_has_subordinated is True
    assert r.issuer_subordinated_count == 1


def test_an_unresolved_lookup_reports_unknown(conn):
    _load(conn, [_sub("INE001A08002", "ALPHA LIMITED")], D1)
    lk = CreditLookup(conn)
    assert lk.by_isin("INE999Z08999").seniority == A.SENIORITY_UNKNOWN
    assert lk.by_name("Zzyzx Trading").seniority == A.SENIORITY_UNKNOWN
    assert lk.by_isin(None).seniority == A.SENIORITY_UNKNOWN


def test_the_prefix_path_reports_unknown_but_warns(conn):
    """An ISIN never seen resolves to the borrower but not to the bond, so it
    is in the same position as a name."""
    _load(conn, [_sub("INE001A08002", "ALPHA LIMITED", "AAA")], D1)
    r = CreditLookup(conn).by_isin("INE001A08ZZZ")
    assert r.source == "isin_prefix"
    assert r.seniority == A.SENIORITY_UNKNOWN
    assert r.issuer_has_subordinated is True


def test_a_table_that_cannot_tell_seniority_apart_says_unknown(conn):
    """THE GUARD AGAINST A CONFIDENT WRONG ANSWER. classify() only reaches
    SUBORDINATED when the description and maturity rules are in the tree. If
    they are not, every row was stored as CORPORATE, and mapping that to
    "senior" would report every perpetual in the file as senior paper."""
    _load(conn, [_rec("INE001A08001", "ALPHA LIMITED", "AAA"),
                 _rec("INE001A08002", "BETA LIMITED", "AA")], D1)
    lk = CreditLookup(conn)
    assert lk.seniority_available is False
    assert lk.by_isin("INE001A08001").seniority == A.SENIORITY_UNKNOWN


def test_one_subordinated_row_is_enough_to_trust_the_rest(conn):
    """The test is on the DATA, not the code, because the table may have been
    written by an earlier run."""
    _load(conn, [_rec("INE001A08001", "ALPHA LIMITED", "AAA"),
                 _sub("INE001A08002", "BETA LIMITED", "AA")], D1)
    lk = CreditLookup(conn)
    assert lk.seniority_available is True
    assert lk.by_isin("INE001A08001").seniority == A.SENIOR


def test_an_issuer_with_no_subordinated_paper_says_so(conn):
    _load(conn, [_rec("INE001A08001", "ALPHA LIMITED", "AAA"),
                 _sub("INE002A08001", "BETA LIMITED", "AA")], D1)
    r = CreditLookup(conn).by_name("Alpha Limited")
    assert r.issuer_has_subordinated is False
    assert r.issuer_subordinated_count == 0


def test_an_unrecognised_tier_is_unknown_not_senior(conn):
    """A tier this code does not recognise is not evidence that the bond is
    senior."""
    assert A.seniority_of("something_new") == A.SENIORITY_UNKNOWN
    assert A.seniority_of(None) == A.SENIORITY_UNKNOWN
