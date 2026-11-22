# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
Statutory deductions on non-portfolio income, and the cash/assessable split.

Two figures that were one variable:

    taxable()      CASH RECEIVED from streams which happen to be taxable --
                   the whole rent, the whole pension, what reaches the bank
    assessable()   what a tax slab is applied to, after s.16(ia) on salary
                   and pension and s.24(a) on rent

They were the same number until deductions existed, and the engine used one
variable for both, so tax was computed on gross income. Six sites in the
engine applied a slab to cash; two added cash to net cash. Both are correct
now, and they read different methods.

This matters more than it looks: Phase B solves for net cash in hand as a
fixed point, and the loop converges on the tax function. Building the solver
on a tax base that overstated income would have converged on a wrong answer.
"""
from __future__ import annotations

import pytest

from swp_planner.config.configuration import config
from swp_planner.core.models import OtherIncome

SD = float(config.standard_deduction_salary_pension)
RENT_DED = float(config.rental_maintenance_deduction)


def test_cash_received_is_gross():
    """You receive the whole rent and the whole pension."""
    oi = OtherIncome(pension=6.0, rental=12.0, taxable_interest=3.0)
    assert oi.taxable(1) == pytest.approx(21.0)


def test_salary_and_pension_share_one_allowance():
    """s.16(ia) is an allowance against the salary head, not per stream.

    A pension is taxed under that head, which is why it shares the allowance
    rather than getting one of its own. Two allowances would understate tax.
    """
    both = OtherIncome(salary=4.0, pension=6.0)
    assert both.assessable(1) == pytest.approx(10.0 - SD)
    only_one = OtherIncome(salary=10.0)
    assert both.assessable(1) == pytest.approx(only_one.assessable(1))


def test_the_allowance_cannot_create_a_loss():
    """Capped at the income itself."""
    tiny = OtherIncome(pension=SD / 3)
    assert tiny.assessable(1) == pytest.approx(0.0)
    assert tiny.assessable(1) >= 0.0


def test_rent_is_assessed_after_the_maintenance_deduction():
    oi = OtherIncome(rental=100.0)
    assert oi.assessable(1) == pytest.approx(100.0 * (1 - RENT_DED))
    assert oi.taxable(1) == pytest.approx(100.0), "cash received is the whole rent"


def test_interest_gets_no_deduction():
    oi = OtherIncome(taxable_interest=10.0, other_taxable=5.0)
    assert oi.assessable(1) == pytest.approx(15.0)


def test_an_huf_has_no_salary_head():
    """An HUF cannot draw a salary or a pension, so no s.16(ia).

    It can own property, so s.24(a) applies to both.
    """
    oi = OtherIncome(pension=6.0, rental=10.0)
    ind = oi.assessable(1, entity="individual")
    huf = oi.assessable(1, entity="huf")
    assert huf - ind == pytest.approx(SD)
    assert huf == pytest.approx(6.0 + 10.0 * (1 - RENT_DED))


def test_deductions_apply_after_growth_not_before():
    """A grown rent is deducted at 30% of the GROWN figure."""
    oi = OtherIncome(rental=100.0, rental_growth=10.0)
    assert oi.assessable(2) == pytest.approx(110.0 * (1 - RENT_DED))


def test_a_short_first_year_scales_income_before_the_allowance():
    """FY1 of a March-start plan is one month.

    The allowance is a flat annual figure capped at the income, so one month
    of a modest pension is fully absorbed and nothing is assessed -- which is
    the same answer the cap gives at any scale.
    """
    oi = OtherIncome(pension=6.0, rental=12.0)
    a = oi.assessable(1, months=1)
    assert a == pytest.approx(max(0.0, 0.5 - SD) + 1.0 * (1 - RENT_DED))


def test_assessable_never_exceeds_cash_received():
    """A deduction reduces what is assessed; it cannot increase it."""
    for kw in ({"pension": 6.0}, {"rental": 12.0}, {"salary": 3.0, "rental": 4.0},
               {"taxable_interest": 9.0}, {}):
        oi = OtherIncome(**kw)
        assert oi.assessable(1) <= oi.taxable(1) + 1e-12, kw


def test_zero_income_is_zero_either_way():
    oi = OtherIncome()
    assert oi.taxable(1) == oi.assessable(1) == pytest.approx(0.0)
