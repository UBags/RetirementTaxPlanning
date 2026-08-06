# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""borrower.py -- which kind of borrower is on the other side of the bond.

EIGHT CATEGORIES, TESTED IN ORDER, DEFAULTING TO OTHER_CORPORATE. The order is
the whole design: a PSU bank is also a bank and also a corporate, and the first
rule that fires wins, so the sequence encodes which fact matters most.

    GoI              the sovereign
    State            a state government
    PSU Bank         a bank or FI the government has recapitalised before
    Large Pvt Bank   one of the five systemically large private banks
    Pvt Bank         any other private bank
    NBFC             non-bank lender, including housing finance
    Large Corporate  a non-financial issuer above the size floor
    Other Corporate  everything else, and every case that is not clear

WHY NBFC IS TESTED AFTER THE BANKS AND NOT BEFORE. "Non-Banking Financial
Company (NBFC)" contains the word "banking", and a naive bank test put 801
NBFCs in the private-bank band. `caps.is_bank_or_fi` already refuses that, and
this module calls it rather than re-deriving it, so the two cannot drift.

WHY LARGE CORPORATE IS A SIZE TEST AND NOT A LIST. A hand-kept list of large
companies is a list of the companies someone thought of. The NSDL file states
each instrument's issue size, so total outstanding issuance per issuer is
available for nothing and is the same measure for every borrower. The floor is
a named choice, not a fact -- see LARGE_CORPORATE_FLOOR.

WHY THE PRIVATE BANKS ARE A LIST. There is no size column that separates HDFC
Bank from a small private bank, because the NSDL file sizes INSTRUMENTS and a
bank's balance sheet is not its bond programme. The five are named explicitly
and the list is short enough to argue with.
"""
from __future__ import annotations

import re

from fund_filters import caps as _caps
from fund_filters import instruments as _instruments

GOI = "GoI"
STATE = "State"
PSU_BANK = "PSU Bank"
LARGE_PVT_BANK = "Large Pvt Bank"
PVT_BANK = "Pvt Bank"
NBFC = "NBFC"
LARGE_CORPORATE = "Large Corporate"
OTHER_CORPORATE = "Other Corporate"

CATEGORIES = (GOI, STATE, PSU_BANK, LARGE_PVT_BANK, PVT_BANK, NBFC,
              LARGE_CORPORATE, OTHER_CORPORATE)

DEFAULT = OTHER_CORPORATE

# The five private banks in the Reserve Bank's domestic systemically important
# list plus the two next by assets. A NAMED CHOICE: the boundary between a
# large private bank and a private bank is not a fact in any column, and this
# is the line drawn rather than discovered. Matched on a distinctive token so
# "HDFC Bank" is not confused with "HDFC Limited" or "HDB Financial Services".
LARGE_PRIVATE_BANKS = (
    "HDFC BANK", "ICICI BANK", "AXIS BANK", "KOTAK MAHINDRA BANK",
    "INDUSIND BANK",
)

# Total outstanding issuance, in rupees, above which a non-financial issuer is
# LARGE_CORPORATE. Rs 5,000 crore. A NAMED CHOICE: it is roughly the level at
# which an issuer's paper is liquid enough for a fund to exit in a stress, and
# it is stated here so it can be moved in one place and re-run.
LARGE_CORPORATE_FLOOR = 5_000e7

_NBFC_MARKERS = ("non-banking financial", "non banking financial", "nbfc",
                 "housing finance", "financial institution", "finance company")

# A bank by name where the sector column is silent. The NSDL sector column is
# blank on 1,183 rows and this is the fallback, not the primary test.
_BANK_NAME_RE = re.compile(r"\bBANK\b", re.I)


def _t(v):
    return (v or "").strip().lower()


def issuer_totals(records):
    """{issuer_key -> total issue size}. Computed once over the whole file.

    Rows with no issue size contribute nothing rather than raising: a missing
    size should not promote an issuer, and it should not demote one either.
    """
    out = {}
    for r in records:
        k = r.get("issuer_key")
        if not k:
            continue
        out[k] = out.get(k, 0.0) + (r.get("issue_size") or 0.0)
    return out


def is_large_private_bank(issuer):
    up = " ".join((issuer or "").upper().split())
    return any(name in up for name in LARGE_PRIVATE_BANKS)


def is_nbfc(sector, issuer=""):
    s = _t(sector)
    if any(m in s for m in _NBFC_MARKERS):
        return True
    # Sector blank: fall back to the name, but only when it does not say bank.
    if not s and issuer:
        up = issuer.upper()
        if _BANK_NAME_RE.search(up):
            return False
        return any(m.upper() in up for m in
                   ("FINANCE", "FINSERV", "FINCORP", "HOUSING FINANCE",
                    "CAPITAL", "CREDIT"))
    return False


def categorise(rec, issuer_total=None):
    """The category for one record. Never raises, never returns None.

    `issuer_total` is that issuer's total outstanding issuance, from
    `issuer_totals`. Passing None means the size test cannot run, and the
    record falls to OTHER_CORPORATE rather than guessing upward -- an unknown
    borrower is not a large one.
    """
    tier = rec.get("tier")
    if tier == _instruments.SOVEREIGN:
        return GOI
    if tier == _instruments.STATE:
        return STATE

    sector = rec.get("sector") or ""
    ownership = rec.get("ownership") or ""
    issuer = rec.get("issuer") or ""

    bank = _caps.is_bank_or_fi(sector)
    if not bank and not sector and _BANK_NAME_RE.search(issuer.upper()):
        bank = True

    if bank:
        if _caps.is_psu(ownership) or "public sector" in _t(sector):
            return PSU_BANK
        if is_large_private_bank(issuer):
            return LARGE_PVT_BANK
        return PVT_BANK

    if is_nbfc(sector, issuer):
        return NBFC

    if issuer_total is not None and issuer_total >= LARGE_CORPORATE_FLOOR:
        return LARGE_CORPORATE

    return DEFAULT


def categorise_all(records):
    """Category for every record, with the size test in play.

    Returns a NEW list; the caller keeps a comparable copy.
    """
    totals = issuer_totals(records)
    out = []
    for r in records:
        d = dict(r)
        d["borrower_category"] = categorise(r, totals.get(r.get("issuer_key")))
        out.append(d)
    return out


def summarise(records):
    counts = {}
    for r in records:
        c = r.get("borrower_category", DEFAULT)
        counts[c] = counts.get(c, 0) + 1
    return {c: counts.get(c, 0) for c in CATEGORIES if counts.get(c)}
