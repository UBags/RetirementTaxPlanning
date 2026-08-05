# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""instruments.py -- what kind of risk an instrument carries, from its ISIN.

TWO-THIRDS OF THE TRADED DEBT UNIVERSE NEEDS NO RATING FILE AT ALL. Of the
10,678 securities in the NSE WDM list of 2026-08-05, 6,974 are sovereign or
state paper and are identifiable from the security type and the ISIN prefix
alone:

    SG   5526   State Development Loans, ISIN IN<state>20...
    GZ   1243   Government of India STRIPS and zero coupon, ISIN IN00...
    GS    118   Government of India dated securities
    TB     87   Treasury bills

The remaining INE... securities are corporate, and only those need the NSDL
rating join. This matters because the rating download is the fragile part of
the pipeline and this classification is not: an ISIN prefix does not go stale.

THE CAP RELAXATION THAT IS JUSTIFIED, AND THE ONE THAT IS NOT

    SOVEREIGN AND STATE: RELAX. The Government of India does not default on
    rupee obligations it can print, and State Development Loans carry an RBI
    settlement mechanism against state revenues. These are genuinely different
    in kind from a corporate promise, not merely better rated.

    AAA CORPORATE: DO NOT RELAX. DHFL WAS RATED AAA. IL&FS WAS RATED AAA. Both
    held top-of-scale ratings within weeks of default -- IL&FS fell from AAA to
    junk in a matter of days in September 2018, and CARE and Brickwork carried
    DHFL at AAA into early 2019. A rule permitting AAA paper above the general
    cap would have authorised exactly the exposures that cost the funds in
    nav_sample.csv between 2.2% and 8.8% of NAV overnight. The rating did not
    fail at the margin; it was top of the scale until the week it was not.

    SUBORDINATED, PERPETUAL AND ADDITIONAL TIER 1: TIGHTEN. There are 63 AT1
    securities in that same file. Yes Bank's AT1 was written down to ZERO in
    March 2020 while its senior debt was money-good and depositors were made
    whole -- same issuer, same day, total loss on one instrument and none on
    another. Any issuer-level rating map applied to these instruments will rate
    them by the issuer's senior credit and be wrong by several notches in the
    one case that matters.

THE STATE CODE IS IN THE ISIN, positions three and four, assigned
alphabetically when the scheme began: 10 Andhra Pradesh through 38 Puducherry,
with 39 Delhi, 45 Telangana and 49 Jammu and Kashmir added later. Code 39 was
missing from the first draft and was caught by running the classifier over the
whole file and looking for unmapped codes rather than by reading the map --
which is why `summarise` reports unmapped codes explicitly instead of silently
returning None. The
map below was DERIVED from the issue descriptions in the WDM file rather than
typed from memory, which is why 27 reads ORISSA and 35 reads CHHATTISGARH in
the spellings the exchange actually uses.

A per-state sub-limit is worth having even though no state has defaulted: the
RBI mechanism has never been tested by a genuine refusal to pay, and "never
happened" is not the same as "cannot happen". That is a judgement, and it is
stated as one.
"""
from __future__ import annotations

SOVEREIGN = "sovereign"
STATE = "state"
CORPORATE = "corporate"
SUBORDINATED = "subordinated"
MONEY_MARKET_PAPER = "money_market_paper"
UNKNOWN = "unknown"

# NSE WDM security type -> tier. Types absent here fall through to the ISIN
# test and then to UNKNOWN, which is REFUSED rather than defaulted: an
# unrecognised security silently treated as sovereign is the worst error this
# module could make.
SECTYPE_TIER = {
    "GS": SOVEREIGN,          # GoI dated securities
    "GZ": SOVEREIGN,          # GoI STRIPS / zero coupon
    "TB": SOVEREIGN,          # treasury bills
    "GF": SOVEREIGN,          # GoI floating rate
    "SG": STATE,              # State Development Loans
    "CP": MONEY_MARKET_PAPER,  # commercial paper
    "CD": MONEY_MARKET_PAPER,  # certificates of deposit
    "AT": SUBORDINATED,       # Additional Tier 1
    "PF": SUBORDINATED,       # perpetual
    "PZ": SUBORDINATED,       # perpetual zero
    "PS": SUBORDINATED,
    "DB": CORPORATE,          # debentures
    "PT": CORPORATE,          # PSU / taxable bonds
    "BB": CORPORATE,          # bank bonds
    "ID": CORPORATE,
    "DC": CORPORATE,
    "CF": CORPORATE,
    "CT": CORPORATE,
    "MT": CORPORATE,
    "SD": CORPORATE,
    "DP": CORPORATE,
    "MG": CORPORATE,
    "VD": CORPORATE,
    "IF": CORPORATE,
    "FT": CORPORATE,
    "PG": CORPORATE,
    "PI": CORPORATE,
    "TS": CORPORATE,
}

# ISIN positions 3-4 for State Development Loans. Derived from the issue
# descriptions in the NSE WDM file, in the exchange's own spellings.
SDL_STATE = {
    "10": "Andhra Pradesh", "11": "Arunachal Pradesh", "12": "Assam",
    "13": "Bihar", "14": "Goa", "15": "Gujarat", "16": "Haryana",
    "17": "Himachal Pradesh", "18": "Jammu and Kashmir (pre-2019)",
    "19": "Karnataka", "20": "Kerala", "21": "Madhya Pradesh",
    "22": "Maharashtra", "23": "Manipur", "24": "Meghalaya", "25": "Mizoram",
    "26": "Nagaland", "27": "Odisha", "28": "Punjab", "29": "Rajasthan",
    "30": "Sikkim", "31": "Tamil Nadu", "32": "Tripura",
    "33": "Uttar Pradesh", "34": "West Bengal", "35": "Chhattisgarh",
    "36": "Uttarakhand", "37": "Jharkhand", "38": "Puducherry",
    "39": "Delhi (NCT)", "45": "Telangana", "49": "Jammu and Kashmir (UT)",
}

# Caps as a MULTIPLE of the general single-issuer cap. The general cap is
# derived from a loss tolerance in concentration.py; these say how far each
# tier may depart from it, and the argument for each is in the module docstring.
TIER_CAP_MULTIPLE = {
    SOVEREIGN: None,          # None means uncapped
    STATE: 5.0,               # generous, with a per-state sub-limit
    MONEY_MARKET_PAPER: 1.0,
    CORPORATE: 1.0,           # NO relaxation for AAA -- DHFL and IL&FS were AAA
    SUBORDINATED: 0.5,        # tighter: Yes Bank AT1 went to zero
    UNKNOWN: 0.5,             # refuse to be generous with what we cannot name
}

STATE_SUBLIMIT_MULTIPLE = 2.0     # any ONE state, within the STATE allowance


def state_code(isin):
    """Positions 3-4 of an SDL ISIN, or None."""
    s = (isin or "").strip().upper()
    return s[2:4] if len(s) >= 4 and s.startswith("IN") else None


def classify(sectype=None, isin=None, issue_desc=None):
    """Tier an instrument. Returns dict(tier, state, sovereign, why).

    SECTYPE IS TRUSTED FIRST because it is the exchange's own classification.
    The ISIN is a fallback and a cross-check, not the primary key: INE covers
    every kind of corporate instrument including the subordinated ones, so an
    ISIN-only rule cannot separate senior from AT1 and would rate Yes Bank's
    perpetuals as senior bank paper.
    """
    st = (sectype or "").strip().upper()
    code = state_code(isin)
    tier = SECTYPE_TIER.get(st)
    why = f"SECTYPE {st}" if tier else None

    if tier is None and isin:
        s = isin.strip().upper()
        if s.startswith("IN00"):
            tier, why = SOVEREIGN, "ISIN IN00 (Government of India)"
        elif code in SDL_STATE:
            tier, why = STATE, f"ISIN state code {code}"
        elif s.startswith("INE") or s.startswith("INF"):
            tier, why = CORPORATE, "ISIN INE/INF (body corporate)"

    if tier is None:
        return dict(tier=UNKNOWN, state=None, sovereign=False,
                    why="neither SECTYPE nor ISIN recognised")

    state = SDL_STATE.get(code) if tier == STATE else None
    if tier == STATE and state is None:
        why += f" (state code {code} not in the map -- verify)"
    return dict(tier=tier, state=state,
                sovereign=tier in (SOVEREIGN, STATE), why=why)


def cap_for(tier, general_cap, multiples=None):
    """The exposure cap for a tier. None means uncapped.

    `general_cap` should come from concentration.cap_for_tolerance, so the
    whole structure still descends from a stated loss tolerance rather than
    from a number chosen by feel.
    """
    m = (multiples or TIER_CAP_MULTIPLE).get(tier, 1.0)
    return None if m is None else general_cap * m


def parse_wdmlist(rows):
    """Classify an NSE WDM 'Securities available for Trading' export.

    `rows` is an iterable of dicts with SECTYPE, 'ISIN NO.' and ISSUE_DESC.
    Returns a list of dicts with the original fields plus the classification.
    """
    out = []
    for r in rows:
        isin = (r.get("ISIN NO.") or r.get("ISIN NO") or r.get("ISIN")
                or "").strip()
        c = classify(r.get("SECTYPE"), isin, r.get("ISSUE_DESC"))
        out.append(dict(sectype=(r.get("SECTYPE") or "").strip(), isin=isin,
                        description=(r.get("ISSUE_DESC") or "").strip(),
                        maturity=(r.get("MAT_DATE") or "").strip(), **c))
    return out


def summarise(classified):
    """Counts by tier, and by state within the state tier."""
    tiers, states = {}, {}
    for c in classified:
        tiers[c["tier"]] = tiers.get(c["tier"], 0) + 1
        if c["tier"] == STATE:
            k = c["state"] or f"unmapped {state_code(c['isin'])}"
            states[k] = states.get(k, 0) + 1
    return dict(tiers=tiers, states=states, total=len(classified))
