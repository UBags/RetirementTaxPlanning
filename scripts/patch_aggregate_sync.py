#!/usr/bin/env python3
"""
Issue #3 — aggregate and per-fund buckets diverge within each FY.

The aggregate category buckets grow at a blended per-category rate while the
per-fund buckets grow at each fund's own CAGR, so the two views of the same
money drifted apart between annual syncs (~0.24L/month in scenario 0; 190 of
349 months beyond a 0.1% tolerance) and snapped back each April.

The aggregates are already DERIVED state: every reported principal and gain
comes from the per-fund buckets, and the aggregate redeems are explicitly
labelled "Keep aggregate buckets in sync". They simply were not re-derived
often enough.

Fix: re-derive them every month instead of only in April. The sync already
sits in the right place -- after the NAV growth step and before the corpus
snapshot is read -- so this is a one-condition change.

Diverging months: 190 -> 0. Projected values are unchanged to six figures
(terminal corpus, depletion year, cumulative tax identical across all four
scenarios); intra-year monthly corpus figures now match the per-fund truth.

Safe to run twice. Preserves existing line endings.
"""
import shutil, sys
from pathlib import Path

TARGET = Path("engine.py")
OLD = """            # Skip months where _rebalance_portfolio has already synced the agg.
            if cm == 4 and fy >= 1:"""
NEW = """            # Skip months where _rebalance_portfolio has already synced the agg.
            #
            # Re-derived EVERY month, not just in April. The aggregates are
            # derived state -- all reported principal/gain come from the
            # per-fund buckets, and the aggregate redeems below are explicitly
            # labelled "keep aggregate buckets in sync". But the aggregates
            # grow at a blended per-category rate while the per-fund buckets
            # grow at each fund's own CAGR, so between annual syncs the two
            # views drifted apart (~0.24L/month in scenario 0, 190 of 349
            # months beyond a 0.1% tolerance) and snapped back each April.
            # Syncing monthly bounds the divergence to zero.
            if True:"""

def main() -> int:
    if not TARGET.exists():
        print(f"ERROR: {TARGET} not found. Run from the project root.")
        return 1
    raw = TARGET.read_bytes()
    nl = "\r\n" if b"\r\n" in raw else "\n"
    text = raw.decode("utf-8").replace("\r\n", "\n")
    if "Re-derived EVERY month" in text:
        print("Already patched — nothing to do.")
        return 0
    if OLD not in text:
        print("ERROR: the April aggregate-sync guard does not match.")
        print("       Has patch_conservation.py been applied first?")
        return 1
    text = text.replace(OLD, NEW, 1)
    shutil.copy2(TARGET, TARGET.with_suffix(".py.bak"))
    TARGET.write_bytes((text.replace("\n", nl) if nl != "\n" else text).encode("utf-8"))
    print(f"Patched {TARGET} — aggregates re-derived every month")
    return 0

if __name__ == "__main__":
    sys.exit(main())
