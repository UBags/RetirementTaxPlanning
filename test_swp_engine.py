"""
test_swp_engine.py — Robustness & edge-case test suite for SWP Financial Planner.

Covers:
  1. Engine edge cases (corpus depletion, zero-allocation funds, all-equity,
     all-debt, single-fund, windfall in final year, HUF with no corpus)
  2. Glide path edge cases (single chunk, flat path, boundary-year interpolation,
     zero spread, maximum spread)
  3. Optimizer edge cases (empty universe, single fund universe, all-same-type
     universe, incompatible constraints that force relaxation)
  4. Tax correctness (slab thresholds, LTCG exemption boundary, cess rounding,
     87A marginal relief, HUF nil-slab absorption)
  5. FIFO/HIFO lot tracking (correct gain split across lots, STCG vs LTCG
     distinction, exit load calculation)
  6. Rebalancing self-funding (Bug #1 fix: rebal tax must NOT reduce net_cash)
  7. No-trade threshold (drift below tolerance must skip rebalance)

Run with:
    python -m pytest test_swp_engine.py -v
  or standalone:
    python test_swp_engine.py
"""

import copy
import math

# ── allow running from repo root without install ─────────────────────────────
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))

# ─── Test helpers ─────────────────────────────────────────────────────────────

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
_results = []

_COLLECT_MODE = False   # True only for the standalone runner (see run_all)

def _check(name: str, condition: bool, detail: str = ""):
    condition = bool(condition)
    sym = PASS if condition else FAIL
    msg = f"  {sym}  {name}"
    if detail:
        msg += f"  [{detail}]"
    print(msg)
    _results.append((name, condition))
    if not condition and not _COLLECT_MODE:
        raise AssertionError(f"{name}: {detail}" if detail else name)

def _approx(a: float, b: float, tol: float = 1e-4) -> bool:
    return abs(a - b) <= tol

def _section(title: str):
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


# ─── Minimal model factories ──────────────────────────────────────────────────

def _make_fund(name="Fund A", fund_type="debt", allocation=100.0,
               cagr5=7.0, std=0.02, max_dd=0.01):
    from models import FundEntry
    f = FundEntry(
        name=name, fund_type=fund_type, allocation=allocation,
        std_dev=std, sharpe=2.0, sortino=2.5, calmar=1.0,
        alpha=0.5, treynor=0.8, max_dd=max_dd, beta=0.3,
        combined_ratio=1.5,
    )
    f.cagr_1 = cagr5; f.cagr_3 = cagr5; f.cagr_5 = cagr5; f.cagr_10 = cagr5
    return f


def _make_state(funds=None, total_alloc=100.0, req_annual=8.0,
                inv_date=None):
    """Create a minimal AppState with sensible defaults.

    Built on models.default_state() so the six tax-chunk lists stay in sync
    with the application rather than being duplicated here.

    ``total_alloc`` is accepted for signature compatibility but unused --
    each FundEntry carries its own ``allocation``.
    """
    from models import AllocationChunk, FDRateChunk, OtherIncome, ReturnChunk, default_state

    state = default_state()
    state.investment_date = inv_date or date(2024, 4, 1)

    if funds is None:
        funds = [_make_fund()]

    state.funds = list(funds)

    chunk = AllocationChunk(year_from=1, year_to=30, funds=list(funds))
    chunk.min_return   = 0.065
    chunk.max_std_dev  = 0.04
    chunk.max_dd       = 0.03
    chunk.max_per_fund = 0.40
    chunk.min_per_fund = 0.01
    state.allocation_chunks = [chunk]

    # annual_requirements is a Dict[int, float], not a list of chunks
    state.annual_requirements = {y: req_annual for y in range(1, 31)}

    state.return_chunks = [ReturnChunk(year_from=1, year_to=30,
                                       annual_return=0.07)]

    # Left empty: the engine derives debt/equity/other split from the actual
    # fund allocations, which is what these tests vary.
    state.split_chunks = []

    state.personal_income = OtherIncome()
    state.huf_income      = OtherIncome()
    state.windfalls       = []
    state.huf_withdrawal_chunks = []
    state.huf_annual_requirements = {}

    state.fd_rate_chunks = [FDRateChunk(year_from=1, year_to=30, fd_rate=0.07)]
    state.fd_rate = 0.07
    state.allocation_mode = "singular"
    state.rebalance_spread_years = 4
    state.glide_path = None

    return state

# ─────────────────────────────────────────────────────────────────────────────
# Section 1 — Engine edge cases
# ─────────────────────────────────────────────────────────────────────────────

def _run_engine(state, glide_path=None):
    from engine import Engine
    eng = Engine(state, glide_path=glide_path)
    p_rows, yearly, h_rows, _ = eng.run()
    return p_rows, yearly, h_rows


def test_engine_normal_run():
    _section("Engine — Normal Run")
    state = _make_state(total_alloc=200.0,
                        funds=[_make_fund("Debt A", "debt", 160.0),
                               _make_fund("Eq A", "equity", 40.0)])
    p_rows, yearly, _ = _run_engine(state)
    _check("Engine produces 30 yearly summaries", len(yearly) == 30)
    _check("Engine produces monthly rows", len(p_rows) > 0)
    _check("Corpus positive in year 1", yearly[0].corpus_debt_personal > 0)
    _check("Net cash positive in year 1", yearly[0].net_cash_personal > 0)


def test_engine_single_fund():
    _section("Engine — Single Fund (all-debt)")
    state = _make_state(funds=[_make_fund("Debt Only", "debt", 200.0)])
    _, yearly, _ = _run_engine(state)
    _check("Runs to 30 years", len(yearly) == 30)
    _check("No equity corpus", all(y.corpus_equity_personal == 0 for y in yearly))


def test_engine_all_equity():
    _section("Engine — All Equity Corpus")
    state = _make_state(funds=[_make_fund("Eq Only", "equity", 200.0, cagr5=12.0)])
    _, yearly, _ = _run_engine(state)
    _check("Runs without crash", len(yearly) == 30)
    _check("Equity corpus positive year 1", yearly[0].corpus_equity_personal > 0)


def test_engine_corpus_depletion():
    _section("Engine — Corpus Depletion (high withdrawal)")
    # Very high withdrawal relative to corpus — corpus should exhaust but not crash
    state = _make_state(funds=[_make_fund("Debt A", "debt", 50.0)], req_annual=20.0)
    state.annual_requirements = {y: 20.0 for y in range(1, 31)}
    try:
        _, yearly, _ = _run_engine(state)
        _check("Engine doesn't crash on depletion", True)
        # At some point corpus should be near zero
        final_corpus = (yearly[-1].corpus_debt_personal +
                        yearly[-1].corpus_equity_personal +
                        yearly[-1].corpus_other_personal)
        _check("Corpus depletes or near-zero", final_corpus < 5.0,
               f"final={final_corpus:.2f}L")
    except Exception as e:
        _check("Engine doesn't crash on depletion", False, str(e))


def test_engine_zero_allocation_funds():
    _section("Engine — Zero-Allocation Funds Ignored")
    funds = [
        _make_fund("Active Debt", "debt", 100.0),
        _make_fund("Inactive Fund", "debt", 0.0),
        _make_fund("Also Inactive", "equity", 0.0),
    ]
    state = _make_state(funds=funds)
    _, yearly, _ = _run_engine(state)
    _check("Runs normally with zero-alloc funds", len(yearly) == 30)
    _check("Only active fund contributes to corpus",
           all(y.corpus_equity_personal == 0 for y in yearly))


def test_rebalancing_tax_reduces_cash_and_the_solver_covers_it():
    _section("Engine — rebalancing tax is paid out of cash, and covered")
    """
    Phase 3 reversed the old "self-funded" rule.

    That rule was itself a fix for a real bug: net cash going negative in a
    rebalance year. But it treated the symptom. Rebalancing tax is real money
    leaving the plan, so it MUST reduce cash in hand; the cause of the negative
    was the absent compensating redemption, not the deduction.

    So there are two properties now, and this pins both:

      1. Run the engine directly, with no solver, and the rebalancing tax bites
         — net cash falls below the requirement. That is correct: nothing is
         redeeming more to cover it.
      2. Run the same plan through project_plan, and the solver redeems enough
         extra that the requirement is still met to within PLAN_TOL.

    The second is the one that matters. It is the design intent the old test
    could not express, because under the old identity the tax never bit.
    """
    from glide_path import build_glide_path
    from models import AllocationChunk

    state = _make_state(funds=[
        _make_fund("Debt Big", "debt", 160.0),
        _make_fund("Eq Big",   "equity", 40.0),
    ], req_annual=8.0)

    # Two chunks with very different allocations, to force a large rebalance.
    c1 = AllocationChunk(year_from=1, year_to=10,
                         funds=[_make_fund("Eq Heavy", "equity", 160.0, cagr5=12.0),
                                _make_fund("Debt Light", "debt", 40.0)])
    c1.target_weights = {"Eq Heavy": 0.80, "Debt Light": 0.20}
    c1.min_return = 0.065; c1.max_std_dev = 0.10; c1.max_dd = 0.12
    c1.max_per_fund = 1.0; c1.min_per_fund = 0.01

    c2 = AllocationChunk(year_from=11, year_to=30,
                         funds=[_make_fund("Eq Heavy", "equity", 40.0, cagr5=12.0),
                                _make_fund("Debt Light", "debt", 160.0)])
    c2.target_weights = {"Eq Heavy": 0.20, "Debt Light": 0.80}
    c2.min_return = 0.065; c2.max_std_dev = 0.04; c2.max_dd = 0.03
    c2.max_per_fund = 1.0; c2.min_per_fund = 0.01

    state.allocation_chunks = [c1, c2]
    state.funds = c2.funds
    gp = build_glide_path([c1, c2], spread_years=0)
    state.glide_path = gp

    # ── 1. no solver: the tax bites ──────────────────────────────────────
    _, yearly, _ = _run_engine(state, glide_path=gp)
    y11 = yearly[10]                      # FY11, the rebalance year
    _check("Rebalancing tax is tracked",
           y11.rebalance_tax_paid > 0,
           f"rebal_tax={y11.rebalance_tax_paid:.4f}L")
    _check("Without a solver, rebalancing tax reduces cash in hand",
           y11.net_cash_personal < 8.0,
           f"net_cash={y11.net_cash_personal:.2f}L vs req=8.00L")

    # ── 2. through the solver: the requirement is still met ──────────────
    from net_income_plan import PLAN_TOL, project_plan
    _, y2, _, _, sol = project_plan(state, glide_path=gp)
    got = y2[10].net_cash_total
    want = sol.targets[11]
    _check("The solver redeems enough to cover it",
           abs(got - want) <= PLAN_TOL,
           f"FY11 delivered {got:.4f}L vs required {want:.4f}L")


def test_engine_windfall():
    _section("Engine — Windfall in Final Year")
    from models import WindfallEntry
    state = _make_state(funds=[_make_fund("Debt", "debt", 100.0)])
    state.windfalls = [WindfallEntry(year=30, amount=50.0, target="personal")]
    p_rows, yearly, _ = _run_engine(state)
    _check("Engine handles late windfall", len(yearly) == 30)
    _check("Year 30 windfall registered",
           any(r.windfall_personal > 0 for r in p_rows or []))


def test_engine_no_trade_threshold():
    _section("Engine — No-Trade Threshold (Garleanu & Pedersen)")
    from engine import FIFOBucket, Lot, _rebalance_portfolio

    # Build a tiny portfolio that is well within 3% drift
    fund_buckets = {
        "Debt A": FIFOBucket(),
        "Eq A":   FIFOBucket(),
    }
    fund_buckets["Debt A"].invest(80.0, 1.0, 0)
    fund_buckets["Eq A"].invest(20.0, 1.0, 0)
    fund_navs  = {"Debt A": 1.05, "Eq A": 1.02}
    fund_types = {"Debt A": "debt", "Eq A": "equity"}

    # Current weights ≈ {Debt: 80.5%, Eq: 19.5%}  Target: {Debt: 80%, Eq: 20%}
    # Total drift ≈ 1.0% — below 3% threshold
    target = {"Debt A": 0.80, "Eq A": 0.20}
    prior  = {"Debt A": 0.80, "Eq A": 0.20}

    from models import AppState, default_state
    state = _make_state()

    cost, gains, _ = _rebalance_portfolio(
        target_weights=target, prior_weights=prior,
        fund_buckets=fund_buckets, fund_navs=fund_navs,
        fund_types=fund_types, state=state, fy=5, month_idx=48,
        drift_tolerance=0.03,
    )
    _check("No-trade: zero tax when drift < 3%",
           cost.taxes_paid == 0.0, f"taxes={cost.taxes_paid}")
    _check("No-trade: zero exit loads when drift < 3%",
           cost.exit_loads_paid == 0.0)
    # Verify buckets unchanged
    debt_val = fund_buckets["Debt A"].current_value(fund_navs["Debt A"])
    _check("No-trade: Debt A bucket untouched",
           _approx(debt_val, 80.0 * 1.05), f"val={debt_val:.4f}")


# ─────────────────────────────────────────────────────────────────────────────
# Section 2 — GlidePath edge cases
# ─────────────────────────────────────────────────────────────────────────────

def test_glide_path_single_chunk():
    _section("GlidePath — Single Chunk (flat)")
    from glide_path import build_flat_glide_path
    weights = {"Debt A": 0.7, "Eq A": 0.3}
    gp = build_flat_glide_path(weights)
    _check("Single chunk: is_flat() == True", gp.is_flat())
    _check("Single chunk: same weights all years",
           gp.weights_for_year(1) == gp.weights_for_year(30))
    _check("Single chunk: no transition years", len(gp.transition_years()) == 0)


def test_glide_path_year1_and_year30_are_clean():
    _section("GlidePath — Year 1 never in transition, Year 30 fully anchored")
    from glide_path import build_glide_path
    from models import AllocationChunk

    c1 = AllocationChunk(year_from=1, year_to=5, funds=[])
    c1.target_weights = {"Eq": 1.0}
    c2 = AllocationChunk(year_from=6, year_to=30, funds=[])
    c2.target_weights = {"Debt": 1.0}

    gp = build_glide_path([c1, c2], spread_years=10)  # very wide spread
    w1  = gp.weights_for_year(1)
    w30 = gp.weights_for_year(30)
    _check("Year 1: 100% Eq (never in transition)",
           _approx(w1.get("Eq", 0.0), 1.0, 0.01), f"{w1}")
    _check("Year 30: 100% Debt (no ramp-out)",
           _approx(w30.get("Debt", 0.0), 1.0, 0.01), f"{w30}")


def test_glide_path_zero_spread():
    _section("GlidePath — Zero Spread (hard cut)")
    from glide_path import build_glide_path
    from models import AllocationChunk

    c1 = AllocationChunk(year_from=1, year_to=15, funds=[])
    c1.target_weights = {"Eq": 1.0}
    c2 = AllocationChunk(year_from=16, year_to=30, funds=[])
    c2.target_weights = {"Debt": 1.0}

    gp = build_glide_path([c1, c2], spread_years=0)
    w15 = gp.weights_for_year(15)
    w16 = gp.weights_for_year(16)
    _check("Zero spread: Yr15 = 100% Eq",
           _approx(w15.get("Eq", 0.0), 1.0, 0.01))
    _check("Zero spread: Yr16 = 100% Debt",
           _approx(w16.get("Debt", 0.0), 1.0, 0.01))


# ─────────────────────────────────────────────────────────────────────────────
# Section 3 — Optimizer edge cases
# ─────────────────────────────────────────────────────────────────────────────

def test_optimizer_empty_universe():
    _section("Optimizer — Empty Universe (graceful failure)")
    from allocate_funds import run_aim_pass
    from models import AllocationChunk
    c = AllocationChunk(year_from=1, year_to=30, funds=[])
    c.min_return = 0.065; c.max_std_dev = 0.04; c.max_dd = 0.03
    c.max_per_fund = 0.40; c.min_per_fund = 0.01
    try:
        run_aim_pass([c], [], progress_cb=None)
        _check("Empty universe: no crash", True)
        _check("Empty universe: target_weights empty", c.target_weights == {})
    except Exception as e:
        _check("Empty universe: no crash", False, str(e))


def test_optimizer_single_fund_universe():
    _section("Optimizer — Single Fund Universe")
    from allocate_funds import run_aim_pass
    from models import AllocationChunk
    c = AllocationChunk(year_from=1, year_to=30,
                        funds=[_make_fund("Single", "debt", 100.0)])
    c.min_return = 0.065; c.max_std_dev = 0.04; c.max_dd = 0.03
    c.max_per_fund = 1.0; c.min_per_fund = 0.01
    try:
        run_aim_pass([c], [_make_fund("Single", "debt", 100.0)])
        _check("Single fund: no crash", True)
        total_w = sum(c.target_weights.values())
        _check("Single fund: weights sum to 1",
               _approx(total_w, 1.0, 0.01), f"sum={total_w:.4f}")
    except Exception as e:
        _check("Single fund: no crash", False, str(e))


def test_optimizer_type_ratios_locked():
    _section("Optimizer — Track Pass: type ratios enforced as hard constraints")
    from allocate_funds import run_aim_pass, run_track_pass
    from models import AllocationChunk

    # Three debt funds and three equity funds
    funds = [
        _make_fund("D1", "debt",   80.0, cagr5=7.0, std=0.02),
        _make_fund("D2", "debt",   80.0, cagr5=6.8, std=0.018),
        _make_fund("D3", "debt",   80.0, cagr5=7.2, std=0.022),
        _make_fund("E1", "equity", 80.0, cagr5=12.0, std=0.15, max_dd=0.20),
        _make_fund("E2", "equity", 80.0, cagr5=11.5, std=0.14, max_dd=0.18),
        _make_fund("E3", "equity", 80.0, cagr5=12.5, std=0.16, max_dd=0.22),
    ]

    c1 = AllocationChunk(year_from=1, year_to=15, funds=funds)
    c1.min_return = 0.065; c1.max_std_dev = 0.12; c1.max_dd = 0.18
    c1.max_per_fund = 0.5; c1.min_per_fund = 0.01

    c2 = AllocationChunk(year_from=16, year_to=30, funds=funds)
    c2.min_return = 0.065; c2.max_std_dev = 0.04; c2.max_dd = 0.04
    c2.max_per_fund = 0.5; c2.min_per_fund = 0.01

    run_aim_pass([c1, c2], funds)

    t1_equity = c1._type_ratios.get("equity", 0.0)
    t2_equity = c2._type_ratios.get("equity", 0.0)
    _check("Aim pass: Chunk 1 has more equity than Chunk 2 (glide path shape)",
           t1_equity >= t2_equity,
           f"c1_eq={t1_equity:.2f} c2_eq={t2_equity:.2f}")

    run_track_pass([c1, c2], funds)

    # After track pass, verify type ratios were preserved within ±1.5%
    w1 = c1.target_weights
    eq1_after = sum(v for k, v in w1.items() if any(f.name == k and f.fund_type == "equity" for f in funds))
    _check("Track pass: Chunk 1 equity ratio preserved",
           abs(eq1_after - t1_equity) < 0.015,
           f"aim={t1_equity:.4f} track={eq1_after:.4f}")

    w2 = c2.target_weights
    eq2_after = sum(v for k, v in w2.items() if any(f.name == k and f.fund_type == "equity" for f in funds))
    _check("Track pass: Chunk 2 equity ratio preserved",
           abs(eq2_after - t2_equity) < 0.015,
           f"aim={t2_equity:.4f} track={eq2_after:.4f}")

    # Verify weights sum to 1
    _check("Chunk 1 weights sum to 1",
           _approx(sum(w1.values()), 1.0, 0.01))
    _check("Chunk 2 weights sum to 1",
           _approx(sum(w2.values()), 1.0, 0.01))


# ─────────────────────────────────────────────────────────────────────────────
# Section 4 — Tax correctness
# ─────────────────────────────────────────────────────────────────────────────

def test_tax_87a_marginal_relief():
    _section("Tax — 87A Marginal Relief (individual)")
    from engine import compute_slab_tax
    from models import TaxChunk, TaxSlab

    # Simple slabs: 0% up to 3L, 5% 3-7L, 10% 7-10L, 30% above
    chunk = TaxChunk(year_from=1, year_to=5, exempt_limit=7.0,
                     slabs=[
                         TaxSlab(0.0, 3.0, 0.0),
                         TaxSlab(3.0, 7.0, 0.05),
                         TaxSlab(7.0, 10.0, 0.10),
                         TaxSlab(10.0, 1e9, 0.30),
                     ])
    # Below exempt_limit: zero tax
    tax_low = compute_slab_tax(6.0, chunk, "individual")
    _check("87A: income below limit → zero tax",
           tax_low == 0.0, f"tax={tax_low:.4f}")

    # Just above exempt_limit: tax should be min(computed_tax, excess_income)
    tax_above = compute_slab_tax(7.5, chunk, "individual")
    # Slab tax = 0 + (7-3)*0.05 + (7.5-7)*0.10 = 0.20 + 0.05 = 0.25L
    # Excess = 7.5 - 7.0 = 0.5L → marginal relief → tax = min(0.25, 0.5) = 0.25
    _check("87A marginal relief applied",
           tax_above > 0 and tax_above <= 0.5 * 1.04,  # with cess
           f"tax={tax_above:.4f}")


def test_tax_equity_ltcg_exemption():
    _section("Tax — Equity LTCG ₹1.25L exemption")
    from engine import compute_ltcg_individual
    from models import EquityTaxChunk

    chunk = EquityTaxChunk(year_from=1, year_to=5, tax_rate=0.125, exempt_limit=1.25)

    # Below exemption: zero
    _check("LTCG below 1.25L: zero tax",
           compute_ltcg_individual(1.0, chunk) == 0.0)

    # At exemption: zero
    _check("LTCG at 1.25L: zero tax",
           compute_ltcg_individual(1.25, chunk) == 0.0)

    # Above exemption: 12.5% + 4% cess on excess
    tax = compute_ltcg_individual(3.25, chunk)  # gains = 3.25L, excess = 2.0L
    expected = 2.0 * 0.125 * 1.04
    _check("LTCG above exemption: correct tax",
           _approx(tax, expected, 0.001), f"tax={tax:.4f} expected={expected:.4f}")


def test_tax_other_ltcg_no_exemption():
    _section("Tax — Other LTCG (Gold/International): no exemption")
    from engine import compute_ltcg_other_individual
    from models import OtherTaxChunk

    chunk = OtherTaxChunk(year_from=1, year_to=5, tax_rate=0.125)

    # Even small gains: taxed (no exemption)
    tax_small = compute_ltcg_other_individual(0.5, chunk)
    expected  = 0.5 * 0.125 * 1.04
    _check("Other LTCG: no exemption, taxed from ₹1",
           _approx(tax_small, expected, 0.001),
           f"tax={tax_small:.4f}")


# ─────────────────────────────────────────────────────────────────────────────
# Section 5 — FIFO/HIFO lot tracking
# ─────────────────────────────────────────────────────────────────────────────

def test_fifo_bucket_basic():
    _section("FIFO Bucket — Basic invest/redeem")
    from engine import FIFOBucket

    bk = FIFOBucket()
    bk.invest(100.0, 1.0, 0)    # 100 units @ NAV 1.0
    bk.invest(100.0, 2.0, 12)   # 50 units @ NAV 2.0

    _check("Total units after two invests",
           _approx(bk.total_units, 150.0), f"units={bk.total_units}")

    # Redeem 60 units worth at NAV 3.0 (sells oldest lots first = FIFO)
    # FIFO: 60 units from lot1 (all 100 available, take 60)
    # Value redeemed = 60 * 3.0 = 180.  Principal = 60 * 1.0 = 60.  Gain = 60 * 2.0 = 120.
    pr, gn = bk.redeem(180.0, 3.0)
    _check("FIFO: correct principal", _approx(pr, 60.0), f"pr={pr:.2f}")
    _check("FIFO: correct gain",      _approx(gn, 120.0), f"gn={gn:.2f}")
    _check("FIFO: oldest lot consumed first",
           _approx(bk.lots[0].purchase_nav, 1.0), f"first_lot_nav={bk.lots[0].purchase_nav}")


def test_hifo_vs_fifo_gain_difference():
    _section("HIFO — Lower gains than FIFO (tax alpha)")
    from engine import FIFOBucket, Lot, _rebalance_portfolio

    # Two lots: Lot A (old, high gain), Lot B (recent, low gain)
    # FIFO sells Lot A first (high gain = more tax)
    # HIFO sells Lot B first (low gain = less tax)
    def _build_buckets():
        bk = FIFOBucket()
        # Lot A: bought at NAV 1.0, month 0 (old = LTCG, big gain at current nav=3.0)
        bk.lots.append(Lot(units=50.0, purchase_nav=1.0, purchase_month=0))
        # Lot B: bought at NAV 2.5, month 50 (still old, small gain at nav=3.0)
        bk.lots.append(Lot(units=50.0, purchase_nav=2.5, purchase_month=50))
        return bk

    fund_navs  = {"FundX": 3.0}
    fund_types = {"FundX": "equity"}

    state = _make_state()

    # FIFO (use_hifo=False)
    bk_fifo = {"FundX": _build_buckets(), "FundY": FIFOBucket()}
    bk_fifo["FundY"].invest(150.0, 1.0, 0)
    fifo_cost, fifo_gains, _ = _rebalance_portfolio(
        target_weights={"FundX": 0.3, "FundY": 0.7},
        prior_weights= {"FundX": 0.5, "FundY": 0.5},
        fund_buckets=bk_fifo, fund_navs={"FundX": 3.0, "FundY": 1.5},
        fund_types=fund_types | {"FundY": "debt"},
        state=state, fy=5, month_idx=60,
        drift_tolerance=0.0,   # force rebalance
        use_hifo=False,
    )

    # HIFO (use_hifo=True)
    bk_hifo = {"FundX": _build_buckets(), "FundY": FIFOBucket()}
    bk_hifo["FundY"].invest(150.0, 1.0, 0)
    hifo_cost, hifo_gains, _ = _rebalance_portfolio(
        target_weights={"FundX": 0.3, "FundY": 0.7},
        prior_weights= {"FundX": 0.5, "FundY": 0.5},
        fund_buckets=bk_hifo, fund_navs={"FundX": 3.0, "FundY": 1.5},
        fund_types=fund_types | {"FundY": "debt"},
        state=state, fy=5, month_idx=60,
        drift_tolerance=0.0,
        use_hifo=True,
    )

    _check("HIFO produces same or lower tax than FIFO (tax alpha)",
           hifo_cost.taxes_paid <= fifo_cost.taxes_paid + 1e-4,
           f"HIFO={hifo_cost.taxes_paid:.4f}L  FIFO={fifo_cost.taxes_paid:.4f}L")
    _check("HIFO equity gains same or lower",
           hifo_gains.get('e', 0.0) <= fifo_gains.get('e', 0.0) + 1e-4,
           f"HIFO_gains={hifo_gains.get('e',0):.4f}  FIFO_gains={fifo_gains.get('e',0):.4f}")


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

ALL_TESTS = [
    test_engine_normal_run,
    test_engine_single_fund,
    test_engine_all_equity,
    test_engine_corpus_depletion,
    test_engine_zero_allocation_funds,
    test_rebalancing_tax_reduces_cash_and_the_solver_covers_it,
    test_engine_windfall,
    test_engine_no_trade_threshold,
    test_glide_path_single_chunk,
    test_glide_path_year1_and_year30_are_clean,
    test_glide_path_zero_spread,
    test_optimizer_empty_universe,
    test_optimizer_single_fund_universe,
    test_optimizer_type_ratios_locked,
    test_tax_87a_marginal_relief,
    test_tax_equity_ltcg_exemption,
    test_tax_other_ltcg_no_exemption,
    test_fifo_bucket_basic,
    test_hifo_vs_fifo_gain_difference,
]


def run_all():
    global _COLLECT_MODE
    _COLLECT_MODE = True      # collect all results instead of aborting
    print("\n" + "═"*60)
    print("  SWP Financial Planner — Robustness Test Suite")
    print("═"*60)

    for t in ALL_TESTS:
        try:
            t()
        except Exception as ex:
            _section(f"UNEXPECTED ERROR in {t.__name__}")
            import traceback
            traceback.print_exc()
            _results.append((t.__name__ + " [CRASHED]", False))

    passed = sum(1 for _, ok in _results if ok)
    total  = len(_results)
    failed = [n for n, ok in _results if not ok]

    print(f"\n{'═'*60}")
    print(f"  Results: {passed}/{total} passed")
    if failed:
        print("\n  FAILURES:")
        for n in failed:
            print(f"    {FAIL}  {n}")
    else:
        print(f"  {PASS}  All tests passed!")
    print("═"*60 + "\n")
    return len(failed) == 0


if __name__ == "__main__":
    # Try to import models — if it fails, print a helpful message
    try:
        import models
    except ImportError:
        print("ERROR: Could not import 'models'. Run from the project root directory.")
        print("       python test_swp_engine.py")
        sys.exit(1)

    ok = run_all()
    sys.exit(0 if ok else 1)