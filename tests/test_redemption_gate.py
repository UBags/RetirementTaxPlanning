"""Tests for redemption_gate.

THE ONES THAT MATTER are the no-lookahead test and the repay-before-invest
test. Everything else is arithmetic; those two are the properties that would
silently produce a flattering, wrong answer if they broke.
"""
import pytest

from swp_planner.core.redemption_gate import (
    CORPORATE,
    EQUITY,
    HYBRID,
    MONEY_MARKET,
    BorrowBook,
    Gate,
    HarvestQueue,
    UnknownCategory,
    classify,
    execute_month,
    percentile,
)

LIQUID = "Liquid Fund"
CORP = "Corporate Bond Fund"
FLEXI = "Flexi Cap Fund"


def _fund(key, avail, reading, cat=LIQUID, price=100.0):
    return dict(key=key, category=cat, available=avail, reading=reading,
                price=price)


# ── the gate ────────────────────────────────────────────────────────────────

def test_gate_is_silent_before_warmup():
    g = Gate({MONEY_MARKET: 0.5}, warmup=36)
    for i in range(35):
        assert not g.spared("f", -99.0, LIQUID)
        g.observe("f", f"m{i}", i / 100.0)
    assert not g.spared("f", -99.0, LIQUID), "fired one reading short of warm-up"
    g.observe("f", "m35", 0.35)
    assert g.spared("f", -99.0, LIQUID), "still silent AFTER warm-up"


def test_percentile_zero_never_fires():
    g = Gate({MONEY_MARKET: 0.0}, warmup=1)
    for i in range(50):
        g.observe("f", f"m{i}", 0.05)
    assert not g.spared("f", -1.0, LIQUID)


def test_no_view_is_not_below_trend():
    """None must mean NO VIEW. Reading it as 'spare' defers on no evidence."""
    g = Gate({MONEY_MARKET: 0.5}, warmup=2)
    g.observe("f", "a", 0.10)
    g.observe("f", "b", 0.20)
    assert not g.spared("f", None, LIQUID)


def test_threshold_cannot_see_the_month_it_judges():
    """
    THE LOOKAHEAD TEST. A crash month must be judged against the calm months
    BEFORE it. If the reading leaked into its own threshold, a single
    catastrophic value would drag the percentile down to meet itself and the
    gate would stay shut in exactly the month it exists for.
    """
    g = Gate({MONEY_MARKET: 0.20}, warmup=3)
    for i, r in enumerate([0.06, 0.07, 0.08, 0.09]):
        g.observe("f", f"m{i}", r)
    crash = -0.40
    assert g.spared("f", crash, LIQUID)
    thr_before = g.threshold("f", LIQUID)
    g.observe("f", "crash", crash)
    assert g.threshold("f", LIQUID) < thr_before, "observing must move it AFTER"


def test_observe_is_idempotent_per_month():
    g = Gate({MONEY_MARKET: 0.5}, warmup=1)
    for _ in range(5):
        g.observe("f", "same-month", 0.10)
    assert len(g._hist["f"]) == 1


def test_groups_read_different_percentiles():
    g = Gate({EQUITY: 0.50, MONEY_MARKET: 0.0}, warmup=2)
    for i, r in enumerate([0.10, 0.20, 0.30]):
        g.observe("e", f"m{i}", r)
        g.observe("d", f"m{i}", r)
    assert g.spared("e", 0.05, FLEXI)
    assert not g.spared("d", 0.05, LIQUID)


def test_percentile_interpolates():
    assert percentile([1, 2, 3, 4], 0.0) == 1
    assert percentile([1, 2, 3, 4], 1.0) == 4
    assert percentile([1, 2, 3, 4], 0.5) == pytest.approx(2.5)
    assert percentile([], 0.5) is None


# ── the borrow book ─────────────────────────────────────────────────────────

def test_interest_accrues_at_one_twelfth_of_the_rate():
    b = BorrowBook(rate=0.12)
    b.draw("m1", 1200.0)
    b.accrue("m2")
    assert b.balance == pytest.approx(1212.0)
    assert b.interest_paid == pytest.approx(12.0)


def test_no_interest_on_a_zero_balance():
    b = BorrowBook(rate=0.12)
    b.accrue("m1")
    assert b.balance == 0.0 and b.months_in_debt == 0


def test_surplus_repays_before_it_invests():
    """
    THE PRECEDENCE TEST. apply_surplus is the only door spare cash passes
    through, and what it RETURNS is the only thing available to invest. A
    surplus smaller than the debt must return exactly nothing.
    """
    b = BorrowBook(rate=0.12)
    b.draw("m1", 100.0)
    assert b.apply_surplus("m2", 40.0) == 0.0
    assert b.balance == pytest.approx(60.0)
    assert b.apply_surplus("m3", 100.0) == pytest.approx(40.0)
    assert b.balance == 0.0


def test_cap_limits_the_draw():
    b = BorrowBook(rate=0.12, cap=50.0)
    assert b.draw("m1", 80.0) == pytest.approx(50.0)
    assert b.draw("m1", 10.0) == 0.0


def test_peak_is_remembered():
    b = BorrowBook()
    b.draw("m1", 100.0)
    b.apply_surplus("m2", 100.0)
    assert b.balance == 0.0 and b.peak == pytest.approx(100.0)


# ── the harvest queue ───────────────────────────────────────────────────────

def test_harvest_defers_at_a_loss_and_releases_above_basis():
    q = HarvestQueue()
    q.defer("m1", "f", units=10.0, cost_basis=100.0)
    assert q.release("m2", lambda k: 95.0) == []
    assert len(q.pending) == 1
    out = q.release("m3", lambda k: 100.0)
    assert len(out) == 1 and out[0]["price"] == 100.0
    assert q.pending == []


def test_harvest_at_exactly_basis_is_not_a_loss():
    q = HarvestQueue()
    q.defer("m1", "f", 1.0, 100.0)
    assert len(q.release("m2", lambda k: 100.0)) == 1


def test_missing_price_holds_rather_than_releases():
    q = HarvestQueue()
    q.defer("m1", "f", 1.0, 100.0)
    assert q.release("m2", lambda k: None) == []
    assert len(q.pending) == 1


# ── the month ───────────────────────────────────────────────────────────────

def _plain_draw(need, funds):
    drawn, per = 0.0, {}
    for f in funds:
        if drawn >= need:
            break
        take = min(f["available"], need - drawn)
        if take > 0:
            per[f["key"]] = take
            drawn += take
    return per


def test_gate_off_reproduces_the_plain_draw_exactly():
    """
    THE ORACLE. With both percentiles at zero the gate must be INVISIBLE --
    same funds, same amounts, nothing borrowed. Any deviation here means the
    machinery is charging the plan for a feature it has switched off.
    """
    funds = [_fund("a", 30.0, -0.9), _fund("b", 30.0, -0.9),
             _fund("c", 30.0, -0.9, cat=FLEXI)]
    g = Gate({EQUITY: 0.0, MONEY_MARKET: 0.0}, warmup=0)
    r, per, _ = execute_month("m1", 50.0, funds, g, BorrowBook(),
                              HarvestQueue())
    assert per == _plain_draw(50.0, funds)
    assert r.borrowed == 0.0 and r.spared == []


def test_a_spared_fund_pushes_the_draw_to_the_next_one():
    funds = [_fund("a", 100.0, -0.9), _fund("b", 100.0, 0.9)]
    g = Gate({MONEY_MARKET: 0.5}, warmup=2)
    for i, r in enumerate([0.05, 0.06, 0.07]):
        g.observe("a", f"w{i}", r)
        g.observe("b", f"w{i}", r)
    _, per, _ = execute_month("m1", 40.0, funds, g, BorrowBook(),
                              HarvestQueue())
    assert "a" not in per and per["b"] == pytest.approx(40.0)


def test_borrowing_covers_what_sparing_removed():
    funds = [_fund("a", 100.0, -0.9)]
    g = Gate({MONEY_MARKET: 0.5}, warmup=2)
    for i, r in enumerate([0.05, 0.06, 0.07]):
        g.observe("a", f"w{i}", r)
    b = BorrowBook()
    res, per, _ = execute_month("m1", 40.0, funds, g, b, HarvestQueue())
    assert per == {} and res.borrowed == pytest.approx(40.0)
    assert res.shortfall == 0.0 and b.balance == pytest.approx(40.0)


def test_shortfall_is_reported_when_the_cap_binds():
    funds = [_fund("a", 100.0, -0.9)]
    g = Gate({MONEY_MARKET: 0.5}, warmup=2)
    for i, r in enumerate([0.05, 0.06, 0.07]):
        g.observe("a", f"w{i}", r)
    res, _, _ = execute_month("m1", 40.0, funds, g, BorrowBook(cap=10.0),
                              HarvestQueue())
    assert res.borrowed == pytest.approx(10.0)
    assert res.shortfall == pytest.approx(30.0)


def test_interest_accrues_before_the_month_draws():
    """A draw before accrual would hand the plan one free month of credit."""
    b = BorrowBook(rate=0.12)
    b.draw("m0", 1200.0)
    execute_month("m1", 0.0, [], Gate(), b, HarvestQueue())
    assert b.balance == pytest.approx(1212.0)


def test_surplus_settles_debt_and_returns_the_remainder():
    b = BorrowBook(rate=0.12)
    b.draw("m0", 100.0)
    res, _, leftover = execute_month("m1", 0.0, [], Gate(), b, HarvestQueue(),
                                     surplus=150.0)
    assert b.balance == 0.0
    assert res.repaid == pytest.approx(101.0)
    assert leftover == pytest.approx(49.0)


def test_underwater_harvest_is_queued_not_sold():
    q = HarvestQueue()
    res, _, _ = execute_month("m1", 0.0, [], Gate(), BorrowBook(), q,
                              harvest=[("f", 10.0, 100.0, 90.0)])
    assert res.harvest_deferred == ["f"] and len(q.pending) == 1


def test_a_healthy_harvest_passes_straight_through():
    q = HarvestQueue()
    res, _, _ = execute_month("m1", 0.0, [], Gate(), BorrowBook(), q,
                              harvest=[("f", 10.0, 100.0, 110.0)])
    assert res.harvest_deferred == [] and q.pending == []


def test_a_queued_harvest_releases_when_the_price_recovers():
    q = HarvestQueue()
    funds = [_fund("f", 0.0, None, price=90.0)]
    execute_month("m1", 0.0, funds, Gate(), BorrowBook(), q,
                  harvest=[("f", 10.0, 100.0, 90.0)])
    funds = [_fund("f", 0.0, None, price=105.0)]
    res, _, _ = execute_month("m2", 0.0, funds, Gate(), BorrowBook(), q)
    assert len(res.harvest_released) == 1 and q.pending == []


def test_deferring_a_harvest_borrows_nothing():
    """A harvest is sell-and-rebuy. Postponing it raises no shortfall."""
    b = BorrowBook()
    execute_month("m1", 0.0, [], Gate(), b, HarvestQueue(),
                  harvest=[("f", 10.0, 100.0, 50.0)])
    assert b.balance == 0.0


# ── the category groups ─────────────────────────────────────────────────────

def test_every_group_is_reachable_from_a_real_category_string():
    assert classify("Liquid Fund") == MONEY_MARKET
    assert classify("Ultra Short Duration Fund") == MONEY_MARKET
    assert classify("Corporate Bond Fund") == CORPORATE
    assert classify("Gilt Fund with 10 year constant duration") == CORPORATE
    assert classify("Aggressive Hybrid Fund") == HYBRID
    assert classify("Flexi Cap Fund") == EQUITY
    assert classify("ELSS") == EQUITY


def test_classify_tolerates_case_hyphens_and_the_fund_suffix():
    assert classify("LIQUID FUND") == MONEY_MARKET
    assert classify("ultra-short duration") == MONEY_MARKET
    assert classify("Money Market") == MONEY_MARKET


def test_a_group_name_classifies_to_itself():
    for g in (MONEY_MARKET, CORPORATE, HYBRID, EQUITY):
        assert classify(g) == g


def test_an_unknown_category_raises_rather_than_defaulting():
    """
    THE ONE THAT MATTERS HERE. Silently filing an unrecognised category under
    some group applies a percentile nobody chose, and the run looks correct.
    """
    with pytest.raises(UnknownCategory):
        classify("Sovereign Gold Bond Fund")


def test_arbitrage_is_hybrid_here_despite_its_tax_treatment():
    """The gate predicts PRICE behaviour; the Act's equity treatment is a
    separate question answered somewhere else, and they are meant to differ."""
    assert classify("Arbitrage Fund") == HYBRID


def test_money_market_and_corporate_are_separately_settable():
    """The whole point of the split: they measured -2.63pp and -0.95pp."""
    g = Gate({MONEY_MARKET: 0.30, CORPORATE: 0.05}, warmup=2)
    assert g.percentile_for("Liquid Fund") == 0.30
    assert g.percentile_for("Corporate Bond Fund") == 0.05
    assert g.percentile_for("Flexi Cap Fund") == 0.10      # untouched default


def test_lookbacks_are_per_group_too():
    g = Gate(lookbacks={CORPORATE: 24})
    assert g.lookback_for("Corporate Bond Fund") == 24
    assert g.lookback_for("Liquid Fund") == 12
    assert g.lookback_for("Flexi Cap Fund") == 18


def test_an_omitted_group_falls_back_to_its_own_default_not_a_global_one():
    g = Gate({EQUITY: 0.25})
    assert g.percentile_for("Flexi Cap Fund") == 0.25
    assert g.percentile_for("Liquid Fund") == 0.0
    assert g.percentile_for("Corporate Bond Fund") == 0.0


def test_percentiles_may_be_keyed_by_category_string():
    g = Gate({"Corporate Bond Fund": 0.07})
    assert g.percentile_for("Gilt Fund") == 0.07, "keyed the GROUP, not the fund"


def test_shipped_defaults_are_the_measured_ones():
    """Pins the argument, so a casual edit has to argue with this test."""
    g = Gate()
    assert g.percentile_for("Liquid Fund") == 0.0
    assert g.percentile_for("Corporate Bond Fund") == 0.0
    assert g.percentile_for("Aggressive Hybrid Fund") == 0.0
    assert g.percentile_for("Flexi Cap Fund") == 0.10
    assert g.active_groups() == (EQUITY,)


def test_two_groups_at_once_spare_independently():
    g = Gate({MONEY_MARKET: 0.50, CORPORATE: 0.0}, warmup=2)
    for i, r in enumerate([0.05, 0.06, 0.07]):
        g.observe("mm", f"w{i}", r)
        g.observe("cp", f"w{i}", r)
    assert g.spared("mm", 0.01, "Liquid Fund")
    assert not g.spared("cp", 0.01, "Corporate Bond Fund")


def test_execute_month_routes_the_draw_past_a_spared_group():
    g = Gate({MONEY_MARKET: 0.50, CORPORATE: 0.0}, warmup=2)
    for i, r in enumerate([0.05, 0.06, 0.07]):
        g.observe("mm", f"w{i}", r)
        g.observe("cp", f"w{i}", r)
    funds = [_fund("mm", 100.0, -0.9, cat=LIQUID),
             _fund("cp", 100.0, -0.9, cat=CORP)]
    res, per, _ = execute_month("m1", 40.0, funds, g, BorrowBook(),
                                HarvestQueue())
    assert res.spared == ["mm"] and per == {"cp": 40.0}


def test_an_unknown_category_in_a_fund_row_raises_during_the_month():
    funds = [_fund("x", 10.0, -0.9, cat="Sovereign Gold Bond Fund")]
    with pytest.raises(UnknownCategory):
        execute_month("m1", 5.0, funds, Gate({MONEY_MARKET: 0.5}, warmup=0),
                      BorrowBook(), HarvestQueue())
