# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""engine.py -- continue every NAV series forward from today.

THE SIMPLEST GENERATOR THAT PASSES THE HARNESS, AND NOT ONE COMPONENT MORE.

The engine this replaces carried GJR-GARCH-in-mean, EVT/GPD tail smoothing,
Hansen skewed-t innovations and a two-state Markov regime switch. It also
returned `nan` on real Indian NAV data, because the regime volatility
multiplier was fed back into the GARCH variance state and the recursion
diverged. Every one of those components was defensible in isolation, and
together they made a thing nobody could audit and no test could fail.

So the rule here is: verify.build_targets says which criteria nineteen years
can actually enforce, and the generator carries only what is needed to meet
them. Measured on nav_sample.csv, the Hurst exponent of |r| is enforceable to
a band of about [0.54, 0.57] while the variance ratio is unmeasurable at every
horizon. A block bootstrap of real returns inherits volatility clustering, the
leverage effect and the fat tails FOR FREE, because it resamples the real thing
rather than modelling it. Add a component when the harness rejects a path
without it, and record which test forced it.

FOUR PROPERTIES THIS DOES GUARANTEE, EACH WITH A TEST.

    EXACT MOMENTS. Volatility is scaled to the historical figure and the drift
    is solved so the CAGR lands on (historical - delta). Both to 1e-9. These
    are solved, not sampled, so a miss is a bug.

    CROSS-FUND CORRELATION. Block starts are drawn ONCE and every fund reads
    the same index sequence. Synthesizing funds independently -- which the
    uploaded engine did -- gives a two-purse model a debt future and an equity
    future that have never heard of each other.

    REPRODUCIBILITY. Every path is a pure function of its seed. No global
    random state is touched.

    A SCREENED INPUT. quality.py separates the 2019 DHFL writedowns, which are
    real and must survive, from the 2010-04-08 print that moved a liquid fund
    6% and gave it back. Resampling the second teaches the generator to emit
    impossible moves forever.

THE CALENDAR FRAME IS MEASURED. These series are calendar daily, about 365
observations a year. Assuming 252 understates CAGR and volatility by roughly
30% and returns a number that still looks plausible.
"""
from __future__ import annotations

import math
import random

from . import metrics as M
from . import quality as Q

EPS = 1e-12
DEFAULT_BLOCK_DAYS = 60
DEFAULT_DEBT_DELTA = 0.0100
DEFAULT_EQUITY_DELTA = 0.0125
DEFAULT_SEED = 20260805


class SynthesisError(RuntimeError):
    """Raised when a target cannot be hit. NEVER returns a path instead.

    A generator that quietly returns its best effort is worse than one that
    fails, because the shortfall then arrives as a fidelity finding fifty
    modules downstream and gets attributed to the market.
    """


def _clean_returns(nav, dates, category, screen=True):
    """Log returns with reverting defects removed, credit events preserved."""
    ds = [d for d in dates if d in nav and nav[d] > EPS]
    lv = [nav[d] for d in ds]
    r = [math.log(lv[i] / lv[i - 1]) for i in range(1, len(lv))]
    dropped = []
    if screen and category:
        bad = set()
        for ev in Q.implausible_moves(nav, ds, category):
            if ev["verdict"] != "permanent_loss":
                j = ds.index(ev["date"]) - 1
                bad.add(j)
                if ev["verdict"] == "reverting":
                    bad.add(j + 1)          # the give-back is the same event
        for ev in Q.rebases(nav, ds):
            bad.add(ds.index(ev["date"]) - 1)
        dropped = sorted(bad)
        r = [x for i, x in enumerate(r) if i not in bad]
    return ds, r, dropped


def _block_indices(n_source, n_out, block, rng):
    """One shared index sequence. EVERY FUND READS THIS, which is the whole
    mechanism by which correlation survives."""
    idx = []
    while len(idx) < n_out:
        s = rng.randrange(0, n_source)
        for b in range(block):
            idx.append((s + b) % n_source)
            if len(idx) == n_out:
                break
    return idx


def _solve(raw, target_vol_per_period, target_cagr, ppy, n):
    """Scale to the target volatility, then shift to the target CAGR.

    ORDER MATTERS AND IS NOT INTERCHANGEABLE. Scaling changes the mean, so the
    drift must be solved AFTER the scale is fixed. Solving the drift first and
    scaling afterwards moves the CAGR off target by the scale factor, which is
    a silent 10-20% error in terminal corpus over sixty years.
    """
    m = sum(raw) / len(raw)
    sd = math.sqrt(sum((x - m) ** 2 for x in raw) / len(raw))
    if sd <= EPS:
        raise SynthesisError("source returns have no dispersion to scale")
    k = target_vol_per_period / sd
    scaled = [(x - m) * k for x in raw]
    want_total = math.log(1.0 + target_cagr) * (n / ppy)
    shift = (want_total - sum(scaled)) / n
    return [x + shift for x in scaled]


def synthesize_one(nav, dates, category, years, delta, index, ppy=None,
                   screen=True):
    """Continue one series forward using a SHARED index sequence."""
    ds, r, dropped = _clean_returns(nav, dates, category, screen)
    if len(r) < 250:
        raise SynthesisError(f"{category}: {len(r)} usable returns is too few")
    ppy = ppy or M.periods_per_year(ds) or 365.0
    lv = [nav[d] for d in ds]

    hist_cagr = M.cagr(lv, ppy)
    hist_vol = M.volatility(lv, ppy)
    if hist_cagr is None or hist_vol is None:
        raise SynthesisError(f"{category}: cannot measure history")
    target_cagr = hist_cagr - delta
    if target_cagr <= -1.0:
        raise SynthesisError(
            f"{category}: delta {delta} drives the target CAGR to "
            f"{target_cagr:.4f}, which is not a return")

    n = int(round(years * ppy))
    raw = [r[i % len(r)] for i in index[:n]]
    out = _solve(raw, hist_vol / math.sqrt(ppy), target_cagr, ppy, n)

    navs, cur = [lv[-1]], lv[-1]
    for x in out:
        cur *= math.exp(x)
        navs.append(cur)
    return dict(nav_levels=navs, hist_cagr=hist_cagr, hist_vol=hist_vol,
                target_cagr=target_cagr, periods_per_year=ppy,
                dropped=len(dropped), n_source=len(r))


def forward_dates(last_date, n):
    """n consecutive calendar dates after `last_date`, inclusive of the first.

    Calendar rather than trading days, to match the frame the input arrives in.
    """
    y, m, d = int(last_date[:4]), int(last_date[5:7]), int(last_date[8:10])
    days_in = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    out = []
    for _ in range(n):
        d += 1
        leap = (y % 4 == 0 and y % 100 != 0) or y % 400 == 0
        cap = 29 if (m == 2 and leap) else days_in[m - 1]
        if d > cap:
            d, m = 1, m + 1
            if m > 12:
                m, y = 1, y + 1
        out.append(f"{y:04d}-{m:02d}-{d:02d}")
    return out


def synthesize(funds, years, seed=DEFAULT_SEED, block_days=DEFAULT_BLOCK_DAYS,
               screen=True):
    """Continue every fund forward together.

    `funds` maps a name to dict(nav=..., dates=..., category=..., delta=...).
    Returns name -> dict(nav=..., dates=..., diagnostics...).

    ONE SHARED INDEX SEQUENCE FOR ALL OF THEM. That is the only reason the
    correlation between debt and equity survives into the synthetic stretch,
    and it is why this takes the whole universe rather than one series.
    """
    if not funds:
        raise SynthesisError("no funds given")
    rng = random.Random(seed)

    lengths = {}
    for name, f in funds.items():
        ds, r, _ = _clean_returns(f["nav"], f["dates"], f.get("category"),
                                  screen)
        lengths[name] = len(r)
    n_source = min(lengths.values())
    if n_source < 250:
        raise SynthesisError(
            f"shortest usable history is {n_source} returns; a block "
            f"bootstrap over that is a replay, not a sample")

    any_f = next(iter(funds.values()))
    ppy = M.periods_per_year(
        [d for d in any_f["dates"] if d in any_f["nav"]]) or 365.0
    n_out = int(round(years * ppy))
    index = _block_indices(n_source, n_out, block_days, rng)

    out = {}
    for name, f in funds.items():
        res = synthesize_one(f["nav"], f["dates"], f.get("category"), years,
                             f["delta"], index, ppy=ppy, screen=screen)
        last = max(d for d in f["dates"] if d in f["nav"])
        fwd = forward_dates(last, len(res["nav_levels"]) - 1)
        all_dates = [last] + fwd
        res["dates"] = all_dates
        res["nav"] = {d: v for d, v in zip(all_dates, res["nav_levels"])}
        res["seed"] = seed
        res["block_days"] = block_days
        out[name] = res
    return out


def synthesize_ensemble(funds, years, n_paths=12, seed=DEFAULT_SEED, **kw):
    """`n_paths` independent futures, seeds derived from one.

    THE ENSEMBLE IS NOT OPTIONAL FOR JUDGING FIDELITY. Two independent draws of
    an identical process disagree on a twelve-month variance ratio by more than
    the real series disagrees with itself across sub-periods. Anything decided
    from one path is decided from noise.
    """
    return [synthesize(funds, years, seed=seed + 1000 * i, **kw)
            for i in range(n_paths)]
