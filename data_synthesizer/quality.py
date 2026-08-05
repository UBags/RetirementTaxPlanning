# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""quality.py -- screen a NAV series before anything is calibrated to it.

A SYNTHESIZER REPRODUCES WHATEVER IT IS SHOWN, INCLUDING THE MISTAKES. The debt
slots in nav_sample.csv carry an excess kurtosis of 259, and inspection shows
that is two different things wearing one number:

    REAL CREDIT EVENTS, which must be preserved. Slot 0 falls 4.85% on
    2009-01-07 -- 34.8 sigma -- which is Satyam. Slots 28 and 29 fall 8.77% and
    6.09% on 2019-06-04, which is DHFL. A debt fund that never writes anything
    down is not a debt fund, and a plan tested against one is not tested.

    REPORTING ERRORS, which must not be. Slots 21 and 29 both rise about 6.4%
    on 2010-04-08 and give it all back on 2010-04-09. Two unrelated funds
    moving together and reverting on the same two days is a bad print, not a
    market. Slot 27 falls 9.63% to a NAV of exactly 100.0000 on the first date
    it appears, which is a re-base rather than a return.

THIS MODULE FLAGS, IT DOES NOT FIX. Every screen returns findings with dates
and magnitudes so a human decides. Silently repairing prices would put an
unreviewable judgement between the AMFI file and every number downstream of it,
and the two bugs already found in the ingest were both of that shape.
"""
from __future__ import annotations

import math

EPS = 1e-12
REVERT_TOLERANCE = 0.30       # reversal within 30% of the move counts as one
SPIKE_SIGMA = 6.0             # how large a move must be to be worth testing
REBASE_VALUES = (100.0, 10.0, 1000.0)
REBASE_TOL = 1e-6


def _series(nav, dates):
    ds = [d for d in dates if d in nav and nav[d] > EPS]
    return ds, [nav[d] for d in ds]


def _log_returns(levels):
    return [math.log(levels[i] / levels[i - 1]) for i in range(1, len(levels))]


def _sigma(rets):
    if len(rets) < 3:
        return None
    m = sum(rets) / len(rets)
    return math.sqrt(sum((r - m) ** 2 for r in rets) / len(rets))


def spike_reversals(nav, dates, sigma_threshold=SPIKE_SIGMA,
                    tolerance=REVERT_TOLERANCE):
    """Large moves undone the very next observation.

    A genuine credit writedown is PERMANENT: the NAV steps down and stays down.
    A bad print reverts. That asymmetry is the whole test, and it is the only
    thing here that separates Satyam from a typo without a human looking.
    """
    ds, lv = _series(nav, dates)
    r = _log_returns(lv)
    s = _sigma(r)
    if s is None or s <= EPS:
        return []
    out = []
    for i in range(len(r) - 1):
        if abs(r[i]) < sigma_threshold * s:
            continue
        if r[i] * r[i + 1] >= 0:
            continue                       # not a reversal at all
        if abs(r[i] + r[i + 1]) <= tolerance * abs(r[i]):
            out.append(dict(date=ds[i + 1], next_date=ds[i + 2],
                            move=math.exp(r[i]) - 1.0,
                            undo=math.exp(r[i + 1]) - 1.0,
                            sigma=abs(r[i]) / s))
    return out


# The largest one-day move each kind of fund can physically produce, keyed by
# AMFI CATEGORY rather than by group. These are not statistical thresholds and
# must not be fitted -- they are statements about what the instrument IS: a
# fund's one-day price move is bounded by its duration times the largest
# plausible yield shift, plus whatever credit it can lose in a session.
#
# KEYED BY CATEGORY BECAUSE THE GROUPS ARE TOO COARSE HERE. An overnight fund
# and a low duration fund sit in the same group for the redemption gate and
# have nothing in common for this test: one carries no duration and the other
# carries a year of it. Screening both at one bound flagged 2020-03-27 across
# eleven funds, which is the RBI's emergency 75bp cut -- the most real debt
# event in the sample after DHFL.
MAX_DAILY_MOVE = {
    "overnight": 0.0015,
    "liquid": 0.004,
    "money market": 0.015,
    "ultra short duration": 0.015,
    "low duration": 0.025,
    "short duration": 0.035,
    "corporate bond": 0.060,
    "credit risk": 0.080,
    "banking and psu": 0.050,
    "gilt": 0.060,
    "dynamic bond": 0.060,
    "floater": 0.040,
    "medium duration": 0.050,
    "medium to long duration": 0.060,
    "long duration": 0.080,
}
DEFAULT_MAX_DAILY_MOVE = 0.25          # equity and hybrid


def _cap_for(category, bounds=None):
    key = " ".join(str(category).lower().replace("-", " ").split())
    for suffix in (" fund", " funds", " scheme"):
        while key.endswith(suffix):
            key = key[: -len(suffix)].strip()
    table = bounds or MAX_DAILY_MOVE
    if key in table:
        return table[key]
    for name, cap in table.items():
        if key.startswith(name):
            return cap
    return DEFAULT_MAX_DAILY_MOVE


def implausible_moves(nav, dates, category, bounds=None, tolerance=REVERT_TOLERANCE):
    """Moves larger than the instrument can produce, each labelled.

    A MOVE BEYOND THE PHYSICAL BOUND IS ONE OF TWO THINGS AND THEY SEPARATE
    CLEANLY. A credit writedown is PERMANENT -- the NAV steps down and stays
    down, and it is always negative. A bad print REVERTS, usually the next
    session, and can go either way. So the same test that catches the typo also
    catalogues the credit events, and neither has to be guessed at:

        2019-06-04  slot 28 falls 8.77% and stays down   -> DHFL, keep it
        2010-04-08  slot 21 rises 6.46%, gives it back   -> a print, drop it

    Verdicts are 'reverting' (a defect), 'permanent_loss' (a credit event, and
    real), and 'permanent_gain' (neither -- a fund cannot gain 6% permanently
    on one session, so this is a re-base or a merger and needs a human).
    """
    ds, lv = _series(nav, dates)
    cap = _cap_for(category, bounds)
    r = _log_returns(lv)
    # Dates that a re-base already explains. Without this the step down to a
    # launch NAV is counted a second time as a credit writedown, and the
    # synthesizer would learn to produce a 9.6% default in a money market fund.
    rebased = {e["date"] for e in rebases(nav, dates)}

    out, i = [], 0
    while i < len(r):
        m = math.exp(r[i]) - 1.0
        if abs(m) <= cap or ds[i + 1] in rebased:
            i += 1
            continue
        nxt = r[i + 1] if i + 1 < len(r) else 0.0
        reverting = (r[i] * nxt < 0
                     and abs(r[i] + nxt) <= tolerance * abs(r[i]))
        if reverting:
            verdict = "reverting"
        elif m < 0:
            verdict = "permanent_loss"
        else:
            verdict = "permanent_gain"
        out.append(dict(date=ds[i + 1], move=m, cap=cap, verdict=verdict,
                        from_nav=lv[i], to_nav=lv[i + 1]))
        # CONSUME BOTH LEGS OF A REVERSAL. The give-back is the same event as
        # the spike, and scoring it separately turned one bad print into a
        # 6% credit loss in a liquid fund -- an instrument that has never had
        # one and structurally cannot.
        i += 2 if reverting else 1
    return out


def rebases(nav, dates, values=REBASE_VALUES, tol=REBASE_TOL):
    """Moves that land on a launch NAV exactly -- 100.0000 and friends."""
    ds, lv = _series(nav, dates)
    out = []
    for i in range(1, len(lv)):
        for v in values:
            if abs(lv[i] - v) <= tol and abs(lv[i] / lv[i - 1] - 1.0) > 0.02:
                out.append(dict(date=ds[i], from_nav=lv[i - 1], to_nav=lv[i],
                                move=lv[i] / lv[i - 1] - 1.0))
    return out


def flat_runs(nav, dates, min_length=10):
    """Stretches where the NAV does not move at all.

    A liquid fund accrues every day. A long flat run means the feed repeated a
    stale value, and every zero return in it drags the volatility estimate down
    while contributing nothing real.
    """
    ds, lv = _series(nav, dates)
    out, start = [], 0
    for i in range(1, len(lv) + 1):
        same = i < len(lv) and abs(lv[i] - lv[start]) <= EPS
        if not same:
            if i - start >= min_length:
                out.append(dict(start=ds[start], end=ds[i - 1],
                                length=i - start, nav=lv[start]))
            start = i
    return out


def coincident_spikes(fund_map_series, sigma_threshold=SPIKE_SIGMA):
    """Dates on which several unrelated funds move together and revert.

    ONE FUND WITH A BAD PRINT IS AN ACCIDENT; FOUR ON THE SAME DAY IS A FEED.
    Passing the whole universe in lets the screen see what a per-fund check
    cannot: 2010-04-08 is invisible in slot 21 alone and obvious across the
    twenty-six.
    """
    hits = {}
    for name, (nav, dates) in fund_map_series.items():
        for ev in spike_reversals(nav, dates, sigma_threshold):
            hits.setdefault(ev["date"], []).append((name, ev["move"]))
    return {d: v for d, v in sorted(hits.items()) if len(v) > 1}


def screen(nav, dates, name="", category=None):
    """Every screen, as one report.

    `implausible` is the only finding here that is a defect on its own. A
    spike-reversal or a flat run is a QUESTION; a move the instrument cannot
    make is an ANSWER.
    """
    imp = implausible_moves(nav, dates, category) if category else []
    return dict(name=name, category=category,
                spike_reversals=spike_reversals(nav, dates),
                implausible=[e for e in imp if e["verdict"] != "permanent_loss"],
                credit_events=[e for e in imp if e["verdict"] == "permanent_loss"],
                rebases=rebases(nav, dates),
                flat_runs=flat_runs(nav, dates))


def format_report(reports, coincident=None):
    lines = []
    for rep in reports:
        bits = []
        if rep.get("implausible"):
            bits.append(f"{len(rep['implausible'])} IMPLAUSIBLE")
        if rep.get("credit_events"):
            bits.append(f"{len(rep['credit_events'])} credit event")
        if rep["spike_reversals"]:
            bits.append(f"{len(rep['spike_reversals'])} spike-reversal")
        if rep["rebases"]:
            bits.append(f"{len(rep['rebases'])} rebase")
        if rep["flat_runs"]:
            bits.append(f"{len(rep['flat_runs'])} flat run")
        if bits:
            lines.append(f"  {rep['name']:34s} {', '.join(bits)}")
    if coincident:
        lines.append("")
        lines.append("  COINCIDENT across funds (a feed problem, not a market):")
        for d, funds in coincident.items():
            names = ", ".join(f"{n} {m * 100:+.2f}%" for n, m in funds)
            lines.append(f"    {d}  {names}")
    return "\n".join(lines) if lines else "  nothing flagged"
