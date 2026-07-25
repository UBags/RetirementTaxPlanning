#!/usr/bin/env python3
"""
Issue #2 — allocate_funds: a single-fund universe yields an empty chunk.

optimise_with_relaxation() guarded `if len(df_filtered) < 2: continue`, so a
one-fund universe was skipped at every relaxation step, the aim solve reported
failure, and the chunk was left with target_weights summing to 0.0 instead
of 1.0.

A single-fund universe is a valid, trivially-solvable problem: the MILP either
returns w=[1.0] (when max_per_fund permits it and the fund clears the return
and risk floors) or correctly reports infeasible. _solve() already handles
n == 1 -- its spread calculations guard with `if n > 1`.

Fix: only skip a genuinely empty universe.

Safe to run twice. Preserves existing line endings.
"""
import shutil, sys
from pathlib import Path

TARGET = Path("allocate_funds.py")
OLD = '''        df_filtered = apply_fund_filters(df, cur_std, cur_dd)
        if len(df_filtered) < 2:
            print(f"    Only {len(df_filtered)} fund(s) pass filters — skipping.")
            continue'''
NEW = '''        df_filtered = apply_fund_filters(df, cur_std, cur_dd)
        if len(df_filtered) < 1:
            print("    No funds pass filters — skipping.")
            continue
        # A single-fund universe is a valid, trivially-solvable problem: the
        # MILP either returns w=[1.0] (when max_per_fund permits it and the
        # fund clears the return/risk floors) or reports infeasible. It was
        # previously skipped by a `< 2` guard, which left the chunk empty with
        # target_weights summing to 0.0 instead of 1.0.'''

def main() -> int:
    if not TARGET.exists():
        print(f"ERROR: {TARGET} not found. Run from the project root.")
        return 1
    raw = TARGET.read_bytes()
    nl = "\r\n" if b"\r\n" in raw else "\n"
    text = raw.decode("utf-8").replace("\r\n", "\n")
    if "No funds pass filters" in text:
        print("Already patched — nothing to do.")
        return 0
    if OLD not in text:
        print("ERROR: the `len(df_filtered) < 2` guard does not match.")
        return 1
    text = text.replace(OLD, NEW, 1)
    shutil.copy2(TARGET, TARGET.with_suffix(".py.bak"))
    TARGET.write_bytes((text.replace("\n", nl) if nl != "\n" else text).encode("utf-8"))
    print(f"Patched {TARGET} — single-fund universes are now solved")
    return 0

if __name__ == "__main__":
    sys.exit(main())
