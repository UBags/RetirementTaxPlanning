# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""redemption_gate.py

De-prioritise a fund for redemption when it is trading below its own trend,
carry the shortfall on borrowed money, and repay before investing.

THE RULE, IN ONE PARAGRAPH. Each month, each fund is judged against ITS OWN
history: the trailing lookback return (18 months for equity, 12 for debt) is
compared with a percentile of that same fund's own past readings. Below the
percentile, the fund is SPARED -- de-prioritised for redemption. What happens
next depends on WHY the redemption was going to happen:

    LIQUIDITY  the money is needed to live on. Draw it from an unspared fund
               if one can supply it; otherwise BORROW at 12% and repay at the
               first opportunity.

    HARVEST    the sale is a basis reset, sell-and-rebuy, CASH NEUTRAL. There
               is no shortfall to fund, so no borrowing arises. It is simply
               carried forward and executed in the first month the lot is not
               at a loss.

THE PERCENTILE IS EXPANDING-WINDOW, NEVER FULL-SAMPLE. The threshold for month
N is computed from readings 1..N-1 only. A full-sample percentile would let the
backtest see the 2008 crash before deciding whether to defer in 2007, and every
number it produced would be a fiction.

WHAT THE DATA SAYS ABOUT THE DEFAULTS (nav_sample.csv, 19 years, 228 months):

    EQUITY MEAN-REVERTS. Below the 10th percentile of its own trailing 18m
    return, the blend went on to earn +65.1% over the next twelve months
    against +13.0% otherwise. Real, but it fired TWICE -- 2008-09 and 2020.
    Two events, not a law.

    DEBT DOES NOT. Below the 10th percentile of its own trailing 12m return,
    the 35 slots went on to earn +5.47% against +7.63% -- the signal runs
    BACKWARDS. A debt fund's trailing return is the prevailing rate level, and
    rate levels persist: a low reading forecasts more low readings, not a
    rebound.

    AND BORROWING AT 12% SETTLES IT. Over a three-month wait the borrow costs
    2.87%; a debt fund beat that in 3.6% of months. On a liquid fund the whole
    0.15-sigma dip being waited out is a 0.09% price move. So the debt gate
    ships at percentile 0.0 -- OFF -- and equity at 0.10. Both are arguments,
    not axioms; sweep_percentile() is how they get re-argued against new data.
"""
from __future__ import annotations

# ── parameters ──────────────────────────────────────────────────────────────

LOOKBACK_EQUITY = 18          # months
LOOKBACK_DEBT = 12

GATE_PERCENTILE_EQUITY = 0.10
GATE_PERCENTILE_DEBT = 0.0    # 0.0 means never spare; measured, see module doc

WARMUP_OBSERVATIONS = 36      # readings needed before the gate may fire at all
BORROW_RATE = 0.12            # per annum, simple monthly accrual
BORROW_CAP = None             # absolute ceiling on balance; None = uncapped
EPS = 1e-12


# ── the percentile gate ─────────────────────────────────────────────────────

def percentile(sorted_or_not, p):
    """Linear-interpolated percentile. Returns None on an empty sample."""
    xs = sorted(sorted_or_not)
    if not xs:
        return None
    k = (len(xs) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


class Gate:
    """Per-fund, expanding-window percentile on the trailing return.

    ONE INSTANCE PER RUN, FED IN MONTH ORDER. `observe` appends this month's
    reading AFTER the decision is taken, which is what keeps the window
    expanding rather than peeking. Calling them out of order is the one way to
    make this thing lie, so `spared` refuses to answer for a month it has
    already observed.
    """

    def __init__(self, percentile_equity=GATE_PERCENTILE_EQUITY,
                 percentile_debt=GATE_PERCENTILE_DEBT,
                 warmup=WARMUP_OBSERVATIONS):
        self.p_eq = percentile_equity
        self.p_dt = percentile_debt
        self.warmup = warmup
        self._hist = {}          # fund key -> [readings, oldest first]
        self._seen = {}          # fund key -> set of months already observed

    def _p(self, is_equity):
        return self.p_eq if is_equity else self.p_dt

    def spared(self, key, reading, is_equity):
        """True when this fund should be de-prioritised for redemption.

        A reading of None means NO VIEW -- not enough history -- and no view
        is NOT below trend. A gate that read a missing signal as 'spare it'
        would defer every redemption for its first eighteen months on no
        evidence whatsoever.
        """
        p = self._p(is_equity)
        if p <= 0.0 or reading is None:
            return False
        past = self._hist.get(key, ())
        if len(past) < self.warmup:
            return False
        thr = percentile(past, p)
        return thr is not None and reading < thr

    def observe(self, key, month, reading):
        """Record the reading so it counts toward FUTURE thresholds only."""
        if reading is None:
            return
        seen = self._seen.setdefault(key, set())
        if month in seen:
            return
        seen.add(month)
        self._hist.setdefault(key, []).append(reading)

    def threshold(self, key, is_equity):
        """The live threshold, for reporting. None before warm-up."""
        p = self._p(is_equity)
        past = self._hist.get(key, ())
        if p <= 0.0 or len(past) < self.warmup:
            return None
        return percentile(past, p)


# ── the borrow book ─────────────────────────────────────────────────────────

class BorrowBook:
    """Money borrowed to avoid a redemption, at 12% p.a., repaid first.

    REPAYMENT TAKES PRECEDENCE OVER INVESTMENT. `apply_surplus` is the single
    door through which spare cash passes, and it settles the balance before it
    hands anything back to be invested. Routing surplus to investment while a
    12% liability sits open is a guaranteed loss of the spread, and the only
    way to guarantee it cannot happen is to make it structurally impossible to
    express.

    Interest ACCRUES INTO THE BALANCE monthly and is not separately capitalised
    for tax: borrowing against securities to fund living expenses is not an
    expense incurred to earn income, so no deduction is assumed. If that
    changes, it changes here and nowhere else.
    """

    def __init__(self, rate=BORROW_RATE, cap=BORROW_CAP):
        self.rate = rate
        self.cap = cap
        self.balance = 0.0
        self.interest_paid = 0.0
        self.peak = 0.0
        self.months_in_debt = 0
        self.ledger = []

    @property
    def headroom(self):
        return float("inf") if self.cap is None else max(0.0, self.cap - self.balance)

    def accrue(self, month):
        """One month of interest. Call ONCE per month, before any draw."""
        if self.balance > EPS:
            i = self.balance * self.rate / 12.0
            self.balance += i
            self.interest_paid += i
            self.months_in_debt += 1
            self.ledger.append((month, "interest", i, self.balance))

    def draw(self, month, amount):
        """Borrow up to `amount`. Returns what was actually raised."""
        got = min(amount, self.headroom)
        if got <= EPS:
            return 0.0
        self.balance += got
        self.peak = max(self.peak, self.balance)
        self.ledger.append((month, "draw", got, self.balance))
        return got

    def apply_surplus(self, month, cash):
        """Settle debt first; return only what is left over to invest."""
        if cash <= EPS or self.balance <= EPS:
            return cash
        pay = min(cash, self.balance)
        self.balance -= pay
        self.ledger.append((month, "repay", pay, self.balance))
        return cash - pay


# ── the deferred harvest queue ──────────────────────────────────────────────

class HarvestQueue:
    """Harvest sales postponed because the lot was under water.

    A HARVEST IS CASH NEUTRAL -- sell and rebuy the same value -- so deferring
    one raises no shortfall and needs no borrowing. All it costs is a delayed
    basis reset, and all it buys is not crystallising a loss that would lower
    the basis rather than raise it.

    The queue is FIFO and re-tested every month against the live price.
    """

    def __init__(self):
        self.pending = []        # [{fund, units, cost_basis, since}]
        self.executed = []
        self.deferrals = 0

    def defer(self, month, fund, units, cost_basis):
        self.pending.append(dict(fund=fund, units=units,
                                 cost_basis=cost_basis, since=month))
        self.deferrals += 1

    def release(self, month, price_of):
        """Everything now at or above its basis. Returns the released items."""
        out, keep = [], []
        for item in self.pending:
            px = price_of(item["fund"])
            if px is not None and px >= item["cost_basis"] - EPS:
                item = dict(item, price=px, executed=month)
                out.append(item)
                self.executed.append(item)
            else:
                keep.append(item)
        self.pending = keep
        return out


# ── the month ───────────────────────────────────────────────────────────────

class MonthResult:
    __slots__ = ("month", "drawn", "borrowed", "spared", "harvest_deferred",
                 "harvest_released", "repaid", "shortfall")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    def __repr__(self):
        return (f"<{self.month} drawn={self.drawn:.3f} "
                f"borrowed={self.borrowed:.3f} spared={len(self.spared)}>")


def execute_month(month, need, funds, gate, book, queue,
                  harvest=(), surplus=0.0):
    """One month of drawing, sparing, borrowing and repaying.

    `funds` is an ordered list of dicts -- the redemption ORDER is the caller's
    business, decided by the tax logic, not here. Each carries:

        key         stable identifier
        is_equity   which lookback and which percentile apply
        available   value that could be redeemed this month
        reading     trailing return at this month end, or None for no view

    `harvest` is [(key, units, cost_basis, price)] the tax layer wants to
    re-base this month. `surplus` is spare cash arriving from anywhere else.

    ORDER MATTERS AND IS FIXED: accrue interest, then release any harvest that
    has come back above water, then draw from unspared funds in the caller's
    order, then borrow the remainder, then apply surplus to the debt. Drawing
    before accruing would give one free month of credit; applying surplus
    before drawing would repay with money about to be re-borrowed.
    """
    book.accrue(month)

    price_of = {f["key"]: f.get("price") for f in funds}
    released = queue.release(month, lambda k: price_of.get(k))

    spared, drawn, per_fund = [], 0.0, {}
    for f in funds:
        if gate.spared(f["key"], f.get("reading"), f["is_equity"]):
            spared.append(f["key"])
            continue
        if drawn >= need - EPS:
            continue
        take = min(f["available"], need - drawn)
        if take > EPS:
            per_fund[f["key"]] = take
            drawn += take

    borrowed = book.draw(month, max(0.0, need - drawn))
    shortfall = max(0.0, need - drawn - borrowed)

    deferred = []
    for key, units, basis, px in harvest:
        if px < basis - EPS:
            queue.defer(month, key, units, basis)
            deferred.append(key)

    before = book.balance
    leftover = book.apply_surplus(month, surplus)
    repaid = before - book.balance

    for f in funds:
        gate.observe(f["key"], month, f.get("reading"))

    return MonthResult(month=month, drawn=drawn, borrowed=borrowed,
                       spared=spared, harvest_deferred=deferred,
                       harvest_released=released, repaid=repaid,
                       shortfall=shortfall), per_fund, leftover
