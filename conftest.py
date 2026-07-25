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
