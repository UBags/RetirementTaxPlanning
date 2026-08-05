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
DEFAULT_BLOCK_DAYS = 125      # MEAN block length; lengths are geometric
DEFAULT_VOL_STATES = 3
DEFAULT_VOL_WINDOW = 21       # trading month, for the local volatility read
DEFAULT_YIELD_WINDOW = 126    # two trading quarters, to read the yield level

# Categories whose NAV is an ACCRUAL rather than a price. Their daily return
# essentially IS the prevailing yield over 365, and the yield wanders slowly
# over years -- which is why liquid funds show an eighteen-month variance
# THIRTEEN TIMES the i.i.d. scaling. No bootstrap of daily returns reproduces
# that at any block length, because the persistence is not in the returns; it
# is in a state the returns are a function of. These funds get a generator that
# models what the instrument actually is.
ACCRUAL_CATEGORIES = ("overnight", "liquid", "money market",
                      "ultra short duration")


def is_accrual(category):
    key = " ".join(str(category).lower().replace("-", " ").split())
    for suffix in (" fund", " funds", " scheme"):
        while key.endswith(suffix):
            key = key[: -len(suffix)].strip()
    return any(key.startswith(c) for c in ACCRUAL_CATEGORIES)


def decompose_accrual(returns, window=DEFAULT_YIELD_WINDOW):
    """Split into a slow yield level and a fast residual."""
    level = []
    for i in range(len(returns)):
        w = returns[max(0, i - window + 1):i + 1]
        level.append(sum(w) / len(w))
    return level, [returns[i] - level[i] for i in range(len(returns))]


def ar1_coefficient(xs):
    """Lag-one persistence of a level series. On liquid funds this reads about
    0.997 daily -- a half-life of roughly eight months."""
    m = sum(xs) / len(xs)
    den = sum((x - m) ** 2 for x in xs)
    if den <= EPS:
        return 0.0
    num = sum((xs[i] - m) * (xs[i - 1] - m) for i in range(1, len(xs)))
    return max(0.0, min(0.99999, num / den))


def simulate_factor(n, phi, rng):
    """A standardised AR(1) path: mean zero, unit unconditional variance.

    SIMULATED RATHER THAN RESAMPLED, and that is the point. Bootstrapping the
    yield needed 2000-day blocks to reach the right persistence -- three blocks
    of real history for a nineteen-year path, which is a re-reading of the past
    rather than a sample of it. An AR(1) decouples persistence from block
    count: it can be as persistent as the data says while every path is new.

    ONE FACTOR SERVES EVERY ACCRUAL FUND, so their mutual correlation -- 0.617
    between the liquid and money market slots -- survives.
    """
    sd = math.sqrt(max(1e-12, 1.0 - phi * phi))
    out, cur = [], 0.0
    for _ in range(n):
        cur = phi * cur + rng.gauss(0.0, sd)
        out.append(cur)
    return out
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
    """Fixed-length blocks, drawn independently. THE NAIVE SAMPLER.

    Kept because it is the honest baseline the regime sampler has to beat, and
    there is a test that it is beaten. Its defect is that volatility clusters
    break at every boundary: the next block knows nothing about the last, so a
    crisis lasts one block and no longer.
    """
    idx = []
    while len(idx) < n_out:
        s = rng.randrange(0, n_source)
        for b in range(block):
            idx.append((s + b) % n_source)
            if len(idx) == n_out:
                break
    return idx


def volatility_states(reference, n_states=DEFAULT_VOL_STATES,
                      window=DEFAULT_VOL_WINDOW):
    """Label every position by its local volatility regime.

    ONE MARKET-WIDE REFERENCE LABELS ALL FUNDS, because regimes ARE
    market-wide: 2008 was not a quiet year for corporate bonds while being a
    crisis for equity. Labelling each fund separately would also break the
    shared index, and with it the cross-fund correlation.
    """
    local = []
    for i in range(len(reference)):
        w = reference[max(0, i - window + 1):i + 1]
        local.append(sum(abs(x) for x in w) / len(w))
    ordered = sorted(local)
    cuts = [ordered[int(len(ordered) * j / n_states)]
            for j in range(1, n_states)]
    return [sum(1 for c in cuts if v >= c) for v in local]


def transition_matrix(states, n_states=DEFAULT_VOL_STATES):
    """Regime-to-regime probabilities, Laplace-smoothed.

    Smoothed so a regime pair unseen in nineteen years is improbable rather
    than impossible. On this data the diagonals come out near 0.94, 0.89 and
    0.95, and the calm state never jumps straight to the crisis state --
    regimes drift rather than teleport, and the synthetic ones must too.
    """
    t = [[1.0] * n_states for _ in range(n_states)]
    for a, b in zip(states, states[1:]):
        t[a][b] += 1
    return [[x / sum(row) for x in row] for row in t]


def _regime_indices(states, matrix, n_source, n_out, mean_block, rng,
                    n_states=DEFAULT_VOL_STATES):
    """One shared index sequence, with persistence ACROSS block boundaries.

    THIS IS THE WHOLE FIX. Volatility clustering does not have to live inside a
    block; it can live in the SEQUENCE OF REGIMES the blocks are drawn from.
    Each block's regime is drawn from the previous one through the real
    transition matrix, and the block start is then drawn from positions in that
    regime, so a crisis block is followed by another crisis block about as often
    as it was in the history.

    Measured against the naive sampler on nav_sample.csv, this puts equity ACF
    decay, equity Hurst and corporate Hurst all inside their bands at a MEAN
    BLOCK OF 125 -- about 58 blocks in a nineteen-year path. The naive sampler
    needed 1000-day blocks to match equity alone, which is seven slabs of real
    history end to end, and it still missed corporate Hurst. Fidelity bought by
    replaying the past is not fidelity.

    Block LENGTHS are geometric with the given mean, so no single length is
    imprinted on the output and the sampler stays stationary.
    """
    pools = {s: [i for i, x in enumerate(states) if x == s]
             for s in range(n_states)}
    p_stop = 1.0 / max(1, mean_block)
    idx, blocks = [], 0
    state = rng.randrange(n_states)
    while len(idx) < n_out:
        r, cum = rng.random(), 0.0
        for j in range(n_states):
            cum += matrix[state][j]
            if r <= cum:
                state = j
                break
        pool = pools[state] or list(range(n_source))
        start = rng.choice(pool)
        length = 1
        while rng.random() > p_stop:
            length += 1
        blocks += 1
        for t in range(length):
            idx.append((start + t) % n_source)
            if len(idx) == n_out:
                break
    return idx, blocks


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
                   screen=True, factor=None, yield_window=DEFAULT_YIELD_WINDOW):
    """Continue one series forward using a SHARED index sequence.

    When `factor` is supplied and the category is an accrual instrument, the
    slow yield level is rebuilt from that shared factor and only the fast
    residual is bootstrapped.
    """
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
    if factor is not None and is_accrual(category):
        level, resid = decompose_accrual(r, yield_window)
        m_l = sum(level) / len(level)
        sd_l = math.sqrt(sum((x - m_l) ** 2 for x in level) / len(level))
        raw = [m_l + sd_l * factor[t] + resid[index[t] % len(resid)]
               for t in range(n)]
    else:
        raw = [r[i % len(r)] for i in index[:n]]
    out = _solve(raw, hist_vol / math.sqrt(ppy), target_cagr, ppy, n)

    navs, cur = [lv[-1]], lv[-1]
    for x in out:
        cur *= math.exp(x)
        navs.append(cur)
    return dict(nav_levels=navs, hist_cagr=hist_cagr, hist_vol=hist_vol,
                target_cagr=target_cagr, periods_per_year=ppy,
                dropped=len(dropped), n_source=len(r),
                accrual=bool(factor is not None and is_accrual(category)))


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
               screen=True, regime=True, reference=None,
               n_states=DEFAULT_VOL_STATES, vol_window=DEFAULT_VOL_WINDOW,
               accrual=True, yield_window=DEFAULT_YIELD_WINDOW,
               yield_phi=None):
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

    if regime:
        # THE REFERENCE IS THE MOST VOLATILE FUND unless told otherwise: it
        # carries the clearest regime signal, and a liquid fund's local
        # volatility barely moves between 2007 and 2009.
        ref_name = reference or max(
            funds, key=lambda k: M.volatility(
                M.series_of(funds[k]["nav"], funds[k]["dates"]), ppy) or 0.0)
        _, ref_r, _ = _clean_returns(funds[ref_name]["nav"],
                                     funds[ref_name]["dates"],
                                     funds[ref_name].get("category"), screen)
        ref_r = ref_r[:n_source]
        states = volatility_states(ref_r, n_states, vol_window)
        matrix = transition_matrix(states, n_states)
        index, blocks = _regime_indices(states, matrix, n_source, n_out,
                                        block_days, rng, n_states)
    else:
        ref_name = None
        index = _block_indices(n_source, n_out, block_days, rng)
        blocks = -(-n_out // block_days)

    factor, phi_used = None, None
    if accrual:
        # PERSISTENCE IS ESTIMATED FROM THE COMMON FACTOR, NOT FROM THE AVERAGE
        # FUND, because a common factor is what gets simulated. Each fund's
        # yield level is the shared rate cycle plus its own noise, and that
        # noise attenuates a per-fund AR(1) estimate toward zero. Standardising
        # each level and averaging cancels it: on nav_sample.csv the mean
        # per-fund phi reads 0.99527 while the common factor reads 0.99840, a
        # half-life of 1.19 years against 0.44. Simulating at the per-fund
        # figure left liquid and money market variance ratios far short --
        # twelve criterion failures against five.
        levels = []
        for f in funds.values():
            if not is_accrual(f.get("category")):
                continue
            _, rr, _ = _clean_returns(f["nav"], f["dates"], f.get("category"),
                                      screen)
            lvl, _ = decompose_accrual(rr[:n_source], yield_window)
            m = sum(lvl) / len(lvl)
            sd = math.sqrt(sum((x - m) ** 2 for x in lvl) / len(lvl))
            if sd > EPS:
                levels.append([(x - m) / sd for x in lvl])
        if levels:
            common = [sum(col) / len(col) for col in zip(*levels)]
            phi_used = yield_phi if yield_phi is not None \
                else ar1_coefficient(common)
            factor = simulate_factor(n_out, phi_used, rng)

    out = {}
    for name, f in funds.items():
        res = synthesize_one(f["nav"], f["dates"], f.get("category"), years,
                             f["delta"], index, ppy=ppy, screen=screen,
                             factor=factor, yield_window=yield_window)
        last = max(d for d in f["dates"] if d in f["nav"])
        fwd = forward_dates(last, len(res["nav_levels"]) - 1)
        all_dates = [last] + fwd
        res["dates"] = all_dates
        res["nav"] = {d: v for d, v in zip(all_dates, res["nav_levels"])}
        res["seed"] = seed
        res["block_days"] = block_days
        res["blocks"] = blocks
        res["regime"] = regime
        res["reference"] = ref_name
        res["yield_phi"] = phi_used
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
