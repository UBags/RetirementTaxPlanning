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


def financial_years(dates):
    """Complete Indian financial years, 1 April to 31 March, inside `dates`.

    Returns [(label, first_iso, last_iso), ...]. INCOMPLETE YEARS AT EITHER
    END ARE DROPPED, because every tax figure in the model -- the s.87A
    ceiling, the HUF nil band, the s.112A exemption, the surcharge thresholds
    -- is a full-year quantity assessed on a full year's income. A part-year
    would be charged full-year allowances and read as a tax-free windfall.
    """
    have = set(dates)
    out = []
    y = int(dates[0][:4]) - 1
    while True:
        y += 1
        start, end = f"{y}-04-01", f"{y + 1}-03-31"
        if start < dates[0]:
            continue
        if end > dates[-1]:
            break
        inside = [d for d in dates if start <= d <= end]
        if not inside or inside[0] not in have:
            continue
        out.append((f"FY{y}-{(y + 1) % 100:02d}", inside[0], inside[-1]))
    return out


def annual_returns(fmap):
    """Realised return per financial year, per debt slot and for equity.

    WHAT THE WORLD DELIVERED, and nothing else. The plan must still SCORE its
    candidates at the modelled rates -- a plan cannot see next year's return,
    which is the same separation the Monte Carlo already enforces through
    EQUITY_PATH and DEBT_PATH. These returns replace what the world does, not
    what the plan believes.

    Returns {"years": [...], "debt": [[r_slot1..r_slotN], ...], "equity": [r]}
    with one row per financial year.
    """
    fys = financial_years(fmap["dates"])
    if not fys:
        raise ValueError("no complete financial years in the sample")
    debt_rows, eq_rows = [], []
    for _label, first, last in fys:
        debt_rows.append([f["nav"][last] / f["nav"][first] - 1.0
                          for f in fmap["debt"]])
        eq_rows.append(fmap["equity"]["nav"][last]
                       / fmap["equity"]["nav"][first] - 1.0)
    return dict(years=[f[0] for f in fys], debt=debt_rows, equity=eq_rows,
                spans=[(f[1], f[2]) for f in fys])


# ── FORWARD SYNTHESIS ──────────────────────────────────────────────────────

SYNTH_DEBT_DELTA = 0.010     # debt earns 1.0 point less than it historically did
SYNTH_EQUITY_DELTA = 0.015   # equity 1.5 points less
SYNTH_BLOCK_DAYS = 60        # bootstrap block length
SYNTH_SEED = 20260804


def _daily_returns(nav, dates):
    prev, out = None, []
    for d in dates:
        v = nav.get(d)
        if v is None:
            out.append(0.0)
            continue
        out.append(0.0 if prev is None or prev <= 0 else v / prev - 1.0)
        prev = v
    return out


def _monthly_vol(nav, dates):
    months = {}
    for d in dates:
        if d in nav:
            months[d[:7]] = nav[d]
    ks = sorted(months)
    rets = [months[b] / months[a] - 1.0 for a, b in zip(ks, ks[1:])
            if months[a] > 0]
    return st.pstdev(rets) * (12 ** 0.5) if len(rets) > 1 else 0.0


def _cagr_from(levels):
    years = (len(levels) - 1) / 365.25
    return (levels[-1] / levels[0]) ** (1.0 / years) - 1.0 if years > 0 else 0.0


def extend_series(fmap, to_years, debt_delta=SYNTH_DEBT_DELTA,
                  equity_delta=SYNTH_EQUITY_DELTA, seed=SYNTH_SEED,
                  block_days=SYNTH_BLOCK_DAYS):
    """Continue every series forward to `to_years`, keeping sigma and cutting
    return.

    TWO GUARANTEES, both enforced by solving rather than hoping.

      SIGMA IS PRESERVED EXACTLY. The synthetic stretch is scaled until its
      annualised volatility, measured from month ends exactly as everything
      else in this file measures it, equals the historical figure to 1e-9. Not
      approximately: the deviations are rescaled and remeasured until they
      match, because "same sigma" was the non-negotiable half of the request.

      RETURN IS CUT BY A STATED AMOUNT. The drift is then solved so the
      synthetic CAGR lands exactly on (historical CAGR - delta), 1.0 point for
      debt and 1.5 for equity by default. Solved for the same reason: adding a
      drift to bootstrapped returns does not move the CAGR by the amount added,
      because compounding is not linear in the mean.

    THE THIRD THING, WHICH WAS NOT ASKED FOR AND MATTERS MOST

      EVERY FUND IS SAMPLED ON THE SAME DATES. The bootstrap draws block start
      positions ONCE and applies them to all funds, so whatever happened to
      debt and equity together in the historical window happens together in the
      synthetic one. Measured on this data, monthly returns of debt slot 1
      against slot 6 correlate at +0.72 and against equity at +0.13. Synthesise
      each fund independently and those collapse toward zero -- thirty-three
      debt funds would look like thirty-three independent bets instead of one
      asset class, the diversification would be imaginary, and the plan would
      appear far safer than it is.

      A BLOCK bootstrap rather than a daily one, for the same reason at a
      different scale: drawing single days destroys volatility clustering, so
      a synthetic 2008 never happens. Sixty-day blocks keep crashes and
      recoveries intact.

    Returns a new fmap. The originals are not modified.
    """
    import random

    hist = fmap["dates"]
    need_days = int(round(to_years * 365.25)) - len(hist)
    if need_days <= 0:
        return fmap

    series = [(f, debt_delta) for f in fmap["debt"]]
    series.append((fmap["equity"], equity_delta))

    rng = random.Random(seed)
    n = len(hist)
    # ONE set of block starts, shared by every fund -- this is the correlation
    # guarantee. Blocks are drawn from anywhere in the history.
    starts = [rng.randrange(0, max(1, n - block_days))
              for _ in range(need_days // block_days + 2)]

    from datetime import date, timedelta
    last = date.fromisoformat(hist[-1])
    future = [(last + timedelta(days=i + 1)).isoformat()
              for i in range(need_days)]

    out_debt, out_eq = [], None
    for f, delta in series:
        rets = _daily_returns(f["nav"], hist)
        drawn = []
        for st_i in starts:
            drawn.extend(rets[st_i:st_i + block_days])
            if len(drawn) >= need_days:
                break
        drawn = drawn[:need_days]
        mean = sum(drawn) / len(drawn)
        dev = [r - mean for r in drawn]

        target_vol = _monthly_vol(f["nav"], hist)
        target_cagr = f["cagr"] - delta
        base = f["nav"][hist[-1]]

        scale, drift = 1.0, mean
        for _ in range(60):
            lv, v = [base], base
            for x in dev:
                v *= (1.0 + x * scale + drift)
                lv.append(v)
            nav_new = dict(zip([hist[-1]] + future, lv))
            got_vol = _monthly_vol(nav_new, [hist[-1]] + future)
            got_cagr = _cagr_from(lv)
            if (abs(got_vol - target_vol) < 1e-9
                    and abs(got_cagr - target_cagr) < 1e-9):
                break
            if got_vol > 1e-12:
                scale *= target_vol / got_vol
            drift += (target_cagr - got_cagr) / 365.25
        else:
            raise RuntimeError(f"synthesis did not converge for "
                               f"{f.get('code', 'equity')}")

        merged = dict(f["nav"])
        merged.update({d: lv[i + 1] for i, d in enumerate(future)})
        new = dict(f, nav=merged, **_stats(merged))
        new["synth_from"] = future[0]
        new["hist_cagr"] = f["cagr"]
        new["hist_vol"] = target_vol
        if f is fmap["equity"]:
            out_eq = new
        else:
            out_debt.append(new)

    notes = list(fmap["notes"]) + [
        f"extended to {to_years} years: {len(future)} synthetic days from "
        f"{future[0]}, sigma preserved exactly, CAGR cut by "
        f"{debt_delta:.1%} on debt and {equity_delta:.1%} on equity",
        "synthetic blocks are drawn on COMMON dates, so cross-fund "
        "correlation survives; independent draws would make the "
        "diversification imaginary",
    ]
    return dict(debt=out_debt, equity=out_eq, dates=hist + future, notes=notes)


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