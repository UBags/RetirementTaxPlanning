#!/usr/bin/env python3
"""What the same plan would have cost entirely in fixed deposits.

METHODOLOGY §16A uses `YearSummary.fd_tax_benchmark` as the denominator for
"how much tax did we avoid". That field is not a counterfactual. It is

    state.total_allocation() * fd_rate(fy)

taxed at slab -- a FROZEN personal-only corpus. Two consequences, both large:

* The HUF is absent. `total_allocation()` is the personal opening corpus;
  the HUF's inheritance never enters, so a third of the money pays no tax in
  the benchmark at all.
* The corpus never moves. Interest is identical in FY30 and FY1, so the
  comparison sets a strategy ending with 1708.6 of corpus against a benchmark
  ending with 214.8.

This builds the honest version: the same opening money, the same requirement,
the same other income, held entirely in fixed deposits. Interest is taxed at
slab every year in the hands of whoever holds it; whatever the requirement
still needs comes out of principal.

WHY IT MATTERS BEYOND TIDINESS

The benchmark is the denominator of the acceptance criterion. Understating it
flatters every percentage in §16A.1. A criterion that grades against a
weakened alternative is not a criterion.

Usage:  python3 scripts/probe_fd_benchmark.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "golden"))

from golden_projects import PROJECTS  # noqa: E402

from engine import compute_slab_tax  # noqa: E402
from models import AppState  # noqa: E402
from net_income_plan import project_plan  # noqa: E402
from pro_rated_utils import months_in_fy  # noqa: E402
from transition_driver import clear_plan_cache  # noqa: E402

SCENARIOS = [
    ("main", 0, "s0_1chunk"), ("main", 1, "s1_2chunk"),
    ("main", 2, "s2_3chunk"), ("main", 3, "s3_4chunk"),
    ("hist_8yr", 2, "hist_8yr"),
]


def fd_counterfactual(state: AppState):
    """Lifetime tax and end corpus with everything in fixed deposits.

    Interest accrues on each purse's own balance and is taxed in that purse's
    hands -- the HUF has its own slab table and no other income, which is
    exactly why leaving it out of the benchmark mattered.

    Draws principal from the personal purse first and the HUF only once that
    is exhausted, mirroring the greedy order the engine uses when
    `huf_ration_k` is zero. A different draw order changes the split between
    the two tax tables, not the conclusion.
    """
    corpus_p = state.total_allocation()
    corpus_h = 0.0
    windfalls = {}
    for w in state.windfalls:
        if getattr(w, "target", "huf") == "huf":
            windfalls[w.year] = windfalls.get(w.year, 0.0) + w.amount

    total_tax = 0.0
    for fy in state.fy_range():
        corpus_h += windfalls.get(fy, 0.0)
        months = months_in_fy(state.investment_date, fy)
        rate = state.get_fd_rate(fy) * months / 12.0

        interest_p, interest_h = corpus_p * rate, corpus_h * rate
        corpus_p += interest_p
        corpus_h += interest_h

        assess_p = state.personal_income.assessable(fy, months, "individual")
        assess_h = state.huf_income.assessable(fy, months, "huf")
        dc_p = state.get_debt_tax_chunk(fy, "individual")
        dc_h = state.get_debt_tax_chunk(fy, "huf")
        tax_p = compute_slab_tax(assess_p + interest_p, dc_p, "individual") if dc_p else 0.0
        tax_h = compute_slab_tax(assess_h + interest_h, dc_h, "huf") if dc_h else 0.0
        total_tax += tax_p + tax_h

        other_cash = (state.personal_income.taxable(fy)
                      + state.personal_income.non_taxable(fy)) * months / 12.0
        need = state.annual_requirements.get(fy, 0.0)
        shortfall = need - (other_cash - tax_p - tax_h)

        # Interest already sits in the corpus, so any shortfall simply comes
        # out of principal. Personal first, HUF only when it runs dry.
        if shortfall > 0:
            take_p = min(corpus_p, shortfall)
            corpus_p -= take_p
            corpus_h -= min(corpus_h, shortfall - take_p)
        else:
            corpus_p -= shortfall            # surplus is reinvested

    return total_tax, corpus_p + corpus_h


def main() -> None:
    print(f"{'scenario':<12}{'FD tax':>10}{'FD corpus':>11}"
          f"{'frozen':>9}{'SWP tax':>9}{'SWP corpus':>12}{'reduction':>11}")
    for proj, idx, label in SCENARIOS:
        clear_plan_cache()
        state = AppState.from_dict(PROJECTS[proj]["scenarios"][idx])
        fd_tax, fd_corpus = fd_counterfactual(state)

        clear_plan_cache()
        live = AppState.from_dict(PROJECTS[proj]["scenarios"][idx])
        _pm, py, _hm, _hy, _s = project_plan(live)
        swp_tax = sum(y.tax_personal + getattr(y, "tax_huf", 0.0) for y in py)
        frozen = sum(y.fd_tax_benchmark for y in py)
        last = py[-1]
        swp_corpus = (last.corpus_debt_personal + last.corpus_equity_personal
                      + last.corpus_other_personal + last.corpus_debt_huf
                      + last.corpus_equity_huf + last.corpus_other_huf)
        cut = 100.0 * (1 - swp_tax / fd_tax) if fd_tax > 0 else float("nan")
        print(f"{label:<12}{fd_tax:>10.2f}{fd_corpus:>11.1f}{frozen:>9.2f}"
              f"{swp_tax:>9.2f}{swp_corpus:>12.1f}{cut:>10.2f}%")


if __name__ == "__main__":
    main()
