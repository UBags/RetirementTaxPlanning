"""Pins for `transition_driver` (Phase 07i-2f).

The driver supplies `converge` with a real projector: it runs the engine under
a candidate plan and reads back the two things a plan is built from -- the
tax-free headroom each year has left after the withdrawal, and the gain each
boundary still owes.

These tests are slower than the rest of the suite because measuring means
projecting, and projecting means solving the schedule. `s0_1chunk` carries the
cheap cases (no boundary, so no window to build) and `s1_2chunk` the one case
that has to exercise a real convergence.

The pure parts -- `_blend` above all -- are tested directly, because
under-relaxation is what makes the iteration settle at all and a blend that
quietly did nothing would leave the driver oscillating while still reporting a
plan.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent / "golden"))

from golden_projects import PROJECTS  # noqa: E402

from models import AppState  # noqa: E402
from transition_driver import _blend, measure, plan_transitions  # noqa: E402
from transition_plan import PURSES, Boundary, Measurement  # noqa: E402


def _state(index: int):
    return AppState.from_dict(PROJECTS["main"]["scenarios"][index])


def _m(need_p: float, need_h: float, spare: float, fy: int = 16) -> Measurement:
    return Measurement(
        boundaries=(Boundary(fy=fy, turnover=0.2,
                             gain={"personal": need_p, "huf": need_h}),),
        spare={p: {y: spare for y in range(1, 31)} for p in PURSES},
        horizon=30)


# ─── under-relaxation ────────────────────────────────────────────────────────

def test_blend_moves_part_way():
    out = _blend(_m(100.0, 20.0, 9.0), _m(0.0, 0.0, 5.0), 0.25)
    assert out.boundaries[0].need("personal") == pytest.approx(75.0)
    assert out.boundaries[0].need("huf") == pytest.approx(15.0)
    assert out.spare["personal"][1] == pytest.approx(8.0)


def test_blend_of_one_is_the_new_reading():
    out = _blend(_m(100.0, 20.0, 9.0), _m(30.0, 4.0, 5.0), 1.0)
    assert out.boundaries[0].need("personal") == pytest.approx(30.0)
    assert out.spare["personal"][1] == pytest.approx(5.0)


def test_blend_attenuates_an_alternating_sequence():
    """The behaviour the driver depends on, stated honestly.

    Raw measurements on `s1_2chunk` went 159.50, 60.48, 142.88, 93.60, 121.01,
    106.60 -- a swing that halves each pass but nowhere near fast enough for a
    sensible cap.

    Under-relaxation does NOT eliminate a persistent alternation. Feeding
    `_blend` a fixed hi/lo sequence at alpha 0.5 settles into a two-cycle of
    amplitude (hi - lo) / 3, not a point. What it does is attenuate: a 100
    swing becomes about 33. The real iteration converges because the
    measurement responds to the plan and the feedback is contractive; blending
    buys enough attenuation for that contraction to finish inside the cap.
    """
    hi, lo = _m(160.0, 0.0, 9.0), _m(60.0, 0.0, 9.0)
    cur, seq = hi, []
    for k in range(10):
        cur = _blend(cur, lo if k % 2 == 0 else hi, 0.5)
        seq.append(cur.boundaries[0].need("personal"))
    raw_swing = 160.0 - 60.0
    swing = max(seq[-4:]) - min(seq[-4:])
    assert swing < raw_swing / 2, (
        f"blending attenuated a {raw_swing} swing only to {swing:.1f}: {seq}")
    assert all(60.0 <= v <= 160.0 for v in seq), "blend left the input range"


def test_blend_keeps_a_boundary_the_old_reading_never_saw():
    out = _blend(_m(100.0, 20.0, 9.0, fy=16), _m(50.0, 10.0, 9.0, fy=21), 0.5)
    assert out.boundaries[0].fy == 21
    assert out.boundaries[0].need("personal") == pytest.approx(50.0)


# ─── measuring a real projection ─────────────────────────────────────────────

@pytest.fixture(scope="module")
def baseline_measurement():
    return measure(_state(1), None)


def test_measure_finds_the_boundary(baseline_measurement):
    assert [b.fy for b in baseline_measurement.boundaries] == [16]


def test_measure_reports_a_requirement_for_both_purses(baseline_measurement):
    b = baseline_measurement.boundaries[0]
    for purse in PURSES:
        assert b.need(purse) > 0.0, f"{purse} boundary requirement is zero"


def test_spare_is_never_negative_and_is_bounded(baseline_measurement):
    for purse in PURSES:
        vals = baseline_measurement.spare[purse]
        assert len(vals) == 30
        assert all(v >= 0.0 for v in vals.values())
        assert any(v > 0.0 for v in vals.values()), (
            f"{purse} has no spare capacity anywhere — fixture is wrong")


def test_a_scenario_with_no_boundary_has_nothing_to_plan():
    m = measure(_state(0), None)
    assert m.boundaries == ()


# ─── the fixed point end to end ──────────────────────────────────────────────

def test_a_single_chunk_plan_converges_immediately():
    result = plan_transitions(_state(0))
    assert result.converged
    assert result.plan.windows == ()


def test_two_chunks_converge_to_a_settled_window():
    result = plan_transitions(_state(1))
    assert result.converged, (
        f"did not settle in {result.iterations} passes; "
        f"residuals {result.residuals}")
    assert len(result.plan.windows) == 1
    w = result.plan.windows[0]
    assert w.fy == 16
    assert w.lo < w.fy < w.hi, "the window did not straddle its boundary"
    assert w.complete, "a scenario with ample capacity left a residual"


def test_undamped_iteration_does_not_settle():
    """Damping is load-bearing, not a tuning nicety."""
    damped = plan_transitions(_state(1))
    undamped = plan_transitions(_state(1), damping=1.0, max_iterations=6)
    assert damped.converged
    assert not undamped.converged, (
        "the undamped iteration settled — this fixture no longer shows why "
        "under-relaxation is needed")


def test_damping_must_be_a_fraction():
    for bad in (0.0, -0.5, 1.5):
        with pytest.raises(ValueError):
            plan_transitions(_state(0), damping=bad)
