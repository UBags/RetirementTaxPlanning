"""
HANDOVER §2.5 consumption — one definition of a fund's expected return.

Before this, the same metric was written out seven times: six methods in
`models.py` and one in `engine.py`. Spec 2.1 exists *because* two of those
copies had drifted apart, so the planner projected one number on screen and
simulated another. Seven copies is seven chances for the next revision to miss
one, and there have now been two metric revisions in two days.

`FundEntry.expected_return()` is the single definition. Everything delegates.

Preference order, worst-first within each tier:

    1. min(Pctile_CAGR_*)   the §2.5 metric
    2. min(Avg_CAGR_*)      the §2.1 metric — retained so a project saved
                            before §2.5 projects exactly as it did
    3. min(cagr_*)          point-in-time, for data predating rolling columns
    4. caller's default

The reading-count guard lives at the point of measurement, in
`get_funds_data`, not here: `FundEntry` stores four scalars and no counts, so
a consumer cannot apply that rule later.
"""
from __future__ import annotations

import pytest

from swp_planner.core.models import FundEntry
from swp_planner.paths import PACKAGE_DIR


def _f(**kw):
    base = dict(name="F", fund_type="debt", allocation=10.0)
    base.update(kw)
    return FundEntry(**base)


# ── the tiers ────────────────────────────────────────────────────────────────

def test_percentile_tier_wins():
    f = _f(pctile_cagr_3=7.1, pctile_cagr_5=6.8, pctile_cagr_7=7.4,
           pctile_cagr_10=7.9,
           avg_cagr_3=9.0, avg_cagr_5=9.2, cagr_5=8.1)
    assert f.expected_return() == pytest.approx(6.8)


def test_falls_back_to_the_spec_2_1_metric():
    """A project saved before §2.5 has no percentile fields and must project
    exactly as it did — that is the whole point of keeping tier 2."""
    f = _f(avg_cagr_3=7.52, avg_cagr_5=7.49, avg_cagr_7=7.60, avg_cagr_10=7.63,
           cagr_5=6.53)
    assert f.expected_return() == pytest.approx(7.49)


def test_falls_back_to_point_in_time():
    f = _f(cagr_3=7.34, cagr_5=6.55, cagr_7=7.1, cagr_10=7.26)
    assert f.expected_return() == pytest.approx(6.55)


def test_returns_the_default_when_there_is_nothing():
    assert _f().expected_return(default=7.0) == pytest.approx(7.0)


def test_returns_none_rather_than_guessing():
    """Callers that need a number pass one; the method does not invent 7%."""
    assert _f().expected_return() is None


def test_tiers_do_not_mix():
    """
    A partial percentile tier must not be topped up from the Avg tier. Mixing
    would produce a number that is neither metric and belongs to no
    documented definition.
    """
    f = _f(pctile_cagr_10=9.0,
           avg_cagr_3=4.0, avg_cagr_5=4.1, avg_cagr_7=4.2, avg_cagr_10=4.3)
    assert f.expected_return() == pytest.approx(9.0), (
        "the Avg tier leaked into a fund that has a percentile value")


@pytest.mark.parametrize("bad", [0.0, -1.5])
def test_non_positive_values_are_missing_data_not_a_zero_return(bad):
    f = _f(pctile_cagr_3=bad, pctile_cagr_5=bad,
           pctile_cagr_7=bad, pctile_cagr_10=bad, avg_cagr_5=7.2)
    assert f.expected_return() == pytest.approx(7.2)


def test_a_partially_populated_percentile_tier_uses_what_it_has():
    """The reading-count guard leaves under-resolved windows as None, so a
    partial tier is the normal case, not an error."""
    f = _f(pctile_cagr_3=8.1, pctile_cagr_5=7.6,
           pctile_cagr_7=None, pctile_cagr_10=None, avg_cagr_5=4.0)
    assert f.expected_return() == pytest.approx(7.6)


# ── everything delegates ─────────────────────────────────────────────────────

def _chunk(funds, **kw):
    from swp_planner.core.models import AllocationChunk
    return AllocationChunk(year_from=1, year_to=30, funds=funds, **kw)


def test_chunk_yields_use_the_single_definition():
    funds = [_f(name="A", pctile_cagr_5=6.0, avg_cagr_5=9.0, allocation=50.0),
             _f(name="B", pctile_cagr_5=8.0, avg_cagr_5=9.0, allocation=50.0)]
    ch = _chunk(funds)
    assert ch.portfolio_yield() == pytest.approx(0.07), (
        "portfolio_yield() is still on the Avg tier")
    assert ch.category_yield("debt") == pytest.approx(0.07)


def test_optimized_yield_uses_the_single_definition():
    """
    The §2.1 requirement, restated for §2.5: whatever the engine applies,
    optimized_yield() must show. This is the pair that drifted apart last time.
    """
    funds = [_f(name="A", pctile_cagr_5=6.0, avg_cagr_5=9.0),
             _f(name="B", pctile_cagr_5=8.0, avg_cagr_5=9.0)]
    ch = _chunk(funds, target_weights={"A": 50.0, "B": 50.0})
    assert ch.optimized_yield() == pytest.approx(0.07)


# The engine-vs-UI equivalence that spec 2.1 was about is asserted over the
# real fixture corpus by tests/golden/test_return_metric.py::
# test_engine_rate_matches_optimized_yield, which needs the `scenario` fixture
# from tests/golden/conftest.py. It is not duplicated here.


def test_no_module_still_inlines_the_metric(project_root):
    """
    The structural guard. Spec 2.1 happened because the definition was copied;
    a new copy is how it happens again.
    """
    offenders = []
    for py in sorted(PACKAGE_DIR.rglob("*.py")):
        if py.name in ("percentile_metrics.py",):
            continue
        text = py.read_text(encoding="utf-8")
        if "avg_cagr_3, f.avg_cagr_5" in text or \
           "avg_cagr_3, fund.avg_cagr_5" in text:
            offenders.append(py.name)
    assert not offenders, (
        f"{offenders} inline the expected-return metric instead of calling "
        "FundEntry.expected_return(). That duplication is what spec 2.1 was.")


# ── the reading-count guard, at measurement ──────────────────────────────────

@pytest.mark.parametrize("months,window,expect_value", [
    (120, 120, False),   # 1 rolling 10Y window
    (130, 120, False),   # 11 — below the floor of 12
    (131, 120, True),    # 12 — exactly at it
    (58, 36, False),     # 23 3Y windows, floor is 24
    (60, 36, True),      # 25
])
def test_percentile_is_withheld_from_under_resolved_windows(
        months, window, expect_value):
    """
    A fund with exactly ten years of history has ONE 10Y window, so its
    percentile is that single number. Because expected_return() takes a MIN,
    that lone value is selected precisely when its noise happens to be low.

    Withheld at measurement, not filtered later: FundEntry stores four scalars
    and no counts and cannot re-derive this judgement.
    """
    import numpy as np
    import pandas as pd

    from swp_planner.funddata.get_funds_data import _rolling_stats

    idx = pd.date_range("2010-01-31", periods=months, freq="ME")
    rng = np.random.default_rng(1)
    monthly = 0.005 + rng.normal(0, 0.001, months)
    nav = pd.Series(np.cumprod(1.0 + monthly), index=idx)
    ret = pd.Series(monthly, index=idx)

    r = _rolling_stats(nav, ret, window)
    got = r.get("pctile_cagr") if r else None
    assert (got is not None) is expect_value, (
        f"{months} months / {window}mo window: "
        f"{len(r.get('_cagrs', []))} readings, pctile={got}")


def test_the_guard_thresholds_are_shared_not_copied():
    from swp_planner.simulation import percentile_metrics as pm
    assert set(pm.MIN_READINGS) == {"3Y", "5Y", "7Y", "10Y"}
    assert pm.MIN_READINGS["3Y"] > pm.MIN_READINGS["10Y"], (
        "a 3Y series has far more windows available than a 10Y one, so its "
        "floor should be higher, not lower")
