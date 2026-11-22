# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
The GUI follows the plan horizon.

A1 made the horizon a variable and A2b-ii gave it a floor, but every dialog
and chart still sized itself to a literal 30. This pins the sweep that fixed
that, and -- more usefully -- pins the THREE DISTINCTIONS it turned on,
because a mechanical "replace 30" pass gets all three wrong:

  plan sites    follow state.horizon
  Monte Carlo   stay at MC_FIXED_HORIZON; the arrays really are 30-element
                until Phase D, and run_monte_carlo() refuses anything else
  layout        main.py's addSpacing(30) is PIXELS. Substituting it would
                silently reflow the toolbar and no test would notice.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="Qt not available")

from swp_planner.core.models import AppState, FDRateChunk, ReturnChunk  # noqa: E402
from swp_planner.paths import PACKAGE_DIR

HORIZONS = [30, 20, 12, 10]


def _state(horizon: int) -> AppState:
    return AppState(
        horizon_years=horizon,
        annual_requirements={1: 25.0},
        return_chunks=[ReturnChunk(1, horizon, 0.07)],
        fd_rate_chunks=[FDRateChunk(1, horizon, 0.07)],
    )


@pytest.mark.parametrize("horizon", HORIZONS)
def test_requirements_table_has_one_row_per_financial_year(qtbot, horizon):
    from swp_planner.gui.dialogs import RequirementsDialog
    dlg = RequirementsDialog(_state(horizon))
    qtbot.addWidget(dlg)
    assert dlg.table.rowCount() == horizon
    assert dlg.table.item(horizon - 1, 0).text() == str(horizon)


@pytest.mark.parametrize("horizon", HORIZONS)
def test_requirements_round_trip_at_any_horizon(qtbot, horizon):
    from swp_planner.gui.dialogs import RequirementsDialog
    state = _state(horizon)
    dlg = RequirementsDialog(state)
    qtbot.addWidget(dlg)
    dlg._save()
    assert sorted(state.annual_requirements) == list(range(1, horizon + 1))


@pytest.mark.parametrize("horizon", HORIZONS)
def test_fd_dialog_validation_covers_exactly_the_plan(qtbot, horizon):
    from swp_planner.gui.dialogs import FDRateChunksDialog
    dlg = FDRateChunksDialog(_state(horizon))
    qtbot.addWidget(dlg)
    dlg._reset_to_defaults()
    covered = []
    for c in dlg.chunks:
        covered.extend(range(c.year_from, c.year_to + 1))
    assert covered == list(range(1, horizon + 1))
    assert "\u2713" in dlg.lbl.text(), dlg.lbl.text()


def test_chunk_editors_refuse_to_add_past_the_horizon(qtbot):
    """Add Chunk used to stop at 30 whatever the plan length was."""
    from swp_planner.gui.dialogs import FDRateChunksDialog
    state = _state(12)
    dlg = FDRateChunksDialog(state)
    qtbot.addWidget(dlg)
    dlg.chunks = [FDRateChunk(1, 12, 0.06)]
    dlg._add()
    assert len(dlg.chunks) == 1, "added a chunk starting past the final year"


def test_monte_carlo_sites_are_not_tied_to_the_plan_horizon():
    """MC arrays are 30-element until Phase D; the GUI must say so.

    If someone later makes these follow state.horizon, run_monte_carlo()
    will already have refused the run -- but the indexing would break first,
    with an IndexError instead of the clear refusal.
    """
    from swp_planner.simulation.monte_carlo import MC_FIXED_HORIZON
    assert MC_FIXED_HORIZON == 30
    for mod in ("main.py", "chart_dialog.py", "dialogs.py"):
        src = (PACKAGE_DIR / "gui" / mod).read_text(encoding="utf-8")
        assert "range(30)" not in src, (
            f"{mod} still has a bare range(30); make it explicit -- either "
            "state.horizon or MC_FIXED_HORIZON, so the reader can tell which "
            "is meant")


def test_layout_constants_were_not_swept_up():
    """addSpacing(30) is pixels. A horizon sweep must not touch it."""
    src = (PACKAGE_DIR / "gui" / "main.py").read_text(encoding="utf-8")
    assert src.count("addSpacing(30)") == 2, (
        "the toolbar spacing constants were rewritten by a horizon pass")


# ── A3-i missed two files; these pin them ────────────────────────────────────

@pytest.mark.parametrize("horizon", HORIZONS)
def test_tax_chunk_editors_span_the_plan(qtbot, horizon):
    """With a 40-year plan the editor could not cover FY31-40 at all.

    That is the chunk-coverage defect A3-ii guards against, reachable
    straight from the GUI: a tax band list that stops short leaves those
    years with no band, and the engine then applies no slab to them.
    """
    from swp_planner.gui.tax_dialog import TaxRulesDialog
    state = _state(horizon)
    dlg = TaxRulesDialog(state)
    qtbot.addWidget(dlg)
    for ed in (dlg.ind_debt_ed, dlg.huf_debt_ed):
        assert ed.horizon == horizon
    for ed in (dlg.ind_eq_ed, dlg.huf_eq_ed):
        assert ed.horizon == horizon


def test_the_87a_column_is_marked_unavailable_for_an_huf(qtbot):
    """s.87A is a resident-individual rebate.

    compute_slab_tax() gates it on entity == "individual", so anything typed
    into that column for an HUF changes nothing. A control that silently does
    nothing is the pattern HANDOVER 9.2 objects to.
    """
    from swp_planner.gui.tax_dialog import DebtTaxEditor
    ind = DebtTaxEditor("individual"); qtbot.addWidget(ind)
    huf = DebtTaxEditor("huf"); qtbot.addWidget(huf)
    assert "87A Limit" in ind.chunk_table.horizontalHeaderItem(2).text()
    assert "n/a" in huf.chunk_table.horizontalHeaderItem(2).text()


def test_needs_assessment_year_spinboxes_span_the_plan(qtbot):
    from swp_planner.core.needs_assessment import NeedsAssessmentDialog
    state = _state(20)
    dlg = NeedsAssessmentDialog(state)
    qtbot.addWidget(dlg)
    src = (PACKAGE_DIR / "core" / "needs_assessment.py").read_text()
    assert "ys.setRange(1, 30)" not in src and "ye.setRange(1, 30)" not in src


def test_row_height_constants_were_not_swept_up():
    """setRowHeight(..., 30) is PIXELS, like main.py's addSpacing(30)."""
    src = (PACKAGE_DIR / "core" / "needs_assessment.py").read_text()
    assert src.count("setRowHeight(0, 30)") == 1
    assert src.count("setRowHeight(r, 30)") == 1
