#!/usr/bin/env python3
"""Per-FY tax and free tax-free headroom, both entities.

METHODOLOGY §20.3: this harness did all the work behind §14 and has been
rebuilt from scratch twice because it lived in a throwaway script each time.
It lives here now.

Usage:  python3 scripts/probe7.py <project> <scenario_index> [label]
        python3 scripts/probe7.py main 1 s1_2chunk

Reports, per FY: personal tax, the rebalancing subset of it, HUF tax, and the
free gain capacity of each entity BEFORE any gain is realised -- i.e. the
headroom the year starts with. Lifetime totals at the foot.

Headroom is a FLOW: it arrives each year and expires unused (Part I §2). The
lifetime total is therefore the budget a capacity-driven transition gets to
spend, and comparing it against the boundary's gain is the feasibility test
that decides whether zero is reachable at all.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "golden"))

from golden_projects import PROJECTS  # noqa: E402

from engine import Engine, free_gain_capacity  # noqa: E402
from models import AppState  # noqa: E402
from net_income_plan import project_plan  # noqa: E402


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(2)
    proj, idx = sys.argv[1], int(sys.argv[2])
    label = sys.argv[3] if len(sys.argv) > 3 else f"{proj}[{idx}]"

    state = AppState.from_dict(PROJECTS[proj]["scenarios"][idx])
    eng = Engine(state)
    _pm, p_yearly, _hm, h_yearly, _ = project_plan(state)

    by_fy_h = {y.year: y for y in h_yearly}

    print(f"=== {label} ===")
    print(f"{'FY':>3} {'tax_p':>8} {'rebal':>8} {'tax_h':>8} "
          f"{'free_p':>8} {'free_h':>8}")
    print("-" * 50)

    tot_p = tot_r = tot_h = tot_fp = tot_fh = 0.0
    for y in p_yearly:
        fy = y.year
        months = eng.months_in_fy(fy)
        assess_p = state.personal_income.assessable(fy, months, "individual")
        assess_h = state.huf_income.assessable(fy, months, "huf")
        free_p = free_gain_capacity(
            state.get_debt_tax_chunk(fy, "individual"), "individual", assess_p)
        free_h = free_gain_capacity(
            state.get_debt_tax_chunk(fy, "huf"), "huf", assess_h)
        yh = by_fy_h.get(fy)
        tax_h = getattr(yh, "tax_huf", 0.0) if yh else getattr(y, "tax_huf", 0.0)

        print(f"{fy:>3} {y.tax_personal:>8.2f} {y.rebalance_tax_paid:>8.2f} "
              f"{tax_h:>8.2f} {free_p:>8.2f} {free_h:>8.2f}")

        tot_p += y.tax_personal
        tot_r += y.rebalance_tax_paid
        tot_h += tax_h
        tot_fp += free_p
        tot_fh += free_h

    print("-" * 50)
    print(f"{'TOT':>3} {tot_p:>8.2f} {tot_r:>8.2f} {tot_h:>8.2f} "
          f"{tot_fp:>8.2f} {tot_fh:>8.2f}")
    print(f"\nlifetime tax        {tot_p + tot_h:>10.2f}")
    print(f"  of which rebalance{tot_r:>10.2f}")
    print(f"lifetime free headroom (p+h) {tot_fp + tot_fh:>9.2f}")


if __name__ == "__main__":
    main()
