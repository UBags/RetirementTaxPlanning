#!/usr/bin/env python3
"""
One-off: move UNTRACKED local files into the data/ + outputs/ layout.

`git pull` moves every tracked file. It cannot move files git does not track:
inputs, caches, logs and per-user output folders stay where they were. This
script relocates them to where the code now looks (src/swp_planner/paths.py).

    python3 scripts/migrate_local_layout.py            # dry run: print the plan
    python3 scripts/migrate_local_layout.py --apply    # do it

Guarantees:
  * never overwrites: if the destination exists, the item is skipped and
    reported, and you resolve it by hand;
  * never touches tracked files (each source is checked with git ls-files);
  * deletes nothing except __pycache__ folders and the directories left empty
    by the moves.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (source relative to ROOT, destination relative to ROOT)
MOVES: list[tuple[str, str]] = [
    # fund universe
    ("Fund Details.csv",           "data/funds/Fund Details.csv"),
    ("Fund_Metrics_Output.csv",    "data/funds/Fund_Metrics_Output.csv"),
    ("Fund_Metrics_Errors.csv",    "data/funds/Fund_Metrics_Errors.csv"),
    ("amfi_map.csv",               "data/funds/amfi_map.csv"),
    ("rolling_data.json",          "data/funds/rolling_data.json"),
    ("Schemes_and_Funds",          "data/funds/Schemes_and_Funds"),
    # NAV corpus
    ("nav_corpus.json",            "data/nav/nav_corpus.json"),
    (".nav_corpus_cache.json",     "data/nav/.nav_corpus_cache.json"),
    ("nav_sample.csv",             "data/nav/nav_sample.csv"),
    # Monte Carlo caches
    ("mc_nifty50_nav.csv",         "data/cache/mc_nifty50_nav.csv"),
    ("mc_debt_index.csv",          "data/cache/mc_debt_index.csv"),
    # logs
    ("fund_analyser.log",          "data/logs/fund_analyser.log"),
    ("swp_debug.log",              "data/logs/swp_debug.log"),
    # hand-maintained inputs no module writes
    ("30_Yrs_Forecast_Data.xlsx",  "data/inputs/30_Yrs_Forecast_Data.xlsx"),
    ("redemptions.csv",            "data/inputs/redemptions.csv"),
    # documents
    ("Database Design Preview.odt", "docs/Database Design Preview.odt"),
    # untracked files left behind in moved package folders
    ("data_synthesizer/production_time_series_synthesizer_dashboard.html",
     "src/swp_planner/data_synthesizer/production_time_series_synthesizer_dashboard.html"),
    # superseded code: into the already-ignored older_versions/
    ("test/portfolio_allocator.py",    "older_versions/legacy_test/portfolio_allocator.py"),
    ("test/Optimized_Portfolios.xlsx", "older_versions/legacy_test/Optimized_Portfolios.xlsx"),
    ("allocate_funds_v0.9.py",         "older_versions/allocate_funds_v0.9.root_copy.py"),
]

# Folders the restructure emptied of tracked files. Removed only if nothing but
# __pycache__ (or nothing at all) is left in them after the moves.
VACATED_DIRS = ["fund_filters", "ingest", "lookup", "data_synthesizer", "test"]

# Root-level folders that are part of the repo, never per-user output.
KNOWN_ROOT_DIRS = {
    ".git", ".github", ".venv", "venv", "env", ".idea", ".vscode", ".claude",
    ".codebuddy", ".gemini", ".kiro", ".qoder", ".code-review-graph",
    "archive", "config", "data", "db", "docs", "older_versions", "outputs",
    "scripts", "src", "tests", "Code packaged into MDs for LLMs",
    *VACATED_DIRS, "Schemes_and_Funds",
}

# A root folder holding any of these is a per-user GUI output folder.
USER_DIR_MARKERS = ("*.swp.json", "allocation_summary.csv", "Fund_Metrics_Output.csv",
                    "*_Personal_Monthly.csv")


def is_tracked(rel: str) -> bool:
    out = subprocess.run(["git", "ls-files", "--", rel], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    return bool(out.strip())


def user_output_dirs() -> list[str]:
    found = []
    for d in sorted(p for p in ROOT.iterdir() if p.is_dir()):
        if d.name in KNOWN_ROOT_DIRS or d.name.startswith("."):
            continue
        if any(next(d.glob(m), None) for m in USER_DIR_MARKERS):
            found.append(d.name)
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--apply", action="store_true", help="perform the moves")
    a = ap.parse_args()

    plan = list(MOVES) + [(u, f"outputs/{u}") for u in user_output_dirs()]
    moved = skipped = 0
    planned: set[Path] = set()      # sources that are (or would be) moved
    print(("APPLYING" if a.apply else "DRY RUN") + f" in {ROOT}\n")

    for src_rel, dst_rel in plan:
        src, dst = ROOT / src_rel, ROOT / dst_rel
        if not src.exists():
            continue
        if is_tracked(src_rel):
            print(f"  SKIP (tracked by git)  {src_rel}")
            skipped += 1
            continue
        if dst.exists():
            print(f"  SKIP (exists)          {src_rel}  ->  {dst_rel}")
            skipped += 1
            continue
        print(f"  move                   {src_rel}  ->  {dst_rel}")
        planned.add(src)
        if a.apply:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
        moved += 1

    # Stale bytecode: the root and vacated folders only hold .pyc for modules
    # that no longer live there.
    stale = [ROOT / "__pycache__"] + [ROOT / d / "__pycache__" for d in VACATED_DIRS]
    for pc in stale:
        if pc.is_dir():
            print(f"  delete stale bytecode  {pc.relative_to(ROOT)}/")
            if a.apply:
                shutil.rmtree(pc)

    for d in VACATED_DIRS:
        p = ROOT / d
        if p.is_dir():
            leftover = [c for c in p.iterdir()
                        if c.name != "__pycache__" and c not in planned]
            if leftover:
                names = ", ".join(sorted(c.name for c in leftover))
                print(f"  KEEP (not empty)       {d}/  [{names}]")
            else:
                print(f"  remove empty folder    {d}/")
                if a.apply:
                    shutil.rmtree(p)

    print(f"\n{moved} to move, {skipped} skipped.")
    if not a.apply and moved:
        print("Re-run with --apply to perform the moves.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
