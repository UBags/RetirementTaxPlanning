# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""Delivery outranks tax, and over-delivery is its own failure.

The objective is lexicographic and the order is not negotiable:

    1. shortfall      - the user must receive the requirement
    2. tax            - minimise it, subject to (1)
    3. terminal corpus- maximise it, subject to (1) and (2)

Ranking on tax alone is actively dangerous, because **a corpus that has been
run to zero pays no tax**. Measured on the standalone model: scaling the
requirement by 1.6 produced a LOWER lifetime tax (87.38) than scaling it by
1.4 (142.44), purely because the corpus died in FY22 and there was nothing
left to tax. Any optimiser that reads tax as its objective will walk straight
into that.

Running both corpora to zero before the horizon is an acceptable OUTCOME when
the plan is genuinely unaffordable. It is never an acceptable STRATEGY, and it
must be reported, never absorbed.

`test_requirement_delivery.py` asserts the requirement is met. These assert the
two asymmetries that file cannot see: that over-delivery is recorded at all,
and that frozen years are not quietly exempt from the check.
"""
from __future__ import annotations

TOL = 1e-2


def _plan(state):
    from net_income_plan import project_plan
    return project_plan(state)


def test_no_year_over_delivers(scenario):
    """Over-delivery is a failure, not a happy accident.

    B1d-ii computed `converged = not shortfalls`, and `shortfalls` counted
    only under-delivery -- so it reported success while net_cash_total drifted
    above target in six to thirteen years, and the tax total looked excellent
    precisely BECAUSE it was over-delivering.
    """
    label, state = scenario
    *_rest, sol = _plan(state)
    assert not sol.overdeliveries, (
        f"{label}: {len(sol.overdeliveries)} year(s) over-delivered, "
        f"{sol.total_overdelivery:.4f}L in total. "
        f"{dict(list(sol.overdeliveries.items())[:6])}"
    )


def test_shortfall_and_overdelivery_are_disjoint(scenario):
    """A year cannot be both short and over. If it is, the records disagree."""
    label, state = scenario
    *_rest, sol = _plan(state)
    both = set(sol.shortfalls) & set(sol.overdeliveries)
    assert not both, f"{label}: FY{sorted(both)} recorded as short AND over"
    assert all(v > 0 for v in sol.shortfalls.values()), (
        f"{label}: a non-positive shortfall is a sign error, not a shortfall"
    )


def test_frozen_years_are_not_exempt_from_delivery(scenario):
    """`converged` skips frozen years -- so check them here.

    solve_schedule computes convergence over `years if not frozen[fy]`. A
    frozen year is one that stopped responding to gross adjustment; freezing
    it is a solver decision, and it must not silently become a licence to
    miss the target in that year.
    """
    label, state = scenario
    _pm, p_yearly, _hm, _hy, sol = _plan(state)
    bad = [
        (y.year, y.net_cash_total, sol.targets[y.year])
        for y in p_yearly
        if y.year in sol.targets
        and abs(y.net_cash_total - sol.targets[y.year]) > TOL
    ]
    assert not bad, (
        f"{label}: {len(bad)} year(s) miss the target once frozen years are "
        "included.\n" + "\n".join(
            f"    FY{fy}: delivered {got:.4f} vs required {want:.4f} "
            f"({got - want:+.4f})" for fy, got, want in bad[:8])
    )
