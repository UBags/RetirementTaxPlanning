"""
Through-the-cycle percentile metrics.

Two quantities, both at a configurable percentile (P33 by decision):

  risk_free_rate      average liquid-fund rolling 3Y CAGR across funds per
                      month, THEN the Pth percentile of that monthly series.
  fund expected ret   Pth percentile of each of the 3Y/5Y/7Y/10Y rolling
                      series, then the lowest of the four.

The order in the first one is the whole point and has its own test:
average-then-percentile measures the rate cycle, percentile-then-average
measures fund dispersion. They are different numbers and only the first is
what "the through-the-cycle liquid-fund yield" means.

No network and no NAV fetching is involved — the module takes series and
returns numbers, so all of this runs offline.
"""
from __future__ import annotations

from datetime import date

import pytest

import percentile_metrics as pm

# ── classification ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("fund_type,name", [
    ("Debt Scheme - Liquid Fund", "HDFC Liquid Fund"),
    ("Debt Scheme - Liquid Fund", "DSP Liquidity Fund"),
    ("Debt Scheme - Liquid Fund", "PGIM India INSTA CASH FUND"),
    (None, "Axis Liquid Fund"),
    ("", "BANDHAN LIQUID FUND"),
])
def test_liquid_funds_are_recognised(fund_type, name):
    assert pm.is_liquid_fund(fund_type, name)


@pytest.mark.parametrize("fund_type", [
    "Debt Scheme - Money Market Fund",
    "Debt Scheme - Overnight Fund",
    "Debt Scheme - Low Duration Fund",
    "Debt Scheme - Ultra Short Duration Fund",
    "Debt Scheme - Short Duration Fund",
    "Debt Scheme - Corporate Bond Fund",
    "Equity Scheme - Large Cap Fund",
])
def test_adjacent_categories_are_not_liquid(fund_type):
    """
    Money Market and Overnight are different instruments with different
    yields. Excluding them is a decision, not an oversight, so it is pinned.
    """
    assert not pm.is_liquid_fund(fund_type, "Some Fund")


def test_matching_is_on_the_category_not_the_full_amfi_string():
    """
    AMFI has reorganised its scheme-category prefixes before. Matching
    "Liquid Fund" rather than "Debt Scheme - Liquid Fund" survives that.
    """
    assert pm.is_liquid_fund("Liquid Fund", None)
    assert pm.is_liquid_fund("Open Ended Debt - Liquid Fund", None)
    assert pm.is_liquid_fund("Debt Scheme - Liquid Fund", None)


def test_type_wins_over_a_misleading_name():
    assert not pm.is_liquid_fund("Debt Scheme - Overnight Fund",
                                 "XYZ Liquid Advantage Fund")


# ── percentile ───────────────────────────────────────────────────────────────

def test_percentile_matches_numpy_linear_interpolation():
    np = pytest.importorskip("numpy")
    vals = [0.031, 0.045, 0.052, 0.061, 0.066, 0.068, 0.071, 0.069, 0.058]
    for p in (0, 20, 25, 33, 50, 100):
        assert pm.percentile_of(vals, p) == pytest.approx(
            float(np.percentile(vals, p)), abs=1e-12), f"p{p}"


def test_percentile_ignores_nan():
    assert pm.percentile_of([0.05, float("nan"), 0.07], 50) == pytest.approx(0.06)


def test_percentile_of_a_single_reading_is_that_reading():
    assert pm.percentile_of([0.055], 33) == pytest.approx(0.055)


def test_too_few_readings_returns_none_rather_than_a_number():
    """
    A percentile of three readings is not a percentile. Returning one invites
    it to be used as though it were — which matters here because the per-fund
    metric takes a MIN, so an unstable value can be the one that wins.
    """
    assert pm.percentile_of([0.05, 0.06, 0.07], 33, min_readings=12) is None
    assert pm.percentile_of([], 33) is None


@pytest.mark.parametrize("bad", [-1, 101, 150])
def test_percentile_out_of_range_is_rejected(bad):
    with pytest.raises(ValueError, match=r"\[0, 100\]"):
        pm.percentile_of([0.05, 0.06], bad)


# ── cross-fund alignment ─────────────────────────────────────────────────────

def _d(y, m):
    return date(y, m, 1)


def test_alignment_is_by_date_not_by_position():
    """
    THE requirement from §1(a). Two funds with different inception dates have
    different calendar months at the same list index; averaging positionally
    would combine unrelated periods.

    Fund A starts a year earlier than Fund B. Only the overlapping months may
    be averaged across both.
    """
    a = [(_d(2020, m), 0.04) for m in range(1, 13)] + \
        [(_d(2021, m), 0.06) for m in range(1, 13)]
    b = [(_d(2021, m), 0.08) for m in range(1, 13)]

    out = dict(pm.cross_fund_monthly_average({"A": a, "B": b}, min_funds=1))

    assert out[_d(2020, 6)] == pytest.approx(0.04), "2020 is fund A alone"
    assert out[_d(2021, 6)] == pytest.approx(0.07), "2021 averages both"


def test_months_with_too_few_funds_are_dropped():
    """
    Early months are covered by a handful of long-lived schemes. Their
    average describes those schemes, not the category, and would otherwise
    set the floor for the whole plan.
    """
    a = [(_d(2015, m), 0.09) for m in range(1, 13)]          # A alone
    wide = {f"F{i}": [(_d(2016, m), 0.05) for m in range(1, 13)]
            for i in range(12)}
    series = {"A": a, **wide}

    out = dict(pm.cross_fund_monthly_average(series, min_funds=10))
    assert _d(2015, 6) not in out, "a single fund set a monthly average"
    assert out[_d(2016, 6)] == pytest.approx(0.05)


def test_average_is_unweighted_across_whatever_funds_have_data():
    series = {
        "A": [(_d(2022, 1), 0.06)],
        "B": [(_d(2022, 1), 0.08)],
        "C": [(_d(2022, 1), 0.10)],
    }
    out = dict(pm.cross_fund_monthly_average(series, min_funds=1))
    assert out[_d(2022, 1)] == pytest.approx(0.08)


def test_output_is_sorted_by_date():
    series = {"A": [(_d(2022, 5), 0.06), (_d(2021, 1), 0.05),
                    (_d(2023, 9), 0.07)]}
    dates = [d for d, _ in pm.cross_fund_monthly_average(series, min_funds=1)]
    assert dates == sorted(dates)


# ── risk-free rate ───────────────────────────────────────────────────────────

def _rate_cycle(n_funds=30, months=120):
    """Liquid funds tracking one rate cycle: ~6.5%, down to ~3.5%, back up.

    Every fund sees the same cycle with a small idiosyncratic offset, which
    is how liquid funds actually behave — they are near-pure rate
    instruments, so cross-fund dispersion is far smaller than the swing.
    """
    import math
    series = {}
    for f in range(n_funds):
        offset = (f - n_funds / 2) * 0.0002
        pts = []
        for m in range(months):
            base = 0.05 + 0.015 * math.cos(2 * math.pi * m / months)
            pts.append((_d(2016 + m // 12, m % 12 + 1), base + offset))
        series[f"Liquid {f}"] = pts
    return series


def test_risk_free_is_below_the_mean_and_above_the_worst():
    series = _rate_cycle()
    monthly = [v for _d_, v in pm.cross_fund_monthly_average(series, min_funds=10)]
    rf = pm.risk_free_from_liquid_funds(series, percentile=33.0, min_funds=10)

    assert rf is not None
    assert min(monthly) < rf < sum(monthly) / len(monthly), (
        f"P33 {rf:.5f} did not land between the worst month {min(monthly):.5f} "
        f"and the mean {sum(monthly) / len(monthly):.5f}")


def test_a_higher_percentile_gives_a_higher_rate():
    """Guards a transposed comparison inside percentile_of."""
    series = _rate_cycle()
    rates = [pm.risk_free_from_liquid_funds(series, percentile=p, min_funds=10)
             for p in (20.0, 25.0, 33.0, 50.0)]
    assert rates == sorted(rates), f"not monotonic in the percentile: {rates}"


def test_average_then_percentile_is_not_percentile_then_average():
    """
    The order is the decision, and the two orders give different answers.

    Average-first asks what liquid funds as a class returned in a bad stretch
    — a statement about the rate cycle. Percentile-first asks what a typical
    fund does in its own bad periods — a statement about fund dispersion.
    Only the first is the through-the-cycle yield.

    Constructed so the two provably differ: fund B's bad months are fund A's
    good months, so the class average is flat while each fund individually
    swings.
    """
    a = [(_d(2020, m), 0.04 if m <= 6 else 0.08) for m in range(1, 13)]
    b = [(_d(2020, m), 0.08 if m <= 6 else 0.04) for m in range(1, 13)]
    series = {"A": a, "B": b}

    avg_then_pct = pm.risk_free_from_liquid_funds(
        series, percentile=33.0, min_funds=2, min_readings=1)

    pct_then_avg = sum(
        pm.percentile_of([v for _d_, v in s], 33.0) for s in series.values()
    ) / 2

    assert avg_then_pct == pytest.approx(0.06), "class average is flat at 6%"
    assert pct_then_avg == pytest.approx(0.04, abs=1e-9)
    assert avg_then_pct != pytest.approx(pct_then_avg), (
        "the two orders coincided — this test proves nothing as written")


def test_thin_data_returns_none_rather_than_a_confident_number():
    """
    The caller must be able to leave the configured rate alone rather than
    overwrite it with something built from four funds and six months.
    """
    assert pm.risk_free_from_liquid_funds({}, min_funds=10) is None
    thin = {f"L{i}": [(_d(2024, m), 0.06) for m in range(1, 4)]
            for i in range(30)}
    assert pm.risk_free_from_liquid_funds(thin, min_funds=10,
                                          min_readings=24) is None


# ── per-fund expected return ─────────────────────────────────────────────────

def test_expected_return_is_the_lowest_percentile_across_windows():
    series = {
        "3Y":  [0.05] * 50 + [0.07] * 40,     # P33 lands inside the 0.05 block
        "5Y":  [0.08] * 60,
        "7Y":  [0.085] * 40,
        "10Y": [0.09] * 20,
    }
    out = pm.fund_expected_return(series, percentile=33.0)
    assert out == pytest.approx(0.05, abs=1e-9)


def test_a_window_with_too_few_readings_cannot_win_the_min():
    """
    The resolution problem, made concrete. Ten years of history gives the 10Y
    series ~13 readings; P33 of thirteen is the fourth value and moves
    visibly. Because the metric takes a MIN, that unstable number would
    otherwise be the one selected whenever it happens to be low.
    """
    series = {
        "3Y":  [0.07] * 90,
        "10Y": [0.01] * 5,                    # implausibly low, 5 readings
    }
    out = pm.fund_expected_return(series, percentile=33.0,
                                  min_readings={"3Y": 24, "10Y": 12})
    assert out == pytest.approx(0.07, abs=1e-9), (
        "a five-reading window was allowed to set the fund's expected return")


def test_expected_return_is_none_when_no_window_qualifies():
    assert pm.fund_expected_return({"3Y": [0.07] * 3}, percentile=33.0) is None
    assert pm.fund_expected_return({}, percentile=33.0) is None


def test_expected_return_moves_with_the_percentile():
    series = {"3Y": [0.03 + 0.001 * i for i in range(90)]}
    lo = pm.fund_expected_return(series, percentile=20.0)
    hi = pm.fund_expected_return(series, percentile=50.0)
    assert lo < hi


# ── the configured values ────────────────────────────────────────────────────

def test_percentiles_are_configurable_and_sane():
    cfg = pm.configured()
    for key in ("risk_free_rate_percentile", "fund_return_percentile"):
        val = float(cfg[key])
        assert 0.0 < val < 100.0, f"{key} = {val} is not a percentile"
        assert val <= 50.0, (
            f"{key} = {val} is above the median. These metrics exist to be "
            "conservative; a percentile above 50 inverts that.")


def test_minimum_fund_count_is_configurable_and_sane():
    n = int(pm.configured()["risk_free_min_funds"])
    assert n >= 2, (
        f"risk_free_min_funds = {n}: a 'cross-fund average' over one fund is "
        "that fund")


# ── the FD benchmark ladder ──────────────────────────────────────────────────

def test_ladder_rungs_come_from_one_series_and_descend():
    """A lower percentile must not yield a higher rate.

    Arithmetically impossible on a single monthly series, so if it happens
    the rungs are describing different fund sets -- which is exactly the
    failure three separate risk_free_from_liquid_funds() calls could produce
    silently after a change to the filtering.
    """
    series = {f"Liquid Fund {i}": [(f"2020-{m:02d}-01", 0.04 + 0.0004 * (m + i))
                                   for m in range(1, 13)]
              for i in range(12)}
    ladder = pm.risk_free_ladder(series, (33, 20, 5), min_funds=10,
                                 min_readings=6)
    assert set(ladder) == {33.0, 20.0, 5.0}
    assert ladder[5.0] <= ladder[20.0] <= ladder[33.0]


def test_ladder_agrees_with_the_single_percentile_function():
    """The base rung must equal what the existing function returns.

    If these ever disagree, the plan's anchor and the benchmark's top rung
    have drifted apart while both still looking measured.
    """
    series = {f"Liquid Fund {i}": [(f"2020-{m:02d}-01", 0.03 + 0.001 * m + 0.0001 * i)
                                   for m in range(1, 13)]
              for i in range(12)}
    ladder = pm.risk_free_ladder(series, (33, 20, 5), min_funds=10,
                                 min_readings=6)
    single = pm.risk_free_from_liquid_funds(series, percentile=33,
                                            min_funds=10, min_readings=6)
    assert ladder[33.0] == pytest.approx(single)


def test_ladder_refuses_rather_than_guessing_on_thin_data():
    series = {f"Liquid Fund {i}": [("2020-01-01", 0.05)] for i in range(12)}
    assert pm.risk_free_ladder(series, (33, 20, 5), min_funds=10,
                               min_readings=24) is None


def test_configured_exposes_all_three_percentiles():
    t = pm.configured()
    for key in ("risk_free_rate_percentile", "risk_free_rate_mid_percentile",
                "risk_free_rate_low_percentile"):
        assert key in t, f"{key} missing from configured()"
    assert (t["risk_free_rate_low_percentile"]
            < t["risk_free_rate_mid_percentile"]
            < t["risk_free_rate_percentile"]), (
        "the ladder percentiles must descend: the later chunks of the FD "
        "benchmark are meant to be the less confident ones")


# ── the fund type decides ────────────────────────────────────────────────────

def test_the_regulatory_type_overrules_a_liquid_sounding_name():
    """A Corporate Bond Fund is not liquid however it is marketed.

    is_liquid_fund used to try the type, and if the type was merely
    UNRECOGNISED fall through to the name -- so a scheme categorised as
    corporate bond but named "...Liquid Fund" was counted as liquid and the
    risk-free rate was measured over a category it does not describe. The
    name is a fallback for missing data, not a second opinion about a
    category the regulator has already assigned.
    """
    assert not pm.is_liquid_fund("Debt Scheme - Corporate Bond Fund",
                                 "ACME Liquid Fund")
    assert not pm.is_liquid_fund("Debt Scheme - Banking and PSU Fund",
                                 "XYZ Liquid Plus")


def test_the_name_is_used_only_when_the_type_is_missing():
    assert pm.is_liquid_fund(None, "ACME Liquid Fund")
    assert pm.is_liquid_fund("", "ACME Liquid Fund")
    assert not pm.is_liquid_fund(None, "ACME Corporate Bond Fund")


def test_a_liquid_type_is_liquid_whatever_the_name():
    assert pm.is_liquid_fund("Debt Scheme - Liquid Fund", "DSP Liquidity Fund")
    assert pm.is_liquid_fund("Debt Scheme - Liquid Fund", "Something Else")


def test_excluded_categories_are_still_excluded_by_type():
    for t in ("Debt Scheme - Overnight Fund", "Debt Scheme - Ultra Short Duration",
              "Debt Scheme - Low Duration Fund", "Debt Scheme - Money Market Fund"):
        assert not pm.is_liquid_fund(t, "ACME Liquid Fund"), t


# ── the risk-free series builder emits no deprecation warnings ───────────────

def test_rf_series_concat_is_not_deprecated(recwarn):
    """pandas FutureWarning at get_funds_data.py:760.

    pd.concat([fallback, overnight_rf]) with an empty entry relied on
    behaviour pandas is removing: empties ignored when working out the result
    dtype. Either side is legitimately empty -- no overnight fund data, or a
    cutover earlier than 2000 -- so this fired on every run of the debt
    benchmark path the Monte Carlo bootstrap depends on.

    recwarn rather than filterwarnings("error"): the assertion is about THIS
    call site, and turning every FutureWarning in the process into an error
    would fail on unrelated pandas churn elsewhere.
    """
    import get_funds_data as gfd

    gfd._rf_cache = None
    try:
        gfd.get_rf_monthly()
    except Exception:
        pytest.skip("rf series unavailable in this environment")
    finally:
        gfd._rf_cache = None

    offenders = [w for w in recwarn
                 if issubclass(w.category, (FutureWarning, DeprecationWarning))
                 and "concat" in str(w.message).lower()]
    assert not offenders, (
        "concat deprecation warning is back: "
        + "; ".join(str(w.message)[:120] for w in offenders))
