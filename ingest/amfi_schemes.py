# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""amfi_schemes.py -- one base fund name per scheme, from NAVAll.txt.

THE JOB IS DEDUPLICATION, NOT SELECTION. AMFI lists every scheme once per plan
and option: Direct/Regular crossed with Growth/IDCW/Payout/Reinvestment, which
is six to eight rows for one fund. ETMoney has one portfolio per FUND, because
a portfolio is a property of the scheme and plans differ only in expense ratio
and NAV. So a filter is needed that keeps exactly one row per fund, and
Direct Plan + Growth is the one that does it: every open-ended scheme has
exactly one such row.

WHICH IS HARDER THAN MATCHING TWO WORDS, because Growth is also a fund name.
"Nippon India Growth Fund - Direct Plan - IDCW" contains both words and is an
IDCW row; "HDFC Growth Opportunities Fund" contains one of them permanently.
Requiring Growth to follow Direct in the string fixes the first case and breaks
"HDFC Liquid Fund - Growth Option - Direct Plan", where the option comes first.

So the name is split into SEGMENTS and the trailing ones that consist only of
plan and option words are the plan part. "Growth Fund" is not such a segment,
because "Fund" is not a plan word. The fund's own name survives containing
Growth, and the order of the trailing segments stops mattering.

WHY NOT REUSE scripts/get_amfi_fund_schemes_names.py. It has the download and
a suffix stripper, but every function in it is underscore-private and it
filters for REGULAR plans, which is the opposite selection. Calling into
another script's privates would couple this to something free to change
without notice; the rule is thirty lines and is stated here where it can be
tested.

PAYOUT VARIANTS ARE DROPPED BY WORD, AND "regular" BY POSITION. See
EXCLUDE_WORDS: four of the five never occur in a fund's own name, but
"regular" does -- ICICI Prudential Regular Savings Fund -- so it is excluded
only when it appears AFTER the last "fund" in the string.

RENAMES CARRY THE OLD NAME IN THE STRING. AMFI writes "Sundaram Banking & PSU
Fund Formerly Known as Sundaram Banking & PSU Debt Fund", and leaving that in
does two kinds of damage: the search gets a string twice as long as any real
fund name and scores 52 against the right answer, and two schemes differing
only in the old name they mention fail to deduplicate. Cut at "formerly".

CLOSED-END AND INTERVAL SCHEMES ARE DROPPED BEFORE ANY OF THIS. They cannot be
bought on a retail platform and they dominate the file -- roughly 690 of 2,295
names in one run were Fixed Maturity Plans, Fixed Horizon Funds and their
relatives, each costing two HTTP round trips to confirm that a scheme which
matured in 2019 is not for sale. The section header says which is which.

NOT EVERY BASE NAME WILL RESOLVE ON ETMONEY, and that is expected rather than
a fault: closed-end schemes, segregated portfolios and very new funds are in
AMFI and not on a retail platform. The batch runner counts them.
"""
from __future__ import annotations

import re
import urllib.request

NAV_URL = "https://portal.amfiindia.com/spages/NAVAll.txt"

TIMEOUT = 120

# SPLIT INTO SEGMENTS, THEN ASK WHICH ARE PLAN AND OPTION -- rather than
# matching plan words anywhere in the string, which two real names defeat:
#
#   "Nippon India Growth Fund - Direct Plan - Growth Option"
#        a regex tail-stripper eats "Growth Fund" and leaves "Nippon India"
#   "HDFC Liquid Fund - Growth Option - Direct Plan"
#        requiring Growth to follow Direct in the STRING drops the fund
#
# A trailing segment is plan-and-option when every alphabetic token in it is a
# plan or option word. "Growth Fund" is not, because "Fund" is not. So the
# fund's name survives having Growth in it, and the order of the trailing
# segments stops mattering -- which is what the position rule was really for.
_SEPARATOR = re.compile(r"\s*[-–—(),]\s*|\s{2,}")

# AMFI carries the old name inside the scheme name after a rename:
#   "Sundaram Banking & PSU Fund Formerly Known as Sundaram Banking & PSU
#    Debt Fund"
# Everything from "formerly" onward, WITHIN THE FUND-NAME PART, belongs to a
# fund that no longer exists. Left in, it does two kinds of damage: the search is handed a string twice as
# long as any real fund name and scores 52 against the right answer, and two
# schemes whose only difference is the old name they mention fail to
# deduplicate -- which is why Sundaram appeared twice in one run.
_FORMERLY = re.compile(
    r"\s*[-–—(,]?\s*\(?\s*(?:formerly|erstwhile|previously)\b.*$", re.I)

_PLAN_WORDS = {
    "direct", "regular", "plan", "growth", "idcw", "dividend", "payout",
    "reinvestment", "reinvest", "option", "options", "bonus", "cumulative",
    "daily", "weekly", "fortnightly", "monthly", "quarterly", "annual",
    "annually", "half", "yearly", "retail", "institutional", "super",
    "discontinued", "of", "and", "the", "unclaimed", "segregated",
}
_WORD = re.compile(r"[A-Za-z]+")


def _segments(name):
    return [x.strip() for x in _SEPARATOR.split(" ".join(
        str(name or "").split())) if x and x.strip()]


def _is_plan_segment(seg):
    words = _WORD.findall(seg.lower())
    return bool(words) and all(w in _PLAN_WORDS for w in words)


def _split(name):
    """(fund name segments, plan/option segments)."""
    segs = _segments(name)
    i = len(segs)
    while i > 0 and _is_plan_segment(segs[i - 1]):
        i -= 1
    return segs[:i], segs[i:]


# NAVAll.txt is sectioned. A line with no semicolon is either a category
# header or an AMC name, and the header is the ONLY place the scheme's
# category appears:
#
#   Open Ended Schemes ( Debt Scheme - Banking and PSU Fund )
#
#   Bajaj Finserv Mutual Fund
#   119551;INF0GJ501234;-;Bajaj Finserv Banking and PSU Fund-Direct Plan- Growth;...
#
# Discarding those headers is what left fund_score with nothing to rank
# within -- a percentile among peers needs to know who the peers are, and
# every fund in one bucket is not a ranking.
_SECTION = re.compile(
    r"^\s*(open\s+ended|close\s+ended|closed\s+ended|interval)\s+"
    r"(?:fund\s+)?schemes?\s*\((.*)\)\s*$", re.I)


def _section(line):
    """(scheme_type, category) if this line is a section header, else None."""
    m = _SECTION.match(line)
    if not m:
        return None
    kind = " ".join(m.group(1).split()).title()
    category = " ".join(m.group(2).split())
    return kind, category


def fetch_nav_text(url=NAV_URL, timeout=TIMEOUT, opener=None):
    op = opener or urllib.request.urlopen
    with op(url, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_nav_text(text):
    """Rows from NAVAll.txt, each carrying the section it appeared under.

    A line is a scheme only if its first field is numeric. Section state IS
    tracked, because the category lives nowhere else -- and without it
    fund_score has no peer group to rank within.
    """
    out = []
    scheme_type = category = amc = None
    for line in (text or "").splitlines():
        if ";" not in line:
            sec = _section(line)
            if sec:
                scheme_type, category = sec
                amc = None          # the AMC name follows the header
            elif line.strip() and not line.lower().startswith("scheme code"):
                amc = " ".join(line.split())
            continue
        parts = [p.strip() for p in line.split(";")]
        if len(parts) < 5 or not parts[0].isdigit():
            continue
        out.append(dict(scheme_code=int(parts[0]), isin=parts[1] or None,
                        name=parts[3], nav=parts[4], amc=amc,
                        scheme_type=scheme_type, category=category))
    return out


# Words that mark a payout-frequency variant rather than a fund.
#
# FOUR ARE UNCONDITIONAL, ONE IS POSITIONAL, and the split is the whole point.
# "daily", "monthly", "weekly" and "idcw" never appear in a fund's own name, so
# finding one anywhere settles it. "regular" DOES appear in product names --
# ICICI Prudential Regular Savings Fund, Aditya Birla Sun Life Regular Savings
# Fund -- and an unconditional rule dropped them, which is a real fund lost to
# a filter aimed at a plan variant.
#
# SO "regular" IS JUDGED BY POSITION. A plan marker comes AFTER the fund's
# name, and the fund's name ends in "fund":
#
#   ICICI Prudential Regular Savings Fund - Direct Plan - Growth   kept
#                    ^regular          ^fund
#   Some Debt Fund - Regular Plan - Growth                         dropped
#                 ^fund     ^regular
#
# Where there is no "fund" at all -- a handful of schemes are named "... Plan"
# or "... Scheme" -- the fallback is the last three words, separators not
# counted, which is where a plan marker sits when it is present.
# "segregated" is not a payout frequency but belongs to the same
# unconditional class: a segregated portfolio is a side-pocket holding
# defaulted paper, carries no NAV anyone can buy, and is not listed on a
# retail platform. "UTI Bond Fund Segregated 17022020" scored 48.1 against
# every candidate, which is the right answer arrived at expensively.
# "unclaimed" and "withheld" join them for the same reason as "segregated":
# these are IEPF holding accounts for money nobody collected -- "JM Liquid
# Fund Unclaimed Dividend IEF", "Canara Robeco Liquid Fund Unclaimed
# Redemption" -- and no one can invest in them. Seven appeared in one run of
# 370, each costing two HTTP round trips to be told no.
FREQUENCY_WORDS = ("daily", "monthly", "weekly", "idcw", "segregated",
                   "unclaimed", "withheld")
POSITIONAL_WORDS = ("regular",)
EXCLUDE_WORDS = FREQUENCY_WORDS + POSITIONAL_WORDS

# Separators are not words. "Fund - Regular - Growth" puts regular second of
# three, not third of five.
_WORDS_RE = re.compile(r"[A-Za-z]+")
POSITIONAL_TAIL_WORDS = 3


def _lower_words(name):
    return _WORDS_RE.findall(str(name or "").lower())


def excluded_word(name):
    """The excluded word this scheme name contains, or None."""
    words = _lower_words(name)
    if not words:
        return None
    for w in FREQUENCY_WORDS:
        if w in words:
            return w
    for w in POSITIONAL_WORDS:
        if w not in words:
            continue
        if "fund" in words:
            # After the LAST "fund": a name may contain the word twice, and
            # only a marker following all of them is a plan marker.
            last_fund = len(words) - 1 - words[::-1].index("fund")
            if any(i > last_fund for i, x in enumerate(words) if x == w):
                return w
        elif w in words[-POSITIONAL_TAIL_WORDS:]:
            return w
    return None


def is_direct_growth(name):
    """Direct and Growth both appear in the PLAN/OPTION part of the name.

    Tested on the trailing segments only, so "Nippon India Growth Fund -
    Direct Plan - IDCW" is correctly rejected -- its Growth belongs to the
    fund, not to the option -- and "HDFC Liquid Fund - Growth Option - Direct
    Plan" is correctly accepted despite the reversed order.
    """
    if excluded_word(name):
        return False
    _, tail = _split(name)
    words = {w for seg in tail for w in _WORD.findall(seg.lower())}
    return "direct" in words and "growth" in words


def base_name(name):
    """The fund's name, with plan and option removed.

    "Bajaj Finserv Banking and PSU Fund-Direct Plan- Growth"
        -> "Bajaj Finserv Banking and PSU Fund"
    """
    head, _ = _split(name)
    # THE CLAUSE IS CUT FROM THE HEAD, NOT FROM THE WHOLE STRING. The plan
    # suffix comes AFTER the old name -- "... Formerly Known as Sundaram
    # Banking & PSU Debt Fund - Direct Plan - Growth" -- so cutting at
    # "formerly" first takes the Direct-Plan-Growth marker with it and the
    # scheme stops being recognised as a growth option at all.
    return " ".join(_FORMERLY.sub("", " ".join(head)).split())


# Closed-end and interval schemes are not on a retail platform and cannot be
# bought, and they dominate the file: of 2,295 names attempted in one run, ~690
# were Fixed Maturity Plans, Fixed Horizon Funds, Interval Funds, Capital
# Protection Oriented Schemes and Dual Advantage series. Every one cost two
# HTTP round trips to be told no, which is 35 minutes of a two-hour run spent
# confirming that a scheme which matured in 2019 is not for sale.
#
# The section header already says which is which, so this is a filter and not
# a guess.
INVESTABLE_SCHEME_TYPES = ("Open Ended",)


def fund_names(rows, scheme_types=INVESTABLE_SCHEME_TYPES):
    """Deduplicated base names, with category, AMC and the codes behind each.

    Returns [{name, category, scheme_type, amc, scheme_codes, isins}] in
    first-seen order, which is AMC order in the file and therefore stable
    between runs -- so a batch that stops halfway resumes in the same place.

    The FIRST category seen wins where a base name somehow appears under two
    sections. That should not happen, and `category_conflicts` reports it if
    it does rather than letting the second silently overwrite the first.
    """
    seen = {}
    for r in rows:
        if scheme_types and r.get("scheme_type") not in scheme_types:
            continue
        if not is_direct_growth(r["name"]):
            continue
        base = base_name(r["name"])
        if not base:
            continue
        d = seen.setdefault(base, dict(
            name=base, category=r.get("category"),
            scheme_type=r.get("scheme_type"), amc=r.get("amc"),
            scheme_codes=[], isins=[], categories=set()))
        d["scheme_codes"].append(r["scheme_code"])
        if r.get("category"):
            d["categories"].add(r["category"])
        if r.get("isin"):
            d["isins"].append(r["isin"])
    for d in seen.values():
        d["categories"] = sorted(d["categories"])
    return list(seen.values())


def category_conflicts(funds):
    """Base names that appeared under more than one section header."""
    return [f for f in funds if len(f.get("categories") or []) > 1]


def excluded_by_word(rows):
    """What EXCLUDE_WORDS removed, so the casualties are visible.

    Only counts schemes that would OTHERWISE have qualified -- a Monthly IDCW
    row was never going to pass the Direct-Growth rule, and listing it here
    would bury the one case that matters.
    """
    out = {}
    for r in rows:
        w = excluded_word(r["name"])
        if not w:
            continue
        _, tail = _split(r["name"])
        words = {x for seg in tail for x in _WORD.findall(seg.lower())}
        if "direct" in words and "growth" in words:
            out.setdefault(w, []).append(base_name(r["name"]))
    return {w: sorted(set(v)) for w, v in out.items()}


def load(url=NAV_URL, opener=None, timeout=TIMEOUT,
         scheme_types=INVESTABLE_SCHEME_TYPES):
    """Download and reduce, in one call."""
    return fund_names(parse_nav_text(
        fetch_nav_text(url, timeout=timeout, opener=opener)),
        scheme_types=scheme_types)
