"""
urrom/ui/health_panel.py — Tuning health scan results as an inspector panel.

Replaces the modal scan-results dialog.  MainWindow runs the checks and calls
:meth:`set_issues`; double-clicking a row emits ``issueActivated`` so the
editor can jump to the offending cell.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
)

from urrom.ui.theme import BG, BG2, BORDER, FG, FG_DIM, GREEN, AMBER, RED


_SEV_COLOURS = {"error": RED, "warning": AMBER, "info": FG_DIM}
_SEV_SYMBOL  = {"error": "✗", "warning": "⚠", "info": "ℹ"}


class HealthPanel(QWidget):
    issueActivated = pyqtSignal(object)   # TuningIssue
    rescanRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        hdr = QHBoxLayout()
        self._summary = QLabel("Load a ROM to run the health scan.")
        self._summary.setTextFormat(Qt.RichText)
        self._summary.setStyleSheet(f"font-size:11px;color:{FG_DIM};")
        self._summary.setWordWrap(True)
        hdr.addWidget(self._summary, 1)
        self._rescan_btn = QPushButton("Rescan")
        self._rescan_btn.setFixedHeight(24)
        self._rescan_btn.setEnabled(False)
        self._rescan_btn.clicked.connect(self.rescanRequested.emit)
        hdr.addWidget(self._rescan_btn)
        lay.addLayout(hdr)

        self._tbl = QTableWidget(0, 3)
        self._tbl.setHorizontalHeaderLabels(["", "Map", "Issue"])
        hh = self._tbl.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        self._tbl.verticalHeader().setVisible(False)
        self._tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tbl.setAlternatingRowColors(True)
        self._tbl.setWordWrap(True)
        self._tbl.setStyleSheet(
            f"QTableWidget{{background:{BG2};gridline-color:{BORDER};}}"
            f"QTableWidget::item:alternate{{background:{BG};}}")
        self._tbl.cellDoubleClicked.connect(self._on_dbl)
        lay.addWidget(self._tbl, 1)

        hint = QLabel("Double-click a row to jump to that cell.")
        hint.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
        lay.addWidget(hint)

        self._issues = []

    def clear(self):
        self._issues = []
        self._tbl.setRowCount(0)
        self._summary.setText("Load a ROM to run the health scan.")
        self._summary.setStyleSheet(f"font-size:11px;color:{FG_DIM};")
        self._rescan_btn.setEnabled(False)

    def set_issues(self, issues: list, variant_name: str = ""):
        self._issues = list(issues)
        self._rescan_btn.setEnabled(True)
        n_err  = sum(1 for i in issues if i.severity == "error")
        n_warn = sum(1 for i in issues if i.severity == "warning")
        n_info = sum(1 for i in issues if i.severity == "info")
        head_col = RED if n_err else (AMBER if n_warn else GREEN)
        head_txt = (f"✗ {n_err} error{'s' if n_err != 1 else ''}" if n_err
                    else f"⚠ {n_warn} warning{'s' if n_warn != 1 else ''}" if n_warn
                    else "✓ clean")
        self._summary.setText(
            f"<b style='color:{head_col};'>{head_txt}</b>"
            f"&nbsp;&nbsp;<span style='color:{FG_DIM};'>"
            f"{n_err} err · {n_warn} warn · {n_info} info</span>")
        self._summary.setStyleSheet("font-size:11px;")

        self._tbl.setRowCount(len(issues))
        for r, iss in enumerate(issues):
            col = _SEV_COLOURS.get(iss.severity, FG)
            sev = QTableWidgetItem(_SEV_SYMBOL.get(iss.severity, "?"))
            sev.setForeground(QBrush(QColor(col)))
            sev.setTextAlignment(Qt.AlignCenter)
            sev.setToolTip(iss.severity)
            sev.setData(Qt.UserRole, iss)
            cell_s = ""
            if iss.cell and iss.cell[1] is not None:
                cell_s = f"  [{iss.cell[0]},{iss.cell[1]}]"
            mp = QTableWidgetItem(f"{iss.map_name}{cell_s}")
            mp.setToolTip(iss.map_name)
            desc = QTableWidgetItem(iss.description)
            desc.setToolTip(iss.description)
            self._tbl.setItem(r, 0, sev)
            self._tbl.setItem(r, 1, mp)
            self._tbl.setItem(r, 2, desc)
        self._tbl.resizeRowsToContents()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        # Issue column stretches with the panel; re-flow wrapped rows
        self._tbl.resizeRowsToContents()

    def _on_dbl(self, row, _col):
        it = self._tbl.item(row, 0)
        if it is None:
            return
        iss = it.data(Qt.UserRole)
        if iss is not None:
            self.issueActivated.emit(iss)
