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
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from swp_planner.core.redemption_gate import (  # noqa: E402
    CORPORATE,
    EQUITY,
    MONEY_MARKET,
    BorrowBook,
    Gate,
    HarvestQueue,
    classify,
    execute_month,
)
from swp_planner.simulation import nav_feed as N  # noqa: E402

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
    cats = {f"d{i}": f["category"] for i, f in enumerate(debt)}
    cats["eq"] = EQUITY
    return units, navs, cats


def run(months, fmap, percentiles):
    units, navs, cats = build(months, fmap)
    gate = Gate(percentiles)
    book, queue = BorrowBook(), HarvestQueue()
    spared_months = 0

    for m in months[1:]:
        rows = []
        for k, nav in navs.items():
            px = nav.get(m)
            if px is None or units[k] <= 0:
                continue
            cat = cats[k]
            lb = gate.lookback_for(cat)
            if k == "eq" and not SELL_EQUITY_LAST:
                gate.observe(k, m, N.trailing_return(nav, m, lb))
                continue
            rows.append(dict(key=k, category=cat, price=px,
                             available=units[k] * px,
                             reading=N.trailing_return(nav, m, lb)))
        rows.sort(key=lambda r: (classify(r["category"]) == EQUITY, r["key"]))

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
    fmap = N.build_fund_map(path=N.NAV_SAMPLE_CSV)
    months = [d for yr in N.monthly_prices(fmap)["months"] for d in yr]
    base = run(months, fmap, {})

    print(f"{len(months)} months, {months[0]} .. {months[-1]}")
    print(f"start {START_CORPUS:.1f}L, draw {MONTHLY_NEED:.2f}L/month, "
          f"borrow at 12%\n")
    print(f"gate OFF -> net {base['net']:8.2f}L\n")
    print(f"{'p':>6}{'net':>10}{'vs off':>9}{'holdings':>10}{'debt':>9}"
          f"{'interest':>10}{'peak':>9}{'months owing':>14}")
    print("-" * 77)
    for grp in (MONEY_MARKET, CORPORATE, EQUITY):
      print(f"\n  -- {grp} --")
      for p in (0.02, 0.05, 0.10, 0.15, 0.20, 0.30):
        r = run(months, fmap, {grp: p})
        print(f"{p:>6.2f}{r['net']:>10.2f}{r['net'] - base['net']:>+9.2f}"
              f"{r['holdings']:>10.2f}{r['debt']:>9.2f}{r['interest']:>10.2f}"
              f"{r['peak']:>9.2f}{r['in_debt']:>14d}")
    print("\nOne group swept at a time, the others at their shipped defaults.")


if __name__ == "__main__":
    main()
