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
# Currently empty — issues #1, #2 and #3 are all fixed.
KNOWN_BUGS: dict[str, str] = {}


def pytest_collection_modifyitems(config, items):
    import pytest as _pytest

    for item in items:
        reason = KNOWN_BUGS.get(item.originalname or item.name)
        if reason:
            item.add_marker(_pytest.mark.xfail(strict=True, reason=reason))
