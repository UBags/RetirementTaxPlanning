#!/usr/bin/env python3
"""
Regenerate the README screenshots in docs/images/.

    QT_QPA_PLATFORM=offscreen python3 scripts/make_readme_screenshots.py

Drives the real GUI headlessly against the golden "main" project
(tests/golden/golden_projects.py: synthetic money figures and generalised
fund names, so nothing personal reaches a public README). The project is
loaded through MainWindow's own File -> Open path and calculated with its
own Run Calculations handler. Monte Carlo uses the frozen history fixture
(tests/golden/fixtures/mc_history.json), so no network access is needed
and the fan chart is reproducible.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests" / "golden")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDate, QSize  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

OUT = ROOT / "docs" / "images"
WIN = QSize(1500, 900)
CHART = QSize(1300, 760)


def pump(app: QApplication, seconds: float = 0.3) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def save(widget, name: str, app: QApplication) -> None:
    pump(app)
    path = OUT / name
    widget.grab().save(str(path))
    print(f"  wrote {path.relative_to(ROOT)}  {widget.width()}x{widget.height()}")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    from golden_projects import PROJECTS

    from swp_planner.gui.app import UserNameDialog
    from swp_planner.gui.main import MainWindow

    tmp = Path(tempfile.mkdtemp())
    project = tmp / "Demo_project.swp.json"
    project.write_text(json.dumps(PROJECTS["main"]), encoding="utf-8")

    # File -> Open returns the golden project; any error dialog fails loudly.
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (str(project), ""))

    def _fail(*a, **k):
        raise RuntimeError(f"GUI reported an error: {a[1:3]}")
    QMessageBox.critical = staticmethod(_fail)

    # 1. Welcome dialog. Its label shows the absolute outputs/ path; show a
    #    neutral one rather than the machine this script ran on.
    import swp_planner.gui.app as gui_app
    gui_app.OUTPUTS_DIR = Path("~/RetirementTaxPlanning/outputs")
    dlg = UserNameDialog()
    dlg.name_edit.setText("Demo")
    dlg.show()
    save(dlg, "01_welcome.png", app)
    dlg.close()

    # 2. Main window, calculated
    w = MainWindow(output_dir=tmp, user_name="Demo")
    w.resize(WIN)
    w.show()
    w._open_project()
    w.date_edit.setDate(QDate(2026, 3, 1))
    w.setFocus()   # no highlighted date field in the shots
    w._run_calculations()
    t0 = time.time()
    while not w.btn_run.isEnabled():
        if time.time() - t0 > 300:
            raise RuntimeError("calculation did not finish in 300 s")
        app.processEvents()
        time.sleep(0.05)

    tabs = w.tabs
    titles = [tabs.tabText(i) for i in range(tabs.count())]

    tabs.setCurrentIndex(titles.index("Annual Combined Summary"))
    save(w, "02_annual_summary.png", app)

    # 3. Charts
    w._show_chart_ann(w._last_yearly)
    chart = w._chart_windows[-1]
    chart.resize(CHART)
    save(chart, "03_annual_chart.png", app)
    chart.close()

    from swp_planner.simulation import monte_carlo as mc
    hist = json.loads((ROOT / "tests" / "golden" / "fixtures" / "mc_history.json")
                      .read_text(encoding="utf-8"))
    import numpy as np
    eq = np.asarray(hist["equity_annual_returns"], dtype=float)
    dbt = np.asarray(hist["debt_annual_returns"], dtype=float)
    mc._fetch_nifty50_annual_returns = lambda: eq
    mc._fetch_debt_index_annual_returns = lambda: dbt
    # Through MainWindow._run_monte_carlo, accepting the parameter dialog's
    # defaults, so the run gets exactly the arguments a user's run gets
    # (notably the deterministic solve's gross withdrawal schedule).
    from swp_planner.gui import dialogs as gui_dialogs
    gui_dialogs.MonteCarloDialog.exec = lambda self: 1
    w._run_monte_carlo()
    pump(app, 1.0)
    w._show_chart_mc(w._last_mc)
    chart = w._chart_windows[-1]
    chart.resize(CHART)
    save(chart, "04_monte_carlo.png", app)
    chart.close()

    # 4. Input dialogs
    from swp_planner.gui.fund_dialog import FundAllocationDialog
    from swp_planner.gui.tax_dialog import TaxRulesDialog

    d = TaxRulesDialog(w.state, w)
    d.show()
    from swp_planner.gui.tax_dialog import DebtTaxEditor
    for ed in d.findChildren(DebtTaxEditor):           # show the first chunk's slabs
        ed.chunk_table.setCurrentCell(0, 0)
        ed.chunk_table.selectRow(0)
    save(d, "05_tax_rules.png", app)
    d.close()

    d = FundAllocationDialog(w.state, w, output_dir=tmp)
    d.resize(CHART)
    d.show()
    save(d, "06_fund_allocation.png", app)
    d.close()

    w.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
