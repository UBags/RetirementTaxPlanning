"""
Phase 1.2 — network/cache isolation and RNG determinism for monte_carlo.

Three things are pinned here.

1. **The isolation actually isolates.** Outbound TCP raises; the MC NAV caches
   point at a tmpdir, not at the owner's `mc_nifty50_nav.csv`. These are
   tested because the failure mode is silent: every equity fetcher ends in
   `except Exception: return None`, so an unblocked network and a blocked one
   both produce a green suite — one of them just quietly used real, changing,
   machine-dependent data.

2. **What offline behaviour actually is.** With TCP blocked,
   `run_monte_carlo(use_bootstrap=True)` warns and falls back to log-normal.
   That is correct, and it is also the admission that the block-bootstrap
   path — the default, the one the owner uses — has never been exercised by a
   test.

3. **The bootstrap path, against frozen history.** `mc_history.json` is a
   committed capture of the two input series, so the bootstrap can run
   deterministically and be snapshotted like the engine is. Generate it with
   `python scripts/capture_mc_history.py`; without it these tests skip rather
   than inventing numbers.

The snapshot here is thin on purpose — percentiles and ruin probability, not
the full (n_sims, 30) array. It exists to catch "someone changed the clamp
order" or "someone reseeded", not to freeze float noise.
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path

import numpy as np
import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "mc_history.json"
SNAPSHOT_DIR = Path(__file__).parent / "snapshots"
CAPTURE_HINT = (
    "tests/golden/fixtures/mc_history.json is absent. Generate it once with "
    "`python scripts/capture_mc_history.py` (that script is allowed to use "
    "the network; tests are not)."
)


# ── 1. the isolation isolates ────────────────────────────────────────────────

def test_outbound_tcp_is_blocked():
    import socket
    with pytest.raises(RuntimeError, match="Outbound TCP blocked"):
        socket.create_connection(("api.mfapi.in", 443), timeout=1)

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(RuntimeError, match="Outbound TCP blocked"):
            s.connect(("127.0.0.1", 9))
    finally:
        s.close()


def test_unix_sockets_are_not_blocked():
    """Headless Qt and DBus need AF_UNIX; the guard must not be a blunt
    instrument that breaks them."""
    import socket
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        with pytest.raises(OSError):        # nothing listening — but NOT RuntimeError
            s.connect("/tmp/definitely-not-a-real-socket-abc123")
    finally:
        s.close()


def test_mc_caches_point_outside_the_project(project_root):
    """
    A test run must neither read the owner's cache (results would depend on
    when it was last refreshed) nor let _save_equity_cache() overwrite it.
    """
    import monte_carlo as mc
    for name in ("_EQUITY_CACHE_CSV", "_DEBT_CACHE_CSV"):
        p = Path(getattr(mc, name)).resolve()
        assert project_root.resolve() not in p.parents, (
            f"{name} still points inside the project at {p} — a test run can "
            "read or clobber the real NAV cache")


def test_equity_fetch_degrades_to_none_offline():
    """
    Not a tautology: it pins that the fetchers SWALLOW the network error
    rather than propagating it. That swallowing is why the missing isolation
    was invisible, and if it ever changes to a raise, run_monte_carlo() stops
    falling back and starts crashing.
    """
    import monte_carlo as mc
    assert mc._fetch_nifty50_annual_returns() is None


def test_debt_history_needs_no_network():
    """The debt series is embedded in get_funds_data.py, so it must survive
    the block. If this skips, the bootstrap fixture cannot be built either."""
    import monte_carlo as mc
    dbt = mc._fetch_debt_index_annual_returns()
    if dbt is None:
        pytest.skip("get_funds_data.py not importable — debt history unavailable")
    assert len(dbt) > 5
    assert np.isfinite(dbt).all()


@pytest.mark.slow
def test_bootstrap_falls_back_to_lognormal_offline(scenario):
    """
    The honest statement of what the suite currently covers: with no network
    and no fixture, the default path is NOT being exercised.
    """
    from monte_carlo import run_monte_carlo
    label, state = scenario
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        res = run_monte_carlo(state, n_sims=80, seed=3, use_bootstrap=True)
    assert res.method_used == "log_normal", (
        f"{label}: bootstrap ran offline — the network block is not in effect")
    assert any("bootstrap" in str(w.message).lower() for w in caught), (
        f"{label}: fell back to log-normal without warning the caller")


# ── 2. RNG determinism ───────────────────────────────────────────────────────

@pytest.mark.slow
def test_same_seed_same_everything(scenario):
    from monte_carlo import run_monte_carlo
    label, state = scenario
    a = run_monte_carlo(state, n_sims=120, seed=7, use_bootstrap=False)
    b = run_monte_carlo(state, n_sims=120, seed=7, use_bootstrap=False)
    assert np.array_equal(a.corpus_raw, b.corpus_raw), label
    assert a.ruin_probability == b.ruin_probability, label


@pytest.mark.slow
def test_different_seed_different_paths(scenario):
    """Guards the opposite failure: a seed that is accepted and ignored."""
    from monte_carlo import run_monte_carlo
    label, state = scenario
    a = run_monte_carlo(state, n_sims=120, seed=7, use_bootstrap=False)
    b = run_monte_carlo(state, n_sims=120, seed=8, use_bootstrap=False)
    assert not np.array_equal(a.corpus_raw, b.corpus_raw), (
        f"{label}: changing the seed changed nothing — seeding is not wired "
        "through to the generators")


# ── 3. the bootstrap path, against frozen history ────────────────────────────

@pytest.fixture(scope="session")
def mc_history():
    if not FIXTURE.exists():
        pytest.skip(CAPTURE_HINT)
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    eq = np.array(data["equity_annual_returns"], dtype=float)
    dbt = np.array(data["debt_annual_returns"], dtype=float)
    assert len(eq) >= 15, "frozen equity history too short to bootstrap"
    assert len(dbt) >= 5, "frozen debt history too short to bootstrap"
    return eq, dbt


@pytest.fixture
def frozen_bootstrap(mc_history, monkeypatch):
    """Feed the committed history to run_monte_carlo in place of the fetchers."""
    import monte_carlo as mc
    eq, dbt = mc_history
    monkeypatch.setattr(mc, "_fetch_nifty50_annual_returns", lambda: eq)
    monkeypatch.setattr(mc, "_fetch_debt_index_annual_returns", lambda: dbt)
    return eq, dbt


@pytest.mark.slow
def test_bootstrap_runs_against_frozen_history(frozen_bootstrap, scenario):
    from monte_carlo import run_monte_carlo
    label, state = scenario
    res = run_monte_carlo(state, n_sims=200, seed=42, use_bootstrap=True)
    assert res.method_used == "block_bootstrap", (
        f"{label}: fell back to log-normal despite frozen history being supplied")
    assert res.n_equity_years == len(frozen_bootstrap[0])
    # the §2.2 band must hold on this path too, not just the log-normal one
    assert np.isfinite(res.corpus_p50).all()
    assert (res.caps > res.floors).all()


@pytest.mark.slow
def test_bootstrap_percentiles_snapshot(frozen_bootstrap, scenario, request):
    """
    The point of Phase 1.2: MC output becomes comparable run-to-run.

    Regenerate deliberately, as with the engine snapshots:
        pytest tests/golden/test_mc_isolation.py --regen-golden -q
    and review the diff. A snapshot regenerated without review is worth
    nothing.

    THE SCHEDULE IS SOLVED FIRST, as the application does. Since Phase B
    state.annual_requirements is a NET target, and run_monte_carlo() called
    without a gross_schedule falls back to reading it as a GROSS withdrawal
    -- the pre-Phase-B meaning. A snapshot taken that way pins behaviour
    nothing runs, which is the objection that moved the engine snapshots onto
    project_plan. Monte Carlo stress-tests the plan the deterministic solve
    chose; it does not re-plan per path.

    KNOWN COVERAGE LIMIT. Every fixture scenario is 100% debt, so this
    snapshot is sensitive to the DEBT series and completely insensitive to
    the equity one — perturbing a Nifty year moves it by exactly zero. See
    test_equity_history_is_dead_weight_for_all_debt_portfolios below. It
    still catches band changes, clamp-order changes and reseeding, which is
    what §2.2 needed and did not have.
    """
    from monte_carlo import run_monte_carlo
    from net_income_plan import project_plan
    label, state = scenario
    _, _, _, _, solution = project_plan(state)
    res = run_monte_carlo(state, n_sims=500, seed=42, use_bootstrap=True,
                          gross_schedule=solution.gross)

    rows = ["fy,corpus_p5,corpus_p50,corpus_p95,ruin_by_fy"]
    for i in range(30):
        rows.append(f"{i + 1},{res.corpus_p5[i]:.6f},{res.corpus_p50[i]:.6f},"
                    f"{res.corpus_p95[i]:.6f},{res.ruin_by_fy[i]:.6f}")
    rows.append(f"# ruin_probability={res.ruin_probability:.6f}")
    rows.append(f"# band_fy1=[{res.floors[0]:.6f},{res.caps[0]:.6f}] "
                f"sigma_fy1={res.sigmas[0]:.6f}")
    rows.append(f"# method={res.method_used} n_eq={res.n_equity_years} "
                f"n_dbt={res.n_debt_years} block={res.block_length}")
    body = "\n".join(rows) + "\n"

    snap = SNAPSHOT_DIR / f"mc_{label}_bootstrap.csv"
    if request.config.getoption("--regen-golden"):
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        snap.write_text(body, encoding="utf-8")
        pytest.skip(f"regenerated {snap.name}")

    if not snap.exists():
        pytest.skip(f"{snap.name} absent — create it with --regen-golden")

    expected = snap.read_text(encoding="utf-8")
    if expected == body:
        return

    diffs = [f"  {e} -> {g}"
             for e, g in zip(expected.splitlines(), body.splitlines()) if e != g]
    pytest.fail(
        f"{label}: Monte Carlo bootstrap output moved.\n"
        f"{len(diffs)} line(s) differ, first few:\n" + "\n".join(diffs[:8]) +
        "\n\nIf this was intended (a §2.2-style band change, a new financial "
        "year captured into mc_history.json), regenerate with --regen-golden "
        "as a separate reviewed commit."
    )


# ── 4. what the bootstrap path actually depends on ───────────────────────────

def test_equity_history_is_dead_weight_for_all_debt_portfolios(
        frozen_bootstrap, scenario):
    """
    Every fixture scenario holds 100% debt, so `_block_bootstrap_returns()`
    weights the Nifty series by (w_equity + w_other) = 0 and the entire equity
    fetch is multiplied away.

    That fetch — six scheme codes, mfapi then the AMFI portal, the only
    outbound request the program makes, and the sole reason Phase 1.2 exists —
    contributes nothing to these plans.

    This test documents the fact rather than asserting it is desirable. It
    fails informatively the day a scenario holds equity or "other", at which
    point the equity series starts mattering and the snapshot above gains
    coverage it does not currently have.
    """
    import monte_carlo as mc
    from monte_carlo import run_monte_carlo
    label, state = scenario

    w_eq = state.total_equity_allocation()
    w_oth = state.total_other_allocation()
    if (w_eq + w_oth) > 0:
        pytest.skip(f"{label} holds equity/other — the equity series is live here")

    eq, dbt = frozen_bootstrap
    base = run_monte_carlo(state, n_sims=300, seed=42, use_bootstrap=True)

    import numpy as _np
    mangled = _np.asarray(eq, dtype=float) + 0.5          # +50pp every year
    mc._fetch_nifty50_annual_returns = lambda: mangled
    try:
        after = run_monte_carlo(state, n_sims=300, seed=42, use_bootstrap=True)
    finally:
        mc._fetch_nifty50_annual_returns = lambda: eq

    assert _np.array_equal(base.corpus_p50, after.corpus_p50), (
        f"{label}: adding 50pp to every Nifty year changed the projection, so "
        "the equity weight is no longer zero — update this test and the "
        "snapshot's coverage note")


@pytest.mark.slow
def test_bootstrap_is_refused_when_the_irrelevant_fetch_fails(
        frozen_bootstrap, scenario):
    """
    The actionable consequence of the test above.

    `run_monte_carlo()` gates on `eq_hist is not None and dbt_hist is not
    None`. For a 100%-debt portfolio that means an offline owner silently gets
    the log-normal fallback instead of the bootstrap — because a series that
    would have been multiplied by zero could not be downloaded.

    Filed as an observation, not fixed here: the gate belongs in §2.x, and
    changing it changes which generator the owner's real runs use.
    """
    import monte_carlo as mc
    from monte_carlo import run_monte_carlo
    label, state = scenario
    if (state.total_equity_allocation() + state.total_other_allocation()) > 0:
        pytest.skip(f"{label} holds equity — the equity fetch is load-bearing")

    eq, _dbt = frozen_bootstrap
    mc._fetch_nifty50_annual_returns = lambda: None
    try:
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            res = run_monte_carlo(state, n_sims=80, seed=5, use_bootstrap=True)
    finally:
        mc._fetch_nifty50_annual_returns = lambda: eq

    assert res.method_used == "log_normal", (
        f"{label}: the equity-fetch gate no longer forces the fallback — if "
        "that was fixed deliberately, delete this test and say so in the "
        "commit message")


@pytest.mark.slow
def test_band_clamps_a_large_share_of_bootstrap_draws(frozen_bootstrap, scenario):
    """
    Measurement, with a sanity ceiling rather than a target.

    `_per_chunk_sigmas()` derives sigma from the FUNDS' own std_dev (~1.0-1.5%
    for diversified debt funds), while the bootstrap draws from raw INDEX
    history (debt index sigma ~3%). The band is therefore narrower than the
    distribution it is clamping, and a large fraction of draws land exactly on
    an edge — measured at roughly a third on the fixture corpus.

    That is worth knowing, because the module docstring advertises that the
    bootstrap "preserves historical volatility / tail shape". At these rates
    it substantially does not. §2.2 widened the effect by adding the cap; it
    did not create it.

    The ceiling below is deliberately loose. It fails only if the band
    collapses to the point where the bootstrap is a two-point distribution,
    which would make the whole method decorative.
    """
    import monte_carlo as mc
    label, state = scenario
    eq, dbt = frozen_bootstrap

    mu = np.array([state.get_return_rate(fy) for fy in range(1, 31)])
    sig = mc._per_chunk_sigmas(state)
    floors, caps = mu - 2.5 * sig, mu + 3.0 * sig
    w_eq = state.total_equity_allocation()
    w_dbt = state.total_debt_allocation()
    w_oth = state.total_other_allocation()
    tot = w_eq + w_dbt + w_oth
    if tot <= 0:
        pytest.skip(f"{label}: no allocation")

    port = mc._block_bootstrap_returns(
        eq_hist=np.asarray(eq, float), dbt_hist=np.asarray(dbt, float),
        w_equity=w_eq / tot, w_debt=w_dbt / tot, w_other=w_oth / tot,
        mu_det=mu, n_sims=1500, block_length=3,
        floors=floors, caps=caps, rng=np.random.default_rng(42),
    )
    at_edge = (np.isclose(port, floors[None, :]).mean()
               + np.isclose(port, caps[None, :]).mean())

    assert at_edge < 0.75, (
        f"{label}: {at_edge*100:.1f}% of bootstrap draws are pinned to the "
        "band edges. Above 75% the block bootstrap is a two-point "
        "distribution and the historical tail shape it claims to preserve is "
        "gone. Widen the band or derive sigma from the same series the "
        "bootstrap draws from.")
