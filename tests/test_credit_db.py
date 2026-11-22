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

import swp_planner.lookup.api as A  # noqa: E402
from swp_planner.fund_filters import borrower as B  # noqa: E402
from swp_planner.ingest import store as S  # noqa: E402
from swp_planner.lookup.api import CreditLookup, brand_matches, normalise  # noqa: E402

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
                # Real ranks, not a two-valued stand-in: the group fallback
                # picks the WORST member by rank, and a fixture where every
                # non-AAA grade shares rank 5 cannot tell worst from best.
                worst_grade=grade,
                worst_rank=(None if grade is None else
                            {"AAA": 0, "AA+": 1, "AA": 2, "AA-": 3, "A+": 4,
                             "A": 5, "A-": 6, "BBB+": 7, "BBB": 8, "BBB-": 9,
                             "BB": 11, "D": 19}.get(grade, 10)),
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


# ── the lookup cache, which is what makes a 2,300-fund batch finish ─────────

def test_the_same_name_is_only_resolved_once(conn):
    """The same issuers recur relentlessly: once per month for twenty-five
    months within one fund, and again in every other fund holding them. A
    batch over 2,300 funds does hundreds of thousands of lookups over a few
    thousand distinct names."""
    _load(conn, [_rec("INE001A08001", "ALPHA LIMITED", "AAA")], D1)
    lk = CreditLookup(conn)
    calls = []
    original = lk._by_name_uncached
    lk._by_name_uncached = lambda raw, limit=3: (calls.append(raw)
                                                 or original(raw, limit))
    for _ in range(20):
        assert lk.by_name("Alpha Limited").rating == "AAA"
    assert calls == ["Alpha Limited"], "resolved once, served twenty times"


def test_a_cached_result_cannot_be_mutated_by_its_caller(conn):
    """enrich() copies what it is given, but a shared dict handed out twenty
    times is a bug waiting to be written."""
    _load(conn, [_rec("INE001A08001", "ALPHA LIMITED", "AAA")], D1)
    lk = CreditLookup(conn)
    first = lk.by_name("Alpha Limited")
    first["rating"] = "TAMPERED"
    assert lk.by_name("Alpha Limited").rating == "AAA"


def test_invalidate_forgets_everything_that_could_be_stale(conn):
    """The cache assumes the rating table is not changing underneath it --
    true for a batch run, false for a process that ingests NSDL and then
    resolves funds without restarting."""
    _load(conn, [_rec("INE001A08001", "ALPHA LIMITED", "AAA")], D1)
    lk = CreditLookup(conn)
    assert lk.by_name("Alpha Limited").rating == "AAA"
    _load(conn, [_rec("INE001A08001", "ALPHA LIMITED", "BBB")], D2)
    assert lk.by_name("Alpha Limited").rating == "AAA", "still cached"
    lk.invalidate()
    assert lk.by_name("Alpha Limited").rating == "BBB"


# ── abbreviations, from the first real batch queue ─────────────────────────

@pytest.mark.parametrize("etmoney,nsdl", [
    ("Nuclear Power Corpn. Of India Ltd.",
     "NUCLEAR POWER CORPORATION OF INDIA LIMITED"),
    ("Small Industries Devp. Bank of India Ltd.",
     "SMALL INDUSTRIES DEVELOPMENT BANK OF INDIA"),
    ("Tata Housing Devp. Co. Ltd",
     "TATA HOUSING DEVELOPMENT COMPANY LIMITED"),
])
def test_an_abbreviated_name_matches_its_expansion(etmoney, nsdl):
    """FOUND IN THE FIRST REAL QUEUE. "Nuclear Power Corpn. Of India Ltd." at
    9.13% across ten funds went to manual review for a spelling.

    The failure was ASYMMETRIC: the suffix stripper removed CORPORATION from
    the NSDL side while CORPN survived on the ETMoney side, so the two were
    compared as "NUCLEAR POWER CORPN OF INDIA" against "NUCLEAR POWER OF
    INDIA" -- 88.0, under the 90 trust floor. Expanding BEFORE stripping makes
    both sides lose the same tokens."""
    assert normalise(etmoney) == normalise(nsdl)


def test_expansion_does_not_merge_distinct_borrowers():
    """The abbreviations must not buy their recall by blurring names apart."""
    assert normalise("HDFC Bank Ltd.") != normalise("HDFC Limited")
    assert normalise("Tata Power Ltd") != normalise("Tata Motors Ltd")


def test_an_unabbreviated_name_is_unaffected():
    assert normalise("REC Ltd.") == normalise("REC LIMITED")


# ── the brand guard, from four wrong companies in a live queue ──────────────

def test_the_matcher_does_not_name_a_different_company(conn):
    """THE MOST SERIOUS DEFECT IN THIS LINE, and character similarity cannot
    see it. Measured live against the real NSDL table:

        Tata Motors Finance Ltd.  -> DHARA MOTOR FINANCE LIMITED      84.2
        Tata Capital Fin Services -> SI CAPITAL & FINANCIAL SERVICES  90.0
        Aditya Birla Finance Ltd. -> ADITYA BIRLA HOUSING FINANCE     83.3

    The second was AT the 0.90 trust floor, so a credit rating belonging to SI
    Capital was about to be attached to Tata Capital paper. No floor setting
    fixes it: the distinctive part of an Indian company name is two or three
    characters of twenty, and MOTOR FINANCE / CAPITAL FINANCIAL SERVICES is
    shared industry vocabulary."""
    _load(conn, [_rec("INE001A08001", "DHARA MOTOR FINANCE LIMITED", "BBB"),
                 _rec("INE002A08001", "SI CAPITAL & FINANCIAL SERVICES "
                                      "LIMITED", "BB")], D1)
    lk = CreditLookup(conn)
    for name in ("Tata Motors Finance Ltd.",
                 "Tata Capital Financial Services Ltd."):
        r = lk.by_name(name)
        assert r.found is False, f"{name} resolved to {r.issuer_name}"
        assert r.rating is None


def test_the_rejected_candidate_is_reported_not_hidden(conn):
    """A near miss should be visible: it is what a brand-blind matcher would
    have chosen, and someone reading the queue needs to know that."""
    _load(conn, [_rec("INE001A08001", "DHARA MOTOR FINANCE LIMITED", "BBB")],
          D1)
    r = CreditLookup(conn).by_name("Tata Motors Finance Ltd.")
    assert r.found is False
    assert r.confidence > 0.7, "it scored well, and that is the point"
    assert "leading token" in (r.reason or "")


def test_the_right_company_below_the_top_is_still_found(conn):
    """The brand guard must not merely reject -- the right answer often sits
    under the wrong one."""
    _load(conn, [_rec("INE001A08001", "DHARA MOTOR FINANCE LIMITED", "BBB"),
                 _rec("INE002A08001", "TATA MOTORS FINANCE LIMITED", "AA")],
          D1)
    r = CreditLookup(conn).by_name("Tata Motors Finance Ltd.")
    assert r.found and r.issuer_name == "TATA MOTORS FINANCE LIMITED"
    assert r.rating == "AA"


@pytest.mark.parametrize("query,candidate", [
    ("REC Ltd.", "REC LIMITED"),
    ("Nuclear Power Corpn. Of India Ltd.",
     "NUCLEAR POWER CORPORATION OF INDIA LIMITED"),
    ("Small Industries Devp. Bank of India Ltd.",
     "SMALL INDUSTRIES DEVELOPMENT BANK OF INDIA"),
    ("L&T Finance Ltd.", "L&T FINANCE LIMITED"),
    ("The Wealth Company Ltd", "WEALTH COMPANY LIMITED"),
])
def test_the_guard_does_not_break_matches_that_worked(query, candidate):
    """Leading articles are skipped, so "The Wealth Company" still reaches
    "Wealth Company"."""
    assert brand_matches(normalise(query), normalise(candidate))


def test_both_directions_are_checked_not_just_one():
    """One direction alone is not enough, and the observed failures do not
    show it because all three fail BOTH ways.

    "Capital First Ltd" against "IDFC FIRST CAPITAL LIMITED" is the shape that
    distinguishes them: the query's leading token CAPITAL does appear in the
    candidate, so a one-directional check accepts a different company. The
    candidate's leading token IDFC does not appear in the query, and that is
    what rejects it."""
    q, c = normalise("Capital First Ltd"), normalise("IDFC FIRST CAPITAL "
                                                     "LIMITED")
    from swp_planner.lookup.api import _tokens, head_token
    assert head_token(q) in set(_tokens(c)), "one direction passes"
    assert head_token(c) not in set(_tokens(q)), "the other does not"
    assert brand_matches(q, c) is False


def test_a_near_miss_within_the_same_brand_is_left_to_the_floor(conn):
    """Aditya Birla Finance and Aditya Birla Housing Finance share a brand, so
    the guard passes them and the 0.90 trust floor is what separates them --
    which it does, at 0.833."""
    assert brand_matches(normalise("Aditya Birla Finance Ltd."),
                         normalise("ADITYA BIRLA HOUSING FINANCE LIMITED"))
    _load(conn, [_rec("INE001A08001", "ADITYA BIRLA HOUSING FINANCE LIMITED",
                      "AAA")], D1)
    r = CreditLookup(conn).by_name("Aditya Birla Finance Ltd.")
    assert r.confidence < 0.90


# ── instrument-variant suffixes on the issuer's own name ────────────────────

@pytest.mark.parametrize("etmoney,nsdl", [
    ("Axis Bank Ltd. Warrant", "AXIS BANK LIMITED"),
    ("LIC Housing Finance Ltd. GDR/GDS", "LIC HOUSING FINANCE LIMITED"),
    ("Tata Capital Ltd.(Partly Paid Equity)", "TATA CAPITAL LIMITED"),
    ("Vedanta Ltd - Pref. Shares 7.50", "VEDANTA LIMITED"),
    ("Mahindra & Mahindra Financial Services Ltd. - Right Entitlement",
     "MAHINDRA & MAHINDRA FINANCIAL SERVICES LIMITED"),
    ("AU Small Finance Bank Ltd. Pref. Shares",
     "AU SMALL FINANCE BANK LIMITED"),
    ("TVS Motor Company Ltd - Pref. Shares", "TVS MOTOR COMPANY LIMITED"),
])
def test_a_security_class_suffix_does_not_hide_the_issuer(etmoney, nsdl):
    """SAME LEGAL ENTITY, DIFFERENT CLASS OF SECURITY. Axis Bank is AAA in the
    NSDL file and "Axis Bank Ltd. Warrant" resolved to nothing -- the suffix
    was the only thing keeping them apart."""
    assert normalise(etmoney) == normalise(nsdl)


@pytest.mark.parametrize("name", [
    "Equity Intelligence India Ltd", "Bonus Hospitality Ltd",
    "Rights Issue Advisors Ltd",
])
def test_the_suffix_stripper_is_anchored_to_the_end(name):
    """"SHARES", "EQUITY" or "BONUS" in the middle of a name belongs to the
    company; only a trailing security class is a suffix."""
    assert normalise(name).split()[0] == name.split()[0].upper()


# ── group-implied ratings ───────────────────────────────────────────────────

def test_a_cp_only_subsidiary_takes_its_groups_rating(conn):
    """NSDL IS THE CORPORATE BOND DATABASE, so a company issuing only
    commercial paper is absent from it -- HDFC Securities, Kotak Mahindra
    Securities and Aditya Birla Finance all return zero rows, which is about
    2% of the debt book with no rating obtainable by any amount of
    matching."""
    _load(conn, [_rec("INE040A08001", "HDFC BANK LIMITED", "AAA",
                      sector="Private Sector Bank")], D1)
    r = CreditLookup(conn).by_group("HDFC Securities Ltd.")
    assert r.found and r.rating == "AAA"
    assert r.source == "group_implied"
    assert "HDFC group" in r.reason


def test_the_worst_rating_in_the_group_is_used_without_an_anchor(conn):
    """Same principle as taking the worst across an issuer's own paper: a
    rating guessed upward is the error that costs money."""
    _load(conn, [_rec("INE001A08001", "GODREJ PROPERTIES LIMITED", "AA"),
                 _rec("INE002A08001", "GODREJ INDUSTRIES LIMITED", "A")], D1)
    r = CreditLookup(conn).by_group("Godrej Consumer Products Ltd.")
    assert r.found and r.rating == "A", "the weaker of the two"


def test_an_anchor_entity_wins_over_the_worst(conn):
    """Where the flagship is in the file, that is what "the group's rating"
    means to anyone reading it."""
    _load(conn, [_rec("INE040A08001", "HDFC BANK LIMITED", "AAA",
                      sector="Private Sector Bank"),
                 _rec("INE001A08002", "HDFC CREDILA LIMITED", "A")], D1)
    assert CreditLookup(conn).by_group("HDFC Securities Ltd.").rating == "AAA"


def test_a_group_rating_is_never_presented_as_an_agency_opinion(conn):
    """CRISIL rates HDFC Securities in its own right, and a subsidiary can sit
    below its parent. IL&FS was a AAA group with a subsidiary that
    defaulted."""
    _load(conn, [_rec("INE040A08001", "HDFC BANK LIMITED", "AAA",
                      sector="Private Sector Bank")], D1)
    lk = CreditLookup(conn)
    rows = [dict(company="HDFC Securities Ltd.", company_id=1,
                 instrument_type="Commercial Paper", instrument_code=2,
                 asset_class="debt", percent=9.8, value=1e8, is_issuer=True,
                 expected_in_nsdl=False, is_securitised=False, seniority=None)]
    from swp_planner.lookup import portfolio_credit as _PC
    out = _PC.enrich(rows, lk)
    assert out[0]["rating"] == "A1+", "converted to the short-term scale"
    assert out[0]["rating_source"] == "group_short_term"
    assert out[0]["rating_trusted"] is False
    assert out[0]["rating_confidence"] < 0.9


def test_reliance_capital_does_not_borrow_reliance_industries_rating(conn):
    """THE BRAND TOKEN ALONE IS DANGEROUS. "Reliance" names both Mukesh
    Ambani's Reliance Industries, rated AAA, and Anil Ambani's Reliance
    Capital, which defaulted at D. They share a word and nothing else."""
    from swp_planner.fund_filters import groups as G
    assert G.group_of("Reliance Industries Ltd") == "RELIANCE"
    assert G.group_of("Reliance Retail Ventures Ltd.") == "RELIANCE"
    assert G.group_of("Reliance Capital Ltd") is None
    assert G.group_of("Reliance Home Finance Ltd") is None
    _load(conn, [_rec("INE002A08001", "RELIANCE INDUSTRIES LIMITED", "AAA")],
          D1)
    assert CreditLookup(conn).by_group("Reliance Capital Ltd").found is False


def test_kotak_mahindra_is_not_the_mahindra_group(conn):
    from swp_planner.fund_filters import groups as G
    assert G.group_of("Kotak Mahindra Prime Ltd.") == "KOTAK"
    assert G.group_of("Mahindra & Mahindra Financial Services Ltd.") == \
        "MAHINDRA"


def test_a_company_in_no_group_gets_nothing(conn):
    """Inventing a group for a company is worse than a gap.

    The examples had to change: Infina and Panatone were the original two and
    both have since been declared. Time Technoplast, Alembic and Redington
    have not, and the point is the same -- a name with no group and no rated
    sibling gets no rating at all."""
    _load(conn, [_rec("INE001A08001", "ALPHA LIMITED", "AAA")], D1)
    lk = CreditLookup(conn)
    for name in ("Time Technoplast Ltd.", "Alembic Pharmaceuticals Ltd.",
                 "Redington Ltd.", "SEIL Energy India Ltd."):
        assert lk.by_group(name).found is False, name


def test_the_group_mechanism_can_be_switched_off(conn):
    _load(conn, [_rec("INE040A08001", "HDFC BANK LIMITED", "AAA",
                      sector="Private Sector Bank")], D1)
    from swp_planner.lookup import portfolio_credit as _PC
    rows = [dict(company="HDFC Securities Ltd.", company_id=1,
                 instrument_type="Commercial Paper", instrument_code=2,
                 asset_class="debt", percent=9.8, value=1e8, is_issuer=True,
                 expected_in_nsdl=False, is_securitised=False, seniority=None)]
    out = _PC.enrich(rows, CreditLookup(conn), groups=False)
    assert out[0]["rating"] is None and out[0]["gap"] == _PC.NOT_IN_NSDL


def test_a_merged_bond_issuer_is_answered_by_its_group(conn):
    """Tata Motors Finance and Tata Capital Financial Services are absent from
    NSDL -- merged into their parents -- and were sitting unresolved."""
    _load(conn, [_rec("INE306N08001", "TATA CAPITAL LIMITED", "AAA")], D1)
    from swp_planner.lookup import portfolio_credit as _PC
    rows = [dict(company="Tata Motors Finance Ltd.", company_id=1,
                 instrument_type="Debenture", instrument_code=3,
                 asset_class="debt", percent=13.5, value=1e8, is_issuer=True,
                 expected_in_nsdl=True, is_securitised=False, seniority=None)]
    out = _PC.enrich(rows, CreditLookup(conn))
    assert out[0]["rating"] == "AAA"
    assert out[0]["rating_source"] == "group_implied"
    assert out[0]["rating_trusted"] is False


# ── declared group ratings ──────────────────────────────────────────────────

@pytest.mark.parametrize("company,key,grade", [
    ("DSP Finance Pvt Ltd.", "DSP", "AA"),
    ("City Union Bank Ltd.", "CITY UNION BANK", "AA"),
    ("Mirae Asset Capital Markets (India) Pvt. Ltd.", "MIRAE", "AA"),
    ("Mirae Asset Sharekhan Financial Services Ltd.", "MIRAE", "AA"),
    ("Deutsche Investments (I) Pvt. Ltd.", "DEUTSCHE", "AA"),
    ("Balrampur Chini Mills Ltd.", "BALRAMPUR", "AA"),
    ("Nuvama Wealth Management Ltd.", "NUVAMA", "AA"),
    ("Nuvama Clearing Services Ltd.", "NUVAMA", "AA"),
    ("IIFL Capital Services Ltd", "IIFL", "AA-"),
    ("MashreqBank PSC", "MASHREQ", "AAA"),
    ("Avenue Supermarts Ltd.", "AVENUE SUPERMARTS", "AAA"),
])
def test_a_declared_group_rating_answers_where_nothing_can_be_inferred(
        conn, company, key, grade):
    """EVERY GROUP HERE FUNDS THROUGH COMMERCIAL PAPER, not listed bonds, so
    it is absent from the NSDL corporate bond file entirely -- which is why
    its members were in the review queue and why inference cannot reach
    them."""
    from swp_planner.fund_filters import groups as G
    assert G.group_of(company) == key
    _load(conn, [_rec("INE001A08001", "UNRELATED LIMITED", "AAA")], D1)
    r = CreditLookup(conn).by_group(company)
    assert r.found and r.rating == grade
    assert r.source == "group_declared"
    assert "declared for the" in r.reason


def test_mashreq_matches_a_name_written_as_one_word():
    """The source writes "MashreqBank PSC" with no space, and a trailing word
    boundary does not match inside it."""
    from swp_planner.fund_filters import groups as G
    assert G.group_of("MashreqBank PSC") == "MASHREQ"
    assert G.group_of("Mashreem Textiles Ltd") is None


def test_iifl_wealth_stays_with_360_one_not_iifl():
    """360 ONE was IIFL Wealth, and the two must not merge: 360 ONE has 62
    rated instruments in NSDL while IIFL Capital Services has none and takes a
    declared AA-.

    TWO THINGS KEEP THEM APART AND ONLY ONE IS LOAD-BEARING. 360 ONE is listed
    first and the first match wins, so ORDER alone is sufficient -- a
    falsifier removing the exclusion did not bite. The exclusion is
    belt-and-braces against someone reordering the registry, which is exactly
    the mistake the rating_source column exists to survive elsewhere. Both are
    asserted so a reorder fails here rather than silently."""
    from swp_planner.fund_filters import groups
    from swp_planner.fund_filters import groups as G
    assert G.group_of("IIFL Wealth Prime Ltd") == "360 ONE"
    assert G.group_of("IIFL Capital Services Ltd") == "IIFL"
    keys = [k for k, _i, _e, _a in groups._GROUPS]
    assert keys.index("360 ONE") < keys.index("IIFL"), "order is the guard"
    iifl = next(e for k, _i, e, _a in groups._GROUPS if k == "IIFL")
    assert iifl and "WEALTH" in iifl, "and the exclusion is the backup"


def test_an_inferred_rating_beats_a_declared_one(conn):
    """A rated sibling in NSDL is evidence; a declared grade is a judgement.
    Where both exist the evidence wins."""
    _load(conn, [_rec("INE001A08001", "IIFL FINANCE LIMITED", "AA+")], D1)
    r = CreditLookup(conn).by_group("IIFL Capital Services Ltd")
    assert r.source == "group_implied" and r.rating == "AA+"


def test_a_declared_group_grade_is_converted_for_money_market_paper(conn):
    """AA- on commercial paper is A1+, not AA-.

    I first wrote A2+ here and the code was right: the agencies' linkage puts
    everything from AAA down to AA- at the top of the short-term scale,
    because a year of liquidity is a different question from a decade of
    solvency. A2+ is where A- lands."""
    from swp_planner.lookup import portfolio_credit as _PC
    _load(conn, [_rec("INE001A08001", "UNRELATED LIMITED", "AAA")], D1)
    rows = [dict(company="IIFL Capital Services Ltd", company_id=1,
                 instrument_type="Commercial Paper", instrument_code=2,
                 asset_class="debt", percent=2.18, value=1e7, is_issuer=True,
                 expected_in_nsdl=False, is_securitised=False, seniority=None)]
    out = _PC.enrich(rows, CreditLookup(conn))
    assert out[0]["rating"] == "A1+"
    assert out[0]["rating_source"] == "group_short_term"
    assert out[0]["gap"] is None
    from swp_planner.lookup.portfolio_credit import short_term_grade
    assert short_term_grade("A-") == "A2+", "which is where A- lands"


@pytest.mark.parametrize("company,key,grade", [
    ("Panatone Finvest Ltd", "PANATONE", "AAA"),
    ("Birla Group Holdings Pvt. Ltd.", "BIRLA GROUP HOLDINGS", "AAA"),
    ("IDBI Capital Market Services Ltd.", "IDBI", "AA"),
    ("Infina Finance Pvt. Ltd.", "INFINA", "AA"),
    ("Sharekhan Ltd.", "SHAREKHAN", "AA"),
    ("CTBC Bank Ltd.", "CTBC", "AAA"),
])
def test_holding_companies_with_no_group_brand_in_the_name(
        conn, company, key, grade):
    """Panatone Finvest is Tata's holding vehicle for Tata Communications and
    Birla Group Holdings is Aditya Birla's; neither name reads as such."""
    from swp_planner.fund_filters import groups as G
    assert G.group_of(company) == key
    assert G.declared_rating(key)[0] == grade


@pytest.mark.parametrize("company", [
    "Birla Corporation Limited", "Birla Tyres Limited",
    "Himadri Birla Tyre Manufacturer Private Limited",
])
def test_the_birla_surname_is_shared_across_three_families(conn, company):
    """MEASURED AGAINST THE REAL NSDL FILE, which has all of them: BIRLA
    CORPORATION at AA and BIRLA TYRES are MP Birla and BK Birla, not Aditya
    Birla. A bare \\bBIRLA\\b would hand them a declared AAA -- the Reliance
    trap wearing a different coat.

    They are left ungrouped rather than mis-grouped: no rating is better than
    another family's."""
    from swp_planner.fund_filters import groups as G
    assert G.group_of(company) is None


def test_aditya_birla_still_infers_from_its_own_entities(conn):
    """Narrowing to BIRLA GROUP must not cost the group that does have rated
    members in NSDL."""
    from swp_planner.fund_filters import groups as G
    assert G.group_of("Aditya Birla Finance Ltd.") == "ADITYA BIRLA"
    _load(conn, [_rec("INE001A08001", "ADITYA BIRLA CAPITAL LIMITED", "AAA")],
          D1)
    r = CreditLookup(conn).by_group("Aditya Birla Finance Ltd.")
    assert r.found and r.source == "group_implied"


def test_mirae_asset_sharekhan_stays_with_mirae(conn):
    """Both are AA so the grade is the same either way, and the assignment
    should still be the one that reflects ownership."""
    from swp_planner.fund_filters import groups as G
    assert G.group_of("Mirae Asset Sharekhan Financial Services Ltd.") == \
        "MIRAE"
    assert G.group_of("Sharekhan Ltd.") == "SHAREKHAN"
