# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""verify.py -- decide whether a generated series is good enough to use.

A HARNESS THAT CANNOT FAIL IS NOT A HARNESS. The engine this package replaces
shipped with a batch verifier reporting a 20/20 pass rate, and it reported that
because its "historical" series were Gaussian noise with two crash windows
pasted in -- data possessing none of the stylised facts being tested for. There
is a test in tests/ whose only job is to feed this module a deliberately wrong
series and assert that it says so.

TWO KINDS OF CRITERION, AND CONFLATING THEM IS THE WHOLE PROBLEM.

    EXACT      CAGR and volatility are SOLVED for by the generator, so they are
               held to 1e-4. A miss here is a bug, not variation.

    ESTIMATED  Variance ratio, Hurst, ACF decay, recovery times. These are
               measured from nineteen years of history and carry real
               uncertainty. They are held to the range the REAL series itself
               spans across contiguous sub-periods -- never to a tighter
               tolerance than the data can support.

THE BAND COMES FROM CONTIGUOUS SUBSAMPLING, NEVER FROM RESHUFFLING. A moving
block bootstrap destroys the long-range dependence it is being asked to put a
band around; used here it returned intervals that did not contain their own
point estimates, and it would have acquitted every generator of every charge.
"""
from __future__ import annotations

import math
import random
import statistics as st

from . import metrics as M

EXACT_TOLERANCE = 1e-4
WINDOW_YEARS = 9
MIN_EFFECTIVE_BLOCKS = 30     # below this a path is a replay, not a sample
DECILE_HORIZONS = (3, 6, 9, 12, 18)
DECILE_NULL_DRAWS = 200
DECILE_NULL_BLOCK = 12        # months, so the overlap structure survives
MAX_LENGTH_RATIO = 1.25       # synthetic-to-real length, for fidelity scoring


def _pearson(x, y):
    n = min(len(x), len(y))
    x, y = x[:n], y[:n]
    mx, my = sum(x) / n, sum(y) / n
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    dx = math.sqrt(sum((a - mx) ** 2 for a in x))
    dy = math.sqrt(sum((b - my) ** 2 for b in y))
    return num / (dx * dy) if dx > 0 and dy > 0 else None


def correlation_matrix(funds):
    """Pairwise correlation of daily log returns. `funds` maps name -> (nav, dates)."""
    r = {k: M.log_returns(M.series_of(nav, dates))
         for k, (nav, dates) in funds.items()}
    names = sorted(r)
    out = {}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            out[(a, b)] = _pearson(r[a], r[b])
    return out


def verify_correlations(real_funds, ensemble, alpha=0.10):
    """Does every REAL pairwise correlation sit inside the ensemble's spread?

    THE PROPERTY NO PER-ASSET SYNTHESIZER CAN HAVE. A two-purse plan lives or
    dies on whether debt and equity fall together, and a generator that gets
    every marginal right while making the joints independent will understate
    every simultaneous drawdown it exists to study. `ensemble` is a list of
    dicts mapping fund name -> (nav, dates).
    """
    real = correlation_matrix(real_funds)
    findings, ok = [], True
    for pair, want in real.items():
        vals = sorted(v for v in (correlation_matrix(p).get(pair)
                                  for p in ensemble) if v is not None)
        if want is None or len(vals) < 4:
            findings.append(dict(name=f"{pair[0]}~{pair[1]}", kind="corr",
                                 verdict="not estimable"))
            continue
        lo = vals[int(len(vals) * alpha / 2)]
        hi = vals[max(0, int(len(vals) * (1 - alpha / 2)) - 1)]
        inside = lo <= want <= hi
        ok = ok and inside
        findings.append(dict(name=f"{pair[0]}~{pair[1]}", kind="corr",
                             got=sum(vals) / len(vals), point=want,
                             lo=lo, hi=hi,
                             verdict="pass" if inside else "FAIL"))
    return ok, findings


def decile_null(real_month_levels, n, draws=DECILE_NULL_DRAWS,
                block=DECILE_NULL_BLOCK, seed=0):
    """What deviation a PERFECT generator still shows on a path this long.

    THE TOLERANCE CANNOT BE CHOSEN, IT HAS TO BE MEASURED. Occupancy has no
    real value to sit inside a band -- the real series scored against its own
    deciles gives zero by construction -- so the criterion needs an absolute
    threshold, and any number picked by taste would be indistinguishable from
    one picked to pass.

    Measured on nav_sample.csv the floor is LARGE: at eighteen months a perfect
    equity generator still shows a mean 12.5pp max-bucket deviation and a 95th
    percentile of 24.5. Deviations reported earlier as the weakest part of the
    synthesizer sat at or below that floor. Raw occupancy numbers cannot be
    read without it.

    TWO INDEPENDENT RESAMPLES, NOT RESAMPLE-VERSUS-ORIGINAL. Scoring a
    bootstrap of the real path against that same path's deciles shares data
    between the thing measured and the thing measured against, and understates
    the floor: an ensemble drawn from an IDENTICAL process then fails at three
    months, ratio 1.61. Real synthetic paths are independent realisations and
    carry sampling error on BOTH sides, so the null must too.

    Blocks are twelve months, so the heavy overlap between rolling windows
    survives. An i.i.d. null would understate the floor badly, because
    overlapping n-month returns share n-1 months of data.
    """
    rets = M.monthly_log_returns(real_month_levels, 1)
    if len(rets) < block * 3:
        return None, None
    rng = random.Random(seed)

    def resample():
        out = []
        while len(out) < len(rets):
            st_ = rng.randrange(0, len(rets) - block)
            out.extend(rets[st_:st_ + block])
        lv = [1.0]
        for x in out[:len(rets)]:
            lv.append(lv[-1] * math.exp(x))
        return lv

    devs = []
    for _ in range(draws):
        thr = M.quantile_thresholds(
            [math.exp(x) - 1 for x in M.monthly_log_returns(resample(), n)])
        if thr is None:
            continue
        ss = [math.exp(x) - 1 for x in M.monthly_log_returns(resample(), n)]
        d = M.occupancy_deviation(M.occupancy(ss, thr))[0]
        if d is not None:
            devs.append(d)
    if len(devs) < 20:
        return None, None
    devs.sort()
    return st.fmean(devs), devs[int(len(devs) * 0.95)]


def check_deciles(real_nav, real_dates, paths, horizons=DECILE_HORIZONS,
                  drift_delta=0.0, seed=0):
    """Rolling n-month return occupancy against the real series' deciles.

    Scored on DRIFT-ADJUSTED returns: the synthetic series has a deliberately
    lower CAGR, so it must sit low in the real distribution unless the cut is
    added back over the window. Without that, the requirement to reduce the
    return and the requirement to match the quantiles contradict each other and
    no series can satisfy both.

    Passes when the ensemble MEAN deviation is at or under the null's 95th
    percentile -- that is, when the generator is not distinguishable from a
    correct one at this sample size.
    """
    ml = [real_nav[d] for d in M.month_ends(real_dates, real_nav)]
    findings, ok = [], True
    for n in horizons:
        mean_null, p95 = decile_null(ml, n, seed=seed)
        devs = []
        for nav, dates in paths:
            sl = [nav[d] for d in M.month_ends(dates, nav)]
            occ = M.decile_occupancy(ml, sl, n, drift_delta=drift_delta)
            if occ is None:
                continue
            mx, _ = M.occupancy_deviation(occ)
            devs.append(mx)
        if not devs or p95 is None:
            findings.append(dict(name=f"decile{n}", kind="decile",
                                 verdict="not estimable"))
            continue
        got = st.fmean(devs)
        passed = got <= p95
        ok = ok and passed
        findings.append(dict(name=f"decile{n}", kind="decile", got=got,
                             null_mean=mean_null, null_p95=p95,
                             ratio=got / mean_null if mean_null else None,
                             verdict="pass" if passed else "FAIL"))
    return ok, findings


def check_novelty(blocks, n_out, mean_block,
                  min_blocks=MIN_EFFECTIVE_BLOCKS):
    """Is the path a sample of history, or a re-reading of it?

    EVERY OTHER CRITERION HERE IS A SIMILARITY MEASURE, so all of them are
    maximised by returning the real series unchanged. Without this one, block
    length can be tuned until anything passes: on nav_sample.csv a naive
    bootstrap at 1000-day blocks matched equity ACF decay and Hurst exactly,
    using SEVEN contiguous slabs of the real past for a nineteen-year path. It
    passed because it had stopped generating.
    """
    ok = blocks >= min_blocks
    return dict(name="novelty", kind="novelty", blocks=blocks,
                n_out=n_out, mean_block=mean_block, min_blocks=min_blocks,
                verdict="pass" if ok else "FAIL"), ok


def check_length(real_dates, synth_dates, max_ratio=MAX_LENGTH_RATIO):
    """Fidelity must be scored on paths the same length as the history.

    Hurst is length-invariant; the ACF decay exponent is NOT. Measured on this
    data the same generator scored [0.55, 0.96] on nineteen-year paths and
    [0.74, 1.26] on thirty-five-year ones -- a shift of 0.2 that is arithmetic,
    not fidelity. Generate at the real length to SCORE, and at the planning
    horizon to USE.
    """
    ratio = len(synth_dates) / max(1, len(real_dates))
    ok = (1 / max_ratio) <= ratio <= max_ratio
    return dict(name="length", kind="length", ratio=ratio,
                verdict="pass" if ok else "FAIL"), ok


def subsample_range(nav, dates, fn, window_years=WINDOW_YEARS, step_years=1):
    """The range `fn` spans across contiguous windows of the REAL series.

    Contiguous, so dependence inside each window is untouched. This is the only
    honest statement available about how much a long-horizon statistic moves
    for reasons that are not the generator's fault.
    """
    years = sorted({d[:4] for d in dates})
    vals = []
    for i in range(0, len(years) - window_years + 1, step_years):
        lo, hi = years[i], years[i + window_years - 1]
        sub = [d for d in dates if lo <= d[:4] <= hi]
        v = fn(nav, sub)
        if v is not None:
            vals.append(v)
    if not vals:
        return None
    return min(vals), max(vals), len(vals)


def _vr(n):
    def f(nav, dates):
        me = M.month_ends(dates, nav)
        return M.variance_ratio([nav[d] for d in me], n)
    return f


def _hurst(nav, dates):
    lv = M.series_of(nav, dates)
    return M.hurst_exponent([abs(x) for x in M.log_returns(lv)])


def _acf_beta(nav, dates):
    lv = M.series_of(nav, dates)
    return M.acf_abs_decay_exponent(M.log_returns(lv))


def _leverage1(nav, dates):
    lv = M.series_of(nav, dates)
    return M.leverage_correlation(M.log_returns(lv), 1)


ESTIMATED_CRITERIA = {
    "vr3": _vr(3), "vr6": _vr(6), "vr12": _vr(12), "vr18": _vr(18),
    "hurst": _hurst, "acf_beta": _acf_beta, "leverage1": _leverage1,
}


def build_targets(nav, dates, window_years=WINDOW_YEARS):
    """Point estimate plus enforceable band for every estimated criterion.

    A criterion whose band spans a random walk is marked UNMEASURABLE. Holding
    a generator to a target the data cannot resolve manufactures precision, and
    the report would carry four significant figures earned by nothing.
    """
    out = {}
    for name, fn in ESTIMATED_CRITERIA.items():
        point = fn(nav, dates)
        rng = subsample_range(nav, dates, fn, window_years)
        if point is None or rng is None:
            out[name] = dict(point=point, lo=None, hi=None, n=0,
                             enforceable=False, reason="not estimable")
            continue
        lo, hi, n = rng
        width = hi - lo
        enforceable = width < abs(point) if point else False
        out[name] = dict(point=point, lo=lo, hi=hi, n=n,
                         enforceable=enforceable,
                         reason="" if enforceable else
                         f"band [{lo:.2f}, {hi:.2f}] wider than the estimate")
    return out


def verify_ensemble(real_nav, real_dates, paths, alpha=0.10,
                    min_paths=8, target_cagr=None, target_vol=None):
    """Does the REAL value sit inside the spread of MANY synthetic paths?

    THIS IS THE RIGHT WAY ROUND, AND THE OTHER WAY IS A TRAP I FELL INTO TWICE.
    Scoring one synthetic path against a band built from contiguous windows of
    the real series REJECTS GENERATORS THAT ARE PROVABLY CORRECT: two
    independent draws from an identical process differ by more than the real
    series differs from itself across sub-periods, because those sub-periods
    all share one realised history. Measured, VR(12) on two draws of the same
    GBM read 1.42 and 0.46 against a sub-period band of [0.48, 1.85].

    So the ensemble supplies the band and the real series supplies the single
    observation being tested. A criterion whose ensemble band still spans a
    random walk is reported UNMEASURABLE and does not vote.

    `paths` is a sequence of (nav, dates).
    """
    if len(paths) < min_paths:
        raise ValueError(
            f"{len(paths)} paths is not an ensemble; {min_paths} is the "
            f"minimum for a band that means anything. A single path has more "
            f"latitude than any generator defect this would detect.")

    findings, ok = [], True
    real = M.measure(real_nav, real_dates)

    want_cagr = target_cagr if target_cagr is not None else real.get("cagr")
    want_vol = target_vol if target_vol is not None else real.get("vol")
    for label, key, want in (("cagr", "cagr", want_cagr),
                             ("vol", "vol", want_vol)):
        got = [M.measure(n, d).get(key) for n, d in paths]
        got = [g for g in got if g is not None]
        if not got or want is None:
            findings.append(dict(name=label, kind="exact", verdict="MISSING"))
            ok = False
            continue
        worst = max(abs(g - want) for g in got)
        passed = worst <= EXACT_TOLERANCE
        ok = ok and passed
        findings.append(dict(name=label, kind="exact", got=sum(got) / len(got),
                             want=want, miss=worst,
                             verdict="pass" if passed else "FAIL"))

    for name, fn in ESTIMATED_CRITERIA.items():
        vals = sorted(v for v in (fn(n, d) for n, d in paths) if v is not None)
        point = fn(real_nav, real_dates)
        if len(vals) < min_paths or point is None:
            findings.append(dict(name=name, kind="estimated",
                                 verdict="not estimable"))
            continue
        lo = vals[int(len(vals) * alpha / 2)]
        hi = vals[max(0, int(len(vals) * (1 - alpha / 2)) - 1)]
        spans_walk = lo <= 1.0 <= hi and name.startswith("vr")
        if spans_walk:
            findings.append(dict(name=name, kind="estimated", got=point,
                                 point=point, lo=lo, hi=hi,
                                 verdict="UNMEASURABLE",
                                 reason="ensemble band spans a random walk"))
            continue
        inside = lo <= point <= hi
        ok = ok and inside
        findings.append(dict(name=name, kind="estimated", got=point,
                             point=point, lo=lo, hi=hi,
                             verdict="pass" if inside else "FAIL"))
    return ok, findings


def verify(real_nav, real_dates, synth_nav, synth_dates, targets=None,
           target_cagr=None, target_vol=None, enforce_unmeasurable=False):
    """Score a generated series. Returns (ok, findings).

    SINGLE-PATH FORM, AND IT HAS LOW POWER ON EVERY ESTIMATED CRITERION. Use
    verify_ensemble for real work: one path cannot distinguish a defective
    generator from an unlucky draw, and the sub-period band this uses is
    narrower than the true realisation-to-realisation spread. Kept because the
    exact criteria -- CAGR and volatility, both solved for -- are perfectly
    testable on one path, and because a cheap smoke test is worth having.
    """
    targets = targets or build_targets(real_nav, real_dates)
    real = M.measure(real_nav, real_dates)
    synth = M.measure(synth_nav, synth_dates)
    findings, ok = [], True

    want_cagr = target_cagr if target_cagr is not None else real.get("cagr")
    want_vol = target_vol if target_vol is not None else real.get("vol")
    for label, got, want in (("cagr", synth.get("cagr"), want_cagr),
                             ("vol", synth.get("vol"), want_vol)):
        if got is None or want is None:
            findings.append(dict(name=label, kind="exact", verdict="MISSING"))
            ok = False
            continue
        miss = abs(got - want)
        passed = miss <= EXACT_TOLERANCE
        ok = ok and passed
        findings.append(dict(name=label, kind="exact", got=got, want=want,
                             miss=miss, verdict="pass" if passed else "FAIL"))

    for name, fn in ESTIMATED_CRITERIA.items():
        t = targets.get(name, {})
        got = fn(synth_nav, synth_dates)
        if got is None:
            findings.append(dict(name=name, kind="estimated",
                                 verdict="not estimable"))
            continue
        if not t.get("enforceable") and not enforce_unmeasurable:
            findings.append(dict(name=name, kind="estimated", got=got,
                                 point=t.get("point"), lo=t.get("lo"),
                                 hi=t.get("hi"), verdict="UNMEASURABLE",
                                 reason=t.get("reason", "")))
            continue
        inside = t["lo"] <= got <= t["hi"]
        ok = ok and inside
        findings.append(dict(name=name, kind="estimated", got=got,
                             point=t["point"], lo=t["lo"], hi=t["hi"],
                             verdict="pass" if inside else "FAIL"))
    return ok, findings


def format_findings(findings):
    lines = []
    for f in findings:
        if f["kind"] == "exact":
            if f["verdict"] == "MISSING":
                lines.append(f"  {f['name']:10s} exact       MISSING")
            else:
                lines.append(f"  {f['name']:10s} exact      got {f['got']:+.6f} "
                             f"want {f['want']:+.6f}  miss {f['miss']:.2e}  "
                             f"{f['verdict']}")
        elif f["kind"] == "decile":
            if f["verdict"] == "not estimable":
                lines.append(f"  {f['name']:10s} decile     not estimable")
            else:
                lines.append(
                    f"  {f['name']:10s} decile     got {f['got']:5.1f}pp  "
                    f"null mean {f['null_mean']:5.1f}  p95 {f['null_p95']:5.1f}"
                    f"  ratio {f['ratio']:.2f}  {f['verdict']}")
        elif f["kind"] == "novelty":
            lines.append(f"  {'novelty':10s} {f['blocks']} blocks in "
                         f"{f['n_out']} observations at mean {f['mean_block']}"
                         f"  (floor {f['min_blocks']})  {f['verdict']}")
        elif f["kind"] == "length":
            lines.append(f"  {'length':10s} synthetic/real = {f['ratio']:.2f}"
                         f"  {f['verdict']}")
        elif f["verdict"] in ("not estimable",):
            lines.append(f"  {f['name']:10s} estimated  {f['verdict']}")
        else:
            band = (f"[{f['lo']:.2f}, {f['hi']:.2f}]"
                    if f.get("lo") is not None else "[--]")
            lines.append(f"  {f['name']:10s} estimated  got {f['got']:+.3f}  "
                         f"real {f['point']:+.3f} band {band:>16s}  "
                         f"{f['verdict']}")
    return "\n".join(lines)
