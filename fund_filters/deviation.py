# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""deviation.py -- sustained deviation from self, family and peers.

WHAT THIS IS FOR, STATED NARROWLY BECAUSE THE WIDE VERSION IS FALSE. This ranks
held funds for redemption priority. It does not predict credit events, and the
measurement that settles that is in amc_registry.py: over the eighteen months
before DHFL and IL&FS, the five affected funds' trailing returns against their
category medians ran -0.29pp to +0.32pp, inside a control group averaging
-0.19pp with a standard deviation of 0.45pp. There was nothing to see.

What this DOES catch is the aftermath, quickly, and persistent decay. Both
matter for a withdrawal plan: when something has gone wrong in a fund, the
question stops being "will it recover" and becomes "which unit do I sell this
month", and that question has an answer even when prediction does not.

THREE REFERENCE POINTS, BECAUSE THEY FAIL DIFFERENTLY.

    SELF. Trailing return against the fund's OWN long-run median. Catches a
    fund that has decayed absolutely -- a debt fund whose yield has collapsed
    -- but is blind to a whole category falling together, which is exactly the
    case where selling is worst.

    PEERS. Against the median of the same AMFI category. Catches relative
    decay and is the workhorse. Blind to a category-wide problem, and noisy
    when a category holds few funds.

    FAMILY. Against the median of other schemes from the SAME AMC. The one
    that carries information the others cannot: a house problem -- a credit
    call gone wrong, a manager departure, a liquidity squeeze -- shows up
    across an AMC's schemes at once, and neither self nor peer comparison sees
    that as anything but bad luck.

PERSISTENCE IS THE WHOLE POINT. A single bad month is noise; this project has
already been burned twice by treating one observation as a finding. A signal
fires only after `min_months` CONSECUTIVE months beyond the threshold, and the
consecutive count is reported so a two-month reading is never mistaken for a
ten-month one.

THRESHOLDS ARE IN ROBUST UNITS, NOT PERCENTAGES. Deviation is scaled by the
cross-sectional MAD of the comparison set, so a 30bp gap means something very
different in a liquid fund than in an equity fund and the same number can be
used for both.
"""
from __future__ import annotations

import statistics as st

DEFAULT_LOOKBACK = 12          # months in the trailing return
DEFAULT_MIN_MONTHS = 3         # consecutive breaches before a signal fires
DEFAULT_THRESHOLD = -2.0       # robust z below the reference
MIN_COMPARATORS = 3


def trailing_return(nav, month_ends, i, lookback):
    if i < lookback:
        return None
    a, b = nav.get(month_ends[i - lookback]), nav.get(month_ends[i])
    return b / a - 1.0 if a and b else None


def robust_z(values, x):
    """Median/MAD. Returns None when the comparison set cannot support one."""
    vals = [v for v in values if v is not None]
    if len(vals) < MIN_COMPARATORS or x is None:
        return None
    med = st.median(vals)
    mad = st.median([abs(v - med) for v in vals])
    if mad <= 1e-12:
        return None
    return (x - med) / (1.4826 * mad)


def series_z(funds, name, month_ends, lookback=DEFAULT_LOOKBACK,
             reference="peers"):
    """Robust z of one fund against its reference, month by month.

    `funds` maps name -> dict(nav=..., category=..., amc=...).
    `reference` is 'peers' (same category), 'family' (same AMC) or 'self'.
    """
    me = month_ends
    f = funds[name]
    if reference == "peers":
        comp = [k for k, v in funds.items()
                if v.get("category") == f.get("category") and k != name]
    elif reference == "family":
        comp = [k for k, v in funds.items()
                if v.get("amc") and v.get("amc") == f.get("amc") and k != name]
    elif reference == "self":
        comp = []
    else:
        raise ValueError(f"unknown reference {reference!r}")

    out = []
    hist = []
    for i, m in enumerate(me):
        mine = trailing_return(f["nav"], me, i, lookback)
        if mine is None:
            out.append((m, None))
            continue
        if reference == "self":
            # AGAINST ITS OWN PAST ONLY, never including the current reading:
            # a fund that has decayed for a year would otherwise drag its own
            # median down to meet it and look healthy.
            z = robust_z(hist, mine) if len(hist) >= 24 else None
            hist.append(mine)
        else:
            vals = [trailing_return(funds[k]["nav"], me, i, lookback)
                    for k in comp]
            z = robust_z(vals, mine)
        out.append((m, z))
    return out


def signals(funds, month_ends, lookback=DEFAULT_LOOKBACK,
            threshold=DEFAULT_THRESHOLD, min_months=DEFAULT_MIN_MONTHS,
            references=("self", "peers", "family")):
    """Current state of every fund against every reference.

    Returns name -> dict(reference -> dict(z, consecutive, firing)).

    A SIGNAL IS A RUN, NOT A READING. `consecutive` counts unbroken months at
    or beyond the threshold ending at the last month; `firing` is that run
    reaching min_months. Reporting the run length rather than a bare boolean is
    deliberate: a fund three months into a decline and one twelve months in
    both read True, and they are not the same fund.
    """
    out = {}
    for name in funds:
        entry = {}
        for ref in references:
            try:
                zs = series_z(funds, name, month_ends, lookback, ref)
            except ValueError:
                continue
            run = 0
            for _, z in reversed(zs):
                if z is not None and z <= threshold:
                    run += 1
                else:
                    break
            last = next((z for _, z in reversed(zs) if z is not None), None)
            entry[ref] = dict(z=last, consecutive=run,
                              firing=run >= min_months)
        out[name] = entry
    return out


def redemption_priority(sig, weights=None):
    """Rank funds worst-first, for deciding what to sell.

    FAMILY IS WEIGHTED HIGHEST because it is the reference carrying
    information the others cannot: a house-level problem appears across an
    AMC's schemes simultaneously, and both self and peer comparisons read that
    as bad luck.

    The score is a sum of firing runs, so a fund failing on two references
    outranks one failing worse on a single reference. A fund with no firing
    signal scores zero and is not ranked above one that does.
    """
    w = weights or {"self": 1.0, "peers": 1.5, "family": 2.0}
    rows = []
    for name, refs in sig.items():
        score = sum(w.get(r, 1.0) * v["consecutive"]
                    for r, v in refs.items() if v["firing"])
        firing = [r for r, v in refs.items() if v["firing"]]
        rows.append(dict(name=name, score=score, firing=firing,
                         detail={r: (v["z"], v["consecutive"])
                                 for r, v in refs.items()}))
    return sorted(rows, key=lambda r: -r["score"])


def format_priority(rows, top=None):
    lines = [f"{'fund':28s}{'score':>7}  firing references and run lengths"]
    for r in rows[:top]:
        if not r["firing"]:
            continue
        bits = ", ".join(
            f"{ref} z={r['detail'][ref][0]:+.1f} for {r['detail'][ref][1]}m"
            for ref in r["firing"])
        lines.append(f"{r['name'][:27]:28s}{r['score']:>7.1f}  {bits}")
    if len(lines) == 1:
        lines.append("  nothing firing")
    return "\n".join(lines)
