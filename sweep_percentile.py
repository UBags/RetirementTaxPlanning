"""sweep_percentile.py -- what the gate is worth, at every percentile.

THE COMPARATOR IS NET TERMINAL WEALTH: holdings at the closing price MINUS the
outstanding borrowing. An earlier attempt scored the rule on how far each
month's redemption deviated from the annual plan, which punished the rule for
doing the thing it exists to do and made both directions look catastrophic.
Deviation is not the objective. What the corpus is worth at the end, net of
what is owed, is.

Everything is drawn from real month-end NAVs. The equity gate is swept
separately from the debt gate, because the two signals were measured to run in
OPPOSITE directions and a single knob would net them against each other and
report a wash.
"""
import nav_feed as N
from redemption_gate import BorrowBook, Gate, HarvestQueue, execute_month

LOOKBACK_DEBT = 12
LOOKBACK_EQUITY = 18
START_CORPUS = 580.0          # lakh, personal 390 + HUF 190
DEBT_SHARE = 0.70
MONTHLY_NEED = 3.33           # lakh, the 40L/yr rung
SELL_EQUITY_LAST = True       # equity is the last resort, so borrowing
                              # happens ONLY when the gate spares everything


def build(months, fmap):
    """Units held per slot, and a price lookup, from month-end NAVs."""
    d0 = months[0]
    debt = fmap["debt"]
    per = START_CORPUS * DEBT_SHARE / len(debt)
    units = {f"d{i}": per / f["nav"][d0] for i, f in enumerate(debt)}
    units["eq"] = START_CORPUS * (1 - DEBT_SHARE) / fmap["equity"]["nav"][d0]
    navs = {f"d{i}": f["nav"] for i, f in enumerate(debt)}
    navs["eq"] = fmap["equity"]["nav"]
    return units, navs


def run(months, fmap, p_debt, p_equity):
    units, navs = build(months, fmap)
    gate = Gate(percentile_equity=p_equity, percentile_debt=p_debt)
    book, queue = BorrowBook(), HarvestQueue()
    spared_months = 0

    for m in months[1:]:
        rows = []
        for k, nav in navs.items():
            px = nav.get(m)
            if px is None or units[k] <= 0:
                continue
            eq = k == "eq"
            if eq and not SELL_EQUITY_LAST:
                gate.observe(k, m, N.trailing_return(nav, m, LOOKBACK_EQUITY))
                continue
            lb = LOOKBACK_EQUITY if eq else LOOKBACK_DEBT
            rows.append(dict(key=k, is_equity=eq, price=px,
                             available=units[k] * px,
                             reading=N.trailing_return(nav, m, lb)))
        rows.sort(key=lambda r: (r["is_equity"], r["key"]))

        res, per_fund, _ = execute_month(m, MONTHLY_NEED, rows, gate, book,
                                         queue)
        spared_months += 1 if res.spared else 0
        for k, amt in per_fund.items():
            units[k] -= amt / navs[k][m]

    last = months[-1]
    holdings = sum(u * navs[k][last] for k, u in units.items()
                   if navs[k].get(last))
    return dict(holdings=holdings, debt=book.balance,
                net=holdings - book.balance, interest=book.interest_paid,
                peak=book.peak, in_debt=book.months_in_debt,
                spared_months=spared_months)


def main():
    fmap = N.build_fund_map(path="nav_sample.csv")
    months = [d for yr in N.monthly_prices(fmap)["months"] for d in yr]
    base = run(months, fmap, 0.0, 0.0)

    print(f"{len(months)} months, {months[0]} .. {months[-1]}")
    print(f"start {START_CORPUS:.1f}L, draw {MONTHLY_NEED:.2f}L/month, "
          f"borrow at 12%\n")
    print(f"gate OFF -> net {base['net']:8.2f}L\n")
    print(f"{'p':>6}{'net':>10}{'vs off':>9}{'holdings':>10}{'debt':>9}"
          f"{'interest':>10}{'peak':>9}{'months owing':>14}")
    print("-" * 77)
    for p in (0.02, 0.05, 0.10, 0.15, 0.20, 0.30):
        r = run(months, fmap, p, 0.0)
        print(f"{p:>6.2f}{r['net']:>10.2f}{r['net'] - base['net']:>+9.2f}"
              f"{r['holdings']:>10.2f}{r['debt']:>9.2f}{r['interest']:>10.2f}"
              f"{r['peak']:>9.2f}{r['in_debt']:>14d}")
    print("\n(debt gate swept; equity is never sold for cash on this plan)")


if __name__ == "__main__":
    main()
