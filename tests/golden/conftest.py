"""
Fixtures for the golden-master suite.

The fixture corpus lives in tests/golden/fixtures/ — frozen copies of the saved
projects. Uddipan_project.swp.json carries four scenarios covering 1, 2, 3 and 4
allocation chunks respectively, so glide-path interpolation is exercised at
every arity.

These are copies on purpose. The originals under Uddipan/ are the owner's live
working files; the GUI rewrites them on save, and pointing the suite at them
made every plan edit look like an engine regression.

Snapshots live in tests/golden/snapshots/ and compare with a tight tolerance:
Engine.run() is deterministic, so movement is a real behaviour change.

Regenerate deliberately:

    pytest tests/golden --regen-golden

and review the diff before committing. A snapshot regenerated without review
is worth nothing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"

# Frozen copies of the saved projects. Deliberately NOT Uddipan/ — that is the
# owner's live working file and the GUI rewrites it on every save, which would
# make this suite red on plan edits rather than on code changes.
FIXTURE_DIR = Path(__file__).parent / "fixtures"

SCENARIOS = [
    ("main", 0, "s0_1chunk"),
    ("main", 1, "s1_2chunk"),
    ("main", 2, "s2_3chunk"),
    ("main", 3, "s3_4chunk"),
    ("hist_8yr", 2, "hist_8yr"),
]

# Config keys that change engine output. Recorded in every snapshot header so a
# config edit gives a clear message instead of a baffling numeric diff.
CONFIG_FINGERPRINT_KEYS = [
    "cess_rate", "stcg_holding_months", "exit_load_fraction",
    "drift_cap_personal", "drift_cap_huf", "weight_drift_threshold",
    "max_simulation_years", "swp_start_month", "smart_withdrawal_start_month",
    "fallback_equity_ltcg_rate", "fallback_other_ltcg_rate",
    "fallback_stcg_rate", "fallback_debt_top_rate",
]


def pytest_addoption(parser):
    parser.addoption(
        "--regen-golden", action="store_true", default=False,
        help="Rewrite golden snapshots instead of comparing against them.",
    )


@pytest.fixture(scope="session")
def regen_golden(request) -> bool:
    return request.config.getoption("--regen-golden")


@pytest.fixture(scope="session")
def snapshot_dir() -> Path:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    return SNAPSHOT_DIR


@pytest.fixture(scope="session")
def config_fingerprint() -> str:
    from configuration import config
    return ";".join(f"{k}={config.get(k, '<missing>')}"
                    for k in CONFIG_FINGERPRINT_KEYS)


def load_scenario(project_root: Path, rel_path: str, index: int):
    """Load one scenario from the frozen fixture corpus.

    *project_root* is accepted for signature stability but deliberately unused:
    fixtures resolve against FIXTURE_DIR so that editing a plan in the GUI can
    never move a golden snapshot.
    """
    from models import AppState

    from golden_projects import PROJECTS

    data = PROJECTS.get(rel_path)
    if data is None:
        pytest.skip(f"no golden project named {rel_path!r} in golden_projects.py")
    scenarios = data.get("scenarios")
    if not scenarios:
        pytest.skip(f"{rel_path} has no 'scenarios' key")
    if index >= len(scenarios):
        pytest.skip(f"{rel_path} has only {len(scenarios)} scenarios")

    return AppState.from_dict(scenarios[index])


@pytest.fixture(params=SCENARIOS, ids=[s[2] for s in SCENARIOS])
def scenario(request, project_root):
    rel_path, index, label = request.param
    return label, load_scenario(project_root, rel_path, index)
