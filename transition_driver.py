"""Driving the transition plan to a fixed point against real projections.

`transition_plan.converge` does the iterating and is tested against stubs.
This module supplies the real projector: it runs the engine under a candidate
plan and reads back the two things a plan is built from -- the tax-free
headroom each year has left once the ordinary withdrawal has taken its share,
and the gain each boundary still owes.

WHY IT HAS TO ITERATE

A window is sized against spare capacity, and spreading a transition CHANGES
that capacity: the boundary stops dumping its whole turnover into one year, so
the withdrawal's own gain in the surrounding years shifts and the spare
profile moves under the plan that was sized against it. Measuring once takes
numbers from a run the fix then invalidates.

It is not a small effect either. The distance to target does not even fall
monotonically across a window -- the ordinary withdrawal draws unevenly and
growth compounds unevenly, so the portfolio drifts away from target while the
transition is chasing it.

WHY THE REQUIREMENT IS READ, NOT SCALED

Under a plan the transition gain is spread across the window, so the boundary
year's rebalance gain no longer says what the boundary NEEDS -- only what that
one year moved. Summing the window works when the window completed and
under-reports when it starved, which would shrink the next window and
converge on an under-funded plan that reports success. So the requirement is
realised-so-far plus `remaining_gain`, which `Engine.transition_ledger` prices
exactly through `gain_within_cash`.

COST

One `project_plan` (three or four engine runs to solve the schedule) plus one
more run to read the ledgers, per iteration, capped at five iterations. That
is deliberate and bounded rather than incidental.
"""
from __future__ import annotations

import sys
from typing import Dict, Optional

from engine import Engine, free_gain_capacity
from models import AppState
from net_income_plan import project_plan
from transition_plan import (
    PURSES,
    Boundary,
    Convergence,
    Measurement,
    TransitionPlan,
    build_plan,
    converge,
    plans_same_shape,
    widest_seen,
)

__all__ = ["measure", "plan_transitions"]

_LEDGER_KEY = {"personal": "rebalance_personal", "huf": "rebalance_huf"}
_ENTITY = {"personal": "individual", "huf": "huf"}


def _spare(state: AppState, eng: Engine) -> Dict[str, Dict[int, float]]:
    """Gain each purse may still realise tax-free, after the withdrawal.

    Gross headroom is the wrong input: the cash withdrawal is charged first
    (Part I §5.3), and sizing a window against gross would over-commit it.
    """
    led = eng.gain_ledger
    out: Dict[str, Dict[int, float]] = {p: {} for p in PURSES}
    for fy in state.fy_range():
        months = eng.months_in_fy(fy)
        for purse in PURSES:
            entity = _ENTITY[purse]
            income = (state.personal_income if purse == "personal"
                      else state.huf_income)
            gross = free_gain_capacity(
                state.get_debt_tax_chunk(fy, entity), entity,
                income.assessable(fy, months, entity))
            total_d = led[purse].get(fy, {}).get("gain_d", 0.0)
            rebal_d = led[_LEDGER_KEY[purse]].get(fy, {}).get("d", 0.0)
            withdrawal_d = max(0.0, total_d - rebal_d)
            out[purse][fy] = max(0.0, gross - withdrawal_d)
    return out


def _requirement(eng: Engine, boundary: int, purse: str,
                 plan: Optional[TransitionPlan]) -> float:
    """Gain this boundary must realise, measured under the plan in force."""
    if plan is None:
        # The hard cut books the whole turnover in the boundary year.
        booked = eng.gain_ledger[_LEDGER_KEY[purse]].get(boundary, {})
        return sum(booked.get(k, 0.0) for k in "deo")

    rows = eng.transition_ledger.get(purse, {}).get(boundary, {})
    if not rows:
        return 0.0
    realised = sum(r["gain"] for r in rows.values())
    # What the window did NOT manage to move still has to be paid for.
    return realised + rows[max(rows)]["remaining_gain"]


def measure(state: AppState, plan: Optional[TransitionPlan]) -> Measurement:
    """Project under `plan` and read back what a planner needs."""
    *_rest, solution = project_plan(state, transition_plan=plan)
    eng = Engine(state, gross_schedule=solution.gross, transition_plan=plan)
    eng.run()

    horizon = state.horizon
    boundaries = tuple(
        Boundary(fy=fy, turnover=0.0,
                 gain={p: _requirement(eng, fy, p, plan) for p in PURSES})
        for fy in state.chunk_boundary_years())
    return Measurement(boundaries=boundaries, spare=_spare(state, eng),
                       horizon=horizon)


def _blend(old: Measurement, new: Measurement, alpha: float) -> Measurement:
    """Under-relaxation: step only part of the way toward the new reading."""
    by_fy = {b.fy: b for b in old.boundaries}
    boundaries = tuple(
        Boundary(fy=b.fy, turnover=b.turnover, gain={
            p: (1 - alpha) * by_fy[b.fy].need(p) + alpha * b.need(p)
            if b.fy in by_fy else b.need(p) for p in PURSES})
        for b in new.boundaries)
    spare = {p: {fy: (1 - alpha) * old.spare.get(p, {}).get(fy, v) + alpha * v
                 for fy, v in new.spare.get(p, {}).items()} for p in PURSES}
    return Measurement(boundaries=boundaries, spare=spare,
                       horizon=new.horizon)


def plan_transitions(state: AppState, margin: float = 1.2,
                     max_iterations: int = 12,
                     damping: float = 0.5) -> Convergence:
    """The settled transition plan for `state`, and how it was reached.

    UNDER-RELAXATION, AND WHY IT IS NEEDED

    Taking each measurement at face value makes the iteration oscillate. The
    feedback is real, not a bug: a wider window sells earlier, when gain
    fractions are lower, so the measured requirement FALLS; that builds a
    narrower window, which raises it again. Measured on `s1_2chunk`, the
    requirement went 159.50, 60.48, 142.88, 93.60, 121.01, 106.60 -- a damped
    oscillation whose swing roughly halves each pass, heading for about 110.

    It would settle on its own, but not inside any sensible cap. Stepping only
    `damping` of the way toward each new reading converges it in a few passes
    instead. `damping = 1.0` restores the undamped behaviour.

    `max_iterations` is 12 rather than 5. The cost is one `project_plan` plus
    one run per pass; this is a monthly re-evaluation, not a hot path, and a
    plan that settles is worth more than a plan that stops early.
    """
    if not 0.0 < damping <= 1.0:
        raise ValueError("damping must be in (0, 1]")
    prev: Dict[str, Optional[Measurement]] = {"m": None}
    seen: list = []

    def project(plan: Optional[TransitionPlan]) -> Measurement:
        # Every plan the iteration tries passes through here, which is the
        # only place the whole cycle is visible -- `converge` returns one plan,
        # not the set it considered.
        if plan is not None:
            seen.append(plan)
        got = measure(state, plan)
        if prev["m"] is not None and damping < 1.0:
            got = _blend(prev["m"], got, damping)
        prev["m"] = got
        return got

    result = converge(project, max_iterations=max_iterations,
                      margin=margin, compare=plans_same_shape)
    if result.converged or prev["m"] is None:
        return result

    # A boundary whose need lands almost exactly on one year's capacity
    # chatters between two widths forever -- `s3_4chunk` FY17 alternated M=0
    # and M=1 on every pass, with the other two boundaries already settled.
    # Under-relaxation damps the measurement; it cannot damp a threshold. So
    # floor each boundary at the widest width it reached and rebuild ONCE,
    # which keeps the capacity allocation consistent -- picking windows from
    # different passes and stitching them together would double-spend.
    # `seen[0]` is built from the unplanned baseline, whose requirement is
    # inflated by the tax-covering sell loop and by selling late at high gain
    # fractions -- it is the least informed plan in the set. Flooring against
    # it widened `s3_4chunk` FY9 to M=3 when the informed passes had all
    # settled on M=2. Drop it.
    informed = seen[1:] or seen
    floors = widest_seen(informed + [result.plan])
    settled = build_plan(prev["m"].boundaries, prev["m"].spare,
                         prev["m"].horizon, margin, min_m=floors)
    return Convergence(
        plan=settled, iterations=result.iterations, converged=False,
        oscillated=result.oscillated,
        residuals=result.residuals + (settled.residual_total(),),
        chosen=len(result.residuals))


def main(argv=None) -> None:
    """Report the settled plan for the golden fixtures.

    METHODOLOGY §20.1: predict the snapshot movement before looking at the
    diff. This runs the driver WITHOUT wiring it into the projection path, so
    the numbers below are a prediction that turning it on can then be checked
    against, rather than a rationalisation of whatever the diff turns out to
    be.
    """
    sys.path.insert(0, "tests/golden")
    from golden_projects import PROJECTS

    argv = sys.argv[1:] if argv is None else argv
    targets = [("main", 0, "s0_1chunk"), ("main", 1, "s1_2chunk"),
               ("main", 2, "s2_3chunk"), ("main", 3, "s3_4chunk"),
               ("hist_8yr", 2, "hist_8yr")]
    if len(argv) >= 2:
        targets = [(argv[0], int(argv[1]), f"{argv[0]}[{argv[1]}]")]

    for proj, idx, label in targets:
        state = AppState.from_dict(PROJECTS[proj]["scenarios"][idx])
        result = plan_transitions(state)
        status = ("converged" if result.converged
                  else "OSCILLATED" if result.oscillated else "did not settle")
        print(f"\n{label}: {status} after {result.iterations} pass(es); "
              f"residual {result.plan.residual_total():.1f}")
        for w in result.plan.windows:
            print(f"  boundary FY{w.fy:<3} M={w.m:<2} FY{w.lo}-{w.hi}"
                  f"  residual p {w.residual['personal']:>7.1f}"
                  f"  h {w.residual['huf']:>7.1f}"
                  + ("  CLAMPED" if w.clamped else ""))


if __name__ == "__main__":
    main()
