# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
Fund selection and allocation dialog.
"""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QHeaderView, QComboBox, QMessageBox,
    QDialogButtonBox, QAbstractItemView, QDoubleSpinBox, QLineEdit,
    QGroupBox, QSplitter, QWidget, QSizePolicy
)
from PySide6.QtCore import Qt, QSortFilterProxyModel
from PySide6.QtGui import QColor, QFont
from typing import List
import copy

import json
from pathlib import Path
from models import AppState, FundEntry, DEFAULT_FUNDS, SCORE_COLUMNS, _first_available


SORT_OPTIONS = [c[1] for c in SCORE_COLUMNS]
SORT_KEYS    = [c[0] for c in SCORE_COLUMNS]
SORT_DIR     = [c[2] for c in SCORE_COLUMNS]


class FundAllocationDialog(QDialog):
    def __init__(self, state: AppState, parent=None, output_dir: Path = None):
        super().__init__(parent)
        self.setWindowTitle("View Fund Selection & Allocation")
        self.resize(1200, 700)
        self.state = state
        # Load rolling data for mouseover charts
        self._rolling_data: dict = {}
        if output_dir is not None:
            _rp = Path(output_dir) / "rolling_data.json"
            if _rp.exists():
                try:
                    with open(_rp, 'r', encoding='utf-8') as _f:
                        self._rolling_data = json.load(_f)
                except Exception:
                    pass

        # Build per-chunk fund lists (or single list if no chunks)
        if state.allocation_chunks:
            self._chunks = state.allocation_chunks          # List[AllocationChunk]
            self._chunk_idx = 0                             # currently displayed
            # Work on deep copies so cancel works
            self._chunk_funds = [
                [copy.deepcopy(f) for f in ac.funds]
                for ac in self._chunks
            ]
            self.funds = self._chunk_funds[0]
        else:
            self._chunks = []
            self._chunk_idx = 0
            self.funds: List[FundEntry] = [copy.deepcopy(f) for f in state.funds]
            self._chunk_funds = [self.funds]

        self._build_ui()
        self._populate()

    def _build_ui(self):
        main = QVBoxLayout(self)

        # ── Chunk selector (only shown when allocation_chunks exist) ──────────
        if self._chunks:
            chunk_bar = QHBoxLayout()
            chunk_bar.addWidget(QLabel("<b>Viewing chunk:</b>"))
            self.combo_chunk = QComboBox()
            for ac in self._chunks:
                self.combo_chunk.addItem(
                    f"Chunk {self._chunks.index(ac)+1}  "
                    f"(Years {ac.year_from}–{ac.year_to})")
            self.combo_chunk.currentIndexChanged.connect(self._on_chunk_changed)
            chunk_bar.addWidget(self.combo_chunk)
            self.lbl_chunk_yield = QLabel("")
            self.lbl_chunk_yield.setStyleSheet("color:#2980b9; font-weight:bold;")
            chunk_bar.addWidget(self.lbl_chunk_yield)
            chunk_bar.addStretch()
            main.addLayout(chunk_bar)
            self._update_chunk_yield_label()

        # Sort controls
        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("Sort by:"))
        self.combo_sort = QComboBox()
        self.combo_sort.addItems(SORT_OPTIONS)
        self.combo_sort.currentIndexChanged.connect(self._sort)
        ctrl.addWidget(self.combo_sort)

        ctrl.addWidget(QLabel("Filter type:"))
        self.combo_type = QComboBox()
        self.combo_type.addItems(["All", "Debt", "Equity", "Other"])
        self.combo_type.currentIndexChanged.connect(self._filter)
        ctrl.addWidget(self.combo_type)

        ctrl.addStretch()

        lbl_total = QLabel("Total allocated (L):")
        self.lbl_total_val = QLabel("0")
        self.lbl_total_val.setFont(QFont("", 12, QFont.Weight.Bold))
        ctrl.addWidget(lbl_total)
        ctrl.addWidget(self.lbl_total_val)

        main.addLayout(ctrl)

        # Table
        all_cols = ["Name", "Type", "Sub-Category", "Allocation (L)", "Weight %",
                    "Comb Ratio", "Std Dev", "Sharpe", "Sortino",
                    "Calmar", "Alpha", "Treynor", "Max DD", "Beta",
                    "Avg 3Y%", "Avg 5Y%", "Avg 7Y%", "Avg 10Y%",
                    "CAGR Used%", "Std Used%", "|DD| Used%"]
        self.table = QTableWidget(0, len(all_cols))
        self.table.setHorizontalHeaderLabels(all_cols)
        # Tooltips on the 3 "used" columns so users understand what they represent
        _used_tips = {
            "CAGR Used%": (
                "min(Avg_CAGR_3Y/5Y/7Y/10Y)\n"
                "Used by the allocator as the conservative expected return (adj_ret)\n"
                "and by the engine for per-fund NAV compounding each month."
            ),
            "Std Used%":  (
                "max(Std_Dev_10Y/5Y/3Y) — worst-case rolling std dev.\n"
                "Used as adj_std in the portfolio volatility constraint\n"
                "and as per-fund sigma in Monte Carlo simulations."
            ),
            "|DD| Used%": (
                "abs(min(Max_DD_10Y/5Y/3Y)) — worst-case max drawdown magnitude.\n"
                "Used as adj_dd in the portfolio drawdown constraint."
            ),
        }
        for _ci, _col in enumerate(all_cols):
            if _col in _used_tips:
                _hdr_item = self.table.horizontalHeaderItem(_ci)
                if _hdr_item:
                    _hdr_item.setToolTip(_used_tips[_col])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, len(all_cols)):
            self.table.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.itemChanged.connect(self._on_alloc_changed)
        self.table.setMouseTracking(True)
        self.table.viewport().setMouseTracking(True)   # required for cellEntered to fire
        self.table.cellEntered.connect(self._on_cell_entered)
        self._chart_popup = None   # holds the active RollingChartPopup
        self._last_hovered_row = -1
        main.addWidget(self.table)

        # Note
        note = QLabel("💡 Edit the 'Allocation (L)' column directly. "
                      "'Weight %' = fund allocation as % of chunk total.  "
                      "Row colours: 🟢 in all chunks | 🟠 some chunks | 🔴 one chunk only.")
        note.setStyleSheet("color: #555; font-size: 11px;")
        main.addWidget(note)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._save_and_accept)
        btns.rejected.connect(self.reject)
        main.addWidget(btns)

        self._row_fund_map: List[int] = []   # row -> index in self.funds

    def _populate(self):
        self._filter()

    def _filter(self):
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self._row_fund_map = []
        type_filter = self.combo_type.currentText().lower()

        sort_key = SORT_KEYS[self.combo_sort.currentIndex()]
        sort_dir = SORT_DIR[self.combo_sort.currentIndex()]

        # Filter
        candidates = [(i, f) for i, f in enumerate(self.funds)
                      if type_filter == "all" or f.fund_type == type_filter]
        # Sort
        def sortval(pair):
            val = getattr(pair[1], sort_key, None)
            return val if val is not None else -1e9
        candidates.sort(key=sortval, reverse=(sort_dir == "higher"))

        # ── Cross-chunk fund presence map (for color coding) ─────────────
        # For each fund name, count how many chunks it appears in (alloc > 0)
        num_chunks = len(self._chunk_funds) if self._chunks else 1
        fund_chunk_count = {}
        if num_chunks > 1:
            for chunk_funds in self._chunk_funds:
                for f in chunk_funds:
                    if f.allocation > 0:
                        fund_chunk_count[f.name] = fund_chunk_count.get(f.name, 0) + 1

        # Total allocation for this chunk (for weight % calculation)
        total_alloc = sum(f.allocation for f in self.funds if f.allocation > 0)

        for orig_idx, fund in candidates:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self._row_fund_map.append(orig_idx)

            # Opt. Weight % = fund's allocation as a percentage of chunk total
            opt_weight_pct = ""
            if fund.allocation > 0 and total_alloc > 0:
                opt_weight_pct = f"{fund.allocation / total_alloc * 100:.2f}%"

            # AMFI sub-category: show a short label (strip "Debt Scheme - " etc.)
            amfi_sub = fund.amfi_fund_type or ""
            if " - " in amfi_sub:
                amfi_sub = amfi_sub.split(" - ", 1)[1]

            # ── "Used" values: what the allocator and engine actually use ──────
            # CAGR Used = min(avg_cagr_3/5/7/10): the conservative return the
            #   allocator maximises against (adj_ret) and the engine uses for
            #   per-fund NAV compounding.  Same basis as the Yield% in the header.
            _avg_cagrs = [c for c in (
                getattr(fund, "avg_cagr_3",  None),
                getattr(fund, "avg_cagr_5",  None),
                getattr(fund, "avg_cagr_7",  None),
                getattr(fund, "avg_cagr_10", None),
            ) if c is not None and c > 0]
            _cagr_used_str = f"{min(_avg_cagrs):.2f}%" if _avg_cagrs else "–"
            # Std Used = fund.std_dev = max(Std_Dev_10Y/5Y/3Y) loaded at import:
            #   worst-case rolling std dev; used as adj_std in portfolio constraint
            #   and as per-fund sigma in Monte Carlo.
            _std_used_str  = f"{fund.std_dev:.2f}%"
            # |DD| Used = abs(fund.max_dd) = abs(min(Max_DD_10Y/5Y/3Y)):
            #   worst-case drawdown magnitude; used as adj_dd in portfolio constraint.
            _dd_used_str   = f"{abs(fund.max_dd):.3f}%"

            cols = [
                fund.name, fund.fund_type, amfi_sub,
                str(fund.allocation),
                opt_weight_pct,                          # col 4: Opt. Weight %
                f"{fund.combined_ratio:.3f}",
                f"{fund.std_dev:.2f}", f"{fund.sharpe:.2f}", f"{fund.sortino:.2f}",
                f"{fund.calmar:.2f}", f"{fund.alpha:.2f}", f"{fund.treynor:.2f}",
                f"{fund.max_dd:.3f}", f"{fund.beta:.2f}",
                f"{fund.avg_cagr_3:.2f}%"  if getattr(fund, 'avg_cagr_3',  None) else "–",
                f"{fund.avg_cagr_5:.2f}%"  if getattr(fund, 'avg_cagr_5',  None) else "–",
                f"{fund.avg_cagr_7:.2f}%"  if getattr(fund, 'avg_cagr_7',  None) else "–",
                f"{fund.avg_cagr_10:.2f}%" if getattr(fund, 'avg_cagr_10', None) else "–",
                _cagr_used_str,   # col 18: CAGR Used%
                _std_used_str,    # col 19: Std Used%
                _dd_used_str,     # col 20: |DD| Used%
            ]

            # ── Determine row background color ───────────────────────────
            # Default: type-based coloring (equity=light blue, other=light orange)
            # Override if multi-chunk: green=all chunks, orange=some, pink=one only
            row_bg = None
            if num_chunks > 1 and fund.allocation > 0:
                n = fund_chunk_count.get(fund.name, 0)
                if n >= num_chunks:
                    row_bg = QColor("#d5f5e3")    # light green — in ALL chunks
                elif n > 1:
                    row_bg = QColor("#fdebd0")    # light orange — in SOME chunks
                else:
                    row_bg = QColor("#fadbd8")    # light pink — in ONE chunk only

            # Column index of the first "Used" column (CAGR Used%)
            _FIRST_USED = len(cols) - 3   # 18 for the current schema

            for col_idx, val in enumerate(cols):
                item = QTableWidgetItem(val)
                if col_idx != 3:   # only allocation (col 3) is editable
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if col_idx == 4 and opt_weight_pct:
                    # highlight optimized weight column
                    item.setForeground(QColor("#2980b9"))
                # ── "Used" columns: teal tint to visually separate from display metrics
                if col_idx >= _FIRST_USED:
                    item.setBackground(QColor("#d0f0f0"))   # light teal
                    item.setForeground(QColor("#145a6b"))
                    if fund.allocation > 0:
                        _f = QFont(); _f.setBold(True); item.setFont(_f)
                else:
                    # Apply row background
                    if row_bg is not None:
                        item.setBackground(row_bg)
                    elif fund.fund_type == "equity":
                        item.setBackground(QColor("#e8f4fd"))
                    elif fund.fund_type == "other":
                        item.setBackground(QColor("#fdf5e6"))
                    if fund.allocation > 0:
                        item.setFont(QFont("", -1, QFont.Weight.Bold))
                self.table.setItem(r, col_idx, item)

        self.table.blockSignals(False)
        self._update_total()

    def _sort(self):
        self._filter()

    def _on_alloc_changed(self, item):
        if item.column() != 3:
            return
        row = item.row()
        if row >= len(self._row_fund_map):
            return
        orig_idx = self._row_fund_map[row]
        try:
            val = float(item.text())
            self.funds[orig_idx].allocation = max(0.0, val)
        except ValueError:
            pass
        self._update_total()

    def _update_total(self):
        total = sum(f.allocation for f in self.funds)
        self.lbl_total_val.setText(f"₹ {total:.1f} L")
        debt = sum(f.allocation for f in self.funds if f.fund_type == "debt")
        eq = sum(f.allocation for f in self.funds if f.fund_type == "equity")
        oth = sum(f.allocation for f in self.funds if f.fund_type == "other")
        self.lbl_total_val.setToolTip(f"Debt: {debt:.1f}L | Equity: {eq:.1f}L | Other: {oth:.1f}L")
        if self._chunks:
            self._update_chunk_yield_label()

    def _on_chunk_changed(self, idx: int):
        """Switch displayed fund list to the selected chunk."""
        # Save current edits back into _chunk_funds before switching
        self._chunk_funds[self._chunk_idx] = [
            copy.deepcopy(f) for f in self.funds
        ]
        self._chunk_idx = idx
        self.funds = self._chunk_funds[idx]
        self._update_chunk_yield_label()
        self._filter()

    def _update_chunk_yield_label(self):
        if not self._chunks:
            return
        ac    = self._chunks[self._chunk_idx]
        funds = self._chunk_funds[self._chunk_idx]

        # Compute portfolio yield from current edits
        total = weight = 0.0
        wtd_std = wtd_dd = 0.0
        for f in funds:
            if f.allocation > 0:
                cagr = _first_available(f.cagr_5, f.cagr_3, f.cagr_1, default=7.0)
                total  += cagr * f.allocation
                weight += f.allocation
                # Weighted std_dev and max_dd
                wtd_std += (f.std_dev or 0.0) * f.allocation
                wtd_dd  += abs(f.max_dd or 0.0) * f.allocation
        yld   = (total / weight) if weight > 0 else 0.0
        port_std = (wtd_std / weight) if weight > 0 else 0.0
        port_dd  = (wtd_dd / weight) if weight > 0 else 0.0
        alloc = sum(f.allocation for f in funds if f.allocation > 0)

        # ── Aim-pass type ratios (_type_ratios set by run_aim_pass) ──────────
        tr     = getattr(ac, "_type_ratios", {})
        eq_pct = tr.get("equity", 0.0) * 100
        dt_pct = tr.get("debt",   0.0) * 100
        ot_pct = tr.get("other",  0.0) * 100
        if tr:
            ratio_str = (
                f"  │  D:{dt_pct:.0f}% E:{eq_pct:.0f}%"
                + (f" O:{ot_pct:.0f}%" if ot_pct > 0.5 else "")
            )
        else:
            ratio_str = ""

        # ── Portfolio risk metrics ───────────────────────────────────────────
        # std_dev is already in % (e.g. 1.91); max_dd is decimal fraction
        # (e.g. 0.021 = 2.1%), so multiply by 100 for display.
        risk_str = f"  │  Std:{port_std:.2f}%  |DD|:{port_dd * 100:.2f}%"

        # ── Constraint slack (constraint_slack_used set by run_track_pass) ───
        sl = getattr(ac, "constraint_slack_used", {})
        slack_parts = []
        if sl.get("return",  0.0) > 1e-5:
            slack_parts.append(f"ret↓{sl['return']*100:.2f}pp")
        if sl.get("std_dev", 0.0) > 1e-5:
            slack_parts.append(f"std↑{sl['std_dev']*100:.2f}pp")
        if sl.get("max_dd",  0.0) > 1e-5:
            slack_parts.append(f"dd↑{sl['max_dd']*100:.2f}pp")
        slack_str = ("  ⚠ Slack: " + ", ".join(slack_parts)) if slack_parts else ""

        self.lbl_chunk_yield.setText(
            f"  ₹{alloc:.1f} L  |  Yield: {yld:.2f}%{ratio_str}{risk_str}{slack_str}"
        )

        # Colour: orange if slack consumed, blue if optimised, grey if not yet run
        if slack_parts:
            self.lbl_chunk_yield.setStyleSheet(
                "color:#e67e22; font-weight:bold; font-size:11px;")
        elif tr:
            self.lbl_chunk_yield.setStyleSheet(
                "color:#2980b9; font-weight:bold; font-size:11px;")
        else:
            self.lbl_chunk_yield.setStyleSheet(
                "color:#2980b9; font-weight:bold;")

    def _on_cell_entered(self, row: int, col: int):
        """Show rolling CAGR/std chart popup when hovering over col 0 (Name)."""
        if col != 0:
            return
        if row == self._last_hovered_row:
            return
        self._last_hovered_row = row
        if row >= len(self._row_fund_map):
            return
        orig_idx = self._row_fund_map[row]
        fund = self.funds[orig_idx]
        rolling = self._rolling_data.get(fund.name, {})
        if not rolling:
            if self._chart_popup:
                self._chart_popup.hide()
            return
        # Position popup to the right of the table
        item = self.table.item(row, 0)
        if item is None:
            return
        rect   = self.table.visualItemRect(item)
        gpos   = self.table.viewport().mapToGlobal(rect.topRight())
        if self._chart_popup is None:
            self._chart_popup = RollingChartPopup(self)
        self._chart_popup.update_chart(fund.name, rolling)
        self._chart_popup.move(gpos.x() + 8, gpos.y())
        self._chart_popup.show()
        self._chart_popup.raise_()

    def leaveEvent(self, event):  # noqa: N802
        if self._chart_popup:
            self._chart_popup.hide()
        self._last_hovered_row = -1
        super().leaveEvent(event)

    def _save_and_accept(self):
        # Save current chunk edits
        self._chunk_funds[self._chunk_idx] = [
            copy.deepcopy(f) for f in self.funds
        ]

        if self._chunks:
            # Update allocation_chunks in state
            for i, ac in enumerate(self.state.allocation_chunks):
                ac.funds = [copy.deepcopy(f) for f in self._chunk_funds[i]]
            # Also update flat funds list to match the last (most recent) chunk
            last_funds = self._chunk_funds[-1]
            self.state.funds = [copy.deepcopy(f) for f in last_funds]
        else:
            self.state.funds = [copy.deepcopy(f) for f in self.funds]

        # Update FD rate from portfolio yield (legacy scalar, for backward compat)
        self.state.fd_rate = self.state.portfolio_yield()
        # Note: FD rate chunks are now user-managed separately
        self.accept()


# ─────────────────────────────────────────────────────────────────────────
# Rolling returns popup chart
# ─────────────────────────────────────────────────────────────────────────
class RollingChartPopup(QWidget):
    """
    Floating popup that shows rolling CAGR and Std Dev distributions
    for a single fund across 3Y / 5Y / 7Y / 10Y windows.

    Uses pure Qt (QPainter) — no matplotlib dependency.

    Layout (top→bottom):
      TITLE BAR  : fund name
      CHART 1    : Rolling CAGR %   (line per window)
      X-AXIS 1   : month numbers
      GAP
      CHART 2    : Rolling Std Dev % (line per window)
      X-AXIS 2   : month numbers
      LEGEND BAR : coloured line + label per window
    """

    WIDTH   = 780
    HEIGHT  = 520

    # Colours per window label
    WINDOW_COLORS = {
        "3Y":  QColor("#e74c3c"),   # red
        "5Y":  QColor("#e67e22"),   # orange
        "7Y":  QColor("#27ae60"),   # green
        "10Y": QColor("#2980b9"),   # blue
    }
    WINDOWS = ["3Y", "5Y", "7Y", "10Y"]

    # Layout constants (pixels)
    PAD_L   = 52   # left  (y-axis labels)
    PAD_R   = 18   # right margin
    PAD_T   = 34   # top   (title)
    PAD_B   = 42   # bottom (legend)
    XAXIS_H = 18   # height reserved for x-axis tick labels below each chart
    GAP     = 14   # gap between the two sub-charts
    CTITLE  = 14   # height reserved for chart title inside each sub-chart

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.ToolTip)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setStyleSheet(
            "background:#1a2035; border:1px solid #3a4562; border-radius:7px;"
        )
        self._fund_name = ""
        self._series: dict = {}

    def update_chart(self, fund_name: str, rolling: dict):
        self._fund_name = fund_name
        self._series = rolling
        self.update()

    # ── painting ─────────────────────────────────────────────────────────
    def paintEvent(self, event):  # noqa: N802
        from PySide6.QtGui import QPainter, QPen, QFontMetrics
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        W, H = self.WIDTH, self.HEIGHT
        PL, PR, PT, PB = self.PAD_L, self.PAD_R, self.PAD_T, self.PAD_B
        XH, GAP, CTH = self.XAXIS_H, self.GAP, self.CTITLE

        plot_w = W - PL - PR
        # Total vertical space for the two charts
        charts_h = H - PT - PB - XH * 2 - GAP
        sub_h = charts_h // 2        # height of one chart's plot area

        # ── Title ────────────────────────────────────────────────────────
        tf = self.font()
        tf.setPointSize(9)
        tf.setBold(True)
        p.setFont(tf)
        p.setPen(QColor("#ecf0f1"))
        name_short = (self._fund_name[:68] + "…") if len(self._fund_name) > 68 else self._fund_name
        p.drawText(PL, PT - 6, name_short)

        # ── Draw each sub-chart ───────────────────────────────────────────
        chart_defs = [
            ("cagrs", "Rolling CAGR %"),
            ("stds",  "Rolling Std Dev %"),
        ]

        for chart_idx, (key, chart_title) in enumerate(chart_defs):
            # y0/y1 = top/bottom of this chart's plot area
            y0 = PT + chart_idx * (sub_h + XH + GAP)
            y1 = y0 + sub_h

            # ── Collect all values ────────────────────────────────────────
            all_vals = []
            for wk in self.WINDOWS:
                vals = self._series.get(wk, {}).get(key, [])
                if vals:
                    all_vals.extend(vals)
            if not all_vals:
                continue

            v_min = min(all_vals)
            v_max = max(all_vals)
            v_range = max(v_max - v_min, 1e-4)

            # ── Axes ─────────────────────────────────────────────────────
            af = self.font()
            af.setPointSize(7)
            p.setFont(af)

            # Y-axis labels  (show 3 values: max, mid, min)
            p.setPen(QColor("#7f8c8d"))
            mid_v = (v_max + v_min) / 2
            for yv, ypos in [(v_max, y0 + 8), (mid_v, (y0 + y1) // 2 + 4), (v_min, y1)]:
                p.drawText(2, ypos, f"{yv:.2f}")

            # Chart title (left-aligned inside the chart area)
            ctf = self.font()
            ctf.setPointSize(8)
            ctf.setBold(True)
            p.setFont(ctf)
            p.setPen(QColor("#bdc3c7"))
            p.drawText(PL + 4, y0 + 11, chart_title)
            p.setFont(af)

            # Axis lines
            p.setPen(QPen(QColor("#2c3e50"), 1))
            p.drawLine(PL, y0, PL, y1)          # Y-axis
            p.drawLine(PL, y1, W - PR, y1)      # X-axis baseline

            # Horizontal grid (mid line)
            grid_pen = QPen(QColor("#263045"), 1)
            grid_pen.setStyle(Qt.PenStyle.DotLine)
            p.setPen(grid_pen)
            p.drawLine(PL, (y0 + y1) // 2, W - PR, (y0 + y1) // 2)

            # ── X-axis ticks and labels ───────────────────────────────────
            # Determine max series length across all windows for this chart
            max_n = max(
                (len(self._series.get(wk, {}).get(key, [])) for wk in self.WINDOWS),
                default=0
            )
            if max_n > 1:
                p.setPen(QColor("#566573"))
                tick_font = self.font()
                tick_font.setPointSize(6)
                p.setFont(tick_font)
                # Draw ~8 evenly spaced month-number ticks
                n_ticks = min(8, max_n)
                for ti in range(n_ticks + 1):
                    month_num = int(ti * (max_n - 1) / n_ticks) if n_ticks > 0 else 0
                    x = PL + int(month_num / (max_n - 1) * plot_w)
                    p.setPen(QPen(QColor("#566573"), 1))
                    p.drawLine(x, y1, x, y1 + 4)   # tick mark
                    p.setPen(QColor("#7f8c8d"))
                    lbl = str(month_num + 1)
                    fm = QFontMetrics(tick_font)
                    p.drawText(x - fm.horizontalAdvance(lbl) // 2, y1 + XH - 2, lbl)
                p.setFont(af)
                # X-axis label "Month"
                p.setPen(QColor("#566573"))
                p.drawText(W - PR - 30, y1 + XH - 2, "month")

            # ── Draw one line per window ──────────────────────────────────
            for wk in self.WINDOWS:
                vals = self._series.get(wk, {}).get(key, [])
                if len(vals) < 2:
                    continue
                color = self.WINDOW_COLORS[wk]
                pen = QPen(color, 1)
                p.setPen(pen)
                n = len(vals)
                pts = []
                for i, v in enumerate(vals):
                    x = PL + int(i / (n - 1) * plot_w)
                    y = y1 - int((v - v_min) / v_range * sub_h)
                    pts.append((x, y))
                for i in range(len(pts) - 1):
                    p.drawLine(pts[i][0], pts[i][1], pts[i+1][0], pts[i+1][1])

        # ── Legend ───────────────────────────────────────────────────────
        leg_y = H - PB + 10
        lx = PL
        lf = self.font()
        lf.setPointSize(8)
        p.setFont(lf)
        for wk in self.WINDOWS:
            if wk not in self._series:
                continue
            color = self.WINDOW_COLORS[wk]
            p.setPen(QPen(color, 2))
            p.drawLine(lx, leg_y + 5, lx + 20, leg_y + 5)
            # Small dot at mid-point
            p.setBrush(color)
            p.drawEllipse(lx + 7, leg_y + 2, 6, 6)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QColor("#ecf0f1"))
            p.drawText(lx + 28, leg_y + 9, wk)
            lx += 68
        p.end()