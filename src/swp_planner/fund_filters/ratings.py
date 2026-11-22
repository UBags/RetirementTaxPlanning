# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""ratings.py -- read a credit rating string, and know what it does not say.

FIVE AGENCIES WRITE THE SAME GRADE FIVE WAYS. CRISIL writes 'CRISIL AAA/Stable',
ICRA writes '[ICRA]AAA(Stable)', CARE writes 'CARE AAA; Stable', India Ratings
writes 'IND AAA/Stable' and Brickwork writes 'BWR AAA'. The NSDL file carries
whatever the issuer reported, in a column named
CREDIT_RATING_CREDIT_RATING_AGENCY with the agency and grade run together, and
sometimes with the rating in REMARKS instead. A parser that only understands one
house style silently returns nothing for four fifths of the file, and a missing
rating looks identical to an unrated instrument.

TWO SUFFIXES CHANGE THE MEANING ENTIRELY, AND THIS IS THE PART MOST EASILY
LOST.

    (CE) CREDIT ENHANCEMENT and (SO) STRUCTURED OBLIGATION mean the grade rests
    on a guarantee, a letter of comfort or a payment structure rather than on
    the borrower's own strength. AAA(CE) is not AAA. Strip the suffix and a
    weak issuer wearing a strong parent's guarantee is indistinguishable from
    the parent. Several IL&FS group SPVs carried (SO) ratings on the strength
    of the parent, and when the parent went the structure went with it.

    So `enhanced` is returned as a flag, and any cap policy should treat an
    enhanced rating as the STANDALONE grade or refuse it, never as the headline
    one.

A RATING IS A SNAPSHOT AND THE TRAJECTORY IS THE SIGNAL. DHFL was AAA into
early 2019 and IL&FS was AAA days before default, so the LEVEL fooled everyone
who looked at it. What did not fool anyone was the direction: both fell several
notches in weeks. `notch_change` exists so a store of dated observations can be
asked "what moved", and the ordinal scale exists so that question has an
arithmetic answer.

WHAT THIS MODULE DOES NOT DO. It does not fetch, and it does not decide. It
turns a string into (agency, grade, rank, enhanced) so that a database can hold
comparable values and a policy can act on them.
"""
from __future__ import annotations

import re

# Long-term scale, best first. The rank is an ORDINAL, not a probability: the
# gap between AAA and AA+ is not the gap between BB and BB-, and no arithmetic
# here should pretend otherwise. It exists to answer "did this move, and which
# way", which is the question that mattered in 2018 and 2019.
LONG_TERM_SCALE = [
    "AAA", "AA+", "AA", "AA-", "A+", "A", "A-",
    "BBB+", "BBB", "BBB-", "BB+", "BB", "BB-",
    "B+", "B", "B-", "C+", "C", "C-", "D",
]
RANK = {g: i for i, g in enumerate(LONG_TERM_SCALE)}

# Short-term scale, used for commercial paper and certificates of deposit. Kept
# SEPARATE and deliberately not merged into the long-term ranks: A1+ is not
# comparable to AAA, and an issuer can hold a strong short-term grade while its
# long-term outlook is deteriorating -- which is exactly the shape of a
# refinancing crisis.
SHORT_TERM_SCALE = ["A1+", "A1", "A2+", "A2", "A3+", "A3", "A4+", "A4", "D"]
SHORT_RANK = {g: i for i, g in enumerate(SHORT_TERM_SCALE)}

AGENCY_PATTERNS = [
    (re.compile(r"\bCRISIL\b", re.I), "CRISIL"),
    (re.compile(r"\[?\s*ICRA\s*\]?", re.I), "ICRA"),
    (re.compile(r"\bCARE\b", re.I), "CARE"),
    (re.compile(r"\bIND\b|\bINDIA\s+RATINGS\b|\bFITCH\b", re.I),
     "India Ratings"),
    (re.compile(r"\bBWR\b|\bBRICKWORK\b", re.I), "Brickwork"),
    (re.compile(r"\bACUITE?\b|\bSMERA\b", re.I), "Acuite"),
    (re.compile(r"\bINFOMERICS\b|\bIVR\b", re.I), "Infomerics"),
]

# Grades are matched longest-first so 'AA+' is never read as 'AA', and the
# token must not be followed by another grade character.
_GRADE_RE = re.compile(
    r"(?<![A-Z0-9])(" + "|".join(
        re.escape(g) for g in sorted(
            LONG_TERM_SCALE + SHORT_TERM_SCALE, key=len, reverse=True))
    + r")(?![A-Z0-9+\-])")

_ENHANCED_RE = re.compile(r"\(\s*(CE|SO)\s*\)", re.I)
_OUTLOOK_RE = re.compile(
    r"\b(stable|positive|negative|developing|watch)\b", re.I)
_WITHDRAWN_RE = re.compile(r"\bwithdraw|\bsuspend|\bnot\s+meaningful\b", re.I)
_NOT_COOPERATING_RE = re.compile(r"issuer\s+not\s+cooperat", re.I)


def parse(raw):
    """Read one rating string.

    Returns dict(agency, grade, scale, rank, enhanced, outlook, withdrawn,
    raw). `grade` is None when nothing recognisable is present, and that is
    NOT the same as unrated -- it means this string did not say.
    """
    text = " ".join(str(raw or "").strip().split())
    if not text:
        return _empty(text)

    agency = None
    for pat, name in AGENCY_PATTERNS:
        if pat.search(text):
            agency = name
            break

    enhanced = bool(_ENHANCED_RE.search(text))
    not_cooperating = bool(_NOT_COOPERATING_RE.search(text))
    withdrawn = bool(_WITHDRAWN_RE.search(text))
    outlook_m = _OUTLOOK_RE.search(text)

    # Remove the agency name before hunting for the grade: 'CARE A' would
    # otherwise be read as the grade 'A' sitting inside the word CARE, and
    # 'IND AAA' would trip on the 'D' in IND.
    hunt = text.upper()
    for pat, _ in AGENCY_PATTERNS:
        hunt = pat.sub(" ", hunt)
    hunt = _ENHANCED_RE.sub(" ", hunt)
    hunt = _OUTLOOK_RE.sub(" ", hunt)

    m = _GRADE_RE.search(hunt)
    if not m:
        return _empty(text, agency=agency, enhanced=enhanced,
                      withdrawn=withdrawn,
                      outlook=outlook_m.group(1).lower() if outlook_m else None)
    grade = m.group(1)
    if grade in RANK and grade not in SHORT_RANK:
        scale, rank = "long", RANK[grade]
    elif grade in SHORT_RANK and grade not in RANK:
        scale, rank = "short", SHORT_RANK[grade]
    else:
        # 'D' appears on both scales. Context decides, and without context the
        # long-term reading is the conservative one: it is the scale a
        # concentration policy is written against.
        scale, rank = "long", RANK[grade]
    return dict(agency=agency, grade=grade, scale=scale, rank=rank,
                enhanced=enhanced, not_cooperating=not_cooperating,
                outlook=outlook_m.group(1).lower() if outlook_m else None,
                withdrawn=withdrawn, raw=text)


def _empty(text, **kw):
    base = dict(agency=None, grade=None, scale=None, rank=None,
                enhanced=False, not_cooperating=False, outlook=None,
                withdrawn=False, raw=text)
    base.update(kw)
    return base


def split_multi(raw):
    """One NSDL cell into one parsed rating per agency.

    THE FILE PACKS SEVERAL AGENCIES INTO ONE CELL, semicolon separated:
    'CARE AAA/Stable ();ICRA AAA/Stable ();CRISIL AAA/Stable'. 4,274 rows of
    28,670 carry more than one. Parsing the cell as a single string finds
    whichever grade appears first and silently discards the rest -- including
    this real row, where the first opinion is four notches below the last:

        BWR BB-/Stable () (ISSUER NOT COOPERATING*);IND AA+/Stable ();
        ACUITE AA/Stable ()
    """
    text = str(raw or "")
    return [parse(p) for p in text.split(";") if p.strip()]


def any_not_cooperating(parsed_list):
    """Did ANY agency flag non-cooperation?

    A REGULATOR-MANDATED DISCLOSURE AND THE MOST PREDICTIVE FIELD IN THE FILE.
    It means the issuer stopped supplying information to its rating agency,
    which is a decision the issuer took, not an opinion the agency formed. 169
    of 28,670 rows carry it. Everything else in this module is descriptive;
    this is the one thing that looks forward.
    """
    return any(p.get("not_cooperating") for p in parsed_list if p)


def parse_any(*fields):
    """First parseable rating across several columns.

    The NSDL file puts the rating in CREDIT_RATING_CREDIT_RATING_AGENCY for
    most rows and in REMARKS for some, so both are tried in order and the
    first that yields a grade wins.
    """
    fallback = None
    for f in fields:
        got = parse(f)
        if got["grade"]:
            return got
        if fallback is None and (got["agency"] or got["raw"]):
            fallback = got
    return fallback or _empty("")


def is_investment_grade(parsed):
    """BBB- or better on the long scale, A3 or better on the short one.

    An ENHANCED rating is not treated as investment grade here, because the
    grade describes a structure rather than the borrower. Callers who want the
    headline grade can read it off `parsed` themselves; the default refuses.
    """
    if not parsed or not parsed["grade"] or parsed["enhanced"]:
        return False
    if parsed["scale"] == "long":
        return parsed["rank"] <= RANK["BBB-"]
    return parsed["rank"] <= SHORT_RANK["A3"]


def notch_change(older, newer):
    """Notches moved between two observations. NEGATIVE is a downgrade.

    Returns None when the two are not comparable -- different scales, or
    either unparsed. Comparing a short-term grade with a long-term one would
    produce a number, and the number would be meaningless.
    """
    if not older or not newer:
        return None
    if not older.get("grade") or not newer.get("grade"):
        return None
    if older["scale"] != newer["scale"]:
        return None
    return older["rank"] - newer["rank"]


def is_downgrade(older, newer, notches=1):
    n = notch_change(older, newer)
    return n is not None and n <= -notches


def worst(parsed_list):
    """The weakest grade among several agencies.

    WHEN AGENCIES DISAGREE, TAKE THE WORST. A concentration policy that reads
    the most flattering of five opinions is choosing its own answer.
    """
    known = [p for p in parsed_list
             if p and p.get("grade") and p.get("scale") == "long"]
    if not known:
        return None
    return max(known, key=lambda p: p["rank"])
