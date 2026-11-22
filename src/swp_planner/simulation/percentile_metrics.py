# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
percentile_metrics.py
=====================
Through-the-cycle return metrics built from rolling NAV windows.

Two quantities live here.

RISK-FREE RATE
    The through-the-cycle yield of liquid funds.  For every liquid fund,
    take the rolling N-month CAGR series; align those series by window END
    DATE; average across funds within each calendar month; then take the
    Pth percentile of the resulting monthly series.

    Average first, percentile second.  The order matters and is not
    interchangeable: percentile-then-average would ask "what does a typical
    fund do in its own bad periods", which is a statement about fund
    dispersion.  Average-then-percentile asks "what did liquid funds as a
    class return in a bad stretch", which is a statement about the rate
    cycle -- and the rate cycle is what liquid funds actually track.

PER-FUND EXPECTED RETURN
    For each of the 3Y / 5Y / 7Y / 10Y rolling series, take the Pth
    percentile; the fund's expected return is the lowest of the four.

WHY A PERCENTILE AT ALL
-----------------------
`get_funds_data.py` already exports the mean of each rolling series
(`Avg_CAGR_*`) and its minimum (`Min_CAGR_*`).  The mean is too kind -- it
averages the good windows in.  The minimum is one window, and one bad month
in the NAV history can own it.  A percentile sits between them and is stable
against both.

At P33 there is roughly a two-in-three chance the realised rate is higher,
which is the intended bias: a plan that comes in ahead of projection is a
better failure mode than one that comes in behind.

WHAT THE NUMBERS LOOK LIKE
--------------------------
Measured on Fund_Metrics_Output.csv, 30 liquid funds, the bracket a
percentile must fall inside:

    window   mean of Avg_CAGR   mean of Min_CAGR   spread
      3Y          5.76%              3.68%         2.08pp
      5Y          5.59%              4.66%         0.93pp
      7Y          5.65%              5.12%         0.52pp
     10Y          6.38%              6.01%         0.37pp

The 3Y spread is five times the 10Y spread because a three-year window can
sit entirely inside one phase of the rate cycle, while a ten-year window
always straddles several.  The immediate `3Y_CAGR` averages 6.42%, so the
through-the-cycle figure is materially below what liquid funds pay today --
by design.  It is a floor, not a quote.

RESOLUTION WARNING
------------------
With ten years of monthly history the 3Y series has ~85 readings and the 10Y
series has ~13.  P33 of thirteen readings is the fourth value; it moves
visibly if one window changes.  Since the per-fund metric takes the MIN of
the four percentiles, an unstable long-window value can be the one selected.
`min_readings` guards this: series with too few windows are excluded rather
than trusted.

This module holds no I/O and no NAV fetching, so all of it is testable
without a network.  `get_funds_data.py` supplies the series.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "DEFAULT_PERCENTILE",
    "MIN_READINGS",
    "configured",
    "LIQUID_MARKERS",
    "EXCLUDED_MARKERS",
    "is_liquid_fund",
    "percentile_of",
    "cross_fund_monthly_average",
    "risk_free_from_liquid_funds",
    "fund_expected_return",
    "risk_free_ladder",
]

DEFAULT_PERCENTILE = 33.0

# Minimum rolling windows a series must have before its percentile is worth
# quoting.  Ten years of monthly history gives the 3Y series ~85 readings and
# the 10Y series ~13; a percentile of thirteen is the fourth value.  Because
# the per-fund metric takes a MIN across the four windows, an under-resolved
# long window is selected precisely when its noise happens to be low.
#
# Applied at the point of MEASUREMENT (get_funds_data emits nothing for a
# window below its threshold) so the CSV carries only figures worth
# trusting, and FundEntry -- which stores four scalars and no counts --
# never has to re-derive a judgement it lacks the data to make.
MIN_READINGS = {"3Y": 24, "5Y": 18, "7Y": 12, "10Y": 12}

# Matched as a substring, case-insensitively, against the AMFI "Fund Type"
# and then the fund name.  Deliberately NOT the full "Debt Scheme - Liquid
# Fund": AMFI has reorganised its scheme-category prefixes before and may
# again, but "Liquid Fund" is the regulatory category name itself and is not
# going to change.
LIQUID_MARKERS = ("liquid",)

# Adjacent categories that are NOT liquid funds. Money Market and Overnight
# are different instruments with different yields, and "Low Duration" reads
# liquid but is not. Listed explicitly so the boundary is visible in the
# source rather than implied by what the marker above happens to miss.
EXCLUDED_MARKERS = ("money market", "overnight", "low duration",
                    "ultra short", "short duration")


def is_liquid_fund(fund_type: Optional[str], name: Optional[str] = None) -> bool:
    """True for AMFI 'Liquid Fund' schemes.

    Checks the fund type first, because it is the regulatory category and
    unambiguous; falls back to the name only when the type is absent, which
    is the case for any FundEntry loaded before the importer started reading
    the Fund Type column.

    'DSP Liquidity Fund' is a Liquid Fund whose name says Liquidity, so the
    substring match catches it either way.

    The type DECIDES when it is present.  Falling through to the name when
    the type is merely unrecognised -- which is what this did -- means a
    scheme categorised as a Corporate Bond Fund but marketed as something
    "Liquid" is counted as liquid, and the risk-free rate is then measured
    over a category it does not describe.  The name is a fallback for
    missing data, not a second opinion about a category the regulator has
    already assigned.
    """
    candidate = fund_type if fund_type else name
    if not candidate:
        return False
    text = str(candidate).lower()
    if any(bad in text for bad in EXCLUDED_MARKERS):
        return False
    return any(good in text for good in LIQUID_MARKERS)


def percentile_of(values: Sequence[float], percentile: float,
                  min_readings: int = 1) -> Optional[float]:
    """Linear-interpolated Pth percentile, or None if there is too little data.

    Returns None rather than a number when `values` is shorter than
    `min_readings`: a percentile of three readings is not a percentile, and
    silently returning one invites it to be treated as though it were.
    """
    clean = [float(v) for v in values if v is not None and v == v]  # drop NaN
    if len(clean) < max(1, min_readings):
        return None
    if not 0.0 <= percentile <= 100.0:
        raise ValueError(f"percentile must be in [0, 100], got {percentile!r}")

    clean.sort()
    if len(clean) == 1:
        return clean[0]
    pos = (percentile / 100.0) * (len(clean) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(clean) - 1)
    frac = pos - lo
    return clean[lo] + frac * (clean[hi] - clean[lo])


def cross_fund_monthly_average(
    per_fund_series: Mapping[str, Sequence[Tuple[object, float]]],
    min_funds: int = 10,
) -> List[Tuple[object, float]]:
    """Average rolling-window values across funds, keyed by window end date.

    `per_fund_series` maps fund name -> [(end_date, value), ...].  Series are
    aligned by DATE, never by position: fund A's tenth window and fund B's
    tenth window are different calendar months whenever the two funds have
    different inception dates, and averaging them positionally would be
    meaningless.

    Months covered by fewer than `min_funds` funds are dropped.  Early months
    are typically covered by a handful of long-lived schemes whose average is
    a statement about those schemes rather than about the category; including
    them would let three funds set the floor for the whole plan.

    Returns [(date, mean)] sorted by date.
    """
    if min_funds < 1:
        raise ValueError(f"min_funds must be >= 1, got {min_funds!r}")

    buckets: Dict[object, List[float]] = defaultdict(list)
    for series in per_fund_series.values():
        for date, value in series:
            if value is not None and value == value:
                buckets[date].append(float(value))

    return [
        (date, sum(vals) / len(vals))
        for date, vals in sorted(buckets.items())
        if len(vals) >= min_funds
    ]


def risk_free_from_liquid_funds(
    per_fund_3y_series: Mapping[str, Sequence[Tuple[object, float]]],
    percentile: float = DEFAULT_PERCENTILE,
    min_funds: int = 10,
    min_readings: int = 24,
) -> Optional[float]:
    """The through-the-cycle liquid-fund yield.

    Average across funds per month, then take the Pth percentile of that
    monthly series -- in that order; see the module docstring.

    Returns None when the inputs cannot support the calculation, so the
    caller can leave the configured value alone rather than overwrite it with
    something built from four funds and six months.
    """
    monthly = cross_fund_monthly_average(per_fund_3y_series, min_funds=min_funds)
    return percentile_of([v for _d, v in monthly], percentile,
                         min_readings=min_readings)


def fund_expected_return(
    rolling_by_window: Mapping[str, Sequence[float]],
    percentile: float = DEFAULT_PERCENTILE,
    min_readings: Mapping[str, int] | None = None,
) -> Optional[float]:
    """Lowest Pth percentile across a fund's 3Y/5Y/7Y/10Y rolling series.

    `rolling_by_window` maps a window label ("3Y", "5Y", ...) to that
    window's rolling CAGR series.

    Windows with too few readings are skipped rather than allowed to win the
    min() on the strength of a dozen data points -- see the resolution
    warning in the module docstring.  Returns None if no window qualifies.
    """
    floors = min_readings or MIN_READINGS
    candidates = []
    for label, series in rolling_by_window.items():
        p = percentile_of(series, percentile,
                          min_readings=floors.get(label, 12))
        if p is not None:
            candidates.append(p)
    return min(candidates) if candidates else None


# ── configured defaults ──────────────────────────────────────────────────────

def risk_free_ladder(
    per_fund_3y_series: Mapping[str, Sequence[Tuple[object, float]]],
    percentiles: Sequence[float],
    min_funds: int = 10,
    min_readings: int = 24,
) -> Optional[Dict[float, float]]:
    """Several percentiles of ONE liquid-fund monthly series.

    Returns {percentile: rate}, or None if the inputs cannot support the
    calculation -- the same refusal contract as risk_free_from_liquid_funds().

    Why one function rather than calling that one repeatedly: every rate in
    the ladder must come from the same monthly average, computed once. Three
    separate calls would each rebuild the series, and any future change to
    the filtering that was applied inconsistently would produce a ladder
    whose rungs describe different fund sets. They would still look like a
    ladder.

    The result is checked for monotonicity. A lower percentile yielding a
    HIGHER rate is arithmetically impossible on one series, so if it happens
    the series differs between rungs and the whole ladder is refused rather
    than half-trusted.
    """
    monthly = cross_fund_monthly_average(per_fund_3y_series, min_funds=min_funds)
    values = [v for _d, v in monthly]

    out: Dict[float, float] = {}
    for pct in percentiles:
        rate = percentile_of(values, pct, min_readings=min_readings)
        if rate is None:
            return None
        out[float(pct)] = rate

    ordered = [out[float(p)] for p in sorted(out, reverse=True)]
    if any(a < b for a, b in zip(ordered, ordered[1:])):
        raise ValueError(
            f"risk-free ladder is not monotonic: {out}. A lower percentile "
            "cannot yield a higher rate on a single series, so the rungs are "
            "not describing the same data."
        )
    return out


def configured() -> Dict[str, float]:
    """Resolve the tunables from RetirementTaxPlanning.configuration.

    The functions above take everything explicitly and touch no global state,
    which is what makes them testable without a config file at all.  This is
    the single place that reads the keys, so callers do not each grow their
    own `config.get(..., 33)` with a different fallback.

        risk_free_rate_percentile       applied to the liquid-fund monthly
                                        series; the plan's base anchor
        risk_free_rate_mid_percentile   the middle rung of the FD benchmark
                                        ladder
        risk_free_rate_low_percentile   the lowest rung
        fund_return_percentile          applied to each fund's rolling series
        risk_free_min_funds             months below this fund count are
                                        dropped

    The three rate percentiles are separate keys on purpose. They answer
    different questions -- what a plan should assume it earns, versus what a
    notional FD comparator should be credited with a decade or two out -- and
    a single knob would tie them together silently.
    """
    from swp_planner.config.configuration import config
    return {
        "risk_free_rate_percentile": float(config.risk_free_rate_percentile),
        "risk_free_rate_mid_percentile": float(config.risk_free_rate_mid_percentile),
        "risk_free_rate_low_percentile": float(config.risk_free_rate_low_percentile),
        "fund_return_percentile": float(config.fund_return_percentile),
        "risk_free_min_funds": int(config.risk_free_min_funds),
    }
