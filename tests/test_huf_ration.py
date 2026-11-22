# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""The HUF ration -- gated off, plus the folio-ordering helper it relies on.

HANDOVER §4.2: a greedy per-year split drains the HUF as fast as it looks
locally cheap. It empties around FY25-29 and the personal purse then carries
everything alone at an 85-88% gain fraction, which is where every rupee of
late-plan tax comes from. Capping the draw at `k * value / years_remaining`
took measured lifetime tax from 16.47 to 0.29 -- **98% of the whole
reduction**. The vintage folios are the other 2%.

GATED at `huf_ration_k = 0.0`, so the fixed `huf_withdrawal_chunks` schedule
still applies and no snapshot can move. `test_the_gate_is_off_by_default` and
`test_at_k_zero_the_fixed_schedule_is_unchanged` are what make that claim
checkable rather than asserted.

Also pins `_cash_consumption_order` directly. It was exercised only through
`cash_within_gain_budget` and the review graph flagged it untested -- the
folio ordering is the part that decides which basis a cash draw actually
reaches, so it earns its own tests.
"""
from __future__ import annotations

import types

import pytest

from swp_planner.config import configuration
from swp_planner.core.engine import (
    Engine,
    FIFOBucket,
    FolioSet,
    _cash_consumption_order,
)
from swp_planner.core.models import TaxChunk, TaxSlab

NAV = 2.0
_SLABS = [TaxSlab(0, 4, 0.0), TaxSlab(4, 8, 0.05), TaxSlab(8, 1e9, 0.30)]
CHUNK = TaxChunk(1, 30, 4.0, _SLABS)


# ── _cash_consumption_order ──────────────────────────────────────────────
def test_a_plain_bucket_is_consumed_in_fifo_order():
    bk = FIFOBucket()
    bk.invest(10.0, 1.0, 0)
    bk.invest(10.0, 1.8, 12)
    assert [l.purchase_nav for l in _cash_consumption_order(bk, NAV)] == [1.0, 1.8]


def test_folios_are_ordered_cheapest_first():
    """A cash draw takes the LOWEST front-lot gain fraction first."""
    fs = FolioSet(threshold=0.0, max_cohorts=5)
    fs.invest(100.0, 1.0, 0)            # F1, 50% gain at NAV 2
    fs.open_folio("F2")
    fs.invest(18.0, 1.8, 12, folio="F2")  # F2, 10% gain -- cheaper
    order = [l.purchase_nav for l in _cash_consumption_order(fs, NAV)]
    assert order[0] == 1.8, "the cheaper folio must be consumed first"
    assert order == [1.8, 1.0]


def test_empty_folios_are_skipped():
    """An empty folio has an infinite front-lot fraction; it must not sort first
    NOR contribute lots."""
    fs = FolioSet(threshold=0.0, max_cohorts=5)
    fs.invest(100.0, 1.0, 0)
    fs.open_folio("F2")                  # opened, never funded
    assert [l.purchase_nav for l in _cash_consumption_order(fs, NAV)] == [1.0]


def test_three_folios_sort_by_front_lot_gain_fraction():
    fs = FolioSet(threshold=0.0, max_cohorts=5)
    fs.invest(100.0, 1.0, 0)
    for fid, nav in (("F2", 1.5), ("F3", 1.9)):
        fs.open_folio(fid)
        fs.invest(10.0, nav, 12, folio=fid)
    assert [l.purchase_nav for l in _cash_consumption_order(fs, NAV)] == [1.9, 1.5, 1.0]


# ── the ration ───────────────────────────────────────────────────────────
def _engine(k, chunks, override=None):
    zero = types.SimpleNamespace(
        assessable=lambda *a, **kw: 0.0,
        taxable=lambda *a, **kw: 0.0,
        non_taxable=lambda *a, **kw: 0.0)
    eng = Engine.__new__(Engine)
    eng.state = types.SimpleNamespace(
        huf_withdrawal_chunks=chunks,
        huf_annual_requirements=override or {},
        huf_income=zero,
        personal_income=zero,
        # Large by default so the demand ceiling does not bind; the tests that
        # exercise it set it explicitly via _engine_with_demand.
        get_requirement=lambda fy: 1e6,
        horizon=30,
        get_debt_tax_chunk=lambda fy, entity: CHUNK,
    )
    eng.months_in_fy = lambda fy: 12
    return eng


def _buckets():
    bk = FIFOBucket()
    bk.invest(100.0, 1.0, 0)             # 100 units, 50% gain at NAV 2 -> 200
    return {"F": bk}, {"F": NAV}


def test_the_gate_is_off_by_default():
    """If this fails, patcher 04 was not inert and every snapshot moved."""
    assert float(configuration.config.get("huf_ration_k", 0.0)) == 0.0


def test_at_k_zero_the_fixed_schedule_is_unchanged(monkeypatch):
    chunks = [types.SimpleNamespace(year_from=1, year_to=30, annual_withdrawal=3.5)]
    eng = _engine(0.0, chunks)
    b, n = _buckets()
    assert eng._huf_annual_wd(6, b, n, 30) == 3.5


def _set_k(monkeypatch, k):
    orig = configuration.config.get
    monkeypatch.setattr(
        configuration.config, "get",
        lambda key, default=None: k if key == "huf_ration_k" else orig(key, default))


def test_the_ration_binds_when_the_band_is_ample(monkeypatch):
    """value 200, 25 years left, k=1.2 -> 9.6. Band cap is 4/0.5 = 8 ... so the
    BAND binds here, not the ration. Use a longer horizon to make the ration
    the smaller of the two."""
    _set_k(monkeypatch, 1.2)
    eng = _engine(1.2, [])
    b, n = _buckets()
    # 30 years left: 1.2 * 200 / 30 = 8.0 exactly, band cap is 8.0 -> tie
    # 40 years left: 1.2 * 200 / 40 = 6.0 < 8.0 -> the ration binds
    eng.state.horizon = 40
    assert eng._huf_annual_wd(1, b, n, 40) == pytest.approx(6.0)


def test_the_band_cap_binds_when_the_ration_is_large(monkeypatch):
    """Late in the plan `k * value / years_remaining` explodes; the nil band is
    what must stop it. 4.0 of band at a 50% gain fraction buys 8.0 of cash."""
    _set_k(monkeypatch, 1.2)
    eng = _engine(1.2, [])
    b, n = _buckets()
    # 1 year left: ration = 1.2 * 200 / 1 = 240, far above the band cap
    assert eng._huf_annual_wd(30, b, n, 30) == pytest.approx(8.0)


def test_the_cap_is_measured_across_the_spill_not_from_the_front_lot(monkeypatch):
    """The bug this whole pair of primitives exists to prevent."""
    _set_k(monkeypatch, 1.2)
    eng = _engine(1.2, [])
    fs = FolioSet(threshold=0.0, max_cohorts=5)
    fs.invest(100.0, 1.0, 0)
    fs.open_folio("F2")
    fs.invest(18.0, 1.8, 12, folio="F2")
    got = eng._huf_annual_wd(30, {"F": fs}, {"F": NAV}, 30)
    # Correct: F2 gives 20.0 of cash for 2.0 of gain, then 2.0 of remaining
    # budget buys 4.0 from F1 at a 50% fraction. 24.0.
    naive = 4.0 / 0.10                   # band / cheapest front fraction = 40
    assert got == pytest.approx(24.0)
    assert got < naive, (
        f"{got:.4f} is not below the naive band/front_frac figure ({naive}); "
        "the spill is not being measured and the HUF would blow past its band")


def test_the_override_still_wins(monkeypatch):
    _set_k(monkeypatch, 1.2)
    eng = _engine(1.2, [], override={6: 42.0})
    b, n = _buckets()
    assert eng._huf_annual_wd(6, b, n, 30) == 42.0


def test_the_ration_is_computed_once_per_fy(monkeypatch):
    """It is called MONTHLY as `/12`. Recomputing each month would re-read a
    corpus that the earlier months had already drawn down, so the twelve
    instalments would not sum to the annual ration."""
    _set_k(monkeypatch, 1.2)
    eng = _engine(1.2, [])
    b, n = _buckets()
    calls = []
    real = eng._huf_ration
    eng._huf_ration = lambda *a: (calls.append(1), real(*a))[1]
    for _ in range(12):
        eng._huf_annual_wd(6, b, n, 30)
    assert len(calls) == 1, f"recomputed {len(calls)} times in one FY"


def test_an_empty_huf_rations_nothing(monkeypatch):
    _set_k(monkeypatch, 1.2)
    eng = _engine(1.2, [])
    assert eng._huf_annual_wd(6, {"F": FIFOBucket()}, {"F": NAV}, 30) == 0.0


# ── the demand ceiling ───────────────────────────────────────────────────
def _engine_with_demand(req, oi):
    eng = _engine(1.2, [])
    # split the cash across the two purses so neither is double counted
    eng.state.personal_income = types.SimpleNamespace(
        assessable=lambda *a, **kw: 0.0, taxable=lambda *a, **kw: oi,
        non_taxable=lambda *a, **kw: 0.0)
    eng.state.huf_income = types.SimpleNamespace(
        assessable=lambda *a, **kw: 0.0, taxable=lambda *a, **kw: 0.0,
        non_taxable=lambda *a, **kw: 0.0)
    eng.state.get_requirement = lambda fy: req
    return eng


def test_the_ration_never_exceeds_the_years_demand(monkeypatch):
    """The ration is a CEILING on what the HUF may supply, not a mandate.

    Without this the HUF hands over its full ration even in years where that
    alone exceeds the requirement. The personal draw cannot go negative to
    compensate, so the plan OVER-DELIVERS and the solver reports
    converged=False -- the B1d-ii shape exactly.

    Measured on s0_1chunk before the fix: 7.876L of over-delivery at k=1.2,
    concentrated in FY11-20 where the ration (9.74 at FY15) ran past the
    corpus demand (8.715).
    """
    _set_k(monkeypatch, 1.2)
    b, n = _buckets()
    # ration 1.2*200/30 = 8.0, band cap 8.0, but only 3.0 is actually needed
    eng = _engine_with_demand(req=12.0, oi=9.0)
    assert eng._huf_annual_wd(1, b, n, 30) == pytest.approx(3.0)


def test_a_year_needing_nothing_draws_nothing(monkeypatch):
    _set_k(monkeypatch, 1.2)
    b, n = _buckets()
    eng = _engine_with_demand(req=5.0, oi=9.0)      # other income covers it
    assert eng._huf_annual_wd(1, b, n, 30) == 0.0


def test_the_demand_ceiling_does_not_bind_when_the_year_needs_more(monkeypatch):
    """Otherwise the ceiling would silently become the whole rule."""
    _set_k(monkeypatch, 1.2)
    b, n = _buckets()
    eng = _engine_with_demand(req=100.0, oi=9.0)    # demand 91, far above
    assert eng._huf_annual_wd(1, b, n, 30) == pytest.approx(8.0)


def test_huf_ration_is_the_minimum_of_all_three_constraints(monkeypatch):
    """Direct cover for `_huf_ration`, which the review graph flagged as
    reached only through `_huf_annual_wd`."""
    _set_k(monkeypatch, 1.2)
    b, n = _buckets()
    eng = _engine_with_demand(req=100.0, oi=0.0)
    # ration binds  (40 years -> 6.0 < band cap 8.0 < demand 100)
    assert eng._huf_ration(1, b, n, 40) == pytest.approx(6.0)
    # band binds    (1 year left -> ration 240, cap 8.0)
    assert eng._huf_ration(30, b, n, 30) == pytest.approx(8.0)
    # demand binds
    eng2 = _engine_with_demand(req=12.0, oi=9.0)
    assert eng2._huf_ration(1, b, n, 30) == pytest.approx(3.0)
