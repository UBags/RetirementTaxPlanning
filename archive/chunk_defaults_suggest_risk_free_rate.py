# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
ARCHIVED — chunk_defaults.suggest_risk_free_rate()

Removed from chunk_defaults.py.  Superseded by
percentile_metrics.risk_free_from_liquid_funds().

WHY IT WENT
-----------
This computed the risk-free rate as the mean of min(Avg_CAGR_*) across funds
whose NAME contained "liquid", "overnight" or "money market".  Three things
about that turned out to be wrong once the method was specified properly:

  1. WRONG METRIC.  min(Avg_CAGR_*) is the lowest of four means-of-rolling-
     windows.  The agreed measure is the 33rd percentile of the cross-fund
     monthly average of the rolling 3Y CAGR series — average across funds
     first, percentile second.  Different number, and only the second is a
     statement about the rate cycle.
  2. WRONG CLASSIFIER.  Matching on the fund name was a fallback pressed into
     service as the primary route, because models.import_fund_metrics() never
     read the "Fund Type" column.  AMFI publishes an exact category and
     Fund_Metrics_Output.csv carries it in column 1.
  3. WRONG SET.  It swept Overnight and Money Market funds in with Liquid
     funds.  They are different instruments with different yields.

It also could not have worked as designed: the cross-fund averaging needs the
aligned monthly rolling series, and a FundEntry only ever holds per-fund
summary statistics.

KEPT BECAUSE
------------
The name-matching fallback is still the only route available for a FundEntry
loaded from a project file that predates the Fund Type import, and the shape
of the "audit the config value against loaded data" idea is worth having on
record even though the arithmetic inside it was replaced.

NOT IMPORTABLE AS-IS.  Nothing calls this and nothing should; it is here to
be read, not run.
"""

from typing import Iterable, List, Optional


# ── Checking the anchor against reality ──────────────────────────────────────

_LIQUID_MARKERS = ("liquid", "overnight", "money market")


def suggest_risk_free_rate(funds: Iterable[object]) -> Optional[float]:
    """What ``config.risk_free_rate`` should say, from loaded fund metrics.

    Returns a FRACTION, or None when no liquid fund carries usable data.

    Nothing calls this automatically — see the module docstring on why the
    rate is stored rather than computed.  It exists so the config key can be
    audited instead of trusted: run it after importing a fund CSV and compare.

    Liquid funds are identified by name, because ``FundEntry`` records only a
    coarse ``fund_type`` ("debt") and an ``amfi_fund_type`` that is not
    guaranteed to be populated.  Both are checked; the name is the fallback.
    The per-fund figure is ``min(Avg_CAGR_*)``, the same metric §2.1 put the
    engine on, so the suggestion is measured the way everything else is.
    """
    rates: List[float] = []
    for f in funds:
        haystack = " ".join(
            str(getattr(f, attr, "") or "") for attr in ("name", "amfi_fund_type")
        ).lower()
        if not any(marker in haystack for marker in _LIQUID_MARKERS):
            continue
        avgs = [
            c for c in (
                getattr(f, "avg_cagr_3", None), getattr(f, "avg_cagr_5", None),
                getattr(f, "avg_cagr_7", None), getattr(f, "avg_cagr_10", None),
            )
            if c is not None and c > 0
        ]
        if avgs:
            rates.append(min(avgs) / 100.0)

    if not rates:
        return None
    return sum(rates) / len(rates)
