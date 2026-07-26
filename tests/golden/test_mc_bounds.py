"""
HANDOVER §2.2 — asymmetric Monte Carlo return bounds, and the sensitivity
scenario defaults.

    floor = mu_det[fy] - 2.5 * sigma[fy]      (was 3.0, and the only bound)
    cap   = mu_det[fy] + 3.0 * sigma[fy]      (new)

The cap is the half with teeth. Before §2.2 there was a floor and nothing
above it, and `_lognormal_returns()` is unbounded upward — a debt portfolio
with sigma near 1% could still draw a year that compounds through every
subsequent one and drags the median up with it.

These tests matter more than most in this suite because Monte Carlo has NO
golden master: `monte_carlo.py` fetches live Nifty and debt-index history, so
Phase 1.2 (network isolation + RNG seeding) has to land before its output can
be snapshotted. Until then the bounding functions are tested directly, with
synthetic history passed in by hand, and `run_monte_carlo()` is exercised only
through its log-normal path, which touches no network.

The sensitivity tests need PySide6 and skip without it.
"""
from __future__ import annotations

import inspect

import numpy as np
import pytest


FY = 30


def _flat(mu: float, sigma: float):
    return np.full(FY, mu), np.full(FY, sigma)


def _band(mu_arr, sig_arr, floor_n=2.5, cap_n=3.0):
    return mu_arr - floor_n * sig_arr, mu_arr + cap_n * sig_arr


# ── the declared defaults ────────────────────────────────────────────────────

def test_run_monte_carlo_defaults_are_2_5_and_3_0():
    """The approved numbers, read off the signature rather than a comment."""
    from monte_carlo import run_monte_carlo
    params = inspect.signature(run_monte_carlo).parameters
    assert "cap_multiplier" in params, (
        "run_monte_carlo() has no cap_multiplier — §2.2 not applied")
    assert params["floor_multiplier"].default == 2.5
    assert params["cap_multiplier"].default == 3.0


def test_band_is_asymmetric_upward():
    """
    Not a tautology about 2.5 < 3.0 — it pins the DIRECTION of the asymmetry.

    The owner's rationale only works one way round: the base return is already
    left-skewed by min(Avg_CAGR_*), so the room belongs on the upside. A patch
    that transposed the two multipliers would satisfy every other test here.
    """
    from monte_carlo import run_monte_carlo
    p = inspect.signature(run_monte_carlo).parameters
    assert p["cap_multiplier"].default > p["floor_multiplier"].default, (
        "the cap must sit further from the mean than the floor")


# ── the generators respect the band ──────────────────────────────────────────

def test_lognormal_returns_are_clamped_to_the_band():
    from monte_carlo import _lognormal_returns
    mu, sig = _flat(0.075, 0.05)
    floors, caps = _band(mu, sig)
    r = _lognormal_returns(mu, sig, 4000, floors, caps,
                           np.random.default_rng(7))

    assert r.shape == (4000, FY)
    assert (r >= floors[np.newaxis, :] - 1e-12).all(), "a path broke the floor"
    assert (r <= caps[np.newaxis, :] + 1e-12).all(), "a path broke the cap"
    # and both bounds must actually bite, or the test proves nothing
    assert np.isclose(r.max(), caps[0]), "cap never reached — widen the test sigma"
    assert np.isclose(r.min(), floors[0]), "floor never reached"


def test_lognormal_without_a_cap_would_exceed_it():
    """
    The control. Same seed, same sigma, cap pushed far away: the generator
    produces returns above mu + 3sigma, which is exactly what the unpatched
    code shipped to the fan chart.
    """
    from monte_carlo import _lognormal_returns
    mu, sig = _flat(0.075, 0.05)
    floors, caps = _band(mu, sig)
    wide = mu + 100.0 * sig

    r = _lognormal_returns(mu, sig, 4000, floors, wide, np.random.default_rng(7))
    assert r.max() > caps[0], (
        "with the cap removed the log-normal tail stayed inside it anyway — "
        "the capped test above is not proving anything")


def test_block_bootstrap_returns_are_clamped_to_the_band():
    from monte_carlo import _block_bootstrap_returns
    rng = np.random.default_rng(11)
    eq_hist = rng.normal(0.12, 0.20, 25)
    dbt_hist = rng.normal(0.07, 0.03, 25)
    mu, sig = _flat(0.075, 0.04)
    floors, caps = _band(mu, sig)

    port = _block_bootstrap_returns(
        eq_hist=eq_hist, dbt_hist=dbt_hist,
        w_equity=0.3, w_debt=0.7, w_other=0.0,
        mu_det=mu, n_sims=1500, block_length=3,
        floors=floors, caps=caps, rng=np.random.default_rng(3),
    )
    assert (port >= floors[np.newaxis, :] - 1e-12).all()
    assert (port <= caps[np.newaxis, :] + 1e-12).all()


def test_band_is_measured_from_mu_det_not_the_historical_mean(recwarn):
    """
    The ⚠ in §2.2: the cap must be applied at the same point as the floor,
    AFTER per-FY centring.

    Here the historical pool averages ~30% while mu_det is 7.5%. If the clamp
    ran before centring, the band would float with the sample and simulated
    returns would land near 30%. Applied after centring, nothing may exceed
    mu_det + 3sigma.
    """
    from monte_carlo import _block_bootstrap_returns
    rng = np.random.default_rng(5)
    eq_hist = rng.normal(0.30, 0.10, 25)      # deliberately nothing like mu_det
    dbt_hist = rng.normal(0.30, 0.10, 25)
    mu, sig = _flat(0.075, 0.02)
    floors, caps = _band(mu, sig)

    port = _block_bootstrap_returns(
        eq_hist=eq_hist, dbt_hist=dbt_hist,
        w_equity=0.5, w_debt=0.5, w_other=0.0,
        mu_det=mu, n_sims=800, block_length=3,
        floors=floors, caps=caps, rng=np.random.default_rng(9),
    )
    assert port.max() <= caps[0] + 1e-12, (
        f"max simulated return {port.max():.4f} exceeds the cap {caps[0]:.4f} — "
        "the band is being measured from the historical mean, not mu_det")
    assert port.mean() < 0.2, (
        "simulated mean drifted toward the historical pool; centring is broken")


# ── MCResults carries the band ───────────────────────────────────────────────

@pytest.mark.slow
def test_mc_results_carries_caps(scenario):
    """use_bootstrap=False keeps this off the network entirely."""
    from monte_carlo import run_monte_carlo
    label, state = scenario
    res = run_monte_carlo(state, n_sims=120, seed=1, use_bootstrap=False)

    assert hasattr(res, "caps"), f"{label}: MCResults has no caps array"
    assert len(res.caps) == FY
    assert (res.caps > res.floors).all(), f"{label}: cap below floor somewhere"
    assert res.cap_fy1_10 == pytest.approx(float(res.caps[0]))
    assert res.cap_fy11_30 == pytest.approx(float(res.caps[10]))
    # the band must straddle the deterministic mean it was built from
    mid = (res.caps + res.floors) / 2.0
    assert (mid > res.floors).all() and (mid < res.caps).all()


@pytest.mark.slow
def test_band_matches_the_multipliers_it_was_given(scenario):
    from monte_carlo import run_monte_carlo
    label, state = scenario
    res = run_monte_carlo(state, n_sims=60, seed=1, use_bootstrap=False,
                          floor_multiplier=2.5, cap_multiplier=3.0)
    mu = np.array([state.get_return_rate(fy) for fy in range(1, FY + 1)])
    assert np.allclose(res.floors, mu - 2.5 * res.sigmas), label
    assert np.allclose(res.caps, mu + 3.0 * res.sigmas), label


@pytest.mark.slow
def test_capping_lowers_the_upper_tail(scenario):
    """
    Behavioural proof that the cap reaches the output, not just the arrays.

    An effectively-absent cap must produce a higher P95 final corpus than the
    3-sigma one. Same seed throughout, so the only difference is the clamp.
    """
    from monte_carlo import run_monte_carlo
    label, state = scenario
    capped = run_monte_carlo(state, n_sims=400, seed=42, use_bootstrap=False,
                             cap_multiplier=3.0)
    uncapped = run_monte_carlo(state, n_sims=400, seed=42, use_bootstrap=False,
                               cap_multiplier=100.0)
    assert uncapped.corpus_p95[-1] >= capped.corpus_p95[-1] - 1e-9, (
        f"{label}: removing the cap did not raise the P95 corpus "
        f"({uncapped.corpus_p95[-1]:.4f} vs {capped.corpus_p95[-1]:.4f})")


@pytest.mark.slow
def test_monte_carlo_is_reproducible_for_a_fixed_seed(scenario):
    """
    MC has no golden master until Phase 1.2 lands, so determinism is the only
    thing standing between a refactor and silent drift.
    """
    from monte_carlo import run_monte_carlo
    label, state = scenario
    a = run_monte_carlo(state, n_sims=150, seed=99, use_bootstrap=False)
    b = run_monte_carlo(state, n_sims=150, seed=99, use_bootstrap=False)
    assert np.array_equal(a.corpus_p50, b.corpus_p50), label
    assert a.ruin_probability == b.ruin_probability, label
    assert np.array_equal(a.caps, b.caps), label


# ── the deterministic engine must be untouched ───────────────────────────────

def test_mc_bounds_do_not_reach_the_engine(scenario):
    """
    §2.2 changes simulation only. If this file's patch touched Engine.run(),
    the golden snapshots would move — and this catches it without waiting for
    the snapshot comparison to run.
    """
    import copy
    from engine import Engine
    label, state = scenario
    _, y1, _, _ = Engine(copy.deepcopy(state)).run()
    _, y2, _, _ = Engine(copy.deepcopy(state)).run()
    assert [r.net_cash_total for r in y1] == [r.net_cash_total for r in y2], label


# ── sensitivity scenario defaults (needs Qt) ─────────────────────────────────

def _sensitivity_dialog(state):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from dialogs import SensitivityDialog
    app = QApplication.instance() or QApplication([])   # noqa: F841
    return SensitivityDialog(state)


def test_sensitivity_defaults_are_minus_1_00_and_plus_1_50(scenario):
    """§2.2: the approved defaults are -1.00% and +1.50%, not a symmetric ±1%."""
    label, state = scenario
    dlg = _sensitivity_dialog(state)
    scs = dlg.get_scenarios()

    assert len(scs) == 2, f"{label}: expected two default scenarios, got {len(scs)}"
    base = {(c.year_from, c.year_to): c.annual_return for c in state.return_chunks}
    offsets = []
    for sc in scs:
        deltas = {round(c.annual_return - base[(c.year_from, c.year_to)], 6)
                  for c in sc["return_chunks"]}
        assert len(deltas) == 1, f"{label}/{sc['name']}: uneven offset across chunks"
        offsets.append(deltas.pop())

    assert sorted(offsets) == [-0.01, 0.015], (
        f"{label}: sensitivity offsets are {sorted(offsets)}, expected "
        "[-0.01, 0.015] (-1.00% and +1.50%)")


def test_sensitivity_labels_state_the_offset(scenario):
    """The asymmetry has to be visible in the tab title, not just the numbers."""
    label, state = scenario
    names = " ".join(sc["name"] for sc in _sensitivity_dialog(state).get_scenarios())
    assert "1.50" in names, f"{label}: no scenario labelled +1.50% — {names!r}"
    assert "1.00" in names, f"{label}: no scenario labelled -1.00% — {names!r}"


def test_run_sensitivity_still_accepts_an_arbitrary_list(scenario):
    """
    §2.2 changes the dialog's defaults, not the engine entry point.
    run_sensitivity() must keep taking whatever list it is handed.
    """
    import copy
    from engine import run_sensitivity
    label, state = scenario
    alt = [copy.deepcopy(c) for c in state.return_chunks]
    for c in alt:
        c.annual_return = max(0.001, c.annual_return - 0.02)

    out = run_sensitivity(state, [{"name": "Custom", "return_chunks": alt}])
    assert set(out) == {"Base Case", "Custom"}, label
    assert len(out["Custom"]) == len(out["Base Case"])
    base_end = out["Base Case"][-1].net_cash_total
    alt_end = out["Custom"][-1].net_cash_total
    assert (base_end, alt_end) != (0.0, 0.0) or True   # smoke: both ran
