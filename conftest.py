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
# Tests failing because of a filed, unfixed bug get xfail(strict=True) here, so
# CI stays green for regressions while the bug stays loudly pinned: fixing it
# makes the test pass unexpectedly and the build fails until the entry is
# removed. Applied here rather than as decorators so test_swp_engine.py keeps
# working as a standalone script without a pytest dependency.
#
# Issues #1, #2 and #3 are fixed. #4 is filed and open.
KNOWN_BUGS: dict[str, str] = {
    "test_aggregate_matches_per_fund_huf": (
        "Issue #4 — HUF aggregate buckets drift from the per-fund buckets "
        "within each FY and snap back at the April re-sync. Same defect family "
        "as #1-#3: two mutable representations of the same money updated by "
        "different rules. Retired for good by making the aggregates a computed "
        "property over the per-fund buckets (Phase 3.1)."
    ),
}


def pytest_collection_modifyitems(config, items):
    import pytest as _pytest

    for item in items:
        reason = KNOWN_BUGS.get(item.originalname or item.name)
        if reason:
            item.add_marker(_pytest.mark.xfail(strict=True, reason=reason))
