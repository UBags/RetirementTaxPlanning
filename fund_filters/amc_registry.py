# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""amc_registry.py -- who runs each fund, and what that does and does not buy.

READ THIS BEFORE USING THE FILTER, because the obvious use of it is the one the
evidence does not support.

WHAT THIS DOES NOT DO: SCREEN CREDIT RISK. The five funds in nav_sample.csv
that took permanent credit writedowns are run by Tata, UTI and LIC Mutual Fund:

    Tata Treasury Advantage Fund      -4.06%   DHFL, 2019-06-04
    Tata Money Market Fund            -5.95%   IL&FS, 2018-10-29
    UTI Ultra Short Duration Fund     -2.78%   DHFL, 2019-06-04
    UTI Low Duration Fund             -8.77%   DHFL, 2019-06-04
    LIC MF Low Duration Fund          -6.09%   DHFL, 2019-06-04

Tata Sons. UTI, sponsored by SBI, LIC, Punjab National Bank and Bank of Baroda.
LIC Mutual Fund, backed by the largest state insurer in the country. If any
three fund houses in India pass a solidity-of-sponsor test, these do. A filter
built on sponsor pedigree would have excluded NONE of them and prevented
NOTHING. The sharpest case is not even in this sample: Franklin Templeton, a
globally recognised name, wound up six Indian debt schemes in April 2020 and
froze roughly 25,000 crore of investor money for years.

NOR DOES EXCESS YIELD WARN IN ADVANCE. Measured over the eighteen months before
each event, the mean trailing twelve-month return of those funds against their
own category median ran from -0.29pp to +0.32pp, against a thirteen-fund
control group averaging -0.19pp with a standard deviation of 0.45pp. Every
affected fund sat INSIDE the control distribution. DHFL carried AAA and AA+
ratings until days before default, so the yield pickup was 50 to 100 basis
points on a small slice of portfolio -- 10 to 20 at fund level, inside the
noise of expense ratios and duration positioning.

THE HONEST CONCLUSION: CREDIT RISK IS NOT VISIBLE IN NAV DATA. What would have
shown it is the monthly portfolio disclosure -- issuer concentration and the
share held below AAA -- which every AMC publishes and which this project does
not currently ingest. That, not this module, is the answer to "how do we avoid
the next DHFL", and it is a data-acquisition problem rather than a modelling
one.

SO WHAT IS THIS FOR. Three risks that sponsor quality genuinely bears on, none
of which is credit:

    CONTINUITY. Scheme mergers, AMC sales and wind-ups break a NAV series and
    silently truncate history. A fund whose AMC changed hands twice has a
    track record that is three funds wearing one name.

    GATING AND OPERATIONAL FAILURE. The decision to suspend redemptions is a
    governance decision, and governance is what a sponsor supplies.

    DISCLOSURE QUALITY. The data screens in quality.py find misprints and
    re-bases. Which AMCs produce them is worth tracking.

Use it to choose which funds to MODEL, not to believe you have avoided
defaults. Anyone reading a tier label as a safety rating has read it wrong, and
the two paragraphs above are why.

ACCURACY AND MAINTENANCE. Sponsor arrangements change often -- HSBC took over
L&T's schemes, Bandhan took over IDFC's, and new entrants keep arriving. Every
entry carries `checked`, the date the classification was last reviewed. Nothing
here is authoritative against AMFI's own register; treat a stale entry as
unknown rather than as a pass.
"""
from __future__ import annotations

CHECKED = "2026-08"

# Sponsor archetypes. The label describes WHO STANDS BEHIND THE AMC, which is
# the only thing this file claims to know. It says nothing about performance
# and, as the docstring labours, nothing about credit.
BANK = "bank_sponsored"              # an Indian bank owns or co-owns the AMC
INSURER = "insurer_sponsored"        # a large insurer stands behind it
GLOBAL_JV = "global_jv"              # joint venture with a global manager
CONGLOMERATE = "conglomerate"        # large diversified Indian group
STANDALONE = "standalone"            # independent asset manager
NEW_ENTRANT = "new_entrant"          # short operating history in Indian MF

# name fragment -> (archetype, note). Matching is on a lowercase substring of
# the scheme name, because that is what AMFI files give us.
AMC_REGISTRY = {
    "sbi": (BANK, "State Bank of India with Amundi"),
    "hdfc": (BANK, "HDFC Bank group"),
    "icici prudential": (BANK, "ICICI Bank with Prudential plc"),
    "kotak": (BANK, "Kotak Mahindra Bank subsidiary"),
    "axis": (BANK, "Axis Bank"),
    "canara robeco": (BANK, "Canara Bank with ORIX"),
    "union": (BANK, "Union Bank of India"),
    "baroda bnp paribas": (BANK, "Bank of Baroda with BNP Paribas"),
    "bank of india": (BANK, "Bank of India"),
    "idbi": (BANK, "IDBI Bank lineage; schemes since absorbed"),
    "indiafirst": (INSURER, "Bank of Baroda and Union Bank life venture"),
    "lic": (INSURER, "Life Insurance Corporation of India"),
    "nippon india": (INSURER, "Nippon Life; formerly Reliance Mutual Fund"),
    "sundaram": (CONGLOMERATE, "Sundaram Finance"),
    "aditya birla sun life": (GLOBAL_JV, "Aditya Birla with Sun Life"),
    "franklin": (GLOBAL_JV, "Franklin Templeton; SEE THE WIND-UP NOTE"),
    "hsbc": (GLOBAL_JV, "HSBC; absorbed the L&T schemes"),
    "invesco": (GLOBAL_JV, "Invesco"),
    "mirae": (GLOBAL_JV, "Mirae Asset, Korea"),
    "pgim": (GLOBAL_JV, "Prudential Financial US"),
    "jm ": (STANDALONE, "JM Financial"),
    "dsp": (STANDALONE, "DSP group; formerly DSP BlackRock"),
    "tata": (CONGLOMERATE, "Tata Sons and Tata Investment Corporation"),
    "uti": (INSURER, "SBI, LIC, PNB and Bank of Baroda; T Rowe Price stake"),
    "edelweiss": (STANDALONE, "Edelweiss"),
    "motilal oswal": (STANDALONE, "Motilal Oswal Financial Services"),
    "quant": (STANDALONE, "Quant; formerly Escorts Mutual Fund"),
    "ppfas": (STANDALONE, "Parag Parikh"),
    "parag parikh": (STANDALONE, "Parag Parikh"),
    "360 one": (STANDALONE, "360 ONE; formerly IIFL"),
    "iifl": (STANDALONE, "now 360 ONE"),
    "bandhan": (BANK, "Bandhan Bank consortium; absorbed IDFC schemes"),
    "idfc": (BANK, "schemes transferred to Bandhan"),
    "mahindra manulife": (GLOBAL_JV, "Mahindra with Manulife"),
    "whiteoak": (NEW_ENTRANT, "WhiteOak Capital"),
    "navi": (NEW_ENTRANT, "Navi group"),
    "groww": (NEW_ENTRANT, "Groww; absorbed Indiabulls schemes"),
    "indiabulls": (NEW_ENTRANT, "schemes transferred to Groww"),
    "taurus": (STANDALONE, "Taurus; small and long-standing"),
    "shriram": (STANDALONE, "Shriram group"),
    "quantum": (STANDALONE, "Quantum"),
    "trust": (NEW_ENTRANT, "TRUST Mutual Fund"),
    "samco": (NEW_ENTRANT, "Samco"),
    "helios": (NEW_ENTRANT, "Helios Capital"),
    "zerodha": (NEW_ENTRANT, "Zerodha with smallcase"),
    "old bridge": (NEW_ENTRANT, "Old Bridge Capital"),
    "unifi": (NEW_ENTRANT, "Unifi Capital"),
    "angel one": (NEW_ENTRANT, "Angel One"),
    "jio blackrock": (NEW_ENTRANT, "Jio Financial with BlackRock"),
    "capitalmind": (NEW_ENTRANT, "Capitalmind"),
}

# AMCs whose schemes carry a specific, documented historical event that a
# modeller should know about. This is a MEMORY, not a verdict.
NOTES = {
    "franklin": "wound up six debt schemes in April 2020, freezing investor "
                "capital for an extended period; a globally recognised sponsor "
                "did not prevent it",
    "uti": "held DHFL paper across several short-duration schemes in 2019",
    "tata": "held IL&FS in 2018 and DHFL in 2019",
    "lic": "held DHFL paper in 2019",
}

# Archetypes admitted by default. NEW_ENTRANT is excluded for want of HISTORY,
# not for want of quality: a synthesizer calibrated on three years of NAVs is
# calibrated on noise, and that is a statistical objection rather than a
# judgement about the firm.
DEFAULT_ADMITTED = (BANK, INSURER, GLOBAL_JV, CONGLOMERATE, STANDALONE)


def identify(scheme_name):
    """(amc_key, archetype, note) for a scheme name, or (None, None, None).

    LONGEST MATCH WINS. 'ICICI Prudential Savings Fund' must not resolve on a
    bare 'prudential' shared with PGIM, and 'Baroda BNP Paribas' must not
    resolve on 'bank of india'.
    """
    key = " ".join(str(scheme_name).lower().split())
    best = None
    for frag in AMC_REGISTRY:
        if frag in key and (best is None or len(frag) > len(best)):
            best = frag
    if best is None:
        return None, None, None
    arch, note = AMC_REGISTRY[best]
    return best, arch, note


def admit(scheme_name, admitted=DEFAULT_ADMITTED, unknown_ok=False):
    """Is this scheme in the investible universe? Returns (bool, reason).

    AN UNRECOGNISED AMC IS REFUSED BY DEFAULT rather than admitted, because the
    registry is maintained by hand and a silent pass on a name nobody added is
    indistinguishable from a considered approval.
    """
    key, arch, note = identify(scheme_name)
    if key is None:
        return (unknown_ok,
                "AMC not in the registry; add it rather than assume it")
    if arch not in admitted:
        return False, f"{key}: {arch} -- {note}"
    extra = NOTES.get(key)
    reason = f"{key}: {arch}" + (f" -- NOTE: {extra}" if extra else "")
    return True, reason


def partition(scheme_names, **kw):
    """(admitted, refused) as lists of (name, reason)."""
    yes, no = [], []
    for n in scheme_names:
        ok, why = admit(n, **kw)
        (yes if ok else no).append((n, why))
    return yes, no
