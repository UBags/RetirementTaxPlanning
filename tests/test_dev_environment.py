# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""
Guards on the development environment itself.

Two failures of this kind have already cost time, and neither was a defect in
the application:

  1. `pytest-xdist` was required by the documented workflow (HANDOVER §0) and
     is credited in §9.10 with exposing the `is_liquid_fund` defect fixed in
     a965e4a -- but it was declared in no requirements file and exercised by no
     CI job. It existed only as an ad-hoc install on one machine, so a fresh
     clone could not reproduce the documented baseline.

  2. A user-level `~/.local/bin/pytest` shadowed the venv's on PATH. It
     rejected `-n 4`, which is the only reason anyone noticed; had it accepted
     the flag it would have run under a system interpreter and reported green
     with every PySide6 test silently skipped. See §9.12.

The first is testable and is tested here. The second is not: by the time this
file is collected, the interpreter has already been chosen.
"""
from __future__ import annotations

import importlib.metadata as md
import re

import pytest


def _declared_distributions(path) -> list[str]:
    """Distribution names declared in a requirements file.

    Skips blanks, comments and `-r`/`-e` directives. Strips version
    specifiers, extras and environment markers, leaving the bare name that
    importlib.metadata expects.
    """
    names: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = re.split(r"[<>=!~\[;]", line, maxsplit=1)[0].strip()
        if name:
            names.append(name)
    return names


def test_declared_dev_dependencies_are_installed(project_root):
    """Everything requirements-dev.txt promises must actually be importable.

    This is the guard that was missing. `pytest -n 4` is the documented
    command; xdist was not declared anywhere; and nothing noticed until the
    command failed on a machine where it happened not to be installed.
    """
    req = project_root / "requirements-dev.txt"
    assert req.is_file(), "requirements-dev.txt is missing from the checkout."

    declared = _declared_distributions(req)
    assert declared, "requirements-dev.txt declares nothing -- parser broken?"

    missing = []
    for dist in declared:
        try:
            md.version(dist)
        except md.PackageNotFoundError:
            missing.append(dist)

    assert not missing, (
        "requirements-dev.txt declares packages that are not installed in this "
        f"environment: {', '.join(missing)}.\n"
        "The suite you just ran is therefore not the suite the project "
        "describes. Install with:\n"
        "    python3 -m pip install -r requirements-dev.txt\n"
        "If a package is genuinely no longer needed, remove it from "
        "requirements-dev.txt rather than leaving the file lying."
    )


def test_ci_runs_the_full_suite_under_xdist(project_root):
    """CI must exercise the order-dependence guard, not just the local machine.

    HANDOVER §9.10: sequential runs hide order-dependent tests. Running the
    full suite under `-n` is what makes that guard real rather than a habit one
    person happens to have.
    """
    ci = project_root / ".github" / "workflows" / "ci.yml"
    if not ci.is_file():
        pytest.skip("no CI workflow in this checkout")

    runs = [
        ln.strip()
        for ln in ci.read_text(encoding="utf-8").splitlines()
        if "run:" in ln and "pytest" in ln and "test_smoke.py" not in ln
    ]
    assert runs, (
        "ci.yml has no full-suite pytest step. Expected a `run: pytest ...` "
        "line that is not the smoke-test step."
    )
    assert any(" -n " in ln for ln in runs), (
        "CI runs the full suite sequentially. Sequential runs hide "
        "order-dependent tests (HANDOVER §9.10 -- this is how a965e4a was "
        "found). Add `-n 4` to the full-suite step, or delete this test and "
        "record the decision in the handover.\n"
        f"  found: {runs}"
    )
