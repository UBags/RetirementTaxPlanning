#!/usr/bin/env python3
"""True turnover at each allocation-chunk boundary.

METHODOLOGY §15.2 step 1: a boundary's window must be sized from the actual
fund-list difference, not from "everything changes". §14.2 measured one
boundary (s3_4chunk FY17) that costs nothing because its two allocations
overlap heavily; a scheduler that opens a window at every boundary would
churn for no reason.

Weights are taken the way `engine.py`'s rebalance takes them --
`f.allocation / sum(allocation over active funds)` -- NOT from the stored
`target_weights` dict, which is the optimiser's output and is not what the
rebalance reads.

turnover_fraction = sum_f max(0, w_old_f - w_new_f)
                  = 0.5 * sum_f |w_old_f - w_new_f|

which is the share of corpus that must actually be sold and rebought.

Reports per boundary so the scheduler's "open no window" test (§14.2) has a
number behind it.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.golden.golden_projects import PROJECTS  # noqa: E402

SCENARIOS = [
    ("main", 0, "s0_1chunk"),
    ("main", 1, "s1_2chunk"),
    ("main", 2, "s2_3chunk"),
    ("main", 3, "s3_4chunk"),
    ("hist_8yr", 2, "hist_8yr"),
]


def weights(chunk) -> dict:
    """Active-fund weights exactly as the engine's rebalance computes them."""
    active = [f for f in chunk["funds"] if f.get("allocation", 0) > 0]
    total = sum(f["allocation"] for f in active)
    if total <= 0:
        return {}
    out = {}
    for f in active:
        out[f["name"]] = out.get(f["name"], 0.0) + f["allocation"] / total
    return out


def main() -> None:
    print(f"{'scenario':<12} {'bdy':>4} {'turnover':>9} {'retiring':>9} "
          f"{'entering':>9}  {'verdict'}")
    print("-" * 66)
    for proj, idx, label in SCENARIOS:
        sc = PROJECTS[proj]["scenarios"][idx]
        chunks = sc.get("allocation_chunks", [])
        if len(chunks) <= 1:
            print(f"{label:<12} {'--':>4} {'--':>9} {'--':>9} {'--':>9}  "
                  f"one chunk, no boundary")
            continue
        for prev, nxt in zip(chunks, chunks[1:]):
            w_old, w_new = weights(prev), weights(nxt)
            names = set(w_old) | set(w_new)
            sell = sum(max(0.0, w_old.get(n, 0.0) - w_new.get(n, 0.0))
                       for n in names)
            retiring = sum(w_old[n] for n in set(w_old) - set(w_new))
            entering = sum(w_new[n] for n in set(w_new) - set(w_old))
            verdict = "NO WINDOW" if sell < 0.01 else "window needed"
            print(f"{label:<12} {nxt['year_from']:>4} {sell:>8.1%} "
                  f"{retiring:>8.1%} {entering:>8.1%}  {verdict}")


if __name__ == "__main__":
    main()
