# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.
"""
Financial Needs Assessment Dialog — US Retirement Planning Module

51 expense categories covering all major spending heads a US retiree faces
over a 2–4 decade horizon.  Each line item records:
  • Monthly amount (USD)
  • Frequency   : monthly | annual | one-time
  • Inflation rate (% per year, default 3 %)
  • Start / end year (plan years 1–30, defaults 1 / 30)
  • Notes (free text)

The dialog computes a year-by-year inflation-adjusted spending projection and
writes the resulting annual totals into AppState.annual_requirements (in US
dollars — callers that still use the Indian Rs-lakh scale must divide by
100,000).

Integration with main.py
------------------------
1.  Add to the imports at the top of main.py:
        from needs_assessment_dialog import NeedsAssessmentDialog
2.  Add one menu action inside the Configuration menu (shared section):
        self._act(cm, "Financial Needs Assessment (US)...",
                  self._open_needs_assessment)
3.  Add the handler method to MainWindow:
        def _open_needs_assessment(self):
            dlg = NeedsAssessmentDialog(self.state, self)
            dlg.exec()
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
    QSpinBox, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem,
    QTextEdit, QVBoxLayout, QWidget,
)

# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

FREQ_MONTHLY   = "monthly"
FREQ_ANNUAL    = "annual"
FREQ_ONE_TIME  = "one-time"
FREQUENCIES    = [FREQ_MONTHLY, FREQ_ANNUAL, FREQ_ONE_TIME]

DEFAULT_INFLATION = 3.0   # % per year
DEFAULT_HEALTHCARE_INFLATION = 5.5  # healthcare items inflate faster


@dataclass
class ExpenseItem:
    """A single line-item expense in the needs assessment."""
    id:         int             # 1–51 (canonical; never changes)
    name:       str
    group:      str             # group label (Housing, Transportation …)
    amount:     float = 0.0    # base amount in USD (monthly equivalent)
    frequency:  str   = FREQ_MONTHLY
    inflation:  float = DEFAULT_INFLATION   # % per year
    year_start: int   = 1       # plan year when spending begins
    year_end:   int   = 30      # plan year when spending ends
    notes:      str   = ""
    enabled:    bool  = True    # unchecked = excluded from totals


def _make_default_items() -> List[ExpenseItem]:
    """Return all 51 expense items with sensible BLS-based defaults."""
    # (id, name, group, default_monthly_USD, inflation_rate)
    raw: List[tuple] = [
        # ── Housing (1–15) ───────────────────────────────────────────────────
        ( 1, "Mortgage or Rent Payments",                   "Housing",                 1_800, DEFAULT_INFLATION),
        ( 2, "Property Taxes",                              "Housing",                   350, DEFAULT_INFLATION),
        ( 3, "Homeowners / Renters Insurance",              "Housing",                   130, DEFAULT_INFLATION),
        ( 4, "Home Maintenance & Repairs (routine)",        "Housing",                   200, DEFAULT_INFLATION),
        ( 5, "Major Home Renovations / Upgrades",           "Housing",                    80, DEFAULT_INFLATION),
        ( 6, "HOA / Condo Association Fees",                "Housing",                   120, DEFAULT_INFLATION),
        ( 7, "Electricity",                                 "Housing",                   130, DEFAULT_INFLATION),
        ( 8, "Natural Gas / Heating Fuel",                  "Housing",                    80, DEFAULT_INFLATION),
        ( 9, "Water & Sewer",                               "Housing",                    60, DEFAULT_INFLATION),
        (10, "Garbage / Trash Collection",                  "Housing",                    30, DEFAULT_INFLATION),
        (11, "Internet / Broadband",                        "Housing",                    75, DEFAULT_INFLATION),
        (12, "Cable TV / Streaming Subscriptions",          "Housing",                    60, DEFAULT_INFLATION),
        (13, "Home Security / Alarms & Monitoring",         "Housing",                    40, DEFAULT_INFLATION),
        (14, "Lawn Care & Gardening Services",              "Housing",                    80, DEFAULT_INFLATION),
        (15, "Pest Control Services",                       "Housing",                    25, DEFAULT_INFLATION),
        # ── Transportation (16–22) ───────────────────────────────────────────
        (16, "Auto Loan / Lease Payments",                  "Transportation",             400, DEFAULT_INFLATION),
        (17, "Auto Insurance",                              "Transportation",             150, DEFAULT_INFLATION),
        (18, "Fuel / Gasoline",                             "Transportation",             150, DEFAULT_INFLATION),
        (19, "Vehicle Maintenance & Repairs",               "Transportation",             120, DEFAULT_INFLATION),
        (20, "Vehicle Registration & DMV Fees",             "Transportation",              20, DEFAULT_INFLATION),
        (21, "Tolls & Parking Fees",                        "Transportation",              30, DEFAULT_INFLATION),
        (22, "Public Transit / Rideshares / Taxis",         "Transportation",              80, DEFAULT_INFLATION),
        # ── Food & Daily Living (23–25) ──────────────────────────────────────
        (23, "Groceries & Household Meals",                 "Food & Daily Living",        500, DEFAULT_INFLATION),
        (24, "Dining Out & Restaurants",                    "Food & Daily Living",        250, DEFAULT_INFLATION),
        (25, "Food Delivery / Takeout",                     "Food & Daily Living",        100, DEFAULT_INFLATION),
        # ── Healthcare (26–33) ───────────────────────────────────────────────
        (26, "Medicare Part B & Medigap / Supplemental",    "Healthcare",                 350, DEFAULT_HEALTHCARE_INFLATION),
        (27, "Prescription Drugs (out-of-pocket)",          "Healthcare",                 150, DEFAULT_HEALTHCARE_INFLATION),
        (28, "Doctor Visits, Co-pays & Deductibles",        "Healthcare",                 100, DEFAULT_HEALTHCARE_INFLATION),
        (29, "Hospital & Major Medical Expenses",           "Healthcare",                  80, DEFAULT_HEALTHCARE_INFLATION),
        (30, "Dental Care & Insurance",                     "Healthcare",                  80, DEFAULT_HEALTHCARE_INFLATION),
        (31, "Vision Care, Eyewear & Exams",                "Healthcare",                  30, DEFAULT_HEALTHCARE_INFLATION),
        (32, "Long-Term Care (aide, assisted living, etc.)", "Healthcare",                 200, DEFAULT_HEALTHCARE_INFLATION),
        (33, "OTC Medications, Health Supplies & Equipment","Healthcare",                  50, DEFAULT_HEALTHCARE_INFLATION),
        # ── Insurance & Protection (34–35) ──────────────────────────────────
        (34, "Life Insurance Premiums",                     "Insurance & Protection",     100, DEFAULT_INFLATION),
        (35, "Umbrella Liability Insurance",                "Insurance & Protection",      20, DEFAULT_INFLATION),
        # ── Personal Care & Household (36–38) ───────────────────────────────
        (36, "Clothing & Apparel",                          "Personal Care",              100, DEFAULT_INFLATION),
        (37, "Personal Grooming, Haircuts & Toiletries",    "Personal Care",               80, DEFAULT_INFLATION),
        (38, "Household Supplies & Cleaning",               "Personal Care",               80, DEFAULT_INFLATION),
        # ── Leisure, Entertainment & Lifestyle (39–45) ──────────────────────
        (39, "Entertainment (hobbies, events, concerts…)",  "Leisure & Lifestyle",        150, DEFAULT_INFLATION),
        (40, "Domestic & International Travel / Vacations", "Leisure & Lifestyle",        250, DEFAULT_INFLATION),
        (41, "Holidays & Family Celebrations",              "Leisure & Lifestyle",        100, DEFAULT_INFLATION),
        (42, "Gifts to Family / Grandchildren",             "Leisure & Lifestyle",         80, DEFAULT_INFLATION),
        (43, "Financial Support for Dependents",            "Leisure & Lifestyle",        200, DEFAULT_INFLATION),
        (44, "Charitable Contributions / Donations",        "Leisure & Lifestyle",        100, DEFAULT_INFLATION),
        (45, "Club / Gym / Social Memberships",             "Leisure & Lifestyle",         60, DEFAULT_INFLATION),
        # ── Communications & Technology (46–47) ─────────────────────────────
        (46, "Cell Phone & Communication Plans",            "Communications & Tech",       80, DEFAULT_INFLATION),
        (47, "Technology Purchases & Upgrades",             "Communications & Tech",       50, DEFAULT_INFLATION),
        # ── Professional & Other Services (48–50) ───────────────────────────
        (48, "Financial Advisor / Planning Fees",           "Professional Services",      150, DEFAULT_INFLATION),
        (49, "Tax Preparation Services",                    "Professional Services",       50, DEFAULT_INFLATION),
        (50, "Legal / Estate Planning Costs",               "Professional Services",       40, DEFAULT_INFLATION),
        # ── Miscellaneous / Emergency Reserve (51) ──────────────────────────
        (51, "Miscellaneous / Emergency Reserve",           "Miscellaneous",              200, DEFAULT_INFLATION),
    ]
    items = []
    for id_, name, group, monthly, infl in raw:
        items.append(ExpenseItem(
            id=id_, name=name, group=group,
            amount=float(monthly), frequency=FREQ_MONTHLY,
            inflation=infl,
            year_start=1, year_end=30,
            notes="", enabled=False,   # all off by default; user enables what applies
        ))
    return items


# Groups in display order
GROUPS: List[str] = [
    "Housing",
    "Transportation",
    "Food & Daily Living",
    "Healthcare",
    "Insurance & Protection",
    "Personal Care",
    "Leisure & Lifestyle",
    "Communications & Tech",
    "Professional Services",
    "Miscellaneous",
]

# Key for persisting needs assessment data in AppState
_STATE_KEY = "needs_assessment_items"


def _load_items_from_state(state) -> List[ExpenseItem]:
    """Read saved expense items from state, or return defaults."""
    raw = getattr(state, _STATE_KEY, None)
    if not raw:
        return _make_default_items()
    try:
        defaults = {i.id: i for i in _make_default_items()}
        items = []
        for d in raw:
            base = defaults.get(d.get("id"), ExpenseItem(id=0, name="", group=""))
            items.append(ExpenseItem(
                id=d.get("id", base.id),
                name=d.get("name", base.name),
                group=d.get("group", base.group),
                amount=float(d.get("amount", 0)),
                frequency=d.get("frequency", FREQ_MONTHLY),
                inflation=float(d.get("inflation", DEFAULT_INFLATION)),
                year_start=int(d.get("year_start", 1)),
                year_end=int(d.get("year_end", 30)),
                notes=d.get("notes", ""),
                enabled=bool(d.get("enabled", False)),
            ))
        return items
    except Exception:
        return _make_default_items()


def _save_items_to_state(state, items: List[ExpenseItem]) -> None:
    """Persist expense items into state as a plain list of dicts."""
    setattr(state, _STATE_KEY, [asdict(i) for i in items])


def _annual_amount(item: ExpenseItem, year: int) -> float:
    """Return inflation-adjusted annual USD amount for a single item in plan year."""
    if not item.enabled:
        return 0.0
    if not (item.year_start <= year <= item.year_end):
        return 0.0
    years_elapsed = year - item.year_start
    factor = (1 + item.inflation / 100.0) ** years_elapsed
    if item.frequency == FREQ_MONTHLY:
        base = item.amount * 12
    elif item.frequency == FREQ_ANNUAL:
        base = item.amount
    else:  # one-time: only in year_start
        base = item.amount if year == item.year_start else 0.0
    return base * factor


def compute_annual_totals(items: List[ExpenseItem], n_years: int = 30) -> Dict[int, float]:
    """Return {plan_year: total_USD} for years 1..n_years."""
    return {
        yr: sum(_annual_amount(i, yr) for i in items)
        for yr in range(1, n_years + 1)
    }


# ─────────────────────────────────────────────────────────────────────────────
# Column indices for the expense table
# ─────────────────────────────────────────────────────────────────────────────
_COL_EN   = 0   # enabled checkbox
_COL_ID   = 1   # item #
_COL_NAME = 2   # expense name
_COL_AMT  = 3   # base amount (USD)
_COL_FREQ = 4   # frequency combo
_COL_INFL = 5   # inflation %
_COL_YS   = 6   # year start
_COL_YE   = 7   # year end
_COL_NOTE = 8   # notes
_N_COLS   = 9


# ─────────────────────────────────────────────────────────────────────────────
# Main dialog
# ─────────────────────────────────────────────────────────────────────────────

class NeedsAssessmentDialog(QDialog):
    """
    US Retirement Financial Needs Assessment.

    One tab per spending group (9 groups + Miscellaneous = 10 tabs).
    A Summary tab shows year-by-year projected totals across all enabled items.
    Ok → writes totals into state.annual_requirements (in USD).
    """

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.setWindowTitle("US Retirement Financial Needs Assessment")
        self.resize(1180, 760)
        self.state = state
        self._items: List[ExpenseItem] = _load_items_from_state(state)
        # Map item.id → row widgets so we can read back values quickly
        self._widgets: Dict[int, dict] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = QLabel(
            "<b>US Retirement Spending Needs Assessment</b> &mdash; "
            "51 expense categories covering housing, healthcare, transportation, "
            "leisure, and more.  "
            "Enable each item that applies, set the monthly/annual amount, "
            "inflation rate, and the plan years it is active.  "
            "Click <b>Preview Projection</b> to see inflation-adjusted year-by-year totals.  "
            "Click <b>OK</b> to write the totals into Annual Withdrawal Requirements."
        )
        hdr.setWordWrap(True)
        hdr.setStyleSheet(
            "background:#eaf4fb;padding:8px;border-radius:4px;"
            "color:#1a3a5c;font-size:12px;"
        )
        root.addWidget(hdr)

        # ── Quick-enable toolbar ──────────────────────────────────────────────
        tb = QHBoxLayout()
        btn_enable_all  = QPushButton("✓ Enable All")
        btn_disable_all = QPushButton("✗ Disable All")
        btn_enable_core = QPushButton("⚡ Enable Core Expenses")
        btn_preview     = QPushButton("📊 Preview Projection")
        btn_reset       = QPushButton("↺ Reset to Defaults")
        for btn, slot in [
            (btn_enable_all,  self._enable_all),
            (btn_disable_all, self._disable_all),
            (btn_enable_core, self._enable_core),
            (btn_preview,     self._show_projection),
            (btn_reset,       self._reset_defaults),
        ]:
            btn.clicked.connect(slot)
            tb.addWidget(btn)
        tb.addStretch()
        self._lbl_total = QLabel("Annual total (Year 1): $0")
        self._lbl_total.setStyleSheet("font-weight:bold;font-size:13px;color:#1a3a5c;")
        tb.addWidget(self._lbl_total)
        root.addLayout(tb)

        # ── Tabs ──────────────────────────────────────────────────────────────
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        root.addWidget(self._tabs, stretch=1)

        # One tab per group
        self._group_tables: Dict[str, QTableWidget] = {}
        for group in GROUPS:
            group_items = [i for i in self._items if i.group == group]
            tab_widget, tbl = self._build_group_tab(group, group_items)
            self._tabs.addTab(tab_widget, group)
            self._group_tables[group] = tbl

        # Summary tab
        self._summary_tab = self._build_summary_tab()
        self._tabs.addTab(self._summary_tab, "📊 Summary")

        # ── Buttons ───────────────────────────────────────────────────────────
        note = QLabel(
            "⚠ <b>USD amounts</b> are written directly to Annual Withdrawal "
            "Requirements. If the main planner uses Rs Lakhs, divide by 100,000 "
            "before entering, or configure AppState to use USD natively."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#8b4513;font-size:11px;background:#fff8dc;"
                           "padding:4px;border-radius:3px;")
        root.addWidget(note)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel |
            QDialogButtonBox.StandardButton.Apply
        )
        btns.accepted.connect(self._save_and_close)
        btns.rejected.connect(self.reject)
        btns.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(
            self._apply)
        root.addWidget(btns)

        self._refresh_total_label()

    # ── Tab builders ──────────────────────────────────────────────────────────

    def _build_group_tab(
        self, group: str, items: List[ExpenseItem]
    ) -> tuple[QWidget, QTableWidget]:
        """Build one tab for a spending group.  Returns (outer_widget, table)."""
        outer = QWidget()
        vbox  = QVBoxLayout(outer)
        vbox.setContentsMargins(4, 4, 4, 4)

        # Group description label
        desc = _GROUP_DESCRIPTIONS.get(group, "")
        if desc:
            lbl = QLabel(desc)
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color:#555;font-size:11px;margin-bottom:4px;")
            vbox.addWidget(lbl)

        tbl = QTableWidget(len(items), _N_COLS)
        tbl.setHorizontalHeaderLabels([
            "On", "#", "Expense Category",
            "Amount ($/mo or $/yr)", "Frequency",
            "Inflation %", "Yr Start", "Yr End", "Notes",
        ])
        hdr = tbl.horizontalHeader()
        hdr.setSectionResizeMode(_COL_EN,   QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(_COL_ID,   QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(_COL_NAME, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(_COL_AMT,  QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(_COL_FREQ, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(_COL_INFL, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(_COL_YS,   QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(_COL_YE,   QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(_COL_NOTE, QHeaderView.ResizeMode.Interactive)
        tbl.setColumnWidth(_COL_AMT,  140)
        tbl.setColumnWidth(_COL_NOTE, 200)
        tbl.setAlternatingRowColors(True)
        tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        tbl.verticalHeader().setVisible(False)
        tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        for row, item in enumerate(items):
            self._populate_row(tbl, row, item)
            # Highlight healthcare rows to signal higher inflation rate
            if item.group == "Healthcare":
                for col in range(_N_COLS):
                    cell = tbl.item(row, col)
                    if cell:
                        cell.setBackground(QColor("#fff8f0"))

        tbl.setRowHeight(0, 30)  # sets a minimum; all rows match
        for r in range(tbl.rowCount()):
            tbl.setRowHeight(r, 30)

        vbox.addWidget(tbl)
        return outer, tbl

    def _populate_row(self, tbl: QTableWidget, row: int, item: ExpenseItem) -> None:
        """Fill one table row with widgets for the given ExpenseItem."""
        # ── Col 0: enable checkbox ────────────────────────────────────────────
        chk = QCheckBox()
        chk.setChecked(item.enabled)
        chk.setToolTip("Enable this expense in the projection")
        chk_w = QWidget()
        chk_l = QHBoxLayout(chk_w)
        chk_l.addWidget(chk)
        chk_l.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chk_l.setContentsMargins(0, 0, 0, 0)
        tbl.setCellWidget(row, _COL_EN, chk_w)

        # ── Col 1: id ─────────────────────────────────────────────────────────
        id_item = QTableWidgetItem(str(item.id))
        id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        id_item.setFlags(id_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        id_item.setForeground(QColor("#888"))
        tbl.setItem(row, _COL_ID, id_item)

        # ── Col 2: name ───────────────────────────────────────────────────────
        name_item = QTableWidgetItem(item.name)
        name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        font = QFont()
        font.setBold(item.enabled)
        name_item.setFont(font)
        tbl.setItem(row, _COL_NAME, name_item)

        # ── Col 3: amount spinbox ─────────────────────────────────────────────
        amt = QDoubleSpinBox()
        amt.setRange(0, 999_999)
        amt.setDecimals(0)
        amt.setPrefix("$")
        amt.setSingleStep(50)
        amt.setValue(item.amount)
        amt.setToolTip(
            "Monthly amount when frequency = 'monthly';\n"
            "Annual amount when frequency = 'annual';\n"
            "Total one-time cost when frequency = 'one-time'."
        )
        tbl.setCellWidget(row, _COL_AMT, amt)

        # ── Col 4: frequency combo ────────────────────────────────────────────
        freq = QComboBox()
        freq.addItems(FREQUENCIES)
        freq.setCurrentText(item.frequency)
        tbl.setCellWidget(row, _COL_FREQ, freq)

        # ── Col 5: inflation spinbox ──────────────────────────────────────────
        infl = QDoubleSpinBox()
        infl.setRange(0, 20)
        infl.setDecimals(1)
        infl.setSuffix("%")
        infl.setSingleStep(0.5)
        infl.setValue(item.inflation)
        if item.inflation > DEFAULT_INFLATION:
            infl.setStyleSheet("background:#fff3e0;")
            infl.setToolTip(
                f"Healthcare inflation is set to {item.inflation}% (above the "
                f"general {DEFAULT_INFLATION}% default) per BLS historical data."
            )
        tbl.setCellWidget(row, _COL_INFL, infl)

        # ── Col 6: year start ─────────────────────────────────────────────────
        ys = QSpinBox()
        ys.setRange(1, 30)
        ys.setValue(item.year_start)
        ys.setToolTip("Plan year when this expense begins (1 = retirement start)")
        tbl.setCellWidget(row, _COL_YS, ys)

        # ── Col 7: year end ───────────────────────────────────────────────────
        ye = QSpinBox()
        ye.setRange(1, 30)
        ye.setValue(item.year_end)
        ye.setToolTip("Plan year when this expense ends (30 = end of plan)")
        tbl.setCellWidget(row, _COL_YE, ye)

        # ── Col 8: notes ──────────────────────────────────────────────────────
        notes = QLineEdit(item.notes)
        notes.setPlaceholderText("optional note…")
        tbl.setCellWidget(row, _COL_NOTE, notes)

        # ── Wire up the checkbox to bold the name ─────────────────────────────
        def _on_toggle(checked, r=row, t=tbl, n=name_item):
            f2 = QFont(); f2.setBold(checked)
            n.setFont(f2)
            self._refresh_total_label()

        chk.toggled.connect(_on_toggle)
        amt.valueChanged.connect(lambda _v: self._refresh_total_label())

        # ── Store widget references ───────────────────────────────────────────
        self._widgets[item.id] = {
            "chk": chk, "amt": amt, "freq": freq,
            "infl": infl, "ys": ys, "ye": ye, "notes": notes,
        }

    def _build_summary_tab(self) -> QWidget:
        """Summary tab with a 31-row table (header + years 1–30)."""
        outer = QWidget()
        vbox  = QVBoxLayout(outer)

        self._summary_table = QTableWidget(30, 5)
        self._summary_table.setHorizontalHeaderLabels([
            "Plan Year", "Annual Total (USD)",
            "Monthly Equiv.", "Cumulative (USD)", "vs. Year 1",
        ])
        self._summary_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self._summary_table.setAlternatingRowColors(True)
        self._summary_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._summary_table.verticalHeader().setVisible(False)

        vbox.addWidget(QLabel(
            "Inflation-adjusted projection for all <b>enabled</b> expense items. "
            "Click <b>Preview Projection</b> or open this tab to refresh."
        ))
        vbox.addWidget(self._summary_table)

        # Totals bar
        self._summary_total_lbl = QLabel("Enable items and click Preview to compute.")
        self._summary_total_lbl.setStyleSheet(
            "font-weight:bold;font-size:13px;color:#1a3a5c;"
            "background:#eaf4fb;padding:6px;border-radius:4px;"
        )
        vbox.addWidget(self._summary_total_lbl)
        return outer

    # ── Data collection ───────────────────────────────────────────────────────

    def _collect_items(self) -> List[ExpenseItem]:
        """Read widget values back into a fresh list of ExpenseItems."""
        result = []
        for item in self._items:
            w = self._widgets.get(item.id)
            if w is None:
                result.append(copy.deepcopy(item))
                continue
            result.append(ExpenseItem(
                id=item.id, name=item.name, group=item.group,
                amount=w["amt"].value(),
                frequency=w["freq"].currentText(),
                inflation=w["infl"].value(),
                year_start=w["ys"].value(),
                year_end=w["ye"].value(),
                notes=w["notes"].text(),
                enabled=w["chk"].isChecked(),
            ))
        return result

    # ── Projection helpers ────────────────────────────────────────────────────

    def _refresh_total_label(self):
        """Update the Year-1 total label in the toolbar."""
        items = self._collect_items()
        totals = compute_annual_totals(items, n_years=30)
        yr1 = totals.get(1, 0.0)
        enabled_count = sum(1 for i in items if i.enabled)
        self._lbl_total.setText(
            f"Annual total (Year 1): <b>${yr1:,.0f}</b>  "
            f"| {enabled_count} items enabled"
        )

    def _show_projection(self):
        """Populate the Summary tab with the full 30-year projection."""
        items  = self._collect_items()
        totals = compute_annual_totals(items, n_years=30)
        yr1    = totals.get(1, 0.0)
        cumul  = 0.0

        tbl = self._summary_table
        tbl.blockSignals(True)
        for row, yr in enumerate(range(1, 31)):
            annual = totals.get(yr, 0.0)
            monthly = annual / 12.0
            cumul += annual
            ratio = (annual / yr1) if yr1 > 0 else 1.0

            def _it(txt, align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter):
                it = QTableWidgetItem(txt)
                it.setTextAlignment(align)
                return it

            tbl.setItem(row, 0, _it(f"Year {yr:2d}", Qt.AlignmentFlag.AlignCenter))
            tbl.setItem(row, 1, _it(f"${annual:>12,.0f}"))
            tbl.setItem(row, 2, _it(f"${monthly:>10,.0f}"))
            tbl.setItem(row, 3, _it(f"${cumul:>14,.0f}"))
            ratio_item = _it(f"{ratio:.2f}×")
            if ratio > 2.0:
                ratio_item.setForeground(QColor("#c0392b"))
            elif ratio > 1.5:
                ratio_item.setForeground(QColor("#e67e22"))
            tbl.setItem(row, 4, ratio_item)

        tbl.blockSignals(False)

        yr30 = totals.get(30, 0.0)
        total_cumul = sum(totals.values())
        self._summary_total_lbl.setText(
            f"Year 1: ${yr1:,.0f}  |  "
            f"Year 30: ${yr30:,.0f}  |  "
            f"30-Year Total: ${total_cumul:,.0f}  |  "
            f"Year-30 / Year-1: {(yr30/yr1):.2f}×" if yr1 > 0 else
            "No expenses enabled."
        )

        # Switch to Summary tab
        self._tabs.setCurrentWidget(self._summary_tab)

    # ── Toolbar button handlers ───────────────────────────────────────────────

    def _enable_all(self):
        for w in self._widgets.values():
            w["chk"].setChecked(True)

    def _disable_all(self):
        for w in self._widgets.values():
            w["chk"].setChecked(False)

    def _enable_core(self):
        """Enable the ~20 core expenses most retirees carry."""
        CORE_IDS = {
            1, 2, 3, 4, 7, 8, 9,          # Housing: mortgage, tax, insurance, maintenance, utilities
            16, 17, 18, 19,                 # Transportation: car payment, insurance, fuel, maintenance
            23, 24,                         # Food: groceries, dining
            26, 27, 28,                     # Healthcare: Medicare, Rx, doctor visits
            36, 37, 38,                     # Personal care
            46,                             # Cell phone
            51,                             # Emergency reserve
        }
        for item in self._items:
            w = self._widgets.get(item.id)
            if w:
                w["chk"].setChecked(item.id in CORE_IDS)

    def _reset_defaults(self):
        reply = QMessageBox.question(
            self, "Reset to Defaults",
            "This will reset all amounts, frequencies, and inflation rates to "
            "BLS-based defaults and disable all items.  Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._items = _make_default_items()
        # Rebuild all tabs
        for i in range(self._tabs.count() - 1):  # -1 = skip Summary tab
            group = GROUPS[i]
            group_items = [x for x in self._items if x.group == group]
            tbl = self._group_tables[group]
            tbl.clearContents()
            tbl.setRowCount(len(group_items))
            for row, item in enumerate(group_items):
                self._populate_row(tbl, row, item)
                tbl.setRowHeight(row, 30)
        self._refresh_total_label()

    # ── Save / Apply ──────────────────────────────────────────────────────────

    def _apply(self):
        """Write annual totals into state without closing the dialog."""
        items  = self._collect_items()
        totals = compute_annual_totals(items, n_years=30)
        _save_items_to_state(self.state, items)
        self.state.annual_requirements = {yr: round(amt, 2) for yr, amt in totals.items()}
        self._show_projection()
        QMessageBox.information(
            self, "Applied",
            f"Needs assessment totals written to Annual Withdrawal Requirements.\n"
            f"Year 1: ${totals.get(1, 0):,.0f}  |  Year 30: ${totals.get(30, 0):,.0f}"
        )

    def _save_and_close(self):
        items = self._collect_items()
        # Validate: warn if year_end < year_start for any enabled item
        bad = [i for i in items if i.enabled and i.year_end < i.year_start]
        if bad:
            names = ", ".join(i.name for i in bad[:3])
            QMessageBox.warning(
                self, "Invalid Year Range",
                f"Some enabled items have Year End < Year Start:\n{names}\n"
                "Please correct before saving."
            )
            return
        totals = compute_annual_totals(items, n_years=30)
        _save_items_to_state(self.state, items)
        self.state.annual_requirements = {yr: round(amt, 2) for yr, amt in totals.items()}
        self.accept()


# ─────────────────────────────────────────────────────────────────────────────
# Group description strings shown at the top of each tab
# ─────────────────────────────────────────────────────────────────────────────
_GROUP_DESCRIPTIONS: Dict[str, str] = {
    "Housing": (
        "Housing is the largest single expense for most retirees (~35% of total spending, "
        "per BLS Consumer Expenditure Survey).  Include property taxes and maintenance even "
        "if the mortgage is paid off.  Accessibility modifications (ramp, grab bars, walk-in "
        "shower) typically cost $5,000–$30,000 as a one-time expense."
    ),
    "Transportation": (
        "Transportation averages ~15% of retiree spending.  If you plan to downsize to one "
        "car after retirement, disable the auto-loan row and reduce insurance accordingly.  "
        "Consider increasing rideshare as driving becomes less feasible in later years "
        "(years 20–30)."
    ),
    "Food & Daily Living": (
        "Food spending for retirees averages ~$550–700/month per BLS data.  Adjust dining "
        "out based on lifestyle.  Food delivery tends to increase in later years."
    ),
    "Healthcare": (
        "Healthcare is the fastest-inflating category (~5–6%/year vs. 3% general CPI).  "
        "Medicare Part B premium alone is $185+/month in 2025 and rises with inflation and "
        "IRMAA surcharges.  Long-term care is the largest unknown — Fidelity estimates "
        "~$315,000 in total healthcare costs per couple in retirement.  "
        "Consider using your HSA balance to cover qualified medical expenses tax-free."
    ),
    "Insurance & Protection": (
        "Life insurance needs typically decline in retirement once the mortgage is paid and "
        "dependents are independent.  Umbrella liability insurance (~$150–300/year for "
        "$1M coverage) is recommended for retirees with significant assets."
    ),
    "Personal Care": (
        "This category covers clothing, grooming, and household supplies.  Spending typically "
        "declines modestly in later retirement years."
    ),
    "Leisure & Lifestyle": (
        "Travel and leisure tend to be front-loaded in retirement (the 'go-go years', "
        "roughly ages 62–75) before declining.  Consider using Year End = 15 for heavy "
        "travel and reducing after that.  Charitable giving may increase in later years "
        "as the primary vehicle for estate planning (QCDs, DAF)."
    ),
    "Communications & Tech": (
        "Technology expenses are growing as retirees rely more on telehealth, smart-home "
        "devices, and video communication with family.  Plan for periodic device upgrades "
        "every 3–4 years."
    ),
    "Professional Services": (
        "Financial advisor fees (typically 0.5–1.0% AUM/year), tax preparation, and "
        "estate planning costs are deductible in some circumstances.  Legal/estate planning "
        "costs are typically front-loaded (years 1–5) and then periodic."
    ),
    "Miscellaneous": (
        "The emergency reserve bucket should cover 3–6 months of core expenses for "
        "unexpected events (appliance replacement, uninsured medical, family emergencies).  "
        "The BLS recommends budgeting ~5% of total spending as miscellaneous contingency."
    ),
}