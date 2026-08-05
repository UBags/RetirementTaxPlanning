# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""redemption_gate.py

De-prioritise a fund for redemption when it is trading below its own trend,
carry the shortfall on borrowed money, and repay before investing.

THE RULE, IN ONE PARAGRAPH. Each month, each fund is judged against ITS OWN
history: the trailing lookback return is compared with a percentile of that
same fund's own past readings. Below the percentile, the fund is SPARED --
de-prioritised for redemption. What happens next depends on WHY the redemption
was going to happen:

    LIQUIDITY  the money is needed to live on. Draw it from an unspared fund
               if one can supply it; otherwise BORROW at 12% and repay at the
               first opportunity.

    HARVEST    the sale is a basis reset, sell-and-rebuy, CASH NEUTRAL. There
               is no shortfall to fund, so no borrowing arises. It is simply
               carried forward and executed in the first month the lot is not
               at a loss.

THE PERCENTILE IS PER CATEGORY GROUP, NOT PER ASSET CLASS. An earlier version
of this file split the world into equity and debt and gave debt one number.
Measured on nav_sample.csv that hid a real difference INSIDE debt: money market
and its short-duration siblings are pure accrual instruments whose trailing
return simply IS the prevailing yield, while corporate bond and its
duration-bearing siblings carry a genuine rate-cycle component. Below the tenth
percentile of its own trailing 12m return the money-market group went on to
earn 4.89% against 7.51% -- an edge of -2.63pp -- while the corporate group
managed -0.95pp. Same sign, a third of the size. Those are not one asset class
and one knob cannot serve both.

THE FOUR GROUPS, and why the line falls where it does:

    money_market   Overnight, Liquid, Ultra Short, Low Duration, Money Market.
                   Near-zero duration, accrual dominated. 0.15 sigma on a
                   liquid fund is a 0.09% price move -- there is nothing to
                   wait for.

    corporate      Short/Medium/Long Duration, Corporate Bond, Credit Risk,
                   Banking and PSU, Gilt, Dynamic Bond, Floater. Duration
                   and/or credit: a real cycle, but one that persists rather
                   than reverts over the horizon this rule acts on.

    hybrid         Aggressive, Conservative and Balanced Hybrid, Balanced
                   Advantage, Multi Asset, Equity Savings, Arbitrage.

    equity         Every equity category, plus index, ETF and fund-of-funds.

ARBITRAGE SITS IN HYBRID HERE EVEN THOUGH THE INCOME TAX ACT TREATS IT AS
EQUITY. This gate predicts PRICE BEHAVIOUR, not tax, so it keys on what drives
the NAV. The tax treatment is a separate question answered in a separate place,
and the two are meant to disagree.

WHAT THE DATA SAYS ABOUT THE DEFAULTS (nav_sample.csv, 19 years, 228 months):

    EQUITY MEAN-REVERTS. Below the 10th percentile of its trailing 18m return
    the blend went on to earn +65.1% over the next twelve months against
    +13.0% otherwise. Real, but it fired TWICE -- 2008-09 and 2020. Two
    events, not a law. Ships at 0.10.

    MONEY MARKET AND CORPORATE DO NOT, at -2.63pp and -0.95pp. Both ship at
    0.0 -- off. Swept end to end on a solvent 19-year drawdown the debt gate
    lost 1.54L at p0.02 and 24.10L at p0.30 WITH NOTHING BORROWED, because
    sparing one slot merely pushes the draw into another and the spared fund
    goes on to earn less. The loss is substitution, not interest; borrowing at
    12% would only deepen it.

    HYBRID IS UNCALIBRATED AND SHIPS OFF. There is not one hybrid fund in the
    corpus. A number chosen by interpolating between corporate and equity would
    look exactly as authoritative as the three that were measured, and it would
    be a guess wearing a measurement's clothes. When hybrid history arrives,
    calibrate it; until then it is 0.0 and this paragraph is why.

Every one of these is an argument from one sample, not an axiom. Sweeping them
is how they get re-argued against new data.
"""
from __future__ import annotations

# ── category groups ─────────────────────────────────────────────────────────

MONEY_MARKET = "money_market"
CORPORATE = "corporate"
HYBRID = "hybrid"
EQUITY = "equity"
GROUPS = (MONEY_MARKET, CORPORATE, HYBRID, EQUITY)

CATEGORY_GROUP = {
    # accrual, near-zero duration
    "overnight": MONEY_MARKET,
    "liquid": MONEY_MARKET,
    "ultra short duration": MONEY_MARKET,
    "low duration": MONEY_MARKET,
    "money market": MONEY_MARKET,
    # duration and/or credit
    "short duration": CORPORATE,
    "medium duration": CORPORATE,
    "medium to long duration": CORPORATE,
    "long duration": CORPORATE,
    "dynamic bond": CORPORATE,
    "corporate bond": CORPORATE,
    "credit risk": CORPORATE,
    "banking and psu": CORPORATE,
    "floater": CORPORATE,
    "gilt": CORPORATE,
    "gilt with 10 year constant duration": CORPORATE,
    # hybrid
    "conservative hybrid": HYBRID,
    "balanced hybrid": HYBRID,
    "aggressive hybrid": HYBRID,
    "dynamic asset allocation or balanced advantage": HYBRID,
    "balanced advantage": HYBRID,
    "multi asset allocation": HYBRID,
    "arbitrage": HYBRID,
    "equity savings": HYBRID,
    # equity
    "multi cap": EQUITY,
    "flexi cap": EQUITY,
    "large cap": EQUITY,
    "large and mid cap": EQUITY,
    "large & mid cap": EQUITY,
    "mid cap": EQUITY,
    "small cap": EQUITY,
    "dividend yield": EQUITY,
    "value": EQUITY,
    "contra": EQUITY,
    "focused": EQUITY,
    "sectoral": EQUITY,
    "thematic": EQUITY,
    "sectoral/thematic": EQUITY,
    "elss": EQUITY,
    "index": EQUITY,
    "etf": EQUITY,
    "fof overseas": EQUITY,
    "fof domestic": EQUITY,
    "retirement": EQUITY,
    "children's": EQUITY,
}

# ── parameters, per group ───────────────────────────────────────────────────

GATE_PERCENTILE = {
    MONEY_MARKET: 0.0,      # measured -2.63pp; off
    CORPORATE: 0.0,         # measured -0.95pp; off
    HYBRID: 0.0,            # NOT MEASURED -- no hybrid fund in the corpus
    EQUITY: 0.10,           # measured +52.1pp, on two events
}

GATE_LOOKBACK = {
    MONEY_MARKET: 12,       # months
    CORPORATE: 12,
    HYBRID: 18,
    EQUITY: 18,
}

WARMUP_OBSERVATIONS = 36      # readings needed before the gate may fire at all
BORROW_RATE = 0.12            # per annum, simple monthly accrual
BORROW_CAP = None             # absolute ceiling on balance; None = uncapped
EPS = 1e-12


class UnknownCategory(KeyError):
    """A category no rule covers.

    RAISED RATHER THAN DEFAULTED. Quietly filing an unrecognised category under
    some group would apply a percentile nobody chose for it, and the run would
    look identical to a correct one. A new SEBI category is a five-second edit
    to CATEGORY_GROUP; a silently misgrouped fund is a defect that never
    surfaces.
    """


def classify(category):
    """AMFI/SEBI category string -> one of GROUPS. Raises on anything else.

    Tolerant of the noise real category strings carry -- case, a trailing
    'Fund', hyphens -- and intolerant of anything it cannot place.
    """
    if category in GROUPS:
        return category
    key = " ".join(str(category).lower().replace("-", " ").split())
    for suffix in (" fund", " funds", " scheme"):
        while key.endswith(suffix):
            key = key[: -len(suffix)].strip()
    if key in CATEGORY_GROUP:
        return CATEGORY_GROUP[key]
    for name, grp in CATEGORY_GROUP.items():
        if key.startswith(name):
            return grp
    raise UnknownCategory(
        f"{category!r} matches no entry in CATEGORY_GROUP. Add it there "
        f"rather than defaulting it -- a misgrouped fund gets a percentile "
        f"nobody chose and the run still looks correct.")


# ── the percentile gate ─────────────────────────────────────────────────────

def percentile(sample, p):
    """Linear-interpolated percentile. Returns None on an empty sample."""
    xs = sorted(sample)
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
    expanding rather than peeking.

    `percentiles` and `lookbacks` are dicts keyed by group OR by any category
    string that classifies into one. Anything omitted falls back to the module
    default FOR THAT GROUP, never to a global one.
    """

    def __init__(self, percentiles=None, lookbacks=None,
                 warmup=WARMUP_OBSERVATIONS):
        self.percentiles = dict(GATE_PERCENTILE)
        self.lookbacks = dict(GATE_LOOKBACK)
        for src, dst in ((percentiles, self.percentiles),
                         (lookbacks, self.lookbacks)):
            for k, v in (src or {}).items():
                dst[classify(k)] = v
        self.warmup = warmup
        self._hist = {}          # fund key -> [readings, oldest first]
        self._seen = {}          # fund key -> months already observed

    def percentile_for(self, category):
        return self.percentiles[classify(category)]

    def lookback_for(self, category):
        return self.lookbacks[classify(category)]

    def active_groups(self):
        """Groups whose percentile is above zero. For reporting."""
        return tuple(g for g in GROUPS if self.percentiles[g] > 0.0)

    def spared(self, key, reading, category):
        """True when this fund should be de-prioritised for redemption.

        A reading of None means NO VIEW -- not enough history -- and no view
        is NOT below trend. A gate that read a missing signal as 'spare it'
        would defer every redemption for its first eighteen months on no
        evidence whatsoever.
        """
        p = self.percentile_for(category)
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

    def threshold(self, key, category):
        """The live threshold, for reporting. None before warm-up."""
        p = self.percentile_for(category)
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
        if self.cap is None:
            return float("inf")
        return max(0.0, self.cap - self.balance)

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
        category    AMFI/SEBI category string, or a group name directly. THIS
                    SELECTS BOTH THE PERCENTILE AND THE LOOKBACK, so the
                    caller must read the trailing return over
                    gate.lookback_for(category) rather than a fixed window --
                    a corporate fund judged on 18 months and an equity fund on
                    12 is a different rule from the one that was measured.
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
        if gate.spared(f["key"], f.get("reading"), f["category"]):
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
