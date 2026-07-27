# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
The plan-level solve: annual_requirements as a NET target.

Before Phase B a requirement meant "withdraw this much" and other income
stacked on top, so a plan asking for 24.55 delivered 38.79. It now means
"end up with this much in hand" -- cash after tax and after the transfer
into the HUF, which is what the engine reports as net_cash_total.

The engine still consumes a GROSS schedule; it always did. What changed is
that the schedule is now solved rather than taken literally from the saved
plan. That separation is deliberate: Engine(state) with no schedule behaves
exactly as it did, which is what the conservation and metric tests rely on.
"""
from __future__ import annotations

import copy
from datetime import date

import pytest

from engine import Engine
from models import AppState, ReturnChunk, SplitChunk, TaxChunk, TaxSlab
from net_income_plan import (
    PlanSolution,
    project_plan,
    prorated_targets,
    solve_schedule,
)


def _linear_project(slope=0.8, offset=5.0, dead=()):
    """A stand-in for the engine: net responds linearly to gross.

    Years listed in *dead* ignore their requirement entirely, which is how an
    exhausted corpus behaves.
    """
    def project(gross):
        return {fy: (offset if fy in dead else offset + slope * g)
                for fy, g in gross.items()}
    return project


# ── the iteration ────────────────────────────────────────────────────────────

def test_it_converges_on_a_linear_response():
    targets = {fy: 20.0 for fy in range(1, 11)}
    sol = solve_schedule(targets, _linear_project())
    assert sol.converged and not sol.shortfalls
    for fy in targets:
        assert sol.gross[fy] == pytest.approx((20.0 - 5.0) / 0.8, abs=1e-3)


def test_a_year_that_cannot_respond_is_frozen_not_chased():
    """An exhausted corpus does not move when the requirement rises.

    A solver that keeps applying the correction diverges; this one stops and
    reports.
    """
    targets = {fy: 20.0 for fy in range(1, 6)}
    sol = solve_schedule(targets, _linear_project(dead=(3,)))
    assert 3 in sol.shortfalls
    assert sol.shortfalls[3] == pytest.approx(15.0, abs=1e-3)
    assert sol.gross[3] < 1e3, "the frozen year's requirement ran away"
    for fy in (1, 2, 4, 5):
        assert fy not in sol.shortfalls


def test_the_live_years_still_converge_around_a_dead_one():
    targets = {fy: 20.0 for fy in range(1, 6)}
    sol = solve_schedule(targets, _linear_project(dead=(3,)))
    assert sol.converged
    for fy in (1, 2, 4, 5):
        assert sol.net[fy] == pytest.approx(20.0, abs=1e-3)


def test_it_does_not_run_forever():
    targets = {fy: 20.0 for fy in range(1, 6)}
    sol = solve_schedule(targets, _linear_project(dead=(1, 2, 3, 4, 5)),
                         max_iter=8)
    assert sol.iterations <= 8


# ── pro-rating ───────────────────────────────────────────────────────────────

def test_a_short_first_year_gets_a_short_target():
    """One month of living costs, not twelve.

    Feeding a full year's target to a one-month FY1 asks for twelve months of
    spending out of one month of withdrawals.
    """
    march = date(2026, 3, 1)
    t = prorated_targets({1: 12.0, 2: 12.0}, march, [1, 2])
    assert t[1] == pytest.approx(1.0)
    assert t[2] == pytest.approx(12.0)


def test_an_april_start_needs_no_pro_rating():
    april = date(2026, 4, 1)
    t = prorated_targets({1: 12.0}, april, [1])
    assert t[1] == pytest.approx(12.0)


# ── the engine contract ──────────────────────────────────────────────────────

def _fund(name="Fund A", fund_type="debt", allocation=250.0, cagr=7.0):
    from models import FundEntry
    f = FundEntry(name=name, fund_type=fund_type, allocation=allocation,
                  std_dev=0.02, sharpe=2.0, sortino=2.5, calmar=1.0,
                  alpha=0.5, treynor=0.8, max_dd=0.01, beta=0.3,
                  combined_ratio=1.5)
    f.cagr_1 = f.cagr_3 = f.cagr_5 = f.cagr_10 = cagr
    return f


def _plan() -> AppState:
    """A minimal plan with a corpus large enough to fund its target."""
    slabs = [TaxSlab(0, 4, 0.0), TaxSlab(4, 8, 0.05), TaxSlab(8, 1e9, 0.10)]
    s = AppState(horizon_years=12, investment_date=date(2026, 4, 1))
    s.funds = [_fund()]
    s.annual_requirements = {fy: 10.0 for fy in range(1, 13)}
    s.return_chunks = [ReturnChunk(1, 12, 0.07)]
    s.split_chunks = [SplitChunk(1, 12, 1.0)]
    s.individual_debt_chunks = [TaxChunk(1, 12, 12.0, slabs)]
    s.huf_debt_chunks = [TaxChunk(1, 12, 0.0, slabs)]
    return s


def test_engine_without_a_schedule_is_unchanged():
    """Engine(state) must keep reading the requirement as gross.

    The conservation and return-metric suites depend on it, and so does any
    caller written before Phase B.
    """
    s = _plan()
    _, a, _, _ = Engine(copy.deepcopy(s)).run()
    _, b, _, _ = Engine(copy.deepcopy(s),
                        gross_schedule=dict(s.annual_requirements)).run()
    assert [y.net_cash_total for y in a] == pytest.approx(
        [y.net_cash_total for y in b])


def test_a_schedule_overrides_the_requirement():
    s = _plan()
    half = {fy: 5.0 for fy in range(1, 13)}
    _, full, _, _ = Engine(copy.deepcopy(s)).run()
    _, part, _, _ = Engine(copy.deepcopy(s), gross_schedule=half).run()
    assert part[1].swp_cash_personal < full[1].swp_cash_personal


# ── end to end ───────────────────────────────────────────────────────────────

def test_project_plan_hits_every_target():
    s = _plan()
    _, yearly, _, _, sol = project_plan(s)
    assert isinstance(sol, PlanSolution)
    assert sol.converged and not sol.shortfalls
    for y in yearly:
        assert y.net_cash_total == pytest.approx(sol.targets[y.year], abs=1e-3)


def test_the_gross_schedule_differs_from_the_target_once_tax_bites():
    """If they were equal the solve would be doing nothing."""
    s = _plan()
    _, _, _, _, sol = project_plan(s)
    assert any(abs(sol.gross[fy] - sol.targets[fy]) > 1e-3
               for fy in sol.targets)


def test_other_income_reduces_what_must_be_withdrawn():
    from models import OtherIncome
    bare = _plan()
    rich = _plan()
    rich.personal_income = OtherIncome(taxable_interest=4.0)
    _, _, _, _, a = project_plan(bare)
    _, _, _, _, b = project_plan(rich)
    assert sum(b.gross.values()) < sum(a.gross.values()), (
        "other income should displace withdrawals, not stack on top")
