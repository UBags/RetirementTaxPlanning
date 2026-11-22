# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""test_etmoney_portfolio.py -- the parser, against a real captured payload.

THE FIXTURE IS REAL, TRIMMED. `tests/fixtures/etmoney_44228.json` is the actual
`getAssetAllocationData` from Bajaj Finserv Banking and PSU Fund, reduced to
two of its twenty-five months. Every structural feature is kept, including the
three cIds the company map cannot resolve -- which is the point, since an
invented fixture would have had a complete map and the missing-name path would
never have been exercised.

THE THREE THAT CARRY THE DESIGN:

  test_nabard_is_three_lines_and_they_must_be_summed  -- a 3% cap breached
      across three lines is still breached
  test_an_unresolvable_company_is_kept_not_dropped  -- a holding whose name is
      unknown is still a percentage of the fund
  test_a_brace_inside_a_company_name_does_not_truncate  -- the failure that
      would silently shorten a holdings list
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from swp_planner.ingest import etmoney_portfolio as P  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "etmoney_44228.json"
LATEST = "2026-07-31"


@pytest.fixture(scope="module")
def payload():
    return json.loads(FIXTURE.read_text())


# ── extraction from the page ────────────────────────────────────────────────

def test_the_payload_is_found_in_a_plain_var_assignment(payload):
    """Not __NEXT_DATA__, not an XHR, not a hydration global. Two rounds were
    spent looking for the mechanism before looking for the assignment."""
    html = ("<script>function afterAllJSFilesLoaded() { var "
            f"getAssetAllocationData = {json.dumps(payload)}; }}</script>")
    assert P.extract_payload(html)["mfHoldingMap"].keys() == \
        payload["mfHoldingMap"].keys()


def test_a_brace_inside_a_company_name_does_not_truncate():
    """THE SILENT FAILURE. A naive brace counter closes the object early and
    returns a SHORTER holdings list, which looks like a valid portfolio.

    The brace must be UNBALANCED. A first version of this test used
    "Odd } Brace { Ltd.", whose two braces cancel -- so the naive counter
    survived it and the falsifier did not bite. One stray "}" is the case
    that actually distinguishes them."""
    doc = {"mfHoldingMap": {"2026-07-31": [
        {"cId": 1, "sId": 1, "astTypCd": 3, "astPer": 60.0, "astVal": 1.0},
        {"cId": 2, "sId": 1, "astTypCd": 3, "astPer": 40.0, "astVal": 1.0}]},
        "mfCompanyDTOMap": {"1": {"company": 'Odd } Ltd.'},
                            "2": {"company": "Normal Ltd."}}}
    html = f"<script>var getAssetAllocationData = {json.dumps(doc)};</script>"
    out = P.extract_payload(html)
    assert len(out["mfHoldingMap"]["2026-07-31"]) == 2
    assert P.holdings(out)[0]["company"] == "Odd } Ltd."


def test_a_missing_var_raises_rather_than_returning_nothing():
    """Empty holdings read as a fund that holds nothing, which is a lie a
    concentration screen would act on."""
    with pytest.raises(P.PortfolioError):
        P.extract_payload("<html>blocked by a bot check</html>")


def test_a_truncated_page_raises(payload):
    html = ("<script>var getAssetAllocationData = "
            f"{json.dumps(payload)[:5000]}")
    with pytest.raises(P.PortfolioError):
        P.extract_payload(html)


# ── the code joins ──────────────────────────────────────────────────────────

def test_holdings_resolve_company_sector_and_instrument(payload):
    rows = P.holdings(payload)
    top = rows[0]
    assert top["company"] == "Others" or top["company"]
    named = [h for h in rows if h["company"] == "REC Ltd."]
    assert named, "cId -> mfCompanyDTOMap join failed"
    assert named[0]["sector"] == "Financial"
    assert named[0]["instrument_type"] == "Debenture"
    assert named[0]["percent"] == 6.95


def test_the_percentages_sum_to_a_hundred(payload):
    assert round(sum(h["percent"] for h in P.holdings(payload)), 2) == 100.0


def test_twenty_five_months_are_available_not_one(payload):
    """One page fetch yields years of monthly holdings, so concentration and
    rating drift are measurable rather than inferred."""
    h = P.history(payload)
    assert len(h) == len(payload["mfHoldingMap"]) >= 2
    assert all(rows for rows in h.values())


def test_a_specific_month_can_be_asked_for(payload):
    rows = P.holdings(payload, as_on=LATEST)
    assert rows and all(str(r["as_on"]) == LATEST for r in rows)


def test_an_absent_month_raises_with_the_range_it_does_have(payload):
    with pytest.raises(P.PortfolioError) as e:
        P.holdings(payload, as_on="1999-01-31")
    assert "1999-01-31" in str(e.value)


# ── aggregation ─────────────────────────────────────────────────────────────

def test_nabard_is_three_lines_and_they_must_be_summed(payload):
    """ROWS ARE PER INSTRUMENT TYPE, NOT PER ISSUER. NABARD appears under
    Bonds and twice under Debenture. A 3% cap breached across three lines is
    still breached."""
    agg = P.by_issuer(P.holdings(payload))
    nabard = [d for d in agg if d["company"] and "NATIONAL BANK FOR AGRI"
              in d["company"].upper()]
    assert nabard, "NABARD not found"
    assert nabard[0]["lines"] == 3
    assert nabard[0]["percent"] == pytest.approx(9.45, abs=0.01)
    assert len(nabard[0]["instrument_types"]) >= 2


def test_aggregation_excludes_things_that_are_not_issuers(payload):
    """A concentration screen that treats "Net Receivables" or CBLO as an
    issuer is measuring nothing."""
    agg = P.by_issuer(P.holdings(payload))
    for d in agg:
        assert "Net Receivables" not in (d["instrument_types"] or [])
        assert "CBLO" not in (d["instrument_types"] or [])


def test_issuer_totals_never_exceed_the_holdings_total(payload):
    rows = P.holdings(payload)
    assert sum(d["percent"] for d in P.by_issuer(rows)) <= \
        sum(h["percent"] for h in rows) + 0.01


# ── what the data cannot do ─────────────────────────────────────────────────

def test_an_unresolvable_company_is_kept_not_dropped():
    """The company map covers only the LATEST month; the history references
    cIds that are not in it. A holding whose name is unknown is still a
    percentage of the fund and must not vanish from a concentration total."""
    doc = {"mfHoldingMap": {"2024-07-31": [
        {"cId": 999, "sId": 8, "astTypCd": 3, "astPer": 12.5, "astVal": 1e7}]},
        "mfCompanyDTOMap": {}, "mfSectorDTOMap": {}}
    rows = P.holdings(doc)
    assert len(rows) == 1
    assert rows[0]["company"] is None and rows[0]["company_id"] == 999
    assert rows[0]["percent"] == 12.5
    assert len(P.unresolved_companies(rows)) == 1
    assert P.by_issuer(rows)[0]["percent"] == 12.5


def test_there_is_no_per_holding_rating(payload):
    """RECORDED AS A FACT ABOUT THE SOURCE, not an oversight. Rating exists
    once, aggregated. So NSDL triangulation is the ONLY per-holding source and
    not a cross-check on ETMoney's, which is what was hoped for."""
    for h in P.holdings(payload):
        assert "rating" not in h
    assert P.rating_buckets(payload)["AAA"] == 77.31


def test_instruments_absent_from_nsdl_are_flagged_not_silently_missed(payload):
    """CD, SDL, CBLO and net receivables are not in the NSDL corporate file,
    so a lookup on them legitimately misses. Flagging says which misses are
    explained."""
    rows = P.holdings(payload)
    cds = [h for h in rows
           if h["instrument_type"] == "Certificate of Deposit"]
    assert cds and not cds[0]["expected_in_nsdl"]
    sdl = [h for h in rows
           if h["instrument_type"] == "State Development Loan"]
    assert sdl and not sdl[0]["expected_in_nsdl"]
    debentures = [h for h in rows if h["instrument_type"] == "Debenture"]
    assert debentures and debentures[0]["expected_in_nsdl"]


def test_the_summary_states_how_much_is_beyond_nsdl(payload):
    """THE NUMBER MOVED WHEN NON-ISSUER ROWS STOPPED BEING COUNTED, and the
    new one is the right one. It was 22.69% while cash, CBLO and net
    receivables were included -- but those are not lookups that failed, they
    are not lookups at all. Debt exposure to an issuer NSDL will not have is
    8.69%: the certificates of deposit and the state development loan.

    Read the three together. lookupable + not_in_nsdl + non_issuer accounts
    for the whole debt side, and only the middle one is a manual queue."""
    s = P.summarise(payload)
    assert s["not_in_nsdl_percent"] == pytest.approx(8.69, abs=0.01)
    assert s["lookupable_percent"] == pytest.approx(77.31, abs=0.01)
    assert s["non_issuer_percent"] == pytest.approx(14.0, abs=0.01)
    assert s["holdings"] == 23 and s["issuers"] > 0


# ── the aggregate cross-check, which is the only validation available ───────

def test_reconcile_reproduces_the_buckets_when_the_lookup_is_right(payload):
    rows = P.holdings(payload)
    buckets = P.rating_buckets(payload)

    def perfect(h):
        if h["instrument_type"] == "Certificate of Deposit":
            return "A1+"
        if h["instrument_type"] == "State Development Loan":
            return "SOV"
        return "AAA" if h["is_issuer"] else None

    rep = P.reconcile_ratings(rows, perfect, buckets)
    by = {r["rating"]: r for r in rep["buckets"]}
    assert by["A1+"]["within_tolerance"] and by["SOV"]["within_tolerance"]


def test_reconcile_reports_the_unrated_share_first(payload):
    """If a fifth of the fund could not be rated at all, the other
    comparisons are noise."""
    rep = P.reconcile_ratings(P.holdings(payload), lambda h: None,
                              P.rating_buckets(payload))
    assert rep["unrated_percent"] == pytest.approx(100.0, abs=0.01)
    assert rep["matched"] == 0


def test_a_wrong_lookup_shows_up_as_a_gap(payload):
    """The point of the cross-check: if our per-holding ratings say 60% AAA
    where ETMoney says 77.31%, the triangulation is wrong and by how much."""
    rep = P.reconcile_ratings(P.holdings(payload),
                              lambda h: "BBB" if h["is_issuer"] else None,
                              P.rating_buckets(payload))
    aaa = [r for r in rep["buckets"] if r["rating"] == "AAA"][0]
    assert aaa["ours"] == 0.0 and aaa["theirs"] == 77.31
    assert aaa["gap"] == pytest.approx(-77.31, abs=0.01)
    assert not aaa["within_tolerance"]


def test_a_small_bucket_cannot_pass_on_nothing(payload):
    """FOUND BY A FAILING TEST, NOT BY REASONING. `tolerance` is in absolute
    percentage points, so SOV at 0.82% and Others at 0.28% both sit inside a
    2-point tolerance while we contribute nothing. A lookup that rated the
    whole fund None scored two matches out of five before the zero guard --
    the same shape of silent pass as a check that returns zero on every real
    file."""
    rep = P.reconcile_ratings(P.holdings(payload), lambda h: None,
                              P.rating_buckets(payload))
    sov = [r for r in rep["buckets"] if r["rating"] == "SOV"][0]
    assert sov["ours"] == 0.0 and sov["theirs"] == 0.82
    assert not sov["within_tolerance"], "0% must not match 0.82% on tolerance"


# ── asset class, from four real funds of different shapes ───────────────────

EQUITY_FIXTURE = Path(__file__).resolve().parent / "fixtures" \
    / "etmoney_10234_equity.json"
HYBRID_FIXTURE = Path(__file__).resolve().parent / "fixtures" \
    / "etmoney_32508_hybrid.json"


@pytest.fixture(scope="module")
def equity():
    return json.loads(EQUITY_FIXTURE.read_text())


@pytest.fixture(scope="module")
def hybrid():
    return json.loads(HYBRID_FIXTURE.read_text())


def test_equity_holdings_are_not_flagged_as_missing_debt(equity):
    """THE BUG THIS FIXTURE EXISTS FOR. Equity carries astTypCd 13, which is
    never in `instrumentWiseHoldingDTO` -- that lists only the DEBT
    breakdown -- so every equity row resolves to instrument_type None. Treating
    "type not in the not-in-NSDL list" as "expect to find it in NSDL" flagged
    53 shares of an equity fund as debt instruments that should have resolved
    and did not: a manual queue made entirely of equities."""
    rows = P.holdings(equity)
    eq = [h for h in rows if h["asset_class"] == "equity"]
    assert len(eq) > 40
    assert all(h["instrument_type"] is None for h in eq)
    assert not any(h["expected_in_nsdl"] for h in eq)
    assert P.summarise(equity)["not_in_nsdl_percent"] == 0


def test_hldBsTyp_separates_the_three_asset_classes(hybrid):
    """Measured across four funds: an equity fund is all hldBsTyp 1, a debt
    fund all 2, and a balanced advantage fund carries all three."""
    classes = {h["asset_class"] for h in P.holdings(hybrid)}
    assert classes == {"equity", "debt", "other"}


def test_the_asset_allocation_is_the_number_on_the_fund_page(equity, hybrid):
    """The same three percentages the main page prints, derived from the
    portfolio payload so that page need not also be fetched."""
    a = P.asset_allocation(equity)
    assert a["equity"] > 95 and a["debt"] < 5
    h = P.asset_allocation(hybrid)
    assert round(sum(h.values())) == 100
    assert h["equity"] > h["debt"] > h["other"]


def test_the_equity_share_is_the_one_that_decides_the_tax_bucket(hybrid):
    """At or above 65% domestic equity a hybrid is taxed as equity; between 35
    and 65 it is the old debt bucket; below 35 it is s.50AA at slab rate. A
    fund that drifts across 65 changes its own tax treatment."""
    assert P.asset_allocation(hybrid)["equity"] >= 65.0


def test_allocation_history_shows_drift_month_by_month(payload):
    hist = P.allocation_history(payload)
    assert len(hist) == len(payload["mfHoldingMap"])
    assert all(round(sum(v.values())) == 100 for v in hist.values())


# ── seniority, which the payload does carry after all ───────────────────────

def test_an_additional_tier_1_bond_is_named_by_etmoney(hybrid):
    """I SAID THE PAYLOAD CARRIED NO PER-HOLDING SENIORITY. It does, for the
    instrument where it matters most: "Additional Tier 1 Bond" is its own
    instrument type -- Union Bank of India, 0.05%. That is independent of the
    NSDL description and sentinel rules, so where both speak they can be
    compared and where NSDL is silent this still answers."""
    sub = [h for h in P.holdings(hybrid) if h["seniority"] == "subordinated"]
    assert sub, "no subordinated holding found"
    assert "Additional Tier 1" in sub[0]["instrument_type"]
    assert P.summarise(hybrid)["subordinated_percent"] > 0


def test_securitised_debt_is_flagged_for_the_manual_queue(hybrid):
    """India Universal Trust AL1 and the rest have no issuer to resolve at
    all, so they are routed rather than left as failed lookups."""
    sec = [h for h in P.holdings(hybrid) if h["is_securitised"]]
    assert sec and not any(h["expected_in_nsdl"] for h in sec)
    assert P.summarise(hybrid)["securitised_percent"] > 0


def test_a_plain_debenture_is_not_given_a_seniority(payload):
    """The hint is only as good as the instrument name; silence must stay
    silence rather than becoming "senior"."""
    deb = [h for h in P.holdings(payload)
           if h["instrument_type"] == "Debenture"]
    assert deb and all(h["seniority"] is None for h in deb)


# ── the coverage numbers that qualify every lookup claim ────────────────────

def test_non_issuer_rows_are_not_counted_as_failed_lookups(payload):
    """Repo and net receivables are not lookups that failed, they are not
    lookups at all. Counting them made an equity fund report 1.85% "not in
    NSDL" when the whole of it was cash and receivables."""
    s = P.summarise(payload)
    assert s["non_issuer_percent"] > 0
    rows = P.holdings(payload)
    assert not any(h["expected_in_nsdl"] for h in rows if not h["is_issuer"])


def test_the_lookupable_share_matches_etmoneys_own_aaa_bucket(payload):
    """AN INDEPENDENT CHECK THAT FELL OUT OF THE DATA. On this fund the share
    expected to resolve in NSDL is 77.31%, and ETMoney's own AAA bucket is
    77.31%. The debentures and bonds are the AAA; the CD, SDL, cash and AIF
    are the rest. Two separately-derived numbers agreeing to two decimals."""
    s = P.summarise(payload)
    assert s["lookupable_percent"] == pytest.approx(
        P.rating_buckets(payload)["AAA"], abs=0.01)


def test_the_others_bucket_is_not_a_borrower():
    """ETMoney rolls small positions into a row literally called "Others" --
    4.88% across five funds in one run, sent to the manual review queue as an
    unresolved issuer. Nobody can look up "Others"; it is the fund's own
    aggregation bucket."""
    doc = {"mfHoldingMap": {"2026-07-31": [
        {"cId": 1, "sId": 1, "astTypCd": 3, "hldBsTyp": 2, "astPer": 4.88,
         "astVal": 1e7},
        {"cId": 2, "sId": 1, "astTypCd": 3, "hldBsTyp": 2, "astPer": 95.12,
         "astVal": 2e7}]},
        "mfCompanyDTOMap": {"1": {"company": "Others"},
                            "2": {"company": "REC Ltd."}},
        "mfSectorDTOMap": {"1": {"sector": "Financial"}}}
    rows = {h["company"]: h for h in P.holdings(doc)}
    assert rows["Others"]["is_issuer"] is False
    assert rows["REC Ltd."]["is_issuer"] is True
    assert rows["Others"]["percent"] == 4.88, "still counted in the fund"


@pytest.mark.parametrize("name", [
    "Mirae Asset Silver ETF - Growth", "SBI Gold ETF",
    "Bandhan Gold ETF-Growth", "Quantum Nifty 50 ETF-IDCW",
    "Nippon India Silver ETF FOF",
])
def test_units_of_another_fund_are_not_borrowers(name):
    """A FUND OF FUNDS HOLDS AN ETF AT 100% OF ITSELF, and the first full run
    put exactly those at the top of the manual review queue -- someone was
    being asked to find the credit rating of a gold ETF. The plan suffix is
    the giveaway: no operating company is called "- Growth"."""
    assert P.FUND_UNIT_RE.search(name)


@pytest.mark.parametrize("name", [
    "REC Ltd.", "Housing & Urban Development Corporation Ltd.",
    "Growth Securities Ltd", "Bharat Forge Ltd",
])
def test_a_real_company_is_not_mistaken_for_a_fund_unit(name):
    assert not P.FUND_UNIT_RE.search(name)


def test_a_fof_holding_is_kept_but_not_queued():
    doc = {"mfHoldingMap": {"2026-07-31": [
        {"cId": 1, "sId": 1, "astTypCd": 13, "hldBsTyp": 3, "astPer": 100.09,
         "astVal": 1e8}]},
        "mfCompanyDTOMap": {"1": {"company": "SBI Gold ETF"}},
        "mfSectorDTOMap": {"1": {"sector": "Other"}}}
    row = P.holdings(doc)[0]
    assert row["is_issuer"] is False
    assert row["percent"] == 100.09, "still counted in the fund"


# ── overseas fund units, from the first real review queue ───────────────────

@pytest.mark.parametrize("name", [
    "Nippon India ETF Gold BeES", "Smam Asia REIT Sub Trust",
    "FSM First Trust Strategic Metal And Energy Equity UCITS Fund",
    "GMO Quality Investment Fund", "CI Emerging Markets Fund I",
    "Manulife Global Fund SICAV-Asia Pacific REIT",
    "HSBC Global Investments Funds - Global Emerging Markets Equity S1D",
])
def test_overseas_fund_units_are_not_borrowers(name):
    """THE FIRST VERSION CAUGHT THE DOMESTIC ETFS AND LEFT THESE BEHIND, so a
    fund-of-funds holding a UCITS fund at 99.5% sent someone off to find its
    credit rating. UCITS, SICAV and BeES are fund-vehicle names no operating
    company carries."""
    assert P.FUND_UNIT_RE.search(name)


@pytest.mark.parametrize("name", [
    "REC Ltd.", "Bharat Forge Ltd", "Fundtech Solutions Ltd",
    "Power Finance Corporation Ltd.",
])
def test_widening_the_rule_did_not_catch_real_companies(name):
    """\\bFUNDS?\\b is a whole-word test: "Fundtech" is not a fund."""
    assert not P.FUND_UNIT_RE.search(name)


@pytest.mark.parametrize("company,collides", [
    # The names actually seen do NOT collide with the widened rule, so the
    # guard is precautionary for them. Checked rather than assumed.
    ("India Universal Trust AL1", False),
    ("Shivshakti Securitisation Trust", False),
    ("Sansar Trust", False),
    # These DO collide, and are the cases the guard is for. The first is
    # CONSTRUCTED; the second is the shape a REIT-backed pool would take.
    ("India Universal Trust Fund AL1", True),
    ("Vivriti Cars REIT Trust", True),
])
def test_a_securitisation_trust_survives_the_widened_rule(company, collides):
    """A SECURITISATION TRUST IS AN ISSUER AND MUST STAY ONE -- they are
    exactly the holdings that need a person, and dropping them to
    not_an_issuer removes them from the review queue entirely.

    The guard only bites where a trust name collides with the fund-unit rule,
    and none of the names observed so far does. That makes it precautionary,
    which is worth stating rather than implying it is load-bearing."""
    assert bool(P.FUND_UNIT_RE.search(company)) is collides
    doc = {"mfHoldingMap": {"2026-07-31": [
        {"cId": 1, "sId": 1, "astTypCd": 5, "hldBsTyp": 2, "astPer": 3.1,
         "astVal": 1e7}]},
        "mfCompanyDTOMap": {"1": {"company": company}},
        "mfSectorDTOMap": {"1": {"sector": "Financial"}},
        "instrumentWiseHoldingDTO": {"holdings": [
            {"instrumentCode": 5, "instrumentDescription": "Securitised Debt",
             "percentage": 3.1}]}}
    row = P.holdings(doc)[0]
    assert row["is_securitised"] and row["is_issuer"] is True


def test_the_holding_fund_settles_what_the_name_cannot():
    """"Julius Baer Global Excellence Equity USD C" carries no ETF, UCITS or
    FUND marker and no name test will ever catch it. The fund holding it at
    98.8% is a fund of funds, and that settles it without guessing."""
    doc = {"mfHoldingMap": {"2026-07-31": [
        {"cId": 1, "sId": 1, "astTypCd": 13, "hldBsTyp": 1, "astPer": 98.8,
         "astVal": 1e8}]},
        "mfCompanyDTOMap": {
            "1": {"company": "Julius Baer Global Excellence Equity USD C"}},
        "mfSectorDTOMap": {"1": {"sector": "Other"}}}
    assert P.holdings(doc)[0]["is_issuer"] is True
    assert P.holdings(doc, fund_category="Other Scheme - FoF Overseas"
                      )[0]["is_issuer"] is False
    assert P.holdings(doc, fund_category="Equity Scheme - Large Cap Fund"
                      )[0]["is_issuer"] is True


@pytest.mark.parametrize("category,fof", [
    ("Other Scheme - FoF Domestic", True),
    ("Other Scheme - FoF Overseas", True),
    ("Fund of Funds", True),
    ("Equity Scheme - Large Cap Fund", False),
    ("Debt Scheme - Liquid Fund", False),
    (None, False),
])
def test_which_categories_count_as_a_fund_of_funds(category, fof):
    assert P.is_fund_of_funds(category) is fof


@pytest.mark.parametrize("instrument", [
    "Interest Rate Swap", "Infrastructure Investment Trust",
    "Real Estate Investment Trusts",
])
def test_instruments_with_no_borrower_are_not_issuers(instrument):
    """An interest rate swap is a contract with a counterparty, not a loan to
    an issuer -- 22 rows sat in the review queue asking for its credit rating.
    InvIT and REIT units are equity in a trust, and the trust is not borrowing
    from the fund."""
    doc = {"mfHoldingMap": {"2026-07-31": [
        {"cId": 1, "sId": 1, "astTypCd": 9, "hldBsTyp": 2, "astPer": 4.67,
         "astVal": 1e7}]},
        "mfCompanyDTOMap": {"1": {"company": "Indus Infra Trust"}},
        "mfSectorDTOMap": {"1": {"sector": "Infrastructure"}},
        "instrumentWiseHoldingDTO": {"holdings": [
            {"instrumentCode": 9, "instrumentDescription": instrument,
             "percentage": 4.67}]}}
    row = P.holdings(doc)[0]
    assert row["is_issuer"] is False
    assert row["percent"] == 4.67, "still counted in the fund"


@pytest.mark.parametrize("instrument", [
    "Debenture", "Commercial Paper", "Non Convertible Debenture", "Bonds",
])
def test_real_debt_instruments_still_have_a_borrower(instrument):
    assert not P.NON_ISSUER_INSTRUMENTS_RE.search(instrument)
