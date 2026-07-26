# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
pro_rated_utils.py
==================
Financial-year month arithmetic.  THE single definition of "how many months
of financial year N does this plan actually span", and of the pro-rating that
follows from it.

WHY THIS MODULE EXISTS
----------------------
The Indian financial year runs 1 April - 31 March.  A plan that starts in any
other month has a SHORT FIRST YEAR: a July start gives FY1 nine months, a
March start gives it one.  Every subsequent FY is a full twelve.

Annual figures must therefore be scaled in FY1 and only in FY1.  Before this
module, some were and some were not:

    pro-rated correctly   annual_requirements   (req_annual / 12, applied
                                                 once per elapsed month)
                          HUF withdrawals       (same shape)
    NOT pro-rated         OtherIncome.taxable() / .non_taxable()
                          the FD tax benchmark

so a March-start plan booked a full year of other income and a full year of
FD interest for one elapsed month.

These are pure functions with no dependency on models, engine or config --
deliberately, so they can be tested without constructing an AppState and so
the import graph stays acyclic.  The horizon is NOT this module's business:
it answers questions about the calendar only.

UNITS
-----
`fy` is a 1-based plan year, matching AppState's chunk keys.  fy <= 0 is
treated as fy 1 rather than raising, because the engine's
`_fy_year_for_month()` already clamps with max(1, ...) and a second, stricter
opinion here would only turn a clamped value into a crash.
"""
from __future__ import annotations

from datetime import date

__all__ = [
    "FY_START_MONTH",
    "MONTHS_PER_YEAR",
    "financial_year_of",
    "fy_fraction",
    "months_in_fy",
    "plan_months",
    "prorate_annual",
]

#: The Indian financial year begins in April.
FY_START_MONTH = 4
MONTHS_PER_YEAR = 12


def financial_year_of(d: date) -> int:
    """Label of the financial year containing *d*, by its starting year.

    1 April 2026 and 31 March 2027 both belong to FY2026-27, labelled 2026.
    """
    return d.year if d.month >= FY_START_MONTH else d.year - 1


def months_in_fy(investment_date: date, fy: int) -> int:
    """Months of financial year *fy* that this plan actually spans.

    FY1 runs from the investment month to the following 31 March, so it is
    short whenever the plan does not start in April.  Every later FY is full.

        April start -> FY1 = 12      July start  -> FY1 = 9
        March start -> FY1 = 1       January     -> FY1 = 3
    """
    if fy > 1:
        return MONTHS_PER_YEAR
    return MONTHS_PER_YEAR - ((investment_date.month - FY_START_MONTH)
                              % MONTHS_PER_YEAR)


def fy_fraction(investment_date: date, fy: int) -> float:
    """Fraction of a full year that *fy* spans: months_in_fy / 12."""
    return months_in_fy(investment_date, fy) / MONTHS_PER_YEAR


def prorate_annual(amount: float, investment_date: date, fy: int) -> float:
    """Scale an ANNUAL figure to the months *fy* actually spans.

    Use this wherever a per-year rupee amount is booked against a financial
    year. Do not inline `amount * m / 12` at the call site: that arithmetic
    was written out separately for requirements and HUF withdrawals and
    omitted entirely for other income and the FD benchmark, which is the
    defect this module exists to retire.
    """
    return float(amount) * fy_fraction(investment_date, fy)


def plan_months(investment_date: date, horizon: int) -> int:
    """Months from the investment date to 31 March of the final FY.

    The plan ends 31 March of (investment FY + horizon), so a 30-year plan
    starting in March 2026 ends 31 March 2055 and spans 349 months, not 360.
    That is not a truncation: it is what "thirty financial years out" means
    when the first of them is one month long. A plan wanting thirty FULL
    years from a March start is a horizon of 31.
    """
    horizon = max(1, int(horizon))
    return (months_in_fy(investment_date, 1)
            + (horizon - 1) * MONTHS_PER_YEAR)
