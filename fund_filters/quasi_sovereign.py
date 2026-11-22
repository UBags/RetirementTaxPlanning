# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""quasi_sovereign.py -- borrowers the single-issuer cap should not bind on.

WHY A CONCENTRATION CAP HAS EXEMPTIONS AT ALL. The cap exists because an
issuer-specific default wipes out a position with no warning from the price
series. That reasoning does not transfer to a borrower the Government of India
would not let fail: NABARD, REC and NHAI do not default independently of the
sovereign, so 9% in NABARD is not the same risk as 9% in a private NBFC even
though the arithmetic is identical. A cap that flags them buries the breaches
that matter under the ones that do not -- on the first real fund, four of the
eight flagged issuers were government entities.

TWO EXEMPTION ROUTES, AND THEY ARE DIFFERENT KINDS OF FACT.

  by CATEGORY   GoI, State and PSU Bank come from borrower.categorise, which
                derives them from the NSDL sector and ownership columns. That
                is data, and it updates itself when NSDL does.
  by NAME       The fifteen quasi-sovereign corporates below are a LIST, and a
                list is a judgement someone made on a particular day. NTPC and
                IOCL are majority government-owned commercial companies, not
                government departments; including them is a view about implicit
                support, not a fact about their balance sheets. If that view is
                wrong the cap is too loose on exactly those names.

MATCHING IS ON WHOLE WORDS WITH ABBREVIATION TOLERANCE, because the source
writes "Nuclear Power Corpn. Of India Ltd." in one fund and "NPCIL" in the
next. Every pattern allows CORP/CORPN/CORPORATION and LTD/LIMITED, and every
one is anchored on word boundaries -- a substring test for "NPC" or "REC"
would fire inside unrelated names, which is the mistake that put 801 NBFCs in
the private-bank band.

THE EXEMPT ROW IS STILL STORED. Exemption sets over_cap to false; it does not
drop the exposure. A fund with 9.45% in NABARD is still a fund with 9.45% in
NABARD, and someone reading concentration should see it even though nothing is
flagged.
"""
from __future__ import annotations

import re

from fund_filters import borrower as _borrower

# Categories the cap does not bind on. Derived from NSDL data rather than kept
# by hand, so they follow the source.
EXEMPT_CATEGORIES = frozenset({_borrower.GOI, _borrower.STATE,
                               _borrower.PSU_BANK})

# Tolerances used throughout the patterns below.
_CORP = r"CORP(?:N|ORATION)?"
_LTD = r"(?:\s+(?:LTD|LIMITED))?"
_OF_INDIA = r"(?:\s+OF\s+INDIA)?"

# The fifteen. Each is (key, regex), and the regex must match on word
# boundaries. Order does not matter; the first hit wins and they are disjoint.
QUASI_SOVEREIGN = (
    ("NABARD",
     r"\bNABARD\b|\bNATIONAL\s+BANK\s+F(?:OR|OR\s+THE)\s+AGRI\w*"
     r"(?:\s*(?:&|AND)\s*|\s+)RURAL\s+DEV\w*"),
    ("REC",
     rf"\bREC{_LTD}\b|\bRURAL\s+ELECTRIFICATION\s+{_CORP}\b"),
    ("HUDCO",
     rf"\bHUDCO\b|\bHOUSING\s*(?:&|AND)\s*URBAN\s+DEV\w*\s+{_CORP}\b"),
    ("PFC",
     rf"\bPFC{_LTD}\b|\bPOWER\s+FINANCE\s+{_CORP}\b"),
    ("IRFC",
     rf"\bIRFC\b|\bINDIAN\s+RAIL\w*\s+FINANCE\s+{_CORP}\b"),
    ("NHAI",
     rf"\bNHAI\b|\bNATIONAL\s+HIGHWAYS?\s+AUTH\w*{_OF_INDIA}\b"),
    ("IREDA",
     r"\bIREDA\b|\bINDIAN\s+RENEWABLE\s+ENERGY\s+DEV\w*\s+AGENCY\b"),
    ("NHB",
     r"\bNHB\b|\bNATIONAL\s+HOUSING\s+BANK\b"),
    ("IIFCL",
     rf"\bIIFCL\b|\bINDIA\s+INFRA\w*\s+FINANCE\s+(?:COMPANY|{_CORP})\b"),
    ("NTPC",
     rf"\bNTPC\b|\bNATIONAL\s+THERMAL\s+POWER\s+{_CORP}\b"),
    ("NHPC",
     rf"\bNHPC\b|\bNATIONAL\s+HYDRO\w*\s+POWER\s+{_CORP}\b"),
    ("PGCIL",
     rf"\bPGCIL\b|\bPOWER\s*GRID\s+{_CORP}{_OF_INDIA}\b|\bPOWER\s*GRID{_LTD}\b"),
    ("IOCL",
     rf"\bIOCL\b|\bINDIAN\s*OIL\s+{_CORP}{_OF_INDIA}\b|\bINDIAN\s*OIL{_LTD}\b"),
    ("NPCIL",
     rf"\bNPCIL\b|\bNPC{_LTD}\b|\bNUCLEAR\s+POWER\s+{_CORP}{_OF_INDIA}\b"),
    ("SIDBI",
     r"\bSIDBI\b|\bSMALL\s+IND\w*\s+DEV\w*\s+BANK(?:\s+OF\s+INDIA)?\b"),
    # Added after it surfaced as the top flagged breach on the first real
    # fund: wholly government-owned, and its borrowings carry an explicit GoI
    # guarantee, which is a stronger claim than the implicit support the rest
    # of this list rests on.
    ("FCI",
     r"\bFCI\b|\bFOOD\s+{corp}(?:\s+OF\s+INDIA)?\b".format(corp=_CORP)),
)

_COMPILED = tuple((key, re.compile(pattern, re.I))
                  for key, pattern in QUASI_SOVEREIGN)

_PUNCT = re.compile(r"[^A-Z0-9&\s]+")


def _norm(name):
    """Upper-case, punctuation dropped, whitespace collapsed.

    "Nuclear Power Corpn. Of India Ltd." -> "NUCLEAR POWER CORPN OF INDIA LTD"
    """
    return " ".join(_PUNCT.sub(" ", str(name or "").upper()).split())


def match(name):
    """The quasi-sovereign key this name is, or None."""
    n = _norm(name)
    if not n:
        return None
    for key, pattern in _COMPILED:
        if pattern.search(n):
            return key
    return None


def is_quasi_sovereign(name):
    return match(name) is not None


def cap_exemption(company, borrower_category=None):
    """(exempt, reason). Reason names WHICH rule fired, not just that one did.

    A category exemption follows NSDL and updates itself; a name exemption is
    a list someone maintains. Reading a flagged fund six months from now, the
    difference decides whether to check the data or the list.
    """
    if borrower_category in EXEMPT_CATEGORIES:
        return True, f"category:{borrower_category}"
    key = match(company)
    if key:
        return True, f"quasi_sovereign:{key}"
    return False, None
