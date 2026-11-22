# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""
The plan must deliver the stated requirement, every year, on every scenario.

This is the constraint the whole system exists to satisfy. Tax is the thing
being minimised subject to it -- so a tax total, however good, means nothing
unless this holds first.

`tests/test_net_income_plan.py::test_project_plan_hits_every_target` asserts
the same thing on one synthetic state. These run it against the five golden
scenarios: real chunk structures, a windfall into the HUF at FY5, thirty years
of compounding, and the tax ladders the plan actually uses.

Why it is worth having twice
----------------------------
HANDOVER §4 records two bugs found during the design work that the tax totals
hid completely -- a split that over-delivered 44.64 against a 42.5 requirement,
and a transition whose realised gain was never fed back into the withdrawal
solve. Both looked like excellent tax results. Both failed this check on the
first year they touched.

Every phase of the §4 build changes how withdrawals are chosen between purses,
folios and transition instalments. This is the invariant those changes must not
break, and the one that will catch them when they do.
"""
from __future__ import annotations

import pytest

TOL = 1e-2          # Rs lakh; matches net_income_plan.PLAN_TOL


def _plan(state):
    from swp_planner.core.net_income_plan import project_plan
    return project_plan(state)


def test_every_year_receives_its_requirement(scenario):
    """Cash in hand equals the target, in every year of the plan."""
    label, state = scenario
    _pm, p_yearly, _hm, _hy, sol = _plan(state)

    missing = [y.year for y in p_yearly if y.year not in sol.targets]
    assert not missing, f"{label}: no target recorded for FY{missing}"

    bad = [
        (y.year, y.net_cash_total, sol.targets[y.year])
        for y in p_yearly
        if abs(y.net_cash_total - sol.targets[y.year]) > TOL
    ]
    assert not bad, (
        f"{label}: cash in hand does not match the requirement in "
        f"{len(bad)} year(s).\n" + "\n".join(
            f"    FY{fy}: delivered {got:.4f} vs required {want:.4f} "
            f"({got - want:+.4f})" for fy, got, want in bad[:8]
        ) + ("\n    ..." if len(bad) > 8 else "")
    )


def test_the_solver_converged_without_shortfalls(scenario):
    """A shortfall is a year the corpus could not fund.

    None of the golden scenarios should have one: they are all funded plans.
    If this fires, either the corpus ran dry or the solver gave up -- both are
    findings, not test problems.
    """
    label, state = scenario
    *_rest, sol = _plan(state)

    assert sol.converged, (
        f"{label}: solver did not converge after {sol.iterations} iteration(s)."
    )
    assert not sol.shortfalls, (
        f"{label}: {len(sol.shortfalls)} year(s) short, "
        f"{sol.total_shortfall:.4f}L in total, worst FY{sol.worst_year()}. "
        f"{dict(list(sol.shortfalls.items())[:6])}"
    )


def test_total_cash_is_the_sum_of_its_purses(scenario):
    """net_cash_total must be net_cash_personal + net_cash_huf.

    Internal consistency, cheap to check, and the first thing to break if a
    future split policy credits cash to one purse without debiting the other.
    """
    label, state = scenario
    _pm, p_yearly, *_rest = _plan(state)

    bad = []
    for y in p_yearly:
        parts = y.net_cash_personal + y.net_cash_huf
        if abs(y.net_cash_total - parts) > TOL:
            bad.append((y.year, y.net_cash_total, parts))
    assert not bad, (
        f"{label}: total cash != personal + HUF in {len(bad)} year(s).\n"
        + "\n".join(f"    FY{fy}: total {t:.4f} vs parts {p:.4f} "
                     f"({t - p:+.4f})" for fy, t, p in bad[:8])
    )


@pytest.mark.parametrize("field", ["net_cash_total", "tax_personal"])
def test_reported_figures_are_finite(scenario, field):
    """No NaN or inf reaches a snapshot.

    Cheap, and it fails loudly rather than writing a snapshot full of `nan`
    that then compares equal to nothing and gets regenerated silently.
    """
    import math
    label, state = scenario
    _pm, p_yearly, *_rest = _plan(state)
    bad = [y.year for y in p_yearly if not math.isfinite(getattr(y, field))]
    assert not bad, f"{label}: {field} is not finite in FY{bad}"
