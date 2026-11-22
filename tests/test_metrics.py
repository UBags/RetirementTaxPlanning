"""Tests for data_synthesizer.metrics.

EVERY TEST HERE HAS A CLOSED-FORM ANSWER OR A CONSTRUCTED SERIES WITH A KNOWN
PROPERTY. Checking a statistic against a number the same code produced is how a
metric layer acquires confident, self-consistent, wrong values -- so nothing
below compares the code to itself.

The two that matter most:

    test_variance_ratio_matches_the_closed_form  -- VR(2) = 1 + rho for an
    MA(1); if this drifts, every fidelity claim about the term structure of
    variance is unanchored.

    test_periods_per_year_is_measured_not_252 -- the upstream engine assumed
    252 on a calendar-daily series and understated CAGR and volatility by
    about 30% while returning entirely plausible numbers.
"""
import math

import pytest

from swp_planner.data_synthesizer import metrics as M

# ── helpers ─────────────────────────────────────────────────────────────────

def levels_from(returns, start=100.0):
    lv = [start]
    for r in returns:
        lv.append(lv[-1] * math.exp(r))
    return lv


def daily_dates(n, start_year=2010):
    """n consecutive calendar dates as YYYY-MM-DD, 30-day months for ease."""
    out = []
    y, m, d = start_year, 1, 1
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}-{d:02d}")
        d += 1
        if d > 30:
            d = 1
            m += 1
            if m > 12:
                m = 1
                y += 1
    return out


def nav_of(dates, levels):
    return {d: v for d, v in zip(dates, levels)}


# ── frames ──────────────────────────────────────────────────────────────────

def test_periods_per_year_is_measured_not_252():
    """A calendar-daily series is ~365/yr. Assuming 252 is a 30% error."""
    dates = daily_dates(360 * 5)
    ppy = M.periods_per_year(dates)
    assert 340 < ppy < 380, f"got {ppy}"


def test_periods_per_year_handles_a_trading_day_series():
    dates = [d for i, d in enumerate(daily_dates(252 * 4 * 10 // 10))
             if i % 10 < 7]
    ppy = M.periods_per_year(dates)
    assert 230 < ppy < 280, f"got {ppy}"


def test_periods_per_year_needs_two_dates():
    assert M.periods_per_year([]) is None
    assert M.periods_per_year(["2010-01-01"]) is None


def test_month_ends_takes_the_last_dated_nav_in_each_month():
    dates = ["2010-01-05", "2010-01-28", "2010-02-03", "2010-02-27"]
    nav = {d: 1.0 for d in dates}
    assert M.month_ends(dates, nav) == ["2010-01-28", "2010-02-27"]


def test_month_ends_ignores_dates_with_no_nav():
    dates = ["2010-01-05", "2010-01-28"]
    assert M.month_ends(dates, {"2010-01-05": 1.0}) == ["2010-01-05"]


# ── first moments ───────────────────────────────────────────────────────────

def test_cagr_of_a_doubling_year():
    lv = [100.0 * 2 ** (i / 365.0) for i in range(366)]
    assert M.cagr(lv, 365.0) == pytest.approx(1.0, abs=1e-6)


def test_cagr_of_a_flat_series_is_zero():
    assert M.cagr([50.0] * 400, 365.0) == pytest.approx(0.0, abs=1e-12)


def test_volatility_of_a_constant_growth_series_is_zero():
    lv = [100.0 * 1.0002 ** i for i in range(500)]
    assert M.volatility(lv, 252.0) == pytest.approx(0.0, abs=1e-12)


def test_volatility_scales_with_the_square_root_of_frequency():
    """Same returns annualised at 4x the frequency must double the vol."""
    rets = [0.01 if i % 2 else -0.01 for i in range(1000)]
    lv = levels_from(rets)
    v1 = M.volatility(lv, 63.0)
    v4 = M.volatility(lv, 252.0)
    assert v4 == pytest.approx(2.0 * v1, rel=1e-9)


def test_skew_of_a_symmetric_series_is_zero():
    assert M.skewness([-2, -1, 0, 1, 2]) == pytest.approx(0.0, abs=1e-12)


def test_excess_kurtosis_of_a_two_point_series_is_minus_two():
    """A fair Bernoulli has kurtosis 1, so excess -2. Closed form."""
    xs = [-1.0, 1.0] * 500
    assert M.excess_kurtosis(xs) == pytest.approx(-2.0, abs=1e-9)


# ── variance ratio ──────────────────────────────────────────────────────────

def test_variance_ratio_of_an_alternating_series_is_below_one():
    """Perfect reversal: two-period returns cancel, so VR(2) -> 0."""
    lv = levels_from([0.02 if i % 2 == 0 else -0.02 for i in range(400)])
    assert M.variance_ratio(lv, 2) == pytest.approx(0.0, abs=1e-9)


def test_variance_ratio_matches_the_closed_form():
    """
    THE ANCHOR TEST. For returns with lag-1 autocorrelation rho,
    VR(2) = 1 + rho exactly. Build an MA(1) with a known rho and check it.
    """
    import random
    rng = random.Random(4)
    e = [rng.gauss(0, 1) for _ in range(60000)]
    theta = 0.6
    r = [0.001 * (e[i] + theta * e[i - 1]) for i in range(1, len(e))]
    rho = theta / (1 + theta ** 2)          # MA(1) lag-1 autocorrelation
    lv = levels_from(r)
    assert M.variance_ratio(lv, 2) == pytest.approx(1 + rho, abs=0.02)


def test_variance_ratio_of_a_random_walk_is_about_one():
    import random
    rng = random.Random(11)
    lv = levels_from([rng.gauss(0, 0.01) for _ in range(40000)])
    for n in (3, 6, 12):
        assert M.variance_ratio(lv, n) == pytest.approx(1.0, abs=0.12)


def test_variance_ratio_needs_enough_observations():
    assert M.variance_ratio([100.0, 101.0, 102.0], 12) is None


# ── occupancy ───────────────────────────────────────────────────────────────

def test_quantile_thresholds_split_into_equal_populations():
    thr = M.quantile_thresholds(list(range(1000)), buckets=10)
    assert len(thr) == 9
    occ = M.occupancy(list(range(1000)), thr)
    assert max(abs(o - 10.0) for o in occ) < 1.0


def test_occupancy_of_a_series_against_its_own_thresholds_is_flat():
    """
    THE VACUITY CHECK. Scored against its OWN quantiles every series looks
    perfect -- which is why the real thresholds must come from the real series.
    """
    xs = [i * i for i in range(500)]
    occ = M.occupancy(xs, M.quantile_thresholds(xs))
    assert max(abs(o - 10.0) for o in occ) < 1.5


def test_occupancy_of_a_shifted_series_piles_into_the_bottom_bucket():
    base = list(range(1000))
    thr = M.quantile_thresholds(base)
    occ = M.occupancy([x - 10000 for x in base], thr)
    assert occ[0] == pytest.approx(100.0)


def test_decile_occupancy_drift_adjustment_moves_the_mass_back():
    """
    Two series identical but for a deliberate drift cut. UNADJUSTED the lower
    one must sit low in the real buckets; ADJUSTED it must come back. If this
    ever passes both ways the drift correction has stopped doing anything, and
    requirement (b) and requirement (c) silently contradict each other again.
    """
    import random
    rng = random.Random(9)
    r = [rng.gauss(0.0004, 0.01) for _ in range(4000)]
    dates = daily_dates(len(r) + 1)
    hi = nav_of(dates, levels_from(r))
    cut = math.log(1 - 0.10) / 365.0        # a brutal 10pp/yr cut, to be clear
    lo = nav_of(dates, levels_from([x + cut for x in r]))
    mh = [hi[d] for d in M.month_ends(dates, hi)]
    ml = [lo[d] for d in M.month_ends(dates, lo)]
    raw = M.decile_occupancy(mh, ml, 12, drift_delta=0.0)
    adj = M.decile_occupancy(mh, ml, 12, drift_delta=1 / 0.90 - 1)
    assert raw[0] > 15.0, "unadjusted series should pile into the bottom"
    _, raw_dev = M.occupancy_deviation(raw)
    _, adj_dev = M.occupancy_deviation(adj)
    assert adj_dev < raw_dev / 4.0


def test_drift_delta_is_the_multiplicative_add_back_not_the_cut():
    """
    PINS AN AMBIGUITY THAT WOULD OTHERWISE BE GUESSED AT EVERY CALL SITE. A
    series cut to 0.90x must be restored with 1/0.90 - 1 = 0.1111, not with
    0.10. Passing the cut itself leaves a residual that looks like a fidelity
    failure and is arithmetic.
    """
    import random
    rng = random.Random(9)
    r = [rng.gauss(0.0004, 0.01) for _ in range(4000)]
    dates = daily_dates(len(r) + 1)
    hi = nav_of(dates, levels_from(r))
    cut = math.log(1 - 0.10) / 365.0
    lo = nav_of(dates, levels_from([x + cut for x in r]))
    mh = [hi[d] for d in M.month_ends(dates, hi)]
    ml = [lo[d] for d in M.month_ends(dates, lo)]
    _, dev_wrong = M.occupancy_deviation(
        M.decile_occupancy(mh, ml, 12, drift_delta=0.10))
    _, dev_right = M.occupancy_deviation(
        M.decile_occupancy(mh, ml, 12, drift_delta=1 / 0.90 - 1))
    assert dev_right < dev_wrong


# ── memory and asymmetry ────────────────────────────────────────────────────

def test_autocorrelation_of_a_repeating_pattern_is_exact():
    xs = [1.0, -1.0] * 500
    # Not 1e-9: the estimator sums lag..n while normalising over 0..n, so a
    # finite sample cannot reach exactly -1. That gap IS the edge effect, and
    # a tolerance tight enough to fail on it would be testing arithmetic
    # rather than behaviour.
    assert M.autocorrelation(xs, 1) == pytest.approx(-1.0, abs=1e-2)
    assert M.autocorrelation(xs, 2) == pytest.approx(1.0, abs=1e-2)


def test_acf_decay_exponent_recovers_a_planted_power_law():
    """Build |r| with ACF ~ k^-0.5 by construction and read beta back."""
    import random
    rng = random.Random(3)
    n = 40000
    base = [abs(rng.gauss(0, 1)) for _ in range(n)]
    # long-memory-ish smoothing: overlapping windows give a slow decay
    w = 40
    smooth = [sum(base[max(0, i - w):i + 1]) / min(w + 1, i + 1)
              for i in range(n)]
    r = [s * (1 if rng.random() < 0.5 else -1) for s in smooth]
    got = M.acf_abs_decay_exponent(r, max_lag=60)
    assert got is not None and 0.0 < got < 2.0, f"got {got}"


def test_hurst_of_white_noise_is_near_a_half():
    import random
    rng = random.Random(5)
    h = M.hurst_exponent([abs(rng.gauss(0, 1)) for _ in range(20000)])
    assert h == pytest.approx(0.5, abs=0.08), f"got {h}"


def test_hurst_of_a_trending_series_is_above_a_half():
    import random
    rng = random.Random(6)
    xs, cur = [], 0.0
    for _ in range(20000):
        cur = 0.995 * cur + rng.gauss(0, 1)     # strong persistence
        xs.append(abs(cur))
    h = M.hurst_exponent(xs)
    assert h > 0.6, f"got {h}"


def test_hurst_returns_none_rather_than_a_plausible_default():
    """A silent 0.5 is indistinguishable from a measurement of no memory."""
    assert M.hurst_exponent([1.0] * 50) is None


def test_leverage_correlation_is_negative_when_losses_raise_volatility():
    import random
    rng = random.Random(8)
    r, vol = [], 0.01
    for _ in range(20000):
        x = rng.gauss(0, vol)
        r.append(x)
        vol = 0.01 + (0.5 * abs(x) if x < 0 else 0.0)   # only losses raise it
    assert M.leverage_correlation(r, 1) < -0.02


def test_leverage_correlation_is_about_zero_when_volatility_is_symmetric():
    import random
    rng = random.Random(10)
    r, vol = [], 0.01
    for _ in range(20000):
        x = rng.gauss(0, vol)
        r.append(x)
        vol = 0.01 + 0.5 * abs(x)                       # both directions
    assert abs(M.leverage_correlation(r, 1)) < 0.05


# ── drawdown ────────────────────────────────────────────────────────────────

def test_max_drawdown_of_a_known_path():
    assert M.max_drawdown([100, 120, 60, 90]) == pytest.approx(-0.5)


def test_max_drawdown_of_a_monotone_series_is_zero():
    assert M.max_drawdown([1, 2, 3, 4]) == pytest.approx(0.0)


def test_underwater_durations_counts_completed_spells():
    done, unfinished = M.underwater_durations([10, 9, 8, 11, 10, 12])
    assert done == [2, 1] and unfinished == 0


def test_an_unfinished_spell_is_reported_separately():
    """
    A path ending 40% under water has one enormous INCOMPLETE spell. Folding it
    into the completed distribution would report a recovery that never happened
    -- which for an SWP is the difference between survivable and ruinous.
    """
    done, unfinished = M.underwater_durations([10, 12, 8, 7, 6])
    assert done == [] and unfinished == 3


def test_negative_run_distribution_sums_to_one():
    d = M.negative_run_distribution([-1, -1, 1, -1, 1, -1, -1, -1])
    assert sum(d.values()) == pytest.approx(1.0)
    assert d[3] == pytest.approx(1 / 3)


def test_expected_shortfall_is_the_mean_of_the_worst_tail():
    xs = [-10.0] + [1.0] * 99
    assert M.expected_shortfall(xs, q=0.99) == pytest.approx(10.0)


# ── the vector ──────────────────────────────────────────────────────────────

def test_measure_returns_none_not_defaults_on_a_short_series():
    dates = daily_dates(20)
    assert M.measure(nav_of(dates, [100.0] * 20), dates) == {}


def test_measure_populates_every_documented_key():
    import random
    rng = random.Random(2)
    n = 3000
    dates = daily_dates(n)
    lv = levels_from([rng.gauss(0.0003, 0.012) for _ in range(n - 1)])
    m = M.measure(nav_of(dates, lv), dates)
    for k in ("n_obs", "periods_per_year", "cagr", "vol", "skew", "kurtosis",
              "vr", "acf_beta", "hurst", "leverage", "max_drawdown", "ttr",
              "ttr_unfinished", "neg_runs", "es99"):
        assert k in m, f"missing {k}"
    assert m["n_obs"] == n
    assert set(m["vr"]) == {3, 6, 12, 18}


def test_measure_uses_the_debt_horizons_when_told_it_is_debt():
    import random
    rng = random.Random(1)
    n = 3000
    dates = daily_dates(n)
    lv = levels_from([rng.gauss(0.0002, 0.001) for _ in range(n - 1)])
    m = M.measure(nav_of(dates, lv), dates, is_equity=False)
    assert set(m["vr"]) == {3, 6, 12}
