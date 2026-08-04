"""Tests for redemption_gate.

THE ONES THAT MATTER are the no-lookahead test and the repay-before-invest
test. Everything else is arithmetic; those two are the properties that would
silently produce a flattering, wrong answer if they broke.
"""
import pytest

from redemption_gate import (
    BorrowBook,
    Gate,
    HarvestQueue,
    execute_month,
    percentile,
)


def _fund(key, avail, reading, eq=False, price=100.0):
    return dict(key=key, is_equity=eq, available=avail, reading=reading,
                price=price)


# ── the gate ────────────────────────────────────────────────────────────────

def test_gate_is_silent_before_warmup():
    g = Gate(percentile_debt=0.5, warmup=36)
    for i in range(35):
        assert not g.spared("f", -99.0, False)
        g.observe("f", f"m{i}", i / 100.0)
    assert not g.spared("f", -99.0, False), "fired one reading short of warm-up"
    g.observe("f", "m35", 0.35)
    assert g.spared("f", -99.0, False), "still silent AFTER warm-up"


def test_percentile_zero_never_fires():
    g = Gate(percentile_debt=0.0, warmup=1)
    for i in range(50):
        g.observe("f", f"m{i}", 0.05)
    assert not g.spared("f", -1.0, False)


def test_no_view_is_not_below_trend():
    """None must mean NO VIEW. Reading it as 'spare' defers on no evidence."""
    g = Gate(percentile_debt=0.5, warmup=2)
    g.observe("f", "a", 0.10)
    g.observe("f", "b", 0.20)
    assert not g.spared("f", None, False)


def test_threshold_cannot_see_the_month_it_judges():
    """
    THE LOOKAHEAD TEST. A crash month must be judged against the calm months
    BEFORE it. If the reading leaked into its own threshold, a single
    catastrophic value would drag the percentile down to meet itself and the
    gate would stay shut in exactly the month it exists for.
    """
    g = Gate(percentile_debt=0.20, warmup=3)
    for i, r in enumerate([0.06, 0.07, 0.08, 0.09]):
        g.observe("f", f"m{i}", r)
    crash = -0.40
    assert g.spared("f", crash, False)
    thr_before = g.threshold("f", False)
    g.observe("f", "crash", crash)
    assert g.threshold("f", False) < thr_before, "observing must move it AFTER"


def test_observe_is_idempotent_per_month():
    g = Gate(percentile_debt=0.5, warmup=1)
    for _ in range(5):
        g.observe("f", "same-month", 0.10)
    assert len(g._hist["f"]) == 1


def test_equity_and_debt_read_different_percentiles():
    g = Gate(percentile_equity=0.50, percentile_debt=0.0, warmup=2)
    for i, r in enumerate([0.10, 0.20, 0.30]):
        g.observe("e", f"m{i}", r)
        g.observe("d", f"m{i}", r)
    assert g.spared("e", 0.05, True)
    assert not g.spared("d", 0.05, False)


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
             _fund("c", 30.0, -0.9, eq=True)]
    g = Gate(percentile_equity=0.0, percentile_debt=0.0, warmup=0)
    r, per, _ = execute_month("m1", 50.0, funds, g, BorrowBook(),
                              HarvestQueue())
    assert per == _plain_draw(50.0, funds)
    assert r.borrowed == 0.0 and r.spared == []


def test_a_spared_fund_pushes_the_draw_to_the_next_one():
    funds = [_fund("a", 100.0, -0.9), _fund("b", 100.0, 0.9)]
    g = Gate(percentile_debt=0.5, warmup=2)
    for i, r in enumerate([0.05, 0.06, 0.07]):
        g.observe("a", f"w{i}", r)
        g.observe("b", f"w{i}", r)
    _, per, _ = execute_month("m1", 40.0, funds, g, BorrowBook(),
                              HarvestQueue())
    assert "a" not in per and per["b"] == pytest.approx(40.0)


def test_borrowing_covers_what_sparing_removed():
    funds = [_fund("a", 100.0, -0.9)]
    g = Gate(percentile_debt=0.5, warmup=2)
    for i, r in enumerate([0.05, 0.06, 0.07]):
        g.observe("a", f"w{i}", r)
    b = BorrowBook()
    res, per, _ = execute_month("m1", 40.0, funds, g, b, HarvestQueue())
    assert per == {} and res.borrowed == pytest.approx(40.0)
    assert res.shortfall == 0.0 and b.balance == pytest.approx(40.0)


def test_shortfall_is_reported_when_the_cap_binds():
    funds = [_fund("a", 100.0, -0.9)]
    g = Gate(percentile_debt=0.5, warmup=2)
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
