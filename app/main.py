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
    CHIP_REQUIREMENTS,
    write_map, apply_checksum, DetectionResult, ROMVariant, MapDef,
    fuel_encode, ign_encode, ign_encode_3b,
    get_axes,
    MAIN_CHIP_WORKING, MAIN_CHIP_PHYSICAL,
    ALL_VARIANTS,
)
from urrom.kwp import (
    KWPMonitor, LiveValues,
    kwpbridge_available, kwpbridge_running,
    status_label as kwp_status_label,
    live_summary as kwp_live_summary,
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

        # Chip info row
        chip_row = QHBoxLayout()
        self._main_chip_lbl = QLabel("Main chip: —")
        self._main_chip_lbl.setStyleSheet(f"color:{FG};font-size:11px;")
        self._boost_chip_lbl = QLabel("Boost chip: —")
        self._boost_chip_lbl.setStyleSheet(f"color:{FG};font-size:11px;")
        self._checksum_lbl = QLabel("")
        self._checksum_lbl.setStyleSheet(f"color:{FG_DIM};font-size:11px;")
        chip_row.addWidget(self._main_chip_lbl)
        chip_row.addSpacing(24)
        chip_row.addWidget(self._boost_chip_lbl)
        chip_row.addStretch()
        chip_row.addWidget(self._checksum_lbl)
        layout.addLayout(chip_row)

        # Map inventory table — double-click row to jump to that map
        map_title_row = QHBoxLayout()
        map_title = QLabel("MAP INVENTORY")
        map_title.setStyleSheet(f"color:{FG_DIM};font-size:10px;letter-spacing:1px;")
        hint = QLabel("double-click a row to open that map")
        hint.setStyleSheet(f"color:{FG_DIM};font-size:10px;font-style:italic;")
        map_title_row.addWidget(map_title)
        map_title_row.addStretch()
        map_title_row.addWidget(hint)
        layout.addLayout(map_title_row)

        self._map_table = QTableWidget(0, 5)
        self._map_table.setHorizontalHeaderLabels(
            ["Map", "Address", "Size", "Unit", "Confidence"])
        self._map_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._map_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self._map_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._map_table.setAlternatingRowColors(True)
        self._map_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._map_table.setStyleSheet(
            f"alternate-background-color:{BG};background:{BG2};"
            f"QTableWidget::item:selected{{background:{ACCENT}20;}}")
        self._map_table.verticalHeader().setVisible(False)
        self._map_table.setFixedHeight(320)
        layout.addWidget(self._map_table)

        # Tuning note / warning area
        self._tuning_note = QLabel("")
        self._tuning_note.setStyleSheet(
            f"color:{AMBER};font-size:10px;padding:4px 8px;"
            f"background:{BG2};border-left:2px solid {AMBER};")
        self._tuning_note.setWordWrap(True)
        self._tuning_note.setVisible(False)
        layout.addWidget(self._tuning_note)

        layout.addStretch()

        # Signal for jump-to-map (emitted with map index in variant.main_maps)
        self._jump_callback = None  # set by MainWindow
        self._map_table.cellDoubleClicked.connect(self._on_row_jump)

    def set_jump_callback(self, fn):
        """Register a callback fn(map_index) called when user double-clicks a map row."""
        self._jump_callback = fn

    def _on_row_jump(self, row, col):
        if self._jump_callback and row < len(self._maps_for_jump):
            self._jump_callback(self._maps_for_jump[row])

    def update(self, det: DetectionResult | None, boost_det: DetectionResult | None = None):
        self._maps_for_jump = []   # list of int: main_maps index for each table row

        if det is None:
            self._title.setText("No ROM loaded")
            self._detail.setText("")
            self._info.setText("")
            self._map_table.setRowCount(0)
            self._main_chip_lbl.setText("Main chip: —")
            self._boost_chip_lbl.setText("Boost chip: —")
            self._checksum_lbl.setText("")
            self._tuning_note.setVisible(False)
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
            bosch_pn = v.bosch_pns[0] if v.bosch_pns else "—"
            self._info.setText(
                f"Engine: {engine_str}  •  ECU PN: {pn_str}  •  Bosch PN: {bosch_pn}"
            )
        else:
            self._info.setText("\n".join(det.warnings))

        # Chip info row
        eprom_type = "27C512 (64KB)" if det.crc32 else "27C256 (32KB)"
        self._main_chip_lbl.setText(
            f"Main chip: {det.variant.ecu_pns[0] if v and v.ecu_pns else '—'}  "
            f"•  {eprom_type}  •  WH 0x8000–0xFFFF")
        if boost_det and boost_det.variant:
            self._boost_chip_lbl.setText(
                f"Boost chip: {boost_det.variant.software_id}  •  build 0x{boost_det.build_number:04X}")
            self._boost_chip_lbl.setStyleSheet(f"color:{GREEN};font-size:11px;")
        else:
            self._boost_chip_lbl.setText("Boost chip: not loaded")
            self._boost_chip_lbl.setStyleSheet(f"color:{FG_DIM};font-size:11px;")

        # Checksum state
        sw_id = v.software_id if v else ""
        CHECKSUM_VARIANTS = {"551AA_0202","551C","551B","551B_D02","551AA","551A","404","404V8"}
        if sw_id in CHECKSUM_VARIANTS:
            self._checksum_lbl.setText("PRJmod checksum: applied on save")
        else:
            self._checksum_lbl.setText("Stock Bosch ID string (no computed checksum)")

        # Map inventory — 5 columns, track jump indices for main_maps only
        all_maps = v.all_maps if v else []
        main_map_set = set(id(m) for m in (v.main_maps if v else []))
        self._map_table.setRowCount(len(all_maps))

        for row, m in enumerate(all_maps):
            conf_str = m.confidence
            conf_col = (GREEN if conf_str == "CONFIRMED"
                        else AMBER if conf_str == "PROVISIONAL"
                        else RED)
            chip_tag = "boost" if m.chip == "boost" else "main"
            items = [
                QTableWidgetItem(m.name),
                QTableWidgetItem(f"WH 0x{m.main_addr:04X}"),
                QTableWidgetItem(f"{m.rows}×{m.cols}"),
                QTableWidgetItem(m.unit or "raw"),
                QTableWidgetItem(conf_str),
            ]
            items[4].setForeground(QBrush(QColor(conf_col)))
            # Dim boost-chip rows slightly
            if chip_tag == "boost":
                for it in items:
                    it.setForeground(QBrush(QColor(FG_DIM)))
                items[4].setForeground(QBrush(QColor(conf_col)))
            for col_i, item in enumerate(items):
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                self._map_table.setItem(row, col_i, item)

            # Track main_maps index for jump
            if id(m) in main_map_set:
                try:
                    jump_idx = list(v.main_maps).index(m)
                except ValueError:
                    jump_idx = -1
            else:
                jump_idx = -1
            self._maps_for_jump.append(jump_idx)

        # Tuning note for unconfirmed / stock variants
        notes_txt = []
        if sw_id not in CHECKSUM_VARIANTS:
            notes_txt.append("This is a stock Bosch ROM. Map addresses confirmed from direct chip reads.")
        unconfirmed = [m.name for m in all_maps if m.confidence == "UNCONFIRMED" and m.rows > 1]
        if unconfirmed:
            notes_txt.append(f"UNCONFIRMED maps (do not write): {', '.join(unconfirmed[:4])}")
        if notes_txt:
            self._tuning_note.setText("  ·  ".join(notes_txt))
            self._tuning_note.setVisible(True)
        else:
            self._tuning_note.setVisible(False)

        # Hardware requirements from CHIP_REQUIREMENTS lookup
        crc = det.crc32 if det else None
        req = CHIP_REQUIREMENTS.get(crc, {}) if crc else {}
        if req:
            parts = []
            if req.get("boost_chip"):
                parts.append("⚡ BOOST CHIP — must pair with matching fuel chip")
            if req.get("map_kpa"):
                parts.append(f"MAP sensor: {req['map_kpa']} kPa required (swap from stock 200 kPa)")
            if req.get("injector_cc"):
                parts.append(f"Injectors: {req['injector_cc']} cc")
            if req.get("fpr_bar"):
                parts.append(f"FPR: {req['fpr_bar']:.1f} BAR")
            if req.get("turbo"):
                parts.append(f"Turbo: {req['turbo']}")
            notes_detail = req.get("notes", "")
            hw_text = "  ·  ".join(parts)
            if notes_detail:
                hw_text += f"\n{notes_detail}"
            self._tuning_note.setText(hw_text)
            self._tuning_note.setStyleSheet(
                f"color:{AMBER};font-size:10px;padding:4px 8px;"
                f"background:#2a1a00;border-left:3px solid {AMBER};border-radius:2px;")
            self._tuning_note.setVisible(True)


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
        self._undo_stack: list[list[list[int]]] = []   # max 30 states
        self._redo_stack: list[list[list[int]]] = []
        self._rpm_axis:  list = []
        self._load_axis: list = []
        self._is_ign = False
        self._is_fuel = False

        self.setItemDelegate(ChangedCellDelegate(self))
        self.setEditTriggers(QTableWidget.DoubleClicked | QTableWidget.AnyKeyPressed)
        self.setMouseTracking(True)
        self._status_callback = None   # set by MainChipTab to push msgs to status bar
        self.itemChanged.connect(self._on_cell_changed)
        self._loading = False

    def load(self, rom: bytearray, map_def: MapDef,
             rpm_axis: list | None = None, load_axis: list | None = None):
        self._loading = True
        self._rom = rom
        self._map_def = map_def
        self._is_ign  = map_def.map_type == "ign"
        self._is_fuel = map_def.map_type == "fuel"
        # get_axes returns (row_axis, col_axis) — row axis labels vertical header,
        # col axis labels horizontal header.
        self._row_axis = rpm_axis  or list(range(map_def.rows))
        self._col_axis = load_axis or list(range(map_def.cols))

        raw = read_map(bytes(rom), map_def)
        self._original_raw = copy.deepcopy(raw)
        self._current_raw  = copy.deepcopy(raw)

        self.setRowCount(map_def.rows)
        self.setColumnCount(map_def.cols)

        # Row headers: display inverted (row 0 = highest index = top of visual table)
        # so the highest-value row axis entry appears at the top
        def _fmt(v) -> str:
            if isinstance(v, float) and v != int(v):
                return f"{v:.1f}"
            return str(int(v))

        row_labels = [_fmt(v) for v in reversed(self._row_axis)]
        col_labels = [_fmt(v) for v in self._col_axis]
        self.setVerticalHeaderLabels(row_labels)
        self.setHorizontalHeaderLabels(col_labels)

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
        # _display_decode can be overridden by the "Decoded/Raw" toggle
        decode = getattr(self, "_display_decode", self._map_def.decode)

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

                # Tooltip: show raw byte + decoded value
                if decode:
                    decoded_val = decode(item_raw)
                    dec_str = (f"{decoded_val:.2f}" if isinstance(decoded_val, float)
                               else str(decoded_val))
                    item.setToolTip(
                        f"raw: {item_raw}  decoded: {dec_str} {self._map_def.unit or ''}")
                else:
                    item.setToolTip(f"raw: {item_raw}")
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

    def accept_current_as_baseline(self):
        self._original_raw = copy.deepcopy(self._current_raw)
        self._redraw()

    # ── Undo / redo ───────────────────────────────────────────────────────────

    def _push_undo(self):
        """Snapshot current state onto undo stack before a bulk edit."""
        self._undo_stack.append(copy.deepcopy(self._current_raw))
        if len(self._undo_stack) > 30:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def undo(self):
        if not self._undo_stack:
            return
        self._redo_stack.append(copy.deepcopy(self._current_raw))
        self._current_raw = self._undo_stack.pop()
        self._redraw()
        self.itemChanged.emit(QTableWidgetItem())

    def redo(self):
        if not self._redo_stack:
            return
        self._undo_stack.append(copy.deepcopy(self._current_raw))
        self._current_raw = self._redo_stack.pop()
        self._redraw()
        self.itemChanged.emit(QTableWidgetItem())

    def revert(self):
        self._push_undo()
        self._current_raw = copy.deepcopy(self._original_raw)
        self._redraw()

    # ── Editing tools ─────────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        """Ctrl+C/V/A/Z/Y, Del, arrows (+/-1), Enter (commit+advance)."""
        from PyQt5.QtCore import Qt
        mod = event.modifiers()
        key = event.key()
        if mod == Qt.ControlModifier:
            if key == Qt.Key_C:   self._copy_selection(); return
            if key == Qt.Key_V:   self._paste_selection(); return
            if key == Qt.Key_A:   self.selectAll(); return
            if key == Qt.Key_Z:   self.undo(); return
            if key == Qt.Key_Y:   self.redo(); return
        if mod == (Qt.ControlModifier | Qt.ShiftModifier):
            if key == Qt.Key_Z:   self.redo(); return
        if key in (Qt.Key_Delete, Qt.Key_Backspace):
            self._delete_selection(); return
        # Arrow keys without modifier: increment/decrement selected cells
        if mod == Qt.NoModifier and key in (Qt.Key_Plus, Qt.Key_Equal):
            self._nudge_selection(+1); return
        if mod == Qt.NoModifier and key == Qt.Key_Minus:
            self._nudge_selection(-1); return
        if mod == Qt.ShiftModifier and key == Qt.Key_Plus:
            self._nudge_selection(+5); return
        if mod == Qt.ShiftModifier and key == Qt.Key_Minus:
            self._nudge_selection(-5); return
        if mod == Qt.ControlModifier and key == Qt.Key_Plus:
            self._nudge_selection(+10); return
        if mod == Qt.ControlModifier and key == Qt.Key_Minus:
            self._nudge_selection(-10); return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        """Right-click context menu: copy, paste, scale, interpolate."""
        from PyQt5.QtWidgets import QMenu, QAction, QInputDialog
        if self._map_def is None:
            return
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu{{background:{BG2};color:{FG};border:1px solid {BORDER};}}"
            f"QMenu::item:selected{{background:{BG3};}}"
            f"QMenu::item{{padding:4px 20px;}}")

        sel = self.selectedRanges()
        has_sel = bool(sel)

        a_copy  = QAction("Copy  Ctrl+C",  self); a_copy.setEnabled(has_sel)
        a_paste = QAction("Paste  Ctrl+V", self)
        a_del   = QAction("Clear selection  Del", self); a_del.setEnabled(has_sel)
        menu.addAction(a_copy)
        menu.addAction(a_paste)
        menu.addAction(a_del)
        menu.addSeparator()

        a_scale = QAction("Scale selection…", self); a_scale.setEnabled(has_sel)
        a_interp  = QAction("Interpolate rows",    self); a_interp.setEnabled(has_sel)
        a_interpc = QAction("Interpolate columns", self); a_interpc.setEnabled(has_sel)
        a_smooth = QAction("Smooth (3-point)", self); a_smooth.setEnabled(has_sel)
        menu.addAction(a_scale)
        menu.addAction(a_interp)
        menu.addAction(a_interpc)
        menu.addAction(a_smooth)
        menu.addSeparator()

        a_fill = QAction("Fill with value…", self); a_fill.setEnabled(has_sel)
        a_invert = QAction("Invert selection (255-x)", self); a_invert.setEnabled(has_sel)
        a_offset = QAction("Add/subtract offset…", self); a_offset.setEnabled(has_sel)
        a_copyall = QAction("Copy whole map (all cells)", self)
        a_copyall.setEnabled(self._map_def is not None)
        menu.addAction(a_fill)
        menu.addAction(a_invert)
        menu.addAction(a_offset)
        menu.addSeparator()
        menu.addAction(a_copyall)
        # Nudge hint
        nudge_lbl = QAction("  ±1/5/10:  +/- / Shift+/- / Ctrl+-", self)
        nudge_lbl.setEnabled(False)
        menu.addAction(nudge_lbl)
        menu.addSeparator()
        a_undo = QAction(f"Undo  Ctrl+Z  ({len(self._undo_stack)} available)", self)
        a_undo.setEnabled(bool(self._undo_stack))
        a_redo = QAction(f"Redo  Ctrl+Y  ({len(self._redo_stack)} available)", self)
        a_redo.setEnabled(bool(self._redo_stack))
        menu.addAction(a_undo)
        menu.addAction(a_redo)

        act = menu.exec_(event.globalPos())
        if act == a_copy:    self._copy_selection()
        elif act == a_paste: self._paste_selection()
        elif act == a_del:   self._delete_selection()
        elif act == a_scale:
            decode = self._map_def.decode if self._map_def else None
            unit   = self._map_def.unit   if self._map_def else "raw"
            hint_lines = [
                "Examples:",
                "  1.05 = +5%   richening fuel / retarding ign",
                "  0.95 = -5%   leaning fuel / advancing ign",
                "  1.00 = no change",
            ]
            if decode:
                hint_lines.append(f"Values displayed in {unit}")
            hint = "\n".join(hint_lines)
            factor, ok = QInputDialog.getDouble(
                self, "Scale selection", f"Multiply all selected values by:\n{hint}",
                1.0, 0.1, 10.0, 3)
            if ok: self._scale_selection(factor)
        elif act == a_interp:  self._interpolate_rows()
        elif act == a_interpc: self._interpolate_cols()
        elif act == a_smooth: self._smooth_selection()
        elif act == a_fill:
            val, ok = QInputDialog.getInt(
                self, "Fill with value", "Set all selected cells to (0–255):", 128, 0, 255)
            if ok: self._fill_selection(val)
        elif act == a_invert: self._invert_selection()
        elif act == a_offset:
            val, ok = QInputDialog.getInt(
                self, "Add/subtract offset",
                "Add this raw value to all selected cells (-127 to +127):\n"
                "(positive = richer/more advance, negative = leaner/less advance)",
                0, -127, 127)
            if ok: self._offset_selection(val)
        elif act == a_copyall: self._copy_all_cells()
        elif act == a_undo:   self.undo()
        elif act == a_redo:   self.redo()

    def _selected_cells(self) -> list[tuple[int,int]]:
        """Return list of (display_row, col) for current selection."""
        cells = []
        for rng in self.selectedRanges():
            for r in range(rng.topRow(), rng.bottomRow()+1):
                for c in range(rng.leftColumn(), rng.rightColumn()+1):
                    cells.append((r, c))
        return cells

    def _disp_to_raw(self, disp_row: int) -> int:
        """Convert display row (inverted) back to raw data row index."""
        if self._map_def is None:
            return disp_row
        return self._map_def.rows - 1 - disp_row

    def _copy_selection(self):
        """Copy selected cells as TSV to clipboard."""
        from PyQt5.QtWidgets import QApplication
        cells = self._selected_cells()
        if not cells: return
        rows_used = sorted(set(r for r,c in cells))
        cols_used = sorted(set(c for r,c in cells))
        grid = {}
        for r,c in cells:
            it = self.item(r, c)
            grid[(r,c)] = it.text() if it else "0"
        lines = []
        for r in rows_used:
            row_vals = [grid.get((r,c), "") for c in cols_used]
            lines.append("	".join(row_vals))
        QApplication.clipboard().setText("\n".join(lines))
        self._clipboard_shape = (len(rows_used), len(cols_used))

    def _paste_selection(self):
        """Paste TSV clipboard data starting at top-left of current selection."""
        self._push_undo()
        from PyQt5.QtWidgets import QApplication
        text = QApplication.clipboard().text()
        if not text.strip(): return
        rows_text = text.strip().split("\n")
        sel = self.selectedRanges()
        if not sel: return
        top_r = min(rng.topRow() for rng in sel)
        left_c = min(rng.leftColumn() for rng in sel)
        self.blockSignals(True)
        for dr, line in enumerate(rows_text):
            vals = line.split("	")
            for dc, val_str in enumerate(vals):
                r = top_r + dr
                c = left_c + dc
                if r >= self.rowCount() or c >= self.columnCount():
                    continue
                try:
                    raw_val = int(float(val_str.strip()))
                    raw_r = self._disp_to_raw(r)
                    self._current_raw[raw_r][c] = max(0, min(255, raw_val))
                except (ValueError, IndexError):
                    pass
        self.blockSignals(False)
        self._redraw()
        self.itemChanged.emit(self.item(top_r, left_c) or QTableWidgetItem())

    def _delete_selection(self):
        self._push_undo()
        """Set selected cells to 128 (neutral/stock value)."""
        self._fill_selection(128)

    def _fill_selection(self, value: int):
        self._push_undo()
        cells = self._selected_cells()
        if not cells: return
        for disp_r, c in cells:
            raw_r = self._disp_to_raw(disp_r)
            if 0 <= raw_r < len(self._current_raw) and 0 <= c < len(self._current_raw[raw_r]):
                self._current_raw[raw_r][c] = max(0, min(255, value))
        self._redraw()
        self.itemChanged.emit(QTableWidgetItem())

    def _scale_selection(self, factor: float):
        cells = self._selected_cells()
        if not cells: return
        self._push_undo()
        for disp_r, c in cells:
            raw_r = self._disp_to_raw(disp_r)
            if 0 <= raw_r < len(self._current_raw) and 0 <= c < len(self._current_raw[raw_r]):
                new_val = round(self._current_raw[raw_r][c] * factor)
                self._current_raw[raw_r][c] = max(0, min(255, new_val))
        self._redraw()
        self.itemChanged.emit(QTableWidgetItem())

    def _smooth_selection(self):
        """3-point running average across selected cells in each row."""
        cells = self._selected_cells()
        if not cells: return
        self._push_undo()
        by_row: dict[int, list[int]] = {}
        for disp_r, c in cells:
            by_row.setdefault(disp_r, []).append(c)
        for disp_r, cols in by_row.items():
            raw_r = self._disp_to_raw(disp_r)
            if not (0 <= raw_r < len(self._current_raw)):
                continue
            cols_sorted = sorted(cols)
            row_data = list(self._current_raw[raw_r])
            new_vals = {}
            for i, c in enumerate(cols_sorted):
                neighbours = [row_data[cc] for cc in cols_sorted
                              if abs(cc - c) <= 1 and 0 <= cc < len(row_data)]
                new_vals[c] = round(sum(neighbours) / len(neighbours))
            for c, v in new_vals.items():
                self._current_raw[raw_r][c] = max(0, min(255, v))
        self._redraw()
        self.itemChanged.emit(QTableWidgetItem())

    def _interpolate_rows(self):
        """Linear interpolate between first and last selected column in each row."""
        cells = self._selected_cells()
        if not cells: return
        self._push_undo()
        by_row: dict[int, list[int]] = {}
        for disp_r, c in cells:
            by_row.setdefault(disp_r, []).append(c)
        for disp_r, cols in by_row.items():
            raw_r = self._disp_to_raw(disp_r)
            if not (0 <= raw_r < len(self._current_raw)):
                continue
            cols_sorted = sorted(cols)
            if len(cols_sorted) < 2:
                continue
            c_start = cols_sorted[0]
            c_end   = cols_sorted[-1]
            v_start = self._current_raw[raw_r][c_start]
            v_end   = self._current_raw[raw_r][c_end]
            span    = c_end - c_start
            for c in cols_sorted:
                t = (c - c_start) / span
                interp = round(v_start + (v_end - v_start) * t)
                self._current_raw[raw_r][c] = max(0, min(255, interp))
        self._redraw()
        self.itemChanged.emit(QTableWidgetItem())

    def _interpolate_cols(self):
        """Linear interpolate between first and last selected row in each column."""
        cells = self._selected_cells()
        if not cells: return
        self._push_undo()
        by_col: dict[int, list[int]] = {}
        for disp_r, c in cells:
            by_col.setdefault(c, []).append(disp_r)
        for c, disp_rows in by_col.items():
            rows_sorted = sorted(disp_rows)
            if len(rows_sorted) < 2:
                continue
            r_start = rows_sorted[0]
            r_end   = rows_sorted[-1]
            raw_start = self._disp_to_raw(r_start)
            raw_end   = self._disp_to_raw(r_end)
            v_start = self._current_raw[raw_start][c] if 0 <= raw_start < len(self._current_raw) else 0
            v_end   = self._current_raw[raw_end][c]   if 0 <= raw_end   < len(self._current_raw) else 0
            span = r_end - r_start
            for disp_r in rows_sorted:
                raw_r = self._disp_to_raw(disp_r)
                if not (0 <= raw_r < len(self._current_raw)):
                    continue
                t = (disp_r - r_start) / span
                interp = round(v_start + (v_end - v_start) * t)
                self._current_raw[raw_r][c] = max(0, min(255, interp))
        self._redraw()
        self.itemChanged.emit(QTableWidgetItem())

    def _invert_selection(self):
        """Invert selected cells: 255 - x."""
        cells = self._selected_cells()
        if not cells: return
        self._push_undo()
        for disp_r, c in cells:
            raw_r = self._disp_to_raw(disp_r)
            if 0 <= raw_r < len(self._current_raw) and 0 <= c < len(self._current_raw[raw_r]):
                self._current_raw[raw_r][c] = 255 - self._current_raw[raw_r][c]
        self._redraw()
        self.itemChanged.emit(QTableWidgetItem())

    def _nudge_selection(self, delta: int):
        """Increment/decrement all selected cells by delta (+1/-1/+5/-5/+10/-10)."""
        cells = self._selected_cells()
        if not cells: return
        self._push_undo()
        for disp_r, c in cells:
            raw_r = self._disp_to_raw(disp_r)
            if 0 <= raw_r < len(self._current_raw) and 0 <= c < len(self._current_raw[raw_r]):
                self._current_raw[raw_r][c] = max(0, min(255, self._current_raw[raw_r][c] + delta))
        self._redraw()
        self.itemChanged.emit(QTableWidgetItem())

    def _copy_all_cells(self):
        """Copy entire map as TSV to clipboard (for paste into another map or spreadsheet)."""
        from PyQt5.QtWidgets import QApplication
        if not self._current_raw: return
        lines = []
        for r in reversed(range(len(self._current_raw))):
            row = self._current_raw[r]
            decode = self._map_def.decode if self._map_def else None
            if decode:
                lines.append("	".join(f"{decode(v):.2f}" if isinstance(decode(v), float)
                                       else str(decode(v)) for v in row))
            else:
                lines.append("	".join(str(v) for v in row))
        QApplication.clipboard().setText("\n".join(lines))
        self._clipboard_shape = (len(self._current_raw), len(self._current_raw[0]))

    def _offset_selection(self, offset: int):
        """Add a fixed raw byte offset to all selected cells."""
        cells = self._selected_cells()
        if not cells: return
        self._push_undo()
        for disp_r, c in cells:
            raw_r = self._disp_to_raw(disp_r)
            if 0 <= raw_r < len(self._current_raw) and 0 <= c < len(self._current_raw[raw_r]):
                self._current_raw[raw_r][c] = max(0, min(255, self._current_raw[raw_r][c] + offset))
        self._redraw()
        self.itemChanged.emit(QTableWidgetItem())

    def set_status_callback(self, fn):
        """Register fn(msg) to push hover info to the main window status bar."""
        self._status_callback = fn

    def mouseMoveEvent(self, event):
        item = self.itemAt(event.pos())
        if item and self._map_def and self._status_callback:
            rc = item.data(Qt.UserRole + 1)
            if rc:
                r_log, c_log = rc
                raw = self._current_raw[r_log][c_log]
                decode = self._map_def.decode
                unit   = self._map_def.unit or ""
                if decode:
                    dec = decode(raw)
                    dec_s = f"{dec:.2f}" if isinstance(dec, float) else str(dec)
                    msg = f"Cell [{r_log},{c_log}]  raw={raw}  {dec_s} {unit}"
                else:
                    msg = f"Cell [{r_log},{c_log}]  raw={raw}"
                orig = self._original_raw[r_log][c_log] if self._original_raw else raw
                if raw != orig:
                    msg += f"  (was {orig})"
                self._status_callback(msg)
        super().mouseMoveEvent(event)

    # ── Live overlay (KWPBridge) ──────────────────────────────────────────────

    def attach_kwp(self):
        self._kwp_active   = True
        self._kwp_col      = None
        self._kwp_row      = None
        self._kwp_lambda   = None

    def detach_kwp(self):
        self._kwp_active   = False
        self._kwp_col      = None
        self._kwp_row      = None
        self._kwp_lambda   = None
        self._refresh_overlay()

    def update_overlay(self, lv: "LiveValues"):
        """Update the live cursor from a LiveValues object."""
        if not getattr(self, "_kwp_active", False) or self._map_def is None:
            return
        if lv is None or not lv.valid:
            return

        new_col = self._kwp_col
        new_row = self._kwp_row

        # Match RPM → column via col_axis, load → row via row_axis
        col_axis = getattr(self, "_col_axis", [])
        row_axis = getattr(self, "_row_axis", [])

        if col_axis and lv.rpm is not None and len(col_axis) > 1:
            new_col = min(range(len(col_axis)),
                         key=lambda i: abs(col_axis[i] - lv.rpm))

        if row_axis and lv.load is not None and len(row_axis) > 1:
            # lv.load is the raw KWP cell value (1-255 MAF load units).
            # row_axis is also raw (not /25 decoded) — compare directly.
            new_row = min(range(len(row_axis)),
                         key=lambda i: abs(row_axis[i] - lv.load))

        changed = (new_col != self._kwp_col or
                   new_row != self._kwp_row or
                   lv.lambda_ != self._kwp_lambda)
        self._kwp_col    = new_col
        self._kwp_row    = new_row
        self._kwp_lambda = lv.lambda_
        if changed:
            self._refresh_overlay()

    def _refresh_overlay(self):
        """Repaint all cells, adding overlay highlights where needed."""
        if self._map_def is None:
            return
        active   = getattr(self, "_kwp_active", False)
        kwp_col  = getattr(self, "_kwp_col",    None)
        kwp_row  = getattr(self, "_kwp_row",    None)
        lambda_  = getattr(self, "_kwp_lambda", None)
        nrows    = self._map_def.rows
        ncols    = self._map_def.cols

        # Lambda tint colour
        if active and lambda_ is not None:
            if 0.97 <= lambda_ <= 1.03:
                tint = QColor(45, 255, 110, 55)     # green — stoich
            elif lambda_ < 0.97:
                tint = QColor(255, 80, 0, 70)        # orange/red — rich
            else:
                tint = QColor(0, 140, 255, 60)       # blue — lean
        else:
            tint = None

        for r in range(nrows):
            disp_r = nrows - 1 - r
            for c in range(ncols):
                item = self.item(disp_r, c)
                if item is None:
                    continue

                cell_raw = self._current_raw[r][c]
                decode   = self._map_def.decode
                val      = decode(cell_raw) if decode else cell_raw

                # Base colour
                if self._is_ign and decode:
                    base = _ign_colour(val)
                elif self._is_fuel:
                    base = _fuel_colour(cell_raw)
                else:
                    all_v = [self._current_raw[rr][cc]
                             for rr in range(nrows) for cc in range(ncols)]
                    base = _heat(cell_raw, min(all_v), max(all_v))

                bg = QColor(base)

                # Overlay: blend tint into the whole active row/col
                if active and tint is not None:
                    is_active_col = (kwp_col is not None and c == kwp_col)
                    is_active_row = (kwp_row is not None and r == kwp_row)
                    if is_active_col or is_active_row:
                        a = tint.alpha() / 255.0
                        bg = QColor(
                            int(bg.red()   * (1-a) + tint.red()   * a),
                            int(bg.green() * (1-a) + tint.green() * a),
                            int(bg.blue()  * (1-a) + tint.blue()  * a),
                        )

                # Hot cell: current intersection
                if (active and kwp_col == c and kwp_row == r):
                    bg = QColor(255, 255, 255)  # white hot-spot

                item.setBackground(QBrush(bg))
                item.setForeground(QBrush(_text_colour(bg.name())))


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

        self._decode_btn = QPushButton("Decoded ▾")
        self._decode_btn.setCheckable(True)
        self._decode_btn.setChecked(True)
        self._decode_btn.setFixedWidth(90)
        self._decode_btn.setStyleSheet(
            f"QPushButton{{background:{BG3};color:{FG};border:1px solid {BORDER};"
            f"border-radius:3px;padding:0 8px;font-size:10px;}}"
            f"QPushButton:checked{{background:{ACCENT}30;border-color:{ACCENT};}}"
            f"QPushButton:hover{{border-color:{ACCENT};}}")
        self._decode_btn.clicked.connect(self._on_toggle_decode)
        toolbar.addWidget(self._decode_btn)

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

        # SD mode / VE table notice bar
        self._sd_bar = QLabel("")
        self._sd_bar.setStyleSheet(
            f"color:{AMBER};font-size:10px;padding:4px 8px;"
            f"background:#2a1f00;border-left:3px solid {AMBER};border-radius:2px;")
        self._sd_bar.setVisible(False)
        layout.addWidget(self._sd_bar)

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
        # Show all multi-cell maps that are tunable (fuel, ign, raw with decode,
        # or raw without decode for informational viewing). Exclude 1-row axis
        # tables that are just index references, and very large lookup tables
        # (DTC classes 60×60) that aren't tune targets.
        self._maps = [
            m for m in variant.main_maps
            if m.rows > 1 and not (m.rows >= 50 and m.cols >= 50)
        ]

        self._map_combo.blockSignals(True)
        self._map_combo.clear()
        for m in self._maps:
            label = f"{m.name}  [{m.rows}×{m.cols}  {m.unit}]"
            self._map_combo.addItem(label)
        self._map_combo.blockSignals(False)

        if self._maps:
            self._map_combo.setCurrentIndex(0)
            self._on_map_selected(0)

        # SD VE detection notice (551AA_0202 only)
        sw = getattr(variant, "software_id", "")
        if sw == "551AA_0202":
            from urrom.hw_patches import _analyse_sd_ve
            sd_result = _analyse_sd_ve(bytes(rom))
            if sd_result and "POPULATED" in sd_result.detail:
                self._sd_bar.setText(
                    f"⚡  SD mode active — VE table populated at WH 0x2074. "
                    f"Select 'VE Table' to edit.  MAP sensor: {sd_result.status}")
                self._sd_bar.setVisible(True)
            elif sd_result:
                self._sd_bar.setText(
                    f"ℹ  SD mode NOT active — VE table is blank (MAF-based tune). "
                    f"Detected: {sd_result.status}")
                self._sd_bar.setStyleSheet(
                    f"color:{FG_DIM};font-size:10px;padding:4px 8px;"
                    f"background:{BG2};border-left:3px solid {FG_DIM};border-radius:2px;")
                self._sd_bar.setVisible(True)
            else:
                self._sd_bar.setVisible(False)
        else:
            self._sd_bar.setVisible(False)

    def set_status_fn(self, fn):
        """Register the status-bar push function from MainWindow."""
        self._status_fn = fn
        self._table.set_status_callback(fn)

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
        # Wire hover → status via callback set by MainWindow
        if hasattr(self, "_status_fn"):
            self._table.set_status_callback(self._status_fn)
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

        # SD mode detection: show notice when viewing VE table or when VE table
        # is active and user is editing fuel maps (warn they may be in SD mode)
        v_id = v.software_id if v else ""
        if v_id == "551AA_0202":
            from urrom.hw_patches import SD_VE_TABLE_OFFSET, SD_VE_TABLE_SIZE, _is_real_ve_table
            ve_bytes = bytes(self._rom[SD_VE_TABLE_OFFSET: SD_VE_TABLE_OFFSET + SD_VE_TABLE_SIZE])
            sd_active = _is_real_ve_table(ve_bytes)
            if m.main_addr == SD_VE_TABLE_OFFSET:
                if sd_active:
                    self._sd_bar.setText(
                        "⚡ Speed-density VE table — active (non-blank values detected). "
                        "Edit this table to change VE targets. MAF fuel maps ignored in SD mode.")
                else:
                    self._sd_bar.setText(
                        "ℹ VE table is blank (all 0x02). This ROM uses MAF-based fuelling. "
                        "Populate this table to enable speed-density mode.")
                self._sd_bar.setVisible(True)
            elif sd_active and m.map_type == "fuel":
                self._sd_bar.setText(
                    "⚠ SD mode active — VE table is populated. "
                    "Fuel P/T map may not be used. Check VE Table tab.")
                self._sd_bar.setVisible(True)
            else:
                self._sd_bar.setVisible(False)
        else:
            self._sd_bar.setVisible(False)

    def _on_toggle_decode(self):
        """Toggle between showing decoded values (°BTDC, AFR) and raw bytes."""
        decoded = self._decode_btn.isChecked()
        self._decode_btn.setText("Decoded ▾" if decoded else "Raw ▾")
        if not self._maps:
            return
        idx = self._map_combo.currentIndex()
        if 0 <= idx < len(self._maps):
            m = self._maps[idx]
            # Temporarily override decode fn based on toggle
            if decoded:
                # Restore normal decode
                self._table._display_decode = m.decode
            else:
                # Show raw bytes
                self._table._display_decode = None
            self._table._redraw()

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

    def attach_kwp(self):
        self._table.attach_kwp()

    def detach_kwp(self):
        self._table.detach_kwp()

    def update_overlay(self, lv):
        self._table.update_overlay(lv)


# ── Boost chip tab ────────────────────────────────────────────────────────────

class BoostTab(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        # ── Toolbar ───────────────────────────────────────────────────────
        tb = QHBoxLayout()
        self._map_combo = QComboBox()
        self._map_combo.setMinimumWidth(300)
        self._map_combo.currentIndexChanged.connect(self._on_map_selected)
        tb.addWidget(self._map_combo)
        tb.addStretch()
        layout.addLayout(tb)

        self._status = QLabel("No boost chip loaded")
        self._status.setStyleSheet(f"color: {FG_DIM}; font-size: 12px;")
        layout.addWidget(self._status)

        self._note = QLabel(
            "Boost chip maps (boost target, N75 duty cycle) are displayed here "
            "once a boost chip file is loaded.\n\n"
            "Boost chip addresses are PROVISIONAL — verify map data against "
            "known-good values before editing.")
        self._note.setStyleSheet(f"color: {FG_DIM}; font-size: 11px;")
        self._note.setWordWrap(True)
        layout.addWidget(self._note)

        self._table = MapTable()
        self._table.setVisible(False)
        layout.addWidget(self._table)

        layout.addStretch()
        self._boost_rom = None
        self._maps = []

    def load(self, boost_rom: bytearray, variant):
        self._variant = variant
        self._boost_rom = boost_rom
        self._maps = [m for m in variant.boost_maps if m.rows > 1]
        self._map_combo.blockSignals(True)
        self._map_combo.clear()
        if not self._maps:
            self._status.setText("No confirmed boost chip maps for this variant")
            self._table.setVisible(False)
            self._map_combo.blockSignals(False)
            return
        for m in self._maps:
            self._map_combo.addItem(f"{m.name}  [{m.rows}×{m.cols}  {m.unit}]")
        self._map_combo.blockSignals(False)
        self._map_combo.setCurrentIndex(0)
        self._on_map_selected(0)

    def _on_map_selected(self, idx: int):
        if not self._maps or self._boost_rom is None:
             return
        if idx < 0 or idx >= len(self._maps):
             return
        m = self._maps[idx]
        # Pass axis labels — boost chip has no embedded descriptor so use
        # sequential indices; displayed as column/row numbers.
        from urrom.ecu_profiles import get_axes
        rpm_axis, load_axis = get_axes(bytes(self._boost_rom), m, self._variant)
        self._table.load(self._boost_rom, m, rpm_axis, load_axis)
        self._table.setVisible(True)
        self._note.setVisible(False)
        self._status.setText(
             f"Boost chip  —  {m.name}  [{m.rows}×{m.cols}  {m.confidence}]")
    def clear(self):
        self._boost_rom = None
        self._maps = []
        self._map_combo.clear()
        self._status.setText("No boost chip loaded")
        self._note.setVisible(True)
        self._table.setVisible(False)


# ── Hardware / Patch tab ──────────────────────────────────────────────────────

class HardwareTab(QWidget):
    """
    Hardware modification and firmware patch detection panel.

    Shows detected patches, MAP sensor type, and required hardware mods.
    Also allows loading a separate boost chip binary for enhanced detection.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(10)

        # ── Header row ────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        title = QLabel("Hardware & Patch Status")
        title.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {FG};")
        hdr.addWidget(title)
        hdr.addStretch()

        self._boost_lbl = QLabel("Boost chip: not loaded")
        self._boost_lbl.setStyleSheet(f"color: {FG_DIM}; font-size: 11px;")
        hdr.addWidget(self._boost_lbl)

        self._open_boost_btn = QPushButton("Load boost chip…")
        self._open_boost_btn.setFixedHeight(26)
        self._open_boost_btn.setStyleSheet(
            f"QPushButton{{background:{BG3};color:{FG};border:1px solid {BORDER};"
            f"border-radius:3px;padding:0 10px;}}"
            f"QPushButton:hover{{border-color:{ACCENT};}}")
        self._open_boost_btn.clicked.connect(self._on_open_boost)
        hdr.addWidget(self._open_boost_btn)
        outer.addLayout(hdr)

        # ── Subtitle ─────────────────────────────────────────────────────
        sub = QLabel(
            "Detected modifications and firmware patches in the loaded ROM. "
            "Load the boost chip (32 KB) to enable MAP sensor identification."
        )
        sub.setStyleSheet(f"color: {FG_DIM}; font-size: 11px;")
        sub.setWordWrap(True)
        outer.addWidget(sub)

        # ── MAP sensor info card ──────────────────────────────────────────
        self._sensor_card = self._make_sensor_card()
        outer.addWidget(self._sensor_card)

        # ── Section label ─────────────────────────────────────────────────
        sec_lbl = QLabel("DETECTED PATCHES & MODIFICATIONS")
        sec_lbl.setStyleSheet(
            f"color: {FG_DIM}; font-size: 10px; letter-spacing: 1px;")
        outer.addWidget(sec_lbl)

        # ── Patch list ────────────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            f"QScrollArea{{border:none;background:{BG};}}"
            f"QScrollBar:vertical{{width:8px;background:{BG2};}}"
            f"QScrollBar::handle:vertical{{background:{BORDER};border-radius:4px;}}")
        self._patch_container = QWidget()
        self._patch_container.setStyleSheet(f"background:{BG};")
        self._patch_layout = QVBoxLayout(self._patch_container)
        self._patch_layout.setSpacing(6)
        self._patch_layout.setContentsMargins(0, 0, 0, 0)
        self._patch_layout.addStretch()
        scroll.setWidget(self._patch_container)
        outer.addWidget(scroll, 1)

        # ── QLCC / Ben Swann note ─────────────────────────────────────────
        note = QLabel(
            "ℹ  QLCC (Quick Launch Control Chip, Ben Swann): No QLCC binary files were "
            "found in public repositories. QLCC chips used MPX4250 (250 kPa) sensors and "
            "custom LC implementations predating prjmod. If you have QLCC binaries, open "
            "them directly — UrROM will analyse them using the same detection logic.\n\n"
            "ℹ  Bosch chip ID: Stock M2.3 EPROMs embed an ASCII part-number string at the "
            "end of the working half (e.g. '4A0907551C  2,2l R5 MOTR.RHV RS2D01PMC…'). "
            "This is not a computed checksum. prjmod ROMs use M232csum.dll for checksum "
            "verification after burning; stock chips rely on EPROM read-back only."
        )
        note.setStyleSheet(
            f"color: {FG_DIM}; font-size: 10px; padding: 6px 8px; "
            f"background: {BG2}; border-left: 2px solid {BORDER}; border-radius: 2px;")
        note.setWordWrap(True)
        outer.addWidget(note)

        # ── LC/NLS scalars panel ─────────────────────────────────────────
        lc_hdr = QLabel("LC / NLS SCALARS  (prjmod 0x0202 firmware)")
        lc_hdr.setStyleSheet(
            f"color:{FG_DIM};font-size:10px;letter-spacing:1px;margin-top:8px;")
        outer.addWidget(lc_hdr)

        lc_card = QFrame()
        lc_card.setStyleSheet(
            f"QFrame{{background:{BG3};border:1px solid {BORDER};"
            f"border-radius:4px;padding:2px;}}")
        lc_grid = QVBoxLayout(lc_card)
        lc_grid.setContentsMargins(12, 8, 12, 8)
        lc_grid.setSpacing(4)

        self._lc_rows: list[tuple] = []  # (name_lbl, val_lbl, raw_lbl)

        LC_SCALARS = [
            ("Hard RPM limit",        0x0617, lambda b: b * 40,   "RPM"),
            ("LC speed threshold",    0x0620, lambda b: b * 2,    "km/h"),
            ("LC ign retard RPM",     0x0625, lambda b: b * 40,   "RPM"),
            ("LC ign angle (ATDC)",   0x062B, lambda b: b * 1,    "°"),
            ("NLS min RPM",           0x063D, lambda b: b * 40,   "RPM"),
            ("NLS ign angle (ATDC)",  0x0643, lambda b: b * 1,    "°"),
            ("Spark cut knock RPM",   0x064C, lambda b: b * 40,   "RPM"),
            ("LC ign cut RPM",        0x066E, lambda b: b * 40,   "RPM"),
        ]

        for name, wh_off, decode_fn, unit in LC_SCALARS:
            row_w = QWidget()
            row_h = QHBoxLayout(row_w)
            row_h.setContentsMargins(0, 0, 0, 0)
            row_h.setSpacing(6)
            n_lbl = QLabel(name)
            n_lbl.setStyleSheet(f"color:{FG};font-size:11px;min-width:180px;")
            v_lbl = QLabel("—")
            v_lbl.setStyleSheet(f"color:{ACCENT};font-size:11px;font-weight:bold;")
            r_lbl = QLabel("")
            r_lbl.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
            addr_lbl = QLabel(f"WH 0x{wh_off:04X}")
            addr_lbl.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
            row_h.addWidget(n_lbl)
            row_h.addWidget(v_lbl)
            row_h.addWidget(QLabel(unit))
            row_h.addStretch()
            row_h.addWidget(r_lbl)
            row_h.addWidget(addr_lbl)
            lc_grid.addWidget(row_w)
            self._lc_rows.append((v_lbl, r_lbl, wh_off, decode_fn))

        self._lc_inactive = QLabel(
            "LC/NLS scalars require prjmod 0x0202 firmware (551AA_0202 variant).")
        self._lc_inactive.setStyleSheet(f"color:{FG_DIM};font-size:11px;")
        lc_grid.addWidget(self._lc_inactive)
        outer.addWidget(lc_card)
        self._lc_card = lc_card


        # State
        self._boost_bytes: bytes | None = None
        self._wh: bytes | None = None
        self._variant_name: str = ""

    # ── Sensor card ───────────────────────────────────────────────────────

    def _make_sensor_card(self) -> QFrame:
        card = QFrame()
        card.setStyleSheet(
            f"QFrame{{background:{BG3};border:1px solid {BORDER};"
            f"border-radius:4px;padding:4px;}}")
        layout = QVBoxLayout(card)
        layout.setSpacing(4)
        layout.setContentsMargins(12, 8, 12, 8)

        row1 = QHBoxLayout()
        lbl = QLabel("MAP Sensor")
        lbl.setStyleSheet(f"color:{FG};font-size:12px;font-weight:bold;")
        self._sensor_type_lbl = QLabel("—")
        self._sensor_type_lbl.setStyleSheet(f"color:{ACCENT};font-size:12px;font-weight:bold;")
        row1.addWidget(lbl)
        row1.addStretch()
        row1.addWidget(self._sensor_type_lbl)
        layout.addLayout(row1)

        self._sensor_detail_lbl = QLabel(
            "Load boost chip for sensor identification.  "
            "Stock: Bosch 200 kPa.  SD mode requires MPXH6400A 400 kPa.")
        self._sensor_detail_lbl.setStyleSheet(f"color:{FG_DIM};font-size:11px;")
        self._sensor_detail_lbl.setWordWrap(True)
        layout.addWidget(self._sensor_detail_lbl)

        # Sensor comparison row
        sensors_row = QHBoxLayout()
        for kpa, label, notes in [
            ("200 kPa", "Stock Bosch",   "≤1.0 bar gauge\nMAF builds only"),
            ("250 kPa", "MPX4250",        "≤1.5 bar gauge\nQLCC-era"),
            ("300 kPa", "MPX4300",        "≤2.0 bar gauge\n034EFI / R201 swap"),
            ("400 kPa", "MPXH6400A",      "≤2.9 bar gauge\nprjmod SD std"),
        ]:
            cell = QFrame()
            cell.setStyleSheet(
                f"QFrame{{background:{BG2};border:1px solid {BORDER};"
                f"border-radius:3px;}}")
            cell_l = QVBoxLayout(cell)
            cell_l.setContentsMargins(8, 4, 8, 4)
            cell_l.setSpacing(1)
            kpa_lbl = QLabel(kpa)
            kpa_lbl.setStyleSheet(f"color:{FG};font-size:11px;font-weight:bold;")
            name_lbl = QLabel(label)
            name_lbl.setStyleSheet(f"color:{ACCENT};font-size:10px;")
            notes_lbl = QLabel(notes)
            notes_lbl.setStyleSheet(f"color:{FG_DIM};font-size:9px;")
            for w in (kpa_lbl, name_lbl, notes_lbl):
                cell_l.addWidget(w)
            sensors_row.addWidget(cell)
        layout.addLayout(sensors_row)
        return card

    # ── Patch card factory ────────────────────────────────────────────────

    def _make_patch_card(self, result) -> QFrame:
        """Build one patch result card widget."""
        cat_colours = {
            "firmware": ACCENT,
            "hardware": AMBER,
            "sensor":   "#c586c0",
        }
        cat_colour = cat_colours.get(result.category, FG_DIM)

        # Status colour
        if result.status == "DETECTED" or result.status.startswith("KNOWN"):
            st_colour = GREEN
        elif result.status == "NOT DETECTED" or result.status == "NOT REQUIRED":
            st_colour = FG_DIM
        elif result.status == "UNKNOWN":
            st_colour = AMBER
        elif "REQUIRED" in result.status:
            st_colour = AMBER
        else:
            st_colour = FG

        card = QFrame()
        card.setStyleSheet(
            f"QFrame{{background:{BG2};border:1px solid {BORDER};"
            f"border-left:3px solid {cat_colour};border-radius:3px;}}")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(3)

        top = QHBoxLayout()
        name_lbl = QLabel(result.name)
        name_lbl.setStyleSheet(f"color:{FG};font-size:12px;font-weight:bold;")

        cat_lbl = QLabel(result.category.upper())
        cat_lbl.setStyleSheet(
            f"color:{cat_colour};font-size:9px;font-weight:bold;"
            f"background:{BG3};border-radius:2px;padding:1px 4px;")

        st_lbl = QLabel(result.status)
        st_lbl.setStyleSheet(
            f"color:{st_colour};font-size:11px;font-weight:bold;")

        conf_lbl = QLabel(result.confidence)
        conf_lbl.setStyleSheet(f"color:{FG_DIM};font-size:10px;")

        top.addWidget(name_lbl)
        top.addWidget(cat_lbl)
        top.addStretch()
        if result.wh_offset is not None:
            off_lbl = QLabel(f"WH 0x{result.wh_offset:04X}")
            off_lbl.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
            top.addWidget(off_lbl)
        top.addWidget(st_lbl)
        top.addWidget(conf_lbl)
        layout.addLayout(top)

        detail_lbl = QLabel(result.detail)
        detail_lbl.setStyleSheet(f"color:{FG_DIM};font-size:11px;")
        detail_lbl.setWordWrap(True)
        layout.addWidget(detail_lbl)

        if result.recommended:
            rec_lbl = QLabel("★  Recommended for this build")
            rec_lbl.setStyleSheet(f"color:{AMBER};font-size:10px;")
            layout.addWidget(rec_lbl)

        return card

    # ── Public update method ──────────────────────────────────────────────

    def update(self, wh: bytes | None, variant_name: str = "") -> None:
        """Called when main chip is loaded."""
        self._wh = wh
        self._variant_name = variant_name
        self._refresh()

    def set_boost(self, boost_bytes: bytes | None, filename: str = "") -> None:
        """Called when boost chip is loaded."""
        self._boost_bytes = boost_bytes
        if boost_bytes:
            self._boost_lbl.setText(
                f"Boost chip: {filename or 'loaded'}  ({len(boost_bytes)} B)")
        else:
            self._boost_lbl.setText("Boost chip: not loaded")
        self._refresh()

    # ── Internal refresh ──────────────────────────────────────────────────

    def _refresh(self) -> None:
        from urrom.hw_patches import detect_patches

        # Clear existing patch cards (all but the trailing stretch)
        while self._patch_layout.count() > 1:
            item = self._patch_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if self._wh is None:
            placeholder = QLabel("Load a ROM to see patch detection results.")
            placeholder.setStyleSheet(f"color:{FG_DIM};font-size:12px;")
            placeholder.setAlignment(Qt.AlignCenter)
            self._patch_layout.insertWidget(0, placeholder)
            self._sensor_type_lbl.setText("—")
            self._sensor_detail_lbl.setText("No ROM loaded.")
            return

        results = detect_patches(self._wh, self._variant_name, self._boost_bytes)

        # Update sensor card
        sensor_result = next((r for r in results if r.name == "MAP Sensor Type"), None)
        if sensor_result:
            self._sensor_type_lbl.setText(sensor_result.status)
            col = (GREEN if "400kPa" in sensor_result.status
                   else AMBER if "UNKNOWN" not in sensor_result.status
                   else FG_DIM)
            self._sensor_type_lbl.setStyleSheet(
                f"color:{col};font-size:12px;font-weight:bold;")
            self._sensor_detail_lbl.setText(sensor_result.detail)

        # Insert patch cards (skip the sensor result — shown in card above)
        insert_pos = 0
        for r in results:
            if r.name == "MAP Sensor Type":
                continue
            card = self._make_patch_card(r)
            self._patch_layout.insertWidget(insert_pos, card)
            insert_pos += 1

        # ── Update LC/NLS scalars ─────────────────────────────────────────
        is_0202 = (self._variant_name == "551AA_0202")
        self._lc_inactive.setVisible(not is_0202)
        for (v_lbl, r_lbl, wh_off, decode_fn) in self._lc_rows:
            if is_0202 and self._wh and wh_off < len(self._wh):
                raw = self._wh[wh_off]
                decoded = decode_fn(raw)
                v_lbl.setText(f"{decoded}")
                r_lbl.setText(f"(raw 0x{raw:02X}={raw})")
            else:
                v_lbl.setText("—")
                r_lbl.setText("")


    # ── Boost chip file open ──────────────────────────────────────────────

    def _on_open_boost(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Boost Chip",
            str(Path.home()),
            "ROM files (*.bin *.BIN *.rom);;All files (*.*)")
        if not path:
            return
        try:
            raw = Path(path).read_bytes()
        except OSError as e:
            QMessageBox.critical(self, "Error", f"Cannot read boost chip:\n{e}")
            return
        if len(raw) not in (32768, 65536):
            QMessageBox.warning(
                self, "Unexpected size",
                f"Expected 32 KB or 64 KB boost chip, got {len(raw)} bytes.\n"
                "Loading anyway — results may be unreliable.")
        # For 64KB doubled: take the upper half
        if len(raw) == 65536:
            raw = raw[0x8000:]
        self.set_boost(raw, Path(path).name)


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

        # Export row
        exp_row = QHBoxLayout()
        exp_row.addStretch()
        self._export_btn = QPushButton("Export diff report…")
        self._export_btn.setEnabled(False)
        self._export_btn.setFixedHeight(24)
        self._export_btn.setStyleSheet(
            f"QPushButton{{background:{BG3};color:{FG};border:1px solid {BORDER};"
            f"border-radius:3px;padding:0 10px;font-size:10px;}}"
            f"QPushButton:hover{{border-color:{ACCENT};}}")
        self._export_btn.clicked.connect(self._on_export_diff)
        exp_row.addWidget(self._export_btn)
        layout.addLayout(exp_row)

    def set_rom_a(self, rom, variant):
        self._rom_a   = rom
        self._variant = variant
        self._maps = [
            m for m in variant.main_maps
            if m.rows > 1 and not (m.rows >= 50 and m.cols >= 50)
        ]
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
        if Path(path).suffix.lower() == ".034":
            from urrom.descramble import descramble_034, is_valid_034
            if is_valid_034(raw):
                raw = descramble_034(raw)
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

                if delta != 0 and decode:
                    # Show delta in decoded units (°BTDC, AFR, etc.)
                    d_decoded = decode(b_raw) - decode(a_raw)
                    d_text = f"+{d_decoded:.2f}" if d_decoded > 0 else f"{d_decoded:.2f}"
                elif delta != 0:
                    d_text = f"+{delta}" if delta > 0 else str(delta)
                else:
                    d_text = "\u2014"

                if delta > 0:
                    bg_d, fg_d = QColor("#1a3a1a"), QColor(GREEN)
                elif delta < 0:
                    bg_d, fg_d = QColor("#3a1a1a"), QColor(RED)
                else:
                    bg_d, fg_d = QColor(BG2),       QColor(FG_DIM)

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
        self._export_btn.setEnabled(bool(self._rom_b))
        if raw_b:
            pct = 100 * changed_count / total
            # Compute max/min decoded delta for headline stat
            if decode:
                deltas_decoded = [
                    decode(raw_b[r][c]) - decode(raw_a[r][c])
                    for r in range(nrows) for c in range(ncols)
                    if raw_b[r][c] != raw_a[r][c]
                ]
                if deltas_decoded:
                    d_max = max(deltas_decoded)
                    d_min = min(deltas_decoded)
                    range_str = (f"  |  max Δ {d_max:+.2f}  min Δ {d_min:+.2f}  {m.unit}")
                else:
                    range_str = ""
            else:
                raw_deltas = [raw_b[r][c] - raw_a[r][c]
                              for r in range(nrows) for c in range(ncols)
                              if raw_b[r][c] != raw_a[r][c]]
                if raw_deltas:
                    range_str = f"  |  max Δ {max(raw_deltas):+d}  min Δ {min(raw_deltas):+d}  raw"
                else:
                    range_str = ""
            self._summary.setText(
                f"{m.name}  \u2014  {changed_count}/{total} cells changed  ({pct:.0f}%){range_str}")
        else:
            self._summary.setText(f"Load ROM B to see delta  \u2014  {m.name}")

    def _on_export_diff(self):
        """Export a text diff report for all changed maps."""
        if self._rom_a is None or self._rom_b is None or not self._maps:
            return
        from urrom.ecu_profiles import read_map
        lines = [
            f"UrROM diff report",
            f"Variant: {self._variant.name if self._variant else '?'}",
            f"{'=' * 60}",
            "",
        ]
        total_changed = 0
        for m in self._maps:
            if m.rows <= 1:
                continue
            raw_a = read_map(self._rom_a, m)
            raw_b = read_map(self._rom_b, m)
            decode = m.decode
            changed = [(r, c, raw_a[r][c], raw_b[r][c])
                       for r in range(m.rows) for c in range(m.cols)
                       if raw_a[r][c] != raw_b[r][c]]
            if not changed:
                continue
            total_changed += len(changed)
            lines.append(f"{m.name}  WH 0x{m.main_addr:04X}  ({len(changed)} cells changed)")
            for r, c, va, vb in changed[:32]:  # cap at 32 cells per map
                if decode:
                    da = decode(va); db = decode(vb)
                    lines.append(f"  [{r:2d},{c:2d}]  {da:+.2f} → {db:+.2f} {m.unit}  "
                                 f"(raw {va} → {vb})")
                else:
                    lines.append(f"  [{r:2d},{c:2d}]  {va} → {vb}")
            if len(changed) > 32:
                lines.append(f"  ... and {len(changed)-32} more cells")
            lines.append("")
        lines.append(f"{'=' * 60}")
        lines.append(f"Total: {total_changed} cells changed across {sum(1 for m in self._maps if m.rows>1)} maps")

        path, _ = QFileDialog.getSaveFileName(
            self, "Export diff report", "diff_report.txt",
            "Text files (*.txt);;All files (*.*)")
        if not path:
            return
        Path(path).write_text("\n".join(lines))
        QMessageBox.information(self, "Exported",
                                f"Diff report saved to {Path(path).name}")

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
        self._unsaved     = False
        self._kwp_matched = False

        self._build_ui()
        self._build_menu()
        self._update_status("Ready — open a ROM file to begin")

        # ── KWPBridge live overlay ────────────────────────────────────────────
        self._kwp_monitor = KWPMonitor(self)
        self._kwp_monitor.connected.connect(self._on_kwp_connected)
        self._kwp_monitor.disconnected.connect(self._on_kwp_disconnected)
        self._kwp_monitor.live_data.connect(self._on_kwp_live_data)
        self._kwp_monitor.mismatch.connect(self._on_kwp_mismatch)
        # Poll KWP status badge every 2 s even when no ECU connected
        self._kwp_status_timer = QTimer(self)
        self._kwp_status_timer.timeout.connect(self._refresh_kwp_badge)
        self._kwp_status_timer.start(2000)

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

        # KWP status badge
        self._kwp_badge = QLabel("● KWPBridge")
        self._kwp_badge.setStyleSheet(
            f"color: {FG_DIM}; font-size: 10px; padding: 0 8px;")
        fb_layout.addWidget(self._kwp_badge)

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
        self._hardware_tab  = HardwareTab()

        self._tabs.addTab(self._overview_tab,  "Overview")
        self._tabs.addTab(self._main_chip_tab, "Main Chip Maps")
        self._tabs.addTab(self._boost_tab,     "Boost Chip")
        self._tabs.addTab(self._compare_tab,   "Compare")
        self._tabs.addTab(self._hardware_tab,  "Hardware")
        root.addWidget(self._tabs)

        # Wire Overview double-click → jump to map in Main Chip Maps tab
        def _jump_to_map(main_map_idx: int):
            if main_map_idx < 0:
                return
            self._tabs.setCurrentWidget(self._main_chip_tab)
            self._main_chip_tab._map_combo.setCurrentIndex(main_map_idx)
        self._overview_tab.set_jump_callback(_jump_to_map)

        # Wire hover status bar for map table
        self._main_chip_tab.set_status_fn(self._update_status)

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

        # ── File ─────────────────────────────────────────────────────────────
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

        quit_act = QAction("Quit\t", self)
        quit_act.setShortcut("Ctrl+Q")
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

        # ── Tools ─────────────────────────────────────────────────────────────
        tools_menu = mb.addMenu("Tools")

        self._act_kwp_status = QAction("KWPBridge: not running", self)
        self._act_kwp_status.setEnabled(False)
        tools_menu.addAction(self._act_kwp_status)

        tools_menu.addSeparator()

        kwp_dlg_act = QAction("Live Data Connection…", self)
        kwp_dlg_act.setShortcut("Ctrl+K")
        kwp_dlg_act.triggered.connect(self._show_kwp_dialog)
        tools_menu.addAction(kwp_dlg_act)

        act_dash = QAction("Dashboard…", self)
        act_dash.setShortcut("Ctrl+D")
        act_dash.triggered.connect(self._toggle_dashboard)
        tools_menu.addAction(act_dash)

        # 2-second timer keeps the menu label current
        self._kwp_menu_timer = QTimer(self)
        self._kwp_menu_timer.timeout.connect(self._refresh_kwp_menu_label)
        self._kwp_menu_timer.start(2000)

        # ── Help ─────────────────────────────────────────────────────────────
        help_menu = mb.addMenu("Help")
        about_act = QAction("About UrROM", self)
        about_act.triggered.connect(self._on_about)
        help_menu.addAction(about_act)

    def _refresh_kwp_menu_label(self):
        """Update Tools menu KWP label every 2 seconds."""
        if not hasattr(self, '_act_kwp_status'):
            return
        if not kwpbridge_available():
            self._act_kwp_status.setText("KWPBridge: not installed")
        elif kwpbridge_running():
            pn = self._kwp_monitor.current_pn()
            if pn:
                self._act_kwp_status.setText(f"KWPBridge: connected  ·  {pn}")
            else:
                self._act_kwp_status.setText("KWPBridge: running — no ECU")
        else:
            self._act_kwp_status.setText("KWPBridge: not running")

    def _show_kwp_dialog(self):
        """Show KWPBridge connection status and info dialog."""
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                                     QDialogButtonBox)
        dlg = QDialog(self)
        dlg.setWindowTitle("Live Data — KWPBridge Connection")
        dlg.setMinimumWidth(420)
        dlg.setStyleSheet(f"background:{BG2}; color:{FG};")
        lay = QVBoxLayout(dlg)
        lay.setSpacing(12)

        if not kwpbridge_available():
            dot, body = "⚫", (
                "<b>KWPBridge is not installed.</b><br><br>"
                "UrROM works fully standalone without it.<br><br>"
                "KWPBridge adds optional live ECU data overlay:<br>"
                "• Real-time RPM, load, coolant on the map tabs<br>"
                "• Lambda and timing overlaid on active map cells<br>"
                "• Part-number safety gate before writing<br><br>"
                "Install KWPBridge and run it alongside UrROM.")
        elif kwpbridge_running():
            pn = self._kwp_monitor.current_pn()
            det = self._det
            pns = det.variant.ecu_pns if det and det.variant else []
            if pn:
                matched = self._kwp_matched
                dot  = "🟢" if matched else "🟡"
                if matched:
                    body = (f"<b>KWPBridge connected.</b><br><br>"
                            f"ECU: <b>{pn}</b><br>"
                            "ECU matches loaded ROM — live overlay active.")
                else:
                    rom_str = ", ".join(pns) if pns else "(none loaded)"
                    body = (f"<b>KWPBridge connected.</b><br><br>"
                            f"ECU: <b>{pn}</b><br>"
                            f"ROM expects: <b>{rom_str}</b><br>"
                            "Load the matching ROM to enable overlay.")
            else:
                dot  = "🟡"
                body = ("<b>KWPBridge running — no ECU detected.</b><br><br>"
                        "Connect your KL-line interface and turn ignition on.")
        else:
            dot  = "🔴"
            body = ("<b>KWPBridge is installed but not running.</b><br><br>"
                    "UrROM is fully operational without it.<br><br>"
                    "Start KWPBridge to enable live ECU data overlay.<br>"
                    "It will be detected automatically within 2 seconds.")

        icon = QLabel(dot)
        icon.setStyleSheet("font-size: 28px;")
        msg = QLabel(body)
        msg.setWordWrap(True)

        row = QHBoxLayout()
        row.addWidget(icon)
        row.addWidget(msg, 1)
        w = QWidget()
        w.setLayout(row)
        lay.addWidget(w)

        bb = QDialogButtonBox(QDialogButtonBox.Ok)
        bb.accepted.connect(dlg.accept)
        lay.addWidget(bb)
        dlg.exec_()

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
        self._overview_tab.update(det, self._boost_det if hasattr(self, "_boost_det") else None)
        self._hardware_tab.update(bytes(wh), det.variant.name if det.variant else "")
        self._save_btn.setEnabled(True)

        if det.variant:
            self._main_chip_tab.load(self._main_rom, det.variant)
            self._compare_tab.set_rom_a(bytes(self._main_rom), det.variant)
            self._tabs.setCurrentIndex(1)  # jump to map editor
            # Tell KWP monitor which PNs are valid for this variant
            self._kwp_monitor.set_rom_part_numbers(det.variant.ecu_pns)
            self._refresh_kwp_badge()

            # Warn on variants with unconfirmed map addresses
            v_name = det.variant.name or ""
            if "V8" in v_name or "ABH" in v_name or "PT" in v_name:
                QMessageBox.warning(
                    self, "V8 ROM — unconfirmed map addresses",
                    "V8 map addresses have not been verified against real hardware.\n\n"
                    "You can view the ROM in hex and compare files, but do not\n"
                    "write map values until the addresses are confirmed.\n\n"
                    "If you have a V8 chip read, please contribute it — "
                    "it would be the first confirmed V8 ROM in this tool.")
        else:
            self._main_chip_tab.clear()
            self._compare_tab.clear()
            self._kwp_monitor.set_rom_part_numbers([])
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

        # Normalise: accept 8KB, 16KB, 32KB, or 64KB doubled boost chips.
        # 551x boost chips are 32KB (27C256); 3B/551A are 8KB (27C64).
        if len(raw) == 65536:
             boost_raw = bytearray(raw[0x8000:])  # doubled — take upper half
        elif len(raw) == 32768:
             boost_raw = bytearray(raw)
        elif len(raw) <= 8192:
             boost_raw = bytearray(raw)
        else:
             boost_raw = bytearray(raw[:32768])

        boost_det = detect_rom(bytes(boost_raw))
        self._boost_det  = boost_det
        self._boost_path = path
        self._boost_rom  = boost_raw
        self._boost_lbl.setText(path.name)
        self._boost_lbl.setStyleSheet(f"color: {FG}; font-size: 11px;")

        self._boost_tab.load(boost_raw, self._det.variant)
        self._hardware_tab.set_boost(bytes(raw), path.name)
        self._overview_tab.update(self._det, boost_det)

        bld = boost_det.build_number if boost_det else 0
        crc = boost_det.crc32 if boost_det else 0
        self._update_status(
             f"Boost chip: {path.name}  CRC32 0x{crc:08X}  build 0x{bld:04X}")

    def _on_save(self):
        if self._main_rom is None:
            return

        rom_out = bytearray(self._main_rom)
        rom_out = self._main_chip_tab.commit_to_rom(rom_out)

        variant = self._det.variant if self._det else None
        sw_id = getattr(variant, "software_id", "") if variant else ""
        CHECKSUM_VARIANTS = {"551AA_0202", "551C", "551B", "551B_D02",
                             "551AA", "551A", "404", "404V8"}
        needs_checksum = sw_id in CHECKSUM_VARIANTS
        if needs_checksum:
            old_cs = bytes(rom_out[0x3FFA:0x3FFE])
            rom_out = apply_checksum(rom_out)
            cs_changed = bytes(rom_out[0x3FFA:0x3FFE]) != old_cs
        else:
            cs_changed = False

        if self._det and self._det.variant and self._det.variant.working_half_offset == 0x8000:
            full = bytearray(MAIN_CHIP_PHYSICAL)
            full[0x0000:0x8000] = rom_out
            full[0x8000:0x10000] = rom_out
            out_bytes = bytes(full)
        else:
            out_bytes = bytes(rom_out)

        stem   = self._main_path.stem
        suffix = self._main_path.suffix.lower()
        default_name = stem + "_edited" + (suffix if suffix in (".bin", ".034") else ".bin")
        path, sel_filter = QFileDialog.getSaveFileName(
            self, "Save ROM", default_name,
            "Binary ROM (*.bin *.BIN);;034 Rip Chip (*.034);;All files (*.*)")
        if not path:
            return

        if path.lower().endswith(".034") or "034" in sel_filter:
            from urrom.descramble import scramble_034
            out_bytes = bytes(scramble_034(out_bytes))

        try:
            Path(path).write_bytes(out_bytes)
        except OSError as e:
            QMessageBox.critical(self, "Error", f"Cannot save:\n{e}")
            return

        notes = []
        if cs_changed:     notes.append("checksum updated")
        elif needs_checksum: notes.append("checksum ok")
        if path.lower().endswith(".034"): notes.append(".034 scrambled")
        note_str = f"  ({', '.join(notes)})" if notes else ""
        self._main_chip_tab._table.accept_current_as_baseline()
        self._unsaved = False
        self._clear_dirty()
        self._update_status(f"Saved → {Path(path).name}  ({len(out_bytes):,} bytes){note_str}")

    # ── KWPBridge overlay ──────────────────────────────────────────────────────

    def _on_import_xdf(self):
        """Import a TunerPro XDF v1.50 file to add map entries to current variant."""
        if self._det is None or self._det.variant is None:
            QMessageBox.information(self, "Import XDF",
                "Load a ROM first, then import an XDF to add its map addresses.")
            return

        path, _ = QFileDialog.getOpenFileName(
            self, "Import XDF", "",
            "TunerPro XDF (*.xdf *.XDF);;All files (*.*)")
        if not path:
            return

        try:
            from urrom.xdf_import import parse_xdf, xdf_to_mapdefs
            result = parse_xdf(path)
            maps   = xdf_to_mapdefs(result, filter_min_cells=4)
        except Exception as e:
            QMessageBox.critical(self, "XDF Import Error", str(e))
            return

        if not maps:
            QMessageBox.information(self, "XDF Import",
                "No usable maps found in this XDF file.")
            return

        v = self._det.variant
        existing_addrs = {m.main_addr for m in v.main_maps + v.boost_maps}
        new_maps = [m for m in maps if m.main_addr not in existing_addrs]
        dup_count = len(maps) - len(new_maps)

        msg = (
            f"XDF: {result.title or Path(path).name}\n"
            f"Author: {result.author or chr(8212)}\n"
            f"Found {len(maps)} maps, {dup_count} already known.\n\n"
            f"Add {len(new_maps)} new PROVISIONAL maps to {v.name}?\n\n"
            "These will be available in Map editor for this session only.\n"
            "To persist them, edit ecu_profiles.py."
        )
        # Inject into variant (session only — not persisted)
        v.main_maps = v.main_maps + new_maps

        # Reload tabs
        if self._main_rom is not None:
            self._main_chip_tab.load(self._main_rom, v)
            self._compare_tab.set_rom_a(bytes(self._main_rom), v)
            self._overview_tab.update(self._det, self._boost_det if hasattr(self, "_boost_det") else None)

        self._update_status(
            f"XDF imported: {len(new_maps)} maps added from {Path(path).name}")

    def _on_kwp_connected(self, ecu_pn: str):
        self._kwp_matched = self._kwp_monitor.is_matched()
        self._refresh_kwp_badge()
        if self._kwp_matched:
            self._main_chip_tab.attach_kwp()
            self._update_status(
                f"KWPBridge connected  ·  {ecu_pn}  ·  ECU matches ROM  ·  live overlay active")
        else:
            variant = self._det.variant if self._det else None
            rom_pn = (variant.ecu_pns[0] if variant and variant.ecu_pns else "?")
            self._update_status(
                f"KWPBridge connected  ·  ECU {ecu_pn}  ≠  ROM {rom_pn}  ·  overlay locked")

    def _on_kwp_disconnected(self):
        self._kwp_matched = False
        self._main_chip_tab.detach_kwp()
        self._refresh_kwp_badge()
        self._update_status("KWPBridge disconnected")

    def _on_kwp_mismatch(self, ecu_pn: str, rom_pn: str):
        self._kwp_matched = False
        self._main_chip_tab.detach_kwp()
        self._refresh_kwp_badge()
        self._update_status(
            f"KWPBridge: ECU {ecu_pn} does not match loaded ROM {rom_pn}  ·  overlay locked")

    def _on_kwp_live_data(self, lv):
        if not self._kwp_matched:
            return
        self._main_chip_tab.update_overlay(lv)
        summary = kwp_live_summary(lv)
        if summary:
            self._update_status(f"🟢  {summary}")

    def _toggle_dashboard(self):
        """Open or close the live ECU dashboard window."""
        from urrom.kwp import DashboardWindow
        if not hasattr(self, '_dashboard') or self._dashboard is None:
            self._dashboard = DashboardWindow(self._kwp_monitor, parent=self)
        if self._dashboard.is_visible():
            self._dashboard.hide()
        else:
            self._dashboard.show()

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
        self._kwp_monitor.stop()
        event.accept()

    # ── KWPBridge live overlay ────────────────────────────────────────────────

    def _on_kwp_connected(self, ecu_pn: str):
        self._kwp_matched = self._kwp_monitor.is_matched()
        self._refresh_kwp_badge()
        if self._kwp_matched:
            self._main_chip_tab.attach_kwp()
            self._update_status(
                f"KWPBridge  ·  {ecu_pn}  ·  ECU matches ROM  ·  live overlay active")
        else:
            rom_pns = (self._det.variant.ecu_pns
                       if self._det and self._det.variant else [])
            rom_str = rom_pns[0] if rom_pns else "no ROM"
            self._update_status(
                f"KWPBridge  ·  ECU {ecu_pn}  ≠  {rom_str}  ·  overlay locked")

    def _on_kwp_disconnected(self):
        self._kwp_matched = False
        self._main_chip_tab.detach_kwp()
        self._refresh_kwp_badge()
        self._update_status("KWPBridge disconnected")

    def _on_kwp_mismatch(self, ecu_pn: str, rom_pn: str):
        self._kwp_matched = False
        self._main_chip_tab.detach_kwp()
        self._refresh_kwp_badge()

    def _on_kwp_live_data(self, lv: "LiveValues"):
        if not self._kwp_matched:
            return
        self._main_chip_tab.update_overlay(lv)
        summary = kwp_live_summary(lv)
        if summary:
            self._update_status(f"🟢  {summary}")
        self._refresh_kwp_badge(lv)

    def _refresh_kwp_badge(self, lv=None):
        """Update the KWP status badge in the file bar and Tools menu label."""
        self._refresh_kwp_menu_label()
        rom_pns = (self._det.variant.ecu_pns
                   if self._det and self._det.variant else [])
        text, colour = kwp_status_label(self._kwp_monitor, rom_pns)
        if lv and self._kwp_matched:
            summary = kwp_live_summary(lv)
            if summary:
                text = f"🟢  {summary}"
        self._kwp_badge.setText(text)
        self._kwp_badge.setStyleSheet(
            f"color: {colour}; font-size: 10px; padding: 0 8px;")


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
