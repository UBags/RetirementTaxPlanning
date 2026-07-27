# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
tax_allocator.py
================
Split a year's realised gain between two tax entities so that the COMBINED
tax is minimised.

THE PROBLEM
-----------
Personal and HUF each face their own progressive slab, their own s.87A
position and their own equity exemption. A rupee of gain costs a different
amount depending on which purse it comes out of, and that cost changes as
each purse fills up. For a given total, the split decides the tax.

WHY NOT WATER-FILLING
---------------------
The obvious approach -- fill the cheapest bracket in either entity, then the
next, equalising marginal rates -- is correct for CONVEX tax functions, and
progressive slabs on their own are convex. s.87A is not:

    income <= exempt_limit          tax = 0
    income >  exempt_limit          tax = min(slab_tax, income - exempt_limit)

That second line is marginal relief, and it makes the marginal rate rise to
104% (100% plus cess) and then FALL back to the slab rate. On the live
project's bands the sequence is 0% -> 104% -> 15.6%. A falling marginal rate
means the function is not convex, so equalising marginal rates can settle on
a point that is not the minimum, and a greedy fill will never step ACROSS
the relief band even when the far side is cheaper.

WHAT THIS DOES INSTEAD
----------------------
Both entities' tax curves are piecewise linear in the amount allocated. So
is their sum. The minimum of a piecewise-linear function is attained at a
breakpoint or at an endpoint -- always. Enumerate every breakpoint of both
curves, evaluate the total there, take the best. That is exact, needs no
iteration and no convexity, and it agrees with water-filling wherever
water-filling is right.

There are on the order of fifteen breakpoints per entity, so this is cheaper
than the bisection it replaces.

WHAT THIS MODULE DOES NOT DO
----------------------------
* It does not convert cash to gain. A rupee withdrawn realises a gain that
  depends on the cost basis of the units sold, which differs per purse and
  drifts as the plan runs. The caller supplies the conversion.
* It does not solve for net cash in hand. That is an outer fixed point --
  withdrawal -> gain -> tax -> net cash -> withdrawal -- which wraps this.
* It does not decide how long a purse should last. Draining one purse to use
  its cheap bands can exhaust it, and the bands it would have offered in
  later years are then lost. That is a lifetime constraint, applied by the
  caller as a per-purse cap.

Each of those is a separate, testable piece. This one answers exactly one
question: given a total to place this year and a cap on each purse, what
split costs least?

NOTE ON THE IMPORT
------------------
`compute_slab_tax` and friends live in engine.py. They are imported lazily
so the module graph stays acyclic while the engine also calls this module.
Their proper home is a tax-primitives module, which is Phase 3.1's business;
duplicating them here to avoid the lazy import would create a second copy of
the tax rules, which is the failure this codebase keeps undoing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

__all__ = [
    "Allocation",
    "EntityYear",
    "best_split",
    "combined_tax",
]

#: Amounts below this are treated as zero (Rs lakhs -- one rupee is 1e-5).
EPS = 1e-9


@dataclass(frozen=True)
class EntityYear:
    """One tax entity's position in one financial year, before any withdrawal.

    *base_income* is what already occupies the slab before a rupee is drawn
    from the corpus: assessable other income, net of s.16(ia) and s.24(a).
    Use OtherIncome.assessable(), never .taxable() -- see models.py.

    *debt_chunk* / *equity_chunk* / *other_chunk* are the AppState chunks for
    this financial year. Any may be None, in which case that head is untaxed,
    which is what the engine already does.
    """

    entity: str                       # "individual" | "huf"
    base_income: float                # assessable other income
    debt_chunk: object = None         # TaxChunk
    equity_chunk: object = None       # EquityTaxChunk
    other_chunk: object = None        # OtherTaxChunk

    # ── tax ──────────────────────────────────────────────────────────────
    def tax(self, debt_gain: float = 0.0, equity_gain: float = 0.0,
            other_gain: float = 0.0) -> float:
        """Total tax for this entity given the gains realised into it."""
        from engine import (
            compute_ltcg_individual,
            compute_ltcg_other_individual,
            compute_slab_tax,
        )
        t = 0.0
        if self.debt_chunk is not None:
            t += compute_slab_tax(self.base_income + max(0.0, debt_gain),
                                  self.debt_chunk, self.entity)
        if self.equity_chunk is not None:
            t += compute_ltcg_individual(max(0.0, equity_gain),
                                         self.equity_chunk)
        t += compute_ltcg_other_individual(max(0.0, other_gain),
                                           self.other_chunk)
        return t

    # ── where the marginal rate changes ──────────────────────────────────
    def breakpoints(self) -> List[float]:
        """Debt-gain amounts at which this entity's marginal rate changes.

        Returned as gains, i.e. already net of base_income, and never
        negative. The list is what makes the exact search exact: between two
        consecutive breakpoints the tax is linear, so nothing interesting can
        happen in between.

        Included:
          * every slab boundary
          * the s.87A limit, for an individual -- the start of relief
          * the point where relief ends and the ordinary slab resumes, found
            by bisection because it depends on the whole slab structure
          * the equity exemption limit
        """
        pts: List[float] = [0.0]
        dc = self.debt_chunk
        if dc is not None:
            for slab in getattr(dc, "slabs", ()) or ():
                for edge in (slab.lower, slab.upper):
                    if edge < 1e8:                       # skip the open top
                        pts.append(edge - self.base_income)
            if self.entity == "individual":
                limit = float(getattr(dc, "exempt_limit", 0.0) or 0.0)
                pts.append(limit - self.base_income)
                exit_pt = self._relief_exit(limit)
                if exit_pt is not None:
                    pts.append(exit_pt - self.base_income)
        if self.equity_chunk is not None:
            pts.append(float(getattr(self.equity_chunk, "exempt_limit", 0.0) or 0.0))
        return sorted({round(p, 9) for p in pts if p > -EPS})

    def _relief_exit(self, limit: float) -> Optional[float]:
        """Income at which s.87A marginal relief stops binding.

        Relief binds while (income - limit) < slab_tax(income). Both sides
        are increasing and the gap closes once, so a bisection finds it. If
        it never binds, there is nothing to add.
        """
        from engine import compute_slab_tax
        if limit <= 0 or self.debt_chunk is None:
            return None
        lo, hi = limit, limit
        for _ in range(64):                              # bracket upward
            hi = hi * 2 if hi > 0 else 1.0
            if hi - limit >= compute_slab_tax(hi, self.debt_chunk, "individual"):
                break
        else:
            return None
        for _ in range(80):                              # then bisect
            mid = 0.5 * (lo + hi)
            binds = (mid - limit) < compute_slab_tax(
                mid, self.debt_chunk, "individual")
            lo, hi = (mid, hi) if binds else (lo, mid)
        return 0.5 * (lo + hi)


@dataclass(frozen=True)
class Allocation:
    """The chosen split and what it costs."""

    to_first: float
    to_second: float
    tax_first: float
    tax_second: float

    @property
    def total_tax(self) -> float:
        return self.tax_first + self.tax_second

    @property
    def total_allocated(self) -> float:
        return self.to_first + self.to_second


def combined_tax(first: EntityYear, second: EntityYear,
                 to_first: float, to_second: float) -> float:
    """Combined tax when *to_first* and *to_second* are realised as debt gain."""
    return first.tax(debt_gain=to_first) + second.tax(debt_gain=to_second)


def best_split(total: float, first: EntityYear, second: EntityYear,
               cap_first: Optional[float] = None,
               cap_second: Optional[float] = None,
               extra_candidates: Sequence[float] = ()) -> Allocation:
    """Split *total* between two entities so combined tax is least.

    Exact. The combined tax is piecewise linear in the amount given to the
    first entity, so its minimum sits at a breakpoint of either entity's
    curve or at an endpoint of the feasible interval. Every one is evaluated.

    *cap_first* / *cap_second* bound what each purse can supply -- a corpus
    balance, or a sustainability cap that keeps the purse alive to use its
    bands in later years. If the caps cannot cover *total*, as much as
    possible is allocated and the caller can see the shortfall from
    Allocation.total_allocated.

    TIES ARE COMMON and are broken deliberately. Whenever both purses still
    have zero-rate room, every split inside it costs nothing, and an
    arbitrary choice would drain one purse first -- losing the bands it would
    have offered in later years. Ties therefore go to the split PROPORTIONAL
    TO THE CAPS, which in practice are the corpus balances: draw from each in
    proportion to its size and they run dry together, which is exactly the
    lifetime objective. With no caps the tie-break is an even split.
    """
    total = max(0.0, float(total))
    room_first = float("inf") if cap_first is None else max(0.0, float(cap_first))
    room_second = float("inf") if cap_second is None else max(0.0, float(cap_second))

    # Neither purse can be overdrawn to cover the other. When the two caps
    # together fall short, each gives everything it has and the caller sees
    # the gap in Allocation.total_allocated -- a shortfall reported, not a
    # corpus quietly driven negative.
    if room_first + room_second <= total + EPS:
        x = 0.0 if room_first == float("inf") else room_first
        y = 0.0 if room_second == float("inf") else room_second
        return Allocation(x, y, first.tax(debt_gain=x), second.tax(debt_gain=y))

    hi_first = min(total, room_first)
    lo_first = max(0.0, total - room_second)

    # The tie-break target: proportional to the caps, so both purses run dry
    # together. It has to be a candidate in its own right -- it is not a
    # breakpoint of either tax curve, and without it the tie-break can only
    # choose among corners.
    if cap_first is not None and cap_second is not None and (
            room_first + room_second) > EPS:
        preferred = total * room_first / (room_first + room_second)
    else:
        preferred = total / 2.0
    preferred = min(max(preferred, lo_first), hi_first)

    candidates = {lo_first, hi_first, preferred}
    for p in first.breakpoints():
        candidates.add(p)
    for p in second.breakpoints():
        candidates.add(total - p)
    for p in extra_candidates:
        candidates.add(float(p))

    feasible: List[Tuple[float, float, float]] = []
    for x in sorted(candidates):
        if x < lo_first - EPS or x > hi_first + EPS:
            continue
        x = min(max(x, lo_first), hi_first)
        y = max(0.0, total - x)
        feasible.append((x, first.tax(debt_gain=x), second.tax(debt_gain=y)))
    if not feasible:                            # degenerate: nothing feasible
        return Allocation(0.0, 0.0, first.tax(), second.tax())

    cheapest = min(t1 + t2 for _x, t1, t2 in feasible)
    tied = [(x, t1, t2) for x, t1, t2 in feasible if t1 + t2 <= cheapest + EPS]
    x, t1, t2 = min(tied, key=lambda c: abs(c[0] - preferred))
    return Allocation(x, max(0.0, total - x), t1, t2)


def _describe(e: EntityYear) -> str:
    """Human-readable marginal-rate ladder. For diagnostics and tests."""
    pts = e.breakpoints()
    out = []
    for a, b in zip(pts, pts[1:]):
        mid = 0.5 * (a + b)
        step = max(1e-4, (b - a) * 0.01)
        rate = (e.tax(debt_gain=mid + step) - e.tax(debt_gain=mid)) / step
        out.append(f"{a:.2f}-{b:.2f}: {rate * 100:.1f}%")
    return "  ".join(out)
