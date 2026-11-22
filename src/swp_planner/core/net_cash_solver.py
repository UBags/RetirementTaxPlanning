# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
net_cash_solver.py
==================
Find the smallest gross withdrawal that leaves a required amount of cash in
hand.

THE CHANGE THIS SERVES
----------------------
`annual_requirements` means "withdraw this much from the corpus", and other
income stacks on top of it. Phase B makes it mean "end up with this much in
hand":

    cash in hand = withdrawals from both purses
                 + all other income
                 - taxes actually paid
                 - the transfer into the HUF for fresh investment

which is what the engine already reports as net_cash_total. The last term is
subtracted because that money is invested, not spent; adding it back would
make the solver withdraw more to replace money that had only changed pocket.

WHY IT IS A FIXED POINT
-----------------------
Withdrawing realises a gain, the gain attracts tax, the tax reduces the cash
in hand, and the shortfall has to be withdrawn -- which realises more gain.
The target is not reachable by a formula; it has to be solved.

WHY BISECTION IS NOT AUTOMATICALLY SAFE
---------------------------------------
Bisection needs net cash to rise with the gross withdrawal. It usually does:

    d(net)/d(gross) = 1 - marginal_tax_rate x gain_fraction

With an ordinary top slab the marginal rate is about 0.31, so even a
withdrawal that is entirely gain leaves the derivative around 0.69 and the
function climbs.

Inside the s.87A marginal relief band the marginal rate is 1.04 -- every
extra rupee of income costs more than a rupee of tax. There the derivative
turns negative once

    gain_fraction > 1 / 1.04 = 96.2%

and MORE withdrawal yields LESS cash in hand. On the live project the gain
fraction reaches about 82% by FY25, so this does not arise; a longer plan at
higher returns can pass 96%, and a solver that quietly assumed monotonicity
would return a wrong answer rather than fail.

So the interval is checked for monotonicity first. When it holds -- almost
always -- bisection runs. When it does not, the solver scans for the SMALLEST
gross that meets the target, which is the right answer in either case, and
says so in the result.

WHAT THIS MODULE DOES NOT DO
----------------------------
It does not know about corpora, lots or tax. The caller supplies one
callable, gross -> cash in hand, and the caps. That keeps the fixed point
testable against hand-worked arithmetic instead of against the engine.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

__all__ = [
    "SolveOutcome",
    "constant_fraction_net",
    "solve_gross_for_net",
]

#: Rs lakhs. One rupee is 1e-5, so this is well inside a paisa.
TOL = 1e-7

#: Fallback grid when the caller supplies no breakpoints. A grid is a
#: HEURISTIC and can miss a narrow inversion: the s.87A relief band is only
#: about 0.83 wide, so a 64-point grid over a 200-wide interval steps clean
#: over it. Pass *breakpoints* and the check becomes exact instead.
_PROBES = 64


@dataclass(frozen=True)
class SolveOutcome:
    """The smallest gross withdrawal that meets the target, and what happened."""

    gross: float
    net: float
    target: float
    #: target - net, positive only when the corpus could not reach the target.
    shortfall: float
    #: True when the cap bound rather than the target.
    exhausted: bool
    #: False when cash in hand was found to fall somewhere as the gross rose.
    #: The answer is still the smallest gross meeting the target; the flag is
    #: there so a caller can report the plan is in the s.87A relief band.
    monotone: bool
    iterations: int

    @property
    def met(self) -> bool:
        return self.shortfall <= TOL


def solve_gross_for_net(
    target: float,
    net_of_gross: Callable[[float], float],
    max_gross: float,
    tol: float = TOL,
    max_iter: int = 200,
    breakpoints: "Sequence[float] | None" = None,
) -> SolveOutcome:
    """Smallest gross withdrawal with net_of_gross(gross) >= target.

    *net_of_gross* must include everything: other income, both purses' tax,
    and the HUF transfer. It is evaluated O(60) times, so it may be as
    expensive as a full year of engine arithmetic but not a full projection.

    *max_gross* is what the corpora can actually supply. If the target cannot
    be reached even there, the result carries the shortfall rather than
    pretending: a plan that runs out should say so, not quietly withdraw
    money that is not there.

    *breakpoints* are gross amounts at which the slope of net_of_gross
    changes -- slab edges, the s.87A limit and where its relief stops
    binding, the equity exemption. Supplying them makes the monotonicity
    check EXACT: between two consecutive breakpoints the function is linear,
    so it cannot turn around there, and checking every breakpoint therefore
    catches every inversion. Without them the solver falls back to a uniform
    grid, which can miss a band narrower than its spacing -- and the s.87A
    band is narrow. tax_allocator.EntityYear.breakpoints() produces them.
    """
    target = float(target)
    max_gross = max(0.0, float(max_gross))

    net_at_zero = net_of_gross(0.0)
    if net_at_zero >= target - tol:
        # Other income alone covers it. Withdrawing anything would be waste.
        return SolveOutcome(0.0, net_at_zero, target, 0.0, False, True, 1)

    net_at_max = net_of_gross(max_gross)
    if net_at_max < target - tol:
        return SolveOutcome(max_gross, net_at_max, target,
                            target - net_at_max, True, True, 2)

    # ── is cash in hand actually rising across the interval? ──────────────
    grid = {max_gross * i / _PROBES for i in range(_PROBES + 1)}
    for b in (breakpoints or ()):
        b = float(b)
        if 0.0 <= b <= max_gross:
            grid.add(b)
            # Straddle each kink: the slope changes AT it, so a sample either
            # side is what reveals the direction on both.
            grid.add(min(max_gross, b + 1e-4))
            grid.add(max(0.0, b - 1e-4))
    probes = sorted(grid)
    values = [net_of_gross(g) for g in probes]
    monotone = all(b >= a - tol for a, b in zip(values, values[1:]))
    used = len(probes) + 2

    if not monotone:
        # The s.87A relief band has inverted the function somewhere. Take the
        # first probe interval that reaches the target and refine inside it;
        # scanning from the left is what makes the answer the SMALLEST gross
        # rather than merely a gross that works.
        lo, hi = 0.0, max_gross
        for (g0, v0), (g1, v1) in zip(zip(probes, values),
                                      zip(probes[1:], values[1:])):
            if v1 >= target - tol:
                lo, hi = g0, g1
                break
        gross, net, it = _bisect_first_crossing(
            target, net_of_gross, lo, hi, tol, max_iter)
        return SolveOutcome(gross, net, target, max(0.0, target - net),
                            False, False, used + it)

    gross, net, it = _bisect_first_crossing(
        target, net_of_gross, 0.0, max_gross, tol, max_iter)
    return SolveOutcome(gross, net, target, max(0.0, target - net),
                        False, True, used + it)


def _bisect_first_crossing(target, fn, lo, hi, tol, max_iter):
    """Smallest g in [lo, hi] with fn(g) >= target, assuming fn(hi) >= target."""
    it = 0
    for _ in range(max_iter):
        it += 1
        mid = 0.5 * (lo + hi)
        if fn(mid) >= target - tol:
            hi = mid
        else:
            lo = mid
        if hi - lo <= tol:
            break
    return hi, fn(hi), it


def constant_fraction_net(
    other_income: float,
    gain_fraction: float,
    tax_of_income: Callable[[float], float],
    base_income: float = 0.0,
    transfer: float = 0.0,
) -> Callable[[float], float]:
    """A simple gross -> cash-in-hand curve, for tests and for reasoning.

    Every rupee withdrawn realises *gain_fraction* of gain; that gain is
    stacked on *base_income* and taxed by *tax_of_income*. The engine's real
    curve is piecewise linear rather than a constant fraction, because lots
    are sold in cost order -- but the shape, and every property this solver
    relies on, is the same.
    """
    def net(gross: float) -> float:
        gain = max(0.0, gross) * gain_fraction
        tax = tax_of_income(base_income + gain)
        return max(0.0, gross) + other_income - tax - transfer
    return net
