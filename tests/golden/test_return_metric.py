"""
HANDOVER §2.1 — the engine and the UI must quote the same per-fund return.

Before this change `Engine._fund_annual_return()` applied the point-in-time
5Y CAGR while `AllocationChunk.optimized_yield()` — the figure written into
`return_chunks`, displayed in the UI, and used as the Monte Carlo
deterministic mean — used min(Avg_CAGR_3Y, 5Y, 7Y, 10Y). Both were already
per-fund; they were simply different columns of the same fund data. The
planner therefore projected one number on screen and simulated another
(7.932% vs 6.813% on Opt2, on the inputs current when the decision was taken).

These tests pin the *metric*, not any particular projected number, so they
keep their meaning after the golden snapshots are regenerated. The snapshots
say something moved; these say what it moved to.

Two of them are documentation guards. That is deliberate: the misleading
`Worst_Exp_Ret_%` docstring in get_funds_data.py is the reason "Conservative
mode" was believed to be a stress test for as long as it was, and a comment
that nothing tests is a comment that drifts again.
"""
from __future__ import annotations

import pytest

from swp_planner.paths import PACKAGE_DIR

# ── helpers ──────────────────────────────────────────────────────────────────

def _mk_fund(**kw):
    from swp_planner.core.models import FundEntry
    base = dict(name="F", fund_type="debt", allocation=10.0)
    base.update(kw)
    return FundEntry(**base)


def _bare_engine(**kw):
    """An Engine over an empty AppState.

    Adequate for every branch of _fund_annual_return() that does not reach the
    category-average fallback, which is the only branch that touches state.
    """
    from swp_planner.core.engine import Engine
    from swp_planner.core.models import AppState
    return Engine(AppState(), **kw)


def _avg_min(fund):
    vals = [c for c in (fund.avg_cagr_3, fund.avg_cagr_5,
                        fund.avg_cagr_7, fund.avg_cagr_10)
            if c is not None and c > 0]
    return min(vals) if vals else None


def _weights(chunk):
    """(name -> weight) exactly as optimized_yield() would resolve them."""
    if chunk.target_weights:
        return {n: w for n, w in chunk.target_weights.items() if w > 0}
    return {f.name: f.allocation for f in chunk.funds if f.allocation > 0}


# ── the metric itself ────────────────────────────────────────────────────────

def test_historical_mode_uses_min_avg_cagr():
    """The headline requirement: min of the four rolling-average windows."""
    eng = _bare_engine()
    f = _mk_fund(cagr_5=6.53, cagr_10=7.29,
                 avg_cagr_3=7.52, avg_cagr_5=7.49,
                 avg_cagr_7=7.60, avg_cagr_10=7.63)
    assert eng._fund_annual_return(f, 1) == pytest.approx(7.49 / 100.0, abs=1e-12)


def test_historical_mode_is_flat_across_financial_years():
    """The metric is a property of the fund, not of the year."""
    eng = _bare_engine()
    f = _mk_fund(avg_cagr_3=8.0, avg_cagr_5=7.0, avg_cagr_7=9.0, avg_cagr_10=9.5)
    rates = [eng._fund_annual_return(f, fy) for fy in (1, 7, 15, 30)]
    assert len(set(rates)) == 1, f"rate varies by FY: {rates}"
    assert rates[0] == pytest.approx(0.07, abs=1e-12)


def test_historical_mode_ignores_missing_windows():
    """A fund too young for a 10Y window still gets the min of what it has."""
    eng = _bare_engine()
    f = _mk_fund(cagr_5=10.30,
                 avg_cagr_3=10.32, avg_cagr_5=10.53,
                 avg_cagr_7=10.48, avg_cagr_10=None)
    assert eng._fund_annual_return(f, 1) == pytest.approx(10.32 / 100.0, abs=1e-12)


def test_historical_mode_ignores_non_positive_windows():
    """A zero or negative Avg_CAGR is missing data, not a 0% return.

    Without this the min() would latch onto the sentinel and project a fund at
    zero growth for thirty years.
    """
    eng = _bare_engine()
    f = _mk_fund(avg_cagr_3=0.0, avg_cagr_5=-1.2,
                 avg_cagr_7=7.60, avg_cagr_10=7.63)
    assert eng._fund_annual_return(f, 1) == pytest.approx(7.60 / 100.0, abs=1e-12)


def test_falls_back_to_point_in_time_cagr_when_no_rolling_averages():
    """Project files predating the Avg_CAGR_* columns must project as before."""
    eng = _bare_engine()
    f = _mk_fund(cagr_1=5.45, cagr_3=7.34, cagr_5=6.55, cagr_10=7.26)
    assert eng._fund_annual_return(f, 1) == pytest.approx(6.55 / 100.0, abs=1e-12)


@pytest.mark.parametrize("kwargs,expected", [
    (dict(cagr_5=6.55, cagr_10=7.26, cagr_3=7.34, cagr_1=5.45), 6.55),
    (dict(cagr_10=7.26, cagr_3=7.34, cagr_1=5.45), 7.26),
    (dict(cagr_3=7.34, cagr_1=5.45), 7.34),
    (dict(cagr_1=5.45), 5.45),
])
def test_fallback_chain_order_is_5y_10y_3y_1y(kwargs, expected):
    """The pre-§2.1 preference order is retained verbatim beneath the new one."""
    eng = _bare_engine()
    f = _mk_fund(**kwargs)
    assert eng._fund_annual_return(f, 1) == pytest.approx(expected / 100.0, abs=1e-12)


def test_falls_back_to_category_average_when_no_cagr_data_at_all(monkeypatch):
    eng = _bare_engine()
    monkeypatch.setattr(eng, "_category_annual_return",
                        lambda fy, ft: 0.0777)
    assert eng._fund_annual_return(_mk_fund(), 1) == pytest.approx(0.0777)


def test_conservative_mode_still_reads_worst_exp_ret():
    """§2.1 changes the Historical branch only."""
    eng = _bare_engine(conservative_mode=True)
    f = _mk_fund(worst_exp_ret=6.20,
                 avg_cagr_3=7.52, avg_cagr_5=7.49,
                 avg_cagr_7=7.60, avg_cagr_10=7.63)
    assert eng._fund_annual_return(f, 1) == pytest.approx(6.20 / 100.0, abs=1e-12)


def test_sensitivity_override_still_beats_the_fund_metric():
    """run_sensitivity() passes alt_return_chunks; it must win over per-fund data."""
    from swp_planner.core.models import ReturnChunk
    eng = _bare_engine(alt_return_chunks=[ReturnChunk(year_from=1, year_to=30,
                                                      annual_return=0.05)])
    f = _mk_fund(avg_cagr_3=7.52, avg_cagr_5=7.49,
                 avg_cagr_7=7.60, avg_cagr_10=7.63)
    assert eng._fund_annual_return(f, 1) == pytest.approx(0.05, abs=1e-12)


def test_monthly_factor_compounds_back_to_the_annual_rate():
    eng = _bare_engine()
    f = _mk_fund(avg_cagr_3=7.52, avg_cagr_5=7.49,
                 avg_cagr_7=7.60, avg_cagr_10=7.63)
    m = eng._fund_monthly_factor(f, 1)
    assert m ** 12 == pytest.approx(1.0 + eng._fund_annual_return(f, 1), rel=1e-12)


# ── the acceptance test: engine rate == the rate the UI shows ────────────────

def test_engine_rate_matches_optimized_yield(scenario):
    """
    THE point of §2.1.

    `AllocationChunk.optimized_yield()` is what populates `return_chunks` and
    what the UI displays. The weight-average of the rate the engine actually
    applies per fund must reproduce it exactly.

    Chunks containing a fund with no positive Avg_CAGR_* are skipped: below
    that line the two sides use deliberately different fallbacks (the engine
    prefers point-in-time cagr_5, optimized_yield() takes min(cagr_3/5/7/10)),
    and reconciling the fallbacks was not part of the specification.
    """
    label, state = scenario
    eng = _bare_engine()

    checked = 0
    for i, chunk in enumerate(state.allocation_chunks):
        weights = _weights(chunk)
        if not weights:
            continue
        fmap = {f.name: f for f in chunk.funds}
        known = [n for n in weights if n in fmap]
        if len(known) != len(weights):
            pytest.skip(f"{label} chunk {i}: weights name a fund not in the chunk")
        if any(_avg_min(fmap[n]) is None for n in known):
            continue                      # fallback territory; see docstring

        total = sum(weights[n] for n in known)
        wtd = sum(weights[n] * eng._fund_annual_return(fmap[n], chunk.year_from)
                  for n in known) / total

        assert wtd == pytest.approx(chunk.optimized_yield(), abs=1e-12), (
            f"{label} chunk {i} (FY{chunk.year_from}-{chunk.year_to}): the "
            f"engine applies {wtd * 100:.4f}% but the UI shows "
            f"{chunk.optimized_yield() * 100:.4f}%. §2.1 requires these to be "
            "the same metric."
        )
        checked += 1

    if checked == 0:
        pytest.skip(f"{label}: no chunk had complete Avg_CAGR_* coverage")


def test_conservative_is_never_above_historical(scenario):
    """
    Post-§2.1, `worst_exp_ret` is min(Avg_CAGR_*) less an STT charge, so the
    Conservative projection can only be lower or equal.

    This is the guard that was missing when the inversion went unnoticed:
    before §2.1, Conservative was HIGHER than Historical for every fund in the
    dataset, so the setting labelled as a stress test produced the more
    optimistic plan.
    """
    label, state = scenario
    hist = _bare_engine()
    cons = _bare_engine(conservative_mode=True)

    seen = 0
    for chunk in state.allocation_chunks:
        for f in chunk.funds:
            if _avg_min(f) is None or f.worst_exp_ret is None:
                continue
            h = hist._fund_annual_return(f, chunk.year_from)
            c = cons._fund_annual_return(f, chunk.year_from)
            assert c <= h + 1e-12, (
                f"{label}/{f.name}: conservative {c * 100:.4f}% exceeds "
                f"historical {h * 100:.4f}%"
            )
            seen += 1

    if seen == 0:
        pytest.skip(f"{label}: no fund carries both metrics")


def test_metric_actually_moved_off_cagr_5(scenario):
    """
    A no-op patch would leave every other test in this file green.

    At least one fund in the corpus must now project at something other than
    its point-in-time 5Y CAGR, or §2.1 was not applied.
    """
    label, state = scenario
    eng = _bare_engine()

    for chunk in state.allocation_chunks:
        for f in chunk.funds:
            if f.cagr_5 is None or _avg_min(f) is None:
                continue
            if abs(eng._fund_annual_return(f, 1) - f.cagr_5 / 100.0) > 1e-9:
                return
    pytest.fail(
        f"{label}: every fund still projects at its 5Y CAGR. Either the "
        "engine is unpatched or the fixture has no Avg_CAGR_* data."
    )


# ── documentation guards ─────────────────────────────────────────────────────

def test_worst_exp_ret_docstring_describes_the_actual_computation(project_root):
    """
    get_funds_data.py's header claimed `min(1Y, 3Y, 5Y, 10Y CAGR)`. The code
    uses the Avg_CAGR_* columns and excludes 1Y. That single wrong line is why
    "Conservative mode" was taken for a worst-case stress test.
    """
    src = (PACKAGE_DIR / "funddata" / "get_funds_data.py").read_text(encoding="utf-8")
    header = src.split('"""')[1] if src.count('"""') >= 2 else ""

    stale = "min(1Y, 3Y, 5Y, 10Y CAGR) adjusted" in src
    assert not stale, (
        "get_funds_data.py still documents Worst_Exp_Ret_% as "
        "min(1Y, 3Y, 5Y, 10Y CAGR). The computation uses Avg_CAGR_3Y/5Y/7Y/10Y."
    )
    corrected = "Avg_CAGR_3Y" in header
    assert corrected, (
        "the corrected Worst_Exp_Ret_% description is not in the module header"
    )


def test_mode_toggle_tooltip_no_longer_claims_5y_cagr(project_root):
    """The scenario-tab tooltip is the only place a user is told what the
    modes mean. §2.1 made its first sentence false."""
    src = (PACKAGE_DIR / "gui" / "main.py").read_text(encoding="utf-8")

    claims_cagr_5 = "per-fund returns use 5Y CAGR" in src
    assert not claims_cagr_5, (
        "main.py's mode-toggle tooltip still tells the user Historical mode "
        "uses the 5Y CAGR."
    )
    claims_stress_test = "stress-tested lower-bound for corpus survival" in src
    assert not claims_stress_test, (
        "main.py still describes Conservative mode as a stress-tested "
        "lower bound. Post-§2.1 it differs from Historical only by the 0.40% "
        "STT charge."
    )
