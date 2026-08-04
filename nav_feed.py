# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""nav_feed.py

Turn nav_sample.csv into the shape swp_dp_model.py wants: twenty-one debt funds
in a defined order, and one equity series.

THE ALLOCATION LOGIC LIVES IN ONE FUNCTION -- build_fund_map. If the rule for
which real fund becomes f1 changes, that is the only thing to edit; everything
downstream takes the mapping as given.

WHY SORTED BY REALISED CAGR

  The model gives base fund k a rate from spec["rate"](k) -- a smooth ladder
  running 6.00% down to 5.25% across the chunks, with the lowest-numbered funds
  paying most. That ordering is not decoration: the chunk transitions move
  money DOWN the ladder over time, and the whole glide-path argument assumes
  f1 yields more than f21.

  Real funds arrive unordered. Assigning them positionally would scramble the
  ladder, so a "transition to a lower chunk" could move money into a
  higher-yielding fund and the transition schedule would be measuring noise.
  Sorting by realised twenty-year CAGN, descending, preserves what the ladder
  is FOR.

  It is a choice, not a law. Sorting by volatility, or by duration category,
  or leaving it positional, are all defensible -- which is exactly why the rule
  is one function.

THE EQUITY SLEEVE IS ONE SERIES, NOT FIVE

  Sleeve.gain(nav), Sleeve.value(nav) and Sleeve.sell(nav, ...) all take a
  SINGLE nav. The twenty buckets share one price by construction. They are
  vintage buckets of one holding, and that is deliberate -- the docstring
  explains that filling them sequentially rather than equally makes each a
  distinct vintage, so the most recent bucket has a gain fraction near 16%
  instead of 72% and the same s.112A exemption frees about four times the cash.

  So the five real equity funds are blended into one index: rebased to 1.0 at
  the first date they all share, then averaged. That is EQUAL WEIGHT AT
  INCEPTION AND THEREAFTER BUY-AND-HOLD, not daily rebalancing -- averaging
  rebased levels lets the winners grow their share, which is what an untouched
  sleeve actually does. Averaging daily RETURNS would silently rebalance every
  day and flatter the result.

A CAVEAT THE DATA CARRIES, and it belongs here rather than in a footnote

  SEBI recategorised the whole industry in 2018 and AMFI reports TODAY's
  category against the WHOLE history. "UTI - Overnight Fund" has eleven
  negative months between 2008 and 2017 and none at all after April 2018,
  because it was not an overnight fund then. Category labels are trustworthy
  from about 2018 and not before.

  For testing monthly execution that does not matter -- what is needed is real
  prices with real intra-year movement, and these are real. For calibrating
  what a debt ladder yields, it matters a great deal.
"""

from __future__ import annotations

import csv
import statistics as st
from pathlib import Path

DEBT_CATEGORIES = (
    "Overnight Fund", "Liquid Fund", "Money Market Fund",
    "Ultra Short Duration Fund", "Low Duration Fund",
    "Short Duration Fund", "Corporate Bond Fund",
)
EQUITY_CATEGORIES = ("Large Cap Fund", "Flexi Cap Fund", "Mid Cap Fund",
                     "Large & Mid Cap Fund")


def load_nav_sample(path="nav_sample.csv"):
    """Parse the wide CSV into one record per fund.

    Header is code|category|name. A blank cell means the fund did not exist on
    that date and is left absent rather than filled, because a pre-launch zero
    would read as a real price.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"{p} not found. Build it with build_nav_corpus.py then "
            f"extract_nav_sample.py."
        )
    with open(p, newline="") as fh:
        rows = list(csv.reader(fh))
    header, data = rows[0], rows[1:]

    funds = []
    for i, col in enumerate(header[1:], 1):
        parts = col.split("|")
        if len(parts) != 3:
            raise ValueError(f"column {i} header is not code|category|name: "
                             f"{col!r}")
        code, category, name = parts
        nav = {r[0]: float(r[i]) for r in data if r[i]}
        if not nav:
            continue
        funds.append(dict(code=code, category=category, name=name, nav=nav,
                          **_stats(nav)))
    return funds


def _stats(nav):
    """CAGR and annualised volatility from the fund's own month ends."""
    months = {}
    for d in sorted(nav):
        months[d[:7]] = nav[d]
    keys = sorted(months)
    years = len(keys) / 12.0
    cagr = (months[keys[-1]] / months[keys[0]]) ** (1.0 / years) - 1.0
    rets = [months[b] / months[a] - 1.0 for a, b in zip(keys, keys[1:])]
    return dict(first=keys[0], last=keys[-1], months=len(keys),
                cagr=cagr, vol=st.pstdev(rets) * (12 ** 0.5))


def blend_equal_weight(funds):
    """One index from several funds: rebase to 1.0, then average.

    EQUAL WEIGHT AT INCEPTION, BUY-AND-HOLD AFTER. Averaging rebased LEVELS
    lets a fund that trebles carry three times the weight of one that does not,
    which is what an untouched sleeve does. Averaging daily RETURNS instead
    would rebalance every day, quietly selling winners, and would report a
    return no investor could have had without trading daily.

    The index starts on the first date every member has a price. Days where
    some member is missing carry that member's last known price, since a
    missing NAV means no new price was published.
    """
    if not funds:
        raise ValueError("nothing to blend")
    start = max(min(f["nav"]) for f in funds)
    end = min(max(f["nav"]) for f in funds)
    dates = sorted({d for f in funds for d in f["nav"] if start <= d <= end})

    base, last = {}, {}
    for f in funds:
        base[f["code"]] = f["nav"][min(d for d in f["nav"] if d >= start)]

    index = {}
    for d in dates:
        total = 0.0
        for f in funds:
            if d in f["nav"]:
                last[f["code"]] = f["nav"][d]
            if f["code"] not in last:
                break
            total += last[f["code"]] / base[f["code"]]
        else:
            index[d] = total / len(funds)
    return index


def build_fund_map(sample=None, n_base=21, path="nav_sample.csv"):
    """THE ALLOCATION RULE. Change this function, change the allocation.

    Returns

        {
          "debt": [ {slot, code, category, name, cagr, vol, nav}, ... ],
          "equity": {"members": [...], "nav": {...}, "cagr":, "vol":},
          "dates": [...],          # the dates every debt fund shares
          "notes": [...],          # anything a reader should know
        }

    debt is sorted by realised CAGR DESCENDING and slotted 1..n_base, so slot 1
    is the highest-yielding fund and matches the highest chunk rate. Fewer
    funds than n_base is an error rather than a silent short list: the chunk
    ladder addresses f1..f21 and a gap would be read as an empty fund.
    """
    funds = sample if sample is not None else load_nav_sample(path)
    notes = []

    debt = [f for f in funds if f["category"] in DEBT_CATEGORIES]
    equity = [f for f in funds if f["category"] in EQUITY_CATEGORIES]
    other = [f for f in funds
             if f["category"] not in DEBT_CATEGORIES + EQUITY_CATEGORIES]
    if other:
        notes.append(f"{len(other)} fund(s) in neither list, ignored: "
                     + ", ".join(sorted({f['category'] for f in other})))

    if len(debt) < n_base:
        raise ValueError(
            f"only {len(debt)} debt funds for {n_base} base slots. The chunk "
            f"ladder addresses f1..f{n_base} and a gap reads as an empty "
            f"fund. Harvest more, or lower n_base."
        )
    if len(debt) > n_base:
        notes.append(f"{len(debt)} debt funds for {n_base} slots -- the "
                     f"{len(debt) - n_base} lowest-yielding are unused")
    if not equity:
        raise ValueError("no equity funds in the sample")

    debt = sorted(debt, key=lambda f: -f["cagr"])[:n_base]
    for slot, f in enumerate(debt, 1):
        f["slot"] = slot

    eq_index = blend_equal_weight(equity)
    eq = dict(members=[f["code"] for f in equity], nav=eq_index,
              **_stats(eq_index))
    notes.append(f"equity is an equal-weight buy-and-hold blend of "
                 f"{len(equity)} funds")

    shared = set(debt[0]["nav"])
    for f in debt[1:]:
        shared &= set(f["nav"])
    shared &= set(eq_index)

    # Category labels are only trustworthy from the 2018 recategorisation.
    notes.append("AMFI reports today's category against the whole history; "
                 "SEBI recategorised in 2018, so labels before then describe "
                 "a fund the scheme no longer is")

    return dict(debt=debt, equity=eq, dates=sorted(shared), notes=notes)


def describe(fmap):
    """A readable summary, for eyeballing before anything consumes it."""
    out = [f"{'slot':>5}  {'code':>7}  {'category':26s}{'CAGR':>8}{'vol':>8}"
           f"  fund", "-" * 92]
    for f in fmap["debt"]:
        out.append(f"{f['slot']:>5}  {f['code']:>7}  {f['category']:26s}"
                   f"{f['cagr'] * 100:>7.2f}%{f['vol'] * 100:>7.2f}%  "
                   f"{f['name'][:30]}")
    e = fmap["equity"]
    out.append("")
    out.append(f"{'EQUITY':>5}  {'blend':>7}  {'equal weight':26s}"
               f"{e['cagr'] * 100:>7.2f}%{e['vol'] * 100:>7.2f}%  "
               f"{len(e['members'])} funds: {', '.join(e['members'])}")
    out.append("")
    out.append(f"{len(fmap['dates'])} dates shared by every series, "
               f"{fmap['dates'][0]} to {fmap['dates'][-1]}")
    for n in fmap["notes"]:
        out.append(f"  note: {n}")
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    print(describe(build_fund_map(
        path=sys.argv[1] if len(sys.argv) > 1 else "nav_sample.csv")))