"""
Root pytest configuration.

  1. Put the project root on sys.path so `import engine` works from tests/.
  2. Force headless Qt before PySide6 is imported anywhere, so CI (and any
     SSH session) can import the dialog modules without an X server.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def project_root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def config_path(project_root: Path) -> Path:
    return project_root / "RetirementTaxPlanning.configuration"


# ── Known-bug registry ───────────────────────────────────────────────────────
# Tests that fail because of a filed, unfixed bug. Marked xfail(strict=True) so
# CI stays green for regressions while these stay loudly pinned: when someone
# fixes the bug the test passes unexpectedly and the build fails, forcing the
# marker to be removed.
#
# Applied here rather than as decorators so test_swp_engine.py keeps working as
# a standalone script without a pytest dependency.
KNOWN_BUGS = {
    "test_glide_path_zero_spread": (
        "glide_path.build_glide_path does `spread = max(2, spread_years)`, so "
        "spread_years=0 still produces a 2-year interpolation window instead of "
        "a hard cut at the chunk boundary. Year 15 comes out 50/50 rather than "
        "100% equity. spread_years of 0, 1 and 2 all yield identical schedules."
    ),
    "test_optimizer_single_fund_universe": (
        "allocate_funds.py:962 guards `if len(df_filtered) < 2: continue`, so a "
        "one-fund universe is skipped at every relaxation step and the chunk is "
        "left empty (weights sum to 0.0 instead of 1.0). A single-fund universe "
        "has the trivially correct answer w=[1.0] and should be special-cased."
    ),
}


def pytest_collection_modifyitems(config, items):
    import pytest as _pytest

    for item in items:
        reason = KNOWN_BUGS.get(item.originalname or item.name)
        if reason:
            item.add_marker(_pytest.mark.xfail(strict=True, reason=reason))
