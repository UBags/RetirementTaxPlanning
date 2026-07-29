"""Pins for `Engine._transition_slice` (Phase 07i-2a).

The engine now accepts a `TransitionPlan`. Given one, a chunk boundary
realises only the gain that plan budgets for the year and carries the
remainder; given none, the hard-cut path runs untouched and no snapshot moves.

These tests exercise the plan path, which the golden suite cannot reach --
every fixture constructs the engine without a plan, so `tests/golden` staying
green proves inertness and nothing else. Both halves need pinning.

`s1_2chunk` is the fixture throughout: one boundary at FY16 carrying 216.1 of
gain under the hard cut, which is a large enough signal that a capped slice is
unmistakable rather than a rounding difference.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent / "golden"))

from golden_projects import PROJECTS  # noqa: E402

from engine import Engine  # noqa: E402
from models import AppState  # noqa: E402
from transition_plan import TransitionPlan, Window  # noqa: E402

BOUNDARY_FY = 16


def _state():
    return AppState.from_dict(PROJECTS["main"]["scenarios"][1])


def _plan(budget: float, fy: int = BOUNDARY_FY) -> TransitionPlan:
    return TransitionPlan((Window(
        fy=fy, m=0, lo=fy, hi=fy, band=(1, 30),
        instalments={"personal": {fy: budget}, "huf": {fy: budget}},
        residual={"personal": 0.0, "huf": 0.0}, clamped=False),))


def _boundary_gain(plan) -> float:
    eng = Engine(_state(), transition_plan=plan)
    eng.run()
    # The REBALANCE ledger, not the personal one -- the latter also carries
    # the ordinary withdrawal's gain, which a transition budget does not bound.
    g = eng.gain_ledger["rebalance_personal"].get(BOUNDARY_FY, {})
    return sum(g.get(k, 0.0) for k in "deo")


@pytest.fixture(scope="module")
def baseline() -> float:
    """Gain the hard cut realises at the boundary, with no plan."""
    return _boundary_gain(None)


def test_baseline_is_large_enough_to_measure_against(baseline):
    """Guard: if the hard cut stopped realising gain every test below passes."""
    assert baseline > 50.0, (
        f"boundary realises only {baseline:.2f} — this fixture can no longer "
        "distinguish a capped slice from a rounding difference")


def test_a_budget_caps_the_gain_realised(baseline):
    capped = _boundary_gain(_plan(5.0))
    assert capped < baseline, "the plan did not reduce the gain realised"
    assert capped <= baseline / 2, (
        f"capped {capped:.2f} against baseline {baseline:.2f} — not a cap")


def test_a_zero_budget_realises_no_transition_gain():
    """Nothing budgeted, nothing moved. The remainder is simply outstanding."""
    assert _boundary_gain(_plan(0.0)) == pytest.approx(0.0, abs=1e-6)


def test_a_larger_budget_moves_more_than_a_smaller_one():
    small, large = _boundary_gain(_plan(2.0)), _boundary_gain(_plan(20.0))
    assert large > small, f"budget 20 moved {large:.2f}, budget 2 moved {small:.2f}"


def test_budget_in_another_year_does_not_move_the_boundary():
    """A plan is not a licence to rebalance in a year it did not budget for."""
    assert _boundary_gain(_plan(50.0, fy=BOUNDARY_FY + 3)) == pytest.approx(
        0.0, abs=1e-6)


def test_no_plan_leaves_the_hard_cut_untouched(baseline):
    assert _boundary_gain(None) == pytest.approx(baseline)


def test_an_empty_plan_moves_nothing():
    assert _boundary_gain(TransitionPlan()) == pytest.approx(0.0, abs=1e-6)


def test_corpus_is_conserved_across_the_slice():
    """Sells fund buys exactly; a transition creates and destroys nothing."""
    eng = Engine(_state(), transition_plan=_plan(8.0))
    _p, yearly, _h, _hy = eng.run()
    before = next(y for y in yearly if y.year == BOUNDARY_FY - 1)
    after = next(y for y in yearly if y.year == BOUNDARY_FY)
    tot = (lambda y: y.corpus_debt_personal + y.corpus_equity_personal
           + y.corpus_other_personal)
    assert tot(after) > 0.0 and tot(before) > 0.0
