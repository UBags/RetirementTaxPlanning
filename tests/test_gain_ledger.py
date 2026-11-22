"""Pins for `Engine.gain_ledger` (patcher 07a).

The ledger landed with no test. `free_gain_capacity` and
`cash_within_gain_budget` both landed dead WITH tests and that is the house
pattern; this is the deviation, and every remaining phase reads the ledger to
size its windows, so it needs pinning before anything is built on it.

What these assert, and why each earns its place:

* the ledger exists and is shaped as documented — the cheapest possible guard
  against a future refactor quietly dropping it;
* rebalance gain appears at chunk boundaries and nowhere else;
* rebalance gain never exceeds that FY's total gain of the same type, so the
  withdrawal's own share -- the quantity Phase 07 budgets against -- can never
  come out negative;
* `rebalance_tax_paid > 0` implies rebalance gain was recorded, cross-checking
  the ledger against `YearSummary`, which computes its figure independently;
* the ledger is a snapshot, so a caller mutating it cannot corrupt a later run.

The last test is the important one. METHODOLOGY §7.9 and §9.14: a fixture that
does not exercise the path proves nothing, and a defect shipped green twice
because of exactly that. `test_ledger_is_not_vacuous` fails if the multi-chunk
fixture stops recording boundary gain, which would make every other assertion
here true and meaningless.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent / "golden"))

from golden_projects import PROJECTS  # noqa: E402

from swp_planner.core.engine import Engine  # noqa: E402
from swp_planner.core.models import AppState  # noqa: E402

GAIN_KEYS = ("gain_d", "gain_e", "gain_o")
_CACHE: dict = {}


def _run(project: str, index: int):
    """One engine run per scenario, cached. Engine.run() is deterministic."""
    key = (project, index)
    if key not in _CACHE:
        state = AppState.from_dict(PROJECTS[project]["scenarios"][index])
        eng = Engine(state)
        _, yearly, _, _ = eng.run()
        _CACHE[key] = (state, eng, yearly)
    return _CACHE[key]


@pytest.fixture(scope="module")
def one_chunk():
    return _run("main", 0)


@pytest.fixture(scope="module")
def four_chunk():
    return _run("main", 3)


def test_ledger_exists_and_is_shaped(four_chunk):
    _state, eng, _yearly = four_chunk
    led = eng.gain_ledger
    assert set(led) == {"personal", "huf",
                        "rebalance_personal", "rebalance_huf"}
    for entity in ("personal", "huf"):
        assert led[entity], f"{entity} ledger is empty"
        for fy, acc in led[entity].items():
            assert isinstance(fy, int)
            for k in GAIN_KEYS:
                assert isinstance(acc[k], float)


def test_ledger_is_not_vacuous(four_chunk):
    """Guard against every other assertion here being trivially true."""
    _state, eng, _yearly = four_chunk
    rebal = eng.gain_ledger["rebalance_personal"]
    booked = {fy: sum(g.get(k, 0.0) for k in "deo")
              for fy, g in rebal.items()}
    positive = {fy: v for fy, v in booked.items() if v > 0.0}
    assert len(positive) >= 2, (
        "multi-chunk fixture recorded boundary gain at fewer than two "
        f"boundaries: {booked!r} — the other pins are meaningless")


def test_single_chunk_books_no_rebalance_gain(one_chunk):
    _state, eng, _yearly = one_chunk
    for side in ("rebalance_personal", "rebalance_huf"):
        booked = sum(sum(g.get(k, 0.0) for k in "deo")
                     for g in eng.gain_ledger[side].values())
        assert booked == pytest.approx(0.0, abs=1e-9), (
            f"{side} booked gain with only one allocation chunk")


def test_rebalance_gain_only_at_chunk_boundaries(four_chunk):
    state, eng, _yearly = four_chunk
    boundaries = set(state.chunk_boundary_years())
    assert boundaries, "fixture has no boundaries — wrong scenario"
    for side in ("rebalance_personal", "rebalance_huf"):
        for fy, g in eng.gain_ledger[side].items():
            if sum(g.get(k, 0.0) for k in "deo") > 0.0:
                assert fy in boundaries, (
                    f"{side} booked gain in FY{fy}, not a boundary "
                    f"(boundaries: {sorted(boundaries)})")


@pytest.mark.parametrize("entity,side", [
    ("personal", "rebalance_personal"),
    ("huf", "rebalance_huf"),
])
def test_withdrawal_share_is_never_negative(four_chunk, entity, side):
    """Phase 07 budgets against total gain LESS the rebalance's share."""
    _state, eng, _yearly = four_chunk
    led = eng.gain_ledger
    for fy, g in led[side].items():
        acc = led[entity].get(fy)
        assert acc is not None, f"{side} has FY{fy} but {entity} does not"
        for short, long in (("d", "gain_d"), ("e", "gain_e"), ("o", "gain_o")):
            rebal, total = g.get(short, 0.0), acc[long]
            assert rebal <= total + 1e-6, (
                f"FY{fy} {entity} {long}: rebalance {rebal:.6f} exceeds "
                f"total {total:.6f}, so the withdrawal share is negative")


def test_rebalance_tax_implies_rebalance_gain(four_chunk):
    """Cross-check the ledger against YearSummary's independent figure.

    One direction only. The converse is false and deliberately not asserted:
    `s3_4chunk` FY17 books 11.4 of rebalance gain and pays no tax at all,
    because the gain lands inside that year's tax-free headroom. A boundary
    can be free without being idle.
    """
    _state, eng, yearly = four_chunk
    led = eng.gain_ledger
    for y in yearly:
        if y.rebalance_tax_paid > 1e-9:
            booked = sum(
                led[side].get(y.year, {}).get(k, 0.0)
                for side in ("rebalance_personal", "rebalance_huf")
                for k in "deo")
            assert booked > 0.0, (
                f"FY{y.year} paid {y.rebalance_tax_paid:.4f} of rebalancing "
                "tax but the ledger booked no rebalancing gain")


def test_ledger_is_a_snapshot_not_a_live_reference():
    """A caller mutating the ledger must not corrupt a later run."""
    state = AppState.from_dict(PROJECTS["main"]["scenarios"][3])
    eng = Engine(state)
    eng.run()
    first = {fy: acc["gain_d"] for fy, acc in eng.gain_ledger["personal"].items()}

    for acc in eng.gain_ledger["personal"].values():
        acc["gain_d"] = -12345.0
    eng.gain_ledger["rebalance_personal"].clear()

    eng.run()
    second = {fy: acc["gain_d"] for fy, acc in eng.gain_ledger["personal"].items()}
    assert second == pytest.approx(first), (
        "mutating the ledger changed what a subsequent run reported")
