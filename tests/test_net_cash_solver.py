# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
Solving for cash in hand.

Phase B changes annual_requirements from "withdraw this much" to "end up
with this much in hand". That is a fixed point -- withdrawing realises a
gain, the gain attracts tax, the tax reduces the cash, and the shortfall has
to be withdrawn -- so it is solved rather than computed.

The property worth testing hardest is the one that is NOT safe by
assumption. Bisection needs cash in hand to rise with the gross withdrawal:

    d(net)/d(gross) = 1 - marginal_rate x gain_fraction

Under an ordinary slab that is comfortably positive. Inside the s.87A
marginal relief band the marginal rate is 1.04, so the derivative goes
NEGATIVE once the gain fraction passes 96.2%, and more withdrawal yields
less cash. The live project peaks near 82% by FY25, but a longer plan at
higher returns can cross it, and a solver that assumed monotonicity would
return a wrong answer rather than fail.
"""
from __future__ import annotations

import pytest

from models import TaxChunk, TaxSlab
from net_cash_solver import constant_fraction_net, solve_gross_for_net

SLABS = [TaxSlab(0, 4, 0.0), TaxSlab(4, 8, 0.05), TaxSlab(8, 12, 0.10),
         TaxSlab(12, 16, 0.15), TaxSlab(16, 20, 0.20), TaxSlab(20, 24, 0.25),
         TaxSlab(24, 1e9, 0.30)]


def _tax(limit=12.0, entity="individual"):
    from engine import compute_slab_tax
    chunk = TaxChunk(1, 30, limit,
                     [TaxSlab(s.lower, s.upper, s.rate) for s in SLABS])
    return lambda income: compute_slab_tax(income, chunk, entity)


def _flat(rate):
    return lambda income: max(0.0, income) * rate


def _kinks(base_income, gain_fraction, limit=12.0):
    """Gross amounts at which the tax slope changes.

    In the engine these come from tax_allocator.EntityYear.breakpoints();
    here they are built directly so the test does not depend on that module.
    """
    from tax_allocator import EntityYear
    chunk = TaxChunk(1, 30, limit,
                     [TaxSlab(s.lower, s.upper, s.rate) for s in SLABS])
    gains = EntityYear("individual", base_income, chunk, None, None).breakpoints()
    return [g / gain_fraction for g in gains if gain_fraction > 0]


# ── the basic solve ──────────────────────────────────────────────────────────

def test_no_tax_means_the_gap_is_the_answer():
    net = constant_fraction_net(other_income=10.0, gain_fraction=0.5,
                                tax_of_income=_flat(0.0))
    r = solve_gross_for_net(25.0, net, max_gross=100.0)
    assert r.gross == pytest.approx(15.0, abs=1e-5)
    assert r.met and r.monotone


def test_tax_makes_the_gross_exceed_the_gap():
    """Withdrawing to cover the gap creates tax, which widens the gap."""
    net = constant_fraction_net(10.0, 0.5, _flat(0.20))
    r = solve_gross_for_net(25.0, net, 100.0)
    # g + 10 - 0.2*(0.5g) = 25  ->  0.9g = 15
    assert r.gross == pytest.approx(15.0 / 0.9, abs=1e-4)
    assert r.net == pytest.approx(25.0, abs=1e-4)


def test_other_income_alone_can_cover_the_target():
    """Nothing should be withdrawn if nothing needs to be."""
    net = constant_fraction_net(30.0, 0.5, _flat(0.2))
    r = solve_gross_for_net(25.0, net, 100.0)
    assert r.gross == pytest.approx(0.0)
    assert r.met


def test_the_transfer_into_the_huf_raises_the_gross_required():
    """It is invested, not spent, so it does not count toward the target."""
    plain = constant_fraction_net(10.0, 0.5, _flat(0.0))
    with_tx = constant_fraction_net(10.0, 0.5, _flat(0.0), transfer=3.0)
    a = solve_gross_for_net(25.0, plain, 100.0)
    b = solve_gross_for_net(25.0, with_tx, 100.0)
    assert b.gross == pytest.approx(a.gross + 3.0, abs=1e-5)


# ── running out ──────────────────────────────────────────────────────────────

def test_a_shortfall_is_reported_not_hidden():
    net = constant_fraction_net(5.0, 0.5, _flat(0.2))
    r = solve_gross_for_net(60.0, net, max_gross=20.0)
    assert not r.met
    assert r.exhausted
    assert r.gross == pytest.approx(20.0)
    assert r.shortfall > 0
    assert r.shortfall == pytest.approx(60.0 - r.net, abs=1e-9)


def test_an_empty_corpus_still_reports_other_income():
    net = constant_fraction_net(9.0, 0.5, _flat(0.2))
    r = solve_gross_for_net(25.0, net, max_gross=0.0)
    assert r.gross == 0.0
    assert r.net == pytest.approx(9.0)
    assert r.shortfall == pytest.approx(16.0)


# ── the answer must be the SMALLEST gross that works ─────────────────────────

@pytest.mark.parametrize("target", [8, 12, 16, 20, 25, 30, 40])
def test_the_solution_is_minimal(target):
    """Anything larger wastes corpus that later years need."""
    net = constant_fraction_net(9.7, 0.6, _tax(), base_income=3.362)
    r = solve_gross_for_net(target, net, 200.0)
    if not r.met:
        pytest.skip("target unreachable")
    assert net(r.gross) >= target - 1e-6
    if r.gross <= 1e-9:
        return          # other income already covers it; nothing is minimal-er
    assert net(r.gross - 1e-3) < target + 1e-6


# ── the s.87A relief band ────────────────────────────────────────────────────

def test_monotonicity_holds_at_realistic_gain_fractions():
    """The live project peaks near 82%."""
    for frac in (0.2, 0.5, 0.82, 0.95):
        net = constant_fraction_net(9.7, frac, _tax(), base_income=3.362)
        r = solve_gross_for_net(30.0, net, 200.0)
        assert r.monotone, f"gain fraction {frac} reported non-monotone"


def test_a_gain_fraction_above_96_percent_breaks_monotonicity():
    """1 / 1.04 = 96.2%. Past that, more withdrawal yields less cash.

    The solver must NOTICE rather than return whatever bisection lands on.
    """
    net = constant_fraction_net(9.7, 1.0, _tax(), base_income=3.362)
    kinks = _kinks(base_income=3.362, gain_fraction=1.0)

    # The inversion is LOCAL and shallow: the derivative in the band is
    # 1 - 1.04 x 1.0 = -0.04, so cash in hand slips by four paise per rupee
    # withdrawn. It never falls below its level before the band -- which is
    # exactly why a coarse grid misses it and why the check needs the kinks.
    grid = [8.5 + i * 0.02 for i in range(60)]
    falls = [(a, b) for a, b in zip(grid, grid[1:]) if net(b) < net(a) - 1e-12]
    assert falls, (
        "cash in hand should fall somewhere inside the relief band at a 100% "
        "gain fraction; if it does not, the premise of this test has changed")
    a, b = falls[0]
    assert net(b) - net(a) < 0, f"expected a fall between {a:.3f} and {b:.3f}"
    r = solve_gross_for_net(20.0, net, 200.0, breakpoints=kinks)
    assert not r.monotone
    assert net(r.gross) >= 20.0 - 1e-6


def test_the_non_monotone_answer_is_still_minimal():
    net = constant_fraction_net(9.7, 1.0, _tax(), base_income=3.362)
    kinks = _kinks(base_income=3.362, gain_fraction=1.0)
    r = solve_gross_for_net(20.0, net, 200.0, breakpoints=kinks)
    assert not r.monotone
    for i in range(1, 40):
        g = r.gross - i * 0.01
        if g < 0:
            break
        assert net(g) < 20.0 - 1e-9, (
            f"a smaller gross {g:.4f} also meets the target; "
            f"solver returned {r.gross:.4f}")


# ── sanity ───────────────────────────────────────────────────────────────────

def test_a_higher_target_never_needs_less_gross():
    net = constant_fraction_net(9.7, 0.6, _tax(), base_income=3.362)
    prev = -1.0
    for target in range(10, 60, 2):
        g = solve_gross_for_net(float(target), net, 300.0).gross
        assert g >= prev - 1e-6
        prev = g


def test_the_evaluation_count_stays_bounded():
    calls = []
    base = constant_fraction_net(9.7, 0.6, _tax(), base_income=3.362)

    def counted(g):
        calls.append(g)
        return base(g)

    solve_gross_for_net(30.0, counted, 200.0)
    assert len(calls) < 150, f"{len(calls)} evaluations is too many per year"
