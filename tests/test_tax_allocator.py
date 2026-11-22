# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
Splitting a year's gain between Personal and HUF to minimise combined tax.

The claim under test is EXACTNESS, so the central test is an independent
oracle: a fine brute-force grid over every feasible split. If the grid ever
finds a cheaper answer than best_split(), the method is wrong -- not merely
imprecise.

The reason exactness needs proving here rather than being assumed: s.87A
marginal relief makes the tax function NON-CONVEX. On the live project's
bands the personal marginal rate runs 0% -> 104% -> 15.6%. Water-filling,
which is what "fill the lowest bracket in both, then the next" means, is
optimal only for convex costs, so it can settle inside the 104% band or
refuse to step across it when the far side is cheaper.
"""
from __future__ import annotations

import pytest

from swp_planner.core.models import EquityTaxChunk, TaxChunk, TaxSlab
from swp_planner.core.tax_allocator import (
    EntityYear,
    best_cash_split,
    best_split,
    combined_tax,
)

# The live project's bands, and its s.87A rebate limit.
SLABS = [TaxSlab(0, 4, 0.0), TaxSlab(4, 8, 0.05), TaxSlab(8, 12, 0.10),
         TaxSlab(12, 16, 0.15), TaxSlab(16, 20, 0.20), TaxSlab(20, 24, 0.25),
         TaxSlab(24, 1e9, 0.30)]
LIMIT = 12.0


def _chunk(limit=LIMIT):
    return TaxChunk(1, 30, limit, [TaxSlab(s.lower, s.upper, s.rate) for s in SLABS])


def _ind(base=0.0, limit=LIMIT):
    return EntityYear("individual", base, _chunk(limit), None, None)


def _huf(base=0.0):
    return EntityYear("huf", base, _chunk(), None, None)


def _brute(total, a, b, cap_a=None, cap_b=None, steps=2000):
    """Independent oracle: scan every feasible split on a fine grid."""
    hi = total if cap_a is None else min(total, cap_a)
    lo = 0.0 if cap_b is None else max(0.0, total - cap_b)
    if lo > hi:
        return None
    best = None
    for i in range(steps + 1):
        x = lo + (hi - lo) * i / steps
        t = combined_tax(a, b, x, total - x)
        if best is None or t < best[1]:
            best = (x, t)
    return best


# ── exactness ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("total", [0.5, 2, 4, 6, 8, 8.6, 9, 10, 12, 14,
                                   16, 20, 25, 30, 40, 60])
@pytest.mark.parametrize("base", [0.0, 3.362, 8.0, 11.9])
def test_never_beaten_by_brute_force(total, base):
    a, b = _ind(base), _huf()
    got = best_split(total, a, b)
    oracle = _brute(total, a, b)
    assert got.total_tax <= oracle[1] + 1e-6, (
        f"grid found {oracle[1]:.6f} at x={oracle[0]:.4f}; "
        f"best_split returned {got.total_tax:.6f} at x={got.to_first:.4f}")


@pytest.mark.parametrize("total,cap_a,cap_b", [
    (20, 5, 100), (20, 100, 5), (20, 8, 8), (30, 12, 25), (10, 0, 100),
])
def test_never_beaten_by_brute_force_with_caps(total, cap_a, cap_b):
    a, b = _ind(3.362), _huf()
    got = best_split(total, a, b, cap_first=cap_a, cap_second=cap_b)
    oracle = _brute(total, a, b, cap_a, cap_b)
    if oracle is None:                      # caps cannot meet the total
        assert got.total_allocated == pytest.approx(min(total, cap_a + cap_b))
        return
    assert got.total_tax <= oracle[1] + 1e-6


# ── the s.87A trap ───────────────────────────────────────────────────────────

def test_the_relief_band_is_a_breakpoint():
    """Entering it costs 104% per rupee; the search must be able to stop there."""
    a = _ind(3.362)
    pts = a.breakpoints()
    entry = LIMIT - 3.362
    assert any(abs(p - entry) < 1e-6 for p in pts), (
        f"the s.87A limit is not a breakpoint: {pts}")
    assert any(p > entry + 1e-6 for p in pts), "relief exit is not a breakpoint"


def test_the_optimum_parks_at_the_edge_of_the_relief_band():
    """With room in the other purse, personal should stop exactly at the limit.

    Not one rupee past it -- that rupee costs 1.04.
    """
    a, b = _ind(3.362), _huf()
    alloc = best_split(20.0, a, b)
    assert alloc.to_first == pytest.approx(LIMIT - 3.362, abs=1e-6)


def test_stepping_into_relief_is_never_chosen_when_avoidable():
    a, b = _ind(3.362), _huf()
    entry = LIMIT - 3.362
    for total in (10, 12, 14, 16, 18, 20):
        x = best_split(total, a, b).to_first
        assert not (entry + 1e-6 < x < entry + 0.7), (
            f"total={total} landed inside the relief band at x={x:.4f}")


# ── the HUF is a different taxpayer ──────────────────────────────────────────

def test_an_huf_gets_no_87a_rebate():
    """compute_slab_tax gates the rebate on entity == 'individual'."""
    assert _huf().tax(debt_gain=11.0) > 0.0
    assert _ind().tax(debt_gain=11.0) == pytest.approx(0.0)


def test_the_cheaper_purse_is_used_first():
    """Personal already holds other income; the HUF's bands are free."""
    a, b = _ind(11.5), _huf()
    alloc = best_split(4.0, a, b)
    assert alloc.to_second > alloc.to_first


# ── caps, shortfall, degenerate inputs ───────────────────────────────────────

def test_caps_are_respected():
    alloc = best_split(20.0, _ind(), _huf(), cap_first=6.0, cap_second=9.0)
    assert alloc.to_first <= 6.0 + 1e-9
    assert alloc.to_second <= 9.0 + 1e-9


def test_a_shortfall_is_visible_rather_than_hidden():
    """Caps below the total must under-allocate, not silently overdraw."""
    alloc = best_split(20.0, _ind(), _huf(), cap_first=6.0, cap_second=9.0)
    assert alloc.total_allocated == pytest.approx(15.0, abs=1e-6)
    assert alloc.total_allocated < 20.0


def test_zero_and_negative_totals_are_safe():
    for t in (0.0, -5.0):
        alloc = best_split(t, _ind(), _huf())
        assert alloc.total_allocated == pytest.approx(0.0)
        assert alloc.total_tax == pytest.approx(0.0)


# ── the tie-break serves corpus longevity ────────────────────────────────────

def test_ties_are_split_in_proportion_to_the_caps():
    """Every split inside the zero-rate room costs nothing.

    An arbitrary choice would drain one purse first and lose the bands it
    would have offered later, so ties follow the corpus sizes: both run dry
    together.
    """
    alloc = best_split(2.0, _ind(), _huf(), cap_first=90.0, cap_second=30.0)
    assert alloc.total_tax == pytest.approx(0.0)
    assert alloc.to_first == pytest.approx(1.5, abs=1e-6)
    assert alloc.to_second == pytest.approx(0.5, abs=1e-6)


def test_identical_entities_split_evenly_when_untaxed():
    a, b = _huf(), _huf()
    alloc = best_split(6.0, a, b)
    assert alloc.to_first == pytest.approx(alloc.to_second, abs=1e-6)


# ── sanity ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("base", [0.0, 3.362])
def test_more_gain_never_costs_less_tax(base):
    a, b = _ind(base), _huf()
    prev = -1.0
    for total in [t / 2 for t in range(0, 121)]:
        t = best_split(total, a, b).total_tax
        assert t >= prev - 1e-9, f"tax fell at total={total}"
        prev = t


def test_the_equity_exemption_is_a_breakpoint():
    a = EntityYear("individual", 0.0, _chunk(), EquityTaxChunk(1, 30, 0.125, 1.25), None)
    assert any(abs(p - 1.25) < 1e-9 for p in a.breakpoints())


# ── splitting CASH rather than gain ──────────────────────────────────────────

def _brute_cash(total, a, b, ga, gb, cap_a=None, cap_b=None, steps=2000):
    hi = total if cap_a is None else min(total, cap_a)
    lo = 0.0 if cap_b is None else max(0.0, total - cap_b)
    if lo > hi:
        return None
    best = None
    for i in range(steps + 1):
        x = lo + (hi - lo) * i / steps
        t = a.tax(debt_gain=x * ga) + b.tax(debt_gain=(total - x) * gb)
        if best is None or t < best[1]:
            best = (x, t)
    return best


@pytest.mark.parametrize("total", [2, 8, 15, 20, 30, 45, 60, 90])
@pytest.mark.parametrize("ga,gb", [(0.6, 0.3), (0.3, 0.6), (0.85, 0.85),
                                   (1.0, 0.2), (0.5, 0.5)])
def test_cash_split_never_beaten_by_brute_force(total, ga, gb):
    """The decision variable is cash; gain is not conserved across the split.

    A rupee drawn from one purse realises a different amount of gain than a
    rupee from the other, so moving money between them changes how much gain
    exists at all. The method still holds -- each tax is a linear scaling of
    a piecewise-linear function -- but it has to be proved, not assumed.
    """
    a, b = _ind(3.362), _huf()
    got = best_cash_split(total, a, b, ga, gb)
    oracle = _brute_cash(total, a, b, ga, gb)
    assert got.total_tax <= oracle[1] + 1e-6, (
        f"grid found {oracle[1]:.6f} at x={oracle[0]:.4f}; "
        f"got {got.total_tax:.6f} at x={got.to_first:.4f}")


@pytest.mark.parametrize("total,cap_a,cap_b", [
    (30, 10, 100), (30, 100, 10), (30, 12, 12), (50, 20, 20),
])
def test_cash_split_respects_caps_and_reports_shortfall(total, cap_a, cap_b):
    a, b = _ind(3.362), _huf()
    got = best_cash_split(total, a, b, 0.6, 0.3, cap_first=cap_a, cap_second=cap_b)
    assert got.to_first <= cap_a + 1e-9
    assert got.to_second <= cap_b + 1e-9
    assert got.total_allocated == pytest.approx(min(total, cap_a + cap_b), abs=1e-6)


def test_a_purse_with_no_embedded_gain_is_free():
    """All principal, no gain: drawing from it costs nothing whatever.

    It also has no breakpoints, since its tax never changes -- so the code
    must handle a zero fraction rather than divide by it.
    """
    a, b = _ind(3.362), _huf()
    got = best_cash_split(40.0, a, b, gain_first=0.6, gain_second=0.0)
    assert got.tax_second == pytest.approx(0.0)
    assert got.total_tax <= _brute_cash(40.0, a, b, 0.6, 0.0)[1] + 1e-6


def test_the_relief_edge_moves_into_cash_space():
    """A kink at gain g sits at cash g / gain_fraction."""
    a, b = _ind(3.362), _huf()
    got = best_cash_split(45.0, a, b, gain_first=0.6, gain_second=0.3)
    assert got.to_first == pytest.approx((12.0 - 3.362) / 0.6, abs=1e-4)


def test_equal_entities_and_equal_fractions_split_evenly_when_untaxed():
    a, b = _huf(), _huf()
    got = best_cash_split(6.0, a, b, 0.5, 0.5)
    assert got.to_first == pytest.approx(got.to_second, abs=1e-6)


def test_cash_split_prefers_the_cheaper_gain_fraction_when_tax_is_equal():
    """Less gain realised per rupee means less tax for the same cash."""
    a, b = _huf(), _huf()
    got = best_cash_split(40.0, a, b, gain_first=0.9, gain_second=0.1)
    assert got.to_second > got.to_first
