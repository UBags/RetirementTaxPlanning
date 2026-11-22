# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
net_income_plan.py
==================
Find the withdrawal schedule that delivers a required cash-in-hand target in
every financial year of the plan.

WHY AN OUTER LOOP RATHER THAN SURGERY ON THE ENGINE
---------------------------------------------------
The engine runs monthly and is path dependent: this month's withdrawal
changes next month's corpus, the glide path rebalances at year boundaries,
and tax for a year is only known once the year has finished. Solving each
year in place would mean being able to re-run a single financial year, which
the engine cannot do -- state is mutated as it goes. Decomposing it is Phase
3.1's job.

So this wraps the engine whole. It proposes a GROSS withdrawal schedule,
projects it, compares the cash in hand against the targets, and corrects.
Nothing inside Engine.run() changes.

WHY IT CONVERGES
----------------
Cash in hand in year k responds mostly to that year's own withdrawal, and
only weakly to earlier years through the corpus. The Jacobian is close to
lower triangular and strongly diagonal, and the diagonal is

    d(net)/d(gross) = 1 - marginal_rate x gain_fraction

which sits between about 0.7 and 1.0 in practice. A plain correction --
add the shortfall to the requirement -- therefore contracts by roughly
|1 - that|, and two or three passes reach a rupee. There is no need for a
Jacobian or a line search, and adding one would obscure the failures below.

TWO WAYS A YEAR CAN REFUSE TO CONVERGE
--------------------------------------
Both are real, both appear on the live project, and both used to make a
naive iteration run away.

1.  THE TARGET MUST BE PRO-RATED. A plan starting in March has a one-month
    FY1, which needs one month of living costs, not twelve. Feeding it a
    full year's target asks for twelve months of spending out of one month
    of withdrawals, and the requirement climbs without limit. The target is
    scaled by months_in_fy, exactly as A2a scales every other annual figure.

2.  A YEAR MAY NOT BE ABLE TO RESPOND AT ALL. The usual cause is an
    exhausted corpus: there is nothing left to withdraw, so raising the
    requirement changes nothing. This also used to happen in FY1, because
    config.swp_start_month delayed the first withdrawal by three months and
    a March-start FY1 is one month long -- that key has since been removed
    as an artefact of the original design, but the failure mode it revealed
    is real and remains. The correction has no effect, and a solver that
    keeps applying it diverges.

    So the response of each year is measured across successive passes, and a
    year that does not move is FROZEN at its current requirement and
    reported as a shortfall. A plan that cannot pay should say so.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Mapping

__all__ = [
    "PlanSolution",
    "project_plan",
    "prorated_targets",
    "solve_schedule",
]

#: Rs lakhs. Convergence is declared when every year is within this.
TOL = 1e-4

#: Rs lakhs. The tolerance project_plan solves a real plan to.
#:
#: Deliberately looser than TOL. Until the identity changed, every measured
#: d(net)/d(gross) was exactly 1.000 -- the tax term cancelled out of cash in
#: hand -- so the first correction landed exactly on the answer and the error
#: was 0.00e+00 in one pass. TOL was therefore never exercised: it is not a
#: precision the engine was shown to meet, it is one the problem never asked
#: for. Once tax stops cancelling, the engine turns out to have a floor around
#: 0.01 on a 12-25L target, roughly 7e-4 relative, from monthly granularity
#: and lot boundaries.
#:
#: 0.01 lakh is a thousand rupees a year on a retirement plan. Immaterial to
#: the decision; material to a solver told to hit 1e-4.
PLAN_TOL = 1e-2

#: A year whose cash in hand moves by less than this per unit of extra
#: requirement is treated as unable to respond.
_DEAD_RESPONSE = 1e-6

#: Passes of plain unit-gain correction before the secant step engages.
#: Every golden scenario converges in 10 or fewer today (4/7/6/10/8), so
#: raising this above 10 keeps the secant branch unreachable under the
#: current identity and no snapshot can move.
_SECANT_AFTER = 14

#: Below this slope a secant step would be a division by almost nothing.
#: Fall back to the unit step rather than taking a wild jump.
_MIN_SLOPE = 0.02

#: Hard cap on the secant correction, as a multiple of the raw error.
_MAX_STEP_MULT = 8.0


@dataclass
class PlanSolution:
    """The schedule, what it achieves, and where it could not."""

    gross: Dict[int, float]
    net: Dict[int, float]
    targets: Dict[int, float]
    #: FY -> amount by which cash in hand falls short. Only years that could
    #: not respond appear here.
    shortfalls: Dict[int, float] = field(default_factory=dict)
    #: FY -> amount by which cash in hand EXCEEDS the target, any year.
    #: Recorded separately because over-delivery is a different failure from
    #: under-delivery and must never be netted against it: B1d-ii computed
    #: `converged = not shortfalls`, so over-delivery was invisible and the
    #: solver reported success while drifting above target in six to thirteen
    #: years. `converged` here excludes frozen years, so a frozen year that
    #: over-delivers is currently absent from BOTH records. This field closes
    #: that gap; it does not change any number.
    overdeliveries: Dict[int, float] = field(default_factory=dict)
    iterations: int = 0
    converged: bool = False

    @property
    def total_shortfall(self) -> float:
        return sum(self.shortfalls.values())

    @property
    def total_overdelivery(self) -> float:
        return sum(self.overdeliveries.values())

    def worst_year(self) -> int | None:
        if not self.shortfalls:
            return None
        return max(self.shortfalls, key=lambda fy: self.shortfalls[fy])


def prorated_targets(annual: Mapping[int, float], investment_date,
                     years) -> Dict[int, float]:
    """Scale each annual target to the months that financial year spans.

    Only FY1 is ever short, and only when the plan does not start in April.
    """
    from swp_planner.core.pro_rated_utils import months_in_fy
    return {fy: float(annual.get(fy, 0.0))
            * months_in_fy(investment_date, fy) / 12.0
            for fy in years}


def solve_schedule(
    targets: Mapping[int, float],
    project: Callable[[Dict[int, float]], Dict[int, float]],
    initial: Mapping[int, float] | None = None,
    scale: Mapping[int, float] | None = None,
    tol: float = TOL,
    max_iter: int = 60,
) -> PlanSolution:
    """Gross schedule whose projected cash in hand meets *targets*.

    *project* takes a gross schedule and returns cash in hand per year -- in
    practice a full engine run. It is called once per pass, so the cost is a
    handful of projections rather than one per year.

    *scale* converts a shortfall in cash into an adjustment of the ANNUAL
    requirement. The engine spends a requirement at one twelfth per elapsed
    month, so a short FY1 only spends a fraction of it; the correction has to
    be divided by that fraction or the year never catches up. Pass
    12 / months_in_fy(fy). Defaults to 1.

    Years that stop responding are frozen and reported in shortfalls rather
    than chased.
    """
    years: List[int] = sorted(targets)
    gross: Dict[int, float] = {
        fy: float(initial[fy]) if initial and fy in initial else float(targets[fy])
        for fy in years
    }
    step = {fy: float((scale or {}).get(fy, 1.0)) for fy in years}
    frozen: Dict[int, bool] = {fy: False for fy in years}
    prev_gross: Dict[int, float] = {}
    prev_net: Dict[int, float] = {}
    net: Dict[int, float] = {}

    it = 0
    for it in range(1, max_iter + 1):
        net = project(gross)
        errors = {fy: targets[fy] - net.get(fy, 0.0) for fy in years}

        # Freeze any year that did not move when its requirement did.
        for fy in years:
            if frozen[fy] or fy not in prev_gross:
                continue
            dg = gross[fy] - prev_gross[fy]
            dn = net.get(fy, 0.0) - prev_net.get(fy, 0.0)
            if abs(dg) > tol and abs(dn) <= _DEAD_RESPONSE:
                frozen[fy] = True

        live = [fy for fy in years if not frozen[fy]]
        if all(abs(errors[fy]) <= tol for fy in live):
            break

        # Secant correction, but only once the unit step has plainly stalled.
        #
        # The unit step assumes d(net)/d(gross) is about 1, which held while
        # the tax term cancelled out of cash in hand. Where it does not --
        # inside the s.87A marginal-relief band tax rises rupee for rupee, so
        # the slope collapses toward 1 - gain_fraction -- the unit step still
        # converges but at a crawl, and a plan can run out of iterations
        # before it arrives. The secant step divides the error by the measured
        # slope instead, which is scale-free.
        #
        # Gated at _SECANT_AFTER because every golden scenario converges in
        # ten passes or fewer today, so this branch is unreachable until the
        # identity changes underneath it. Measured: 4 / 7 / 6 / 10 / 8.
        slopes: Dict[int, float] = {}
        if it > _SECANT_AFTER:
            for fy in live:
                if fy not in prev_gross:
                    continue
                dg = gross[fy] - prev_gross[fy]
                dn = net.get(fy, 0.0) - prev_net.get(fy, 0.0)
                if abs(dg) > tol:
                    slopes[fy] = dn / dg

        prev_gross = dict(gross)
        prev_net = dict(net)
        for fy in live:
            if abs(errors[fy]) <= tol:
                continue
            adj = errors[fy]
            s = slopes.get(fy)
            if s is not None and s >= _MIN_SLOPE:
                # Cap the correction: a small slope would otherwise produce a
                # step large enough to overshoot into a different tax band and
                # oscillate.
                lim = abs(errors[fy]) * _MAX_STEP_MULT
                adj = max(-lim, min(lim, errors[fy] / s))
            gross[fy] = max(0.0, gross[fy] + adj * step[fy])

    shortfalls = {fy: targets[fy] - net.get(fy, 0.0)
                  for fy in years
                  if frozen[fy] and targets[fy] - net.get(fy, 0.0) > tol}
    overdeliveries = {fy: net.get(fy, 0.0) - targets[fy]
                      for fy in years
                      if net.get(fy, 0.0) - targets[fy] > tol}
    converged = all(
        abs(targets[fy] - net.get(fy, 0.0)) <= tol
        for fy in years if not frozen[fy]
    )
    return PlanSolution(gross=gross, net=net, targets=dict(targets),
                        shortfalls=shortfalls,
                        overdeliveries=overdeliveries, iterations=it,
                        converged=converged)


def project_plan(state, auto_transition: bool = True, **engine_kwargs):
    """Solve the schedule, then project the plan that delivers it.

    THE Phase B entry point. Returns the engine's usual four sequences plus
    the PlanSolution, so a caller can see the schedule that was chosen and
    any year that could not be met.

    state.annual_requirements is read as a NET target -- cash the plan must
    leave in hand after tax and after the transfer into the HUF, which is
    what the engine reports as net_cash_total. Targets are pro-rated for a
    short FY1 exactly as every other annual figure is.

    Cost is one engine run per pass, and it converges in three or four on the
    live project. Anything calling this in a loop should hold the result.
    """
    import copy

    from swp_planner.core.engine import Engine
    from swp_planner.core.pro_rated_utils import months_in_fy

    # Phase 07. A chunk boundary used to move the whole portfolio in one FY,
    # and on the golden corpus that single act was very nearly the only source
    # of lifetime tax. Unless a plan is supplied or auto_transition is off,
    # settle one first and project against it.
    #
    # `plan_transitions` calls back into this function, so the inner calls
    # must have auto_transition off or the recursion never bottoms out. The
    # plan is cached on (identity, content fingerprint), so the five golden
    # scenarios each compute theirs once rather than once per caller.
    if auto_transition and "transition_plan" not in engine_kwargs:
        from swp_planner.core.transition_driver import plan_transitions
        if state.chunk_boundary_years():
            engine_kwargs["transition_plan"] = plan_transitions(state).plan

    years = list(state.fy_range())
    targets = prorated_targets(
        {fy: state.get_requirement(fy) for fy in years},
        state.investment_date, years)
    # A requirement is spent at one twelfth per elapsed month, so a short FY1
    # only spends part of it; the correction must be divided by that fraction
    # or the year never catches up.
    scale = {fy: 12.0 / months_in_fy(state.investment_date, fy) for fy in years}

    def project(gross):
        probe = copy.copy(state)
        _, yearly, _, _ = Engine(probe, gross_schedule=gross,
                                 **engine_kwargs).run()
        return {y.year: y.net_cash_total for y in yearly}

    solution = solve_schedule(targets, project, scale=scale, tol=PLAN_TOL)
    p_monthly, p_yearly, h_monthly, h_yearly = Engine(
        state, gross_schedule=solution.gross, **engine_kwargs).run()
    return p_monthly, p_yearly, h_monthly, h_yearly, solution
