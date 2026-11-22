# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""groups.py -- corporate groups whose rating carries to their subsidiaries.

WHAT THIS IS FOR. NSDL is the corporate BOND database, so a company that issues
only commercial paper is absent from it entirely -- HDFC Securities, Kotak
Mahindra Securities, Aditya Birla Finance and Cholamandalam Securities all
return zero rows. That is 575 holdings and about 2% of the debt book with no
rating available, and no amount of matching fixes it because there is nothing
to match against.

WHAT THIS IS NOT. A group-implied rating is a JUDGEMENT, not an agency
opinion. CRISIL rates HDFC Securities in its own right and a broking
subsidiary can sit below its parent; IL&FS was a AAA group with a subsidiary
that defaulted. So every rating produced here is labelled `group_implied`,
carries reduced confidence, and is marked untrusted so a consumer can exclude
it wholesale.

THE WORST RATING IN THE GROUP, NOT THE BEST. Same principle as taking the worst
across an issuer's own paper: a rating guessed upward is the error that costs
money.

THE RELIANCE PROBLEM, WHICH IS WHY EVERY GROUP CARRIES AN EXCLUSION LIST. The
brand token alone is dangerous. "Reliance" names both Mukesh Ambani's Reliance
Industries, rated AAA, and Anil Ambani's Reliance Capital, which defaulted at
D. They share a word and nothing else. Matching on the token would either hand
Reliance Retail a D or hand Reliance Capital's paper a AAA, and the second is
the one that loses money. So the ADAG entities are excluded by name.

THE SAME CAUTION APPLIES WHEREVER A BRAND IS SHARED without common ownership,
and the list below will be wrong about some of them. It is a registry someone
maintains, stated in one place so it can be argued with, and `--no-groups`
turns the whole mechanism off.
"""
from __future__ import annotations

import re

# How much confidence a group-implied rating carries. A NAMED CHOICE, below
# every other source: the issuer is inferred, not identified.
GROUP_CONFIDENCE = 0.60

# (key, include, exclude, anchors)
#
# `include` matches a company into the group. `exclude` takes it back out, and
# exists because brands are shared. `anchors` name the entities whose rating
# should be preferred when several group members are in NSDL -- the flagship
# borrower rather than the smallest one.
_GROUPS = (
    ("TATA",
     r"\bTATA\b",
     r"\bTATA\s+TELESERVICES\b",
     (r"^TATA\s+SONS", r"^TATA\s+CAPITAL\s+LIMITED")),

    ("ADITYA BIRLA",
     r"\bADITYA\s+BIRLA\b|\bGRASIM\b|\bULTRATECH\b|\bHINDALCO\b",
     None,
     (r"^ADITYA\s+BIRLA\s+CAPITAL", r"^GRASIM")),

    ("RELIANCE",
     r"\bRELIANCE\b|\bJIO\b",
     # Anil Ambani's group. Shares the word and nothing else; Reliance Capital
     # defaulted at D while Reliance Industries is AAA.
     r"RELIANCE\s+(?:CAPITAL|HOME\s+FINANCE|COMMERCIAL|COMMUNICATIONS|"
     r"POWER|INFRASTRUCTURE|NAVAL|BIG|ANIL)",
     (r"^RELIANCE\s+INDUSTRIES", r"^RELIANCE\s+JIO")),

    ("ADANI", r"\bADANI\b", None, (r"^ADANI\s+PORTS",)),
    ("HDFC", r"\bHDFC\b", None, (r"^HDFC\s+BANK",)),
    ("LIC", r"\bLIC\b|LIFE\s+INSURANCE\s+CORPORATION", None,
     (r"^LIC\s+HOUSING\s+FINANCE",)),
    ("SBI", r"\bSBI\b|\bSBICAP\b|STATE\s+BANK\s+OF\s+INDIA", None,
     (r"^STATE\s+BANK\s+OF\s+INDIA",)),
    ("ICICI", r"\bICICI\b", None, (r"^ICICI\s+BANK",)),
    ("AXIS", r"\bAXIS\b", r"\bAXIS\s+CADES\b", (r"^AXIS\s+BANK",)),
    ("PNB", r"\bPNB\b|PUNJAB\s+NATIONAL\s+BANK", None,
     (r"^PUNJAB\s+NATIONAL\s+BANK",)),
    ("CANARA", r"\bCANARA\b", None, (r"^CANARA\s+BANK",)),
    ("KOTAK", r"\bKOTAK\b", None, (r"^KOTAK\s+MAHINDRA\s+BANK",)),
    ("BHARTI", r"\bBHARTI\b|\bAIRTEL\b", None, (r"^BHARTI\s+AIRTEL",)),
    ("ONGC", r"\bONGC\b|OIL\s+AND\s+NATURAL\s+GAS", None, (r"^ONGC",)),
    ("MAHINDRA", r"\bMAHINDRA\b", r"\bKOTAK\s+MAHINDRA\b",
     (r"^MAHINDRA\s*(?:&|AND)\s*MAHINDRA\s+LIMITED",)),
    ("INFOSYS", r"\bINFOSYS\b", None, ()),
    ("WIPRO", r"\bWIPRO\b", None, ()),
    ("VEDANTA", r"\bVEDANTA\b|HINDUSTAN\s+ZINC", None, (r"^VEDANTA",)),
    ("SAIL", r"\bSAIL\b|STEEL\s+AUTHORITY\s+OF\s+INDIA", None, ()),
    ("BAJAJ", r"\bBAJAJ\b", None, (r"^BAJAJ\s+FINANCE",)),
    ("CHOLAMANDALAM", r"\bCHOLAMANDALAM\b|\bMURUGAPPA\b", None,
     (r"^CHOLAMANDALAM\s+INVESTMENT",)),
    ("GODREJ", r"\bGODREJ\b", None, ()),
    ("TITAN", r"\bTITAN\b", None, ()),
    ("STANDARD CHARTERED", r"STANDARD\s+CHARTERED", None, ()),
    ("BARCLAYS", r"\bBARCLAYS\b", None, ()),
    ("360 ONE", r"\b360\s*ONE\b|\bIIFL\s+WEALTH\b", None, ()),

    # Groups with no NSDL member at all -- they fund through commercial paper
    # and certificates of deposit, not listed bonds -- so nothing can be
    # inferred for them and their grade is DECLARED below.
    #
    # IIFL sits after 360 ONE deliberately: 360 ONE was IIFL Wealth, and the
    # first matching group wins. The exclusion says so twice rather than
    # relying on the order alone.
    ("IIFL", r"\bIIFL\b", r"\bIIFL\s+WEALTH\b", ()),
    ("DSP", r"\bDSP\b", None, ()),
    ("CITY UNION BANK", r"CITY\s+UNION\s+BANK|\bCUB\b", None, ()),
    ("MIRAE", r"\bMIRAE\b", None, ()),
    ("DEUTSCHE", r"\bDEUTSCHE\b", None, ()),
    ("BALRAMPUR", r"\bBALRAMPUR\b", None, ()),
    ("NUVAMA", r"\bNUVAMA\b", None, ()),
    # No trailing boundary: the source writes "MashreqBank PSC" as one
    # word, and \bMASHREQ\b does not match inside it.
    ("MASHREQ", r"\bMASHREQ", None, ()),
    ("AVENUE SUPERMARTS", r"AVENUE\s+SUPERMARTS|\bDMART\b", None, ()),

    # Holding companies whose names carry no group brand at all. Panatone
    # Finvest is Tata's holding vehicle for Tata Communications and Birla
    # Group Holdings is Aditya Birla's; neither reads as such.
    #
    # "BIRLA GROUP", NOT "BIRLA". The surname is shared across three unrelated
    # families and NSDL has all of them: BIRLA CORPORATION at AA and BIRLA
    # TYRES are MP Birla and BK Birla, not Aditya Birla. A bare \bBIRLA\b
    # would hand them a declared AAA -- the Reliance trap wearing a different
    # coat.
    ("PANATONE", r"\bPANATONE\b", None, ()),
    ("BIRLA GROUP HOLDINGS", r"BIRLA\s+GROUP", None, ()),
    ("IDBI", r"\bIDBI\b", None, ()),
    ("INFINA", r"\bINFINA\b", None, ()),
    ("SHAREKHAN", r"\bSHAREKHAN\b", None, ()),
    ("CTBC", r"\bCTBC\b", None, ()),
)

# GRADES SUPPLIED BY HAND FOR A WHOLE GROUP, used only where NSDL has no
# member to infer from. Every group below funds through commercial paper and
# CDs rather than listed bonds, so it is absent from the corporate bond file
# entirely and no amount of matching reaches it.
#
# THIS IS A THIRD MECHANISM AND IT IS WORTH KEEPING DISTINCT. An override
# answers one NAME; an inferred group rating reads a rated SIBLING; a declared
# group rating is a person's answer applied to a SET. The output says which,
# via rating_source, so a reader can tell an agency opinion from a judgement.
#
# The same caution as everywhere else: these are as supplied on 2026-08-08 and
# they expire in the reader's head, not in the table. A group's credit is not
# a constant.
DECLARED_RATINGS = {
    "DSP": ("AA", "user-supplied 2026-08-08"),
    "CITY UNION BANK": ("AA", "user-supplied 2026-08-08"),
    "MIRAE": ("AA", "user-supplied 2026-08-08"),
    "DEUTSCHE": ("AA", "user-supplied 2026-08-08"),
    "BALRAMPUR": ("AA", "user-supplied 2026-08-08"),
    "NUVAMA": ("AA", "user-supplied 2026-08-08"),
    "IIFL": ("AA-", "user-supplied 2026-08-08"),
    "MASHREQ": ("AAA", "user-supplied 2026-08-08"),
    "AVENUE SUPERMARTS": ("AAA", "user-supplied 2026-08-08"),
    "PANATONE": ("AAA", "user-supplied 2026-08-08"),
    "BIRLA GROUP HOLDINGS": ("AAA", "user-supplied 2026-08-08"),
    "IDBI": ("AA", "user-supplied 2026-08-08"),
    "INFINA": ("AA", "user-supplied 2026-08-08"),
    "SHAREKHAN": ("AA", "user-supplied 2026-08-08"),
    "CTBC": ("AAA", "user-supplied 2026-08-08"),
}


def declared_rating(key):
    """(grade, note) supplied by hand for this group, or None."""
    return DECLARED_RATINGS.get(key)

_COMPILED = tuple(
    (key,
     re.compile(inc, re.I),
     re.compile(exc, re.I) if exc else None,
     tuple(re.compile(a, re.I) for a in anchors))
    for key, inc, exc, anchors in _GROUPS)

_PUNCT = re.compile(r"[^A-Z0-9&\s]+")


def _norm(name):
    return " ".join(_PUNCT.sub(" ", str(name or "").upper()).split())


def group_of(name):
    """The group key this company belongs to, or None.

    The FIRST match wins and the groups are disjoint by construction -- except
    Mahindra and Kotak Mahindra, where the exclusion on MAHINDRA keeps Kotak's
    entities out of the Mahindra group.
    """
    n = _norm(name)
    if not n:
        return None
    for key, inc, exc, _anchors in _COMPILED:
        if inc.search(n) and not (exc and exc.search(n)):
            return key
    return None


def anchors_for(key):
    for k, _inc, _exc, anchors in _COMPILED:
        if k == key:
            return anchors
    return ()


def members(key, names):
    """Which of `names` belong to this group."""
    return [n for n in names if group_of(n) == key]
