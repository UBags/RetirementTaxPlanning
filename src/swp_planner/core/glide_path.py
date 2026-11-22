# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
glide_path.py
=============
Builds the year-by-year portfolio weight schedule (GlidePath) from the
per-chunk target_weights produced by the sticky-portfolio optimizer.

Public API
----------
build_glide_path(chunks, spread_years)  → GlidePath
    Constructs a GlidePath that:
      • Holds chunk.target_weights flat within the stable interior of each chunk.
      • Linearly interpolates weights across a transition window centred on each
        chunk boundary, spreading the rebalance over ``spread_years`` years to
        minimise tax incidence and exit loads.
      • Handles edge cases: first year is a clean buy at Chunk 1 weights (no
        ramp-in); last year holds Chunk N weights until end-of-life (no ramp-out).
      • Truncates overlapping windows symmetrically when chunks are short.

build_flat_glide_path(weights, horizon)  → GlidePath
    Returns a GlidePath where every year (1..horizon) maps to the same weight
    dict.  Used for Mode A (buy-and-hold) and single-chunk plans.

HORIZON
-------
Both builders take *horizon* as an optional keyword defaulting to
``config.max_simulation_years``.  Neither can reach an AppState, so the plan
length has to arrive as an argument; making it optional keeps every existing
caller -- including the engine tests and the archived v0.9 allocator --
producing byte-identical schedules.  Pass ``state.horizon`` from anywhere that
has a state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from swp_planner.core.models import GlidePath


# ─────────────────────────────────────────────────────────────────────────────
# Public helpers
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_horizon(horizon: "int | None") -> int:
    """Plan length in financial years, defaulting to the configured ceiling."""
    if horizon is None:
        from swp_planner.config.configuration import config
        horizon = int(config.max_simulation_years)
    return max(1, int(horizon))


def build_flat_glide_path(weights: dict, horizon: "int | None" = None) -> "GlidePath":
    """
    Return a GlidePath where every plan year (1..horizon) carries the same
    weight dict.  Used for Mode A (singular buy-and-hold).
    """
    from swp_planner.core.models import GlidePath
    horizon = _resolve_horizon(horizon)
    w = dict(weights)   # defensive copy
    return GlidePath(schedule={y: w for y in range(1, horizon + 1)})


def _weights_from_funds(chunk) -> dict:
    """
    Derive normalised target_weights from chunk.funds[].allocation.
    Used as a fallback when the Two-Pass optimizer has not been run yet
    (target_weights is empty).  Returns {} if no funds have allocation > 0.
    """
    total = sum(f.allocation for f in (chunk.funds or []) if f.allocation > 0)
    if total < 1e-9:
        return {}
    return {
        f.name: f.allocation / total
        for f in chunk.funds
        if f.allocation > 0
    }


def _effective_weights(chunk) -> dict:
    """
    Return target_weights if populated, otherwise fall back to weights
    derived from chunk.funds[].allocation.
    Guarantees a non-empty dict if the chunk has any funded assets.
    """
    tw = chunk.target_weights or {}
    if tw:
        return dict(tw)
    return _weights_from_funds(chunk)


def build_glide_path(
    chunks:       list,   # list[AllocationChunk]
    spread_years: int,    # total years spanning each boundary transition
    horizon: "int | None" = None,   # plan length in FYs; None -> config ceiling
) -> "GlidePath":
    """
    Build a year-by-year WITHDRAWAL-WEIGHT schedule from per-chunk target_weights.

    Conceptual model
    ----------------
    The glide path defines the target portfolio state for each year.
    By LINEARLY INTERPOLATING the weights over the `spread_years` window, the
    engine's `_rebalance_portfolio` function calculates a small delta each year.

    Because the SWP engine natively prioritises selling "over-weight" funds,
    the natural monthly withdrawals do the heavy lifting of transitioning the
    portfolio from the old chunk to the new chunk. This completely avoids
    massive one-time Capital Gains Tax events.

    Parameters
    ----------
    chunks : list[AllocationChunk]
        Sorted by year_from ascending (as stored in AppState).
    spread_years : int
        Total width of each transition window.  Must be >= 2.
    horizon : int, optional
        Plan length in financial years.  Defaults to
        ``config.max_simulation_years``.  Pass ``state.horizon``.

    Returns
    -------
    GlidePath
    """
    from swp_planner.core.models import GlidePath

    horizon = _resolve_horizon(horizon)

    if not chunks:
        return GlidePath(schedule={y: {} for y in range(1, horizon + 1)})

    if len(chunks) == 1:
        w = _effective_weights(chunks[0])
        return GlidePath(schedule={y: w for y in range(1, horizon + 1)})

    # Every year carries its owning chunk's weights.
    #
    # The linear interpolation that used to run here was retired in Phase 2.
    # Measured on the golden fixtures it smoothed gain across progressive
    # slabs rather than harvesting free capacity — it turned two zero-tax
    # years into 10.75L of tax while ~21L of headroom sat unused in each.
    # Its replacement schedules against each year's remaining tax-free
    # headroom instead. See HANDOVER §4.3.
    #
    # `spread_years` is accepted and ignored: callers in main.py and
    # allocate_funds.py still pass state.rebalance_spread_years, and the
    # parameter will mean something again when Phase 6 lands.
    schedule = {}
    for y in range(1, horizon + 1):
        for c in chunks:
            if c.year_from <= y <= c.year_to:
                schedule[y] = _effective_weights(c)
                break
        else:
            schedule[y] = _effective_weights(chunks[-1])
    return GlidePath(schedule=schedule)
