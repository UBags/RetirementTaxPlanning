# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""portfolio_credit.py -- what a fund's holdings are actually worth in credit.

THE JOIN THAT MAKES THE REST OF IT WORTH ANYTHING. `etmoney_portfolio` knows
what a fund holds and `lookup.api` knows what a borrower is rated. Neither has
ever spoken to the other, so nothing yet answers the only question that
matters: how much of this fund is exposed to what, and at what seniority.

NO ISINs. ETMoney publishes company names, not ISINs, so every rating here
comes from `by_name` -- fuzzy matched, with a runner-up and an ambiguity flag.
That is weaker than an ISIN lookup and the output says so on every row:
`rating_source` is 'name' and `rating_confidence` carries the score. A caller
that treats these as equal to an ISIN match is misreading them.

SENIORITY COMES FROM ETMONEY, NOT FROM NSDL, AND THE REASON IS STRUCTURAL.
Seniority is a property of the INSTRUMENT. Without an ISIN there is no
instrument, only a borrower -- and a borrower can have senior paper and a
perpetual outstanding at once. ETMoney names the instrument type per holding,
including "Additional Tier 1 Bond", so that is the only per-holding seniority
available and NSDL cannot improve on it here. What NSDL adds is
`issuer_has_subordinated`: not which instrument this is, but whether this
borrower has such paper in issue at all.

THE THREE WAYS A HOLDING FAILS TO RESOLVE, AND THEY NEED DIFFERENT HANDLING:

  not_in_nsdl   CP and CD whose ISSUER did not resolve either. Where the
                issuer IS in NSDL with a long-term grade, the short-term grade
                is derived from it rather than queued -- see
                SHORT_TERM_FROM_LONG. Absent from the NSDL corporate file
                by construction, so the miss is EXPLAINED and no human should
                ever look at it. On a money market fund this is ~100%.

                AND THE SCALES DIFFER, WHICH IS THE DEEPER REASON. A
                certificate of deposit carries a SHORT-TERM rating, A1+, while
                NSDL publishes the issuer's LONG-TERM grade. SIDBI is AAA long
                and A1+ short. Handing a CD the long-term grade is not a
                conservative approximation, it is a category error, and it
                showed up immediately as 86.0% AAA against ETMoney's 77.31%.
                The borrower is still resolved for these -- category and the
                subordinated warning are facts about the company -- but the
                rating is left empty and the holding is QUEUED, because the
                short-term grade is published and a person can read it off in
                a minute.

                SDL, GOI securities and treasury bills are NOT in this class.
                They are sovereign by virtue of what they are, so they are
                answered by rule before any lookup happens.
  securitised   India Universal Trust AL1 and the rest. There is no issuer to
                resolve; the originator may be findable but the trust is not.
                A manual queue with a real answer at the end of it.
  unresolved    A name that should have matched and did not, AND whose
                corporate group could not answer either. THE ONLY ONE THAT
                INDICTS THE LOOKUP, and the only one worth reading first.

GROUP-IMPLIED RATINGS ARE LABELLED AND UNTRUSTED. NSDL is the corporate BOND
database, so a company issuing only commercial paper is absent from it --
HDFC Securities, Kotak Mahindra Securities and Aditya Birla Finance all return
zero rows, which is about 2% of the debt book with no rating obtainable. Where
the company belongs to a recognised group, the group's rating is used, marked
`group_implied` or `group_short_term`, given reduced confidence and
rating_trusted=False. It is a judgement: CRISIL rates HDFC Securities in its
own right, and IL&FS was a AAA group with a subsidiary that defaulted.

WEIGHTED BY PERCENT, ALWAYS. A 0.03% securitisation trust and a 7% unresolved
issuer are not the same problem, and a queue sorted by row count puts them in
the wrong order. Everything here is reported as a share of the fund.

THE AGGREGATE CHECK IS THE VALIDATION. ETMoney publishes rating buckets --
AAA 77.31%, A1+ 7.87%, SOV 0.82%. Our per-holding ratings weighted by percent
must reproduce them. That is the only way to find out whether the name matching
is working at scale, because nobody is going to eyeball 1,500 funds.
"""
from __future__ import annotations

import re

from fund_filters import borrower as _borrower
from ingest import etmoney_portfolio as _portfolio

# A name match below this is reported but not trusted for a rating. It is
# deliberately ABOVE lookup.api.MIN_SCORE: that floor decides whether a name
# resolved at all, this one decides whether the answer is good enough to size
# a position on, and the second question is stricter than the first.
TRUST_SCORE = 0.90

# Reasons a holding carries no rating. Ordered by how much attention each
# deserves, most first.
# Government paper needs no lookup and no person. A state development loan is
# sovereign because of WHAT IT IS, not because of who issued it, and the same
# holds for GOI securities and treasury bills. Queueing "Maharashtra State" for
# a human to go and discover that it is sovereign is a waste of the queue.
#
# This is a RULE, not a lookup, which is why it lives here as a pattern rather
# than as rows someone has to maintain.
GOVERNMENT_INSTRUMENTS = (
    (re.compile(r"state\s+development\s+loan|\bSDL\b", re.I),
     _borrower.STATE),
    (re.compile(r"goi\s+securit|government\s+securit|treasury\s+bill|"
                r"\bg-?sec\b|cash\s+management\s+bill", re.I),
     _borrower.GOI),
)
# THE BORROWER'S NAME, NOT ONLY THE INSTRUMENT'S TYPE. The rule above reads
# instrument_type, and "GOI" appeared in the review queue at 76.58% across 27
# funds as `unresolved` -- a person being asked to confirm that the Government
# of India is sovereign, because its holdings carry an instrument type the
# pattern above does not name.
#
# Matched on whole words and anchored where the name is an abbreviation, so
# "GOI" is the borrower and "GOI Bonds Trading Ltd" would not be. State
# development loans are already caught by instrument type; the state's own
# name is not added here because a list of state names would also match every
# company called after one.
SOVEREIGN_COMPANIES = (
    (re.compile(r"^\s*GOI\s*$|government\s+of\s+india|"
                r"^\s*GOVT\.?\s+OF\s+INDIA\s*$|"
                r"reserve\s+bank\s+of\s+india", re.I), _borrower.GOI),
)

SOVEREIGN_GRADE = "SOV"

# Money market paper specifically -- certificates of deposit and commercial
# paper. Narrower than NOT_IN_NSDL_RE, which also covers government paper and
# securitisations; those are answered elsewhere.
MONEY_MARKET_RE = re.compile(
    r"certificate\s+of\s+deposit|commercial\s+paper|\bCD\b|\bCP\b", re.I)

# THE AGENCIES' OWN LONG-TERM TO SHORT-TERM LINKAGE. I had been queueing every
# bank CD for a person on the grounds that NSDL publishes a long-term grade and
# a CD carries a short-term one. The scale difference is real; the conclusion
# was not. Canara Bank is in the NSDL file with 16 rated instruments, Indian
# Bank with 4, Axis Bank with 9 -- and a person sent to find their CD rating
# would apply this table, which the rating agencies publish precisely so that
# the two scales can be related.
#
# CONSERVATIVE WHERE THE MAPPING IS A RANGE. An issuer at A+ may carry A1+ or
# A1 depending on its liquidity; the weaker is taken, because a short-term
# grade guessed upward is the error that costs money.
#
# IT IS A CONVENTION, NOT AN IDENTITY, and the output says so: the rating
# source is 'short_term_derived' and the confidence is reduced. An issuer can
# hold a strong short-term profile while its long-term rating slips, and this
# table cannot see that.
SHORT_TERM_FROM_LONG = {
    "AAA": "A1+", "AA+": "A1+", "AA": "A1+", "AA-": "A1+",
    "A+": "A1", "A": "A1", "A-": "A2+",
    "BBB+": "A2", "BBB": "A3", "BBB-": "A3",
    "BB+": "A4", "BB": "A4", "BB-": "A4",
    "B+": "A4", "B": "A4", "B-": "A4",
    "C+": "A4", "C": "A4", "C-": "A4", "D": "D",
}

# How much confidence a derived short-term grade carries. A NAMED CHOICE: the
# issuer is certain, the scale conversion is a convention.
DERIVED_CONFIDENCE = 0.80


def short_term_grade(long_grade):
    """The short-term grade implied by a long-term one, or None."""
    if not long_grade:
        return None
    return SHORT_TERM_FROM_LONG.get(" ".join(str(long_grade).upper().split()))

UNRESOLVED = "unresolved"
SECURITISED = "securitised"
NOT_IN_NSDL = "not_in_nsdl"
NOT_AN_ISSUER = "not_an_issuer"
EQUITY = "equity"
RESOLVED = None


def _group_fallback(lookup, name):
    """The group-implied result for a company, or None. Tolerant of a lookup
    that predates the mechanism, so a stub in a test need not implement it."""
    fn = getattr(lookup, "by_group", None)
    if fn is None or not name:
        return None
    res = fn(name)
    return res if res and res.get("found") else None


def enrich(rows, lookup, trust_score=TRUST_SCORE, groups=True):
    """Holdings with rating, borrower category and seniority attached.

    `rows` from `etmoney_portfolio.holdings`; `lookup` a
    `lookup.api.CreditLookup`. Returns a new list; mutates nothing.

    Each row gains

        rating, rating_confidence, rating_source, rating_trusted
        borrower_category      always set -- Other Corporate is the default
        seniority              from ETMoney's instrument type, or 'unknown'
        issuer_has_subordinated  does this BORROWER have perpetuals out
        gap                    None when resolved, else why not
        ambiguous, runner_up   from the fuzzy match, for the doubtful ones
    """
    out = []
    for h in rows:
        d = dict(h)
        d.update(rating=None, rating_confidence=None, rating_source=None,
                 rating_trusted=False,
                 borrower_category=_borrower.DEFAULT,
                 seniority=h.get("seniority") or "unknown",
                 issuer_has_subordinated=None, ambiguous=False,
                 runner_up=None, gap=None)

        if not h.get("is_issuer"):
            d["gap"] = NOT_AN_ISSUER
            out.append(d)
            continue
        if h.get("asset_class") == "equity":
            # An equity holding has no credit rating and never will. It is a
            # concentration exposure, not a credit one.
            d["gap"] = EQUITY
            out.append(d)
            continue
        if h.get("is_securitised"):
            d["gap"] = SECURITISED
            out.append(d)
            continue

        desc = h.get("instrument_type") or ""
        gov = next((cat for pattern, cat in GOVERNMENT_INSTRUMENTS
                    if pattern.search(desc)), None)
        if gov is None:
            company_name = h.get("company") or ""
            gov = next((cat for pattern, cat in SOVEREIGN_COMPANIES
                        if pattern.search(company_name)), None)
        if gov:
            d.update(rating=SOVEREIGN_GRADE, rating_source="instrument",
                     rating_confidence=1.0, rating_trusted=True,
                     borrower_category=gov, gap=None)
            out.append(d)
            continue

        name = h.get("company")
        res = lookup.by_name(name) if name else None
        issuer_grade = res["rating"] if res and res["found"] else None
        if res is not None and res["found"]:
            # THE BORROWER IS ALWAYS TAKEN. Category and the subordinated
            # warning are facts about the COMPANY and hold whatever instrument
            # is held, so SIDBI is a PSU bank whether the fund owns its bond
            # or its certificate of deposit.
            d.update(rating_confidence=res["confidence"],
                     borrower_category=(res["borrower_category"]
                                        or _borrower.DEFAULT),
                     issuer_has_subordinated=res["issuer_has_subordinated"],
                     ambiguous=bool(res["ambiguous"]),
                     runner_up=res["runner_up"])

        # THE RATING IS NOT, AND THE REASON IS THAT THE SCALES DIFFER. A
        # certificate of deposit carries a SHORT-TERM rating -- A1+ -- and
        # NSDL publishes the issuer's LONG-TERM rating. SIDBI is AAA long and
        # A1+ short; they are not the same statement and neither converts to
        # the other. Borrowing the long-term grade for a CD produced 86.0% AAA
        # against ETMoney's 77.31%, and the 8.69 difference was exactly the
        # CDs and the state development loan.
        #
        # So money market paper, government paper and anything else outside
        # the NSDL corporate file gets a borrower and no rating.
        if not h.get("expected_in_nsdl"):
            # MONEY MARKET PAPER FROM A RATED ISSUER IS DERIVABLE, not a job
            # for a person. Canara Bank sat in the review queue at 16.45%
            # across 161 funds while being present in NSDL with 16 rated
            # instruments -- queued because its CDs are on a different scale,
            # which the agencies publish a mapping for.
            long_grade = (issuer_grade
                          if (res and res["found"]
                              and (res["confidence"] or 0) >= trust_score)
                          else None)
            group = None
            if long_grade is None and groups:
                # THE COMPANY IS NOT IN NSDL AT ALL, which for a commercial
                # paper issuer is the normal case: NSDL is the corporate BOND
                # database, and HDFC Securities, Kotak Mahindra Securities and
                # Aditya Birla Finance return zero rows because they issue no
                # listed bonds. The group is the only answer available.
                group = _group_fallback(lookup, name)
                if group:
                    long_grade = group["rating"]
                    if group["borrower_category"]:
                        d["borrower_category"] = group["borrower_category"]
            derived = (short_term_grade(long_grade)
                       if MONEY_MARKET_RE.search(desc or "") else None)
            if derived:
                d.update(rating=derived,
                         rating_source=("group_short_term" if group
                                        else "short_term_derived"),
                         rating_confidence=(group["confidence"] if group
                                            else DERIVED_CONFIDENCE),
                         rating_trusted=not group, gap=None)
            elif group:
                d.update(rating=group["rating"],
                         rating_source="group_implied",
                         rating_confidence=group["confidence"],
                         rating_trusted=False, gap=None)
            else:
                d["gap"] = NOT_IN_NSDL
            out.append(d)
            continue

        if res is not None and res["found"]:
            trusted = (res["confidence"] or 0) >= trust_score
            if trusted and res["rating"] is not None:
                d.update(rating=res["rating"], rating_source=res["source"],
                         rating_trusted=True)
                out.append(d)
                continue
        elif res is not None:
            d["rating_confidence"] = res["confidence"]

        # A BOND ISSUER THAT DID NOT RESOLVE CAN STILL HAVE A GROUP. Tata
        # Motors Finance and Tata Capital Financial Services are absent from
        # NSDL -- merged into their parents -- and the group answers where the
        # name cannot.
        group = _group_fallback(lookup, name) if groups else None
        if group:
            d.update(rating=group["rating"], rating_source="group_implied",
                     rating_confidence=group["confidence"],
                     rating_trusted=False, gap=None,
                     borrower_category=(group["borrower_category"]
                                        or d["borrower_category"]))
        else:
            d["gap"] = UNRESOLVED
        out.append(d)
    return out


def _pct(rows, predicate):
    return round(sum(r["percent"] or 0.0 for r in rows if predicate(r)), 2)


def coverage(rows):
    """How much of the fund got a rating, and why the rest did not.

    READ `unresolved` FIRST AND THE OTHERS SECOND. The others are explained --
    equity has no rating, money market paper is not in the file, a trust has no
    issuer. `unresolved` is a name that should have matched and did not, and it
    is the only number that says the lookup is failing.
    """
    return dict(
        rated=_pct(rows, lambda r: r["rating"] is not None),
        unresolved=_pct(rows, lambda r: r["gap"] == UNRESOLVED),
        securitised=_pct(rows, lambda r: r["gap"] == SECURITISED),
        not_in_nsdl=_pct(rows, lambda r: r["gap"] == NOT_IN_NSDL),
        equity=_pct(rows, lambda r: r["gap"] == EQUITY),
        not_an_issuer=_pct(rows, lambda r: r["gap"] == NOT_AN_ISSUER),
        ambiguous=_pct(rows, lambda r: r["ambiguous"]),
        subordinated=_pct(rows, lambda r: r["seniority"] == "subordinated"),
        issuer_has_subordinated=_pct(
            rows, lambda r: bool(r["issuer_has_subordinated"])))


def by_category(rows):
    """Fund share by borrower category. The eight buckets, weighted."""
    out = {}
    for r in rows:
        if r["gap"] in (NOT_AN_ISSUER, EQUITY):
            continue
        c = r["borrower_category"]
        out[c] = round(out.get(c, 0.0) + (r["percent"] or 0.0), 2)
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


# What a person can actually answer. THE TEST IS NOT "IS IT IN NSDL" BUT "CAN
# SOMEONE SUPPLY IT" -- an earlier version excluded not_in_nsdl on the grounds
# that no attention makes an NSDL file contain a treasury bill, which is true
# and beside the point: a Bank of Baroda certificate of deposit carries a
# published A1+ that a person can read off CRISIL's site in a minute. That
# exclusion also disagreed with india.pending_rating_review, which included
# them -- two places implementing one policy differently.
QUEUEABLE = (UNRESOLVED, SECURITISED, NOT_IN_NSDL)


def manual_queue(rows, floor=0.10):
    """What a human must look at, largest first, and nothing else.

    `floor` drops holdings too small to be worth anyone's time -- a 0.03%
    securitisation trust cannot move a portfolio and should not consume a
    person. A NAMED CHOICE: 0.10% of a fund.

    Equity and cash never appear: no rating exists to find. Government paper
    never appears either, because it is answered by rule before it gets here.
    """
    q = [r for r in rows
         if r["gap"] in QUEUEABLE and (r["percent"] or 0) >= floor]
    return sorted(q, key=lambda r: -(r["percent"] or 0))


def reconcile(rows, payload, tolerance=2.0):
    """Our weighted ratings against ETMoney's published buckets.

    THE ONLY VALIDATION THAT SCALES. Nobody is going to eyeball 1,500 funds,
    and this is the number that says whether the name matching works.
    """
    return _portfolio.reconcile_ratings(
        rows, lambda h: h.get("rating"),
        _portfolio.rating_buckets(payload), tolerance=tolerance)


def report(payload, lookup, as_on=None, floor=0.10, sample=15):
    """One page per fund."""
    rows = enrich(_portfolio.holdings(payload, as_on=as_on), lookup)
    cov, cats = coverage(rows), by_category(rows)
    rec = reconcile(rows, payload)
    alloc = _portfolio.asset_allocation(payload, as_on=as_on)
    q = manual_queue(rows, floor=floor)

    lines = [
        "credit profile", "-" * 68,
        f"  asset allocation   {alloc}",
        "",
        "coverage (% of fund)",
        f"  rated              {cov['rated']:>7}",
        f"  UNRESOLVED         {cov['unresolved']:>7}   <- read this first",
        f"  securitised        {cov['securitised']:>7}",
        f"  not in NSDL        {cov['not_in_nsdl']:>7}   (CP/CD/SDL/GOI)",
        f"  equity             {cov['equity']:>7}",
        f"  not an issuer      {cov['not_an_issuer']:>7}   (cash, repo)",
        f"  ambiguous match    {cov['ambiguous']:>7}",
        f"  subordinated       {cov['subordinated']:>7}",
        "",
        "borrower category (% of fund)",
    ]
    for c, p in cats.items():
        lines.append(f"  {c:<20}{p:>7}")
    lines += ["", "rating check against ETMoney's own buckets",
              f"  unrated by us      {rec['unrated_percent']:>7}"]
    for b in rec["buckets"]:
        # A BUCKET WE NEVER RATE IS NOT A MISMATCH. A1+, SOV and "Cash & Call
        # Money" are excluded by design -- short-term paper is on another
        # scale, government paper is not in the NSDL file, and cash is not a
        # rating at all. Printing MISMATCH against them buried the one line
        # that mattered under four that never could match.
        if b["theirs"] is not None and b["ours"] == 0.0:
            mark = "n/a  (not rated by design)"
        elif b["within_tolerance"]:
            mark = "ok"
        else:
            mark = "MISMATCH"
        theirs = "-" if b["theirs"] is None else f"{b['theirs']:.2f}"
        lines.append(f"  {b['rating']:<20}ours {b['ours']:>7}  "
                     f"theirs {theirs:>7}  {mark}")
    lines += ["", f"manual queue ({len(q)} holdings above {floor}%)"]
    for r in q[:sample]:
        lines.append(f"  {r['percent']:>6}  {r['gap']:<12}"
                     f"{(r['company'] or '?')[:40]:<42}"
                     f"{(r['instrument_type'] or '')[:22]}")
    return "\n".join(lines)
