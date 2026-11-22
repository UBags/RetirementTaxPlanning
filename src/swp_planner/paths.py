# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
Single source of truth for on-disk locations.

Every default path in the application resolves through this module, so the
repository layout is described in exactly one place:

    <PROJECT_ROOT>/
      config/RetirementTaxPlanning.configuration   tracked
      db/                                          schema DDL, tracked
      data/                                        untracked (gitignored)
        funds/      Fund Details.csv, Fund_Metrics_Output.csv,
                    Fund_Metrics_Errors.csv, amfi_map.csv,
                    Schemes_and_Funds/
        nav/        nav_corpus.json, .nav_corpus_cache.json, nav_sample.csv
        cache/      mc_nifty50_nav.csv, mc_debt_index.csv
        inputs/     hand-maintained inputs not produced by any module
        logs/
        etmoney/, nsdl_active_*.xlsx, manifest.jsonl   (ingest pipeline)
      outputs/<user>/                              untracked, per-user GUI output

PROJECT_ROOT is derived from this file's location, which assumes the package
is used from a checkout (``pip install -e .`` or the root ``run.py`` shim).
A non-editable install would place it inside site-packages.
"""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
SRC_DIR = PACKAGE_DIR.parent
PROJECT_ROOT = SRC_DIR.parent

CONFIG_DIR = PROJECT_ROOT / "config"
CONFIG_FILENAME = "RetirementTaxPlanning.configuration"
DB_DIR = PROJECT_ROOT / "db"

DATA_DIR = PROJECT_ROOT / "data"
FUNDS_DIR = DATA_DIR / "funds"
NAV_DIR = DATA_DIR / "nav"
CACHE_DIR = DATA_DIR / "cache"
INPUTS_DIR = DATA_DIR / "inputs"
LOGS_DIR = DATA_DIR / "logs"

OUTPUTS_DIR = PROJECT_ROOT / "outputs"


def ensure_parent(path: "str | os.PathLike") -> Path:
    """Create *path*'s parent directory if missing; return *path* as a Path."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def module_command(module: str, *args: str) -> list[str]:
    """argv that runs *module* as ``python -m`` under the current interpreter."""
    import sys
    return [sys.executable, "-m", module, *args]


def child_env() -> dict[str, str]:
    """Environment for a child Python process that must import swp_planner.

    Prepends SRC_DIR to PYTHONPATH so ``-m swp_planner...`` works even when the
    parent was launched through the root run.py shim without an install.
    """
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (str(SRC_DIR) + os.pathsep + existing) if existing else str(SRC_DIR)
    return env
