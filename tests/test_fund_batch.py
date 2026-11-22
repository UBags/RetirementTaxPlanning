# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""test_fund_batch.py -- the AMFI filter and the resumable batch runner.

NO NETWORK ANYWHERE. The NAV file is replayed from a literal and the fetch is
injected, so the whole run is exercised without touching AMFI or ETMoney.

THE FOUR THAT CARRY THE DESIGN:

  test_a_growth_fund_is_not_a_growth_option  -- "Nippon India Growth Fund"
      contains the option word permanently
  test_one_broken_fund_does_not_lose_the_rest -- fourteen hundred successes
      must not be thrown away by one timeout
  test_a_finished_fund_is_skipped_on_a_rerun  -- what makes the run resumable
  test_an_error_is_retried_but_a_no_match_is_not -- the reason status has more
      than two values
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from swp_planner.ingest import amfi_schemes as A  # noqa: E402
from swp_planner.ingest import fund_store as F  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "fund_batch", _ROOT / "scripts" / "fund_batch.py")
FB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(FB)

TODAY = dt.date(2026, 8, 7)

NAV_TEXT = """Scheme Code;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;\
Scheme Name;Net Asset Value;Date

Open Ended Schemes ( Debt Scheme - Banking and PSU Fund )

Bajaj Finserv Mutual Fund
119551;INF0GJ501234;-;Bajaj Finserv Banking and PSU Fund-Direct Plan- Growth;\
11.5432;06-Aug-2026
119552;INF0GJ501242;-;Bajaj Finserv Banking and PSU Fund-Direct Plan- IDCW;\
11.1;06-Aug-2026
119553;INF0GJ501259;-;Bajaj Finserv Banking and PSU Fund-Regular Plan- Growth;\
11.2;06-Aug-2026

Nippon India Mutual Fund
100123;INF204K01234;-;Nippon India Growth Fund - Direct Plan - Growth Option;\
3200.5;06-Aug-2026
100124;INF204K01242;-;Nippon India Growth Fund - Direct Plan - IDCW;\
900.1;06-Aug-2026

HDFC Mutual Fund
100200;INF179K01234;-;HDFC Liquid Fund - Growth Option - Direct Plan;\
4700.2;06-Aug-2026
100201;INF179K01242;-;HDFC Growth Opportunities Fund - Direct Plan - Growth;\
310.9;06-Aug-2026
"""


# ── the AMFI filter ─────────────────────────────────────────────────────────

def test_the_nav_file_parses_past_headers_and_amc_lines():
    rows = A.parse_nav_text(NAV_TEXT)
    assert len(rows) == 7
    assert rows[0]["scheme_code"] == 119551
    assert rows[0]["isin"] == "INF0GJ501234"


def test_one_row_survives_per_fund():
    """AMFI lists a fund once per plan and option; ETMoney has one portfolio
    per FUND, because plans differ only in expense ratio and NAV."""
    names = [f["name"] for f in A.fund_names(A.parse_nav_text(NAV_TEXT))]
    assert names == ["Bajaj Finserv Banking and PSU Fund",
                     "Nippon India Growth Fund", "HDFC Liquid Fund",
                     "HDFC Growth Opportunities Fund"]


def test_a_growth_fund_is_not_a_growth_option():
    """"Nippon India Growth Fund" carries the option word permanently. A
    regex tail-stripper reduced it to "Nippon India"; segments do not, because
    "Growth Fund" is not a plan segment -- "Fund" is not a plan word."""
    assert A.base_name(
        "Nippon India Growth Fund - Direct Plan - IDCW") == \
        "Nippon India Growth Fund"
    assert not A.is_direct_growth(
        "Nippon India Growth Fund - Direct Plan - IDCW")
    assert A.is_direct_growth(
        "Nippon India Growth Fund - Direct Plan - Growth Option")


def test_the_option_may_come_before_the_plan():
    """Requiring Growth to FOLLOW Direct in the string dropped "HDFC Liquid
    Fund - Growth Option - Direct Plan" entirely."""
    n = "HDFC Liquid Fund - Growth Option - Direct Plan"
    assert A.is_direct_growth(n) and A.base_name(n) == "HDFC Liquid Fund"


def test_regular_plans_are_excluded():
    assert not A.is_direct_growth("SBI Liquid Fund - Regular Plan - Growth")


@pytest.mark.parametrize("raw,base", [
    ("Bajaj Finserv Banking and PSU Fund-Direct Plan- Growth",
     "Bajaj Finserv Banking and PSU Fund"),
    ("Aditya Birla Sun Life Banking & PSU Debt Fund - DIRECT - GROWTH",
     "Aditya Birla Sun Life Banking & PSU Debt Fund"),
    ("Axis Nifty 50 Index Fund - Direct Plan - Growth",
     "Axis Nifty 50 Index Fund"),
])
def test_the_base_name_is_what_the_search_api_wants(raw, base):
    assert A.base_name(raw) == base


def test_the_order_is_stable_so_a_stopped_run_resumes_in_place():
    a = [f["name"] for f in A.fund_names(A.parse_nav_text(NAV_TEXT))]
    b = [f["name"] for f in A.fund_names(A.parse_nav_text(NAV_TEXT))]
    assert a == b


def test_every_amfi_code_that_produced_a_name_is_kept():
    funds = {f["name"]: f for f in A.fund_names(A.parse_nav_text(NAV_TEXT))}
    assert funds["Bajaj Finserv Banking and PSU Fund"]["scheme_codes"] == \
        [119551]


# ── the batch runner ────────────────────────────────────────────────────────

@pytest.fixture()
def conn(dsn):
    import psycopg2

    from swp_planner.ingest import store as S
    c = psycopg2.connect(dsn)
    with c.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS india CASCADE")
    c.commit()
    S.apply_schema(c)
    F.apply_schema(c)
    yield c
    c.close()


# A minimal but REAL page: the parser rejects anything without the var, which
# is correct and which a bare "<html>" stub does not survive.
PAGE = ('<script>function afterAllJSFilesLoaded() { var '
        'getAssetAllocationData = {"mfHoldingMap": {"2026-07-31": '
        '[{"id": 1, "cId": 5, "sId": 8, "astTypCd": 3, "hldBsTyp": 2, '
        '"astPer": 100.0, "astVal": 1000.0}]}, "mfCompanyDTOMap": '
        '{"5": {"company": "REC Ltd."}}, "mfSectorDTOMap": '
        '{"8": {"sector": "Financial"}}}; }</script>')


# EVERY STUB `resolve` MUST ACCEPT exclude_ids, as E.resolve does. The batch
# passes it on a retry after a colliding id, and a stub without it raises
# TypeError -- which the batch dutifully logs as an "error", making a fixture
# problem look like a data problem. That is the third time a lagging stub has
# cost a debugging round; the signature is now part of every one.
def _match(found=True, fund_id=1, label="A Fund", reason=None, slug="a"):
    return dict(found=found, fund_id=fund_id, label=label, score=99.0,
                seo_web_url=f"/mutual-funds/{slug}/{fund_id}",
                portfolio_url=f"https://www.etmoney.com/mutual-funds/{slug}"
                              f"/portfolio-details/{fund_id}", reason=reason,
                ambiguous=False, runner_up=None, margin=None,
                searched_as=None, ladder_steps=0)


def _loader(months=("2026-07-31",), boom=None, seen=None):
    """Stands in for fund_load.load_fund.

    **kwargs DELIBERATELY, so this stub does not have to be edited every time
    the real signature grows -- a stub that lags produces a TypeError the
    batch dutifully logs as an 'error', which looks like a data problem and is
    not. `seen` captures what was passed, so the tests that care about a
    specific argument can assert on it.
    """
    def load_fund(conn, payload, fund_id, label, lookup, months=None,
                  **kwargs):
        # EVERYTHING PAST `lookup` IS **kwargs, including the named arguments
        # the real signature declares. A first version listed amfi_name,
        # seo_web_url and score explicitly and forwarded only the rest, so
        # `seo_web_url` was silently dropped and a test asserting on it failed
        # against correct production code -- the stub lagging in a second way
        # after being fixed for the first.
        if seen is not None:
            seen.update(kwargs)
        if boom:
            raise boom
        F.upsert_fund(conn, fund_id, label,
                      **{k: v for k, v in kwargs.items()
                         if k not in ("score", "today")},
                      match_score=kwargs.get("score"),
                      as_on=kwargs.get("today"))
        return {m: "inserted" for m in (months or ["2026-07-31"])}
    return load_fund


def test_a_successful_fund_is_logged_ok(conn):
    row = FB.do_one(conn, "A Fund", None, _loader(), today=TODAY,
                    fetch=lambda u: PAGE, resolve=lambda n, exclude_ids=(): _match(),
                    latest_only=True)
    assert row["status"] == "ok" and row["fund_id"] == 1
    assert F.run_summary(conn, on=TODAY) == [
        dict(status="ok", funds=1, months=1)]


def test_a_fund_etmoney_does_not_list_is_no_match_not_an_error(conn):
    """Closed-end schemes, segregated portfolios and funds too new for a
    retail platform are in AMFI and not on ETMoney. That is expected, and
    counting it as an error would bury the real ones."""
    row = FB.do_one(conn, "Closed Fund", None, _loader(), today=TODAY,
                    resolve=lambda n, exclude_ids=(): _match(found=False,
                                             reason="no candidates"))
    assert row["status"] == "no_match" and row["reason"] == "no candidates"


def test_one_broken_fund_does_not_lose_the_rest(conn):
    """Fourteen hundred successes must not be thrown away by one timeout."""
    def flaky(name):
        if name == "Bad Fund":
            raise TimeoutError("read timed out")
        return _match()

    # Distinct ids per scheme: the collision guard is right to refuse two
    # names claiming one fund, so a fixture that reused an id was testing the
    # guard rather than the failure isolation it meant to test.
    ids = {"Good One": 1, "Good Two": 2}
    for n in ("Good One", "Bad Fund", "Good Two"):
        FB.do_one(conn, n, None, _loader(), today=TODAY,
                  fetch=lambda u: PAGE,
                  resolve=lambda name, _f=flaky, _i=ids: _f(name) if name
                  == "Bad Fund" else _match(fund_id=_i[name]),
                  latest_only=True)
    got = {s["status"]: s["funds"] for s in F.run_summary(conn, on=TODAY)}
    assert got == {"ok": 2, "error": 1}


def test_a_parse_failure_is_recorded_with_its_cause(conn):
    row = FB.do_one(conn, "A Fund", None, _loader(boom=ValueError("no var")),
                    today=TODAY, fetch=lambda u: PAGE,
                    resolve=lambda n, exclude_ids=(): _match())
    assert row["status"] == "error" and "ValueError" in row["reason"]
    assert row["fund_id"] == 1, "the id is kept so the failure can be chased"


def test_a_finished_fund_is_skipped_on_a_rerun(conn):
    """WHAT MAKES THE RUN RESUMABLE. Half an hour is long enough for a laptop
    lid, and restarting must cost nothing."""
    FB.do_one(conn, "A Fund", None, _loader(), today=TODAY,
              fetch=lambda u: PAGE, resolve=lambda n, exclude_ids=(): _match(),
              latest_only=True)
    assert F.already_done(conn, on=TODAY) == {"A Fund"}


def test_an_error_is_retried_but_a_no_match_is_not(conn):
    """A fund ETMoney does not list will not start being listed this
    afternoon; a timeout may well succeed next pass. That distinction is the
    reason status has more than two values."""
    FB.do_one(conn, "Timeout Fund", None, _loader(), today=TODAY,
              resolve=lambda n, exclude_ids=(): (_ for _ in ()).throw(TimeoutError("x")))
    FB.do_one(conn, "Missing Fund", None, _loader(), today=TODAY,
              resolve=lambda n, exclude_ids=(): _match(found=False, reason="no candidates"))
    done = F.already_done(conn, on=TODAY)
    assert "Missing Fund" in done
    assert "Timeout Fund" not in done, "an error must be retryable"


def test_yesterdays_log_does_not_skip_todays_run(conn):
    FB.do_one(conn, "A Fund", None, _loader(), today=dt.date(2026, 8, 6),
              fetch=lambda u: PAGE, resolve=lambda n, exclude_ids=(): _match(),
              latest_only=True)
    assert F.already_done(conn, on=TODAY) == set()


def test_the_failure_list_separates_the_kinds(conn):
    FB.do_one(conn, "Missing", None, _loader(), today=TODAY,
              resolve=lambda n, exclude_ids=(): _match(found=False, reason="no candidates"))
    FB.do_one(conn, "Broken", None, _loader(), today=TODAY,
              resolve=lambda n, exclude_ids=(): (_ for _ in ()).throw(OSError("reset")))
    kinds = {f["amfi_name"]: f["status"] for f in F.failures(conn, on=TODAY)}
    assert kinds == {"Missing": "no_match", "Broken": "error"}


# ── reapplying overrides ────────────────────────────────────────────────────

def _holding(company, pct=5.0, rating=None, gap="unresolved"):
    return dict(company_id=abs(hash(company)) % 10000, instrument_code=3,
                company=company, sector="Financial",
                instrument_type="Debenture", asset_class="debt", percent=pct,
                value=pct * 1e7, is_issuer=True, expected_in_nsdl=True,
                is_securitised=False, seniority=None, rating=rating,
                rating_confidence=None, borrower_category="Other Corporate",
                gap=gap)


def test_an_answered_rating_reaches_holdings_already_stored(conn):
    """THE GAP THIS CLOSES. Overrides were consulted at load time only, so a
    rating answered in the review queue did nothing until that fund was
    fetched again -- and refetching would not fix it either, because the
    snapshot hash correctly declines to rewrite an unchanged month."""
    F.upsert_fund(conn, 1, "A Fund", as_on=TODAY)
    F.load_month(conn, 1, dt.date(2026, 7, 31),
                 [_holding("SHIVSHAKTI SECURITISATION TRUST")], today=TODAY)
    F.set_override(conn, "Shivshakti Securitisation Trust", rating="AA",
                   source_note="CRISIL 2026-08-07")
    assert F.reapply_overrides(conn, on=TODAY) == 1
    with conn.cursor() as cur:
        cur.execute("SELECT rating, rating_band, gap FROM india.fund_holding")
        assert cur.fetchone() == ("AA", "AA", None)


def test_reapplying_never_overwrites_a_resolved_rating(conn):
    """A stale hand entry must not quietly outrank a rating agency."""
    F.upsert_fund(conn, 1, "A Fund", as_on=TODAY)
    F.load_month(conn, 1, dt.date(2026, 7, 31),
                 [_holding("REC LIMITED", rating="AAA", gap=None)],
                 today=TODAY)
    F.set_override(conn, "REC Limited", rating="BBB", source_note="a guess")
    assert F.reapply_overrides(conn, on=TODAY) == 0
    with conn.cursor() as cur:
        cur.execute("SELECT rating FROM india.fund_holding")
        assert cur.fetchone()[0] == "AAA"


def test_an_unratable_answer_fills_nothing(conn):
    F.upsert_fund(conn, 1, "A Fund", as_on=TODAY)
    F.load_month(conn, 1, dt.date(2026, 7, 31),
                 [_holding("OBSCURE TRUST")], today=TODAY)
    F.set_override(conn, "Obscure Trust", rating=None, unratable=True,
                   source_note="none exists")
    assert F.reapply_overrides(conn, on=TODAY) == 0


def test_one_answer_fills_every_fund_holding_that_name(conn):
    """Keyed on the name, so the queue shrinks rather than repeating."""
    for fid in (1, 2, 3):
        F.upsert_fund(conn, fid, f"Fund {fid}", as_on=TODAY)
        F.load_month(conn, fid, dt.date(2026, 7, 31),
                     [_holding("INDIA UNIVERSAL TRUST AL1")], today=TODAY)
    F.set_override(conn, "India Universal Trust AL1", rating="AAA",
                   source_note="CARE 2026-08-07")
    assert F.reapply_overrides(conn, on=TODAY) == 3


# ── renames, found on the first real batch run ──────────────────────────────

@pytest.mark.parametrize("raw,base", [
    ("Sundaram Banking & PSU Fund Formerly Known as Sundaram Banking & PSU "
     "Debt Fund - Direct Plan - Growth", "Sundaram Banking & PSU Fund"),
    ("Sundaram Banking & PSU Fund (Formerly Known as Sundaram Banking & PSU "
     "Debt Fund) - Direct Plan - Growth", "Sundaram Banking & PSU Fund"),
    ("HSBC Large Cap Fund (erstwhile L&T Large Cap Fund) - Direct Plan - "
     "Growth", "HSBC Large Cap Fund"),
])
def test_the_old_name_is_cut_after_a_rename(raw, base):
    """FOUND ON THE FIRST REAL BATCH RUN. AMFI keeps the previous name inside
    the scheme name, which does two kinds of damage: the search is handed a
    string twice as long as any real fund and scored 52.3 against the right
    answer, and two schemes differing only in the old name they mention fail
    to deduplicate -- Sundaram appeared twice in one run of 25."""
    assert A.base_name(raw) == base


def test_cutting_the_old_name_does_not_take_the_plan_suffix_with_it():
    """The plan marker comes AFTER the old name, so cutting at "formerly"
    across the whole string removes Direct-Plan-Growth too and the scheme
    stops being recognised as a growth option at all -- which is what the
    first version of this fix did."""
    raw = ("Sundaram Banking & PSU Fund Formerly Known as Sundaram Banking & "
           "PSU Debt Fund - Direct Plan - Growth")
    assert A.is_direct_growth(raw)


def test_two_spellings_of_a_rename_collapse_to_one_fund():
    # scheme_type is required now: closed-end schemes are filtered out before
    # anything else, and a row without one is not open-ended.
    rows = [dict(scheme_code=1, isin=None, scheme_type="Open Ended",
                 name="Sundaram Banking & PSU Fund Formerly Known as Sundaram"
                      " Banking & PSU Debt Fund - Direct Plan - Growth"),
            dict(scheme_code=2, isin=None, scheme_type="Open Ended",
                 name="Sundaram Banking & PSU Fund (Formerly Known as Sundaram"
                      " Banking & PSU Debt Fund) - Direct Plan - Growth")]
    assert [f["name"] for f in A.fund_names(rows)] == \
        ["Sundaram Banking & PSU Fund"]


# ── categories, and the exclusion words ─────────────────────────────────────

SECTIONED = """Scheme Code;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;\
Scheme Name;Net Asset Value;Date

Open Ended Schemes ( Debt Scheme - Banking and PSU Fund )

Bajaj Finserv Mutual Fund
119551;INF0GJ501234;-;Bajaj Finserv Banking and PSU Fund-Direct Plan- Growth;\
11.5;06-Aug-2026

Open Ended Schemes ( Equity Scheme - Large Cap Fund )

Nippon India Mutual Fund
100123;INF204K01234;-;Nippon India Growth Fund - Direct Plan - Growth Option;\
3200.5;06-Aug-2026
100199;INF204K01299;-;Nippon India Liquid Fund - Direct Plan - Daily IDCW;\
100.0;06-Aug-2026
100299;INF204K01399;-;ICICI Prudential Regular Savings Fund - Direct Plan - \
Growth;71.2;06-Aug-2026
100399;INF204K01499;-;SBI Magnum Income Fund - Direct Plan - Monthly IDCW;\
60.0;06-Aug-2026
"""


def test_the_category_comes_from_the_section_header():
    """THE ONLY PLACE IT APPEARS, and discarding it is what left fund_score
    with nothing to rank within -- a percentile among peers needs to know who
    the peers are, and every fund in one bucket is not a ranking."""
    rows = A.parse_nav_text(SECTIONED)
    by_code = {r["scheme_code"]: r for r in rows}
    assert by_code[119551]["category"] == "Debt Scheme - Banking and PSU Fund"
    assert by_code[100123]["category"] == "Equity Scheme - Large Cap Fund"
    assert by_code[119551]["scheme_type"] == "Open Ended"


def test_the_amc_is_captured_too():
    rows = A.parse_nav_text(SECTIONED)
    assert rows[0]["amc"] == "Bajaj Finserv Mutual Fund"
    assert rows[1]["amc"] == "Nippon India Mutual Fund"


def test_the_header_row_is_not_mistaken_for_an_amc():
    rows = A.parse_nav_text(SECTIONED)
    assert all(r["amc"] and "Scheme Code" not in r["amc"] for r in rows)


def test_the_category_survives_deduplication():
    funds = {f["name"]: f for f in A.fund_names(A.parse_nav_text(SECTIONED))}
    assert funds["Bajaj Finserv Banking and PSU Fund"]["category"] == \
        "Debt Scheme - Banking and PSU Fund"
    assert funds["Nippon India Growth Fund"]["category"] == \
        "Equity Scheme - Large Cap Fund"


def test_a_segregated_portfolio_is_excluded():
    """A side-pocket holding defaulted paper carries no NAV anyone can buy and
    is not listed on a retail platform. "UTI Bond Fund Segregated 17022020"
    scored 48.1 against every candidate -- the right answer, arrived at by two
    HTTP round trips instead of a word test."""
    name = "UTI Bond Fund Segregated 17022020 - Direct Plan - Growth"
    assert A.excluded_word(name) == "segregated"
    assert not A.is_direct_growth(name)


@pytest.mark.parametrize("word",
                         ["daily", "monthly", "weekly", "idcw", "segregated"])
def test_a_frequency_word_anywhere_excludes(word):
    """These four never appear in a fund's own name, so finding one anywhere
    settles it."""
    assert word in A.FREQUENCY_WORDS
    assert A.excluded_word(f"Some Fund - Direct Plan - {word.title()}") == word
    assert A.excluded_word(f"{word.title()} Something Fund - Direct - Growth")\
        == word


@pytest.mark.parametrize("name", [
    "ICICI Prudential Regular Savings Fund - Direct Plan - Growth",
    "Aditya Birla Sun Life Regular Savings Fund - Direct Plan - Growth",
])
def test_regular_in_a_product_name_is_kept(name):
    """THE CASUALTY OF THE FIRST VERSION. An unconditional rule dropped these
    -- real funds lost to a filter aimed at a plan variant. A plan marker
    comes AFTER the fund's name, and the fund's name ends in "fund"."""
    assert A.excluded_word(name) is None
    assert A.is_direct_growth(name)


@pytest.mark.parametrize("name", [
    "Some Debt Fund - Regular Plan - Growth",
    "Some Debt Fund - Regular - Growth",
    "ICICI Prudential Regular Savings Fund Regular Plan Growth",
])
def test_regular_after_the_last_fund_is_a_plan_marker(name):
    """The last one carries the word twice: only a marker following ALL
    occurrences of "fund" is a plan marker."""
    assert A.excluded_word(name) == "regular"


def test_the_last_fund_decides_not_the_first():
    """CONSTRUCTED, AND SAID SO. Every real name in the file contains "fund"
    once, where first and last are the same word -- so a falsifier swapping
    them did not bite, and this pins the choice on the only shape that
    distinguishes them: two "fund"s with "regular" between.

    "Multi Fund Solutions Regular Savings Fund - Direct Plan - Growth" is a
    product name throughout. Reading from the FIRST "fund" would call its
    third word a plan marker and drop the scheme."""
    name = "Multi Fund Solutions Regular Savings Fund - Direct Plan - Growth"
    assert A.excluded_word(name) is None
    assert A.is_direct_growth(name)
    assert A.base_name(name) == "Multi Fund Solutions Regular Savings Fund"


def test_with_no_fund_in_the_name_the_last_three_words_decide():
    """A handful of schemes are named "... Plan" or "... Scheme", and a plan
    marker sits at the end when it is present."""
    assert A.excluded_word("Some Scheme - Direct - Regular") == "regular"
    assert A.excluded_word("Regular Income Plan Direct Growth") is None


def test_separators_do_not_count_as_words():
    """"Fund - Regular - Growth" puts regular second of three, not third of
    five."""
    assert A.excluded_word("Some Scheme - Regular - Growth") == "regular"
    assert A.excluded_word("Some Scheme : Regular ; Growth") == "regular"


def test_the_casualty_report_omits_rows_that_would_have_failed_anyway():
    """A Monthly IDCW row was never going to pass the Direct-Growth rule, and
    listing it as a casualty would bury any case that matters."""
    rows = A.parse_nav_text(SECTIONED)
    names = {f["name"] for f in A.fund_names(rows)}
    assert "ICICI Prudential Regular Savings Fund" in names, \
        "the positional rule keeps it"
    assert A.excluded_by_word(rows) == {}


def test_the_category_reaches_the_loader(conn):
    """The batch reads it from AMFI and the store keeps it, so fund_score has
    a peer group. Nothing between them may drop it."""
    seen = {}
    scheme = dict(name="A Fund", category="Debt Scheme - Corporate Bond Fund",
                  scheme_type="Open Ended", amc="Some Mutual Fund",
                  scheme_codes=[119551])
    FB.do_one(conn, "A Fund", None, _loader(seen=seen), today=TODAY,
              fetch=lambda u: PAGE, resolve=lambda n, exclude_ids=(): _match(),
              latest_only=True, scheme=scheme)
    assert seen["category"] == "Debt Scheme - Corporate Bond Fund"
    assert seen["amc"] == "Some Mutual Fund"
    assert seen["amfi_scheme_code"] == 119551
    with conn.cursor() as cur:
        cur.execute("SELECT category, amc, amfi_scheme_code FROM india.fund")
        assert cur.fetchone() == ("Debt Scheme - Corporate Bond Fund",
                                  "Some Mutual Fund", 119551)


def test_a_later_load_that_knows_less_does_not_erase_the_category(conn):
    """A --file load has no AMFI name or category and must not blank them."""
    F.upsert_fund(conn, 1, "A Fund", as_on=TODAY, category="Equity Scheme",
                  amc="X MF")
    F.upsert_fund(conn, 1, "A Fund", as_on=TODAY)
    with conn.cursor() as cur:
        cur.execute("SELECT category, amc FROM india.fund WHERE fund_id=1")
        assert cur.fetchone() == ("Equity Scheme", "X MF")


# ── migrating a database that ran an earlier version ────────────────────────

OLD_SHAPE = """
CREATE SCHEMA IF NOT EXISTS india;
CREATE TABLE india.fund (
    fund_id INTEGER PRIMARY KEY, label TEXT NOT NULL, amfi_name TEXT,
    seo_web_url TEXT, match_score NUMERIC(6,2),
    first_seen_date DATE NOT NULL, last_fetched_date DATE NOT NULL);
CREATE TABLE india.fund_holding (
    fund_id INTEGER NOT NULL REFERENCES india.fund(fund_id),
    as_on DATE NOT NULL, company_id INTEGER NOT NULL,
    instrument_code INTEGER NOT NULL, company TEXT, sector TEXT,
    instrument_type TEXT, asset_class TEXT NOT NULL, percent NUMERIC(9,4),
    value NUMERIC(24,2), is_issuer BOOLEAN NOT NULL DEFAULT TRUE,
    expected_in_nsdl BOOLEAN NOT NULL DEFAULT FALSE,
    is_securitised BOOLEAN NOT NULL DEFAULT FALSE, seniority TEXT,
    rating TEXT, rating_band TEXT, rating_confidence NUMERIC(6,4),
    borrower_category TEXT, gap TEXT, last_updated_date DATE NOT NULL,
    PRIMARY KEY (fund_id, as_on, company_id, instrument_code));
INSERT INTO india.fund VALUES
    (1,'Old Fund',NULL,NULL,NULL,'2026-08-01','2026-08-01');
INSERT INTO india.fund_holding (fund_id, as_on, company_id, instrument_code,
    company, asset_class, percent, last_updated_date)
    VALUES (1,'2026-07-31',5,3,'REC Ltd.','debt',6.95,'2026-08-01');
"""


@pytest.fixture()
def old_conn(dsn):
    """A database as it stood before category, line_no and cap_exempt."""
    import psycopg2

    from swp_planner.ingest import store as S
    c = psycopg2.connect(dsn)
    with c.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS india CASCADE")
    c.commit()
    S.apply_schema(c)
    with c.cursor() as cur:
        cur.execute(OLD_SHAPE)
    c.commit()
    yield c
    c.close()


def _pk_columns(conn, table):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT a.attname FROM pg_constraint c "
            "JOIN LATERAL unnest(c.conkey) WITH ORDINALITY k(attnum, ord) "
            "  ON TRUE "
            "JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.attnum"
            " WHERE c.conrelid=%s::regclass AND c.contype='p' ORDER BY k.ord",
            (table,))
        return [r[0] for r in cur.fetchall()]


def test_an_existing_table_gains_the_new_columns(old_conn):
    """CREATE TABLE IF NOT EXISTS SILENTLY SKIPS AN EXISTING TABLE, so every
    column added after the first run is missing from any database that ran an
    earlier version. A batch over 2,314 funds died on its first fund because
    india.fund had no `category`."""
    F.apply_schema(old_conn)
    with old_conn.cursor() as cur:
        cur.execute("SELECT category, scheme_type, amc, amfi_scheme_code "
                    "FROM india.fund WHERE fund_id=1")
        assert cur.fetchone() == (None, None, None, None)


def test_the_holdings_primary_key_is_rebuilt_with_line_no(old_conn):
    """One issuer can hold two lines of the same instrument type in one month
    -- HUDCO does, at 6.74% and 2.78%."""
    assert "line_no" not in _pk_columns(old_conn, "india.fund_holding")
    F.apply_schema(old_conn)
    assert _pk_columns(old_conn, "india.fund_holding") == [
        "fund_id", "as_on", "company_id", "instrument_code", "line_no"]


def test_existing_rows_survive_the_migration(old_conn):
    """line_no defaults to 0, which is correct for every stored row: the OLD
    key forbade two lines of one instrument for one issuer, so nothing can
    collide at 0."""
    F.apply_schema(old_conn)
    with old_conn.cursor() as cur:
        cur.execute("SELECT company, line_no FROM india.fund_holding")
        assert cur.fetchone() == ("REC Ltd.", 0)


def test_the_views_see_the_migrated_columns(old_conn):
    """Both exposure views use SELECT e.*, which PostgreSQL expands at CREATE
    time -- so a view defined before the ALTERs would freeze the old column
    list and never show cap_exempt however often the file is re-run."""
    F.apply_schema(old_conn)
    with old_conn.cursor() as cur:
        cur.execute("SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='india' AND "
                    "table_name='issuer_cap_breach' AND "
                    "column_name IN ('cap_exempt','exempt_reason')")
        assert len(cur.fetchall()) == 2


def test_the_migration_is_idempotent(old_conn):
    for _ in range(3):
        F.apply_schema(old_conn)
    assert _pk_columns(old_conn, "india.fund_holding")[-1] == "line_no"


def test_a_fund_that_fails_does_not_poison_the_next_one(conn):
    """ROLL BACK BEFORE LOGGING. A psycopg2 error inside load_fund leaves the
    connection in an aborted transaction, and every later statement on it --
    including the one recording the failure -- raises InFailedSqlTransaction.
    The run then dies on the FIRST bad fund, which is exactly what "failures
    are rows, not exceptions" was meant to prevent."""
    def breaks_the_transaction(conn_, payload, fund_id, label, lookup,
                               **kwargs):
        with conn_.cursor() as cur:
            cur.execute("SELECT * FROM india.no_such_table")

    FB.do_one(conn, "Broken Fund", None, breaks_the_transaction, today=TODAY,
              fetch=lambda u: PAGE, resolve=lambda n, exclude_ids=(): _match(),
              latest_only=True)
    FB.do_one(conn, "Good Fund", None, _loader(), today=TODAY,
              fetch=lambda u: PAGE, resolve=lambda n, exclude_ids=(): _match(fund_id=2),
              latest_only=True)
    got = {s["status"]: s["funds"] for s in F.run_summary(conn, on=TODAY)}
    assert got == {"error": 1, "ok": 1}
    assert "UndefinedTable" in F.failures(conn, on=TODAY)[0]["reason"]


# ── one ETMoney fund is one AMFI fund ───────────────────────────────────────

def test_a_second_scheme_cannot_claim_an_id_already_resolved(conn):
    """MEASURED ON A REAL RUN: four schemes took a neighbour's id at scores
    ABOVE the 88 floor. TRUSTMF Flexi Cap took Taurus's 44630 at 88.4, UTI
    Short Duration took Union's at 91.7, ICICI Prudential Short Term took the
    Gilt fund's at 92.8. Raising the floor would lose legitimate matches;
    refusing a second claim costs nothing and catches all four.

    The symptom was visible and nearly missed: the second fund reported "0mo"
    because every month it wrote was already there."""
    FB.do_one(conn, "Taurus Flexi Cap Fund", None, _loader(), today=TODAY,
              fetch=lambda u: PAGE, resolve=lambda n, exclude_ids=(): _match(fund_id=44630),
              latest_only=True)
    row = FB.do_one(conn, "TRUSTMF Flexi Cap Fund", None, _loader(),
                    today=TODAY, fetch=lambda u: PAGE,
                    resolve=lambda n, exclude_ids=(): _match(fund_id=44630), latest_only=True)
    assert row["status"] == "conflict"
    assert "Taurus Flexi Cap Fund" in row["reason"]
    got = {s["status"]: s["funds"] for s in F.run_summary(conn, on=TODAY)}
    assert got == {"ok": 1, "conflict": 1}


def test_the_same_scheme_may_reclaim_its_own_id(conn):
    """A rerun is not a collision."""
    for _ in range(2):
        row = FB.do_one(conn, "Taurus Flexi Cap Fund", None, _loader(),
                        today=TODAY, fetch=lambda u: PAGE,
                        resolve=lambda n, exclude_ids=(): _match(fund_id=44630),
                        latest_only=True)
    assert row["status"] == "ok"


def test_a_conflict_is_not_retried(conn):
    """Retrying produces the same collision and the same wasted round trips."""
    FB.do_one(conn, "Taurus Flexi Cap Fund", None, _loader(), today=TODAY,
              fetch=lambda u: PAGE, resolve=lambda n, exclude_ids=(): _match(fund_id=44630),
              latest_only=True)
    FB.do_one(conn, "TRUSTMF Flexi Cap Fund", None, _loader(), today=TODAY,
              fetch=lambda u: PAGE, resolve=lambda n, exclude_ids=(): _match(fund_id=44630),
              latest_only=True)
    assert "TRUSTMF Flexi Cap Fund" in F.already_done(conn, on=TODAY)


@pytest.mark.parametrize("name", [
    "JM Liquid Fund Unclaimed Dividend IEF - Direct Plan - Growth",
    "Canara Robeco Liquid Fund Unclaimed Redemption - Direct - Growth",
    "JM Liquid Fund Withheld Brokerage I.E.F. - Direct Plan - Growth",
])
def test_unclaimed_and_withheld_holding_accounts_are_excluded(name):
    """IEPF holding accounts for money nobody collected. Seven appeared in one
    run of 370, each costing two HTTP round trips to be told no."""
    assert A.excluded_word(name) in ("unclaimed", "withheld")
    assert not A.is_direct_growth(name)


# ── the batch resolves to the Direct plan before storing ────────────────────

REGULAR_MAIN_PAGE = (
    '<html><script>var schemeData={'
    'counterpartSchemeUrl:"/mutual-funds/uti-fund-direct-growth/15530",'
    'counterpartSchemeId:"15530",'
    'currentSchemeUrl:"/mutual-funds/uti-fund/9467",'
    'currentSchemeId:"9467"}</script></html>')


def _two_page_fetch(seen):
    """Serves the main page for the fund URL and the payload for the
    portfolio URL, recording what was asked for."""
    def fetch(url):
        seen.append(url)
        return REGULAR_MAIN_PAGE if "portfolio-details" not in url else PAGE
    return fetch


def test_the_batch_stores_the_direct_plan_id_not_the_regular_one(conn):
    """The search returns whichever plan it feels like, usually Regular. Both
    plans hold the same portfolio, but they do not share an expense ratio or a
    NAV -- and those are what a retirement model runs on."""
    seen = []
    row = FB.do_one(conn, "UTI Fund", None, _loader(), today=TODAY,
                    fetch=_two_page_fetch(seen),
                    resolve=lambda n, exclude_ids=(): _match(fund_id=9467,
                                             slug="uti-fund"),
                    latest_only=True)
    assert row["status"] == "ok"
    assert row["fund_id"] == 15530, "the Direct id, not the Regular 9467"
    assert any("portfolio-details/15530" in u for u in seen)
    with conn.cursor() as cur:
        cur.execute("SELECT fund_id, seo_web_url FROM india.fund")
        fid, url = cur.fetchone()
    assert fid == 15530 and "direct-growth" in url


def test_a_search_that_already_landed_on_direct_makes_no_extra_request(conn):
    """One hop, not two, when the answer is already in hand."""
    seen = []
    FB.do_one(conn, "UTI Fund", None, _loader(), today=TODAY,
              fetch=_two_page_fetch(seen),
              resolve=lambda n, exclude_ids=(): _match(fund_id=15530,
                                       slug="uti-fund-direct-growth"),
              latest_only=True)
    assert all("portfolio-details" in u for u in seen), \
        "no main-page fetch was needed"


def test_a_direct_id_already_claimed_is_a_conflict(conn):
    """The collision check has to run again on the id ACTUALLY used: two
    schemes can reach one Direct id from two different Regular ones."""
    seen = []
    FB.do_one(conn, "First Scheme", None, _loader(), today=TODAY,
              fetch=_two_page_fetch(seen),
              resolve=lambda n, exclude_ids=(): _match(fund_id=9467, slug="uti-fund"),
              latest_only=True)
    row = FB.do_one(conn, "Second Scheme", None, _loader(), today=TODAY,
                    fetch=_two_page_fetch(seen),
                    resolve=lambda n, exclude_ids=(): _match(fund_id=1111, slug="other-fund"),
                    latest_only=True)
    assert row["status"] == "conflict"
    assert "First Scheme" in row["reason"] and "15530" in row["reason"]


# ── the first full-universe run, and what it cost ───────────────────────────

CLOSED_END = """Scheme Code;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;\
Scheme Name;Net Asset Value;Date

Open Ended Schemes ( Debt Scheme - Liquid Fund )

HDFC Mutual Fund
100001;INF1;-;HDFC Liquid Fund - Direct Plan - Growth;100.0;06-Aug-2026

Close Ended Schemes ( Debt Scheme - Fixed Maturity Plan )

HDFC Mutual Fund
100002;INF2;-;HDFC FMP 1100D April 2019 1 - Direct Plan - Growth;12.0;06-Aug-2026

Interval Fund Schemes ( Debt Scheme - Interval Fund )

UTI Mutual Fund
100003;INF3;-;UTI Quarterly Interval Fund I - Direct Plan - Growth;15.0;06-Aug-2026
"""


def test_closed_end_and_interval_schemes_are_dropped_before_any_request():
    """MEASURED ON THE FIRST FULL RUN: of 2,295 names attempted, roughly 690
    were Fixed Maturity Plans, Fixed Horizon Funds, Interval Funds and their
    relatives. Each cost two HTTP round trips to confirm that a scheme which
    matured in 2019 is not for sale -- about 35 minutes of a two-hour run.

    The section header says which is which, so this is a filter, not a
    guess."""
    names = [f["name"] for f in A.fund_names(A.parse_nav_text(CLOSED_END))]
    assert names == ["HDFC Liquid Fund"]


def test_the_filter_can_be_widened_deliberately():
    """Passing the types explicitly is how someone would include closed-end
    schemes for a different purpose, rather than editing the module."""
    names = {f["name"] for f in A.fund_names(
        A.parse_nav_text(CLOSED_END), scheme_types=None)}
    assert len(names) == 3


def test_a_rejected_query_does_not_abort_the_whole_ladder():
    """SIXTY-ONE FUNDS DIED HERE IN ONE RUN. The endpoint 400s on some
    punctuation -- "CRISIL IBX 50:50 Gilt Plus SDL", "Axis Children's Fund",
    "Equity Hybrid'95" -- and the full name is the rung most likely to carry
    it. The exception took the whole ladder with it, so a later rung with the
    punctuation stripped was never tried."""
    import io
    import json as _json

    from swp_planner.ingest import etmoney as _E

    hit = {"id": 7, "label": "Axis Childrens Fund",
           "name": "Axis Childrens Fund Regular-Growth",
           "seoWebUrl": "/mutual-funds/axis-childrens-fund/7",
           "securityType": 1}

    class _Fussy:
        def __init__(self):
            self.queries = []

        def __call__(self, req, timeout=None):
            import urllib.parse
            q = urllib.parse.parse_qs(req.data.decode())["query"][0]
            self.queries.append(q)
            if "'" in q or ":" in q:
                raise urllib.error.HTTPError(
                    "u", 400, "Bad Request", {}, None)

            class _Resp(io.BytesIO):
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *a):
                    self_inner.close()

            return _Resp(_json.dumps(
                {"result": {"success": True}, "mfList": [hit]}).encode())

    op = _Fussy()
    m = _E.resolve("Axis Children's Fund", opener=op)
    assert m.found and m.fund_id == 7
    assert "'" in op.queries[0], "the full name was tried first"
    assert len(op.queries) > 1, "and the ladder carried on past the 400"


def test_every_rung_rejected_is_reported_as_such():
    """A punctuation problem and a genuinely absent scheme need different
    responses, and conflating them sends one to the other's queue."""
    import urllib.error

    from swp_planner.ingest import etmoney as _E

    def always_400(req, timeout=None):
        raise urllib.error.HTTPError("u", 400, "Bad Request", {}, None)

    m = _E.resolve("A:B C'D", opener=always_400)
    assert m.found is False
    assert "rejected" in m.reason and "400" in m.reason


# ── the third full run: three defects it exposed ────────────────────────────

def test_the_queue_and_override_arguments_exist():
    """A REGRESSION THAT SURVIVED TWO RUNS. A later rewrite of fund_batch.py
    dropped the review-queue arguments, so refresh.sh's final step failed with
    "unrecognized arguments: --queue" and the manual queue became unreachable
    from the pipeline. The web page still worked, which is why nobody
    noticed."""
    import argparse
    import contextlib
    import io
    with contextlib.redirect_stderr(io.StringIO()), \
            pytest.raises(SystemExit):
        FB.main(["--help"])
    parser_args = {a for a in dir(FB)}
    assert "main" in parser_args
    # Parse each flag for real rather than grepping the source.
    for flag in ("--queue", "--unratable"):
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                FB.main([flag, "--dsn", "postgres://nonexistent/none"])
            except (SystemExit, argparse.ArgumentError):
                raise AssertionError(f"{flag} is not accepted")
            except Exception:
                pass          # it got past argument parsing, which is the test


def test_a_colliding_id_is_retried_with_the_next_candidate(conn):
    """TWELVE FUNDS IN A FULL RUN HIT THIS AND WERE DROPPED ENTIRELY. An id
    belonging to another scheme does not mean this scheme is absent -- it
    means the top candidate was wrong, and the second is usually right."""
    seen = []

    def resolve(name, exclude_ids=()):
        seen.append(tuple(exclude_ids))
        # 44630 is Taurus's; 44999 is the real TRUSTMF fund.
        return _match(fund_id=44999 if 44630 in exclude_ids else 44630,
                      slug="trustmf-flexi-cap-fund-direct-growth")

    FB.do_one(conn, "Taurus Flexi Cap Fund", None, _loader(), today=TODAY,
              fetch=lambda u: PAGE,
              resolve=lambda n, exclude_ids=(): _match(
                  fund_id=44630, slug="taurus-flexi-cap-fund-direct-growth"),
              latest_only=True)
    row = FB.do_one(conn, "TRUSTMF Flexi Cap Fund", None, _loader(),
                    today=TODAY, fetch=lambda u: PAGE, resolve=resolve,
                    latest_only=True)
    assert row["status"] == "ok" and row["fund_id"] == 44999
    assert seen == [(), (44630,)], "one retry, excluding the taken id"


def test_the_retry_is_bounded(conn):
    """A chain of collisions must terminate rather than loop."""
    FB.do_one(conn, "First", None, _loader(), today=TODAY,
              fetch=lambda u: PAGE,
              resolve=lambda n, exclude_ids=(): _match(fund_id=1, slug="a"),
              latest_only=True)
    row = FB.do_one(conn, "Second", None, _loader(), today=TODAY,
                    fetch=lambda u: PAGE,
                    resolve=lambda n, exclude_ids=(): _match(fund_id=1,
                                                             slug="a"),
                    latest_only=True)
    assert row["status"] == "conflict"


def test_pick_can_be_told_to_skip_an_id():
    from swp_planner.ingest import etmoney as _E
    entries = [{"id": 1, "label": "Alpha Fund", "seoWebUrl": "/x/1"},
               {"id": 2, "label": "Alpha Fund", "seoWebUrl": "/x/2"}]
    assert _E.pick("Alpha Fund", entries).fund_id == 1
    assert _E.pick("Alpha Fund", entries, exclude_ids=(1,)).fund_id == 2
    assert _E.pick("Alpha Fund", entries, exclude_ids=(1, 2)).found is False


def test_stored_holdings_are_reclassified_when_the_rules_change(conn):
    """A MONTH IS ONLY REWRITTEN WHEN ITS CONTENT CHANGES -- right for a
    twice-weekly job, wrong when the classification RULES change. 31,291
    months written before fund-of-fund holdings were recognised kept
    is_issuer TRUE, and the review queue asked a person for the credit rating
    of a gold ETF."""
    F.upsert_fund(conn, 1, "A FoF", as_on=TODAY)
    rows = [dict(company_id=1, instrument_code=13, company="SBI Gold ETF",
                 sector="Other", instrument_type=None, asset_class="other",
                 percent=100.09, value=1e8, is_issuer=True,
                 expected_in_nsdl=False, is_securitised=False, seniority=None,
                 rating=None, rating_confidence=None,
                 borrower_category="Other Corporate", gap="not_in_nsdl"),
            dict(company_id=2, instrument_code=3, company="REC Ltd.",
                 sector="Financial", instrument_type="Debenture",
                 asset_class="debt", percent=5.0, value=1e7, is_issuer=True,
                 expected_in_nsdl=True, is_securitised=False, seniority=None,
                 rating="AAA", rating_confidence=0.99,
                 borrower_category="NBFC", gap=None)]
    F.load_month(conn, 1, dt.date(2026, 7, 31), rows, today=TODAY)
    F.load_issuer_exposures(conn, 1, dt.date(2026, 7, 31), rows, today=TODAY)

    assert F.reclassify_non_issuers(conn, on=TODAY) == 1
    with conn.cursor() as cur:
        cur.execute("SELECT company, is_issuer, gap FROM india.fund_holding "
                    "ORDER BY company")
        assert cur.fetchall() == [("REC Ltd.", True, None),
                                  ("SBI Gold ETF", False, "not_an_issuer")]
        cur.execute("SELECT company FROM india.fund_issuer_exposure")
        assert [r[0] for r in cur.fetchall()] == ["REC Ltd."], \
            "the derived exposure has to follow"


def test_the_sql_and_python_non_issuer_rules_agree(conn):
    """The reclassifier duplicates etmoney_portfolio's patterns in SQL rather
    than refetching 1,392 pages. This is what stops the two drifting."""
    from swp_planner.ingest import etmoney_portfolio as P
    names = ["SBI Gold ETF", "Mirae Asset Silver ETF - Growth",
             "Bandhan Gold ETF-Growth", "Quantum Nifty 50 ETF-IDCW",
             "Nippon India Silver ETF FOF", "Others", "TREPS",
             "REC Ltd.", "Growth Securities Ltd", "Bharat Forge Ltd"]
    F.upsert_fund(conn, 1, "A Fund", as_on=TODAY)
    F.load_month(conn, 1, dt.date(2026, 7, 31), [
        dict(company_id=i, instrument_code=3, company=n, sector="X",
             instrument_type="Debenture", asset_class="debt", percent=1.0,
             value=1e6, is_issuer=True, expected_in_nsdl=True,
             is_securitised=False, seniority=None, rating=None,
             rating_confidence=None, borrower_category="Other Corporate",
             gap=None) for i, n in enumerate(names)], today=TODAY)
    F.reclassify_non_issuers(conn, on=TODAY)
    with conn.cursor() as cur:
        cur.execute("SELECT company, is_issuer FROM india.fund_holding")
        sql_says = dict(cur.fetchall())
    for n in names:
        python_says = not (
            " ".join(n.upper().split()) in P.NON_ISSUER_COMPANIES
            or P.FUND_UNIT_RE.search(n))
        assert sql_says[n] == python_says, f"{n}: SQL and Python disagree"


def test_the_reclassifier_clears_stored_fof_holdings(conn):
    """The queue showed 100 names of which most were fund units, because the
    rows were written before the rule existed and a month is only rewritten
    when its CONTENT changes. This is the step that carries a rule change to
    31,291 stored months."""
    F.upsert_fund(conn, 1, "An FoF", as_on=TODAY,
                  category="Other Scheme - FoF Overseas")
    F.upsert_fund(conn, 2, "A Debt Fund", as_on=TODAY,
                  category="Debt Scheme - Corporate Bond Fund")

    def h(cid, company, securitised=False):
        return dict(company_id=cid, instrument_code=3, company=company,
                    sector="X", instrument_type="Debenture",
                    asset_class="debt", percent=50.0, value=1e7,
                    is_issuer=True, expected_in_nsdl=True,
                    is_securitised=securitised, seniority=None, rating=None,
                    rating_confidence=None, borrower_category="Other Corporate",
                    gap="not_in_nsdl")

    F.load_month(conn, 1, dt.date(2026, 7, 31),
                 [h(1, "Julius Baer Global Excellence Equity USD C"),
                  h(2, "GMO Quality Investment Fund")], today=TODAY)
    F.load_month(conn, 2, dt.date(2026, 7, 31),
                 [h(3, "REC Ltd."),
                  h(4, "India Universal Trust Fund AL1", securitised=True)],
                 today=TODAY)

    F.reclassify_non_issuers(conn, on=TODAY)
    with conn.cursor() as cur:
        cur.execute("SELECT company, is_issuer FROM india.fund_holding "
                    "ORDER BY company")
        got = dict(cur.fetchall())
    assert got["Julius Baer Global Excellence Equity USD C"] is False
    assert got["GMO Quality Investment Fund"] is False
    assert got["REC Ltd."] is True
    # The name is chosen because it COLLIDES with the fund-unit rule (it
    # contains "Fund"); the guard is what keeps it an issuer. Observed trust
    # names do not collide, so this is the constructed case that tests it.
    assert got["India Universal Trust Fund AL1"] is True, \
        "a securitisation trust is an issuer and needs a person"


def test_stored_government_holdings_are_answered_by_rule(conn):
    """The queue showed GOI at 76.58% across 27 funds. Rows written before the
    rule existed keep gap='unresolved', and a month is only rewritten when its
    content changes -- so this is the step that carries the rule to them."""
    F.upsert_fund(conn, 1, "A Gilt Fund", as_on=TODAY,
                  category="Debt Scheme - Gilt Fund")

    def h(cid, company):
        return dict(company_id=cid, instrument_code=1, company=company,
                    sector="Sovereign", instrument_type="Bonds",
                    asset_class="debt", percent=25.0, value=1e8,
                    is_issuer=True, expected_in_nsdl=True,
                    is_securitised=False, seniority=None, rating=None,
                    rating_confidence=None, borrower_category="Other Corporate",
                    gap="unresolved")

    F.load_month(conn, 1, dt.date(2026, 7, 31),
                 [h(1, "GOI"), h(2, "Government of India"),
                  h(3, "GOI Bonds Trading Ltd"), h(4, "REC Ltd.")],
                 today=TODAY)
    assert F.reclassify_sovereign(conn, on=TODAY) == 2
    with conn.cursor() as cur:
        cur.execute("SELECT company, rating, borrower_category, gap "
                    "FROM india.fund_holding ORDER BY company")
        got = {r[0]: r[1:] for r in cur.fetchall()}
    assert got["GOI"] == ("SOV", "GoI", None)
    assert got["Government of India"] == ("SOV", "GoI", None)
    assert got["GOI Bonds Trading Ltd"][0] is None, "a company, not the state"
    assert got["REC Ltd."][0] is None


def test_a_resolved_rating_is_not_overwritten_by_the_sovereign_rule(conn):
    F.upsert_fund(conn, 1, "A Fund", as_on=TODAY)
    F.load_month(conn, 1, dt.date(2026, 7, 31), [
        dict(company_id=1, instrument_code=1, company="Government of India",
             sector="Sovereign", instrument_type="Bonds", asset_class="debt",
             percent=10.0, value=1e8, is_issuer=True, expected_in_nsdl=True,
             is_securitised=False, seniority=None, rating="AAA",
             rating_confidence=0.99, borrower_category="GoI", gap=None)],
        today=TODAY)
    assert F.reclassify_sovereign(conn, on=TODAY) == 0


# ── re-enrichment: rule changes must reach rows already stored ──────────────

def test_a_rule_change_reaches_holdings_already_written(conn):
    """THE THIRD TIME THIS HAS BITTEN. `load_month` rewrites a month only when
    its SOURCE content hash changes -- right for a twice-weekly job, and wrong
    every time a RULE changes, because a full re-run finds the months
    unchanged and keeps their old classification.

    Canara Bank stayed in the review queue across 112 funds after the rule
    that answers it had already shipped."""
    from swp_planner.lookup import portfolio_credit as PC
    F.upsert_fund(conn, 1, "A Debt Fund", as_on=TODAY,
                  category="Debt Scheme - Liquid Fund")
    stale = dict(company_id=1, instrument_code=2, company="Canara Bank",
                 sector="Financial",
                 instrument_type="Certificate of Deposit",
                 asset_class="debt", percent=16.45, value=1e8,
                 is_issuer=True, expected_in_nsdl=False, is_securitised=False,
                 seniority=None, rating=None, rating_confidence=None,
                 borrower_category="Other Corporate", gap="not_in_nsdl")
    F.load_month(conn, 1, dt.date(2026, 7, 31), [stale], today=TODAY)

    class _Lookup:
        def by_name(self, name):
            return dict(found=True, rating="AAA", confidence=0.99,
                        source="name", borrower_category="PSU Bank",
                        issuer_has_subordinated=False, ambiguous=False,
                        runner_up=None)

    rep = F.reenrich(conn, PC.enrich, _Lookup(), on=TODAY)
    assert rep["rows_changed"] == 1
    with conn.cursor() as cur:
        cur.execute("SELECT rating, rating_band, gap, borrower_category "
                    "FROM india.fund_holding")
        assert cur.fetchone() == ("A1+", "AAA", None, "PSU Bank")


def test_re_enrichment_is_keyed_on_distinct_classifications(conn):
    """Seven hundred thousand holdings reduce to a few thousand distinct
    (company, instrument, class) combinations, so this is a few thousand
    lookups rather than a row-by-row pass."""
    from swp_planner.lookup import portfolio_credit as PC
    for fid in (1, 2, 3):
        F.upsert_fund(conn, fid, f"Fund {fid}", as_on=TODAY)
        F.load_month(conn, fid, dt.date(2026, 7, 31), [
            dict(company_id=1, instrument_code=2, company="Canara Bank",
                 sector="Financial",
                 instrument_type="Certificate of Deposit",
                 asset_class="debt", percent=10.0, value=1e7, is_issuer=True,
                 expected_in_nsdl=False, is_securitised=False, seniority=None,
                 rating=None, rating_confidence=None,
                 borrower_category="Other Corporate", gap="not_in_nsdl")],
            today=TODAY)

    class _Lookup:
        def __init__(self):
            self.calls = 0

        def by_name(self, name):
            self.calls += 1
            return dict(found=True, rating="AAA", confidence=0.99,
                        source="name", borrower_category="PSU Bank",
                        issuer_has_subordinated=False, ambiguous=False,
                        runner_up=None)

    lk = _Lookup()
    rep = F.reenrich(conn, PC.enrich, lk, on=TODAY)
    assert rep["keys"] == 1, "one distinct classification across three funds"
    assert lk.calls == 1, "one lookup, not three"
    assert rep["rows_changed"] == 3, "and all three rows updated"


def test_re_enrichment_leaves_rows_that_did_not_change(conn):
    """It must be safe to run every refresh, which means writing nothing when
    the rules produce what is already stored."""
    from swp_planner.lookup import portfolio_credit as PC
    F.upsert_fund(conn, 1, "A Fund", as_on=TODAY)
    F.load_month(conn, 1, dt.date(2026, 7, 31), [
        dict(company_id=1, instrument_code=3, company="REC Ltd.",
             sector="Financial", instrument_type="Debenture",
             asset_class="debt", percent=5.0, value=1e7, is_issuer=True,
             expected_in_nsdl=True, is_securitised=False, seniority=None,
             rating="AAA", rating_confidence=0.99,
             borrower_category="NBFC", gap=None)], today=TODAY)

    class _Lookup:
        def by_name(self, name):
            return dict(found=True, rating="AAA", confidence=0.99,
                        source="name", borrower_category="NBFC",
                        issuer_has_subordinated=False, ambiguous=False,
                        runner_up=None)

    assert F.reenrich(conn, PC.enrich, _Lookup(), on=TODAY)["rows_changed"] == 0


# ── hand-entered ratings, and the scale that still applies to them ─────────

def test_an_override_is_converted_for_money_market_paper(conn):
    """AN OVERRIDE OF AAA ON A CERTIFICATE OF DEPOSIT MUST LAND AS A1+, for
    the same reason NSDL's long-term grade does: copying it across is the
    category error that produced 86.0% AAA against a published 77.31%. One
    entry therefore serves an issuer holding both kinds of paper."""
    F.upsert_fund(conn, 1, "A Fund", as_on=TODAY)

    def h(cid, company, instrument):
        return dict(company_id=cid, instrument_code=2, company=company,
                    sector="Fin", instrument_type=instrument,
                    asset_class="debt", percent=5.0, value=1e7,
                    is_issuer=True, expected_in_nsdl=False,
                    is_securitised=False, seniority=None, rating=None,
                    rating_confidence=None,
                    borrower_category="Other Corporate", gap="not_in_nsdl")

    F.load_month(conn, 1, dt.date(2026, 7, 31), [
        h(1, "Karur Vysya Bank Ltd.", "Certificate of Deposit"),
        h(2, "Karur Vysya Bank Ltd.", "Debenture"),
        h(3, "Titan Company Ltd.", "Commercial Paper")], today=TODAY)
    for name in ("Karur Vysya Bank Ltd.", "Titan Company Ltd."):
        F.set_override(conn, name, rating="AAA",
                       source_note="user-supplied 2026-08-07", entered_by="u")
    assert F.reapply_overrides(conn, on=TODAY) == 3
    with conn.cursor() as cur:
        cur.execute("SELECT instrument_type, rating, rating_band "
                    "FROM india.fund_holding ORDER BY instrument_type")
        got = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    assert got["Certificate of Deposit"] == ("A1+", "AAA")
    assert got["Commercial Paper"] == ("A1+", "AAA")
    assert got["Debenture"] == ("AAA", "AAA"), "the long grade stands here"


def test_the_band_follows_the_converted_grade(conn):
    """An A override on a CP becomes A1, which bands AA -- not the Other band
    the long-term A would give."""
    F.upsert_fund(conn, 1, "A Fund", as_on=TODAY)
    F.load_month(conn, 1, dt.date(2026, 7, 31), [
        dict(company_id=1, instrument_code=2, company="Some NBFC Ltd",
             sector="Fin", instrument_type="Commercial Paper",
             asset_class="debt", percent=5.0, value=1e7, is_issuer=True,
             expected_in_nsdl=False, is_securitised=False, seniority=None,
             rating=None, rating_confidence=None,
             borrower_category="NBFC", gap="not_in_nsdl")], today=TODAY)
    F.set_override(conn, "Some NBFC Ltd", rating="A",
                   source_note="user-supplied", entered_by="u")
    F.reapply_overrides(conn, on=TODAY)
    with conn.cursor() as cur:
        cur.execute("SELECT rating, rating_band FROM india.fund_holding")
        assert cur.fetchone() == ("A1", "AA")


# ── holdings with no company name ───────────────────────────────────────────

def test_a_missing_company_name_is_recovered_from_another_fund(conn):
    """THE LARGEST REMAINING GAP, AND IT NEVER REACHED THE QUEUE. ETMoney's
    company map does not always name every cId a fund holds, so those rows
    land with company NULL -- 15 funds carried an unnamed certificate of
    deposit at up to 8.94% -- and pending_rating_review filters on
    `company IS NOT NULL`, so nobody was ever asked about them.

    The name is recoverable because the id is ETMoney-wide: a fund whose page
    did name it supplies the name for one whose page did not."""
    def h(fid, cid, company):
        return dict(company_id=cid, instrument_code=2, company=company,
                    sector="Fin", instrument_type="Certificate of Deposit",
                    asset_class="debt", percent=8.94, value=1e8,
                    is_issuer=True, expected_in_nsdl=False,
                    is_securitised=False, seniority=None, rating=None,
                    rating_confidence=None,
                    borrower_category="Other Corporate", gap="not_in_nsdl")

    for fid in (1, 2):
        F.upsert_fund(conn, fid, f"Fund {fid}", as_on=TODAY)
    F.load_month(conn, 1, dt.date(2026, 7, 31),
                 [h(1, 7001, "Canara Bank")], today=TODAY)
    F.load_month(conn, 2, dt.date(2026, 7, 31),
                 [h(2, 7001, None)], today=TODAY)

    rep = F.backfill_company_names(conn, on=TODAY)
    assert rep["rows_filled"] == 1
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT company FROM india.fund_holding")
        assert [r[0] for r in cur.fetchall()] == ["Canara Bank"]


def test_an_id_with_two_names_is_left_alone(conn):
    """That would mean the id is per-fund rather than ETMoney-wide, and
    guessing which name is right would attach one company's rating to
    another's paper -- the failure this line has already had once."""
    def h(cid, company):
        return dict(company_id=cid, instrument_code=2, company=company,
                    sector="Fin", instrument_type="Commercial Paper",
                    asset_class="debt", percent=5.0, value=1e7,
                    is_issuer=True, expected_in_nsdl=False,
                    is_securitised=False, seniority=None, rating=None,
                    rating_confidence=None,
                    borrower_category="Other Corporate", gap="not_in_nsdl")

    for fid in (1, 2, 3):
        F.upsert_fund(conn, fid, f"Fund {fid}", as_on=TODAY)
    F.load_month(conn, 1, dt.date(2026, 7, 31), [h(9001, "Alpha Ltd")],
                 today=TODAY)
    F.load_month(conn, 2, dt.date(2026, 7, 31), [h(9001, "Beta Ltd")],
                 today=TODAY)
    F.load_month(conn, 3, dt.date(2026, 7, 31), [h(9001, None)], today=TODAY)

    rep = F.backfill_company_names(conn, on=TODAY)
    assert rep["ambiguous_ids"] == 1 and rep["rows_filled"] == 0
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM india.fund_holding "
                    "WHERE company IS NULL")
        assert cur.fetchone()[0] == 1


def test_backfilling_does_not_touch_a_named_holding(conn):
    F.upsert_fund(conn, 1, "A Fund", as_on=TODAY)
    F.load_month(conn, 1, dt.date(2026, 7, 31), [
        dict(company_id=1, instrument_code=3, company="REC Ltd.",
             sector="Fin", instrument_type="Debenture", asset_class="debt",
             percent=5.0, value=1e7, is_issuer=True, expected_in_nsdl=True,
             is_securitised=False, seniority=None, rating="AAA",
             rating_confidence=0.99, borrower_category="NBFC", gap=None)],
        today=TODAY)
    assert F.backfill_company_names(conn, on=TODAY)["rows_filled"] == 0


def test_one_override_covers_every_spelling_of_the_issuer(conn):
    """THE SOURCE WRITES THE SAME ISSUER SEVERAL WAYS -- "TVS Motor Company
    Ltd" and "TVS Motor Company Ltd - Pref. Shares", "Axis Bank Ltd." and
    "Axis Bank Ltd. Warrant". An exact-string key means one entry per
    spelling, which is a queue that never shrinks."""
    F.upsert_fund(conn, 1, "A Fund", as_on=TODAY)

    def h(cid, company, instrument="Debenture"):
        return dict(company_id=cid, instrument_code=3, company=company,
                    sector="Fin", instrument_type=instrument,
                    asset_class="debt", percent=5.0, value=1e7,
                    is_issuer=True, expected_in_nsdl=True,
                    is_securitised=False, seniority=None, rating=None,
                    rating_confidence=None,
                    borrower_category="Other Corporate", gap="unresolved")

    F.load_month(conn, 1, dt.date(2026, 7, 31), [
        h(1, "TVS Motor Company Ltd"),
        h(2, "TVS Motor Company Ltd - Pref. Shares"),
        h(3, "Axis Bank Ltd. Warrant", "Certificate of Deposit")], today=TODAY)
    F.set_override(conn, "TVS Motor Company Ltd", rating="AA",
                   source_note="user-supplied", entered_by="u")
    F.set_override(conn, "Axis Bank Ltd.", rating="AAA",
                   source_note="user-supplied", entered_by="u")

    assert F.reapply_overrides(conn, on=TODAY) == 3
    with conn.cursor() as cur:
        cur.execute("SELECT company, rating FROM india.fund_holding "
                    "ORDER BY company")
        got = dict(cur.fetchall())
    assert got["TVS Motor Company Ltd"] == "AA"
    assert got["TVS Motor Company Ltd - Pref. Shares"] == "AA"
    assert got["Axis Bank Ltd. Warrant"] == "A1+", "and still converted"


def test_re_enrichment_never_overwrites_a_hand_entered_rating(conn):
    """THIS COST A RUN. Overrides were applied first and re-enrichment last,
    so the rules wrote their answer back over every hand-entered rating and
    Karur Vysya, Titan, DBS and Julius Baer returned to the review queue the
    same afternoon they were cleared -- seventeen answers silently undone.

    Ordering alone would fix it today and break again the next time someone
    reorders the steps. rating_source = 'override' is what survives that."""
    from swp_planner.lookup import portfolio_credit as PC
    F.upsert_fund(conn, 1, "A Fund", as_on=TODAY)
    F.load_month(conn, 1, dt.date(2026, 7, 31), [
        dict(company_id=1, instrument_code=2, company="Karur Vysya Bank Ltd.",
             sector="Fin", instrument_type="Certificate of Deposit",
             asset_class="debt", percent=3.83, value=1e7, is_issuer=True,
             expected_in_nsdl=False, is_securitised=False, seniority=None,
             rating=None, rating_confidence=None,
             borrower_category="Other Corporate", gap="not_in_nsdl")],
        today=TODAY)
    F.set_override(conn, "Karur Vysya Bank Ltd.", rating="AAA",
                   source_note="user-supplied 2026-08-07", entered_by="u")
    assert F.reapply_overrides(conn, on=TODAY) == 1

    class _Blind:
        """The rules, which cannot resolve this issuer at all."""

        def by_name(self, name):
            return dict(found=False, rating=None, confidence=0.0,
                        source=None, borrower_category=None,
                        issuer_has_subordinated=False, ambiguous=False,
                        runner_up=None)

        def by_group(self, name):
            return dict(found=False, rating=None, confidence=0.0,
                        borrower_category=None)

    F.reenrich(conn, PC.enrich, _Blind(), on=TODAY)
    with conn.cursor() as cur:
        cur.execute("SELECT rating, gap, rating_source "
                    "FROM india.fund_holding")
        assert cur.fetchone() == ("A1+", None, "override")


def test_the_reapply_order_puts_hand_answers_last():
    """An override exists exactly where the rules have nothing to say, so it
    must be applied after them, not before."""
    import inspect
    src = inspect.getsource(FB.main)
    block = src[src.index("if a.reapply:"):src.index("if a.names_file:")]
    assert block.index("reenrich") < block.index("reapply_overrides")
