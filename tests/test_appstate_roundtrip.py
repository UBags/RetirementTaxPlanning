# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
AppState save/load round-trip.

WHY THIS EXISTS
---------------
`AppState.from_dict()` filters the incoming dict through a `known_keys`
whitelist and silently discards anything not listed.  `horizon_years` was
added to the dataclass, persisted correctly by `to_dict()`, and then dropped
on every load -- the field round-tripped to its default and no error was
raised anywhere.

That is the third silent-drop whitelist in this codebase:

  1. `.gitignore`, whose line 2 is `*` (three files lost)
  2. the CSV `cols` list in `get_funds_data.main()` (five computed columns)
  3. `known_keys` here

The first test below is the general guard: it walks the dataclass rather than
a hand-maintained list, so a field added next year is covered without anyone
remembering this file exists.  The failure message names the missing field and
the line to add it to.
"""
from __future__ import annotations

from dataclasses import fields
from datetime import date

import pytest

from models import DEFAULT_HORIZON_YEARS, AppState

# Fields deliberately not persisted.  Keep this list short and justified --
# every entry is a field whose value cannot survive a save, and each needs a
# reason, not just an entry.
NOT_PERSISTED = {
    # Recomputed by optimize_sticky_portfolio() on load; excluded from
    # to_dict() by its own _skip set.
    "glide_path",
}


def test_every_appstate_field_is_registered_for_load():
    """Every dataclass field must survive from_dict(), or be listed above.

    This is the check that would have caught horizon_years in seconds.
    """
    persisted = {f.name for f in fields(AppState)} - NOT_PERSISTED

    # known_keys is a local inside from_dict, so probe it the honest way:
    # round-trip a state that has a non-default value in every persisted
    # field and see which ones fail to come back.
    src = _state_with_distinctive_values()
    loaded = AppState.from_dict(src.to_dict())

    unset = [n for n in sorted(persisted)
             if getattr(src, n) == getattr(AppState(), n)]
    assert not unset, (
        f"_state_with_distinctive_values() leaves {unset} at the default, so "
        "this test cannot tell whether they round-trip. Give each a distinct "
        "value."
    )

    lost = [n for n in sorted(persisted)
            if getattr(loaded, n) != getattr(src, n)]

    assert not lost, (
        "these AppState fields did not survive to_dict() -> from_dict(): "
        f"{lost}. Add each to the known_keys whitelist in "
        "AppState.from_dict() AND to the cls(...) constructor call below it. "
        "A field missing from known_keys is discarded silently."
    )


def test_horizon_years_round_trips():
    """The specific regression: a non-default horizon must survive a save."""
    s = AppState(horizon_years=15)
    assert AppState.from_dict(s.to_dict()).horizon_years == 15
    assert AppState.from_dict(s.to_dict()).horizon == 15


def test_pre_horizon_save_file_loads_as_the_default():
    """A file written before horizon_years existed must project as it did."""
    legacy = {"fd_rate": 0.07, "investment_date": "2026-03-01"}
    s = AppState.from_dict(legacy)
    assert s.horizon_years == DEFAULT_HORIZON_YEARS
    assert s.horizon == DEFAULT_HORIZON_YEARS


@pytest.mark.parametrize("stored,expected", [
    (30, 30),     # default
    (15, 15),     # a shorter plan is honoured
    (10, 10),     # exactly the floor
    (99, 30),     # clamped up to config.max_simulation_years
    (9, 10),      # clamped down to config.min_simulation_years
    (1, 10),      # ditto -- a one-year drawdown is not a plan
    (0, 30),      # unset / falsy falls back to the ceiling
    (-5, 10),     # nonsense clamps to the floor, never an empty range
])
def test_horizon_clamps_into_the_configured_band(stored, expected):
    assert AppState(horizon_years=stored).horizon == expected


def test_the_floor_can_never_exceed_the_ceiling():
    """A config with min > max must not yield a horizon longer than the
    30-element arrays behind it."""
    import configuration
    cfg = configuration.config
    real = cfg._data["min_simulation_years"]
    try:
        cfg._data["min_simulation_years"] = 99
        assert AppState(horizon_years=5).horizon == int(cfg.max_simulation_years)
    finally:
        cfg._data["min_simulation_years"] = real


def test_the_horizon_band_is_self_consistent():
    from configuration import config
    assert 1 <= int(config.min_simulation_years) <= int(config.max_simulation_years), (
        "min_simulation_years must be at least 1 and no greater than "
        "max_simulation_years")


def test_fy_range_is_one_to_horizon_inclusive():
    s = AppState(horizon_years=15)
    assert list(s.fy_range()) == list(range(1, 16))
    assert s.fy_range()[-1] == s.horizon


def _state_with_distinctive_values() -> AppState:
    """An AppState whose every persisted field differs from its default.

    A field left at its default would round-trip 'successfully' even if it
    were being dropped, which is exactly the failure mode being tested.
    """
    from models import (
        AllocationChunk,
        EquityTaxChunk,
        FDRateChunk,
        FundEntry,
        HUFWithdrawalChunk,
        OtherIncome,
        OtherTaxChunk,
        ReturnChunk,
        SplitChunk,
        TaxChunk,
        TaxSlab,
        WindfallEntry,
    )

    fund = FundEntry(name="Test Fund", fund_type="debt", allocation=100.0)
    return AppState(
        investment_date=date(2027, 7, 1),
        horizon_years=17,
        funds=[fund],
        allocation_chunks=[AllocationChunk(year_from=1, year_to=17, funds=[fund])],
        individual_debt_chunks=[TaxChunk(1, 17, 11.0, [TaxSlab(0, 5, 0.05)])],
        individual_equity_chunks=[EquityTaxChunk(1, 17, 0.125, 1.25)],
        individual_other_chunks=[OtherTaxChunk(1, 17, 0.125)],
        huf_debt_chunks=[TaxChunk(1, 17, 4.0, [TaxSlab(0, 5, 0.05)])],
        huf_equity_chunks=[EquityTaxChunk(1, 17, 0.125, 1.25)],
        huf_other_chunks=[OtherTaxChunk(1, 17, 0.125)],
        return_chunks=[ReturnChunk(1, 17, 0.08)],
        split_chunks=[SplitChunk(1, 17, 0.6)],
        annual_requirements={1: 25.0, 5: 30.0},
        personal_income=OtherIncome(taxable_interest=3.5, pension=1.3,
                                    rental=2.0, rental_growth=0.04),
        huf_income=OtherIncome(taxable_interest=1.1),
        windfalls=[WindfallEntry(year=3, amount=12.0, target="personal")],
        huf_withdrawal_chunks=[HUFWithdrawalChunk(1, 17, 5.0)],
        huf_annual_requirements={2: 6.0},
        fd_rate_chunks=[FDRateChunk(1, 17, 0.069)],
        fd_rate=0.069,
        allocation_mode="singular",
        rebalance_spread_years=6,
        selected_robustness_rank=2,
        bi_tolerances={"return": 0.003},
        allocation_params={"total_money": 1500.0},
    )
