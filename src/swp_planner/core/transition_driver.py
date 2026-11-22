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

import hashlib
import json
import sys
from typing import Dict, Optional

from swp_planner.config.configuration import config
from swp_planner.core.engine import Engine, free_gain_capacity
from swp_planner.core.models import AppState
from swp_planner.core.net_income_plan import project_plan
from swp_planner.core.transition_plan import (
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

__all__ = ["measure", "plan_transitions", "clear_plan_cache"]

# Settled plans, keyed by (identity, content fingerprint).
_PLAN_CACHE: Dict[tuple, Convergence] = {}


def _cache_key(state: AppState, margin: float, damping: float,
               max_iterations: int) -> tuple:
    """Namespace by user; guarantee correctness by content.

    `user_name` does not exist on `AppState` yet, so this reads it defensively
    and will start namespacing the moment it does. Until then every state
    shares the identity `None`, which is exactly why the identity cannot be
    the whole key: the five golden fixtures carry no identity at all, and
    keying on it alone would hand `s3_4chunk`'s plan to `s0_1chunk`.

    The fingerprint matters beyond tests. This tool is re-run monthly against
    a portfolio that has moved -- new NAVs, a changed requirement, an edited
    chunk. A cache keyed on the user alone would return January's plan in
    February and report it as settled. The gates go in too: `huf_ration_k` and
    `folio_cohort_threshold` change the run, so they change the plan.
    """
    ident = (getattr(state, "user_name", None)
             or getattr(state, "user_id", None))
    payload = json.dumps(state.to_dict(), sort_keys=True, default=str)
    gates = (config.get("huf_ration_k", 0.0),
             config.get("folio_cohort_threshold", 0.0))
    # The search parameters belong in the key too. They change the answer, so
    # a key without them hands a damped plan back to an undamped caller --
    # caught by `test_undamped_iteration_does_not_settle` the moment the cache
    # landed.
    return (ident,
            hashlib.sha256((payload + repr(gates)).encode("utf-8")).hexdigest()[:16],
            round(margin, 9), round(damping, 9), int(max_iterations))


def clear_plan_cache() -> None:
    """Drop every cached plan. For tests and for an explicit recompute."""
    _PLAN_CACHE.clear()

_LEDGER_KEY = {"personal": "rebalance_personal", "huf": "rebalance_huf"}
_ENTITY = {"personal": "individual", "huf": "huf"}


# One paisa, in lakh. A plan is a DECISION -- which years a boundary may spend
# in -- and a decision must not turn on the sixteenth significant figure.
#
# The engine carries ULP-level noise in its gain ledger: measured across two
# PYTHONHASHSEED values, spare capacity agreed to twelve decimals in all sixty
# year-purse cells and still differed in the last bits. Unquantised, the fixed
# point AMPLIFIES that -- a 1e-16 wobble moves an instalment into a different
# year, and the projection then differs by 1e-9, which is exactly the golden
# snapshot tolerance. Quantising the plan's inputs stops the amplification at
# source and leaves the engine's own noise where it was: at the last bit,
# invisible to any comparison anyone makes.
_QUANTUM = 1e-6


def _q(value: float) -> float:
    """Round a measurement to the quantum the plan is allowed to see."""
    return round(value / _QUANTUM) * _QUANTUM


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
    # auto_transition off: this IS the thing that builds the plan.
    *_rest, solution = project_plan(state, auto_transition=False,
                                    transition_plan=plan)
    eng = Engine(state, gross_schedule=solution.gross, transition_plan=plan)
    eng.run()

    horizon = state.horizon
    boundaries = tuple(
        Boundary(fy=fy, turnover=0.0,
                 gain={p: _q(_requirement(eng, fy, p, plan)) for p in PURSES})
        for fy in state.chunk_boundary_years())
    spare = {p: {fy: _q(v) for fy, v in cap.items()}
             for p, cap in _spare(state, eng).items()}
    return Measurement(boundaries=boundaries, spare=spare, horizon=horizon)


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
                     damping: float = 0.5,
                     use_cache: bool = True) -> Convergence:
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

    key = (_cache_key(state, margin, damping, max_iterations)
           if use_cache else None)
    if key is not None and key in _PLAN_CACHE:
        return _PLAN_CACHE[key]
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

    def _remember(res: Convergence) -> Convergence:
        if key is not None:
            _PLAN_CACHE[key] = res
        return res

    result = converge(project, max_iterations=max_iterations,
                      margin=margin, compare=plans_same_shape)
    if result.converged or prev["m"] is None:
        return _remember(result)

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
    return _remember(Convergence(
        plan=settled, iterations=result.iterations, converged=False,
        oscillated=result.oscillated,
        residuals=result.residuals + (settled.residual_total(),),
        chosen=len(result.residuals)))


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
