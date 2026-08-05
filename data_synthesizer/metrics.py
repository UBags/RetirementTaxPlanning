# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""metrics.py -- every fidelity statistic, measured the same way on real and
synthetic series.

NOTHING CAN BE FIXED UNTIL IT CAN BE SCORED. This module exists before any
generator does, because the previous synthesizer asserted that it preserved
sigma and cut the CAGR -- which it did -- and made no claim about anything
else, and offered no way to check. The result was a series whose variance ratio
at twelve months was 1.14 where the real one is 0.45, and nothing anywhere
would have said so.

TWO THINGS THIS MODULE REFUSES TO ASSUME.

PERIODS PER YEAR IS MEASURED, NOT ASSUMED. A series of AMFI NAVs is CALENDAR
daily -- about 384 observations a year once weekends are carried forward -- not
252. Annualising by 252 understates both CAGR and volatility by roughly 30%,
silently, in a number that still looks plausible. `periods_per_year` is derived
from the dates whenever dates are available.

A STATISTIC WITHOUT ITS UNCERTAINTY IS NOT A TARGET. Nineteen years contains
roughly twelve independent eighteen-month windows, so the real VR(18) is itself
a noisy estimate. `bootstrap_ci` gives every target a confidence band, and
`score` reports each achieved value in units of that band. Matching a statistic
more tightly than it can be estimated is fitting to noise, and it is the failure
mode this whole package is arranged to prevent.
"""
from __future__ import annotations

import math
import random
import statistics as st

EPS = 1e-12
DEFAULT_HORIZONS_EQUITY = (1, 3, 6, 12, 18)
DEFAULT_HORIZONS_DEBT = (1, 3, 6, 12)


# ── frames ──────────────────────────────────────────────────────────────────

def periods_per_year(dates):
    """Observations per year, MEASURED. Returns None for fewer than two dates.

    The whole reason this is a function rather than a constant is that 252 is
    right for a trading-day series and wrong by a third for the calendar-daily
    NAV series this project actually holds.
    """
    if not dates or len(dates) < 2:
        return None
    y0, y1 = int(dates[0][:4]), int(dates[-1][:4])
    m0, m1 = int(dates[0][5:7]), int(dates[-1][5:7])
    span = (y1 - y0) + (m1 - m0) / 12.0
    return len(dates) / span if span > EPS else None


def month_ends(dates, nav):
    """The last date in each calendar month for which a NAV exists."""
    by_month = {}
    for d in dates:
        if d in nav:
            k = d[:7]
            if k not in by_month or d > by_month[k]:
                by_month[k] = d
    return [by_month[k] for k in sorted(by_month)]


def log_returns(levels):
    return [math.log(levels[i] / levels[i - 1]) for i in range(1, len(levels))
            if levels[i - 1] > EPS and levels[i] > EPS]


def series_of(nav, dates):
    return [nav[d] for d in dates if d in nav]


# ── first moments ───────────────────────────────────────────────────────────

def cagr(levels, ppy):
    n = len(levels) - 1
    if n < 1 or levels[0] <= EPS:
        return None
    return (levels[-1] / levels[0]) ** (ppy / n) - 1.0


def volatility(levels, ppy):
    r = log_returns(levels)
    if len(r) < 2:
        return None
    return st.pstdev(r) * math.sqrt(ppy)


def skewness(xs):
    if len(xs) < 3:
        return None
    m, s = st.fmean(xs), st.pstdev(xs)
    if s <= EPS:
        return None
    return sum(((x - m) / s) ** 3 for x in xs) / len(xs)


def excess_kurtosis(xs):
    if len(xs) < 4:
        return None
    m, s = st.fmean(xs), st.pstdev(xs)
    if s <= EPS:
        return None
    return sum(((x - m) / s) ** 4 for x in xs) / len(xs) - 3.0


# ── term structure of variance ──────────────────────────────────────────────

def monthly_log_returns(month_levels, n):
    return [math.log(month_levels[i] / month_levels[i - n])
            for i in range(n, len(month_levels))
            if month_levels[i - n] > EPS and month_levels[i] > EPS]


def variance_ratio(month_levels, n):
    """Var(n-month) / (n * Var(1-month)).

    1.0 is a random walk, below 1 is mean reversion, above 1 is trending. THIS
    IS THE STATISTIC THE WHOLE PACKAGE TURNS ON: a redemption rule that spares
    funds trading below trend is betting on VR < 1 at its own lookback, so a
    generator that flattens the curve toward 1 will make the rule look weaker
    than it is, and the report will not mention it.
    """
    r1 = monthly_log_returns(month_levels, 1)
    rn = monthly_log_returns(month_levels, n)
    if len(r1) < 3 or len(rn) < 3:
        return None
    v1 = st.pvariance(r1)
    return st.pvariance(rn) / (n * v1) if v1 > EPS else None


def variance_ratio_curve(month_levels, horizons):
    return {n: variance_ratio(month_levels, n) for n in horizons
            if n >= 2 and variance_ratio(month_levels, n) is not None}


# ── rolling-window quantile occupancy ───────────────────────────────────────

def quantile_thresholds(values, buckets=10):
    """Interior cut points, so `buckets` bins of equal population."""
    xs = sorted(values)
    if len(xs) < buckets:
        return None
    return [xs[int((len(xs) - 1) * b / buckets)] for b in range(1, buckets)]


def occupancy(values, thresholds):
    """Percentage of `values` falling in each bin defined by `thresholds`."""
    counts = [0] * (len(thresholds) + 1)
    for v in values:
        b = 0
        while b < len(thresholds) and v >= thresholds[b]:
            b += 1
        counts[b] += 1
    n = len(values) or 1
    return [c / n * 100.0 for c in counts]


def decile_occupancy(real_month_levels, synth_month_levels, n, drift_delta=0.0,
                     buckets=10):
    """Where synthetic rolling n-month returns land in the REAL buckets.

    MEASURED ON DRIFT-ADJUSTED RETURNS, and this is not a detail. The synthetic
    series has a deliberately lower CAGR, so it MUST sit low in the real
    distribution -- the requirement to cut the drift and the requirement to
    match the quantiles are in direct contradiction unless the cut is added
    back over the window first. Scored against its own thresholds instead,
    every series scores perfectly and the test means nothing.
    """
    rr = [math.exp(x) - 1.0 for x in monthly_log_returns(real_month_levels, n)]
    thr = quantile_thresholds(rr, buckets)
    if thr is None:
        return None
    ss = []
    for x in monthly_log_returns(synth_month_levels, n):
        ss.append((math.exp(x)) * (1.0 + drift_delta) ** (n / 12.0) - 1.0)
    if not ss:
        return None
    return occupancy(ss, thr)


def occupancy_deviation(occ, buckets=10):
    """Max and total absolute deviation from a flat 100/buckets per bin."""
    if occ is None:
        return None, None
    flat = 100.0 / buckets
    dev = [abs(o - flat) for o in occ]
    return max(dev), sum(dev)


# ── volatility memory ───────────────────────────────────────────────────────

def autocorrelation(xs, lag):
    n = len(xs)
    if n <= lag + 2:
        return None
    m = st.fmean(xs)
    num = sum((xs[i] - m) * (xs[i - lag] - m) for i in range(lag, n))
    den = sum((x - m) ** 2 for x in xs)
    return num / den if den > EPS else None


def acf_abs_decay_exponent(returns, max_lag=126, min_lag=1):
    """Power-law exponent beta in ACF_|r|(k) ~ k^-beta, by log-log fit.

    Only positive autocorrelations enter the fit; a negative value has no
    logarithm and including it by taking an absolute value would turn evidence
    against clustering into evidence for it.
    """
    pts = []
    a = [abs(r) for r in returns]
    for k in range(max(1, min_lag), max_lag + 1):
        c = autocorrelation(a, k)
        if c is not None and c > EPS:
            pts.append((math.log(k), math.log(c)))
    if len(pts) < 5:
        return None
    mx = st.fmean([p[0] for p in pts])
    my = st.fmean([p[1] for p in pts])
    den = sum((p[0] - mx) ** 2 for p in pts)
    if den <= EPS:
        return None
    slope = sum((p[0] - mx) * (p[1] - my) for p in pts) / den
    return -slope


def hurst_exponent(xs, min_lag=10, max_lag=300, step=10):
    """Rescaled-range estimate on the sequence given (pass |r| for volatility).

    Returns None rather than 0.5 when there is not enough data. A silent 0.5 is
    indistinguishable from a real measurement of no long memory, and the
    upstream engine returned exactly that on every synthetic path it produced.
    """
    n = len(xs)
    if n < 200:
        return None
    pts = []
    for lag in range(min_lag, min(n // 2, max_lag) + 1, step):
        rs = []
        for c in range(n // lag):
            sub = xs[c * lag:(c + 1) * lag]
            m = st.fmean(sub)
            dev, lo, hi = 0.0, 0.0, 0.0
            for v in sub:
                dev += v - m
                lo, hi = min(lo, dev), max(hi, dev)
            s = st.pstdev(sub)
            if s > EPS:
                rs.append((hi - lo) / s)
        if rs:
            pts.append((math.log(lag), math.log(st.fmean(rs))))
    if len(pts) < 3:
        return None
    mx = st.fmean([p[0] for p in pts])
    my = st.fmean([p[1] for p in pts])
    den = sum((p[0] - mx) ** 2 for p in pts)
    if den <= EPS:
        return None
    return sum((p[0] - mx) * (p[1] - my) for p in pts) / den


# ── asymmetry ───────────────────────────────────────────────────────────────

def leverage_correlation(returns, k):
    """Corr(r_t, |r_{t+k}|). Negative in real equity for small k."""
    n = len(returns)
    if n <= k + 3:
        return None
    x = returns[:n - k]
    y = [abs(r) for r in returns[k:]]
    mx, my = st.fmean(x), st.fmean(y)
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    dx = math.sqrt(sum((a - mx) ** 2 for a in x))
    dy = math.sqrt(sum((b - my) ** 2 for b in y))
    return num / (dx * dy) if dx > EPS and dy > EPS else None


def leverage_profile(returns, lags=range(1, 21)):
    return {k: leverage_correlation(returns, k) for k in lags}


# ── drawdown and recovery ───────────────────────────────────────────────────

def underwater_durations(levels):
    """Lengths of each spell below the running maximum, in observations.

    An UNFINISHED spell at the end of the series is returned separately, not
    folded in. A path that ends 40% under water has one enormous incomplete
    spell, and counting it as a completed recovery would report the opposite of
    what happened.
    """
    peak, run, out = levels[0], 0, []
    for p in levels:
        if p >= peak - EPS:
            if run:
                out.append(run)
            run, peak = 0, p
        else:
            run += 1
    return out, run


def max_drawdown(levels):
    peak, worst = levels[0], 0.0
    for p in levels:
        peak = max(peak, p)
        if peak > EPS:
            worst = min(worst, p / peak - 1.0)
    return worst


def negative_run_distribution(returns):
    """P(run of consecutive negative returns == k), as a dict."""
    runs, cur = [], 0
    for r in returns:
        if r < 0:
            cur += 1
        elif cur:
            runs.append(cur)
            cur = 0
    if cur:
        runs.append(cur)
    if not runs:
        return {}
    out = {}
    for r in runs:
        out[r] = out.get(r, 0) + 1
    return {k: v / len(runs) for k, v in sorted(out.items())}


def expected_shortfall(returns, q=0.99):
    """Mean of the worst (1-q) tail. Positive number = size of the loss."""
    if not returns:
        return None
    xs = sorted(returns)
    cut = max(1, int(len(xs) * (1.0 - q)))
    return -st.fmean(xs[:cut])


# ── uncertainty ─────────────────────────────────────────────────────────────

def moving_block_ci(levels, fn, block, draws=200, alpha=0.10, seed=0):
    """Confidence band for `fn(levels)` by moving-block bootstrap.

    THE POINT OF THIS FUNCTION IS TO STOP US MATCHING NOISE. The real VR(18)
    reads 0.85, and across four synthetic seeds at one block length the same
    statistic moved by half a point. Without a band around the target there is
    no way to tell a generator that missed from a generator that is inside the
    only precision the data supports.
    """
    r = log_returns(levels)
    n = len(r)
    if n < block * 3:
        return None, None
    rng = random.Random(seed)
    vals = []
    for _ in range(draws):
        out = []
        while len(out) < n:
            s = rng.randrange(0, n - block)
            out.extend(r[s:s + block])
        out = out[:n]
        lv = [levels[0]]
        for x in out:
            lv.append(lv[-1] * math.exp(x))
        v = fn(lv)
        if v is not None:
            vals.append(v)
    if len(vals) < 10:
        return None, None
    vals.sort()
    lo = vals[int(len(vals) * alpha / 2)]
    hi = vals[int(len(vals) * (1 - alpha / 2)) - 1]
    return lo, hi


# ── the vector ──────────────────────────────────────────────────────────────

def measure(nav, dates, horizons=None, is_equity=True):
    """Every statistic, as one dict. `None` where the data cannot support it.

    Returning None rather than a plausible default is deliberate: a Hurst
    exponent silently reported as 0.5 is indistinguishable from a measurement
    of no long memory, and that is precisely how the upstream engine reported
    every synthetic path it produced.
    """
    levels = series_of(nav, dates)
    if len(levels) < 30:
        return {}
    ppy = periods_per_year([d for d in dates if d in nav]) or 252.0
    r = log_returns(levels)
    me = month_ends(dates, nav)
    ml = [nav[d] for d in me]
    hz = horizons or (DEFAULT_HORIZONS_EQUITY if is_equity
                      else DEFAULT_HORIZONS_DEBT)
    done, unfinished = underwater_durations(levels)
    return dict(
        n_obs=len(levels), periods_per_year=ppy, n_months=len(ml),
        cagr=cagr(levels, ppy), vol=volatility(levels, ppy),
        skew=skewness(r), kurtosis=excess_kurtosis(r),
        vr={n: variance_ratio(ml, n) for n in hz if n >= 2},
        acf_beta=acf_abs_decay_exponent(r),
        hurst=hurst_exponent([abs(x) for x in r]),
        leverage={k: leverage_correlation(r, k) for k in (1, 5, 10, 20)},
        max_drawdown=max_drawdown(levels),
        ttr=done, ttr_unfinished=unfinished,
        ttr_median=(st.median(done) if done else None),
        neg_runs=negative_run_distribution(r),
        es99=expected_shortfall(r),
    )
