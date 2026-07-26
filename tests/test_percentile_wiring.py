"""
`get_funds_data._rolling_stats()` after the percentile wiring.

Three things are pinned:

  1. The Pth percentile is exported beside the existing mean and minimum, at
     the percentile `config.fund_return_percentile` names.
  2. Every window's END DATE comes back aligned 1:1 with its CAGR, which is
     what lets one fund's series be averaged against another's by calendar
     month rather than by list position.
  3. The four per-window series stay positionally aligned even when a window
     produces a non-finite CAGR — the desync the outlier filter used to have.

get_funds_data.py fetches NAV history from AMFI, so everything here builds
its own synthetic series and touches no network. That is also the limit of
this file: it proves the arithmetic, not the data.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from get_funds_data import _rolling_stats


def _series(months=120, drift=0.005, vol=0.001, seed=7):
    idx = pd.date_range("2015-01-31", periods=months, freq="ME")
    rng = np.random.default_rng(seed)
    monthly = drift + rng.normal(0, vol, months)
    nav = pd.Series(np.cumprod(1.0 + monthly), index=idx)
    ret = pd.Series(monthly, index=idx)
    return nav, ret


# ── the percentile ───────────────────────────────────────────────────────────

def test_percentile_is_exported_beside_mean_and_minimum():
    r = _rolling_stats(*_series(), 36)
    for key in ("avg_cagr", "min_cagr", "pctile_cagr", "pctile_used"):
        assert key in r, f"{key} missing from _rolling_stats output"


def test_percentile_uses_the_configured_value():
    from configuration import config
    r = _rolling_stats(*_series(), 36)
    assert r["pctile_used"] == pytest.approx(float(config.fund_return_percentile))


def test_percentile_matches_the_series_it_claims_to_describe():
    """
    Tolerance is 1e-6, not exact: `_cagrs` is stored rounded to 4dp in
    percent for compact JSON, so reconstructing it loses ~5e-8. The stats are
    computed from the unrounded values, which is correct — the rounding is a
    storage decision for the chart popup.
    """
    r = _rolling_stats(*_series(), 36)
    cagrs = [v / 100.0 for v in r["_cagrs"]]      # _cagrs is stored in %
    assert r["pctile_cagr"] == pytest.approx(
        float(np.percentile(cagrs, r["pctile_used"])), abs=1e-6)


def test_percentile_is_at_least_the_minimum():
    """
    The invariant that actually holds. Note the percentile may sit ABOVE the
    mean: on a left-skewed rolling series — a long tail of bad windows
    dragging the mean down, which is what debt funds produce — P33 exceeds
    the average. Asserting `pctile <= avg` would be wrong.
    """
    for window in (36, 60, 84):
        r = _rolling_stats(*_series(), window)
        if not r:
            continue
        assert r["min_cagr"] <= r["pctile_cagr"] + 1e-12, f"window {window}"
        assert r["pctile_cagr"] <= max(r["_cagrs"]) / 100.0 + 1e-12


def test_a_higher_percentile_would_give_a_higher_number(monkeypatch):
    r = _rolling_stats(*_series(), 36)
    cagrs = [v / 100.0 for v in r["_cagrs"]]
    values = [float(np.percentile(cagrs, p)) for p in (10, 25, 33, 50, 75)]
    assert values == sorted(values)


# ── window end dates ─────────────────────────────────────────────────────────

def test_every_cagr_carries_its_window_end_date():
    r = _rolling_stats(*_series(), 36)
    assert len(r["_cagr_dates"]) == len(r["_cagrs"]), (
        "dates and CAGRs are different lengths — cross-fund alignment by "
        "calendar month would silently pair the wrong periods")


def test_dates_are_the_window_end_not_the_window_start():
    """
    A 36-month window over a series starting Jan 2015 first CLOSES in Dec
    2017. If these came back as start dates the whole series would be shifted
    three years earlier, and averaging across funds would still 'work' —
    silently, on the wrong months.
    """
    r = _rolling_stats(*_series(months=120), 36)
    assert r["_cagr_dates"][0].startswith("2017-12"), r["_cagr_dates"][0]
    assert r["_cagr_dates"][-1].startswith("2024-12"), r["_cagr_dates"][-1]


def test_dates_are_strictly_increasing():
    r = _rolling_stats(*_series(), 36)
    dates = r["_cagr_dates"]
    assert dates == sorted(dates)
    assert len(set(dates)) == len(dates), "duplicate window end dates"


def test_window_count_matches_the_history_length():
    """120 months of history, 36-month window, one window per month."""
    r = _rolling_stats(*_series(months=120), 36)
    assert len(r["_cagrs"]) == 120 - 36 + 1


# ── the desync fix ───────────────────────────────────────────────────────────

def test_series_stay_aligned_when_a_window_has_no_finite_cagr():
    """
    The bug this patch fixed. `raw_cagrs` and `raw_stds` were appended
    conditionally, so a window with a finite std but a non-finite CAGR shifted
    every later index by one — after which the outlier filter discarded the
    CAGRs of windows it never meant to touch, and `if i < len(raw_cagrs)`
    swallowed the IndexError.

    A zero opening NAV is the realistic trigger.
    """
    nav, ret = _series()
    nav = nav.copy()
    nav.iloc[0] = 0.0

    r = _rolling_stats(nav, ret, 36)
    assert r, "a single bad NAV point emptied the whole result"
    assert len(r["_cagrs"]) == len(r["_cagr_dates"]), (
        "one bad NAV point desynced the CAGR series from its dates")


def test_a_clean_series_is_unaffected_by_the_fix():
    """
    The fix must not move numbers for funds with clean history — those are
    the overwhelming majority, and Avg_CAGR_* feeds the engine via spec 2.1.
    """
    r = _rolling_stats(*_series(), 36)
    cagrs = [v / 100.0 for v in r["_cagrs"]]
    assert r["avg_cagr"] == pytest.approx(float(np.mean(cagrs)), abs=1e-6)
    assert r["min_cagr"] == pytest.approx(float(np.min(cagrs)), abs=1e-6)


def test_too_short_a_history_returns_empty_not_a_wrong_answer():
    nav, ret = _series(months=24)
    assert _rolling_stats(nav, ret, 36) == {}


# ── it feeds percentile_metrics ──────────────────────────────────────────────

def test_output_is_shaped_for_cross_fund_averaging():
    """
    The handoff. percentile_metrics.cross_fund_monthly_average() wants
    {fund: [(date, value), ...]}; _rolling_stats must be zippable into that
    without reshaping, or the two halves have been designed against different
    assumptions.
    """
    import percentile_metrics as pm

    per_fund = {}
    for i in range(12):
        r = _rolling_stats(*_series(seed=i), 36)
        per_fund[f"Liquid {i}"] = list(zip(
            r["_cagr_dates"], [v / 100.0 for v in r["_cagrs"]]))

    monthly = pm.cross_fund_monthly_average(per_fund, min_funds=10)
    assert monthly, "no month had enough funds — alignment produced nothing"

    rf = pm.risk_free_from_liquid_funds(per_fund, percentile=33.0, min_funds=10)
    assert rf is not None
    assert 0.0 < rf < 0.20, f"implausible risk-free rate {rf}"


# ── the export whitelist ─────────────────────────────────────────────────────

def _cols_whitelist():
    import inspect
    import re

    import get_funds_data as g
    src = inspect.getsource(g.main)
    block = re.search(r"cols = \[(.*?)\n    \]", src, re.S)
    assert block, "could not locate the `cols` whitelist in get_funds_data.main()"
    return set(re.findall(r'"([^"]+)"', block.group(1)))


def test_new_columns_survive_the_export_whitelist():
    """
    `main()` does `pd.DataFrame(results).reindex(columns=cols)` against an
    explicit whitelist. A column computed into `out[...]` but absent from that
    list is silently DROPPED — no warning, no error, it simply is not in the
    CSV.

    That is exactly how Pctile_CAGR_* shipped missing: the values were computed
    correctly and discarded on the way out. Writing into `out` is not
    sufficient to reach the file, and nothing about the code says so.
    """
    have = _cols_whitelist()
    need = {f"Pctile_CAGR_{w}" for w in ("3Y", "5Y", "7Y", "10Y")}
    need.add("Return_Pctile")
    missing = sorted(need - have)
    assert not missing, (
        f"{missing} are computed but not in the export whitelist, so they will "
        "not reach Fund_Metrics_Output.csv")


def test_every_rolling_stat_exported_is_whitelisted():
    """
    The general form, so the next added column cannot repeat this. Anything
    `_rolling_stats()` returns and the per-fund `out` dict exposes must be on
    the whitelist or it dies at the reindex.
    """
    r = _rolling_stats(*_series(), 36)
    public = {k for k in r if not k.startswith("_")}
    have = _cols_whitelist()

    stat_to_col = {
        "avg_cagr": "Avg_CAGR_3Y", "min_cagr": "Min_CAGR_3Y",
        "pctile_cagr": "Pctile_CAGR_3Y", "pctile_used": "Return_Pctile",
        "avg_std_dev": "Avg_Std_Dev_3Y", "max_std_dev": "Max_Std_Dev_3Y",
        "worst_max_dd": "Worst_Max_DD_3Y",
    }
    unmapped = sorted(public - set(stat_to_col))
    assert not unmapped, (
        f"_rolling_stats() now returns {unmapped}; add the matching column to "
        "the export whitelist and to this map, or it will be dropped silently")

    dropped = sorted(c for c in stat_to_col.values() if c not in have)
    assert not dropped, f"{dropped} would be discarded by the reindex"
