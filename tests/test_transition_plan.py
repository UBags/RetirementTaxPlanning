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
    Measurement,
    TransitionPlan,
    bands,
    build_plan,
    converge,
    distance_to_target,
    normalise,
    plans_equal,
    plans_same_shape,
    rebalance_moves,
    spread_residual,
    turnover_fraction,
    widest_seen,
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


# ─── the fixed point ──────────────────────────────────────────────────────────

def _measurement(gain_p: float, gain_h: float = 9.0,
                 fys=(11,)) -> Measurement:
    return Measurement(
        boundaries=tuple(
            Boundary(fy=fy, turnover=0.2,
                     gain={"personal": gain_p, "huf": gain_h})
            for fy in fys),
        spare=corpus_like_spare(),
        horizon=HORIZON,
    )


class Projector:
    """Stub engine. Returns a scripted measurement per call, recording plans.

    The real engine cannot exercise this path until the rebalance sites
    consume a plan, so the driver is tested against something that responds to
    one by construction.
    """

    def __init__(self, script):
        self.script = list(script)
        self.plans = []

    def __call__(self, plan):
        self.plans.append(plan)
        i = min(len(self.plans) - 1, len(self.script) - 1)
        return self.script[i]


def test_converge_baseline_is_measured_without_a_plan():
    proj = Projector([_measurement(40.0)])
    converge(proj, max_iterations=3)
    assert proj.plans[0] is None, "the first projection must be the baseline"


def test_converge_settles_when_the_projection_stops_moving():
    proj = Projector([_measurement(40.0)])
    result = converge(proj, max_iterations=5)
    assert result.converged
    assert not result.oscillated
    assert result.iterations == 2
    assert len(proj.plans) == 2


def test_converge_takes_several_passes_when_the_measurement_drifts():
    proj = Projector([_measurement(40.0), _measurement(50.0),
                      _measurement(60.0), _measurement(60.0)])
    result = converge(proj, max_iterations=5)
    assert result.converged
    assert result.iterations == 4


def test_converge_never_exceeds_its_cap():
    """The cost ceiling. project_plan already runs the engine three or four
    times, so an unbounded fixed point around it would be ruinous."""
    proj = Projector([_measurement(40.0 + 7.0 * k) for k in range(50)])
    result = converge(proj, max_iterations=5)
    assert not result.converged
    assert len(proj.plans) == 5
    assert result.iterations == 5


def test_converge_detects_an_oscillation():
    """A two-cycle, which a naive 'compare with previous' would never end."""
    a, b = _measurement(45.0), _measurement(80.0)
    assert not plans_equal(
        build_plan(a.boundaries, a.spare, a.horizon, 1.2),
        build_plan(b.boundaries, b.spare, b.horizon, 1.2)), (
        "the stub does not actually oscillate — this test proves nothing")
    proj = Projector([a, b, a, b, a, b, a, b])
    result = converge(proj, max_iterations=5)
    assert not result.converged
    assert result.oscillated


def test_converge_returns_the_least_unfunded_plan_when_it_fails():
    big = _measurement(400.0, gain_h=200.0)     # cannot complete
    small = _measurement(40.0)                  # completes
    proj = Projector([big, small, big, small, big])
    result = converge(proj, max_iterations=5)
    assert not result.converged
    assert result.plan.residual_total() == pytest.approx(min(result.residuals))
    assert result.chosen == result.residuals.index(min(result.residuals))


def test_converge_is_deterministic():
    script = [_measurement(40.0 + 7.0 * k) for k in range(50)]
    a = converge(Projector(list(script)), max_iterations=5)
    b = converge(Projector(list(script)), max_iterations=5)
    assert plans_equal(a.plan, b.plan)
    assert (a.converged, a.oscillated, a.chosen) == (
        b.converged, b.oscillated, b.chosen)


def test_converge_passes_the_margin_through():
    """A wider margin must buy a wider window, or it is not being applied."""
    proj_lo = Projector([_measurement(45.0)])
    proj_hi = Projector([_measurement(45.0)])
    lo = converge(proj_lo, max_iterations=2, margin=1.0)
    hi = converge(proj_hi, max_iterations=2, margin=1.5)
    assert hi.plan.windows[0].m > lo.plan.windows[0].m


def test_converge_rejects_a_nonsense_cap():
    with pytest.raises(ValueError):
        converge(Projector([_measurement(40.0)]), max_iterations=0)


# ─── slice to fund movements ──────────────────────────────────────────────────

OLD_MIX = {"A": 40.0, "B": 40.0, "C": 20.0}       # 100 of value
NEW_W = {"A": 20.0, "B": 40.0, "D": 40.0}         # C retires, D arrives


def test_distance_counts_retiring_funds_in_full():
    assert distance_to_target({"C": 50.0, "B": 50.0},
                              {"B": 100.0}) == pytest.approx(50.0)


def test_distance_is_zero_at_target():
    assert distance_to_target({"A": 20.0, "B": 40.0, "D": 40.0},
                              NEW_W) == pytest.approx(0.0)


def test_distance_counts_reweighting_without_fund_changes():
    """`s3_4chunk` FY17 changes no fund, only weights, and still must move."""
    assert distance_to_target({"A": 60.0, "B": 40.0},
                              {"A": 50.0, "B": 50.0}) == pytest.approx(10.0)


def test_distance_of_an_empty_purse_is_zero():
    assert distance_to_target({}, NEW_W) == pytest.approx(0.0)


def test_moves_are_balanced_and_sized():
    sells, buys = rebalance_moves(OLD_MIX, NEW_W, 12.0)
    assert sum(sells.values()) == pytest.approx(12.0)
    assert sum(buys.values()) == pytest.approx(12.0)


def test_moves_only_sell_funds_above_target():
    """Churning a fund already at target realises gain and moves nothing."""
    sells, buys = rebalance_moves(OLD_MIX, NEW_W, 12.0)
    assert set(sells) == {"A", "C"}      # B is exactly at target
    assert set(buys) == {"D"}
    assert "B" not in sells and "B" not in buys


def test_a_full_slice_lands_exactly_on_target():
    dist = distance_to_target(OLD_MIX, NEW_W)
    sells, buys = rebalance_moves(OLD_MIX, NEW_W, dist)
    after = dict(OLD_MIX)
    for n, v in sells.items():
        after[n] = after.get(n, 0.0) - v
    for n, v in buys.items():
        after[n] = after.get(n, 0.0) + v
    total = sum(after.values())
    for n, w in normalise(NEW_W).items():
        assert after.get(n, 0.0) == pytest.approx(total * w, abs=1e-9)
    assert after.get("C", 0.0) == pytest.approx(0.0, abs=1e-9)


def test_an_oversized_slice_never_overshoots():
    dist = distance_to_target(OLD_MIX, NEW_W)
    sells, _ = rebalance_moves(OLD_MIX, NEW_W, dist * 10)
    assert sum(sells.values()) == pytest.approx(dist)


def test_repeated_slices_converge_and_never_overshoot():
    """The window in miniature: several partial moves reaching the target."""
    values = dict(OLD_MIX)
    start = distance_to_target(values, NEW_W)
    assert start > 0, "fixture is already at target — proves nothing"

    seen = [start]
    for _ in range(12):
        sells, buys = rebalance_moves(values, NEW_W, 4.0)
        if not sells:
            break
        for n, v in sells.items():
            values[n] = values.get(n, 0.0) - v
        for n, v in buys.items():
            values[n] = values.get(n, 0.0) + v
        seen.append(distance_to_target(values, NEW_W))

    assert seen[-1] == pytest.approx(0.0, abs=1e-9), f"did not converge: {seen}"
    assert all(b <= a + 1e-9 for a, b in zip(seen, seen[1:])), (
        f"distance increased during the transition: {seen}")
    assert sum(values.values()) == pytest.approx(sum(OLD_MIX.values()))
    assert len(seen) > 3, "converged in one step — not a partial transition"


def test_no_moves_when_already_at_target():
    sells, buys = rebalance_moves({"A": 20.0, "B": 40.0, "D": 40.0},
                                  NEW_W, 50.0)
    assert sells == {} and buys == {}


def test_no_moves_for_a_zero_slice():
    assert rebalance_moves(OLD_MIX, NEW_W, 0.0) == ({}, {})


# ─── geometry comparison, M floors, chatter resolution (07i-2f) ──────────────

def test_same_shape_ignores_instalment_drift():
    """The decision is which years to spend in; instalments are derived."""
    a = _two_boundary_plan(44.0)
    b = _two_boundary_plan(44.5)
    assert not plans_equal(a, b), "fixture chosen badly — instalments match"
    assert [(w.fy, w.m, w.lo, w.hi) for w in a.windows] == \
           [(w.fy, w.m, w.lo, w.hi) for w in b.windows]
    assert plans_same_shape(a, b)


def test_same_shape_detects_a_moved_window():
    assert not plans_same_shape(_two_boundary_plan(45.0),
                                _two_boundary_plan(200.0))


def test_same_shape_detects_a_changed_window_count():
    one = build_plan(
        [Boundary(fy=11, turnover=0.2, gain={"personal": 45.0, "huf": 9.0})],
        corpus_like_spare(), HORIZON)
    assert not plans_same_shape(_two_boundary_plan(), one)


def test_converge_accepts_an_injected_comparator():
    """Geometry-only comparison settles what instalment equality cannot."""
    # Drifting every pass, so the stub never repeats itself inside the cap --
    # a repeated measurement would let instalment equality converge too and
    # the test would prove nothing.
    script = [_measurement(40.0 + 0.1 * k) for k in range(8)]
    loose = converge(Projector(list(script)), max_iterations=5,
                     compare=plans_same_shape)
    strict = converge(Projector(list(script)), max_iterations=5,
                      compare=plans_equal)
    assert [(w.fy, w.m) for w in loose.plan.windows] == \
           [(w.fy, w.m) for w in strict.plan.windows], (
        "the drift moved the geometry — fixture proves nothing")
    assert loose.converged
    assert not strict.converged


def test_min_m_floors_the_search():
    b = Boundary(fy=17, turnover=0.01, gain={"personal": 7.8, "huf": 2.5})
    free = build_plan([b], corpus_like_spare(), HORIZON)
    assert free.windows[0].m == 0
    floored = build_plan([b], corpus_like_spare(), HORIZON, min_m={17: 3})
    assert floored.windows[0].m == 3
    assert floored.windows[0].complete, "a wider window must still complete"


def test_min_m_of_zero_changes_nothing():
    b = Boundary(fy=16, turnover=0.4, gain={"personal": 80.0, "huf": 25.0})
    a = build_plan([b], corpus_like_spare(), HORIZON)
    z = build_plan([b], corpus_like_spare(), HORIZON, min_m={16: 0})
    assert plans_equal(a, z)


def test_min_m_never_narrows_a_window():
    """A floor is a floor: a boundary needing more than the floor keeps it."""
    b = Boundary(fy=16, turnover=0.4, gain={"personal": 80.0, "huf": 25.0})
    wide = build_plan([b], corpus_like_spare(), HORIZON).windows[0].m
    floored = build_plan([b], corpus_like_spare(), HORIZON,
                         min_m={16: 1}).windows[0].m
    assert floored == wide, f"floor of 1 changed a window needing M={wide}"


def test_widest_seen_takes_the_maximum_per_boundary():
    a = build_plan([Boundary(fy=17, turnover=0.0,
                             gain={"personal": 7.8, "huf": 2.5})],
                   corpus_like_spare(), HORIZON)
    b = build_plan([Boundary(fy=17, turnover=0.0,
                             gain={"personal": 30.0, "huf": 9.0})],
                   corpus_like_spare(), HORIZON)
    assert a.windows[0].m < b.windows[0].m, "fixture does not differ"
    assert widest_seen([a, b]) == {17: b.windows[0].m}
    assert widest_seen([b, a]) == {17: b.windows[0].m}


def test_widest_seen_of_nothing_is_empty():
    assert widest_seen([]) == {}
    assert widest_seen([TransitionPlan()]) == {}
