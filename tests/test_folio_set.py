# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""FolioSet: the folio-aware selection methods, and the gate that disables them.

WHY THIS FILE EXISTS
--------------------
Phase 4 landed a corpus representation, a selection API, a config gate and
three call-site conversions across six commits, every one of them claiming to
be inert. Each claim was verified -- and every verification lived in a
throwaway script that no longer exists. Nothing in the suite exercised a single
multi-folio path.

That is HANDOVER 9.14 in its purest form. The rule there is "shape the fixture
like the plan, not like a random walk", but the rule beneath it is that
verification which does not live in the suite proves nothing once the session
ends. Raising folio_cohort_threshold is meant to be a change whose entire diff
is snapshot movement; without these tests, the multi-folio code would get its
first real exercise at exactly the moment the numbers are legitimately moving,
which SESSION_LOG 3 identifies as when a defect hides best.

One of these is a regression test for a defect that already shipped and was
already fixed, and which nothing would currently catch if it came back --
see test_sell_ordered_never_comes_up_short.

FIXTURE SHAPE
-------------
Every fixture here is shaped like the plan: one large initial lot, small annual
additions, drawn down slowly. The 4b-i defect survived 3000 randomised
invest/redeem steps precisely because constant churn keeps the FIFO queue front
fresh, so no second folio ever opened and the multi-folio path never ran once.
Randomness is seeded per-test via random.Random(...) rather than the global
generator, so results are deterministic and xdist-safe.
"""
from __future__ import annotations

import random
import re

import pytest

from swp_planner.config.configuration import config
from swp_planner.core.engine import FIFOBucket, FolioSet, Lot, new_folio_set
from swp_planner.paths import PACKAGE_DIR

STCG = config.stcg_holding_months

# A literal, deliberately, not config.exit_load_fraction. That key now sits in
# UNUSED_KEYS because the engine charges no exit load anywhere on the live path
# -- sell_ordered and sell_to_tax_budget both take it as an argument and
# neither has a caller. Reading it here would quietly contradict that. These
# tests only need *an* exit load to pass through the arithmetic.
EXIT_LOAD = 0.01


# ── fixtures shaped like the plan ────────────────────────────────────────────

def _plan_lots(rng: random.Random, n_extra: int = 12) -> list:
    """One large initial lot, then small annual additions."""
    lots = [Lot(300.0, 1.00, 0)]
    for k in range(1, n_extra + 1):
        lots.append(Lot(rng.uniform(0.5, 10.0), 1.0 + 0.07 * k, k * 12))
    return lots


def _plan_folio_set(threshold: float, rng: random.Random) -> FolioSet:
    """A FolioSet built by investing the way the plan does."""
    fs = FolioSet(threshold=threshold, max_cohorts=5)
    fs.invest(300.0, 1.00, 0)
    for k in range(1, 26):
        fs.invest(rng.uniform(1.0, 9.0), 1.0 + 0.07 * k, k * 12)
    return fs


def _single_folio(lots) -> FolioSet:
    fs = FolioSet()
    fs.folios[fs.order[0]].lots = [
        Lot(x.units, x.purchase_nav, x.purchase_month) for x in lots]
    return fs


# ── the gate ─────────────────────────────────────────────────────────────────

def test_the_gate_opens_exactly_one_folio():
    """Every "inert" claim across six Phase 4 commits rests on this.

    At folio_cohort_threshold = 0.0 a FolioSet must open exactly one folio no
    matter how it is invested, so every len(bk.order) > 1 guard in the engine
    is unreachable and no snapshot can move. Nothing asserted it until now.
    """
    assert config.get("folio_cohort_threshold", 0.0) == 0.0, (
        "The gate is open. These inertness assumptions no longer hold, and the "
        "engine's guarded multi-folio branches are live -- which is the point "
        "at which the golden snapshots must be reviewed, not regenerated.")
    fs = new_folio_set()
    rng = random.Random(1)
    for k in range(60):
        fs.invest(rng.uniform(1.0, 50.0), 1.0 + 0.07 * k, k * 6)
    assert fs.order == ["F1"]
    assert len(fs.folios) == 1


def test_lots_is_by_reference_at_one_folio():
    """4a's compatibility guarantee: the ~80 sites that read .lots, and the
    one that still rebinds it, keep working because a single folio hands back
    the underlying list itself."""
    fs = new_folio_set()
    fs.invest(100.0, 1.0, 0)
    fs.lots.append(Lot(5.0, 2.0, 12))
    assert len(fs.lots) == 2
    assert fs.total_units == pytest.approx(100.0 + 5.0)


def test_lots_setter_refuses_across_folios():
    """The guard that made Phase 4b-iii necessary. Assigning .lots across
    folios would flatten every vintage into one queue, destroying exactly what
    the split is for."""
    fs = FolioSet(threshold=0.30, max_cohorts=5)
    for k in range(0, 25, 5):
        fs.invest(100.0, 1.0 + 0.07 * k, k * 12)
    assert len(fs.order) > 1
    with pytest.raises(NotImplementedError):
        fs.lots = list(fs.lots)


# ── harvest_to_budget: the only conversion on a live path ────────────────────

def _inline_harvest(lots, nav, m, harvest_budget):
    """Verbatim transcription of the loop in Engine._execute_ltcg_harvest.

    harvest_to_budget guards that loop and must reproduce it exactly at one
    folio, or the gate raise would move snapshots for a reason unrelated to
    folio selection.
    """
    harvest_total = 0.0
    new_lots = []
    for lot in lots:
        remaining_budget = harvest_budget - harvest_total
        if remaining_budget < 0.001:
            new_lots.append(lot)
            continue
        if m - lot.purchase_month < STCG:
            new_lots.append(lot)
            continue
        unrealised_gain = lot.units * (nav - lot.purchase_nav)
        if unrealised_gain <= 0.001:
            new_lots.append(lot)
            continue
        if unrealised_gain <= remaining_budget:
            harvest_total += unrealised_gain
            new_lots.append(Lot(lot.units, nav, m))
        else:
            fraction = remaining_budget / unrealised_gain
            harvest_units = lot.units * fraction
            keep_units = lot.units - harvest_units
            harvest_total += remaining_budget
            new_lots.append(Lot(harvest_units, nav, m))
            if keep_units > 1e-12:
                new_lots.append(
                    Lot(keep_units, lot.purchase_nav, lot.purchase_month))
    return harvest_total, new_lots


@pytest.mark.parametrize("budget", [0.0, 0.0005, 3.0, 40.0, 1e9])
def test_harvest_matches_the_loop_it_guards(budget):
    """Single folio, so ordering is identical and only the arithmetic is under
    test: the budget stop, the mid-lot fraction, the basis step-up, and the
    lots that are skipped for holding period or zero gain."""
    rng = random.Random(23)
    for trial in range(40):
        lots = _plan_lots(rng, rng.randint(2, 15))
        lots.append(Lot(5.0, 2.0, 350))          # inside the holding period
        lots.append(Lot(4.0, 9.0, 24))           # at a loss, must be skipped
        nav = 1.0 + 0.07 * rng.uniform(4, 28)
        want_total, want_lots = _inline_harvest(lots, nav, 360, budget)

        fs = _single_folio(lots)
        got_total = fs.harvest_to_budget(budget, nav, 360, STCG,
                                         prefer="harvest")
        assert got_total == pytest.approx(want_total, abs=1e-12), trial
        assert len(fs.lots) == len(want_lots), trial
        for a, b in zip(want_lots, fs.lots):
            assert a.units == pytest.approx(b.units, abs=1e-12)
            assert a.purchase_nav == pytest.approx(b.purchase_nav, abs=1e-12)
            assert a.purchase_month == b.purchase_month


def test_harvest_conserves_units_and_respects_the_budget():
    """A harvest sells NOTHING -- each lot is replaced by one of the same size
    at today's NAV. Any unit drift is a defect, not rounding."""
    rng = random.Random(77)
    for trial in range(60):
        fs = _plan_folio_set(rng.choice([0.10, 0.30, 0.50]), rng)
        nav = 1.0 + 0.07 * rng.uniform(12, 30)
        budget = rng.choice([0.5, rng.uniform(1.0, 400.0)])
        before = fs.total_units
        harvested = fs.harvest_to_budget(budget, nav, 400, STCG,
                                         prefer="harvest")
        assert fs.total_units == pytest.approx(before, abs=1e-9), trial
        assert harvested <= budget + 1e-9, trial


def test_harvest_takes_the_dearest_folio_first():
    """HANDOVER 4.5: a harvest wants the HIGHEST front-lot gain fraction, to
    realise the most gain per rupee churned. That is the opposite of a cash
    draw, and a single "pick the cheapest folio" scan gets the wrong half."""
    fs = FolioSet(threshold=0.30, max_cohorts=5)
    for k in range(0, 25, 5):
        fs.invest(100.0, 1.0 + 0.07 * k, k * 12)
    nav = 4.0
    assert len(fs.order) > 1
    fractions = {f: fs.gain_fraction(f, nav) for f in fs.order}
    dearest = max(fractions, key=fractions.get)
    before = {f: [(x.units, x.purchase_nav) for x in fs.folios[f].lots]
              for f in fs.order}
    fs.harvest_to_budget(50.0, nav, 400, STCG, prefer="harvest")
    touched = [f for f in fs.order
               if [(x.units, x.purchase_nav) for x in fs.folios[f].lots]
               != before[f]]
    assert touched == [dearest], (fractions, touched)


# ── sell_ordered ─────────────────────────────────────────────────────────────

def test_sell_ordered_never_comes_up_short():
    """REGRESSION TEST for the 4b-i defect, which nothing else would catch.

    redeem picked the cheapest folio and delegated; where that folio could not
    cover the amount, the remainder was silently dropped -- 240.5 units
    remaining against 228.3 for plain FIFO. It shipped green, was found by a
    throwaway script, and was fixed in 4b-ii by spilling onward. No test has
    ever pinned it.

    The invariant: what the caller is told it raised must equal what actually
    left the corpus.
    """
    rng = random.Random(5)
    for trial in range(60):
        fs = _plan_folio_set(rng.choice([0.10, 0.30, 0.50]), rng)
        nav = 1.0 + 0.07 * rng.uniform(10, 30)
        asked = rng.uniform(5.0, 400.0)
        before = fs.total_units
        stcg, ltcg, loads, sold = fs.sell_ordered(
            asked, nav, 400, STCG, EXIT_LOAD, prefer="cash")
        value_gone = (before - fs.total_units) * nav
        assert sold == pytest.approx(value_gone, abs=1e-6), trial
        assert sold <= asked + 1e-9, trial
        if fs.total_units > 1e-9:
            # Units remain, so the ask was fully met -- it must not have
            # stopped early with stock still available, which is the shape
            # the 4b-i defect took.
            assert sold == pytest.approx(asked, abs=1e-6), trial


def test_sell_ordered_takes_the_cheapest_folio_first():
    """The opposite ordering to the harvest, and the reason folios pay: cash
    should come from the folio whose basis is freshest."""
    fs = FolioSet(threshold=0.30, max_cohorts=5)
    for k in range(0, 25, 5):
        fs.invest(100.0, 1.0 + 0.07 * k, k * 12)
    nav = 4.0
    assert len(fs.order) > 1
    fractions = {f: fs.gain_fraction(f, nav) for f in fs.order}
    cheapest = min(fractions, key=fractions.get)
    before = {f: fs.folios[f].total_units for f in fs.order}
    fs.sell_ordered(60.0, nav, 400, STCG, EXIT_LOAD, prefer="cash")
    drawn = {f: before[f] - fs.folios[f].total_units for f in fs.order}
    assert max(drawn, key=drawn.get) == cheapest, (fractions, drawn)


def test_sell_ordered_at_one_folio_is_plain_fifo():
    """The compatibility claim from 4b-i: at a single folio this is exactly the
    loop it replaced."""
    rng = random.Random(11)
    lots = _plan_lots(rng, 8)
    fs = _single_folio(lots)
    bk = FIFOBucket()
    bk.lots = [Lot(x.units, x.purchase_nav, x.purchase_month) for x in lots]
    nav = 2.5
    _, _, _, sold = fs.sell_ordered(80.0, nav, 400, STCG, EXIT_LOAD,
                                    prefer="cash")
    bk.redeem(80.0, nav)
    assert sold == pytest.approx(80.0, abs=1e-9)
    assert fs.total_units == pytest.approx(bk.total_units, abs=1e-9)


# ── sell_to_tax_budget ───────────────────────────────────────────────────────

def test_sell_to_tax_budget_never_breaches_the_budget():
    """The mid-lot stop is the whole reason this method exists rather than
    another `prefer` value on sell_ordered."""
    rng = random.Random(31)
    for trial in range(60):
        fs = _plan_folio_set(rng.choice([0.10, 0.30, 0.50]), rng)
        nav = 1.0 + 0.07 * rng.uniform(10, 30)
        rate = rng.choice([0.125 * 1.04, 0.312])
        budget = rng.choice([0.0, 0.25, rng.uniform(0.5, 6.0)])
        before = fs.total_units
        stcg, ltcg, loads, net, tax = fs.sell_to_tax_budget(
            rng.uniform(5.0, 400.0), nav, 400, STCG, EXIT_LOAD, budget,
            lambda g, r=rate: max(0.0, g) * r, prefer="cash")
        assert tax <= budget + 1e-9, trial
        value_gone = (before - fs.total_units) * nav
        assert net + loads == pytest.approx(value_gone, abs=1e-6), trial


def test_sell_to_tax_budget_stops_mid_lot():
    """A budget that bites part-way through a single large lot must take the
    fraction that exactly exhausts it, not the whole lot and not none of it."""
    fs = FolioSet(threshold=0.30, max_cohorts=5)
    fs.invest(100.0, 1.0, 0)
    fs.invest(100.0, 2.0, 120)
    assert len(fs.order) > 1
    nav, rate, budget = 4.0, 0.20, 1.0
    stcg, ltcg, loads, net, tax = fs.sell_to_tax_budget(
        1e6, nav, 400, STCG, EXIT_LOAD, budget,
        lambda g: max(0.0, g) * rate, prefer="cash")
    assert tax == pytest.approx(budget, abs=1e-9)
    assert (stcg + ltcg) == pytest.approx(budget / rate, abs=1e-9)
    assert fs.total_units > 0.0


# ── HIFO stays gone ──────────────────────────────────────────────────────────

def test_the_engine_holds_no_hifo(project_root):
    """HIFO -- sorting every lot by gain and selling the cheapest first -- is
    not achievable. Indian practice computes capital gains FIFO per folio
    (CBDT Circular 704) and an AMC cannot be instructed to sell your
    highest-cost units. Folio choice is the only lot-selection lever.

    It lived only in _rebalance_portfolio and _estimate_transition_tax, both
    archived. This asserts it does not come back, in the same spirit as
    test_gui_horizon's assertion that no module carries a bare range(30).
    """
    src = (PACKAGE_DIR / "core" / "engine.py").read_text(encoding="utf-8")
    assert "use_hifo" not in src, (
        "use_hifo is back in engine.py. Lot selection within a folio is FIFO "
        "and not negotiable; the lever is which folio.")
    assert not re.search(r"sorted\([^)]*\.lots[^)]*purchase_nav", src), (
        "engine.py sorts lots by gain again. See archive/"
        "rebalance_portfolio.py for why that is a modelling fiction.")
