# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
FD Interest Rate Chunks dialog.

The first test in this suite to drive a Qt widget rather than merely import
it. HANDOVER 12 records that test_smoke.py imports every dialog module and
exercises nothing, and that pytest-qt has been installed and unused since
Phase 4 was written down.

What is worth testing here is not the painting but the DECISION: a rate the
user has saved is indistinguishable from one typed a minute ago, so the
dialog must never migrate an existing plan to the default ladder on its own.
"Reset to defaults" is the only route, and it has to actually work.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="Qt not available")

from swp_planner.config import chunk_defaults as cd  # noqa: E402
from swp_planner.core.models import AppState, FDRateChunk  # noqa: E402


@pytest.fixture
def fd_dialog(qtbot):
    """A dialog over a plan carrying a user-set flat rate."""
    from swp_planner.gui.dialogs import FDRateChunksDialog
    state = AppState(fd_rate_chunks=[FDRateChunk(1, 30, 0.07)], fd_rate=0.07)
    dlg = FDRateChunksDialog(state)
    qtbot.addWidget(dlg)
    return state, dlg


def test_opening_the_dialog_does_not_touch_the_users_rates(fd_dialog):
    """Merely looking at the plan must not rewrite it."""
    state, dlg = fd_dialog
    assert [(c.year_from, c.year_to, c.fd_rate) for c in dlg.chunks] == [(1, 30, 0.07)]
    assert state.fd_rate_chunks[0].fd_rate == pytest.approx(0.07)


def test_reset_replaces_the_users_rates_with_the_measured_ladder(fd_dialog):
    _state, dlg = fd_dialog
    dlg._reset_to_defaults()
    expected = cd.default_fd_benchmark_chunks(dlg.horizon)
    assert [(c.year_from, c.year_to, c.fd_rate) for c in dlg.chunks] == expected


def test_reset_alone_does_not_save(fd_dialog):
    """Cancel after a reset must leave the plan as it was."""
    state, dlg = fd_dialog
    dlg._reset_to_defaults()
    assert state.fd_rate_chunks[0].fd_rate == pytest.approx(0.07), (
        "the dialog wrote to the state before the user accepted")


def test_save_after_reset_writes_the_ladder_through(fd_dialog):
    state, dlg = fd_dialog
    dlg._reset_to_defaults()
    dlg._save()
    expected = cd.default_fd_benchmark_chunks(state.horizon)
    assert [(c.year_from, c.year_to, c.fd_rate)
            for c in state.fd_rate_chunks] == expected
    # the legacy scalar tracks the first chunk
    assert state.fd_rate == pytest.approx(expected[0][2])


def test_added_chunk_on_an_empty_table_takes_the_anchored_rate(qtbot):
    """Not a hardcoded 0.07 from nowhere."""
    from swp_planner.gui.dialogs import FDRateChunksDialog
    state = AppState(fd_rate_chunks=[])
    dlg = FDRateChunksDialog(state)
    qtbot.addWidget(dlg)
    dlg.chunks = []
    dlg._add()
    assert dlg.chunks[0].fd_rate == pytest.approx(
        cd.default_fd_benchmark_chunks(state.horizon)[0][2])


def test_the_dialog_follows_the_plan_horizon(qtbot):
    from swp_planner.gui.dialogs import FDRateChunksDialog
    state = AppState(horizon_years=15, fd_rate_chunks=[])
    dlg = FDRateChunksDialog(state)
    qtbot.addWidget(dlg)
    assert dlg.horizon == 15
    dlg._reset_to_defaults()
    covered = []
    for c in dlg.chunks:
        covered.extend(range(c.year_from, c.year_to + 1))
    assert covered == list(range(1, 16))
