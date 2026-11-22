# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""fund_filters -- choosing the investible universe, and watching it.

SIX MODULES, AND A MEASURED WARNING ABOUT WHAT TWO OF THEM DO NOT DO.

    instruments    what kind of risk an instrument carries, from its ISIN.
                   Two-thirds of the traded debt universe classifies with no
                   rating file at all.
    ratings        read a credit rating string, and know what it does not say.
    caps           the exposure cap for one instrument.
    concentration  how much one default can cost, and what cap prevents it.
                   THE ONLY MODULE HERE THAT WOULD ACTUALLY HAVE PREVENTED THE
                   HISTORICAL LOSSES.
    amc_registry   who sponsors each fund, for CONTINUITY, GOVERNANCE and
                   DISCLOSURE risk.
    deviation      sustained deviation from self, peers and family, for
                   REDEMPTION PRIORITY.

NEITHER OF THE LAST TWO SCREENS CREDIT RISK, AND THIS WAS MEASURED RATHER
THAN ASSUMED. The
five funds in nav_sample.csv that took permanent writedowns are run by Tata,
UTI and LIC Mutual Fund -- three of the most institutionally backed houses in
India -- so a sponsor filter would have excluded none of them. And over the
eighteen months before those events their returns against category medians ran
-0.29pp to +0.32pp against a control group averaging -0.19pp with a standard
deviation of 0.45pp, so a deviation monitor would have said nothing.

What DOES work is a concentration limit, because the loss from a default is
arithmetic -- exposure times one minus recovery -- and exposure is chosen in
advance rather than discovered afterwards. concentration.py sizes that policy.
Applying it still needs the monthly portfolio disclosure, which this project
does not yet ingest: the module can size a limit, not audit compliance with
one. That remains the outstanding data-acquisition problem.
"""
from . import (  # noqa: F401
    amc_registry,
    caps,
    concentration,
    deviation,
    instruments,
    ratings,
)

__all__ = ["amc_registry", "caps", "concentration", "deviation", "instruments", "ratings"]
