# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""`free_gain_capacity` -- the per-entity tax-free headroom.

This is the quantity the tax-minimisation design is built on: personal
`H_p = exempt_limit - assessable_other - gain_already`, HUF
`H_h = nil_band - gain_already`. It has no caller yet; it lands dead and
tested, ahead of the harvest and ration work, exactly as 4b-i did.

The load-bearing tests are the two property tests. They tie the function to
`compute_slab_tax` from both sides -- realising exactly the capacity costs
nothing, realising a rupee more costs something -- so the two cannot drift
apart later. A capacity function that disagreed with the tax function would be
worse than none at all: every caller would believe it.

Note on fixtures. The SHIPPED HUF chunks set `exempt_limit` equal to the nil
slab upper (4.0, 4.0, 5.0, 6.0, 7.0, 7.0). A fixture built from those would
pass whether or not the HUF branch reads the rebate threshold, and would prove
nothing -- HANDOVER §9.14. The chunks below deliberately set the two APART.
"""
from __future__ import annotations

import pytest

from swp_planner.core.engine import compute_slab_tax, free_gain_capacity
from swp_planner.core.models import TaxChunk, TaxSlab

_SLABS = [TaxSlab(0, 4, 0.0), TaxSlab(4, 8, 0.05), TaxSlab(8, 12, 0.10),
          TaxSlab(12, 16, 0.15), TaxSlab(16, 20, 0.20), TaxSlab(20, 24, 0.25),
          TaxSlab(24, 1e9, 0.30)]

# exempt_limit 12.0 vs nil slab 4.0 -- deliberately different, so the two
# branches cannot both pass by accident.
CHUNK = TaxChunk(1, 5, 12.0, _SLABS)

EPS = 0.01          # Rs 1,000 in lakh units


def test_personal_capacity_is_the_rebate_threshold_not_the_nil_slab():
    """s.87A makes the individual's free band the rebate threshold."""
    assert free_gain_capacity(CHUNK, "individual", 5.95) == pytest.approx(6.05)


def test_huf_capacity_is_the_nil_slab_not_the_rebate_threshold():
    """The HUF gets NO s.87A rebate, so its free band is the 0% slab.

    If this ever reads `exempt_limit`, the HUF would look like it had 12L of
    free capacity instead of 4L, and every split decision built on it would be
    wrong in the direction that costs money.
    """
    assert free_gain_capacity(CHUNK, "huf", 0.0) == pytest.approx(4.0)
    assert free_gain_capacity(CHUNK, "huf", 0.0) != CHUNK.exempt_limit


def test_gain_already_realised_consumes_the_capacity():
    assert free_gain_capacity(CHUNK, "individual", 5.95, 2.0) == pytest.approx(4.05)
    assert free_gain_capacity(CHUNK, "huf", 0.0, 1.5) == pytest.approx(2.5)


def test_capacity_is_monotone_non_increasing_in_gain_realised():
    prev = float("inf")
    for g in (0.0, 1.0, 2.0, 4.0, 8.0, 20.0):
        cur = free_gain_capacity(CHUNK, "individual", 5.95, g)
        assert cur <= prev
        prev = cur


def test_capacity_never_goes_negative():
    assert free_gain_capacity(CHUNK, "individual", 99.0) == 0.0
    assert free_gain_capacity(CHUNK, "huf", 0.0, 99.0) == 0.0


def test_a_missing_chunk_offers_no_capacity():
    assert free_gain_capacity(None, "individual", 0.0) == 0.0


@pytest.mark.parametrize("entity", ["individual", "huf"])
@pytest.mark.parametrize("other", [0.0, 1.0, 3.362, 5.95])
def test_realising_exactly_the_capacity_does_not_increase_tax(entity, other):
    """The whole point: fill the headroom for free.

    Note the wording. This is NOT "pay zero tax": where assessable other
    income already exceeds the band -- HUF at 5.95 against a 4.0 nil band --
    capacity is zero and tax is already positive. The guarantee is about the
    MARGIN, not the level. Asserting zero here failed exactly that case, which
    is how the distinction was found.
    """
    cap = free_gain_capacity(CHUNK, entity, other)
    base = compute_slab_tax(other, CHUNK, entity)
    filled = compute_slab_tax(other + cap, CHUNK, entity)
    assert filled == pytest.approx(base, abs=1e-9), (
        f"{entity}: realising the stated capacity {cap:.4f} on top of "
        f"{other:.4f} of other income moved tax {base:.6f} -> {filled:.6f}"
    )


@pytest.mark.parametrize("entity", ["individual", "huf"])
@pytest.mark.parametrize("other", [0.0, 1.0, 3.362, 5.95])
def test_one_rupee_past_the_capacity_costs_more(entity, other):
    """The other half. Without it, a capacity of zero would pass the pair."""
    cap = free_gain_capacity(CHUNK, entity, other)
    base = compute_slab_tax(other, CHUNK, entity)
    over = compute_slab_tax(other + cap + EPS, CHUNK, entity)
    assert over > base + 1e-12, (
        f"{entity}: {cap:.4f} is not the true limit -- a further {EPS} was "
        "still free, so the capacity is understated"
    )


def test_capacity_is_zero_when_other_income_already_exceeds_the_band():
    """The case the delta form exists for, pinned explicitly."""
    assert free_gain_capacity(CHUNK, "huf", 5.95) == 0.0
    assert compute_slab_tax(5.95, CHUNK, "huf") > 0.0
