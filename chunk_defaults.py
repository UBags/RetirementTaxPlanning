# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
chunk_defaults.py
=================
Default allocation-chunk structures per Option, and the risk-free rate they
are anchored to.  Implements HANDOVER §2.4.

WHAT THIS REPLACES
------------------
Before this module, every allocation Option opened with a single chunk
spanning the whole horizon and a `min_return` of 6.85 hardcoded in four
places in main.py.  Nothing about 6.85 was written down: it was neither
derived from anything nor documented, and the user had to rebuild the same
chunk layout by hand for every Option, every time.

THE ANCHOR
----------
`min_return` is now `risk_free_rate + spread`, where **risk_free_rate is the
yield offered by liquid funds** — the owner's definition, and the right one:
it is observable, it is what idle money actually earns, and unlike a
per-scenario FD rate it does not vary between plans.

It lives in `RetirementTaxPlanning.configuration` rather than being computed
at runtime.  That is deliberate.  A default that moved every time a fund CSV
was reimported would make plans irreproducible: open a saved project six
months later and the chunk structure it was built against would no longer
exist.  It is measured and written by the fund importer -- see
percentile_metrics.risk_free_from_liquid_funds(), which takes the 33rd
percentile of the cross-fund monthly average of liquid-fund rolling 3Y CAGR.
An earlier draft of that measurement lived here; it is in
archive/chunk_defaults_suggest_risk_free_rate.py with a note on why it went.

THE SPREADS
-----------
Longer chunks carry more duration risk, so they demand more over the
risk-free rate.  Within an Option, earlier chunks are longer-horizon money
and take the wider spread; the final chunk is closest to being spent and
takes the narrowest.

    Option 1   1 chunk    FY1-30                         rf + 1.25
    Option 2   2 chunks   FY1-15, FY16-30                rf + 1.60 / 1.00
    Option 3   3 chunks   FY1-10, FY11-20, FY21-30       rf + 1.90 / 1.50 / 1.00
    Option 4   4 chunks   FY1-7, FY8-15, FY16-23, FY24-30  rf + 2.10 / 1.60 / 1.25 / 1.00

UNITS — READ THIS BEFORE CHANGING ANYTHING
-------------------------------------------
The codebase is inconsistent here and this module has to straddle it:

    config.risk_free_rate   FRACTION   0.0665   (like cess_rate, drift_cap_*)
    spreads below           PERCENT    1.25
    min_return returned     PERCENT    7.90     (matches main.py's 6.85)

`main.py` builds chunk-definition dicts with `"min_return": 6.85` and
`dialogs.py` reads them back with `getattr(chunk, 'min_return', 0.0685)` —
note that fallback is a *fraction* while the dicts hold *percent*, which is a
live inconsistency this module does not attempt to fix.  It is recorded here
because anyone wiring this into the GUI will meet it.

HORIZON
-------
Boundaries are derived by scaling the canonical 30-year layout, so a project
with `max_simulation_years` other than 30 still gets sensible chunks instead
of silently keeping FY24-30 on a 20-year plan.  At 30 the scaling is exact
and reproduces the table above literally — `test_chunk_defaults.py` pins that.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

__all__ = [
    "CANONICAL_HORIZON",
    "SPREADS_PCT",
    "default_chunk_layout",
    "default_chunks_for_option",
]

CANONICAL_HORIZON = 30

# Chunk end-years for the canonical 30-year horizon, per Option.
_CANONICAL_ENDS: Dict[int, Tuple[int, ...]] = {
    1: (30,),
    2: (15, 30),
    3: (10, 20, 30),
    4: (7, 15, 23, 30),
}

# Percentage points above the risk-free rate, per Option, per chunk.
SPREADS_PCT: Dict[int, Tuple[float, ...]] = {
    1: (1.25,),
    2: (1.60, 1.00),
    3: (1.90, 1.50, 1.00),
    4: (2.10, 1.60, 1.25, 1.00),
}


def default_chunk_layout(n_chunks: int, horizon: Optional[int] = None) -> List[Tuple[int, int]]:
    """Return [(year_from, year_to), ...] for *n_chunks* over *horizon* years.

    *horizon* defaults to ``config.max_simulation_years``.  Boundaries are the
    canonical 30-year ones scaled proportionally, then forced strictly
    increasing so a short horizon cannot produce an empty or inverted chunk.
    At horizon 30 the result is exactly the table in the module docstring.
    """
    if n_chunks not in _CANONICAL_ENDS:
        raise ValueError(
            f"n_chunks must be one of {sorted(_CANONICAL_ENDS)}, got {n_chunks!r}"
        )
    if horizon is None:
        from configuration import config
        horizon = int(config.max_simulation_years)
    if horizon < n_chunks:
        raise ValueError(
            f"horizon {horizon} is shorter than {n_chunks} chunks; "
            "each chunk needs at least one financial year"
        )

    ends: List[int] = []
    for i, canonical_end in enumerate(_CANONICAL_ENDS[n_chunks]):
        scaled = round(canonical_end * horizon / CANONICAL_HORIZON)
        # strictly increasing, and leave room for the chunks still to come
        scaled = max(scaled, len(ends) + 1)
        scaled = min(scaled, horizon - (n_chunks - i - 1))
        ends.append(scaled)
    ends[-1] = horizon

    layout, start = [], 1
    for end in ends:
        layout.append((start, end))
        start = end + 1
    return layout


def default_chunks_for_option(
    n_chunks: int,
    risk_free_rate: Optional[float] = None,
    horizon: Optional[int] = None,
) -> List[Dict[str, object]]:
    """Chunk-definition dicts for an Option, ready for the allocation dialog.

    Parameters
    ----------
    n_chunks : 1..4 — the Option number.
    risk_free_rate : FRACTION (0.0665).  Defaults to ``config.risk_free_rate``.
    horizon : financial years.  Defaults to ``config.max_simulation_years``.

    Returns dicts shaped like the ones main.py already builds, with
    ``min_return`` in PERCENT.
    """
    if risk_free_rate is None:
        from configuration import config
        risk_free_rate = float(config.risk_free_rate)
    if not 0.0 < risk_free_rate < 1.0:
        raise ValueError(
            f"risk_free_rate must be a fraction in (0, 1), got {risk_free_rate!r}. "
            "It is the liquid-fund yield, e.g. 0.0665 for 6.65%."
        )

    rf_pct = risk_free_rate * 100.0
    layout = default_chunk_layout(n_chunks, horizon)
    spreads = SPREADS_PCT[n_chunks]

    return [
        {
            "year_from": yr_from,
            "year_to": yr_to,
            "min_return": round(rf_pct + spread, 4),
            "spread": spread,
        }
        for (yr_from, yr_to), spread in zip(layout, spreads)
    ]


def describe_option(n_chunks: int, risk_free_rate: Optional[float] = None,
                    horizon: Optional[int] = None) -> str:
    """One-line-per-chunk summary, for logs and tooltips."""
    chunks = default_chunks_for_option(n_chunks, risk_free_rate, horizon)
    return "\n".join(
        f"FY{c['year_from']}-{c['year_to']}: min_return {c['min_return']:.2f}% "
        f"(rf + {c['spread']:.2f})"
        for c in chunks
    )
