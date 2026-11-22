#!/usr/bin/env python3
"""What the same money would have cost the user in fixed deposits.

This exists for ONE purpose: to print a number showing how much tax the
strategy saved against simply holding the money in FDs. It is illustrative.
It is deliberately NOT a portfolio model, and it should not be made into one.

THE SPECIFICATION, in three parts.

(a) THE RATE. Take the family of liquid funds. For each, compute six-month
    rolling returns over the last twelve months -- six figures per fund. Across
    thirty funds that is 180 figures. Average them, express per annum WITHOUT
    compounding, then add 1% to get the fictional FD rate.

(b) THE CORPUS. ONLY money the user put in from outside, with NO growth of any
    kind. That means the opening allocation plus every external addition in any
    horizon year -- ad hoc windfalls and systematic contributions alike, to
    either purse. It does NOT include investment returns, reinvested income, or
    anything the strategy itself generated. The FD corpus is a running total of
    external contributions and nothing else.

(c) THE TAX. Apply (a) to (b) for each purse to get its FD interest. Add that
    purse's other taxable income. Compute tax with the SAME slab structure and
    rate schedule the horizon uses. Sum the two purses for the per-year figure,
    and across the horizon for the total.

No compounding. No drawdown. No reinvestment. The corpus only ever grows by
external contribution.

DATA GAP -- READ BEFORE TRUSTING THE RATE
=========================================
Part (a) as specified CANNOT be computed from the current codebase. There is no
NAV history anywhere: `FundEntry` carries point-in-time metrics (`cagr_1`,
`cagr_3`, `cagr_5`, `cagr_10`, std dev, sharpe, sortino, calmar, alpha, beta,
treynor, max drawdown) and the metrics CSV carries the same. Six-month rolling
returns need a monthly NAV series that does not exist.

`liquid_fund_rate()` therefore falls back to the mean 1Y CAGR across liquid
funds, plus the 1%. That is a DIFFERENT statistic: it is one twelve-month
figure per fund rather than six six-month figures, so it cannot see
within-year variation and it is compounded where the spec asks for simple.

The fallback is marked, and `--fd-rate` overrides it outright. Implement the
specified calculation when a NAV series exists; the shape of the function is
already right for it.

Usage:
    python3 scripts/probe_fd_benchmark.py
    python3 scripts/probe_fd_benchmark.py --fd-rate 0.075
    python3 scripts/probe_fd_benchmark.py --metrics-csv path/to/metrics.csv
    python3 scripts/probe_fd_benchmark.py --scenario s1_2chunk --per-year
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "golden"))

from golden_projects import PROJECTS  # noqa: E402

from swp_planner.core.engine import compute_slab_tax  # noqa: E402
from swp_planner.core.models import AppState  # noqa: E402
from swp_planner.core.pro_rated_utils import months_in_fy  # noqa: E402

SCENARIOS = [
    ("main", 0, "s0_1chunk"), ("main", 1, "s1_2chunk"),
    ("main", 2, "s2_3chunk"), ("main", 3, "s3_4chunk"),
    ("hist_8yr", 2, "hist_8yr"),
]

FD_PREMIUM = 0.01           # added to the liquid-fund average, per the spec
LIQUID_MARKER = "liquid"    # matched case-insensitively against the category


# ═══════════════════════════════════════════════════════════════════════════
#  (a) the rate
# ═══════════════════════════════════════════════════════════════════════════

def liquid_fund_rate(metrics_csv: Path | None = None, override: float | None = None):
    """The fictional FD rate. Returns (rate, description of how it was got).

    SPECIFIED: mean of six-month rolling returns over the last twelve months
    across every liquid fund, annualised WITHOUT compounding, plus 1%.

    IMPLEMENTED: mean 1Y CAGR across liquid funds, plus 1% -- because no NAV
    series exists to roll over. See the module docstring.
    """
    if override is not None:
        return override, f"explicit --fd-rate {override:.4%}"

    if metrics_csv is None or not Path(metrics_csv).exists():
        raise SystemExit(
            "FATAL: no metrics CSV. The FD rate is derived from liquid-fund\n"
            "returns, so it cannot be guessed. Pass --metrics-csv, or pass\n"
            "--fd-rate to supply the rate directly."
        )

    rates = []
    with open(metrics_csv, "r", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            category = " ".join(str(v) for v in list(row.values())[:1]).lower()
            if LIQUID_MARKER not in category:
                continue
            raw = row.get("1Y_CAGR", "")
            try:
                val = float(raw)
            except (TypeError, ValueError):
                continue
            if val > 0:
                rates.append(val)

    if not rates:
        raise SystemExit(
            f"FATAL: found no liquid funds with a usable 1Y_CAGR in "
            f"{metrics_csv}.\nCheck the category column and the header name."
        )

    mean = sum(rates) / len(rates)
    return mean + FD_PREMIUM, (
        f"mean 1Y CAGR of {len(rates)} liquid funds = {mean:.4%}, "
        f"+{FD_PREMIUM:.2%} = {mean + FD_PREMIUM:.4%}  "
        f"[FALLBACK: spec wants six-month rolling returns; no NAV series]"
    )


# ═══════════════════════════════════════════════════════════════════════════
#  (b) the corpus -- external contributions only, no growth
# ═══════════════════════════════════════════════════════════════════════════

def external_contributions(state: AppState):
    """{fy: (personal added this year, huf added this year)}.

    The opening allocation counts as a contribution in the first year. Every
    windfall counts in its own year, whether ad hoc or systematic -- the model
    does not distinguish them and neither should this.
    """
    first_fy = next(iter(state.fy_range()))
    adds: dict[int, list[float]] = {}

    def _bump(fy, personal=0.0, huf=0.0):
        row = adds.setdefault(fy, [0.0, 0.0])
        row[0] += personal
        row[1] += huf

    _bump(first_fy, personal=state.total_allocation())
    for w in getattr(state, "windfalls", []):
        target = (getattr(w, "target", "") or "").strip().lower()
        amount = float(getattr(w, "amount", 0.0) or 0.0)
        if amount <= 0:
            continue
        if target == "huf":
            _bump(int(w.year), huf=amount)
        else:
            _bump(int(w.year), personal=amount)
    return {fy: tuple(v) for fy, v in adds.items()}


# ═══════════════════════════════════════════════════════════════════════════
#  (c) the tax
# ═══════════════════════════════════════════════════════════════════════════

def _slab_tax_or_raise(income, chunk, entity, fy):
    """compute_slab_tax, but a missing chunk is an ERROR rather than zero tax.

    compute_slab_tax returns 0.0 when `chunk is None`. For most callers that is
    a reasonable default; here it is not. FD interest is taxable at slab
    whether or not a debt chunk happens to be configured for the year, so a
    missing chunk silently zeroing the benchmark would understate it -- and the
    benchmark is the denominator of the acceptance criterion.
    """
    if chunk is None:
        raise SystemExit(
            f"FATAL: no debt tax chunk for {entity} in FY{fy}. The FD benchmark\n"
            f"cannot be computed without one, and defaulting to zero tax would\n"
            f"silently flatter every reduction percentage that quotes it."
        )
    return compute_slab_tax(income, chunk, entity)


def fd_tax_benchmark(state: AppState, fd_rate: float):
    """Per-year and lifetime FD tax. Returns (rows, total).

    Each row: (fy, corpus_p, corpus_h, interest_p, interest_h, tax_p, tax_h).
    """
    adds = external_contributions(state)
    corpus_p = corpus_h = 0.0
    rows, total = [], 0.0

    for fy in state.fy_range():
        add_p, add_h = adds.get(fy, (0.0, 0.0))
        corpus_p += add_p
        corpus_h += add_h

        # pro-rate the first (or any partial) year, exactly as the engine does
        months = months_in_fy(state.investment_date, fy)
        year_rate = fd_rate * months / 12.0

        interest_p = corpus_p * year_rate
        interest_h = corpus_h * year_rate

        assess_p = state.personal_income.assessable(fy, months, "individual")
        assess_h = state.huf_income.assessable(fy, months, "huf")

        tax_p = _slab_tax_or_raise(assess_p + interest_p,
                                   state.get_debt_tax_chunk(fy, "individual"),
                                   "individual", fy)
        tax_h = _slab_tax_or_raise(assess_h + interest_h,
                                   state.get_debt_tax_chunk(fy, "huf"),
                                   "huf", fy)

        total += tax_p + tax_h
        rows.append((fy, corpus_p, corpus_h, interest_p, interest_h,
                     tax_p, tax_h))

    return rows, total


# ═══════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--metrics-csv", type=Path, default=None,
                    help="fund metrics CSV, for the liquid-fund rate")
    ap.add_argument("--fd-rate", type=float, default=None,
                    help="use this rate outright, e.g. 0.075")
    ap.add_argument("--scenario", default=None,
                    help="one scenario label; default is all five")
    ap.add_argument("--per-year", action="store_true",
                    help="print the year-by-year table")
    a = ap.parse_args()

    rate, how = liquid_fund_rate(a.metrics_csv, a.fd_rate)
    print(f"FD rate: {how}\n")

    wanted = [s for s in SCENARIOS if a.scenario in (None, s[2])]
    if not wanted:
        raise SystemExit(f"unknown scenario {a.scenario!r}; "
                         f"one of {[s[2] for s in SCENARIOS]}")

    print(f"{'scenario':>12}{'FD tax':>12}{'ext. corpus':>14}")
    print("-" * 38)
    for proj, idx, label in wanted:
        state = AppState.from_dict(PROJECTS[proj]["scenarios"][idx])
        rows, total = fd_tax_benchmark(state, rate)
        final_p, final_h = rows[-1][1], rows[-1][2]
        print(f"{label:>12}{total:>12.2f}{final_p + final_h:>14.2f}")

        if a.per_year:
            print(f"\n  {'FY':>4}{'corpus_p':>11}{'corpus_h':>11}"
                  f"{'int_p':>10}{'int_h':>10}{'tax_p':>9}{'tax_h':>9}")
            for fy, cp, ch, ip, ih, tp, th in rows:
                print(f"  {fy:>4}{cp:>11.2f}{ch:>11.2f}"
                      f"{ip:>10.2f}{ih:>10.2f}{tp:>9.3f}{th:>9.3f}")
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
