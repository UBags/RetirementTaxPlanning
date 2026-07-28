"""Pins for `transition_plan` (Phase 07g).

The module lands with no caller, so these tests are the only thing standing
between it and silent wrongness when the engine starts consuming it.

Fixtures are shaped like the plan, per METHODOLOGY §7.9: a large corpus drawn
down slowly, boundaries at realistic spacing, capacity of the order the golden
scenarios actually show. A synthetic case that does not resemble the workload
proves nothing -- 3000 randomised steps once passed while never opening a
second folio.

Two guards carry more weight than the rest:

* `test_plan_is_not_vacuous` fails if the corpus fixture stops producing
  multiple windows with a clamped one among them. Without it, every other
  assertion here could be true of an empty plan.
* `test_plans_equal_detects_*` pins `plans_equal` from BOTH sides. A
  convergence test that can only return True would stop the fixed-point driver
  after one pass while reporting success -- which is the B1d-ii shape, a
  routine reporting completion while failing the constraint it exists to
  satisfy.
"""
from __future__ import annotations

import pytest

from transition_plan import (
    PURSES,
    Boundary,
    TransitionPlan,
    bands,
    build_plan,
    normalise,
    plans_equal,
    spread_residual,
    turnover_fraction,
)

HORIZON = 30


def flat(value: float, lo: int = 1, hi: int = HORIZON) -> dict:
    return {fy: value for fy in range(lo, hi + 1)}


def corpus_like_spare() -> dict:
    """Capacity of the order `s3_4chunk` actually shows: ~9 personal, ~3 HUF."""
    return {"personal": flat(9.0), "huf": flat(4.0)}


# ─── allocation arithmetic ────────────────────────────────────────────────────

def test_normalise_ignores_scale_and_drops_inactive():
    a = normalise({"A": 17.1836, "B": 17.1836, "C": 0.0})
    b = normalise({"A": 0.08, "B": 0.08})
    assert a == pytest.approx(b)
    assert "C" not in a
    assert sum(a.values()) == pytest.approx(1.0)


def test_turnover_zero_when_allocations_match():
    same = {"A": 5.0, "B": 5.0}
    assert turnover_fraction(same, dict(same)) == pytest.approx(0.0)


def test_turnover_one_when_disjoint():
    assert turnover_fraction({"A": 1.0}, {"B": 1.0}) == pytest.approx(1.0)


def test_turnover_counts_reweighting_not_just_fund_changes():
    """`s3_4chunk` FY17 retires no fund and admits none, yet still moves 1.1%.

    A rule keyed on the fund list alone would size this boundary at zero and
    book its gain anyway.
    """
    old = {"A": 50.0, "B": 50.0}
    new = {"A": 60.0, "B": 40.0}
    assert turnover_fraction(old, new) == pytest.approx(0.10)


def test_turnover_is_symmetric_in_magnitude():
    old, new = {"A": 70.0, "B": 30.0}, {"A": 30.0, "B": 70.0}
    assert turnover_fraction(old, new) == pytest.approx(
        turnover_fraction(new, old))


def test_bands_span_neighbouring_boundaries():
    assert bands([9, 17, 24], HORIZON) == {
        9: (1, 16), 17: (9, 23), 24: (17, 30)}


def test_bands_single_boundary_spans_the_plan():
    assert bands([16], HORIZON) == {16: (1, 30)}


# ─── the search ───────────────────────────────────────────────────────────────

def test_no_boundaries_gives_an_empty_plan():
    plan = build_plan([], corpus_like_spare(), HORIZON)
    assert plan.windows == ()
    assert plan.complete
    assert plan.residual_total() == pytest.approx(0.0)


def test_year_covering_its_own_gain_needs_no_window():
    """The general rule, replacing any turnover threshold."""
    b = Boundary(fy=17, turnover=0.011, gain={"personal": 7.8, "huf": 2.5})
    plan = build_plan([b], corpus_like_spare(), HORIZON)
    w = plan.windows[0]
    assert w.m == 0 and w.lo == 17 and w.hi == 17
    assert w.complete


def test_window_widens_until_it_completes():
    b = Boundary(fy=16, turnover=0.41, gain={"personal": 80.0, "huf": 25.0})
    plan = build_plan([b], corpus_like_spare(), HORIZON)
    w = plan.windows[0]
    assert w.m > 0
    assert w.complete
    for p in PURSES:
        assert sum(w.instalments[p].values()) == pytest.approx(
            b.need(p), abs=1e-6)


def test_smallest_m_is_chosen_not_a_wider_one():
    """Cost does not rise with length, so a wider window buys nothing."""
    b = Boundary(fy=16, turnover=0.4, gain={"personal": 27.0, "huf": 9.0})
    w = build_plan([b], corpus_like_spare(), HORIZON).windows[0]
    # 9.0 personal a year: FY15-17 is 27.0 exactly, so M=1 suffices.
    assert w.m == 1, f"expected the smallest completing M, got {w.m}"


def test_instalments_are_not_a_fixed_fraction():
    """METHODOLOGY §15.4. Instalments track capacity, not a schedule."""
    spare = {"personal": {fy: 4.0 + (fy % 5) for fy in range(1, 31)},
             "huf": flat(3.0)}
    b = Boundary(fy=16, turnover=0.4, gain={"personal": 40.0, "huf": 6.0})
    w = build_plan([b], spare, HORIZON).windows[0]
    amounts = [v for v in w.instalments["personal"].values() if v > 1e-9]
    assert len(amounts) >= 3
    assert len(set(round(a, 6) for a in amounts)) > 1, (
        f"instalments look like a fixed fraction: {amounts}")


def test_a_year_is_never_spent_twice_across_overlapping_windows():
    """The double-spend guard. Two boundaries whose windows share years.

    The fixture is sized so the windows genuinely collide -- boundaries six
    years apart, each needing eleven years of capacity. An earlier version of
    this test used boundaries at FY9 and FY17 with smaller needs, and the two
    windows settled at FY6-12 and FY14-20 without touching. It passed with the
    capacity decrement deleted from `build_plan`, which is the whole defect it
    exists to catch. The overlap assertion below is there so that can never
    silently recur.
    """
    spare = corpus_like_spare()
    bs = [Boundary(fy=12, turnover=0.3, gain={"personal": 100.0, "huf": 40.0}),
          Boundary(fy=18, turnover=0.3, gain={"personal": 100.0, "huf": 40.0})]
    plan = build_plan(bs, spare, HORIZON)

    a, b = plan.windows
    assert max(a.lo, b.lo) <= min(a.hi, b.hi), (
        f"windows FY{a.lo}-{a.hi} and FY{b.lo}-{b.hi} do not overlap — "
        "this fixture cannot exercise the double-spend path")

    for p in PURSES:
        per_year: dict = {}
        for w in plan.windows:
            for fy, v in w.instalments[p].items():
                per_year[fy] = per_year.get(fy, 0.0) + v
        for fy, spent in per_year.items():
            assert spent <= spare[p][fy] + 1e-6, (
                f"{p} FY{fy}: spent {spent:.6f} of {spare[p][fy]:.6f}")


def test_windows_never_cross_their_band():
    bs = [Boundary(fy=9, turnover=0.2, gain={"personal": 200.0, "huf": 90.0}),
          Boundary(fy=17, turnover=0.2, gain={"personal": 200.0, "huf": 90.0}),
          Boundary(fy=24, turnover=0.2, gain={"personal": 200.0, "huf": 90.0})]
    plan = build_plan(bs, corpus_like_spare(), HORIZON)
    assert plan.windows, "fixture produced no windows"
    for w in plan.windows:
        assert w.band[0] <= w.lo <= w.fy <= w.hi <= w.band[1]


def test_shortfall_is_reported_not_bought_by_widening():
    """`s3_4chunk` FY24. The residual must survive to the caller."""
    b = Boundary(fy=24, turnover=0.23, gain={"personal": 400.0, "huf": 200.0})
    plan = build_plan([b], corpus_like_spare(), HORIZON)
    w = plan.windows[0]
    assert w.clamped
    assert not w.complete
    assert w.residual["personal"] > 0.0
    assert plan.residual_total() > 0.0
    assert w.band[0] <= w.lo and w.hi <= w.band[1]


def test_a_purse_shortfall_is_not_masked_by_the_other_purse():
    """Headroom is not fungible between entities."""
    spare = {"personal": flat(0.5), "huf": flat(50.0)}
    b = Boundary(fy=16, turnover=0.4, gain={"personal": 100.0, "huf": 1.0})
    w = build_plan([b], spare, HORIZON).windows[0]
    assert w.residual["personal"] > 0.0, "personal shortfall was hidden"
    assert w.residual["huf"] == pytest.approx(0.0)


def test_budget_sums_across_overlapping_windows():
    spare = corpus_like_spare()
    bs = [Boundary(fy=12, turnover=0.2, gain={"personal": 45.0, "huf": 9.0}),
          Boundary(fy=20, turnover=0.2, gain={"personal": 45.0, "huf": 9.0})]
    plan = build_plan(bs, spare, HORIZON)
    for fy in range(1, HORIZON + 1):
        direct = sum(w.instalments["personal"].get(fy, 0.0)
                     for w in plan.windows)
        assert plan.budget("personal", fy) == pytest.approx(direct)
        assert plan.budget("personal", fy) <= spare["personal"][fy] + 1e-6


def test_plan_is_not_vacuous():
    """Guard against every other assertion here being trivially true."""
    bs = [Boundary(fy=9, turnover=0.21, gain={"personal": 34.0, "huf": 9.0}),
          Boundary(fy=17, turnover=0.01, gain={"personal": 7.8, "huf": 3.5}),
          Boundary(fy=24, turnover=0.23, gain={"personal": 400.0, "huf": 200.0})]
    plan = build_plan(bs, corpus_like_spare(), HORIZON)
    assert len(plan.windows) == 3
    assert any(w.complete for w in plan.windows), "no window completes"
    assert any(w.clamped and not w.complete for w in plan.windows), (
        "no window is clamped short — the residual path is untested")
    assert any(w.m == 0 for w in plan.windows), "no M=0 window"
    assert any(w.m > 0 for w in plan.windows), "no widened window"


# ─── convergence ──────────────────────────────────────────────────────────────

def _two_boundary_plan(gain_p: float = 45.0) -> TransitionPlan:
    bs = [Boundary(fy=11, turnover=0.2, gain={"personal": gain_p, "huf": 9.0}),
          Boundary(fy=21, turnover=0.2, gain={"personal": 40.0, "huf": 9.0})]
    return build_plan(bs, corpus_like_spare(), HORIZON)


def test_plans_equal_accepts_an_identical_plan():
    assert plans_equal(_two_boundary_plan(), _two_boundary_plan())


def test_plans_equal_detects_a_changed_instalment():
    """If this can only return True the fixed point stops after one pass."""
    assert not plans_equal(_two_boundary_plan(45.0), _two_boundary_plan(50.0))


def test_plans_equal_detects_a_changed_window_count():
    assert not plans_equal(_two_boundary_plan(), build_plan(
        [Boundary(fy=11, turnover=0.2, gain={"personal": 45.0, "huf": 9.0})],
        corpus_like_spare(), HORIZON))


def test_plans_equal_respects_tolerance():
    # 44.0 sits inside a window rather than exactly on its edge, so a
    # nanoscopic change moves the instalments without moving M.
    a = _two_boundary_plan(44.0)
    b = _two_boundary_plan(44.0 + 1e-9)
    assert plans_equal(a, b, tol=1e-6)
    assert not plans_equal(a, _two_boundary_plan(46.0), tol=1e-6)


def test_window_choice_is_knife_edge_at_margin_one():
    """At margin=1.0 an infinitesimal change in need can move M by a whole year.

    Capacity here is 9.0 a year, so a need of exactly 45.0 is covered by the
    five years FY9-13 and takes M=2. One nano-rupee more does not fit, and the
    window jumps to M=3.

    This is not a defect -- it is what "smallest M that completes" means at a
    tolerance of zero. It matters because Phase 07h iterates to a fixed point:
    the gain each boundary must realise is re-measured every pass, and a
    boundary sitting on an exact multiple of capacity can oscillate between
    two window widths forever. A margin above 1.0 moves the decision off the
    knife edge, which is why `build_plan` takes one.
    """
    assert _two_boundary_plan(45.0).windows[0].m == 2
    assert _two_boundary_plan(45.0 + 1e-9).windows[0].m == 3
    assert not plans_equal(_two_boundary_plan(45.0),
                           _two_boundary_plan(45.0 + 1e-9))


# ─── residual placement ───────────────────────────────────────────────────────

def _convex_tax(_fy: int, extra: float) -> float:
    """A two-band slab: free to 5, then 30%. Convex, like a real one."""
    return max(0.0, extra - 5.0) * 0.30


def test_spread_beats_dumping_under_a_convex_charge():
    years = list(range(17, 31))
    plan = spread_residual(years, 30.0, _convex_tax, step=0.25)
    assert sum(plan.values()) == pytest.approx(30.0, abs=1e-6)
    spread_cost = sum(_convex_tax(fy, v) for fy, v in plan.items())
    assert spread_cost < _convex_tax(17, 30.0)
    assert spread_cost == pytest.approx(0.0, abs=1e-6)  # 30 over 14 years fits


def test_spread_uses_more_than_one_year_when_it_must():
    plan = spread_residual(list(range(17, 31)), 40.0, _convex_tax, step=0.25)
    assert len(plan) > 1
    assert sum(plan.values()) == pytest.approx(40.0, abs=1e-6)


def test_spread_of_nothing_is_nothing():
    assert spread_residual([1, 2, 3], 0.0, _convex_tax) == {}
    assert spread_residual([], 10.0, _convex_tax) == {}
