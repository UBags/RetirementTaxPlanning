# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""`cash_within_gain_budget` -- cash raisable inside a gain budget.

Pairs with `free_gain_capacity`: that gives the budget, this converts it into
rupees. Together they answer "how much can this purse hand over without paying
tax". No caller yet; landed dead with tests ahead of the ration and water-fill
work, the sequence 4b-i and free_gain_capacity both used.

The load-bearing test is `test_matches_what_redeem_actually_realises`. A query
that predicts a mutation must be pinned to that mutation, or the two drift and
every caller believes the query.

Why the function exists at all, rather than `budget / front_gain_fraction`:
that formula is exact only while the draw stays in one lot at one basis. The
moment it spills into an older lot or a dearer folio the marginal gain per
rupee changes and the formula overstates. In the standalone model this made
the HUF draw `band / f_cheapest` -- 76, 92, 107, 122 against a band of 5 to 9
-- realising far more than its nil band and paying tax that more folios were
supposed to remove. Lifetime tax RISING with folio count is impossible, since
a folio can always be ignored; that non-monotonicity is what exposed it
(HANDOVER §9.16). `test_the_naive_formula_overstates` pins the difference.

Fixtures are shaped like the plan -- one large old lot plus a small fresh
cohort -- not like a random walk. 3000 randomised steps once passed while
never opening a second folio (HANDOVER §9.14).
"""
from __future__ import annotations

import pytest

from swp_planner.core.engine import FIFOBucket, FolioSet, cash_within_gain_budget

NAV = 2.0


def _plan_shaped():
    """100 units of original basis-1.0 stock, plus a fresh basis-1.8 cohort.

    At NAV 2.0 the old folio is 50% gain and the fresh one 10%, so a cash draw
    takes the fresh folio first and then spills into the old one -- the exact
    situation the naive formula gets wrong.
    """
    fs = FolioSet(threshold=0.0, max_cohorts=5)
    fs.invest(100.0, 1.0, 0)                    # 100 units @ 1.0
    fs.open_folio("F2")
    fs.invest(18.0, 1.8, 12, folio="F2")        # 10 units @ 1.8
    return fs


def test_single_bucket_single_basis():
    bk = FIFOBucket()
    bk.invest(100.0, 1.0, 0)                    # 100 units, 50% gain at NAV 2
    # budget 10 of gain, at 0.5 gain per rupee of NAV value -> 20 of cash
    assert cash_within_gain_budget(bk, NAV, 10.0) == pytest.approx(20.0)


def test_lots_carrying_no_gain_are_free():
    """Basis == NAV realises nothing, so the budget does not constrain it."""
    bk = FIFOBucket()
    bk.invest(50.0, NAV, 0)                     # 25 units @ 2.0, zero gain
    assert cash_within_gain_budget(bk, NAV, 0.0) == pytest.approx(50.0)


def test_budget_larger_than_the_holding_returns_the_whole_value():
    bk = FIFOBucket()
    bk.invest(100.0, 1.0, 0)
    assert cash_within_gain_budget(bk, NAV, 1e6) == pytest.approx(200.0)


def test_the_naive_formula_overstates():
    """`budget / front_frac` against the truth, on a plan-shaped holding."""
    fs = _plan_shaped()
    budget = 5.0
    naive = budget / 0.10                       # front folio is 10% gain -> 50
    actual = cash_within_gain_budget(fs, NAV, budget)
    # fresh folio: 20 of value, 2 of gain. Remaining 3 of budget from the old
    # folio at 50% -> 6 of value. 26, not 50.
    assert actual == pytest.approx(26.0)
    assert naive > actual * 1.9, (
        "the naive formula should be visibly wrong here, or this fixture is "
        "not exercising the spill it exists to test"
    )


def test_matches_what_redeem_actually_realises():
    """The pin: redeeming exactly this much realises exactly the budget.

    Without this, the query and the mutation are free to drift apart.
    """
    budget = 5.0
    cash = cash_within_gain_budget(_plan_shaped(), NAV, budget)
    fs = _plan_shaped()
    _principal, gain = fs.redeem(cash, NAV)
    assert gain == pytest.approx(budget, abs=1e-9), (
        f"predicted {cash:.6f} of cash for a {budget} budget, but redeeming "
        f"it realised {gain:.6f}"
    )


def test_one_rupee_more_breaches_the_budget():
    """Tightness. Without it, any understatement would pass the pin above."""
    budget = 5.0
    cash = cash_within_gain_budget(_plan_shaped(), NAV, budget)
    fs = _plan_shaped()
    _p, gain = fs.redeem(cash + 1.0, NAV)
    assert gain > budget + 1e-9, (
        f"{cash:.6f} is not the limit -- a further rupee still fitted inside "
        f"the {budget} budget, so the answer is understated"
    )


@pytest.mark.parametrize("budget", [0.0, 1.0, 2.0, 5.0, 20.0, 100.0, 1e6])
def test_never_exceeds_the_holding_and_is_monotone(budget):
    fs = _plan_shaped()
    total = fs.current_value(NAV)
    cash = cash_within_gain_budget(fs, NAV, budget)
    assert 0.0 <= cash <= total + 1e-9
    bigger = cash_within_gain_budget(_plan_shaped(), NAV, budget + 1.0)
    assert bigger >= cash - 1e-9, "more budget cannot buy less cash"


def test_a_missing_bucket_raises_nothing_and_offers_nothing():
    assert cash_within_gain_budget(None, NAV, 10.0) == 0.0
