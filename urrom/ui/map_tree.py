"""
urrom/ui/map_tree.py — Left-hand map navigator for the workbench layout.

Groups every editable map by chip and category, shows its confidence, and
filters live from a search box.  Emits ``mapActivated(chip, index)`` where
``chip`` is "main" or "boost" and ``index`` is the position in the list the
caller handed to :meth:`populate` — so the editor tabs can use it directly.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QFont
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel,
    QTreeWidget, QTreeWidgetItem, QAbstractItemView,
)

from urrom.ui.theme import FG, FG_DIM, ACCENT, confidence_colour


# Map-type → human category.  "raw" maps are sorted by their unit.
def _category(m) -> str:
    mt = (m.map_type or "").lower()
    unit = (m.unit or "").lower()
    name = m.name.lower()
    if mt == "fuel":
        return "Fuel"
    if mt == "ign":
        return "Ignition"
    if mt == "boost" or "kpa" in unit or "%dc" in unit or "wgdc" in name or "n75" in name or "boost" in name:
        return "Boost control"
    if "knock" in name or "kr " in name:
        return "Knock"
    if "idle" in name or "isv" in name:
        return "Idle"
    if "axis" in name:
        return "Axes"
    return "Other"


_CATEGORY_ORDER = ["Fuel", "Ignition", "Boost control", "Knock", "Idle", "Other", "Axes"]

_CONF_SHORT = {
    "CONFIRMED":   "✓",
    "PROVISIONAL": "~",
    "UNCONFIRMED": "?",
    "UNVERIFIED":  "✗",
}


class MapTree(QWidget):
    """Search box + tree of maps, grouped by chip and category."""

    mapActivated = pyqtSignal(str, int)   # chip ("main"|"boost"), index

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        top = QWidget()
        top_l = QHBoxLayout(top)
        top_l.setContentsMargins(6, 6, 6, 4)
        top_l.setSpacing(6)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter maps…  (name, address, unit)")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)
        top_l.addWidget(self._search)
        lay.addWidget(top)

        self._count_lbl = QLabel("")
        self._count_lbl.setStyleSheet(f"color:{FG_DIM};font-size:10px;padding:0 8px 4px;")
        lay.addWidget(self._count_lbl)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setColumnCount(2)
        self._tree.setIndentation(14)
        self._tree.setRootIsDecorated(True)
        self._tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tree.setUniformRowHeights(True)
        self._tree.header().setStretchLastSection(False)
        self._tree.header().setSectionResizeMode(0, self._tree.header().Stretch)
        self._tree.header().setSectionResizeMode(1, self._tree.header().ResizeToContents)
        self._tree.currentItemChanged.connect(self._on_current_changed)
        lay.addWidget(self._tree, 1)

        self._items: dict[tuple[str, int], QTreeWidgetItem] = {}
        self._main_maps = []
        self._boost_maps = []
        self._boost_loaded = False
        self._muted = False

    # ── Population ─────────────────────────────────────────────────────────

    def clear(self):
        self._tree.clear()
        self._items.clear()
        self._main_maps = []
        self._boost_maps = []
        self._count_lbl.setText("")

    def populate(self, main_maps: list, boost_maps: list, boost_loaded: bool):
        """
        Rebuild the tree.  ``main_maps`` / ``boost_maps`` must be the exact
        lists the editor tabs index into (MainChipTab._maps, BoostTab._maps).
        """
        self._muted = True
        self._tree.clear()
        self._items.clear()
        self._main_maps = list(main_maps)
        self._boost_maps = list(boost_maps)
        self._boost_loaded = boost_loaded

        bold = QFont()
        bold.setBold(True)

        def _chip_node(title: str, subtitle: str = "") -> QTreeWidgetItem:
            it = QTreeWidgetItem([title, subtitle])
            it.setFont(0, bold)
            it.setForeground(0, QBrush(QColor(FG)))
            it.setForeground(1, QBrush(QColor(FG_DIM)))
            it.setFlags(Qt.ItemIsEnabled)
            it.setData(0, Qt.UserRole, None)
            self._tree.addTopLevelItem(it)
            it.setExpanded(True)
            return it

        def _add_maps(parent: QTreeWidgetItem, chip: str, maps: list, enabled: bool):
            groups: dict[str, list[tuple[int, object]]] = {}
            for i, m in enumerate(maps):
                groups.setdefault(_category(m), []).append((i, m))
            for cat in _CATEGORY_ORDER:
                if cat not in groups:
                    continue
                entries = groups[cat]
                cat_item = QTreeWidgetItem([cat, str(len(entries))])
                cat_item.setForeground(0, QBrush(QColor(FG_DIM)))
                cat_item.setForeground(1, QBrush(QColor(FG_DIM)))
                cat_item.setFlags(Qt.ItemIsEnabled)
                cat_item.setData(0, Qt.UserRole, None)
                parent.addChild(cat_item)
                cat_item.setExpanded(cat in ("Fuel", "Ignition", "Boost control"))
                for i, m in entries:
                    it = QTreeWidgetItem([m.name, _CONF_SHORT.get(m.confidence, "?")])
                    it.setData(0, Qt.UserRole, (chip, i))
                    it.setToolTip(0, self._tooltip(m))
                    it.setToolTip(1, m.confidence)
                    it.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
                    it.setForeground(1, QBrush(QColor(confidence_colour(m.confidence))))
                    if m.confidence in ("UNVERIFIED", "UNCONFIRMED"):
                        it.setForeground(0, QBrush(QColor(FG_DIM)))
                    else:
                        it.setForeground(0, QBrush(QColor(FG)))
                    if enabled:
                        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                    else:
                        it.setFlags(Qt.NoItemFlags)
                    cat_item.addChild(it)
                    self._items[(chip, i)] = it

        def _n(k: int) -> str:
            return f"{k} map" if k == 1 else f"{k} maps"

        if self._main_maps:
            main_node = _chip_node("Main chip", _n(len(self._main_maps)))
            _add_maps(main_node, "main", self._main_maps, True)

        if self._boost_maps:
            sub = _n(len(self._boost_maps)) if boost_loaded else "not loaded"
            boost_node = _chip_node("Boost chip", sub)
            _add_maps(boost_node, "boost", self._boost_maps, boost_loaded)
            boost_node.setExpanded(boost_loaded)
        elif self._main_maps:
            boost_node = _chip_node("Boost chip", "no maps for this variant")
            boost_node.setExpanded(False)

        self._update_count()
        self._muted = False
        self._apply_filter(self._search.text())

    @staticmethod
    def _tooltip(m) -> str:
        return (f"{m.name}\n"
                f"WH 0x{m.main_addr:04X}  ·  {m.rows}×{m.cols}  ·  {m.unit or 'raw'}\n"
                f"{m.confidence}\n\n{m.description}")

    def _update_count(self):
        n = len(self._main_maps) + (len(self._boost_maps) if self._boost_loaded else 0)
        self._count_lbl.setText(f"{n} editable maps" if n else "")

    # ── Selection ──────────────────────────────────────────────────────────

    def select(self, chip: str, index: int):
        """Programmatically highlight a map without re-emitting mapActivated."""
        it = self._items.get((chip, index))
        if it is None:
            return
        self._muted = True
        self._tree.setCurrentItem(it)
        self._tree.scrollToItem(it)
        self._muted = False

    def _on_current_changed(self, cur: QTreeWidgetItem, _prev):
        if self._muted or cur is None:
            return
        data = cur.data(0, Qt.UserRole)
        if not data:
            return
        chip, idx = data
        self.mapActivated.emit(chip, idx)

    # ── Filtering ──────────────────────────────────────────────────────────

    def _apply_filter(self, text: str):
        text = (text or "").strip().lower()
        for (chip, idx), it in self._items.items():
            m = (self._main_maps if chip == "main" else self._boost_maps)[idx]
            match = (not text
                     or text in m.name.lower()
                     or text in f"0x{m.main_addr:04x}"
                     or text in (m.unit or "").lower()
                     or text in (m.map_type or "").lower()
                     or text in m.confidence.lower())
            it.setHidden(not match)
        # Hide empty category nodes, expand everything when filtering
        for t in range(self._tree.topLevelItemCount()):
            chip_node = self._tree.topLevelItem(t)
            any_visible = False
            for c in range(chip_node.childCount()):
                cat = chip_node.child(c)
                vis = any(not cat.child(k).isHidden() for k in range(cat.childCount()))
                cat.setHidden(not vis)
                if vis and text:
                    cat.setExpanded(True)
                any_visible = any_visible or vis
            if text:
                chip_node.setHidden(not any_visible)
            else:
                chip_node.setHidden(False)
        self._search.setStyleSheet(
            f"border-color:{ACCENT};" if text else "")

    def focus_search(self):
        self._search.setFocus()
        self._search.selectAll()
