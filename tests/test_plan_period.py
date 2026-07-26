# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
Plan Period: the start month and the length of the plan.

Both were unreachable. horizon_years existed from A1, round-tripped, was
clamped into a band by A2b-ii and honoured by the engine, the glide path and
every dialog after A3-i -- and nothing could change it from 30.

The two decisions worth pinning here are not the widget layout:

  * The dialog must NOT clamp the start month to the current financial year.
    The live plan starts 1 March 2026, which is FY2025-26, while today is
    FY2026-27. A control that constrained to the current FY would silently
    move an already-running plan and shift every projection.

  * Lengthening a plan past the coverage of its chunk lists produces a
    projection that is WRONG, not merely incomplete: the tax-band lookups
    return None, so those years are silently tax-free, HUF withdrawals stop
    and the FD rate falls back to the legacy scalar. Latent while the horizon
    was fixed at 30; reachable the moment it can be chosen.
"""
from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("PySide6", reason="Qt not available")

from models import (  # noqa: E402
    AppState,
    FDRateChunk,
    HUFWithdrawalChunk,
    ReturnChunk,
    TaxChunk,
    TaxSlab,
)
from pro_rated_utils import financial_year_of  # noqa: E402


def _short_plan(horizon: int = 30) -> AppState:
    """A plan whose chunk lists stop well before its horizon."""
    return AppState(
        horizon_years=horizon,
        investment_date=date(2026, 3, 1),
        individual_debt_chunks=[TaxChunk(1, 20, 12.0, [TaxSlab(0, 5, 0.05)])],
        return_chunks=[ReturnChunk(1, 15, 0.07)],
        fd_rate_chunks=[FDRateChunk(1, 30, 0.07)],
        huf_withdrawal_chunks=[HUFWithdrawalChunk(1, 10, 5.0)],
    )


# ── coverage detection and repair (no Qt needed) ─────────────────────────────

def test_short_chunk_lists_are_detected():
    short = dict((label, last) for _a, label, last in
                 _short_plan().chunks_short_of_horizon())
    assert short["Individual debt tax bands"] == 20
    assert short["Portfolio return rates"] == 15
    assert short["HUF withdrawal schedule"] == 10
    assert "FD benchmark rates" not in short, "FY1-30 covers a 30-year plan"


def test_empty_chunk_lists_are_not_reported_as_short():
    """Absent is a decision already made; there is nothing to extend."""
    s = AppState(horizon_years=30, split_chunks=[], windfalls=[])
    assert all(label != "Portfolio return rates"
               for _a, label, _l in s.chunks_short_of_horizon())


def test_extending_reports_exactly_what_it_changed():
    s = _short_plan()
    changed = dict((label, (old, new)) for label, old, new in
                   s.extend_chunks_to_horizon())
    assert changed["Individual debt tax bands"] == (20, 30)
    assert s.chunks_short_of_horizon() == []
    assert s.individual_debt_chunks[-1].year_to == 30


def test_extending_stretches_the_last_chunk_rather_than_inventing_one():
    s = _short_plan()
    before = len(s.individual_debt_chunks)
    s.extend_chunks_to_horizon()
    assert len(s.individual_debt_chunks) == before


# ── the dialog ───────────────────────────────────────────────────────────────

@pytest.fixture
def dlg(qtbot):
    from dialogs import PlanPeriodDialog
    state = _short_plan()
    d = PlanPeriodDialog(state)
    qtbot.addWidget(d)
    return state, d


def test_a_start_in_a_previous_financial_year_is_kept_and_labelled(dlg):
    """The live plan's own situation. Clamping it would move the plan."""
    state, d = dlg
    assert d._chosen_start() == date(2026, 3, 1)
    assert financial_year_of(d._chosen_start()) == 2025
    assert "previous" in d.lbl_fy.text().lower(), d.lbl_fy.text()


def test_opening_and_cancelling_changes_nothing(dlg):
    state, d = dlg
    d.reject()
    assert state.investment_date == date(2026, 3, 1)
    assert state.horizon_years == 30
    assert state.individual_debt_chunks[-1].year_to == 20


def test_the_spinbox_is_bounded_by_the_configured_band(dlg):
    from configuration import config
    _state, d = dlg
    assert d.spin.minimum() == int(config.min_simulation_years)
    assert d.spin.maximum() == int(config.max_simulation_years)


def test_the_end_date_follows_the_length(dlg):
    _state, d = dlg
    d.spin.setValue(15)
    # start FY2025-26 + 15 -> ends 31 March 2040
    assert "2040" in d.lbl_end.text(), d.lbl_end.text()


def test_a_short_first_year_is_announced(dlg):
    """A March start gives FY1 one month; the user should be told."""
    _state, d = dlg
    assert "1 month" in d.lbl_first.text(), d.lbl_first.text()


def test_saving_applies_both_choices(qtbot):
    from dialogs import PlanPeriodDialog
    state = _short_plan()
    d = PlanPeriodDialog(state)
    qtbot.addWidget(d)
    d.spin.setValue(18)
    d.chk_extend.setChecked(False)
    d._save()
    assert state.horizon_years == 18
    assert state.horizon == 18


def test_saving_with_extend_repairs_the_short_lists(qtbot):
    from dialogs import PlanPeriodDialog
    state = _short_plan()
    d = PlanPeriodDialog(state)
    qtbot.addWidget(d)
    d.chk_extend.setChecked(True)
    d._save()
    assert state.chunks_short_of_horizon() == []
    assert d.extended, "the dialog must report what it changed"


def test_declining_to_extend_leaves_the_plan_short(qtbot):
    """Not silently repaired: the user said no."""
    from dialogs import PlanPeriodDialog
    state = _short_plan()
    d = PlanPeriodDialog(state)
    qtbot.addWidget(d)
    d.chk_extend.setChecked(False)
    d._save()
    assert state.chunks_short_of_horizon() != []
    assert d.extended == []


def test_a_fully_covered_plan_offers_nothing_to_extend(qtbot):
    from dialogs import PlanPeriodDialog
    state = AppState(horizon_years=30,
                     return_chunks=[ReturnChunk(1, 30, 0.07)])
    d = PlanPeriodDialog(state)
    qtbot.addWidget(d)
    assert not d.chk_extend.isEnabled()
    assert "\u2713" in d.lbl_cover.text()
