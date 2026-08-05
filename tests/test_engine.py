"""Tests for data_synthesizer.engine.

FOUR PROPERTIES THE GENERATOR CLAIMS, ONE TEST EACH, AND THEY ARE THE FOUR THE
UPLOADED ENGINE GOT WRONG:

    exact moments        it solved CAGR and never targeted volatility at all
    cross-fund correlation   it synthesized every fund independently
    reproducibility      it used global numpy random state
    a screened input     it had no notion that a NAV could be misprinted

Plus the one that stops the whole thing being theatre: the generator's output
must survive verify_ensemble, and a deliberately broken generator must not.
"""
import math
import statistics as st

import pytest

from data_synthesizer import engine as E
from data_synthesizer import metrics as M
from data_synthesizer import verify as V


def daily_dates(n, start_year=2005):
    out, y, m, d = [], start_year, 1, 1
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}-{d:02d}")
        d += 1
        if d > 30:
            d, m = 1, m + 1
            if m > 12:
                m, y = 1, y + 1
    return out


def gbm(n, mu, sigma, seed, start=100.0):
    rng = random.Random(seed)
    lv, dt = [start], 1.0 / 365.0
    for _ in range(n):
        lv.append(lv[-1] * math.exp(
            rng.gauss(mu * dt - 0.5 * sigma * sigma * dt,
                      sigma * math.sqrt(dt))))
    dates = daily_dates(len(lv))
    return dates, {k: v for k, v in zip(dates, lv)}


import random  # noqa: E402


def two_correlated(n, rho, seed):
    """Two series sharing a common factor, so a real correlation exists."""
    rng = random.Random(seed)
    dates = daily_dates(n + 1)
    a, b = [100.0], [100.0]
    for _ in range(n):
        z = rng.gauss(0, 1)
        ea, eb = rng.gauss(0, 1), rng.gauss(0, 1)
        ra = 0.0003 + 0.010 * (rho * z + math.sqrt(1 - rho ** 2) * ea)
        rb = 0.0002 + 0.004 * (rho * z + math.sqrt(1 - rho ** 2) * eb)
        a.append(a[-1] * math.exp(ra))
        b.append(b[-1] * math.exp(rb))
    return (dates, {d: v for d, v in zip(dates, a)},
            {d: v for d, v in zip(dates, b)})


def fund(nav, dates, category, delta):
    return dict(nav=nav, dates=dates, category=category, delta=delta)


def correlation(x, y):
    mx, my = st.fmean(x), st.fmean(y)
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    dx = math.sqrt(sum((a - mx) ** 2 for a in x))
    dy = math.sqrt(sum((b - my) ** 2 for b in y))
    return num / (dx * dy) if dx > 0 and dy > 0 else 0.0


def rets(res):
    return M.log_returns([res["nav"][d] for d in res["dates"]])


# ── exact moments ───────────────────────────────────────────────────────────

def test_the_cagr_lands_exactly_on_history_minus_delta():
    dates, nav = gbm(365 * 19, 0.11, 0.20, seed=1)
    out = E.synthesize({"eq": fund(nav, dates, "Flexi Cap Fund", 0.0125)}, 20)
    r = out["eq"]
    got = M.cagr(r["nav_levels"], r["periods_per_year"])
    assert got == pytest.approx(r["target_cagr"], abs=1e-9)
    assert r["target_cagr"] == pytest.approx(r["hist_cagr"] - 0.0125, abs=1e-12)


def test_the_volatility_lands_exactly_on_history():
    dates, nav = gbm(365 * 19, 0.11, 0.20, seed=2)
    out = E.synthesize({"eq": fund(nav, dates, "Flexi Cap Fund", 0.0125)}, 20)
    r = out["eq"]
    got = M.volatility(r["nav_levels"], r["periods_per_year"])
    assert got == pytest.approx(r["hist_vol"], abs=1e-9)


def test_scaling_before_shifting_is_what_makes_both_exact():
    """
    ORDER TEST. Scaling changes the mean, so the drift must be solved after
    the scale is fixed. Reversed, the CAGR misses by the scale factor -- a
    silent error worth 10-20% of terminal corpus over sixty years.
    """
    raw = [0.001, -0.002, 0.004, -0.001] * 500
    n, ppy = len(raw), 365.0
    out = E._solve(raw, 0.20 / math.sqrt(ppy), 0.09, ppy, n)
    assert st.pstdev(out) == pytest.approx(0.20 / math.sqrt(ppy), abs=1e-12)
    lv = [100.0]
    for x in out:
        lv.append(lv[-1] * math.exp(x))
    assert M.cagr(lv, ppy) == pytest.approx(0.09, abs=1e-9)


def test_a_delta_that_destroys_the_return_is_refused():
    dates, nav = gbm(365 * 19, 0.05, 0.10, seed=3)
    with pytest.raises(E.SynthesisError):
        E.synthesize({"d": fund(nav, dates, "Liquid Fund", 2.0)}, 20)


def test_too_little_history_is_refused_rather_than_replayed():
    dates, nav = gbm(200, 0.11, 0.20, seed=4)
    with pytest.raises(E.SynthesisError):
        E.synthesize({"eq": fund(nav, dates, "Flexi Cap Fund", 0.0125)}, 20)


# ── cross-fund correlation ──────────────────────────────────────────────────

def test_correlation_between_funds_survives_into_the_synthetic_stretch():
    """
    THE ONE THE UPLOADED ENGINE COULD NOT PASS AT ALL, because it synthesized
    each series on its own. A two-purse model whose debt and equity futures
    are independent will understate every joint drawdown it is built to study.
    """
    dates, a, b = two_correlated(365 * 19, rho=0.8, seed=5)
    real = correlation(M.log_returns(M.series_of(a, dates)),
                       M.log_returns(M.series_of(b, dates)))
    out = E.synthesize({"eq": fund(a, dates, "Flexi Cap Fund", 0.0125),
                        "dt": fund(b, dates, "Corporate Bond Fund", 0.010)}, 20)
    got = correlation(rets(out["eq"]), rets(out["dt"]))
    assert real > 0.5
    assert got == pytest.approx(real, abs=0.08), f"real {real:.3f} got {got:.3f}"


def test_synthesizing_funds_separately_destroys_that_correlation():
    """Pins WHY the shared index exists, by showing the alternative."""
    dates, a, b = two_correlated(365 * 19, rho=0.8, seed=6)
    o1 = E.synthesize({"eq": fund(a, dates, "Flexi Cap Fund", 0.0125)}, 20,
                      seed=11)
    o2 = E.synthesize({"dt": fund(b, dates, "Corporate Bond Fund", 0.010)}, 20,
                      seed=22)
    assert abs(correlation(rets(o1["eq"]), rets(o2["dt"]))) < 0.15


# ── reproducibility ─────────────────────────────────────────────────────────

def test_the_same_seed_gives_a_byte_identical_path():
    dates, nav = gbm(365 * 19, 0.11, 0.20, seed=7)
    f = {"eq": fund(nav, dates, "Flexi Cap Fund", 0.0125)}
    assert E.synthesize(f, 20, seed=99)["eq"]["nav_levels"] == \
        E.synthesize(f, 20, seed=99)["eq"]["nav_levels"]


def test_different_seeds_give_different_paths():
    dates, nav = gbm(365 * 19, 0.11, 0.20, seed=8)
    f = {"eq": fund(nav, dates, "Flexi Cap Fund", 0.0125)}
    assert E.synthesize(f, 20, seed=1)["eq"]["nav_levels"] != \
        E.synthesize(f, 20, seed=2)["eq"]["nav_levels"]


def test_no_global_random_state_is_touched():
    """The upstream engine seeded numpy globally; two runs interfered."""
    dates, nav = gbm(365 * 19, 0.11, 0.20, seed=9)
    f = {"eq": fund(nav, dates, "Flexi Cap Fund", 0.0125)}
    random.seed(123)
    before = random.random()
    E.synthesize(f, 20, seed=55)
    random.seed(123)
    assert random.random() == before


# ── screening ───────────────────────────────────────────────────────────────

def test_a_reverting_print_is_dropped_from_the_source_returns():
    dates, nav = gbm(365 * 19, 0.06, 0.004, seed=10, start=100.0)
    keys = list(dates)
    j = 5000
    scale = 1.0646
    for k in range(j, len(keys)):
        nav[keys[k]] *= scale
    for k in range(j + 1, len(keys)):
        nav[keys[k]] /= scale                       # spike and give-back
    _, r_screened, dropped = E._clean_returns(nav, dates, "Liquid Fund", True)
    _, r_raw, _ = E._clean_returns(nav, dates, "Liquid Fund", False)
    assert len(dropped) == 2
    assert len(r_raw) - len(r_screened) == 2
    assert max(abs(x) for x in r_screened) < 0.02


def test_a_credit_writedown_is_kept():
    """DHFL must survive. A debt fund that never writes anything down is not
    a debt fund, and a plan tested against one is not tested."""
    dates, nav = gbm(365 * 19, 0.07, 0.010, seed=11)
    keys = list(dates)
    for k in range(5000, len(keys)):
        nav[keys[k]] *= 0.9123                      # permanent step down
    _, r, dropped = E._clean_returns(nav, dates, "Low Duration Fund", True)
    assert dropped == []
    assert min(r) < -0.08


def test_screening_can_be_switched_off():
    dates, nav = gbm(365 * 19, 0.06, 0.004, seed=12)
    a = E._clean_returns(nav, dates, "Liquid Fund", True)[1]
    b = E._clean_returns(nav, dates, "Liquid Fund", False)[1]
    assert len(b) >= len(a)


# ── the frame ───────────────────────────────────────────────────────────────

def test_forward_dates_are_calendar_days_and_handle_leap_years():
    assert E.forward_dates("2024-02-27", 3) == \
        ["2024-02-28", "2024-02-29", "2024-03-01"]
    assert E.forward_dates("2023-02-27", 3) == \
        ["2023-02-28", "2023-03-01", "2023-03-02"]
    assert E.forward_dates("2024-12-30", 3) == \
        ["2024-12-31", "2025-01-01", "2025-01-02"]


def test_the_horizon_is_measured_in_calendar_years_not_252_day_years():
    dates, nav = gbm(365 * 19, 0.11, 0.20, seed=13)
    out = E.synthesize({"eq": fund(nav, dates, "Flexi Cap Fund", 0.0125)}, 10)
    r = out["eq"]
    assert r["periods_per_year"] > 340
    assert len(r["nav_levels"]) == pytest.approx(10 * 365, rel=0.02)


# ── the harness ─────────────────────────────────────────────────────────────

def test_the_generators_own_output_survives_the_ensemble_verifier():
    """
    THE POINT OF ALL OF IT. If this fails the generator is not fit to use, and
    if it can never fail the harness is not fit to judge -- which is why the
    next test exists.
    """
    dates, nav = gbm(365 * 19, 0.11, 0.20, seed=14)
    f = {"eq": fund(nav, dates, "Flexi Cap Fund", 0.0)}
    ens = E.synthesize_ensemble(f, 19, n_paths=10, seed=500)
    paths = [(p["eq"]["nav"], p["eq"]["dates"]) for p in ens]
    ok, findings = V.verify_ensemble(nav, dates, paths)
    est = [x for x in findings if x["kind"] == "estimated"]
    assert not any(x["verdict"] == "FAIL" for x in est), \
        V.format_findings(est)


def test_the_verifier_still_rejects_an_ensemble_from_the_wrong_history():
    dates, nav = gbm(365 * 19, 0.11, 0.20, seed=15)
    _, other = gbm(365 * 19, 0.02, 0.45, seed=16)
    f = {"x": fund(other, dates, "Flexi Cap Fund", 0.0)}
    ens = E.synthesize_ensemble(f, 19, n_paths=10, seed=600)
    paths = [(p["x"]["nav"], p["x"]["dates"]) for p in ens]
    ok, _ = V.verify_ensemble(nav, dates, paths)
    assert not ok


def test_an_ensemble_returns_the_number_of_paths_asked_for():
    dates, nav = gbm(365 * 19, 0.11, 0.20, seed=17)
    f = {"eq": fund(nav, dates, "Flexi Cap Fund", 0.0125)}
    assert len(E.synthesize_ensemble(f, 5, n_paths=6)) == 6


# ── regime-conditioned sampling ─────────────────────────────────────────────

def test_volatility_states_label_calm_and_crisis_apart():
    """
    Calm values must VARY. A constant-magnitude calm stretch puts every
    tercile cut on the same number, `v >= cut` is true everywhere, and the
    whole series labels as crisis -- a degenerate case worth knowing about
    because a stale feed produces exactly that shape.
    """
    rng = random.Random(40)
    calm = [rng.gauss(0, 0.004) for _ in range(1000)]
    storm = [rng.gauss(0, 0.05) for _ in range(400)]
    st_ = E.volatility_states(calm + storm + calm, n_states=3, window=21)
    # The CALM stretch spans all three states -- terciles cut it into thirds --
    # so the testable property is that the storm sits above it on average,
    # not that any single calm index reads zero.
    calm_mean = st.fmean(st_[100:900] + st_[1600:2300])
    storm_mean = st.fmean(st_[1050:1350])
    assert storm_mean > calm_mean + 1.0, f"{storm_mean} vs {calm_mean}"
    assert max(st_[1050:1350]) == 2


def test_the_transition_matrix_rows_are_probabilities():
    m = E.transition_matrix([0, 0, 1, 1, 2, 2, 1, 0], n_states=3)
    assert len(m) == 3
    for row in m:
        assert sum(row) == pytest.approx(1.0)
        assert all(0.0 < x < 1.0 for x in row)


def test_an_unseen_regime_pair_is_improbable_not_impossible():
    """Laplace-smoothed: nineteen years need not contain every transition."""
    m = E.transition_matrix([0] * 100, n_states=3)
    assert 0.0 < m[0][2] < 0.05


def test_regime_sampling_preserves_clustering_that_naive_blocks_destroy():
    """
    THE FIX, MEASURED. Persistence lives in the SEQUENCE OF REGIMES rather
    than inside a block, so clusters survive block boundaries. Naive sampling
    at the same block length breaks them, and the ACF of |r| decays faster.
    """
    rng = random.Random(21)
    r = []
    for i in range(7000):
        vol = 0.035 if (i // 900) % 2 else 0.004     # long alternating regimes
        r.append(rng.gauss(0.0002, vol))
    dates = daily_dates(len(r) + 1)
    lv = [100.0]
    for x in r:
        lv.append(lv[-1] * math.exp(x))
    nav = {d: v for d, v in zip(dates, lv)}
    f = {"x": fund(nav, dates, "Flexi Cap Fund", 0.0)}
    # Averaged over seeds: a single path's ACF exponent is noisy enough that
    # one draw would make this test a coin flip rather than a measurement.
    reg, nai = [], []
    for sd_ in range(5):
        a = E.synthesize(f, 19, seed=5 + sd_, block_days=125, regime=True)["x"]
        b = E.synthesize(f, 19, seed=5 + sd_, block_days=125, regime=False)["x"]
        reg.append(M.acf_abs_decay_exponent(M.log_returns(a["nav_levels"])))
        nai.append(M.acf_abs_decay_exponent(M.log_returns(b["nav_levels"])))
    b_real = M.acf_abs_decay_exponent(M.log_returns(M.series_of(nav, dates)))
    assert abs(st.fmean(reg) - b_real) < abs(st.fmean(nai) - b_real), \
        f"real {b_real:.3f} regime {st.fmean(reg):.3f} naive {st.fmean(nai):.3f}"


def test_the_regime_reference_defaults_to_the_most_volatile_fund():
    """Regimes are market-wide, and a liquid fund's local volatility barely
    moves between 2007 and 2009. Read them off the series that shows them."""
    de, ne = gbm(365 * 19, 0.11, 0.20, seed=22)
    _, nd = gbm(365 * 19, 0.07, 0.004, seed=23)
    out = E.synthesize({"eq": fund(ne, de, "Flexi Cap Fund", 0.0),
                        "lq": fund(nd, de, "Liquid Fund", 0.0)}, 19)
    assert out["eq"]["reference"] == "eq"


def test_block_counts_are_reported_so_novelty_can_be_judged():
    dates, nav = gbm(365 * 19, 0.11, 0.20, seed=24)
    out = E.synthesize({"eq": fund(nav, dates, "Flexi Cap Fund", 0.0)}, 19,
                       block_days=125)
    assert out["eq"]["blocks"] > 30


# ── the accrual generator ───────────────────────────────────────────────────

def test_accrual_categories_are_recognised():
    for c in ("Liquid Fund", "Overnight Fund", "Money Market Fund",
              "Ultra Short Duration Fund"):
        assert E.is_accrual(c)
    for c in ("Corporate Bond Fund", "Flexi Cap Fund", "Low Duration Fund"):
        assert not E.is_accrual(c)


def test_decomposition_adds_back_to_the_original():
    r = [0.001, 0.002, -0.0005, 0.003] * 100
    level, resid = E.decompose_accrual(r, window=63)
    for i in range(len(r)):
        assert level[i] + resid[i] == pytest.approx(r[i], abs=1e-15)


def test_ar1_coefficient_recovers_a_planted_persistence():
    rng = random.Random(25)
    phi, x, xs = 0.95, 0.0, []
    for _ in range(50000):
        x = phi * x + rng.gauss(0, 1)
        xs.append(x)
    assert E.ar1_coefficient(xs) == pytest.approx(phi, abs=0.02)


def test_the_simulated_factor_is_standardised():
    f = E.simulate_factor(200000, 0.99, random.Random(26))
    assert st.fmean(f) == pytest.approx(0.0, abs=0.05)
    assert st.pstdev(f) == pytest.approx(1.0, abs=0.05)


def test_a_more_persistent_factor_moves_more_slowly():
    """
    NOT "wanders further" -- both are standardised to unit variance, so the
    persistent one moves SLOWLY rather than far. Measured on the span it comes
    out narrower, which is the opposite of the intuition and the reason this
    test names the property it actually tests.
    """
    a = E.simulate_factor(50000, 0.90, random.Random(27))
    b = E.simulate_factor(50000, 0.9995, random.Random(27))
    assert E.ar1_coefficient(b) > E.ar1_coefficient(a)
    assert st.pstdev(a) == pytest.approx(st.pstdev(b), abs=0.15)


def test_accrual_funds_are_flagged_and_others_are_not():
    dates, ne = gbm(365 * 19, 0.11, 0.20, seed=28)
    _, nl = gbm(365 * 19, 0.07, 0.004, seed=29)
    out = E.synthesize({"eq": fund(ne, dates, "Flexi Cap Fund", 0.0),
                        "lq": fund(nl, dates, "Liquid Fund", 0.0)}, 19)
    assert out["lq"]["accrual"] and not out["eq"]["accrual"]


def test_the_accrual_path_keeps_the_exact_moments():
    """The decomposition must not cost the two criteria that are solved for."""
    dates, nav = gbm(365 * 19, 0.07, 0.004, seed=30)
    r = E.synthesize({"lq": fund(nav, dates, "Liquid Fund", 0.010)}, 19)["lq"]
    assert M.cagr(r["nav_levels"], r["periods_per_year"]) == \
        pytest.approx(r["target_cagr"], abs=1e-9)
    assert M.volatility(r["nav_levels"], r["periods_per_year"]) == \
        pytest.approx(r["hist_vol"], abs=1e-9)


def test_the_accrual_generator_can_be_switched_off():
    dates, nav = gbm(365 * 19, 0.07, 0.004, seed=31)
    f = {"lq": fund(nav, dates, "Liquid Fund", 0.0)}
    assert not E.synthesize(f, 19, accrual=False)["lq"]["accrual"]


def accruing(n, base, seed, noise=0.00004):
    """A series that ACCRUES: return = slowly-wandering yield + small noise.

    A GBM cannot stand in for this. The 63-day level of i.i.d. returns carries
    almost no variance, so the shared yield factor would have nothing to
    share and the test would pass or fail for reasons unrelated to the code.
    """
    rng = random.Random(seed)
    lv, y = [100.0], base / 365.0
    for _ in range(n):
        y = 0.9995 * y + 0.0005 * (base / 365.0) + rng.gauss(0, 4e-6)
        lv.append(lv[-1] * math.exp(max(0.0, y) + rng.gauss(0, noise)))
    dates = daily_dates(len(lv))
    return dates, {k: v for k, v in zip(dates, lv)}


def test_two_accrual_funds_share_one_yield_factor():
    """
    The 0.617 correlation between the real liquid and money market slots IS
    the yield level they both track. Simulating a factor each would leave two
    money market funds with unrelated futures.
    """
    dates, a = accruing(365 * 19, 0.070, seed=32)
    _, b = accruing(365 * 19, 0.065, seed=33)
    out = E.synthesize({"p": fund(a, dates, "Liquid Fund", 0.0),
                        "q": fund(b, dates, "Money Market Fund", 0.0)}, 19,
                       yield_phi=0.9995)
    assert out["p"]["accrual"] and out["q"]["accrual"]
    assert correlation(rets(out["p"]), rets(out["q"])) > 0.3
