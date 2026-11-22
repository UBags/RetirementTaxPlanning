# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""caps.py -- the exposure cap for one instrument.

THE RULE, IN ONE TABLE.

    sovereign (GoI)                         uncapped
    state (SDL)                             STATE_CAP, with a per-state limit
    PSU bank or financial institution,
      AAA, senior                           EXEMPT
    private bank or financial institution,
      AAA, senior                           PRIVATE_BANK_AAA_CAP  (12.5%)
    everything else corporate               GENERAL_CAP           (3%)
    subordinated, perpetual, AT1,
      ANY issuer including PSU              SUBORDINATED_CAP      (2%)

Counted over the 28,670 corporate instruments in the NSDL active list:
818 qualify for the PSU exemption, 875 for the private-bank band, and 2,922
AAA-rated instruments get no relief at all because they are neither.

WHY AAA ALONE BUYS NOTHING. DHFL was rated AAA. IL&FS was rated AAA. Both held
top-of-scale ratings within weeks of default, and a rule relaxing the cap for
AAA would have authorised exactly the exposures that cost the funds in
nav_sample.csv between 2.2% and 8.8% of NAV overnight. The relief here attaches
to WHO the borrower is and WHERE the instrument sits in the capital structure,
never to the rating on its own.

WHY THE SUBORDINATED CAP IS TIGHTER THAN ITS NUMBER SUGGESTS. Everywhere else
the cost of a default is exposure times one minus recovery, and 45% is the
honest recovery assumption -- it is roughly what DHFL returned through Piramal.
AT1 IS DIFFERENT: Yes Bank's was written down to ZERO in March 2020 while its
senior debt was money-good and depositors were made whole. So the subordinated
cap is sized at zero recovery, and a 2% cap means the FULL 2% of NAV is at
risk, not 55% of it. The exemption also does not cross seniority: SBI's AT1 is
not SBI's senior paper, and a PSU exemption that leaked across the capital
structure would be worse than no exemption at all.

WHAT IS NOT SETTLED. PRIVATE_BANK_AAA_CAP sits at 12.5%, which is ABOVE SEBI's
single-issuer ceiling of 10% -- 12% with trustee and board approval. As a rule
for a portfolio you construct yourself that is a free choice. As a SCREEN over
mutual fund holdings it cannot reject anything, because no scheme may legally
hold that much of one issuer. Whether it should instead sit near 9.5%, where it
would actually bind, is a question the ETMoney holdings data will answer and
this module does not pretend to.

Nor is the general 3% final. `concentration.cap_for_tolerance` inverts the
arithmetic: at 45% recovery, 3% costs 1.65% of NAV per default, 2.75% costs
1.51% and 3.5% costs 1.93%; the binding practical constraint is issuer count,
since 3% needs 34 distinct issuers and 2.75% needs 37. Pick the loss you accept
and the cap follows.
"""
from __future__ import annotations

from . import instruments as _instruments

GENERAL_CAP = 0.030
PRIVATE_BANK_AAA_CAP = 0.125
SUBORDINATED_CAP = 0.020
STATE_CAP = 0.090
STATE_SUBLIMIT = 0.045
SOVEREIGN_CAP = None              # None means uncapped

# Recovery assumed when sizing each tier. Subordinated is ZERO, not 45%,
# because that is what Yes Bank's AT1 holders recovered.
RECOVERY_GENERAL = 0.45
RECOVERY_SUBORDINATED = 0.00

EXEMPT = "exempt"

_PSU_MARKERS = ("public sector undertaking", "psu")

# MATCHED AS WHOLE PHRASES, NOT SUBSTRINGS. A substring test for "bank" puts
# "Non-Banking Financial Company (NBFC)" in the bank band: 801 of the 875
# instruments that first landed there were NBFCs, which is precisely the
# category DHFL and IL&FS belonged to. The relief would have gone almost
# entirely to the riskiest sector in the file, and the mistake is the same one
# "Non PSU" containing "PSU" guards against ten lines above.
_BANK_SECTORS = ("private sector bank", "public sector bank",
                 "foreign bank", "small finance bank", "payments bank",
                 "financial institution")
_NOT_BANK = ("non-banking", "non banking")


def is_psu(ownership):
    """True for a public sector undertaking.

    'Non PSU' contains 'PSU' as a substring, so the negative is tested first.
    Getting this backwards would hand the exemption to all 24,445 non-PSU
    instruments and withhold it from the 1,406 that qualify.
    """
    t = " ".join(str(ownership or "").lower().split())
    if not t or "non psu" in t:
        return False
    return any(m in t for m in _PSU_MARKERS)


def is_bank_or_fi(sector):
    """A genuine bank or financial institution, per the NSDL sector column.

    An NBFC is NOT one, however its sector string is spelled. This returns
    False for anything containing 'non-banking' before it tests anything else.
    """
    t = " ".join(str(sector or "").lower().split())
    if any(m in t for m in _NOT_BANK):
        return False
    return any(m in t for m in _BANK_SECTORS)


def cap_for(tier, grade=None, ownership=None, sector=None, enhanced=False,
            general=GENERAL_CAP, private_bank=PRIVATE_BANK_AAA_CAP,
            subordinated=SUBORDINATED_CAP, state=STATE_CAP):
    """The cap for one instrument. Returns (cap, reason).

    A cap of None means uncapped; EXEMPT means no single-issuer limit applies
    but the instrument is still counted for sector and group limits.

    ORDER MATTERS. Seniority is tested BEFORE ownership, so no exemption can
    reach an AT1 bond however good its issuer.
    """
    if tier == _instruments.SOVEREIGN:
        return SOVEREIGN_CAP, "sovereign: rupee obligations the issuer prints"
    if tier == _instruments.STATE:
        return state, "state development loan: RBI settlement mechanism"

    # SENIORITY FIRST, unconditionally.
    if tier == _instruments.SUBORDINATED:
        return subordinated, ("subordinated/perpetual/AT1: sized at ZERO "
                              "recovery, Yes Bank AT1 returned nothing")

    if enhanced:
        return general, ("credit-enhanced: the grade describes a guarantee, "
                         "not the borrower")

    if grade == "AAA" and is_bank_or_fi(sector):
        # THE SECTOR COLUMN WINS WHEN THE TWO DISAGREE. Nine instruments in the
        # NSDL file carry the sector 'Public Sector Bank' while their ownership
        # column does not say PSU. A bank whose sector is public-sector IS a
        # public sector undertaking; the sector string is the more specific
        # claim and the ownership column is the one that is blank or wrong.
        if is_psu(ownership) or "public sector bank" in \
                " ".join(str(sector or "").lower().split()):
            return EXEMPT, ("PSU bank/FI at AAA: repeatedly recapitalised "
                            "rather than allowed to fail")
        return private_bank, ("private bank/FI at AAA: above SEBI's 12% "
                             "ceiling, so inert as a screen")

    if grade == "AAA":
        return general, ("AAA alone buys no relief: DHFL and IL&FS were both "
                         "AAA within weeks of default")
    return general, "general corporate"


def cap_for_record(rec, **kw):
    """Cap for a record produced by ingest.nsdl_securities.parse_workbook."""
    return cap_for(rec.get("tier"), grade=rec.get("worst_grade"),
                   ownership=rec.get("ownership"), sector=rec.get("sector"),
                   enhanced=rec.get("enhanced", False), **kw)


def max_loss(cap, tier=None):
    """NAV loss if this issuer defaults, at the tier's recovery assumption."""
    if cap is None or cap == EXEMPT:
        return None
    rec = (RECOVERY_SUBORDINATED if tier == _instruments.SUBORDINATED
           else RECOVERY_GENERAL)
    return cap * (1.0 - rec)


def summarise(records, **kw):
    """How many instruments land in each band."""
    out = {}
    for r in records:
        cap, reason = cap_for_record(r, **kw)
        key = EXEMPT if cap == EXEMPT else (
            "uncapped" if cap is None else f"{cap:.3%}")
        d = out.setdefault(key, dict(count=0, reason=reason))
        d["count"] += 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["count"]))
