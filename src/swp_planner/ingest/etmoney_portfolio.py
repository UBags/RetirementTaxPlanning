# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""etmoney_portfolio.py -- holdings out of the ETMoney portfolio page.

WHERE THE DATA IS. Not __NEXT_DATA__, not an XHR, not a hydration global: a
plain `var getAssetAllocationData = {...}` inside `function
afterAllJSFilesLoaded()`. Looking for the ASSIGNMENT rather than for a known
framework is what found it, after two rounds of looking for the mechanism.

TWENTY-FIVE MONTHS, NOT ONE. `mfHoldingMap` is keyed by asset date --
2024-07-31 through 2026-07-31 on the fund this was written against -- so a
single page fetch yields two years of monthly holdings. Concentration drift
and rating drift become measurable rather than inferred.

HOLDINGS CARRY CODES, NOT NAMES, and the join is the work:

    cId       -> mfCompanyDTOMap        "REC Ltd.", "Bank Of Baroda"
    sId       -> mfSectorDTOMap         "Financial", "Energy"
    astTypCd  -> instrumentWiseHoldingDTO.holdings
                 3 Debenture, 7 NCD, 1 Bonds, 2 Certificate of Deposit,
                 87 CBLO, 83 Net Receivables, 123 State Development Loan,
                 204 Alternative Investment Fund

THE COMPANY MAP ONLY COVERS THE LATEST MONTH, WHICH IS THE MAIN LIMIT HERE.
Eighteen companies are mapped; the twenty-five months of history reference
nine cIds that are not among them. Those rows are returned with
`company=None` and `company_id` intact rather than dropped, because a holding
whose name is unknown is still 3% of the fund and must not silently vanish
from a concentration total. `unresolved_companies` counts them.

THERE IS NO PER-HOLDING RATING. Rating appears once, aggregated, in
`mfRatingWiseHoldingDTOList` -- AAA 77.31%, A1+ 7.87%, SOV 0.82%. So the NSDL
triangulation is the ONLY per-holding source of RATING.

THERE IS PER-HOLDING SENIORITY, THOUGH, WHICH I SAID THERE WAS NOT. A balanced
advantage fund's instrument vocabulary includes "Additional Tier 1 Bond" as its
own type -- Union Bank of India, 0.05%. That is ETMoney stating subordination
directly, independent of the NSDL description and sentinel rules, so where both
speak they can be compared and where NSDL is silent this still answers. It also
names "Securitised Debt", which is the case with no issuer to resolve at all.

WHAT IS AVAILABLE INSTEAD IS AN AGGREGATE CROSS-CHECK, AND IT IS A GOOD ONE.
Weighting each holding's triangulated rating by `astPer` must reproduce those
buckets. If our per-holding ratings say 60% AAA where ETMoney says 77.31%, the
triangulation is wrong somewhere and the gap says by how much. `rating_buckets`
returns theirs; `reconcile_ratings` does the comparison.

ROWS ARE PER INSTRUMENT TYPE, NOT PER ISSUER. NABARD appears three times in
one month under astTypCd 1, 3 and 3 -- Bonds and two Debenture lines. Anything
asking "how much of this fund is NABARD" has to sum, which `by_issuer` does.
Summing is also what makes a concentration cap meaningful, since a 3% cap
breached across three lines is still breached.
"""
from __future__ import annotations

import datetime as _dt
import json
import re

VAR_NAME = "getAssetAllocationData"

# hldBsTyp is the asset class, and it is the field that matters most for
# routing a holding to the right screen. Measured across four funds: an equity
# fund is 53 rows of hldBsTyp 1, a debt fund is all 2, and a balanced advantage
# fund is 159 / 168 / 4.
ASSET_CLASS = {1: "equity", 2: "debt", 3: "other"}

# EQUITY CARRIES astTypCd 13, WHICH IS NEVER IN THE INSTRUMENT VOCABULARY.
# `instrumentWiseHoldingDTO` lists only the DEBT breakdown, so every equity row
# resolves to instrument_type None. An earlier version then treated "type not
# in the not-in-NSDL list" as "expect to find it in NSDL", which flagged 53
# equity holdings of an equity fund as debt instruments that should have
# resolved and did not -- a manual queue made entirely of shares.
EQUITY_TYPE_CODE = 13

# Descriptions that are not credit exposure to any issuer. Matched on the TEXT
# rather than the code because the codes differ by fund -- net receivables is
# 83 in one and 96 in another, and cash is CBLO in one and Repo in the next.
# COMPANY NAMES THAT ARE NOT COMPANIES. ETMoney rolls small positions into a
# row literally called "Others" -- 4.88% across five funds in one run, sent to
# the manual review queue as an unresolved issuer. Nobody can look up
# "Others"; it is the fund's own aggregation bucket.
NON_ISSUER_COMPANIES = frozenset({"OTHERS", "OTHER", "OTHERS NET",
                                  "NET CURRENT ASSETS", "CASH", "TREPS"})

# UNITS OF ANOTHER FUND ARE NOT A BORROWER. A fund of funds holds "Mirae Asset
# Silver ETF - Growth" or "SBI Gold ETF" at 100% of itself, and the first run
# put exactly those at the top of the manual review queue -- someone was being
# asked to find the credit rating of an ETF. The plan suffix is the giveaway:
# no operating company is called "- Growth".
# WIDENED AFTER A REAL QUEUE. The first version caught the domestic ETFs and
# left every OVERSEAS unit behind, so a fund-of-funds holding "FSM First Trust
# Strategic Metal And Energy Equity UCITS Fund" at 99.5% sent someone off to
# find its credit rating. UCITS, SICAV and BeES are fund-vehicle names that no
# operating company carries, and a holding whose name ends in FUND or FUNDS is
# a scheme rather than a borrower.
FUND_UNIT_RE = re.compile(
    r"\bETF\b|\bFOF\b|\bFUND\s+OF\s+FUNDS?\b|"
    r"\bUCITS\b|\bSICAV\b|\bBeES\b|\bREIT\b|\bInvIT\b|"
    r"\bFUNDS?\b|"
    r"[-\s](?:GROWTH|IDCW|DIVIDEND)\s*$", re.I)

# INSTRUMENT TYPES THAT HAVE NO BORROWER. An interest rate swap is a contract
# with a counterparty, not a loan to an issuer -- 22 rows sat in the queue as
# `unresolved` asking for its credit rating. InvIT and REIT units are equity in
# a trust, and the trust is not borrowing from the fund. Matched on the
# instrument TYPE, which is where the source says so; the company-name rule
# only catches those whose names happen to contain the words.
NON_ISSUER_INSTRUMENTS_RE = re.compile(
    r"interest\s+rate\s+swap|\bIRS\b|"
    r"infrastructure\s+investment\s+trust|\bInvIT\b|"
    r"real\s+estate\s+investment\s+trust", re.I)

NON_ISSUER_RE = re.compile(
    r"net\s+(?:receivable|payable|current\s+asset)|repo|cblo|"
    r"alternative\s+investment\s+fund|term\s+deposit|fixed\s+deposit",
    re.I)

# Instrument descriptions absent from the NSDL corporate file, so a lookup on
# them legitimately misses. Named so the miss is explained rather than
# investigated again. CP and CD are money market paper; SDL and GOI are
# government; securitised debt is the trusts that have no issuer to look up.
NOT_IN_NSDL_RE = re.compile(
    r"certificate\s+of\s+deposit|commercial\s+paper|treasury\s+bill|"
    r"state\s+development\s+loan|goi\s+securit|government\s+securit|"
    r"securitised|securitized|"
    r"real\s+estate\s+investment|infrastructure\s+investment",
    re.I)

# SENIORITY, PER HOLDING, WHICH I SAID THE PAYLOAD DID NOT CARRY. It does, for
# the instruments where it matters most: "Additional Tier 1 Bond" appears as
# its own instrument type. That is ETMoney stating subordination directly, and
# it is independent of the NSDL description and sentinel rules -- so where both
# speak they can be compared, and where NSDL is silent this still answers.
#
# "Securitised Debt" is not subordination but it is the case with no issuer to
# resolve at all -- India Universal Trust AL1, Shriram-originated pools -- so
# it is named here to route those to the manual queue rather than to a failed
# lookup.
SENIORITY_BY_INSTRUMENT = (
    (re.compile(r"additional\s+tier\s*-?\s*1|\bat1\b|perpetual", re.I),
     "subordinated"),
    (re.compile(r"tier\s*-?\s*2|tier\s*-?\s*ii|subordinat", re.I),
     "subordinated"),
)
SECURITISED_RE = re.compile(r"securitis|securitiz", re.I)


class PortfolioError(ValueError):
    """Raised rather than returning empty holdings, which read as a fund that
    holds nothing."""


def _js_value_at(text, start):
    """Balanced {...} at `start`, string-aware.

    A brace inside a quoted company name would otherwise close the object
    early and truncate the holdings list silently.
    """
    if start >= len(text) or text[start] not in "{[":
        return None
    depth, in_str, esc, quote = 0, False, False, ""
    for j in range(start, len(text)):
        c = text[j]
        if esc:
            esc = False
        elif c == "\\":
            esc = True
        elif in_str:
            if c == quote:
                in_str = False
        elif c in "\"'":
            in_str, quote = True, c
        elif c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
            if depth == 0:
                return text[start:j + 1]
    return None


def extract_payload(html, var_name=VAR_NAME):
    """The portfolio JSON out of a saved portfolio-details page."""
    m = re.search(r"\b(?:var|let|const)\s+" + re.escape(var_name) + r"\s*=\s*",
                  html)
    if not m:
        raise PortfolioError(f"no `var {var_name}` in the page: either the "
                             f"fetch was blocked or ETMoney changed the name")
    raw = _js_value_at(html, m.end())
    if not raw:
        raise PortfolioError(f"`var {var_name}` found but its value is not "
                             f"balanced; the page is probably truncated")
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise PortfolioError(f"`var {var_name}` is not JSON: {exc}") from exc


def _lookup(mapping, key):
    """These maps are keyed by string in JSON but by int in the holdings."""
    if not isinstance(mapping, dict):
        return {}
    return mapping.get(str(key)) or mapping.get(key) or {}


def instrument_types(payload):
    """{astTypCd -> description} from the fund's own instrument breakdown."""
    dto = payload.get("instrumentWiseHoldingDTO") or {}
    return {h["instrumentCode"]: h.get("instrumentDescription")
            for h in (dto.get("holdings") or []) if "instrumentCode" in h}


def _date(value):
    """{'value': '31/07/2026'} -> date, and a bare 'YYYY-MM-DD' key too."""
    if isinstance(value, dict):
        value = value.get("value")
    if not value:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S"):
        try:
            return _dt.datetime.strptime(str(value).strip(), fmt).date()
        except ValueError:
            continue
    return None


# A FUND OF FUNDS HOLDS FUND UNITS BY DEFINITION, which is a fact about the
# holder and beats any rule about the holding's name. "Julius Baer Global
# Excellence Equity USD C" carries no ETF, UCITS or FUND marker and no name
# test will ever catch it -- but the fund holding it is an FoF, and that
# settles it without guessing.
FUND_OF_FUNDS_CATEGORY_RE = re.compile(r"\bFoF\b|FUND\s+OF\s+FUNDS?", re.I)


def is_fund_of_funds(category):
    return bool(category and FUND_OF_FUNDS_CATEGORY_RE.search(str(category)))


def holdings(payload, as_on=None, fund_category=None):
    """Holdings for one month, codes resolved. Latest month by default.

    `fund_category` is the holding fund's own SEBI category. When it names a
    fund of funds, every holding is a unit of another scheme and none is a
    borrower -- a fact about the holder, which beats any rule about the
    holding's name.

    Each row:
        as_on, company, company_id, sector, instrument_type, instrument_code,
        percent, value, is_issuer, expected_in_nsdl
    """
    hm = payload.get("mfHoldingMap") or {}
    if not hm:
        raise PortfolioError("no mfHoldingMap: the payload has no holdings")
    key = as_on or sorted(hm)[-1]
    if key not in hm:
        raise PortfolioError(f"no holdings dated {key}; have "
                             f"{sorted(hm)[0]}..{sorted(hm)[-1]}")
    comp, sect = (payload.get("mfCompanyDTOMap") or {},
                  payload.get("mfSectorDTOMap") or {})
    types = instrument_types(payload)
    fof = is_fund_of_funds(fund_category)

    out = []
    for r in hm[key]:
        code = r.get("astTypCd")
        c = _lookup(comp, r.get("cId"))
        desc = types.get(code)
        klass = ASSET_CLASS.get(r.get("hldBsTyp"), "other")
        company = c.get("company")
        securitised = bool(desc and SECURITISED_RE.search(desc))
        is_issuer = not (desc and (NON_ISSUER_RE.search(desc)
                                   or NON_ISSUER_INSTRUMENTS_RE.search(desc)))
        if company and (" ".join(company.upper().split())
                        in NON_ISSUER_COMPANIES
                        # A SECURITISATION TRUST IS AN ISSUER AND MUST STAY
                        # ONE. Widening the fund-unit test to catch REIT and
                        # FUND also catches "India Universal Trust AL1" and
                        # "Shivshakti Securitisation Trust", which would drop
                        # them out of the review queue entirely -- and those
                        # are exactly the holdings that need a person.
                        or (not securitised
                            and FUND_UNIT_RE.search(company))):
            is_issuer = False
        # The holder settles it where the name cannot.
        if fof and not securitised:
            is_issuer = False
        # EXPECTED IN NSDL ONLY WHEN EVERY CONDITION IS MET, never by default.
        # An unknown instrument type is not evidence that the NSDL file should
        # have had it; equity never is; and the named money-market and
        # government types never are.
        expected = bool(klass == "debt" and is_issuer and desc
                        and not NOT_IN_NSDL_RE.search(desc))
        seniority = None
        for pattern, verdict in SENIORITY_BY_INSTRUMENT:
            if desc and pattern.search(desc):
                seniority = verdict
                break
        out.append(dict(
            as_on=_date(key) or _date(r.get("astDt")),
            source_id=r.get("id"),
            company=company, company_id=r.get("cId"),
            sector=(_lookup(sect, r.get("sId")) or {}).get("sector"),
            instrument_type=desc, instrument_code=code,
            asset_class=klass,
            percent=r.get("astPer"), value=r.get("astVal"),
            is_issuer=is_issuer, expected_in_nsdl=expected,
            seniority=seniority, is_securitised=securitised))
    return sorted(out, key=lambda h: -(h["percent"] or 0))


def history(payload, fund_category=None):
    """{date -> holdings}. All twenty-five months, same shape as `holdings`."""
    return {k: holdings(payload, as_on=k, fund_category=fund_category)
            for k in sorted(payload.get("mfHoldingMap") or {})}


def by_issuer(rows):
    """Sum across instrument types. NABARD is three rows in one month.

    A 3% cap breached across three lines is still breached, so this is the
    view a concentration screen must use.
    """
    agg = {}
    for h in rows:
        if not h["is_issuer"]:
            continue
        k = h["company"] or f"cId:{h['company_id']}"
        d = agg.setdefault(k, dict(company=h["company"],
                                   company_id=h["company_id"],
                                   sector=h["sector"], percent=0.0,
                                   value=0.0, lines=0,
                                   instrument_types=set()))
        d["percent"] += h["percent"] or 0.0
        d["value"] += h["value"] or 0.0
        d["lines"] += 1
        if h["instrument_type"]:
            d["instrument_types"].add(h["instrument_type"])
    for d in agg.values():
        d["percent"] = round(d["percent"], 4)
        d["instrument_types"] = sorted(d["instrument_types"])
    return sorted(agg.values(), key=lambda d: -d["percent"])


def rating_buckets(payload):
    """ETMoney's aggregate rating split. The only rating on the page."""
    return {b["rating"]: b["percentage"]
            for b in (payload.get("mfRatingWiseHoldingDTOList") or [])}


def unresolved_companies(rows):
    """Holdings whose cId is not in the company map.

    Returned rather than dropped: a holding whose name is unknown is still a
    percentage of the fund and must not vanish from a concentration total.
    """
    return [h for h in rows if h["is_issuer"] and not h["company"]]


def reconcile_ratings(rows, rating_of, buckets, tolerance=2.0):
    """Weight our per-holding ratings by percent and compare to ETMoney's.

    THE ONLY VALIDATION AVAILABLE, since the payload carries no per-holding
    rating. `rating_of(holding) -> grade or None` is the caller's lookup --
    normally CreditLookup.by_name on the company.

    Returns per-bucket (ours, theirs, gap) plus `unrated_percent`, which is the
    number to read first: if a fifth of the fund could not be rated at all, the
    other comparisons are noise.

    `tolerance` is ABSOLUTE percentage points, which means a bucket smaller
    than the tolerance would pass on nothing at all. See the zero guard below.
    """
    ours, unrated = {}, 0.0
    for h in rows:
        pct = h["percent"] or 0.0
        grade = rating_of(h) if h["is_issuer"] else None
        if not grade:
            unrated += pct
            continue
        ours[grade] = ours.get(grade, 0.0) + pct
    rows_out = []
    for grade in sorted(set(ours) | set(buckets)):
        o, t = round(ours.get(grade, 0.0), 2), buckets.get(grade)
        # THE ZERO GUARD, AND WHY IT IS NOT PEDANTRY. `tolerance` is in
        # absolute percentage points, so any bucket SMALLER than it passes
        # whatever we produce -- SOV at 0.82% and Others at 0.28% both sit
        # inside a 2-point tolerance while we contribute nothing at all. A
        # lookup that rated the entire fund None scored two matches out of
        # five before this, which is the same shape of silent pass as a check
        # that returns zero on every real file.
        covered = o > 0.0 or not t
        rows_out.append(dict(rating=grade, ours=o, theirs=t,
                             gap=None if t is None else round(o - t, 2),
                             within_tolerance=(t is not None and covered
                                               and abs(o - t) <= tolerance)))
    return dict(buckets=rows_out, unrated_percent=round(unrated, 2),
                matched=sum(1 for r in rows_out if r["within_tolerance"]))


def asset_allocation(payload, as_on=None):
    """Equity / Debt / Other percentages, derived from hldBsTyp.

    THE SAME THREE NUMBERS THE FUND'S MAIN PAGE PRINTS -- "Equity 0%, Debt
    95.07%, Other 4.93%" -- but computed from the portfolio-details payload
    already in hand, so the main page does not also have to be fetched. If the
    two ever disagree, the main page is the one to trust and this is the one
    that is wrong, because it is a derivation and that is a statement.

    WHY THIS MATTERS BEYOND CONCENTRATION. The equity share decides the TAX
    bucket, not merely the risk profile: at or above 65% domestic equity a
    hybrid is taxed as equity (20% STCG, 12.5% LTCG); between 35% and 65% it
    falls in the old debt bucket at 12.5% LTCG beyond 24 months; below 35% it
    is a specified debt fund under s.50AA and taxed at slab rate whatever the
    holding period. A fund that drifts across 65% changes its own tax
    treatment, and this is the series that shows it happening.

    CAVEAT THAT MATTERS FOR EXACTLY THAT USE. The 65% test is on DOMESTIC
    equity, and hldBsTyp does not distinguish domestic from overseas. For a
    fund holding foreign equity this number is an upper bound on the tax-
    relevant share, not the share itself.
    """
    return allocation_of(holdings(payload, as_on=as_on))


def allocation_of(rows):
    """The same three numbers from rows already in hand.

    Exists because the caller has usually just built them: computing holdings
    twice per month doubled the parse for no gain.
    """
    return {k: round(sum(h["percent"] or 0.0 for h in rows
                         if h["asset_class"] == k), 2)
            for k in ("equity", "debt", "other")}


def allocation_history(payload):
    """{date -> asset_allocation}. Twenty-five months of drift."""
    return {k: asset_allocation(payload, as_on=k)
            for k in sorted(payload.get("mfHoldingMap") or {})}


def summarise(payload):
    rows = holdings(payload)
    scheme = payload.get("currentSchemeDetailDto") or {}
    hm = payload.get("mfHoldingMap") or {}
    return dict(
        scheme_id=scheme.get("schemeId"), asset_size_cr=scheme.get("assetSize"),
        expense_ratio=scheme.get("expenseRatio"),
        months=len(hm), first_month=min(hm, default=None),
        last_month=max(hm, default=None),
        holdings=len(rows), issuers=len(by_issuer(rows)),
        percent_total=round(sum(h["percent"] or 0 for h in rows), 2),
        unresolved=len(unresolved_companies(rows)),
        asset_allocation=asset_allocation(payload),
        # DEBT EXPOSURE TO AN ISSUER THAT NSDL WILL NOT HAVE. Non-issuer
        # rows -- repo, net receivables -- are excluded, because they are not
        # lookups that failed, they are not lookups at all. Counting them made
        # an equity fund report 1.85% "not in NSDL" when the whole of it was
        # cash and receivables.
        not_in_nsdl_percent=round(
            sum(h["percent"] or 0 for h in rows
                if h["asset_class"] == "debt" and h["is_issuer"]
                and not h["expected_in_nsdl"]), 2),
        lookupable_percent=round(
            sum(h["percent"] or 0 for h in rows if h["expected_in_nsdl"]), 2),
        non_issuer_percent=round(
            sum(h["percent"] or 0 for h in rows if not h["is_issuer"]), 2),
        subordinated_percent=round(
            sum(h["percent"] or 0 for h in rows
                if h["seniority"] == "subordinated"), 2),
        securitised_percent=round(
            sum(h["percent"] or 0 for h in rows if h["is_securitised"]), 2),
        rating_buckets=rating_buckets(payload))
