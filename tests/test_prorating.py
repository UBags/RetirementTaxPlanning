# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
Financial-year month arithmetic and the pro-rating built on it.

The defect these pin: a plan starting in any month other than April has a
SHORT FY1, and before this module some annual figures were scaled to it and
some were not. Requirements and HUF withdrawals were (annual/12 applied per
elapsed month); other income and the FD tax benchmark were not, so a
March-start plan booked twelve months of both for one elapsed month.
"""
from __future__ import annotations

from datetime import date

import pytest

from swp_planner.core.models import AppState, OtherIncome
from swp_planner.core.pro_rated_utils import (
    financial_year_of,
    fy_fraction,
    months_in_fy,
    plan_months,
    prorate_annual,
)


@pytest.mark.parametrize("month,expected", [
    (4, 12),   # April  — a full first year, the only month that gives one
    (5, 11),
    (6, 10),
    (7, 9),    # July   — three months of the FY already gone
    (10, 6),
    (12, 4),
    (1, 3),    # January — Jan, Feb, Mar remain
    (2, 2),
    (3, 1),    # March  — one month, the live project's start
])
def test_months_in_fy1_counts_the_months_that_remain(month, expected):
    d = date(2026 if month >= 3 else 2027, month, 1)
    assert months_in_fy(d, 1) == expected


@pytest.mark.parametrize("month", [1, 3, 4, 7, 12])
def test_every_fy_after_the_first_is_full(month):
    d = date(2026, month, 1)
    assert all(months_in_fy(d, fy) == 12 for fy in range(2, 31))


def test_day_of_month_does_not_change_the_count():
    """The engine works in whole months; the day is cosmetic."""
    assert months_in_fy(date(2026, 7, 1), 1) == months_in_fy(date(2026, 7, 28), 1)


def test_financial_year_label_straddles_the_calendar_year():
    assert financial_year_of(date(2026, 4, 1)) == 2026
    assert financial_year_of(date(2027, 3, 31)) == 2026
    assert financial_year_of(date(2026, 3, 31)) == 2025


@pytest.mark.parametrize("month,expected", [
    (4, 360),   # April start: thirty full years
    (7, 357),
    (3, 349),   # March start: FY1 is one month, so the plan is 349 months
])
def test_plan_months_runs_to_31_march_of_the_final_fy(month, expected):
    d = date(2026 if month >= 3 else 2027, month, 1)
    assert plan_months(d, 30) == expected


def test_plan_months_is_not_a_truncation():
    """349 is what 'thirty financial years' means from a March start.

    A plan wanting thirty FULL years from March is a horizon of 31 -- that is
    a choice made in the dialog, not something the engine should assume.
    """
    march = date(2026, 3, 1)
    assert plan_months(march, 30) == 349
    assert plan_months(march, 31) == 361


def test_prorate_scales_fy1_and_leaves_the_rest_alone():
    july = date(2026, 7, 1)
    assert prorate_annual(120.0, july, 1) == pytest.approx(90.0)   # 9/12
    assert prorate_annual(120.0, july, 2) == pytest.approx(120.0)
    assert fy_fraction(july, 1) == pytest.approx(0.75)


# ── OtherIncome ──────────────────────────────────────────────────────────────

def test_other_income_pro_rates_the_result_not_the_growth_base():
    """A short FY1 must not shrink FY2 onward.

    Pro-rating the base instead of the result would scale all thirty years by
    9/12 -- the plan would lose a quarter of its other income for life.
    """
    oi = OtherIncome(rental=120.0, rental_growth=10.0)
    assert oi.taxable(1, months=9) == pytest.approx(90.0)
    # FY2 still gets a full year, grown once from the ENTERED figure.
    assert oi.taxable(2, months=12) == pytest.approx(132.0)


def test_other_income_defaults_to_a_full_year():
    """Callers that do not care about the calendar are unaffected."""
    oi = OtherIncome(taxable_interest=6.0, tax_free_interest=4.0)
    assert oi.taxable(1) == pytest.approx(6.0)
    assert oi.non_taxable(1) == pytest.approx(4.0)
    assert oi.total(1) == pytest.approx(10.0)


def test_other_income_zero_months_is_zero_not_a_full_year():
    oi = OtherIncome(rental=120.0)
    assert oi.taxable(1, months=0) == pytest.approx(0.0)


# ── the default investment date ──────────────────────────────────────────────

def test_default_investment_date_is_today():
    """Was date(today.year, 3, 1) -- March hardcoded, so a plan created in
    July silently began in the PREVIOUS financial year."""
    assert AppState().investment_date == date.today()


def test_the_default_start_is_always_inside_the_current_financial_year():
    s = AppState()
    assert financial_year_of(s.investment_date) == financial_year_of(date.today())


def test_a_file_with_no_investment_date_loads_as_today():
    assert AppState.from_dict({"fd_rate": 0.07}).investment_date == date.today()
