"""
UrROM — Desktop GUI
PyQt5 ROM editor for Bosch Motronic M2.3 / M2.3.2 ECUs.
Dual-EPROM support: main chip (fuel + ignition) + boost chip.
"""

import sys
import copy
from pathlib import Path

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget,
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFileDialog,
    QTableWidget, QTableWidgetItem, QStatusBar, QMessageBox,
    QSplitter, QAction, QHeaderView, QFrame, QComboBox,
    QStyledItemDelegate, QScrollArea, QSizePolicy,
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QFont, QBrush, QPainter, QPen

sys.path.insert(0, str(Path(__file__).parent.parent))
from urrom.version import APP_VERSION, APP_NAME, WINDOW_TITLE
from urrom.ecu_profiles import (
    normalize_rom, detect_rom, read_map, read_map_decoded,
    write_map, apply_checksum, DetectionResult, ROMVariant, MapDef,
    fuel_encode, ign_encode, ign_encode_3b,
    get_axes,
    MAIN_CHIP_WORKING, MAIN_CHIP_PHYSICAL,
    ALL_VARIANTS,
)

# ── Palette ──────────────────────────────────────────────────────────────────

BG       = "#1e1e1e"
BG2      = "#252526"
BG3      = "#2d2d2d"
BORDER   = "#3c3c3c"
FG       = "#d4d4d4"
FG_DIM   = "#666666"
ACCENT   = "#569cd6"
GREEN    = "#2dff6e"
AMBER    = "#ff9900"
RED      = "#ff4444"
CHANGED  = "#2dff6e"


# ── Colour helpers ────────────────────────────────────────────────────────────

def _heat(value: float, vmin: float, vmax: float) -> QColor:
    t = max(0.0, min(1.0, (value - vmin) / max(1, vmax - vmin)))
    if t < 0.25:
        u = t / 0.25
        return QColor(0, int(u * 80), int(100 + u * 100))
    elif t < 0.5:
        u = (t - 0.25) / 0.25
        return QColor(0, int(80 + u * 120), int(200 - u * 200))
    elif t < 0.75:
        u = (t - 0.5) / 0.25
        return QColor(int(u * 220), 200, 0)
    else:
        u = (t - 0.75) / 0.25
        return QColor(220, int(200 - u * 200), 0)

def _fuel_colour(raw: int) -> QColor:
    # 128 = stoich (neutral blue-ish); <128 lean (cool); >128 rich (warm)
    return _heat(raw, 100, 200)

def _ign_colour(deg: float) -> QColor:
    # -10° (cold) to 40° (hot)
    return _heat(deg, -10, 42)

def _text_colour(bg: QColor) -> QColor:
    lum = bg.red() * 0.299 + bg.green() * 0.587 + bg.blue() * 0.114
    return QColor("#111") if lum > 130 else QColor("#eee")


# ── Changed-cell delegate ─────────────────────────────────────────────────────

class ChangedCellDelegate(QStyledItemDelegate):
    BORDER_COLOUR = QColor(CHANGED)
    BORDER_WIDTH  = 2

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        if index.data(Qt.UserRole) == "changed":
            painter.save()
            pen = QPen(self.BORDER_COLOUR, self.BORDER_WIDTH)
            painter.setPen(pen)
            r = option.rect.adjusted(1, 1, -1, -1)
            painter.drawRect(r)
            painter.restore()


# ── App stylesheet ────────────────────────────────────────────────────────────

STYLESHEET = f"""
QMainWindow, QWidget {{
    background: {BG};
    color: {FG};
    font-family: "Segoe UI", "SF Pro Display", sans-serif;
    font-size: 12px;
}}
QTabWidget::pane {{
    border: 1px solid {BORDER};
    background: {BG2};
}}
QTabBar::tab {{
    background: {BG};
    color: {FG_DIM};
    padding: 6px 14px;
    border: 1px solid {BORDER};
    border-bottom: none;
    margin-right: 2px;
    font-size: 11px;
}}
QTabBar::tab:selected {{
    background: {BG2};
    color: {FG};
    border-bottom: 2px solid {ACCENT};
}}
QPushButton {{
    background: {BG3};
    color: {FG};
    border: 1px solid {BORDER};
    padding: 5px 14px;
    border-radius: 3px;
    font-size: 11px;
}}
QPushButton:hover {{ background: #3a3a3a; border-color: {ACCENT}; }}
QPushButton:pressed {{ background: #444; }}
QPushButton:disabled {{ color: {FG_DIM}; }}
QComboBox {{
    background: {BG3};
    color: {FG};
    border: 1px solid {BORDER};
    padding: 3px 8px;
    border-radius: 3px;
    min-width: 220px;
}}
QComboBox QAbstractItemView {{
    background: {BG2};
    color: {FG};
    border: 1px solid {BORDER};
    selection-background-color: #3a3a3a;
}}
QTableWidget {{
    background: {BG2};
    gridline-color: {BORDER};
    font-size: 11px;
    border: 1px solid {BORDER};
}}
QHeaderView::section {{
    background: {BG3};
    color: {FG_DIM};
    font-size: 10px;
    padding: 2px 4px;
    border: 1px solid {BORDER};
}}
QStatusBar {{ background: {BG}; color: {FG_DIM}; font-size: 11px; }}
QLabel {{ color: {FG}; }}
QFrame {{ color: {BORDER}; }}
QScrollBar:vertical {{
    background: {BG};
    width: 8px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 4px;
}}
"""


# ── Info strip (top of window) ────────────────────────────────────────────────

class InfoStrip(QFrame):
    """
    Horizontal bar showing currently loaded ROM info:
    variant badge  |  build number  |  checksum status  |  confidence
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(38)
        self.setStyleSheet(
            f"background: {BG3}; border-bottom: 1px solid {BORDER};")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(16)

        self._variant_lbl = QLabel("No ROM loaded")
        self._variant_lbl.setStyleSheet(f"color: {FG_DIM}; font-size: 12px;")

        self._build_lbl = QLabel("")
        self._build_lbl.setStyleSheet(f"color: {FG_DIM}; font-size: 11px;")

        self._cs_lbl = QLabel("")
        self._cs_lbl.setStyleSheet(f"color: {FG_DIM}; font-size: 11px;")

        self._conf_lbl = QLabel("")
        self._conf_lbl.setStyleSheet(f"color: {FG_DIM}; font-size: 11px;")

        layout.addWidget(self._variant_lbl)
        layout.addWidget(self._build_lbl)
        layout.addWidget(self._cs_lbl)
        layout.addWidget(self._conf_lbl)
        layout.addStretch()

    def update(self, det: DetectionResult | None, boost_loaded: bool = False):
        if det is None:
            self._variant_lbl.setText("No ROM loaded")
            self._variant_lbl.setStyleSheet(f"color: {FG_DIM}; font-size: 12px;")
            self._build_lbl.setText("")
            self._cs_lbl.setText("")
            self._conf_lbl.setText("")
            return

        # Variant
        name = det.variant.name if det.variant else "Unknown M2.3"
        self._variant_lbl.setText(name)
        self._variant_lbl.setStyleSheet(f"color: {FG}; font-weight: bold; font-size: 12px;")

        # Build
        self._build_lbl.setText(f"build 0x{det.build_number:04X}")
        self._build_lbl.setStyleSheet(f"color: {FG_DIM}; font-size: 11px;")

        # Checksum
        if det.checksum_ok:
            self._cs_lbl.setText("✓ checksum")
            self._cs_lbl.setStyleSheet(f"color: {GREEN}; font-size: 11px;")
        else:
            self._cs_lbl.setText("⚠ checksum unverified")
            self._cs_lbl.setStyleSheet(f"color: {AMBER}; font-size: 11px;")

        # Confidence
        colour = GREEN if det.confidence == "HIGH" else (AMBER if det.confidence == "MEDIUM" else RED)
        self._conf_lbl.setText(f"{det.confidence} confidence")
        self._conf_lbl.setStyleSheet(f"color: {colour}; font-size: 11px;")


# ── Overview tab ──────────────────────────────────────────────────────────────

class OverviewTab(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # Detection card
        self._card = QFrame()
        self._card.setStyleSheet(
            f"background: {BG3}; border: 1px solid {BORDER}; border-radius: 4px;")
        card_layout = QVBoxLayout(self._card)
        card_layout.setSpacing(6)
        self._title   = QLabel("No ROM loaded")
        self._title.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {FG};")
        self._detail  = QLabel("")
        self._detail.setStyleSheet(f"color: {FG_DIM}; font-size: 11px;")
        self._detail.setWordWrap(True)
        card_layout.addWidget(self._title)
        card_layout.addWidget(self._detail)
        layout.addWidget(self._card)

        # Variant info
        self._info = QLabel("")
        self._info.setStyleSheet(f"color: {FG_DIM}; font-size: 11px;")
        self._info.setWordWrap(True)
        layout.addWidget(self._info)

        # Map inventory table
        map_title = QLabel("Map inventory")
        map_title.setStyleSheet(f"color: {FG_DIM}; font-size: 10px; text-transform: uppercase;")
        layout.addWidget(map_title)

        self._map_table = QTableWidget(0, 4)
        self._map_table.setHorizontalHeaderLabels(["Map", "Address", "Size", "Confidence"])
        self._map_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._map_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._map_table.setAlternatingRowColors(True)
        self._map_table.setStyleSheet(
            f"alternate-background-color: {BG}; background: {BG2};")
        layout.addWidget(self._map_table)

        layout.addStretch()

    def update(self, det: DetectionResult | None, boost_det: DetectionResult | None = None):
        if det is None:
            self._title.setText("No ROM loaded")
            self._detail.setText("")
            self._info.setText("")
            self._map_table.setRowCount(0)
            return

        v = det.variant
        conf_colour = GREEN if det.confidence == "HIGH" else (AMBER if det.confidence == "MEDIUM" else RED)

        self._title.setText(v.name if v else "Unknown M2.3 / M2.3.2 variant")
        self._detail.setText(
            f"Detected: {det.method}  •  "
            f"<span style='color:{conf_colour}'>{det.confidence}</span>  •  "
            f"CRC32 0x{det.crc32:08X}  •  build 0x{det.build_number:04X}"
        )
        self._detail.setTextFormat(Qt.RichText)

        if v:
            notes = v.notes or ""
            engine_str = ", ".join(v.engine_codes)
            pn_str = ", ".join(v.ecu_pns[:3])
            self._info.setText(
                f"Engine codes: {engine_str}  •  ECU PNs: {pn_str}\n{notes}"
            )
        else:
            self._info.setText("\n".join(det.warnings))

        # Map inventory
        maps = v.all_maps if v else []
        self._map_table.setRowCount(len(maps))
        for row, m in enumerate(maps):
            conf_str = m.confidence
            conf_col = (GREEN if conf_str == "CONFIRMED"
                        else AMBER if conf_str == "PROVISIONAL"
                        else RED)
            items = [
                QTableWidgetItem(m.name),
                QTableWidgetItem(f"0x{m.main_addr:04X}"),
                QTableWidgetItem(f"{m.rows}×{m.cols}"),
                QTableWidgetItem(conf_str),
            ]
            items[3].setForeground(QBrush(QColor(conf_col)))
            for col, item in enumerate(items):
                item.setFlags(Qt.ItemIsEnabled)
                self._map_table.setItem(row, col, item)


# ── Map editor table ──────────────────────────────────────────────────────────

class MapTable(QTableWidget):
    """
    16×16 (or N×M) heat-mapped editable table for a single ROM map.
    Rows = RPM axis (index 0 = lowest RPM at bottom, displayed inverted).
    Cols = Load axis (index 0 = lowest load at left).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._map_def: MapDef | None = None
        self._rom: bytearray | None = None
        self._original_raw: list[list[int]] = []
        self._current_raw:  list[list[int]] = []
        self._rpm_axis:  list = []
        self._load_axis: list = []
        self._is_ign = False
        self._is_fuel = False

        self.setItemDelegate(ChangedCellDelegate(self))
        self.setEditTriggers(QTableWidget.DoubleClicked | QTableWidget.AnyKeyPressed)
        self.itemChanged.connect(self._on_cell_changed)
        self._loading = False

    def load(self, rom: bytearray, map_def: MapDef,
             rpm_axis: list | None = None, load_axis: list | None = None):
        self._loading = True
        self._rom = rom
        self._map_def = map_def
        self._is_ign  = map_def.map_type == "ign"
        self._is_fuel = map_def.map_type == "fuel"
        self._rpm_axis  = rpm_axis  or list(range(map_def.rows))
        self._load_axis = load_axis or list(range(map_def.cols))

        raw = read_map(bytes(rom), map_def)
        self._original_raw = copy.deepcopy(raw)
        self._current_raw  = copy.deepcopy(raw)

        self.setRowCount(map_def.rows)
        self.setColumnCount(map_def.cols)

        # Axis labels — RPM on rows (inverted: row 0 = highest RPM), load on cols
        rpm_labels  = [str(r) for r in reversed(self._rpm_axis)]
        load_labels = [str(l) for l in self._load_axis]
        self.setVerticalHeaderLabels(rpm_labels)
        self.setHorizontalHeaderLabels(load_labels)

        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)

        self._redraw()
        self._loading = False

    def _redraw(self):
        self._loading = True
        raw   = self._current_raw
        orig  = self._original_raw
        nrows = self._map_def.rows
        ncols = self._map_def.cols
        decode = self._map_def.decode

        # Pre-compute global min/max for consistent heat scale
        all_vals = [raw[r][c] for r in range(nrows) for c in range(ncols)]
        vmin, vmax = min(all_vals), max(all_vals)

        for r in range(nrows):
            # Display inverted — lowest RPM at bottom
            disp_r = nrows - 1 - r
            for c in range(ncols):
                cell_raw = raw[r][c]
                changed  = cell_raw != orig[r][c]

                if decode:
                    val = decode(cell_raw)
                    text = f"{val:.1f}" if isinstance(val, float) else str(val)
                else:
                    val = cell_raw
                    text = str(cell_raw)

                if self._is_ign and decode:
                    bg = _ign_colour(val)
                elif self._is_fuel:
                    bg = _fuel_colour(cell_raw)
                else:
                    bg = _heat(cell_raw, vmin, vmax)

                fg = _text_colour(bg)
                item = QTableWidgetItem(text)
                item.setBackground(QBrush(bg))
                item.setForeground(QBrush(fg))
                item.setTextAlignment(Qt.AlignCenter)
                if changed:
                    item.setData(Qt.UserRole, "changed")
                else:
                    item.setData(Qt.UserRole, None)
                item.setData(Qt.UserRole + 1, (r, c))  # store logical row,col

                self.setItem(disp_r, c, item)

        self._loading = False

    def _on_cell_changed(self, item: QTableWidgetItem):
        if self._loading or self._map_def is None:
            return
        try:
            text = item.text().strip()
            val  = float(text)
        except ValueError:
            # Revert
            r_log, c_log = item.data(Qt.UserRole + 1)
            raw = self._current_raw[r_log][c_log]
            item.setText(str(raw))
            return

        r_log, c_log = item.data(Qt.UserRole + 1)
        encode = self._map_def.encode
        if encode:
            raw_byte = encode(val)
        else:
            raw_byte = max(0, min(255, int(round(val))))

        self._current_raw[r_log][c_log] = raw_byte

        # Update colour/changed marker
        self._loading = True
        orig = self._original_raw[r_log][c_log]
        changed = raw_byte != orig
        decode = self._map_def.decode
        display_val = decode(raw_byte) if decode else raw_byte
        display_text = f"{display_val:.1f}" if isinstance(display_val, float) else str(display_val)
        item.setText(display_text)
        if self._is_ign and decode:
            bg = _ign_colour(display_val)
        elif self._is_fuel:
            bg = _fuel_colour(raw_byte)
        else:
            all_vals = [self._current_raw[r][c]
                        for r in range(self._map_def.rows)
                        for c in range(self._map_def.cols)]
            bg = _heat(raw_byte, min(all_vals), max(all_vals))
        item.setBackground(QBrush(bg))
        item.setForeground(QBrush(_text_colour(bg)))
        item.setData(Qt.UserRole, "changed" if changed else None)
        self._loading = False

    def commit_to_rom(self, rom: bytearray) -> bytearray:
        """Write current edits back into a ROM bytearray."""
        if self._map_def is None:
            return rom
        return write_map(rom, self._map_def, self._current_raw)

    def has_changes(self) -> bool:
        if not self._original_raw or not self._current_raw:
            return False
        return self._original_raw != self._current_raw

    def revert(self):
        self._current_raw = copy.deepcopy(self._original_raw)
        self._redraw()

    def accept_current_as_baseline(self):
        self._original_raw = copy.deepcopy(self._current_raw)
        self._redraw()


# ── Main chip maps tab ────────────────────────────────────────────────────────

class MainChipTab(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._variant: ROMVariant | None = None
        self._rom: bytearray | None = None
        self._maps: list[MapDef] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Toolbar row
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self._map_combo = QComboBox()
        self._map_combo.currentIndexChanged.connect(self._on_map_selected)
        toolbar.addWidget(QLabel("Map:"))
        toolbar.addWidget(self._map_combo)

        self._conf_badge = QLabel("")
        self._conf_badge.setStyleSheet(
            f"font-size: 10px; padding: 2px 6px; border-radius: 3px; "
            f"background: {BG3}; border: 1px solid {BORDER};")
        toolbar.addWidget(self._conf_badge)

        toolbar.addStretch()

        self._revert_btn = QPushButton("Revert changes")
        self._revert_btn.setEnabled(False)
        self._revert_btn.clicked.connect(self._on_revert)
        toolbar.addWidget(self._revert_btn)

        layout.addLayout(toolbar)

        # Map description
        self._desc_lbl = QLabel("")
        self._desc_lbl.setStyleSheet(f"color: {FG_DIM}; font-size: 10px;")
        self._desc_lbl.setWordWrap(True)
        layout.addWidget(self._desc_lbl)

        # Map table
        self._table = MapTable()
        layout.addWidget(self._table)

        # Status row
        status_row = QHBoxLayout()
        self._addr_lbl = QLabel("")
        self._addr_lbl.setStyleSheet(f"color: {FG_DIM}; font-size: 10px;")
        self._size_lbl = QLabel("")
        self._size_lbl.setStyleSheet(f"color: {FG_DIM}; font-size: 10px;")
        status_row.addWidget(self._addr_lbl)
        status_row.addStretch()
        status_row.addWidget(self._size_lbl)
        layout.addLayout(status_row)

    def load(self, rom: bytearray, variant: ROMVariant):
        self._variant = variant
        self._rom = rom
        # Show fuel + ign maps (not boost, not unconfirmed rev limit)
        self._maps = [m for m in variant.main_maps
                      if m.map_type in ("fuel", "ign") and m.rows > 1]

        self._map_combo.blockSignals(True)
        self._map_combo.clear()
        for m in self._maps:
            label = f"{m.name}  [{m.rows}×{m.cols}  {m.unit}]"
            self._map_combo.addItem(label)
        self._map_combo.blockSignals(False)

        if self._maps:
            self._map_combo.setCurrentIndex(0)
            self._on_map_selected(0)

    def clear(self):
        self._variant = None
        self._rom = None
        self._maps = []
        self._map_combo.clear()
        self._desc_lbl.setText("")
        self._addr_lbl.setText("")
        self._size_lbl.setText("")
        self._conf_badge.setText("")
        self._revert_btn.setEnabled(False)

    def _on_map_selected(self, idx: int):
        if not self._maps or idx < 0 or idx >= len(self._maps):
            return
        m = self._maps[idx]
        v = self._variant

        # Determine axes via ecu_profiles helper
        from urrom.ecu_profiles import get_axes
        rpm_axis, load_axis = get_axes(bytes(self._rom), m, v)

        self._table.load(self._rom, m, rpm_axis, load_axis)
        # Re-wire the signal each time a new map is loaded
        try:
            self._table.itemChanged.disconnect()
        except TypeError:
            pass
        self._table.itemChanged.connect(lambda _: self.on_table_changed())
        self._desc_lbl.setText(m.description)
        self._addr_lbl.setText(
            f"Address: 0x{m.main_addr:04X}  (working half offset)")
        self._size_lbl.setText(f"{m.rows}×{m.cols} = {m.size} bytes")

        conf = m.confidence
        conf_colour = (GREEN if conf == "CONFIRMED"
                       else AMBER if conf == "PROVISIONAL" else RED)
        self._conf_badge.setText(conf)
        self._conf_badge.setStyleSheet(
            f"font-size: 10px; padding: 2px 6px; border-radius: 3px; "
            f"color: {conf_colour}; background: {BG3}; border: 1px solid {conf_colour};")
        self._revert_btn.setEnabled(False)

    def _on_revert(self):
        self._table.revert()
        self._revert_btn.setEnabled(False)

    def commit_to_rom(self, rom: bytearray) -> bytearray:
        return self._table.commit_to_rom(rom)

    def has_changes(self) -> bool:
        return self._table.has_changes()

    def on_table_changed(self):
        self._revert_btn.setEnabled(self._table.has_changes())
        # Notify parent window to update title
        p = self.parent()
        while p is not None:
            if hasattr(p, "_mark_dirty"):
                p._mark_dirty()
                break
            p = p.parent()


# ── Boost chip tab ────────────────────────────────────────────────────────────

class BoostTab(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        self._status = QLabel("No boost chip loaded")
        self._status.setStyleSheet(f"color: {FG_DIM}; font-size: 12px;")
        layout.addWidget(self._status)

        self._note = QLabel(
            "Boost chip maps (boost target, N75 duty cycle, knock threshold) "
            "are displayed here once a boost chip file is loaded.\n\n"
            "Boost chip addresses are currently PROVISIONAL — load a known-good "
            "chip file and verify map data before editing.")
        self._note.setStyleSheet(f"color: {FG_DIM}; font-size: 11px;")
        self._note.setWordWrap(True)
        layout.addWidget(self._note)

        self._table = MapTable()
        self._table.setVisible(False)
        layout.addWidget(self._table)

        layout.addStretch()

    def load(self, boost_rom: bytearray, variant):
        boost_maps = [m for m in variant.boost_maps if m.rows > 1]
        if not boost_maps:
            self._status.setText("No confirmed boost chip maps for this variant")
            self._table.setVisible(False)
            return
        # Load the first boost map for now
        m = boost_maps[0]
        self._table.load(boost_rom, m)
        self._table.setVisible(True)
        self._status.setText(
            f"Boost chip loaded  —  showing: {m.name}  [{m.confidence}]")

    def clear(self):
        self._status.setText("No boost chip loaded")
        self._table.setVisible(False)


# ── Compare tab ───────────────────────────────────────────────────────────────

class CompareTab(QWidget):
    """
    Side-by-side ROM diff.
    Left = ROM A (the currently loaded main chip).
    Right = ROM B (a second file loaded independently for comparison).
    Delta column shows B - A per cell; changed cells highlighted.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rom_a    = None
        self._rom_b    = None
        self._variant  = None
        self._maps     = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Toolbar
        tb = QHBoxLayout()
        tb.setSpacing(8)
        self._map_combo = QComboBox()
        self._map_combo.currentIndexChanged.connect(self._refresh)
        tb.addWidget(QLabel("Map:"))
        tb.addWidget(self._map_combo)
        tb.addStretch()
        self._load_b_btn = QPushButton("Load ROM B for compare\u2026")
        self._load_b_btn.clicked.connect(self._on_load_b)
        self._load_b_btn.setEnabled(False)
        tb.addWidget(self._load_b_btn)
        layout.addLayout(tb)

        self._status = QLabel("Load a ROM A first, then load ROM B to compare.")
        self._status.setStyleSheet(f"color: {FG_DIM}; font-size: 11px;")
        layout.addWidget(self._status)

        splitter = QSplitter(Qt.Horizontal)

        def _panel(label_text, colour):
            w = QWidget()
            lay = QVBoxLayout(w)
            lay.setContentsMargins(0, 0, 0, 0)
            lbl = QLabel(label_text)
            lbl.setStyleSheet(f"color: {colour}; font-size: 10px; font-weight: bold;")
            tbl = QTableWidget()
            tbl.setEditTriggers(QTableWidget.NoEditTriggers)
            lay.addWidget(lbl)
            lay.addWidget(tbl)
            return w, tbl

        left_w,  self._table_a = _panel("ROM A  (base)",     ACCENT)
        mid_w,   self._table_d = _panel("Delta  B \u2212 A", FG_DIM)
        right_w, self._table_b = _panel("ROM B  (compare)",  AMBER)

        splitter.addWidget(left_w)
        splitter.addWidget(mid_w)
        splitter.addWidget(right_w)
        splitter.setSizes([380, 140, 380])
        layout.addWidget(splitter, 1)

        self._summary = QLabel("")
        self._summary.setStyleSheet(f"color: {FG_DIM}; font-size: 10px;")
        layout.addWidget(self._summary)

    def set_rom_a(self, rom, variant):
        self._rom_a   = rom
        self._variant = variant
        self._maps = [m for m in variant.main_maps
                      if m.map_type in ("fuel", "ign") and m.rows > 1]
        self._map_combo.blockSignals(True)
        self._map_combo.clear()
        for m in self._maps:
            self._map_combo.addItem(f"{m.name}  [{m.rows}\u00d7{m.cols}  {m.unit}]")
        self._map_combo.blockSignals(False)
        self._load_b_btn.setEnabled(True)
        if self._rom_b:
            self._refresh()
        else:
            self._status.setText("ROM A loaded. Load ROM B to compare.")
            self._clear_tables()

    def clear(self):
        self._rom_a = self._rom_b = self._variant = None
        self._maps = []
        self._map_combo.clear()
        self._load_b_btn.setEnabled(False)
        self._status.setText("Load a ROM A first, then load ROM B to compare.")
        self._clear_tables()
        self._summary.setText("")

    def _on_load_b(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load ROM B for comparison", "",
            "ROM files (*.bin *.BIN *.034 *.rom);;All files (*.*)")
        if not path:
            return
        try:
            raw = open(path, "rb").read()
        except OSError as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        wh, _ = normalize_rom(raw)
        self._rom_b = wh
        self._status.setText(f"ROM B: {Path(path).name}  ({len(wh):,} bytes)")
        self._refresh()

    def _refresh(self):
        idx = self._map_combo.currentIndex()
        if not self._maps or idx < 0 or idx >= len(self._maps) or self._rom_a is None:
            return
        m = self._maps[idx]
        v = self._variant

        raw_a = read_map(self._rom_a, m)
        raw_b = read_map(self._rom_b, m) if self._rom_b else None
        decode = m.decode

        rpm_ax, load_ax = get_axes(self._rom_a, m, v)
        rpm_labels  = [str(r) for r in reversed(rpm_ax)]
        load_labels = [str(l) for l in load_ax]

        nrows, ncols = m.rows, m.cols
        changed_count = 0

        for tbl in (self._table_a, self._table_d, self._table_b):
            tbl.setRowCount(nrows)
            tbl.setColumnCount(ncols)
            tbl.setVerticalHeaderLabels(rpm_labels)
            tbl.setHorizontalHeaderLabels(load_labels)
            tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
            tbl.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)

        all_raws_a = [raw_a[r][c] for r in range(nrows) for c in range(ncols)]
        vmin, vmax = min(all_raws_a), max(all_raws_a)

        for r in range(nrows):
            disp_r = nrows - 1 - r
            for c in range(ncols):
                a_raw = raw_a[r][c]
                b_raw = raw_b[r][c] if raw_b else a_raw
                delta = b_raw - a_raw
                changed = delta != 0
                if changed:
                    changed_count += 1

                a_disp = f"{decode(a_raw):.1f}" if decode else str(a_raw)
                b_disp = f"{decode(b_raw):.1f}" if decode else str(b_raw)

                if m.map_type == "ign" and decode:
                    bg_a = _ign_colour(decode(a_raw))
                    bg_b = _ign_colour(decode(b_raw)) if raw_b else bg_a
                elif m.map_type == "fuel":
                    bg_a = _fuel_colour(a_raw)
                    bg_b = _fuel_colour(b_raw) if raw_b else bg_a
                else:
                    bg_a = _heat(a_raw, vmin, vmax)
                    bg_b = _heat(b_raw, vmin, vmax) if raw_b else bg_a

                if delta > 0:
                    bg_d, fg_d, d_text = QColor("#1a3a1a"), QColor(GREEN), f"+{delta}"
                elif delta < 0:
                    bg_d, fg_d, d_text = QColor("#3a1a1a"), QColor(RED),   str(delta)
                else:
                    bg_d, fg_d, d_text = QColor(BG2),       QColor(FG_DIM), "\u2014"

                def _mk(text, bg, fg=None, bold=False):
                    it = QTableWidgetItem(text)
                    it.setBackground(QBrush(bg))
                    it.setForeground(QBrush(fg or _text_colour(bg)))
                    it.setTextAlignment(Qt.AlignCenter)
                    if bold:
                        f = it.font(); f.setBold(True); it.setFont(f)
                    it.setFlags(Qt.ItemIsEnabled)
                    return it

                self._table_a.setItem(disp_r, c, _mk(a_disp, bg_a))
                self._table_d.setItem(disp_r, c, _mk(d_text, bg_d, fg_d, bold=changed))
                self._table_b.setItem(disp_r, c, _mk(b_disp, bg_b))

        total = nrows * ncols
        if raw_b:
            pct = 100 * changed_count / total
            self._summary.setText(
                f"{changed_count} of {total} cells changed  ({pct:.0f}%)  \u2014  {m.name}")
        else:
            self._summary.setText(f"Load ROM B to see delta  \u2014  {m.name}")

    def _clear_tables(self):
        for tbl in (self._table_a, self._table_d, self._table_b):
            tbl.setRowCount(0)
            tbl.setColumnCount(0)


# ── Main window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(1100, 720)

        # State
        self._main_path:  Path | None = None
        self._boost_path: Path | None = None
        self._main_rom:   bytearray | None = None   # working half
        self._boost_rom:  bytearray | None = None
        self._det:        DetectionResult | None = None
        self._unsaved = False

        self._build_ui()
        self._build_menu()
        self._update_status("Ready — open a ROM file to begin")

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        self.setStyleSheet(STYLESHEET)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # File bar
        file_bar = QFrame()
        file_bar.setFixedHeight(44)
        file_bar.setStyleSheet(
            f"background: {BG2}; border-bottom: 1px solid {BORDER};")
        fb_layout = QHBoxLayout(file_bar)
        fb_layout.setContentsMargins(10, 0, 10, 0)
        fb_layout.setSpacing(8)

        self._open_btn  = QPushButton("Open ROM…")
        self._boost_btn = QPushButton("Open Boost Chip…")
        self._save_btn  = QPushButton("Save ROM…")
        self._save_btn.setEnabled(False)

        self._main_lbl  = QLabel("No main chip")
        self._main_lbl.setStyleSheet(f"color: {FG_DIM}; font-size: 11px;")
        self._boost_lbl = QLabel("No boost chip")
        self._boost_lbl.setStyleSheet(f"color: {FG_DIM}; font-size: 11px;")

        fb_layout.addWidget(self._open_btn)
        fb_layout.addWidget(self._main_lbl)
        fb_layout.addWidget(QLabel("|", styleSheet=f"color: {BORDER};"))
        fb_layout.addWidget(self._boost_btn)
        fb_layout.addWidget(self._boost_lbl)
        fb_layout.addStretch()
        fb_layout.addWidget(self._save_btn)

        root.addWidget(file_bar)

        # Info strip
        self._info_strip = InfoStrip()
        root.addWidget(self._info_strip)

        # Tabs
        self._tabs = QTabWidget()
        self._overview_tab  = OverviewTab()
        self._main_chip_tab = MainChipTab()
        self._boost_tab     = BoostTab()
        self._compare_tab   = CompareTab()

        self._tabs.addTab(self._overview_tab,  "Overview")
        self._tabs.addTab(self._main_chip_tab, "Main Chip Maps")
        self._tabs.addTab(self._boost_tab,     "Boost Chip")
        self._tabs.addTab(self._compare_tab,   "Compare")
        root.addWidget(self._tabs)

        # Status bar
        self._status = QStatusBar()
        self.setStatusBar(self._status)

        # Wire signals
        self._open_btn.clicked.connect(self._on_open_main)
        self._boost_btn.clicked.connect(self._on_open_boost)
        self._save_btn.clicked.connect(self._on_save)

    def _build_menu(self):
        mb = self.menuBar()
        mb.setStyleSheet(
            f"QMenuBar {{ background: {BG2}; color: {FG}; }}"
            f"QMenuBar::item:selected {{ background: {BG3}; }}"
            f"QMenu {{ background: {BG2}; color: {FG}; border: 1px solid {BORDER}; }}"
            f"QMenu::item:selected {{ background: {BG3}; }}")

        file_menu = mb.addMenu("File")

        open_act = QAction("Open ROM…", self)
        open_act.setShortcut("Ctrl+O")
        open_act.triggered.connect(self._on_open_main)
        file_menu.addAction(open_act)

        boost_act = QAction("Open Boost Chip…", self)
        boost_act.triggered.connect(self._on_open_boost)
        file_menu.addAction(boost_act)

        file_menu.addSeparator()

        save_act = QAction("Save ROM…", self)
        save_act.setShortcut("Ctrl+S")
        save_act.triggered.connect(self._on_save)
        file_menu.addAction(save_act)

        file_menu.addSeparator()

        quit_act = QAction("Quit", self)
        quit_act.setShortcut("Ctrl+Q")
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

        help_menu = mb.addMenu("Help")
        about_act = QAction("About UrROM", self)
        about_act.triggered.connect(self._on_about)
        help_menu.addAction(about_act)

    # ── File operations ───────────────────────────────────────────────────────

    def _on_open_main(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open ROM file", "",
            "ROM files (*.bin *.BIN *.034 *.rom);;All files (*.*)")
        if not path:
            return
        self._load_main(Path(path))

    def _load_main(self, path: Path):
        try:
            raw = path.read_bytes()
        except OSError as e:
            QMessageBox.critical(self, "Error", f"Cannot read file:\n{e}")
            return

        # Descramble .034 Rip Chip files before normalising
        if path.suffix.lower() == ".034":
            from urrom.descramble import descramble_034, is_valid_034
            if not is_valid_034(raw):
                QMessageBox.warning(
                    self, "Invalid .034 file",
                    f"{path.name} does not appear to be a valid .034 Rip Chip file.\n\n"
                    "The file may be a Wayback Machine HTML page or a corrupt dump.\n"
                    "Expected: 65536-byte scrambled ROM binary.")
                return
            raw = descramble_034(raw)

        wh, notes = normalize_rom(raw)
        det = detect_rom(wh)

        self._main_path = path
        self._main_rom  = bytearray(wh)
        self._det       = det
        self._unsaved   = False
        self._clear_dirty()

        # Update UI
        fname = path.name
        self._main_lbl.setText(fname)
        self._main_lbl.setStyleSheet(f"color: {FG}; font-size: 11px;")
        self._info_strip.update(det)
        self._overview_tab.update(det)
        self._save_btn.setEnabled(True)

        if det.variant:
            self._main_chip_tab.load(self._main_rom, det.variant)
            self._compare_tab.set_rom_a(bytes(self._main_rom), det.variant)
            self._tabs.setCurrentIndex(1)  # jump to map editor
        else:
            self._main_chip_tab.clear()
            self._compare_tab.clear()
            self._tabs.setCurrentIndex(0)
            QMessageBox.warning(
                self, "Unknown ROM",
                f"ROM not recognised.\n\n"
                f"CRC32: 0x{det.crc32:08X}  Build: 0x{det.build_number:04X}\n\n"
                f"Map editing is disabled for unknown variants.")

        note_text = "; ".join(notes) if notes else ""
        self._update_status(
            f"Loaded {fname}  —  {det.method}"
            + (f"  [{note_text}]" if note_text else ""))

    def _on_open_boost(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open boost chip file", "",
            "ROM files (*.bin *.BIN *.034 *.rom);;All files (*.*)")
        if not path:
            return
        self._load_boost(Path(path))

    def _load_boost(self, path: Path):
        if self._det is None or self._det.variant is None:
            QMessageBox.warning(self, "No main chip",
                                "Load the main chip ROM first.")
            return
        try:
            raw = path.read_bytes()
        except OSError as e:
            QMessageBox.critical(self, "Error", f"Cannot read file:\n{e}")
            return

        # Boost chip working half: first 8KB (0x0000-0x1FFF)
        if len(raw) >= 0x2000:
            boost_wh = bytearray(raw[:0x2000])
        else:
            boost_wh = bytearray(raw)

        self._boost_path = path
        self._boost_rom  = boost_wh
        self._boost_lbl.setText(path.name)
        self._boost_lbl.setStyleSheet(f"color: {FG}; font-size: 11px;")

        self._boost_tab.load(boost_wh, self._det.variant)
        self._update_status(f"Boost chip loaded: {path.name}")

    def _on_save(self):
        if self._main_rom is None:
            return

        # Commit current map edits into the working half
        rom_out = bytearray(self._main_rom)
        rom_out = self._main_chip_tab.commit_to_rom(rom_out)

        # Build full 64KB file for 551x, or 32KB flat for 3B/V8
        if self._det and self._det.variant and self._det.variant.working_half_offset == 0x8000:
            # 551x: pad to 64KB with lower mirror
            full = bytearray(MAIN_CHIP_PHYSICAL)
            full[0x0000:0x8000] = rom_out  # lower half = copy of working half
            full[0x8000:0x10000] = rom_out  # upper half = working half
            out_bytes = bytes(full)
        else:
            # 3B/V8: flat 32KB
            out_bytes = bytes(rom_out)

        default_name = self._main_path.stem + "_edited" + self._main_path.suffix
        path, _ = QFileDialog.getSaveFileName(
            self, "Save ROM", default_name,
            "ROM files (*.bin *.BIN *.034);;All files (*.*)")
        if not path:
            return

        try:
            Path(path).write_bytes(out_bytes)
        except OSError as e:
            QMessageBox.critical(self, "Error", f"Cannot save:\n{e}")
            return

        self._main_chip_tab._table.accept_current_as_baseline()
        self._unsaved = False
        self._clear_dirty()
        self._update_status(f"Saved → {Path(path).name}  ({len(out_bytes):,} bytes)")

    # ── Misc ──────────────────────────────────────────────────────────────────

    def _mark_dirty(self):
        self._unsaved = True
        if not self.windowTitle().startswith("*"):
            self.setWindowTitle("* " + WINDOW_TITLE)

    def _clear_dirty(self):
        self._unsaved = False
        self.setWindowTitle(WINDOW_TITLE)

    def _update_status(self, msg: str):
        self._status.showMessage(msg)

    def _on_about(self):
        QMessageBox.about(
            self, f"About {APP_NAME}",
            f"<b>{APP_NAME}</b> v{APP_VERSION}<br><br>"
            "Open-source ROM editor for Bosch Motronic M2.3 / M2.3.2.<br>"
            "Supports: 3B, AAN, ABY, ADU (RS2), PT / ABH V8.<br><br>"
            "Map addresses confirmed against real ROM dumps and RS2.xdf.<br>"
            "Use at your own risk. Verify all changes on a wideband O2.<br><br>"
            "<a href='https://github.com/dspl1236/UrROM' "
            "style='color:#569cd6'>github.com/dspl1236/UrROM</a>")

    def closeEvent(self, event):
        if self._unsaved or self._main_chip_tab.has_changes():
            r = QMessageBox.question(
                self, "Unsaved changes",
                "You have unsaved map edits. Quit anyway?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                event.ignore()
                return
        event.accept()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    win = MainWindow()
    win.show()
    # If a file is passed on the command line, load it
    if len(sys.argv) > 1:
        p = Path(sys.argv[1])
        if p.exists():
            win._load_main(p)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
