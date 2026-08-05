# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""concentration.py -- how much one default can cost, and what cap prevents it.

THE ONE THING IN THIS PROJECT THAT WOULD ACTUALLY HAVE PREVENTED THE LOSSES.
Sponsor pedigree did not (Tata, UTI and LIC MF all held DHFL). Return deviation
did not (the affected funds sat inside their peer distribution for eighteen
months beforehand). Concentration would have, because the loss from a default
is arithmetic:

    loss to NAV = exposure to the issuer x (1 - recovery rate)

and exposure is a choice made in advance rather than a fact discovered
afterwards.

WHAT THE HISTORY SAYS, backed out of the observed single-day NAV falls in
nav_sample.csv at an assumed 75% write-down:

    UTI Low Duration          fell 8.77%   implied exposure about 11.7%
    LIC MF Low Duration       fell 6.09%   implied about  8.1%
    Tata Money Market         fell 5.95%   implied about  7.9%
    Tata Treasury Advantage   fell 4.06%   implied about  5.4%
    UTI Ultra Short Duration  fell 2.78%   implied about  3.7%

EVERY ONE OF THOSE WAS LEGAL. SEBI's single-issuer limit is 10% of NAV,
extendable to 12% with trustee approval, and since 2022 rating-banded at 10%
for AAA, 8% for AA and 6% for A. The industry's worst cases were far beyond
this sample -- Tata Corporate Bond reportedly carried 28.21% DHFL and some
schemes fell 53% in a day -- but the funds here took real damage while inside
the rules. A limit that permits an 8.77% overnight loss is not protecting
principal; it is bounding the disaster.

THE ISSUER CAP IS NOT THE BINDING CONSTRAINT, AND THIS IS THE PART MOST EASILY
MISSED. IL&FS was September 2018. DHFL was June 2019. Both were NBFC/HFC --
the same sector, nine months apart. SEBI permits 20% in one sector plus an
additional 15% for housing finance. A scheme observing a 3% single-issuer cap
perfectly can still hold 35% of its NAV in financial-sector paper, and if that
sector seizes, a 3% issuer rule saves nothing: the loss is 35% x (1 - recovery),
about 19 points at the 45% DHFL recovery rate.

So a concentration policy needs BOTH numbers, and the sector one does more
work. Sizing either is what this module is for; neither is a constant to be
picked by feel, and `cap_for_tolerance` inverts the arithmetic so the cap
follows from a stated loss tolerance instead.

RECOVERY RATES ARE NOT OPTIMISTIC HERE. DHFL resolved through Piramal at
roughly 44-46%, paid partly in ten-year NCDs rather than cash, so the economic
recovery was lower than the headline. IL&FS reached about 82% of targets across
the group but in staggered phases by entity, with deep haircuts on unsecured
commercial paper. The default assumption below is 45%, and a plan that only
survives at 60% is a plan that has not been stress-tested.

WHAT THIS MODULE CANNOT DO. It computes what a given exposure costs. It cannot
tell you what a fund's exposures ARE -- that lives in the monthly portfolio
disclosure, which this project does not yet ingest. Until it does, these
functions size a policy; they cannot audit compliance with one.
"""
from __future__ import annotations

import math

# SEBI limits, as at the last review. Sources: the 2016 tightening (single
# issuer 15% -> 10%, sector 30% -> 25%, HFC add-on 10% -> 5%), the later
# reduction of the sector cap to 20% with the HFC add-on at 15% plus 5% for
# retail-housing securitised debt, and the November 2022 credit-rating-based
# single-issuer limits for actively managed schemes.
SEBI_SINGLE_ISSUER = 0.10           # extendable to 0.12 with trustee approval
SEBI_SINGLE_ISSUER_EXTENDED = 0.12
SEBI_ISSUER_BY_RATING = {"AAA": 0.10, "AA": 0.08, "A": 0.06}
SEBI_ISSUER_RATING_EXTENSION = 0.02
SEBI_SECTOR = 0.20
SEBI_HFC_ADDITIONAL = 0.15
SEBI_SECURITISED_ADDITIONAL = 0.05
SEBI_GROUP = 0.25

# DHFL resolved at roughly 44-46%, part of it in ten-year paper rather than
# cash. 45% is the honest planning number; 0.60 is the optimistic case and
# should never be the default.
DEFAULT_RECOVERY = 0.45
STRESS_RECOVERY = 0.00              # the assumption that costs nothing to make


def single_issuer_loss(exposure, recovery=DEFAULT_RECOVERY):
    """NAV loss from one issuer defaulting. Pure arithmetic, no model."""
    return exposure * (1.0 - recovery)


def implied_exposure(nav_drop, writedown=0.75):
    """Back out exposure from an observed single-day fall.

    `writedown` is the fraction of face value written off THAT DAY, which is
    rarely 100%: funds typically mark down in stages as ratings fall. At 75%
    an 8.77% NAV fall implies about 11.7% of the portfolio in the issuer.
    Assuming 100% would understate the exposure, so this errs toward the
    larger, more uncomfortable number.
    """
    if writedown <= 0:
        raise ValueError("writedown must be positive")
    return abs(nav_drop) / writedown


def sector_loss(sector_exposure, fraction_defaulting, recovery=DEFAULT_RECOVERY):
    """Loss when part of one sector goes at once.

    THE CASE THE ISSUER CAP DOES NOT COVER. Correlated defaults inside a sector
    are the historical pattern, not the exception: IL&FS and DHFL were both
    NBFC/HFC, nine months apart.
    """
    return sector_exposure * fraction_defaulting * (1.0 - recovery)


def cap_for_tolerance(tolerance, recovery=DEFAULT_RECOVERY):
    """The largest single-issuer exposure that keeps the loss under tolerance.

    THE RIGHT WAY ROUND. A cap chosen by feel -- three percent, five percent --
    is a number with no argument behind it. State how much NAV you are willing
    to lose to one default and the cap follows.
    """
    if recovery >= 1.0:
        return 1.0
    return tolerance / (1.0 - recovery)


def min_issuers(cap):
    """How many issuers a portfolio needs to respect the cap.

    A FEASIBILITY CHECK, NOT A TARGET. At 3% a fund needs at least 34 distinct
    issuers, which a large liquid fund can do and a small credit fund cannot.
    A cap the fund cannot meet is not a policy; it is a reason to exclude the
    fund.
    """
    if cap <= 0:
        raise ValueError("cap must be positive")
    return int(math.ceil(1.0 / cap))


def sebi_issuer_cap(rating="AAA", extended=False):
    """The regulatory ceiling for a rating band."""
    base = SEBI_ISSUER_BY_RATING.get(str(rating).upper(), SEBI_SINGLE_ISSUER)
    return base + (SEBI_ISSUER_RATING_EXTENSION if extended else 0.0)


def sebi_max_financial_sector():
    """The most a scheme may hold in financial-sector paper.

    20% sector plus 15% for housing finance plus 5% securitised: FORTY PERCENT
    of NAV may sit in one correlated complex while every single-issuer rule is
    observed. This is the number that made 2018 and 2019 possible.
    """
    return SEBI_SECTOR + SEBI_HFC_ADDITIONAL + SEBI_SECURITISED_ADDITIONAL


def policy(tolerance_single, tolerance_sector, recovery=DEFAULT_RECOVERY,
           sector_fraction_defaulting=1.0):
    """A concentration policy derived from two loss tolerances.

    Returns the issuer cap, the sector cap, the issuer count each implies, and
    whether the caps are tighter than SEBI's -- which they generally must be,
    since the historical losses happened inside the regulatory limits.
    """
    icap = cap_for_tolerance(tolerance_single, recovery)
    scap = (tolerance_sector / (sector_fraction_defaulting * (1.0 - recovery))
            if recovery < 1.0 else 1.0)
    return dict(
        issuer_cap=icap, sector_cap=scap,
        min_issuers=min_issuers(icap) if icap > 0 else None,
        issuer_tighter_than_sebi=icap < SEBI_SINGLE_ISSUER,
        sector_tighter_than_sebi=scap < sebi_max_financial_sector(),
        worst_single=single_issuer_loss(icap, recovery),
        worst_sector=sector_loss(scap, sector_fraction_defaulting, recovery),
        recovery=recovery)


def sweep(caps=(0.02, 0.03, 0.05, 0.075, 0.10, 0.12),
          recoveries=(0.00, 0.25, 0.45, 0.60)):
    """Loss table across caps and recovery assumptions, for choosing a number."""
    return {c: {r: single_issuer_loss(c, r) for r in recoveries} for c in caps}


def format_sweep(table):
    recs = sorted(next(iter(table.values())))
    lines = [f"{'cap':>7}" + "".join(f"{f'rec {r:.0%}':>10}" for r in recs)]
    for cap in sorted(table):
        lines.append(f"{cap:>6.1%}" + "".join(
            f"{table[cap][r]:>9.2%}" for r in recs))
    return "\n".join(lines)
