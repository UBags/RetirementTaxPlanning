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


# ─── windows, not just boundaries (07i-2b) ───────────────────────────────────

def _window_plan(lo: int, hi: int, budget: float,
                 fy: int = BOUNDARY_FY) -> TransitionPlan:
    """A window spanning FY lo..hi with the same budget in every year."""
    per = {y: budget for y in range(lo, hi + 1)}
    return TransitionPlan((Window(
        fy=fy, m=max(fy - lo, hi - fy), lo=lo, hi=hi, band=(1, 30),
        instalments={"personal": dict(per), "huf": dict(per)},
        residual={"personal": 0.0, "huf": 0.0}, clamped=False),))


def _gain_by_fy(plan):
    eng = Engine(_state(), transition_plan=plan)
    eng.run()
    led = eng.gain_ledger["rebalance_personal"]
    return {fy: sum(g.get(k, 0.0) for k in "deo") for fy, g in led.items()
            if sum(g.get(k, 0.0) for k in "deo") > 1e-9}


def test_a_window_moves_gain_in_non_boundary_years():
    """The point of 07: years that are not the boundary do the work."""
    by_fy = _gain_by_fy(_window_plan(10, 22, 6.0))
    assert by_fy, "the window realised no gain at all"
    off_boundary = {fy: v for fy, v in by_fy.items() if fy != BOUNDARY_FY}
    assert off_boundary, (
        f"gain only at the boundary {by_fy} — the trigger did not widen")
    assert len(by_fy) >= 3, f"only {len(by_fy)} year(s) participated: {by_fy}"


def test_a_window_opening_early_starts_before_the_boundary():
    by_fy = _gain_by_fy(_window_plan(8, 20, 6.0))
    assert min(by_fy) < BOUNDARY_FY, (
        f"earliest year to move was FY{min(by_fy)}, not before FY{BOUNDARY_FY}")


def test_each_year_respects_its_own_budget():
    budget = 4.0
    for fy, gain in _gain_by_fy(_window_plan(12, 20, budget)).items():
        assert gain <= budget + 0.5, (
            f"FY{fy} realised {gain:.2f} against a budget of {budget}")


def test_a_window_realises_less_in_its_worst_year_than_the_hard_cut(baseline):
    """Spreading is the mechanism: no single year should carry the boundary."""
    worst = max(_gain_by_fy(_window_plan(8, 24, 6.0)).values())
    assert worst < baseline / 4, (
        f"worst year {worst:.2f} against a hard cut of {baseline:.2f}")


def test_a_wider_window_moves_at_least_as_much_in_total():
    narrow = sum(_gain_by_fy(_window_plan(15, 17, 6.0)).values())
    wide = sum(_gain_by_fy(_window_plan(8, 24, 6.0)).values())
    assert wide >= narrow - 1e-6, f"wide {wide:.2f} moved less than {narrow:.2f}"


def test_a_window_outside_any_boundary_still_only_targets_its_chunk():
    """Budget in FY2-5 with the window's boundary at FY16 targets FY16's chunk."""
    by_fy = _gain_by_fy(_window_plan(2, 5, 6.0))
    assert all(fy <= 5 for fy in by_fy), f"moved outside its window: {by_fy}"


def test_a_plan_suppresses_the_hard_cut_at_a_boundary_it_does_not_cover():
    """The plan is authoritative; an uncovered boundary is a residual.

    Before this was pinned, a window over FY2-5 left the FY16 boundary
    uncovered and the hard cut fired there anyway, realising 32.9 the plan had
    never budgeted. Completing a transition the plan withheld is the same
    failure as a scheduler widening its own window: constraint breached,
    report still green.
    """
    by_fy = _gain_by_fy(_window_plan(2, 5, 6.0))
    assert BOUNDARY_FY not in by_fy, (
        f"the hard cut fired at FY{BOUNDARY_FY} despite a plan: {by_fy}")


def test_a_pre_boundary_window_actually_pre_transitions():
    """The slice must aim at the window's chunk, not the calendar year's.

    A window over FY8-15 sits entirely BEFORE the FY16 boundary, so every one
    of those years belongs to the old chunk. Aiming at
    `get_funds_for_year(fy)` would target the allocation the portfolio already
    holds and only correct drift; aiming at the boundary year's chunk actually
    moves the portfolio toward where it must end up.

    Measured: 48.10 with the correct target, 3.25 with the wrong one. Every
    other test in this file passes under that sabotage, because they all
    assert that gain was realised rather than that the portfolio MOVED
    somewhere. This is the one that bites.
    """
    moved = sum(_gain_by_fy(_window_plan(8, 15, 6.0)).values())
    assert moved > 20.0, (
        f"a pre-boundary window moved only {moved:.2f} — the slice is "
        "targeting the calendar year's chunk, not the window's")
