# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""test_portfolio_credit.py -- holdings joined to the credit lookup.

MOST OF THESE NEED NO DATABASE. The lookup is an interface, so a stub that
answers from a dict exercises every routing decision in `enrich` without
PostgreSQL. Only the last section uses a real connection, and it skips without
RTP_TEST_DSN.

THE FOUR THAT CARRY THE DESIGN:

  test_the_three_failure_kinds_are_kept_apart  -- explained misses must not be
      counted as lookup failures, or the number that indicts the matcher is
      buried under money market paper
  test_a_low_confidence_match_yields_no_rating -- resolving is not the same
      question as being sure enough to size a position on it
  test_equity_is_never_queued_for_a_human      -- no amount of attention makes
      a corporate bond file contain a share
  test_the_queue_is_ordered_by_share_of_fund   -- 0.03% and 7% are not the
      same problem
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from swp_planner.fund_filters import borrower as B  # noqa: E402
from swp_planner.ingest import etmoney_portfolio as P  # noqa: E402
from swp_planner.lookup import portfolio_credit as PC  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
DEBT = json.loads((FIX / "etmoney_44228.json").read_text())
EQUITY = json.loads((FIX / "etmoney_10234_equity.json").read_text())
HYBRID = json.loads((FIX / "etmoney_32508_hybrid.json").read_text())


class StubLookup:
    """Answers from a dict, in the shape lookup.api.CreditLookup returns."""

    def __init__(self, answers=None, default_found=False):
        self.answers = answers or {}
        self.default_found = default_found
        self.asked = []

    def by_name(self, name):
        self.asked.append(name)
        a = self.answers.get(name)
        if a is None:
            return dict(found=self.default_found, rating=None,
                        confidence=0.0, source="not_found",
                        borrower_category=B.DEFAULT,
                        issuer_has_subordinated=None, ambiguous=False,
                        runner_up=None)
        return dict(found=True, rating=a.get("rating"),
                    confidence=a.get("confidence", 1.0),
                    source="name",
                    borrower_category=a.get("category", B.DEFAULT),
                    issuer_has_subordinated=a.get("has_sub"),
                    ambiguous=a.get("ambiguous", False),
                    runner_up=a.get("runner_up"))


def _all_aaa(rows):
    return StubLookup({r["company"]: dict(rating="AAA", category=B.NBFC)
                       for r in rows if r.get("company")})


# ── routing ─────────────────────────────────────────────────────────────────

def test_the_three_failure_kinds_are_kept_apart():
    """EXPLAINED MISSES MUST NOT BE COUNTED AS LOOKUP FAILURES. Money market
    paper is absent from the NSDL corporate file by construction; a
    securitisation trust has no issuer at all; an unresolved name is the only
    one that indicts the matcher, and burying it under the other two hides the
    one number worth reading."""
    rows = P.holdings(DEBT)
    out = PC.enrich(rows, StubLookup())
    cov = PC.coverage(out)
    assert cov["not_in_nsdl"] > 0, "the CDs"
    assert cov["not_an_issuer"] > 0, "CBLO and net receivables"
    assert cov["unresolved"] > 0, "nothing matched, so the debentures"
    # Not zero: the state development loan is answered by rule, not by lookup.
    assert cov["rated"] == pytest.approx(0.82, abs=0.01)


def test_an_explained_miss_is_not_unresolved():
    """A certificate of deposit that does not resolve is not a failure."""
    rows = [h for h in P.holdings(DEBT)
            if h["instrument_type"] == "Certificate of Deposit"]
    assert rows
    out = PC.enrich(rows, StubLookup())
    assert all(r["gap"] == PC.NOT_IN_NSDL for r in out)
    assert PC.coverage(out)["unresolved"] == 0


def test_equity_is_never_queued_for_a_human():
    """An equity holding has no credit rating and never will. No amount of
    attention makes a corporate bond file contain a share."""
    out = PC.enrich(P.holdings(EQUITY), StubLookup())
    eq = [r for r in out if r["asset_class"] == "equity"]
    assert len(eq) > 40 and all(r["gap"] == PC.EQUITY for r in eq)
    assert PC.manual_queue(out) == []
    assert PC.coverage(out)["unresolved"] == 0


def test_cash_and_repo_are_not_looked_up_at_all():
    out = PC.enrich(P.holdings(DEBT), StubLookup())
    non = [r for r in out if not r["is_issuer"]]
    assert non and all(r["gap"] == PC.NOT_AN_ISSUER for r in non)


def test_a_securitisation_trust_is_queued_not_marked_unresolved():
    """India Universal Trust AL1 has no issuer to resolve. The originator may
    be findable but the trust is not, so it is a queue with a real answer at
    the end rather than a failure of the matcher."""
    out = PC.enrich(P.holdings(HYBRID), StubLookup())
    sec = [r for r in out if r["gap"] == PC.SECURITISED]
    assert sec
    assert PC.coverage(out)["securitised"] > 0


# ── confidence ──────────────────────────────────────────────────────────────

def test_a_low_confidence_match_yields_no_rating():
    """RESOLVING IS NOT THE SAME QUESTION AS BEING SURE ENOUGH TO SIZE A
    POSITION ON IT. TRUST_SCORE is deliberately above the matcher's own floor:
    that one decides whether a name resolved, this one whether the answer can
    be relied on."""
    rows = [h for h in P.holdings(DEBT) if h["company"] == "REC Ltd."]
    lk = StubLookup({"REC Ltd.": dict(rating="AAA", confidence=0.84)})
    out = PC.enrich(rows, lk)
    assert out[0]["rating"] is None
    assert out[0]["rating_confidence"] == 0.84
    assert out[0]["gap"] == PC.UNRESOLVED


def test_a_high_confidence_match_is_trusted_and_carries_its_score():
    rows = [h for h in P.holdings(DEBT) if h["company"] == "REC Ltd."]
    lk = StubLookup({"REC Ltd.": dict(rating="AAA", confidence=0.97,
                                      category=B.LARGE_CORPORATE)})
    out = PC.enrich(rows, lk)
    assert out[0]["rating"] == "AAA" and out[0]["rating_trusted"]
    assert out[0]["rating_source"] == "name"
    assert out[0]["borrower_category"] == B.LARGE_CORPORATE


def test_an_ambiguous_match_is_flagged_even_when_trusted():
    rows = [h for h in P.holdings(DEBT) if h["company"] == "REC Ltd."]
    lk = StubLookup({"REC Ltd.": dict(rating="AAA", confidence=0.99,
                                      ambiguous=True, runner_up="REC Power")})
    out = PC.enrich(rows, lk)
    assert out[0]["ambiguous"] and out[0]["runner_up"] == "REC Power"
    assert PC.coverage(out)["ambiguous"] > 0


def test_a_found_issuer_with_no_rating_is_unresolved_not_rated():
    rows = [h for h in P.holdings(DEBT) if h["company"] == "REC Ltd."]
    lk = StubLookup({"REC Ltd.": dict(rating=None, confidence=0.99)})
    assert PC.enrich(rows, lk)[0]["gap"] == PC.UNRESOLVED


def test_the_category_always_has_a_value():
    """Other Corporate is the default, so a caller sizing a portfolio never
    meets a None it has to handle."""
    out = PC.enrich(P.holdings(DEBT), StubLookup())
    assert all(r["borrower_category"] for r in out)
    assert B.DEFAULT in PC.by_category(out)


# ── seniority ───────────────────────────────────────────────────────────────

def test_seniority_comes_from_etmoney_because_there_is_no_isin():
    """Seniority is a property of the INSTRUMENT. Without an ISIN there is no
    instrument, only a borrower -- and a borrower can have senior paper and a
    perpetual outstanding at once."""
    out = PC.enrich(P.holdings(HYBRID), StubLookup())
    sub = [r for r in out if r["seniority"] == "subordinated"]
    assert sub and "Additional Tier 1" in sub[0]["instrument_type"]
    assert PC.coverage(out)["subordinated"] > 0


def test_a_holding_with_no_instrument_hint_is_unknown_not_senior():
    out = PC.enrich(P.holdings(DEBT), StubLookup())
    deb = [r for r in out if r["instrument_type"] == "Debenture"]
    assert deb and all(r["seniority"] == "unknown" for r in deb)


def test_the_issuer_level_warning_travels_separately():
    """Not which instrument this is, but whether this borrower has such paper
    in issue at all. Different question, different field."""
    rows = [h for h in P.holdings(DEBT) if h["company"] == "REC Ltd."]
    lk = StubLookup({"REC Ltd.": dict(rating="AAA", confidence=0.99,
                                      has_sub=True)})
    out = PC.enrich(rows, lk)
    assert out[0]["issuer_has_subordinated"] is True
    assert out[0]["seniority"] == "unknown", "the holding is still not an AT1"


# ── the queue ───────────────────────────────────────────────────────────────

def test_the_queue_is_ordered_by_share_of_fund():
    """A 0.03% securitisation trust and a 7% unresolved issuer are not the
    same problem, and a queue by row count puts them in the wrong order."""
    out = PC.enrich(P.holdings(DEBT), StubLookup())
    q = PC.manual_queue(out)
    assert q == sorted(q, key=lambda r: -r["percent"])


def test_holdings_too_small_to_matter_are_not_queued():
    """A 0.03% trust cannot move a portfolio and should not consume a person."""
    out = PC.enrich(P.holdings(HYBRID), StubLookup())
    assert all(r["percent"] >= 0.10 for r in PC.manual_queue(out, floor=0.10))
    assert len(PC.manual_queue(out, floor=0.0)) > \
        len(PC.manual_queue(out, floor=0.10))


def test_only_things_a_person_could_answer_reach_the_queue():
    """THE TEST IS NOT "IS IT IN NSDL" BUT "CAN SOMEONE SUPPLY IT". An earlier
    version excluded not_in_nsdl on the grounds that no attention makes an
    NSDL file contain a treasury bill -- true, and beside the point: a Bank of
    Baroda certificate of deposit carries a published A1+ that a person can
    read off CRISIL's site in a minute. That exclusion also disagreed with
    india.pending_rating_review, which included them: two places implementing
    one policy differently.

    Equity and cash still never appear, because no rating exists to find."""
    rows = P.holdings(DEBT)
    q = PC.manual_queue(PC.enrich(rows, _all_aaa(rows)), floor=0.0)
    assert not any(r["gap"] in (PC.NOT_AN_ISSUER, PC.EQUITY) for r in q)
    # The CDs used to appear here. They no longer do: their issuers resolve,
    # so the short-term grade is derived rather than asked for. What still
    # queues is a CD whose ISSUER did not resolve.
    unknown_issuer = PC.enrich(rows, StubLookup())
    assert any(r["gap"] == PC.NOT_IN_NSDL
               for r in PC.manual_queue(unknown_issuer, floor=0.0))


def test_government_paper_is_answered_by_rule_and_never_queued():
    """A state development loan is sovereign because of WHAT IT IS, not
    because of who issued it. Queueing "Maharashtra State" for a human to
    discover that it is sovereign wastes the queue."""
    rows = [h for h in P.holdings(DEBT)
            if h["instrument_type"] == "State Development Loan"]
    assert rows
    out = PC.enrich(rows, StubLookup())
    assert out[0]["rating"] == PC.SOVEREIGN_GRADE
    assert out[0]["rating_source"] == "instrument"
    assert out[0]["gap"] is None
    assert out[0]["borrower_category"] == B.STATE
    assert PC.manual_queue(out, floor=0.0) == []


def test_goi_and_treasury_bills_are_sovereign_too():
    rows = [dict(company="Reserve Bank of India", company_id=1,
                 instrument_type=t, instrument_code=9, asset_class="debt",
                 percent=3.0, value=1e7, is_issuer=True,
                 expected_in_nsdl=False, is_securitised=False, seniority=None)
            for t in ("Treasury Bills", "GOI Securities")]
    out = PC.enrich(rows, StubLookup())
    assert all(r["rating"] == PC.SOVEREIGN_GRADE for r in out)
    assert all(r["borrower_category"] == B.GOI for r in out)


# ── the aggregate check ─────────────────────────────────────────────────────

def test_a_perfect_lookup_reproduces_etmoneys_aaa_bucket():
    """THE ONLY VALIDATION THAT SCALES. Nobody is going to eyeball 1,500
    funds. On this fund the lookupable share is 77.31% and ETMoney's AAA
    bucket is 77.31%, so rating every resolvable issuer AAA must land on it."""
    rows = P.holdings(DEBT)
    out = PC.enrich(rows, _all_aaa(rows))
    rec = PC.reconcile(out, DEBT)
    aaa = [b for b in rec["buckets"] if b["rating"] == "AAA"][0]
    assert aaa["ours"] == pytest.approx(77.31, abs=0.01)
    assert aaa["theirs"] == 77.31
    assert aaa["within_tolerance"]


def test_a_lookup_that_finds_nothing_shows_as_a_gap_not_a_pass():
    """The SDL still resolves, by rule rather than by lookup, so the unrated
    share is everything except it."""
    rec = PC.reconcile(PC.enrich(P.holdings(DEBT), StubLookup()), DEBT)
    assert rec["unrated_percent"] > 98
    sov = [b for b in rec["buckets"] if b["rating"] == PC.SOVEREIGN_GRADE][0]
    assert sov["ours"] == pytest.approx(0.82, abs=0.01)
    assert sov["within_tolerance"], "SOV is answered by rule and must match"


def test_the_report_leads_with_the_number_that_indicts_the_lookup():
    rows = P.holdings(DEBT)
    txt = PC.report(DEBT, _all_aaa(rows))
    assert "UNRESOLVED" in txt and "read this first" in txt
    assert "manual queue" in txt and "borrower category" in txt


# ── against a real database ─────────────────────────────────────────────────

DSN = os.environ.get("RTP_TEST_DSN")


@pytest.mark.skipif(not DSN, reason="set RTP_TEST_DSN")
def test_end_to_end_through_the_real_lookup(dsn):
    """The whole chain on one fund: holdings, names, ratings, categories.

    Loads two issuers that really are in this fund's holdings, so the join is
    exercised against the actual strings ETMoney publishes rather than against
    strings chosen to match.
    """
    import datetime as dt

    import psycopg2

    from swp_planner.fund_filters import borrower as _b
    from swp_planner.ingest import store as S
    from swp_planner.lookup.api import CreditLookup

    conn = psycopg2.connect(dsn)
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS india CASCADE")
    conn.commit()
    S.apply_schema(conn)

    def rec(isin, issuer, grade, sector):
        return dict(isin=isin, issuer=issuer, issuer_key=issuer.upper(),
                    tier="corporate", description="", instrument_type="Bonds",
                    sector=sector, ownership="PSU", status="Active",
                    allotment=None, redemption=dt.date(2030, 1, 1),
                    coupon=7.5, face_value=1e6, issue_size=6e10,
                    raw_rating=f"CRISIL {grade}/Stable", worst_grade=grade,
                    worst_rank=0, enhanced=False, not_cooperating=False)

    S.load_daily(conn, _b.categorise_all([
        rec("INE020B08DL4", "REC LIMITED", "AAA", "Financial Institution"),
        rec("INE134E08MW5", "POWER FINANCE CORPORATION LIMITED", "AAA",
            "Financial Institution")]), as_on=dt.date(2026, 8, 6))

    out = PC.enrich(P.holdings(DEBT), CreditLookup(conn))
    rated = {r["company"]: r["rating"] for r in out if r["rating"]}
    assert "REC Ltd." in rated and rated["REC Ltd."] == "AAA"
    assert PC.coverage(out)["rated"] > 6.0
    # The state development loan is answered by rule, without the database.
    assert any(r["rating"] == PC.SOVEREIGN_GRADE for r in out)
    conn.close()


def test_a_certificate_of_deposit_never_takes_the_long_term_grade():
    """THE ORIGINAL FINDING STANDS AND THE RESPONSE TO IT WAS WRONG.

    A CD carries a SHORT-TERM rating and NSDL publishes the issuer's
    LONG-TERM grade. Handing the CD the long-term grade produced 86.0% AAA
    against ETMoney's published 77.31% -- the 8.69 difference was exactly the
    CDs and the SDL. That part was right, and this test still pins it: the
    long-term grade must never be copied across.

    What was wrong was sending the holding to a person instead. The agencies
    publish a linkage between the scales, so the answer is CONVERTED, not
    queued -- see test_a_bank_cd_is_derived_not_queued."""
    rows = [h for h in P.holdings(DEBT)
            if h["instrument_type"] == "Certificate of Deposit"]
    assert rows
    lk = StubLookup({r["company"]: dict(rating="AAA", confidence=1.0,
                                        category=B.PSU_BANK)
                     for r in rows})
    out = PC.enrich(rows, lk)
    assert all(r["rating"] == "A1+" for r in out), "converted, not copied"
    assert not any(r["rating"] == "AAA" for r in out)
    assert all(r["borrower_category"] == B.PSU_BANK for r in out)


def test_the_borrower_is_resolved_even_where_the_rating_is_not():
    """A money market fund is ~100% unratable from NSDL and still fully
    categorisable, which is what makes a borrower-concentration screen work on
    one at all."""
    rows = P.holdings(DEBT)
    out = PC.enrich(rows, StubLookup(
        {r["company"]: dict(rating="AAA", confidence=1.0,
                            category=B.PSU_BANK)
         for r in rows if r.get("company")}))
    # Nothing is left unrated once money market paper is converted, so the
    # claim now rests on the categories rather than on the gaps. Government
    # paper is excluded: the sovereign rule answers it by instrument type and
    # sets its own category, which is not the stub's.
    from_lookup = [r for r in out
                   if r["is_issuer"] and r.get("company")
                   and r["rating_source"] != "instrument"]
    assert from_lookup
    assert all(r["borrower_category"] == B.PSU_BANK for r in from_lookup)


# ── sovereign by borrower name, from the first cleaned queue ────────────────

@pytest.mark.parametrize("company", [
    "GOI", "Government of India", "GOVT. OF INDIA", "Reserve Bank of India",
])
def test_the_sovereign_is_recognised_by_name_not_only_by_instrument(company):
    """"GOI" sat in the review queue at 76.58% across 27 funds as
    `unresolved` -- a person being asked to confirm that the Government of
    India is sovereign, because the rule read instrument_type and its holdings
    carry a type that rule does not name."""
    rows = [dict(company=company, company_id=1, instrument_type="Bonds",
                 instrument_code=1, asset_class="debt", percent=76.58,
                 value=1e9, is_issuer=True, expected_in_nsdl=False,
                 is_securitised=False, seniority=None)]
    out = PC.enrich(rows, StubLookup())
    assert out[0]["rating"] == PC.SOVEREIGN_GRADE
    assert out[0]["borrower_category"] == B.GOI
    assert out[0]["gap"] is None
    assert PC.manual_queue(out, floor=0.0) == []


@pytest.mark.parametrize("company", [
    "GOI Bonds Trading Ltd", "Goibibo Ltd", "Bank of India", "REC Ltd.",
])
def test_a_company_merely_containing_goi_is_not_the_sovereign(company):
    """Anchored where the name is an abbreviation: "GOI" alone is the
    borrower, "GOI Bonds Trading Ltd" is a company."""
    assert not any(p.search(company) for p, _ in PC.SOVEREIGN_COMPANIES)


def test_the_instrument_type_still_wins_when_it_speaks():
    """A state development loan is STATE, and reading the company name first
    would have called it GoI."""
    rows = [dict(company="Maharashtra State", company_id=1,
                 instrument_type="State Development Loan", instrument_code=123,
                 asset_class="debt", percent=0.82, value=1e7, is_issuer=True,
                 expected_in_nsdl=False, is_securitised=False, seniority=None)]
    assert PC.enrich(rows, StubLookup())[0]["borrower_category"] == B.STATE


# ── money market paper from a rated issuer ──────────────────────────────────

def _cd(company="Canara Bank", pct=16.45):
    return dict(company=company, company_id=1,
                instrument_type="Certificate of Deposit", instrument_code=2,
                asset_class="debt", percent=pct, value=1e8, is_issuer=True,
                expected_in_nsdl=False, is_securitised=False, seniority=None)


def test_a_bank_cd_is_derived_not_queued():
    """CANARA BANK SAT IN THE REVIEW QUEUE AT 16.45% ACROSS 161 FUNDS while
    being present in the NSDL file with 16 rated instruments. I was queueing
    it because a CD is on a different scale from NSDL's long-term grade --
    true, and the wrong conclusion: the agencies publish a mapping between the
    two, and a person sent to look it up would apply exactly that table."""
    lk = StubLookup({"Canara Bank": dict(rating="AAA", confidence=0.99,
                                         category=B.PSU_BANK)})
    out = PC.enrich([_cd()], lk)
    assert out[0]["rating"] == "A1+"
    assert out[0]["rating_source"] == "short_term_derived"
    assert out[0]["gap"] is None
    assert PC.manual_queue(out, floor=0.0) == []


def test_a_derived_grade_says_it_is_derived():
    """A convention, not an identity: an issuer can hold a strong short-term
    profile while its long-term rating slips, and the table cannot see that."""
    lk = StubLookup({"Canara Bank": dict(rating="AAA", confidence=0.99)})
    out = PC.enrich([_cd()], lk)
    assert out[0]["rating_confidence"] == PC.DERIVED_CONFIDENCE < 1.0


@pytest.mark.parametrize("long_grade,short", [
    ("AAA", "A1+"), ("AA+", "A1+"), ("AA-", "A1+"),
    ("A+", "A1"), ("A", "A1"), ("A-", "A2+"),
    ("BBB", "A3"), ("BB", "A4"), ("D", "D"),
])
def test_the_mapping_follows_the_agencies_linkage(long_grade, short):
    """Conservative where the mapping is a range: an issuer at A+ may carry
    A1+ or A1 depending on its liquidity, and the weaker is taken because a
    short-term grade guessed upward is the error that costs money."""
    assert PC.short_term_grade(long_grade) == short


def test_a_cd_from_an_unresolved_issuer_is_still_queued():
    """Aditya Birla Finance is genuinely absent from NSDL -- 0 rows -- so
    there is no long-term grade to convert and a person really is needed."""
    out = PC.enrich([_cd("Aditya Birla Finance Ltd.")], StubLookup())
    assert out[0]["rating"] is None and out[0]["gap"] == PC.NOT_IN_NSDL
    assert PC.manual_queue(out, floor=0.0)


def test_a_low_confidence_issuer_match_does_not_get_a_derived_grade():
    """Deriving from a name that only half-matched would compound a doubtful
    issuer with a converted scale."""
    lk = StubLookup({"Canara Bank": dict(rating="AAA", confidence=0.84)})
    assert PC.enrich([_cd()], lk)[0]["gap"] == PC.NOT_IN_NSDL


def test_only_money_market_paper_is_derived():
    """A state development loan is answered by the sovereign rule and a
    securitisation trust by a person; neither is a scale conversion."""
    sdl = dict(_cd(), instrument_type="State Development Loan",
               company="Maharashtra State")
    lk = StubLookup({"Maharashtra State": dict(rating="AAA", confidence=0.99)})
    assert PC.enrich([sdl], lk)[0]["rating"] == PC.SOVEREIGN_GRADE
    trust = dict(_cd(), instrument_type="Securitised Debt",
                 is_securitised=True, company="India Universal Trust AL1")
    assert PC.enrich([trust], StubLookup())[0]["gap"] == PC.SECURITISED


def test_the_borrower_category_still_comes_from_the_issuer():
    lk = StubLookup({"Canara Bank": dict(rating="AAA", confidence=0.99,
                                         category=B.PSU_BANK)})
    assert PC.enrich([_cd()], lk)[0]["borrower_category"] == B.PSU_BANK
