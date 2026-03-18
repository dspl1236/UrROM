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
    get_boost_pairing, check_chip_pair,
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
        self.setMinimumHeight(34)
        self.setSizePolicy(
            __import__("PyQt5.QtWidgets", fromlist=["QSizePolicy"]).QSizePolicy.Expanding,
            __import__("PyQt5.QtWidgets", fromlist=["QSizePolicy"]).QSizePolicy.Preferred)
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

        self._req_lbl = QLabel("")
        self._req_lbl.setStyleSheet(f"color: {AMBER}; font-size: 10px;")
        self._req_lbl.setWordWrap(True)
        self._req_lbl.setVisible(False)

        layout.addWidget(self._variant_lbl)
        layout.addWidget(self._build_lbl)
        layout.addWidget(self._cs_lbl)
        layout.addWidget(self._conf_lbl)
        layout.addStretch()
        layout.addWidget(self._req_lbl)

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

        # Checksum — distinguish computed/stored for prjmod, stock ID string for Bosch
        from urrom.ecu_profiles import verify_checksum, compute_checksum, read_stored_checksum
        CHECKSUM_VARIANTS = {"551AA_0202","551C","551B","551B_D02","551AA","551A","404","404V8"}
        sw = det.variant.software_id if det.variant else ""
        if sw in CHECKSUM_VARIANTS:
            # PRJmod / tuned: has a computed checksum
            if det.checksum_ok:
                self._cs_lbl.setText("✓ checksum OK")
                self._cs_lbl.setStyleSheet(f"color: {GREEN}; font-size: 11px;")
            else:
                self._cs_lbl.setText("✗ checksum BAD")
                self._cs_lbl.setStyleSheet(f"color: {RED}; font-size: 11px;")
        else:
            # Stock Bosch: ASCII part number at 0x3FFA, not a computed checksum
            self._cs_lbl.setText("ID string (stock)")
            self._cs_lbl.setStyleSheet(f"color: {FG_DIM}; font-size: 11px;")

        # Boost chip pairing requirement
        from urrom.ecu_profiles import get_boost_pairing, KNOWN_CRCS
        pairs = get_boost_pairing(det.crc32) if det.crc32 else []
        if pairs:
            pair_labels = [KNOWN_CRCS.get(c, (None, f"0x{c:08X}"))[1].split(" — ")[0]
                           for c in pairs]
            self._pair_lbl.setText(f"⚡ requires boost: {' | '.join(pair_labels)}")
            self._pair_lbl.setStyleSheet(f"color: {AMBER}; font-size: 10px;")
        else:
            self._pair_lbl.setText("")

        # Hardware requirements for known 034EFI chips
        from urrom.ecu_profiles import get_chip_requirements
        req = get_chip_requirements(det.crc32)
        if req and hasattr(self, "_req_lbl"):
            parts = []
            if req.get("map_kpa"):
                parts.append(f"{req['map_kpa']}kPa MAP")
            if req.get("injectors"):
                parts.append(f"{req['injectors']} injectors")
            if req.get("fpr_bar"):
                parts.append(f"{req['fpr_bar']}BAR FPR")
            if req.get("turbo") and req["turbo"] not in ("NA", "stock"):
                parts.append(f"{req['turbo']} turbo")
            if req.get("maf") and req["maf"] != "stock":
                parts.append(f"MAF: {req['maf']}")
            if req.get("notes"):
                parts.append(req["notes"])
            self._req_lbl.setText("Requires: " + "  ·  ".join(parts) if parts else "")
            self._req_lbl.setVisible(bool(parts))
        elif hasattr(self, "_req_lbl"):
            self._req_lbl.setVisible(False)

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

        # Hardware requirements card
        hw_hdr = QLabel("HARDWARE REQUIREMENTS")
        hw_hdr.setStyleSheet(
            f"color:{FG_DIM};font-size:10px;letter-spacing:1px;margin-top:4px;")
        layout.addWidget(hw_hdr)

        self._hw_card = QFrame()
        self._hw_card.setStyleSheet(
            f"QFrame{{background:{BG3};border:1px solid {BORDER};"
            f"border-radius:4px;padding:2px;}}")
        hw_layout = QVBoxLayout(self._hw_card)
        hw_layout.setContentsMargins(12, 8, 12, 8)
        hw_layout.setSpacing(3)
        self._hw_lbl = QLabel("Load a ROM to see hardware requirements.")
        self._hw_lbl.setStyleSheet(f"color:{FG_DIM};font-size:11px;")
        self._hw_lbl.setWordWrap(True)
        hw_layout.addWidget(self._hw_lbl)
        layout.addWidget(self._hw_card)

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

        # Boost chip pairing hint
        from urrom.ecu_profiles import get_boost_pairing, KNOWN_CRCS
        required_boost = get_boost_pairing(det.crc32)
        if required_boost and not (boost_det and boost_det.crc32 in required_boost):
            boost_names = [KNOWN_CRCS.get(c,(None,""))[1].split(" — ")[0] for c in required_boost]
            hint = "  ·  ".join(boost_names)
            self._tuning_note.setText(f"⚡ BOOST CHIP REQUIRED: {hint}")
            self._tuning_note.setVisible(True)
        elif required_boost and boost_det and boost_det.crc32 in required_boost:
            self._tuning_note.setText("✓ Boost chip confirmed: correct pair loaded.")
            self._tuning_note.setStyleSheet(
                f"color:{GREEN};font-size:10px;padding:4px 8px;"
                f"background:{BG2};border-left:2px solid {GREEN};")
            self._tuning_note.setVisible(True)

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

        # Hardware requirements from KNOWN_CRCS description
        from urrom.ecu_profiles import KNOWN_CRCS, get_boost_pairing
        crc = det.crc32 if det else 0
        if crc and crc in KNOWN_CRCS:
            _, desc = KNOWN_CRCS[crc]
            # Parse structured fields from description
            hw_lines = []
            import re
            # Extract requirements: MAP sensor, injectors, FPR, MAF, whp, boost
            for pattern, label in [
                (r'3\.0 BAR MAP',       '🗺  MAP sensor: 3.0 BAR (e.g. MPX4300 or VMAP)'),
                (r'300kPa MAP',          '🗺  MAP sensor: 300 kPa required'),
                (r'(\d{3,4})cc.*injector', None),
                (r'(\d+\.?\d*) BAR FPR', None),
                (r'stock MAF',           '💨  MAF: stock sensor (no replacement)'),
                (r'big bore.*MAF',       '💨  MAF: big bore housing required'),
                (r'billet MAF',          '💨  MAF: 034 billet housing required'),
                (r'(\d+)whp',            None),
                (r'(\d+)psi.*redline',   None),
                (r'7[0-9]{3}rpm',        None),
            ]:
                m = re.search(pattern, desc, re.IGNORECASE)
                if m:
                    if label:
                        hw_lines.append(label)
                    else:
                        raw = m.group(0)
                        if 'cc' in raw.lower():
                            hw_lines.append(f'💉  Injectors: {raw}')
                        elif 'BAR FPR' in raw:
                            hw_lines.append(f'⛽  FPR: {raw}')
                        elif 'whp' in raw.lower():
                            hw_lines.append(f'📊  Rated: {raw}')
                        elif 'psi' in raw.lower() and 'redline' in raw.lower():
                            hw_lines.append(f'🔩  Boost: {raw}')
                        elif 'rpm' in raw.lower():
                            hw_lines.append(f'🔴  Rev limit: {raw.upper()}')
            # Boost chip pairing
            pairs = get_boost_pairing(crc)
            if pairs:
                pair_labels = [KNOWN_CRCS.get(c, (None, f"0x{c:08X}"))[1].split(" — ")[0]
                               for c in pairs]
                hw_lines.append(f'⚡  Boost chip: {" OR ".join(pair_labels)}')
            if hw_lines:
                self._hw_lbl.setText("\n".join(hw_lines))
                self._hw_lbl.setStyleSheet(f"color:{FG};font-size:11px;")
            else:
                self._hw_lbl.setText("No specific hardware requirements documented.")
                self._hw_lbl.setStyleSheet(f"color:{FG_DIM};font-size:11px;")
        else:
            self._hw_lbl.setText("Hardware requirements not available for this ROM.")
            self._hw_lbl.setStyleSheet(f"color:{FG_DIM};font-size:11px;")

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
        self._undo_stack:   list[list[list[int]]] = []   # max 30 states
        self._redo_stack:   list[list[list[int]]] = []
        self._annotations:  dict[tuple[int,int], str] = {}  # (raw_r, col) → note
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

                # Tooltip: show raw byte + decoded value + annotation
                ann = self._annotations.get((r, c), "")
                tip_parts = []
                if decode:
                    decoded_val = decode(item_raw)
                    dec_str = (f"{decoded_val:.2f}" if isinstance(decoded_val, float)
                               else str(decoded_val))
                    tip_parts.append(f"raw: {item_raw}  decoded: {dec_str} {self._map_def.unit or ''}")
                else:
                    tip_parts.append(f"raw: {item_raw}")
                if ann:
                    tip_parts.append(f"📝 {ann}")
                    # Add asterisk to annotated cells
                    item.setText(item.text() + " *")
                item.setToolTip("\n".join(tip_parts))
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
        a_annotate = QAction("Add note to cell…", self)
        a_annotate.setEnabled(len(self._selected_cells()) == 1)
        menu.addAction(a_annotate)
        a_undo = QAction(f"Undo  Ctrl+Z  ({len(self._undo_stack)} available)", self)
        a_undo.setEnabled(bool(self._undo_stack))
        a_redo = QAction(f"Redo  Ctrl+Y  ({len(self._redo_stack)} available)", self)
        a_redo.setEnabled(bool(self._redo_stack))
        menu.addAction(a_undo)
        menu.addAction(a_redo)
        menu.addSeparator()
        a_copy_from = QAction("Copy this map from another ROM…", self)
        a_copy_from.setEnabled(self._map_def is not None)
        menu.addAction(a_copy_from)

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
        elif act == a_annotate: self._annotate_cell()
        elif act == a_undo:   self.undo()
        elif act == a_redo:   self.redo()
        elif act == a_copy_from: self._copy_map_from_rom()

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

    def _annotate_cell(self):
        """Add or edit a text note for the selected cell."""
        from PyQt5.QtWidgets import QInputDialog
        cells = self._selected_cells()
        if len(cells) != 1:
            return
        disp_r, c = cells[0]
        raw_r = self._disp_to_raw(disp_r)
        key = (raw_r, c)
        existing = self._annotations.get(key, "")
        note, ok = QInputDialog.getText(
            self, "Cell note",
            f"Note for cell [{raw_r},{c}] (leave blank to clear):",
            text=existing)
        if not ok:
            return
        if note.strip():
            self._annotations[key] = note.strip()
        elif key in self._annotations:
            del self._annotations[key]
        self._redraw()

    def _copy_map_from_rom(self):
        """Open a second ROM and copy this map's data into the current map."""
        from PyQt5.QtWidgets import QFileDialog, QMessageBox
        if self._map_def is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open source ROM to copy map from",
            "", "ROM files (*.bin *.BIN *.034);;All files (*.*)")
        if not path:
            return
        try:
            raw = open(path, "rb").read()
        except OSError as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        # Handle .034 format
        if path.lower().endswith(".034"):
            from urrom.descramble import descramble_034, is_valid_034
            if is_valid_034(raw):
                raw = bytes(descramble_034(raw))
        from urrom.ecu_profiles import normalize_rom, read_map
        src_wh, _ = normalize_rom(raw)
        if len(src_wh) < self._map_def.main_addr + self._map_def.size:
            QMessageBox.warning(self, "Map copy",
                f"Source ROM too small for map at WH 0x{self._map_def.main_addr:04X}")
            return
        self._push_undo()
        src_data = read_map(bytes(src_wh), self._map_def)
        self._current_raw = [row[:] for row in src_data]
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

        from PyQt5.QtWidgets import QLineEdit
        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("Search maps…")
        self._search_box.setFixedWidth(140)
        self._search_box.setStyleSheet(
            f"QLineEdit{{background:{BG2};color:{FG};border:1px solid {BORDER};"
            f"border-radius:3px;padding:2px 6px;font-size:10px;}}"
            f"QLineEdit:focus{{border-color:{ACCENT};}}")
        self._search_box.textChanged.connect(self._on_search_maps)
        toolbar.addWidget(self._search_box)

        self._conf_badge = QLabel("")
        self._conf_badge.setStyleSheet(
            f"font-size: 10px; padding: 2px 6px; border-radius: 3px; "
            f"background: {BG3}; border: 1px solid {BORDER};")
        toolbar.addWidget(self._conf_badge)

        toolbar.addStretch()

        self._grid_btn = QPushButton("⊞ Grid")
        self._grid_btn.setCheckable(True)
        self._grid_btn.setChecked(False)
        self._grid_btn.setFixedWidth(70)
        self._grid_btn.setStyleSheet(
            f"QPushButton{{background:{BG3};color:{FG};border:1px solid {BORDER};"
            f"border-radius:3px;padding:0 6px;font-size:10px;}}"
            f"QPushButton:checked{{background:{ACCENT}30;border-color:{ACCENT};}}"
            f"QPushButton:hover{{border-color:{ACCENT};}}")
        self._grid_btn.clicked.connect(self._on_toggle_grid)
        toolbar.addWidget(self._grid_btn)

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

        self._axis_btn = QPushButton("Axis…")
        self._axis_btn.setFixedWidth(50)
        self._axis_btn.setEnabled(False)
        self._axis_btn.setStyleSheet(
            f"QPushButton{{background:{BG3};color:{FG};border:1px solid {BORDER};"
            f"border-radius:3px;padding:0 6px;font-size:10px;}}"
            f"QPushButton:hover{{border-color:{ACCENT};}}")
        self._axis_btn.clicked.connect(self._on_edit_axis)
        toolbar.addWidget(self._axis_btn)

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

        # ── Grid overview panel (all maps side by side) ──────────────────────
        self._grid_panel = QScrollArea()
        self._grid_panel.setWidgetResizable(True)
        self._grid_panel.setStyleSheet(
            f"QScrollArea{{border:none;background:{BG};}}"
            f"QScrollBar:horizontal{{height:8px;background:{BG2};}}"
            f"QScrollBar::handle:horizontal{{background:{BORDER};border-radius:4px;}}")
        self._grid_panel.setVisible(False)
        layout.addWidget(self._grid_panel)

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

        # Statistics strip — updates on selection change
        stats_row = QHBoxLayout()
        self._stats_min = QLabel("min —")
        self._stats_max = QLabel("max —")
        self._stats_mean = QLabel("mean —")
        self._stats_range = QLabel("")
        for lbl in (self._stats_min, self._stats_max, self._stats_mean, self._stats_range):
            lbl.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
            stats_row.addWidget(lbl)
            stats_row.addSpacing(16)
        stats_row.addStretch()
        layout.addLayout(stats_row)

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

    def set_title_fn(self, fn):
        """Register fn(map_name) to update the main window title bar."""
        self._title_fn = fn

    def _on_search_maps(self, text: str):
        """Filter map combo to entries matching search text (name or address)."""
        if not self._maps:
            return
        text = text.strip().lower()
        self._map_combo.blockSignals(True)
        self._map_combo.clear()
        matched = []
        for i, m in enumerate(self._maps):
            if (not text
                    or text in m.name.lower()
                    or text in f"0x{m.main_addr:04x}"
                    or text in (m.unit or "").lower()
                    or text in (m.map_type or "").lower()):
                label = f"{m.name}  [{m.rows}\xd7{m.cols}  {m.unit}]"
                self._map_combo.addItem(label, userData=i)
                matched.append(i)
        self._map_combo.blockSignals(False)
        if matched:
            self._map_combo.setCurrentIndex(0)
            self._on_map_selected_by_real_idx(matched[0])
        if text:
            self._search_box.setStyleSheet(
                f"QLineEdit{{background:{BG2};color:{GREEN if matched else RED};"
                f"border:1px solid {'#2dff6e' if matched else '#ff4444'};"
                f"border-radius:3px;padding:2px 6px;font-size:10px;}}")
        else:
            self._search_box.setStyleSheet(
                f"QLineEdit{{background:{BG2};color:{FG};border:1px solid {BORDER};"
                f"border-radius:3px;padding:2px 6px;font-size:10px;}}")

    def _on_map_selected_by_real_idx(self, real_idx: int):
        """Load map by its real index in self._maps (not combo index)."""
        if 0 <= real_idx < len(self._maps):
            # Temporarily update _on_map_selected to use real idx
            m = self._maps[real_idx]
            from urrom.ecu_profiles import get_axes
            rpm_axis, load_axis = get_axes(bytes(self._rom), m, self._variant)
            self._table.load(self._rom, m, rpm_axis, load_axis)
            try:
                self._table.itemChanged.disconnect()
            except TypeError:
                pass
            self._table.itemChanged.connect(lambda _: self.on_table_changed())
            if hasattr(self, "_status_fn"):
                self._table.set_status_callback(self._status_fn)
            self._desc_lbl.setText(m.description)
            self._addr_lbl.setText(f"Address: 0x{m.main_addr:04X}  (working half offset)")
            self._size_lbl.setText(f"{m.rows}\xd7{m.cols} = {m.size} bytes")
            conf = m.confidence
            conf_colour = (GREEN if conf=="CONFIRMED" else AMBER if conf=="PROVISIONAL" else RED)
            self._conf_badge.setText(conf)
            self._conf_badge.setStyleSheet(
                f"font-size:10px;padding:2px 6px;border-radius:3px;"
                f"color:{conf_colour};background:{BG3};border:1px solid {conf_colour};")
            self._revert_btn.setEnabled(False)
            self._update_sd_bar(m)
            if hasattr(self, "_title_fn"):
                self._title_fn(m.name)

    def _update_sd_bar(self, m):
        """SD mode notice bar logic extracted for reuse."""
        v = self._variant
        v_id = v.software_id if v else ""
        if v_id == "551AA_0202":
            from urrom.hw_patches import SD_VE_TABLE_OFFSET, SD_VE_TABLE_SIZE, _is_real_ve_table
            ve_bytes = bytes(self._rom[SD_VE_TABLE_OFFSET: SD_VE_TABLE_OFFSET + SD_VE_TABLE_SIZE])
            sd_active = _is_real_ve_table(ve_bytes)
            if m.main_addr == SD_VE_TABLE_OFFSET:
                self._sd_bar.setText(
                    "⚡ Speed-density VE table — " +
                    ("active (non-blank). Edit to change VE targets. MAF maps ignored in SD mode."
                     if sd_active else
                     "blank (0x02 fill). ROM uses MAF-based fuelling."))
                self._sd_bar.setVisible(True)
            elif sd_active and m.map_type == "fuel":
                self._sd_bar.setText("⚠ SD mode active — VE table populated. Fuel P/T map may not be used.")
                self._sd_bar.setVisible(True)
            else:
                self._sd_bar.setVisible(False)
        else:
            self._sd_bar.setVisible(False)

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
        if not self._maps or idx < 0:
            return
        # When search is active, combo items carry userData = real index
        real_idx = self._map_combo.itemData(idx)
        if real_idx is not None:
            if real_idx < 0 or real_idx >= len(self._maps):
                return
            self._on_map_selected_by_real_idx(real_idx)
            return
        if idx >= len(self._maps):
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

        self._update_sd_bar(m)

    def _on_toggle_grid(self):
        """Toggle between single-map editor and all-maps grid overview."""
        show_grid = self._grid_btn.isChecked()
        self._table.setVisible(not show_grid)
        self._sd_bar.setVisible(False)
        self._grid_panel.setVisible(show_grid)
        if show_grid and self._rom is not None and self._variant is not None:
            self._refresh_grid()

    def _refresh_grid(self):
        """Build the all-maps mini-grid overview."""
        from urrom.ecu_profiles import read_map, get_axes
        from PyQt5.QtWidgets import QWidget, QGridLayout, QLabel, QFrame, QVBoxLayout
        from PyQt5.QtCore import Qt

        # Only show ign + fuel maps with rows > 1
        maps = [m for m in self._maps if m.rows > 1 and m.cols > 1]

        container = QWidget()
        container.setStyleSheet(f"background:{BG};")
        grid = QGridLayout(container)
        grid.setSpacing(12)
        grid.setContentsMargins(12, 12, 12, 12)

        CELL_SIZE = 6   # px per map cell in the mini thumbnail

        for map_idx, m in enumerate(maps):
            col_pos = map_idx % 4
            row_pos = map_idx // 4

            frame = QFrame()
            frame.setStyleSheet(
                f"QFrame{{background:{BG3};border:1px solid {BORDER};"
                f"border-radius:4px;padding:2px;}}")
            frame.setFixedSize(
                m.cols * CELL_SIZE + 24,
                m.rows * CELL_SIZE + 36)
            frame.mousePressEvent = (lambda e, idx=map_idx: (
                self._grid_btn.setChecked(False),
                self._on_toggle_grid(),
                self._map_combo.setCurrentIndex(idx)
            ))
            frame.setCursor(Qt.PointingHandCursor)

            vl = QVBoxLayout(frame)
            vl.setContentsMargins(4, 4, 4, 4)
            vl.setSpacing(2)

            title = QLabel(m.name)
            title.setStyleSheet(
                f"color:{FG};font-size:9px;font-weight:bold;border:none;")
            title.setAlignment(Qt.AlignCenter)
            vl.addWidget(title)

            # Build mini pixel-art grid as a single QLabel with rich background
            try:
                raw_data = read_map(bytes(self._rom), m)
                decode   = m.decode
                all_raws = [raw_data[r][c] for r in range(m.rows)
                            for c in range(m.cols)]
                vmin, vmax = min(all_raws), max(all_raws)

                # Build HTML table for the thumbnail
                cells_html = ""
                for disp_r in range(m.rows):
                    raw_r = m.rows - 1 - disp_r
                    for c in range(m.cols):
                        rv = raw_data[raw_r][c]
                        if decode:
                            dv = decode(rv)
                            if m.map_type == "ign":
                                bg_hex = _ign_colour(float(dv) if dv else 0).name()
                            elif m.map_type == "fuel":
                                bg_hex = _fuel_colour(rv).name()
                            else:
                                bg_hex = _heat(rv, vmin, vmax).name()
                        else:
                            bg_hex = _heat(rv, vmin, vmax).name()
                        cells_html += (
                            f'<td style="background:{bg_hex};'
                            f'width:{CELL_SIZE}px;height:{CELL_SIZE}px;'
                            f'padding:0;border:none;"></td>')
                    cells_html += "</tr><tr>"

                html_tbl = (
                    f'<table style="border-collapse:collapse;'
                    f'border-spacing:0;border:none;">'
                    f'<tr>{cells_html}</tr></table>')
                lbl = QLabel()
                lbl.setTextFormat(Qt.RichText)
                lbl.setText(html_tbl)
                lbl.setStyleSheet("border:none;")
                vl.addWidget(lbl)

            except Exception:
                err_lbl = QLabel("error")
                err_lbl.setStyleSheet(f"color:{RED};font-size:9px;border:none;")
                vl.addWidget(err_lbl)

            grid.addWidget(frame, row_pos, col_pos)

        # Pad remaining cells
        self._grid_panel.setWidget(container)

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

    def _on_edit_axis(self):
        """Open axis editor to view/modify the RPM and load axis values."""
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                                      QTableWidget, QTableWidgetItem, QDialogButtonBox,
                                      QHeaderView, QGroupBox)
        if not self._maps or self._map_combo.currentIndex() < 0:
            return
        m = self._maps[self._map_combo.currentIndex()]
        rpm_ax = list(self._table._row_axis)
        load_ax = list(self._table._col_axis)

        dlg = QDialog()
        dlg.setWindowTitle(f"Axis editor — {m.name}")
        dlg.setMinimumWidth(520)
        dlg.setStyleSheet(f"background:{BG};color:{FG};")
        lay = QVBoxLayout(dlg)
        lay.setSpacing(8)

        note = QLabel(
            "Axis values shown here are read from the ROM descriptor.\n"
            "Editing updates the display only - not written to ROM.")
        note.setStyleSheet(f"color:{AMBER};font-size:10px;padding:6px;background:{BG2};"
                           f"border-left:3px solid {AMBER};border-radius:2px;")
        note.setWordWrap(True)
        lay.addWidget(note)

        split = QHBoxLayout()

        def _make_axis_table(title, values, unit):
            grp = QGroupBox(title)
            grp.setStyleSheet(f"QGroupBox{{color:{FG};border:1px solid {BORDER};"
                              f"border-radius:4px;padding:4px;margin-top:8px;}}"
                              f"QGroupBox::title{{subcontrol-origin:margin;left:8px;}}")
            tbl = QTableWidget(len(values), 2)
            tbl.setHorizontalHeaderLabels(["#", f"Value ({unit})"])
            tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
            tbl.setStyleSheet(f"background:{BG2};color:{FG};gridline-color:{BORDER};")
            for i, v in enumerate(values):
                tbl.setItem(i, 0, QTableWidgetItem(str(i)))
                it = QTableWidgetItem(str(int(v)) if isinstance(v, float) and v == int(v) else f"{v:.1f}")
                tbl.setItem(i, 1, it)
            gl = QVBoxLayout(grp)
            gl.addWidget(tbl)
            return grp, tbl

        rpm_grp, rpm_tbl = _make_axis_table("RPM Axis (rows)", rpm_ax, "RPM")
        load_grp, load_tbl = _make_axis_table("Load Axis (columns)", load_ax, "load")
        split.addWidget(rpm_grp)
        split.addWidget(load_grp)
        lay.addLayout(split)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)

        def _apply():
            # Read back edited values and update the table's axis display
            new_rpm = []
            for i in range(rpm_tbl.rowCount()):
                it = rpm_tbl.item(i, 1)
                try: new_rpm.append(float(it.text()) if it else rpm_ax[i])
                except ValueError: new_rpm.append(rpm_ax[i])
            new_load = []
            for i in range(load_tbl.rowCount()):
                it = load_tbl.item(i, 1)
                try: new_load.append(float(it.text()) if it else load_ax[i])
                except ValueError: new_load.append(load_ax[i])
            # Apply to map table headers
            self._table._row_axis = new_rpm
            self._table._col_axis = new_load
            from urrom.ecu_profiles import MapDef
            def _fmt(v):
                return f"{v:.1f}" if isinstance(v, float) and v != int(v) else str(int(v))
            row_labels = [_fmt(v) for v in reversed(new_rpm)]
            col_labels = [_fmt(v) for v in new_load]
            self._table.setVerticalHeaderLabels(row_labels)
            self._table.setHorizontalHeaderLabels(col_labels)
            dlg.accept()

        btns.accepted.connect(_apply)
        dlg.exec_()

    def _update_stats(self):
        """Recompute min/max/mean of selected (or all) cells and update strip."""
        if not self._maps or self._table._map_def is None:
            return
        sel = self._table.selectedRanges()
        if sel:
            cells = self._table._selected_cells()
            vals = []
            for disp_r, c in cells:
                raw_r = self._table._disp_to_raw(disp_r)
                if 0 <= raw_r < len(self._table._current_raw):
                    rv = self._table._current_raw[raw_r][c]
                    decode = self._table._map_def.decode
                    vals.append(decode(rv) if decode else float(rv))
            label = f"selection ({len(vals)} cells)"
        else:
            m = self._maps[self._map_combo.currentIndex()]
            decode = m.decode
            vals = []
            for r in range(m.rows):
                for c in range(m.cols):
                    rv = self._table._current_raw[r][c] if self._table._current_raw else 0
                    vals.append(decode(rv) if decode else float(rv))
            label = f"all ({m.rows}×{m.cols})"

        if not vals:
            return
        vmin = min(vals); vmax = max(vals)
        mean = sum(vals) / len(vals)
        unit = self._maps[self._map_combo.currentIndex()].unit or ""

        def _f(v): return f"{v:.1f}" if isinstance(v, float) and v != int(v) else str(int(v))
        self._stats_min.setText(f"min {_f(vmin)} {unit}")
        self._stats_max.setText(f"max {_f(vmax)} {unit}")
        self._stats_mean.setText(f"mean {_f(mean)} {unit}")
        self._stats_range.setText(f"range {_f(vmax-vmin)} {unit}  ·  {label}")

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
        self._inj_btn = QPushButton("⚙ Injector scaling…")
        self._inj_btn.setFixedHeight(26)
        self._inj_btn.setEnabled(False)
        self._inj_btn.setStyleSheet(
            f"QPushButton{{background:{BG3};color:{FG};border:1px solid {BORDER};"
            f"border-radius:3px;padding:0 10px;}}"
            f"QPushButton:hover{{border-color:{ACCENT};}}")
        hdr.addWidget(self._inj_btn)
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
            # (name, wh_off, decode_fn, encode_fn, unit)
            ("Hard RPM limit",        0x0617, lambda b: b*40,   lambda d: d//40,  "RPM"),
            ("LC speed threshold",    0x0620, lambda b: b*2,    lambda d: d//2,   "km/h"),
            ("LC ign retard RPM",     0x0625, lambda b: b*40,   lambda d: d//40,  "RPM"),
            ("LC ign angle (ATDC)",   0x062B, lambda b: b,      lambda d: d,      "°"),
            ("NLS min RPM",           0x063D, lambda b: b*40,   lambda d: d//40,  "RPM"),
            ("NLS ign angle (ATDC)",  0x0643, lambda b: b,      lambda d: d,      "°"),
            ("Spark cut knock RPM",   0x064C, lambda b: b*40,   lambda d: d//40,  "RPM"),
            ("LC ign cut RPM",        0x066E, lambda b: b*40,   lambda d: d//40,  "RPM"),
        ]

        for name, wh_off, decode_fn, encode_fn, unit in LC_SCALARS:
            row_w = QWidget()
            row_h = QHBoxLayout(row_w)
            row_h.setContentsMargins(0, 0, 0, 0)
            row_h.setSpacing(6)
            n_lbl = QLabel(name)
            n_lbl.setStyleSheet(f"color:{FG};font-size:11px;min-width:180px;")
            # Editable spinbox — shows decoded value; accepts decoded input
            from PyQt5.QtWidgets import QSpinBox
            spin = QSpinBox()
            spin.setRange(0, 255 * (40 if "RPM" in unit else 2 if "km/h" in unit else 1))
            spin.setSingleStep(40 if "RPM" in unit else 2 if "km/h" in unit else 1)
            spin.setSuffix(f"  {unit}")
            spin.setValue(0)
            spin.setEnabled(False)
            spin.setFixedWidth(120)
            spin.setStyleSheet(
                f"QSpinBox{{background:{BG2};color:{FG};border:1px solid {BORDER};"
                f"border-radius:3px;padding:1px 4px;font-size:11px;}}"
                f"QSpinBox:disabled{{color:{FG_DIM};}}")
            r_lbl = QLabel("")
            r_lbl.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
            addr_lbl = QLabel(f"WH 0x{wh_off:04X}")
            addr_lbl.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
            row_h.addWidget(n_lbl)
            row_h.addWidget(spin)
            row_h.addStretch()
            row_h.addWidget(r_lbl)
            row_h.addWidget(addr_lbl)
            lc_grid.addWidget(row_w)
            # Wire valueChanged → write back
            _off = wh_off; _enc = encode_fn; _dec = decode_fn
            spin.valueChanged.connect(
                lambda val, o=_off, e=_enc, d=_dec: self._on_lc_scalar_changed(o, d, e, val))
            self._lc_rows.append((spin, r_lbl, wh_off, decode_fn))

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

    def set_injector_callback(self, fn):
        """Register fn() called when injector scaling button is clicked."""
        self._inj_btn.setEnabled(fn is not None)
        if fn:
            try:
                self._inj_btn.clicked.disconnect()
            except TypeError:
                pass
            self._inj_btn.clicked.connect(fn)

    def update(self, wh: bytes | None, variant_name: str = "") -> None:
        """Called when main chip is loaded."""
        self._wh = wh
        self._variant_name = variant_name
        self._refresh()

    def set_boost(self, boost_bytes: bytes | None, filename: str = "") -> None:
        """Called when boost chip is loaded."""
        self._boost_bytes = boost_bytes
        if boost_bytes:
            import zlib, struct
            from urrom.ecu_profiles import KNOWN_CRCS, CHIP_REQUIREMENTS
            crc = zlib.crc32(boost_bytes[:0x8000]) & 0xFFFFFFFF
            known = KNOWN_CRCS.get(crc)
            build = struct.unpack_from(">H", boost_bytes, 0x3FFE)[0] if len(boost_bytes) >= 0x4000 else None
            boost_info = f"Boost chip: {filename or 'loaded'}  ({len(boost_bytes)} B)"
            if known:
                boost_info += f"  — {known[1]}"
            elif build:
                boost_info += f"  build 0x{build:04X}"
            if build == 0x0054:
                boost_info += "  ⚠ 034EFI custom (requires 300kPa MAP + matched fuel chip)"
            self._boost_lbl.setText(boost_info)
            self._boost_lbl.setStyleSheet(
                f"color:{'#ff9900' if build==0x0054 else FG_DIM};font-size:11px;")
        else:
            self._boost_lbl.setText("Boost chip: not loaded")
            self._boost_lbl.setStyleSheet(f"color:{FG_DIM};font-size:11px;")
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
        for (spin, r_lbl, wh_off, decode_fn) in self._lc_rows:
            spin.setEnabled(is_0202)
            if is_0202 and self._wh and wh_off < len(self._wh):
                raw = self._wh[wh_off]
                decoded = decode_fn(raw)
                spin.blockSignals(True)
                spin.setValue(decoded)
                spin.blockSignals(False)
                r_lbl.setText(f"raw 0x{raw:02X}")
            else:
                spin.blockSignals(True)
                spin.setValue(0)
                spin.blockSignals(False)
                r_lbl.setText("")


    # ── Boost chip file open ──────────────────────────────────────────────

    def _on_lc_scalar_changed(self, wh_off: int, decode_fn, encode_fn, value: int):
        """Write edited LC/NLS scalar back to working half and emit dirty signal."""
        if self._wh is None or not (wh_off < len(self._wh)):
            return
        raw = encode_fn(value)
        wh_list = bytearray(self._wh)
        wh_list[wh_off] = raw
        self._wh = bytes(wh_list)
        # Find raw_lbl and update
        for spin, r_lbl, off, dfn in self._lc_rows:
            if off == wh_off:
                r_lbl.setText(f"raw 0x{raw:02X} ← edited")
                r_lbl.setStyleSheet(f"color:{AMBER};font-size:10px;")
        if hasattr(self, "_scalar_changed_callback"):
            self._scalar_changed_callback(wh_off, raw)

    def set_scalar_changed_callback(self, fn):
        """Register fn(wh_off, raw_byte) called when a scalar is edited."""
        self._scalar_changed_callback = fn

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

        # Delta summary — one coloured block per map showing change intensity
        self._delta_strip = QWidget()
        self._delta_strip.setFixedHeight(18)
        self._delta_strip.setVisible(False)
        self._delta_strip.setToolTip("Change intensity per map — click to jump")
        layout.addWidget(self._delta_strip)

        # Export row
        exp_row = QHBoxLayout()
        self._jump_btn = QPushButton("⇒ Most changed map")
        self._jump_btn.setEnabled(False)
        self._jump_btn.setFixedHeight(24)
        self._jump_btn.setStyleSheet(
            f"QPushButton{{background:{BG3};color:{FG};border:1px solid {BORDER};"
            f"border-radius:3px;padding:0 10px;font-size:10px;}}"
            f"QPushButton:hover{{border-color:{ACCENT};}}")
        self._jump_btn.clicked.connect(self._on_jump_most_changed)
        exp_row.addWidget(self._jump_btn)
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

    def set_rom_b_direct(self, wh: bytes, label: str = "stock"):
        """Load ROM B programmatically without a file dialog."""
        self._rom_b = wh
        self._status.setText(f"ROM B: {label}  ({len(wh):,} bytes)  [auto-loaded]")
        self._export_btn.setEnabled(True)
        self._refresh()

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
        self._jump_btn.setEnabled(bool(self._rom_b))
        self._update_delta_strip()
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

    def _on_jump_most_changed(self):
        """Switch map selector to the map with the largest number of changed cells."""
        if self._rom_a is None or self._rom_b is None or not self._maps:
            return
        from urrom.ecu_profiles import read_map
        best_idx, best_count = 0, 0
        for i, m in enumerate(self._maps):
            if m.rows <= 1: continue
            try:
                ra = read_map(self._rom_a, m)
                rb = read_map(self._rom_b, m)
                count = sum(1 for r in range(m.rows) for c in range(m.cols)
                            if ra[r][c] != rb[r][c])
                if count > best_count:
                    best_count, best_idx = count, i
            except Exception:
                pass
        self._map_combo.setCurrentIndex(best_idx)

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

    def _update_delta_strip(self):
        """Build the coloured delta-intensity strip above the export row."""
        if self._rom_a is None or self._rom_b is None or not self._maps:
            self._delta_strip.setVisible(False)
            return
        from urrom.ecu_profiles import read_map
        from PyQt5.QtWidgets import QHBoxLayout, QLabel
        from PyQt5.QtCore import Qt

        # Clear old children
        old_lay = self._delta_strip.layout()
        if old_lay:
            while old_lay.count():
                item = old_lay.takeAt(0)
                if item.widget(): item.widget().deleteLater()
        else:
            old_lay = QHBoxLayout(self._delta_strip)
            old_lay.setContentsMargins(0,0,0,0)
            old_lay.setSpacing(1)

        max_pct = 0.0
        data = []
        for m in self._maps:
            if m.rows <= 1: continue
            try:
                ra = read_map(self._rom_a, m)
                rb = read_map(self._rom_b, m)
                changed = sum(1 for r in range(m.rows) for c in range(m.cols)
                              if ra[r][c] != rb[r][c])
                pct = changed / (m.rows * m.cols)
                data.append((m.name, pct, changed))
                max_pct = max(max_pct, pct)
            except Exception:
                data.append((m.name, 0.0, 0))

        for name, pct, count in data:
            intensity = pct / max_pct if max_pct > 0 else 0
            if intensity < 0.05:
                bg = BG2
            elif intensity < 0.3:
                bg = "#1a2a10"
            elif intensity < 0.6:
                bg = "#2a3a10"
            else:
                bg = "#3a5010"
            cell = QLabel()
            cell.setFixedHeight(16)
            cell.setToolTip(f"{name}: {count} cells changed ({pct*100:.0f}%)")
            cell.setStyleSheet(f"background:{bg};border:none;")
            old_lay.addWidget(cell, 1)

        self._delta_strip.setVisible(True)

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
        self._main_chip_tab.set_title_fn(lambda name: self._update_title(name))

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
        help_menu = mb.addMenu("Help")
        shortcuts_act = QAction("Keyboard shortcuts…", self)
        shortcuts_act.triggered.connect(self._on_show_shortcuts)
        about_act = QAction("About UrROM", self)
        about_act.triggered.connect(self._on_about)
        help_menu.addAction(shortcuts_act)
        help_menu.addSeparator()
        help_menu.addAction(about_act)

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
        find_act = QAction("Find in maps… Ctrl+F", self)
        find_act.setShortcut("Ctrl+F")
        find_act.triggered.connect(self._on_find_in_maps)
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
        # Update window title to show ROM name
        from urrom.version import APP_VERSION, APP_NAME
        self._refresh_title()
        self._info_strip.update(det)
        self._overview_tab.update(det, self._boost_det if hasattr(self, "_boost_det") else None)
        self._hardware_tab.update(bytes(wh), det.variant.name if det.variant else "")
        # Wire LC/NLS scalar edits -> dirty flag + ROM write-back
        def _on_scalar_changed(wh_off: int, raw: int):
            if self._main_rom and wh_off < len(self._main_rom):
                self._main_rom[wh_off] = raw
                self._set_dirty()
        self._hardware_tab.set_scalar_changed_callback(_on_scalar_changed)
        self._hardware_tab.set_injector_callback(self._on_injector_scaling)
        self._save_btn.setEnabled(True)

        if det.variant:
            # Auto-inject XDF maps for 551B/C if the community XDF is available
            self._try_auto_xdf(det.variant)
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
        status_msg = f"Boost chip: {path.name}  CRC32 0x{crc:08X}  build 0x{bld:04X}"

        # Check fuel+boost pairing
        if self._det and boost_det:
            from urrom.ecu_profiles import check_chip_pair
            pair_status, pair_msg = check_chip_pair(self._det.crc32, boost_det.crc32)
            if pair_status == 'mismatch':
                QMessageBox.warning(self, "Boost Chip Mismatch", pair_msg)
                status_msg += "  ⚠ MISMATCH"
            elif pair_status == 'ok' and 'confirmed' in pair_msg.lower():
                status_msg += "  ✓ paired"
        self._update_status(status_msg)

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

    def _on_injector_scaling(self):
        """Guided injector scaling wizard — rescales all fuel maps proportionally."""
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
                                      QLabel, QSpinBox, QDoubleSpinBox, QCheckBox,
                                      QDialogButtonBox, QFrame)

        dlg = QDialog(self)
        dlg.setWindowTitle("Injector scaling wizard")
        dlg.setMinimumWidth(420)
        dlg.setStyleSheet(f"background:{BG};color:{FG};")
        lay = QVBoxLayout(dlg)
        lay.setSpacing(10)

        # Info
        info = QLabel(
            "Rescales all fuel map values when changing injector size.\n"
            "Formula: new_raw = old_raw x (stock_cc / new_cc)\n\n"
            "Common injector sizes:\n"
            "  Stock ABY/AAN: 293 cc/min\n"
            "  RS2 / 034 Stage 1: 440 cc/min (Bosch) or 550 cc/min\n"
            "  034 Stage 1 EV14: 550 cc/min\n"
            "  034 Stage 1 440cc Siemens: 440 cc/min")
        info.setStyleSheet(f"color:{FG_DIM};font-size:11px;background:{BG2};"
                           f"padding:8px;border-radius:4px;")
        info.setWordWrap(True)
        lay.addWidget(info)

        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color:{BORDER};"); lay.addWidget(sep)

        form = QFormLayout()
        form.setSpacing(8)

        stock_spin = QSpinBox()
        stock_spin.setRange(100, 2000); stock_spin.setValue(293)
        stock_spin.setSuffix(" cc/min")
        stock_spin.setStyleSheet(f"background:{BG2};color:{FG};border:1px solid {BORDER};border-radius:3px;padding:2px 4px;")

        new_spin = QSpinBox()
        new_spin.setRange(100, 2000); new_spin.setValue(440)
        new_spin.setSuffix(" cc/min")
        new_spin.setStyleSheet(f"background:{BG2};color:{FG};border:1px solid {BORDER};border-radius:3px;padding:2px 4px;")

        factor_lbl = QLabel("Scale factor: 0.666×")
        factor_lbl.setStyleSheet(f"color:{ACCENT};font-weight:bold;")

        def _update_factor():
            s, n = stock_spin.value(), new_spin.value()
            if n > 0:
                f = s / n
                factor_lbl.setText(f"Scale factor: {f:.4f}×  ({f*100:.1f}% of original)")

        stock_spin.valueChanged.connect(_update_factor)
        new_spin.valueChanged.connect(_update_factor)
        _update_factor()

        # FPR scaling option
        fpr_check = QCheckBox("Also adjust for different fuel pressure")
        fpr_check.setStyleSheet(f"color:{FG};")
        fpr_frame = QFrame()
        fpr_lay = QHBoxLayout(fpr_frame)
        fpr_lay.setContentsMargins(20, 0, 0, 0)
        stock_fpr = QDoubleSpinBox(); stock_fpr.setRange(1.0, 10.0); stock_fpr.setValue(3.0)
        stock_fpr.setSuffix(" bar stock")
        new_fpr = QDoubleSpinBox(); new_fpr.setRange(1.0, 10.0); new_fpr.setValue(5.0)
        new_fpr.setSuffix(" bar new")
        for w in (stock_fpr, new_fpr):
            w.setStyleSheet(f"background:{BG2};color:{FG};border:1px solid {BORDER};border-radius:3px;padding:2px 4px;")
            fpr_lay.addWidget(w)
        fpr_frame.setEnabled(False)
        fpr_check.toggled.connect(fpr_frame.setEnabled)
        fpr_check.toggled.connect(_update_factor)

        form.addRow("Stock injector size:", stock_spin)
        form.addRow("New injector size:", new_spin)
        form.addRow("", factor_lbl)
        form.addRow("", fpr_check)
        form.addRow("", fpr_frame)
        lay.addLayout(form)

        warn = QLabel("\u26a0 This modifies ALL confirmed fuel maps in the ROM.\n"
                      "Use Revert or Undo (Ctrl+Z) to roll back.")
        warn.setStyleSheet(f"color:{AMBER};font-size:10px;")
        warn.setWordWrap(True)
        lay.addWidget(warn)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)

        def _apply():
            s = stock_spin.value(); n = new_spin.value()
            if n == 0: return
            factor = s / n
            if fpr_check.isChecked() and stock_fpr.value() > 0:
                # Flow scales as sqrt(pressure ratio)
                import math
                factor *= math.sqrt(new_fpr.value() / stock_fpr.value())
            # Apply to all confirmed fuel maps
            from urrom.ecu_profiles import read_map, write_map
            v = self._det.variant if self._det else None
            if not v: return
            fuel_maps = [m for m in v.main_maps
                         if m.map_type == 'fuel' and m.confidence == 'CONFIRMED' and m.rows > 1]
            for m in fuel_maps:
                data = read_map(bytes(self._main_rom), m)
                scaled = [[max(0, min(255, round(data[r][c] * factor)))
                           for c in range(m.cols)] for r in range(m.rows)]
                self._main_rom = bytearray(write_map(bytes(self._main_rom), m, scaled))
            # Reload the map editor
            self._main_chip_tab.load(self._main_rom, v)
            self._compare_tab.set_rom_a(bytes(self._main_rom), v)
            self._set_dirty()
            n_maps = len(fuel_maps)
            self._update_status(
                f"Injector scaling applied: {s}cc → {n}cc  (×{factor:.4f}) "
                f"across {n_maps} fuel maps")
            dlg.accept()

        btns.accepted.connect(_apply)
        dlg.exec_()

    def _on_scan_issues(self):
        """Run automated tuning health checks and show results dialog."""
        if self._main_rom is None or self._det is None or not self._det.variant:
            QMessageBox.information(self, "Scan for issues", "Load a ROM first.")
            return

        from urrom.tuning_checks import run_all_checks
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                                      QLabel, QTableWidget, QTableWidgetItem,
                                      QDialogButtonBox, QHeaderView, QProgressBar)

        v   = self._det.variant
        crc = self._det.crc32
        boost = bytes(self._boost_rom) if self._boost_rom else None

        # Run checks (may take a moment for large map sets)
        issues = run_all_checks(bytes(self._main_rom), v, boost_rom=boost, crc32=crc)

        # Build results dialog
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Scan results — {v.name}")
        dlg.setMinimumSize(700, 440)
        dlg.setStyleSheet(f"background:{BG};color:{FG};")
        lay = QVBoxLayout(dlg)

        # Summary row
        n_err  = sum(1 for i in issues if i.severity == 'error')
        n_warn = sum(1 for i in issues if i.severity == 'warning')
        n_info = sum(1 for i in issues if i.severity == 'info')

        summary_lbl = QLabel(
            f"<b style='color:{'#ff4444' if n_err else '#2dff6e'};'>"
            f"{'⚠ ' if n_err else '✓ '}{n_err} errors</b>"
            f"  ·  <span style='color:#ffaa00;'>{n_warn} warnings</span>"
            f"  ·  <span style='color:#6e7681;'>{n_info} info</span>"
            f"  ·  {len(issues)} total")
        summary_lbl.setTextFormat(Qt.RichText)
        summary_lbl.setStyleSheet("font-size:12px;padding:4px 0;")
        lay.addWidget(summary_lbl)

        # Issues table
        tbl = QTableWidget(len(issues), 4)
        tbl.setHorizontalHeaderLabels(["Severity", "Category", "Map", "Description"])
        tbl.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        tbl.setAlternatingRowColors(True)
        tbl.setStyleSheet(
            f"QTableWidget{{background:{BG2};color:{FG};gridline-color:{BORDER};}}"
            f"QTableWidget::item:alternate{{background:{BG};}}")
        tbl.verticalHeader().setVisible(False)
        lay.addWidget(tbl)

        SEV_COLOURS = {'error': RED, 'warning': AMBER, 'info': FG_DIM}
        for row_i, iss in enumerate(issues):
            col = SEV_COLOURS.get(iss.severity, FG)
            for col_i, text in enumerate([
                iss.severity.upper(), iss.category,
                iss.map_name, iss.description
            ]):
                it = QTableWidgetItem(text)
                it.setData(Qt.UserRole, iss)
                if col_i == 0:
                    it.setForeground(QBrush(QColor(col)))
                tbl.setItem(row_i, col_i, it)

        def _on_issue_click(row, col):
            it = tbl.item(row, 0)
            if not it: return
            iss = it.data(Qt.UserRole)
            if not iss or not iss.cell or iss.cell[1] is None:
                return
            # Jump to the cell in the map editor
            try:
                map_idx = next(i for i, m in enumerate(self._main_chip_tab._maps)
                               if m.name == iss.map_name)
                self._tabs.setCurrentWidget(self._main_chip_tab)
                self._main_chip_tab._map_combo.setCurrentIndex(map_idx)
                r, c = iss.cell
                disp_r = self._main_chip_tab._table._map_def.rows - 1 - r
                self._main_chip_tab._table.scrollToItem(
                    self._main_chip_tab._table.item(disp_r, c))
                self._main_chip_tab._table.setCurrentCell(disp_r, c)
            except (StopIteration, Exception):
                pass

        tbl.cellDoubleClicked.connect(_on_issue_click)

        hint = QLabel("Double-click a row to jump to that cell in the map editor.")
        hint.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
        lay.addWidget(hint)

        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)
        dlg.exec_()

    def _try_auto_xdf(self, variant) -> None:
        """
        Silently inject XDF maps for known variants if the community XDF is present.
        Runs transparently — no dialogs, no errors if XDF not found.
        """
        # This XDF uses prjmod firmware layout (64KB doubled ROM, 0x0202 firmware)
        # Only inject into 551AA_0202 (prjmod) variants — stock 551B/C have different map offsets
        XDF_MAP = {
            "551AA_0202": "rs2_xdf/RS2 551B fuel timing.xdf",
        }
        sw_id = variant.software_id
        xdf_rel = XDF_MAP.get(sw_id)
        if not xdf_rel:
            return
        # Look relative to app and cwd
        for base in [Path(__file__).parent.parent, Path(".")]:
            xdf_path = base / xdf_rel
            if xdf_path.exists():
                break
        else:
            return  # XDF not found — silently skip

        try:
            from urrom.xdf_import import parse_xdf, xdf_to_mapdefs
            result = parse_xdf(xdf_path)
            new_maps = xdf_to_mapdefs(result, filter_min_cells=16)
        except Exception:
            return

        if not new_maps:
            return

        existing_addrs = {m.main_addr for m in variant.main_maps + variant.boost_maps}
        added = [m for m in new_maps if m.main_addr not in existing_addrs]
        if added:
            variant.main_maps = variant.main_maps + added
            self._update_status(
                f"Auto-imported {len(added)} maps from {xdf_path.name} ({sw_id})")

    def _on_compare_to_stock(self):
        """Auto-load the matching stock ROM from roms/ and open Compare tab."""
        if self._main_rom is None or self._det is None or not self._det.variant:
            QMessageBox.information(self, "Compare to stock", "Load a ROM first.")
            return
        import sys as _sys
        sw_id = self._det.variant.software_id
        # Map software IDs to bundled stock ROM files
        # Stock baseline ROMs bundled with UrROM
        # 034EFI prjmod tunes (551AA_0202) compare against the prjmod stock rip chip
        # which is itself a prjmod ROM, so deltas show only the tune changes
        STOCK_ROMS = {
            "551B":       "roms/aby_fuel-ign_551aa.bin",
            "551C":       "roms/adu_fuel-ign_551c.bin",
            "551AA":      "roms/aan_fuel-ign_551aa.bin",
            "551A":       "roms/aan_fuel-ign_551a.bin",
            "551AA_0202": "roms/aan_fuel-ign_551aa.bin",
            "551B_D02":   "roms/aby_fuel-ign_551aa.bin",
            "404":        "roms/3b_fuel-ign_404aa.bin",
        }
        # Find the app base directory
        app_base = Path(__file__).parent.parent
        stock_rel = STOCK_ROMS.get(sw_id)
        if not stock_rel:
            QMessageBox.information(self, "Compare to stock",
                f"No bundled stock ROM for variant {sw_id}.\n"
                "Use File → Load ROM then Compare tab to compare manually.")
            return
        stock_path = app_base / stock_rel
        if not stock_path.exists():
            # Try relative to cwd
            stock_path = Path(stock_rel)
        if not stock_path.exists():
            QMessageBox.warning(self, "Stock ROM not found",
                f"Could not find: {stock_rel}\n"
                "Ensure you're running from the UrROM directory.")
            return
        try:
            stock_raw = stock_path.read_bytes()
        except OSError as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        from urrom.ecu_profiles import normalize_rom
        stock_wh, _ = normalize_rom(stock_raw)
        self._compare_tab.set_rom_b_direct(bytes(stock_wh), stock_path.name)
        self._tabs.setCurrentWidget(self._compare_tab)
        self._update_status(f"Comparing to stock: {stock_path.name}")

    def _on_find_in_maps(self):
        """Search all confirmed maps for cells matching a value condition."""
        if self._main_rom is None or self._det is None or not self._det.variant:
            QMessageBox.information(self, "Find in maps", "Load a ROM first.")
            return
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                                      QLabel, QComboBox, QDoubleSpinBox,
                                      QDialogButtonBox, QTableWidget,
                                      QTableWidgetItem, QHeaderView)
        dlg = QDialog(self)
        dlg.setWindowTitle("Find in maps")
        dlg.setMinimumWidth(560)
        dlg.setStyleSheet(f"background:{BG};color:{FG};")
        lay = QVBoxLayout(dlg)
        lay.setSpacing(8)

        # Query row
        q_row = QHBoxLayout()
        q_row.addWidget(QLabel("Find cells where decoded value"))
        op_cb = QComboBox()
        op_cb.addItems([">", "≥", "<", "≤", "=", "≠"])
        op_cb.setFixedWidth(50)
        val_sb = QDoubleSpinBox()
        val_sb.setRange(-999, 9999)
        val_sb.setDecimals(2)
        val_sb.setValue(0.0)
        val_sb.setFixedWidth(90)
        for w in (op_cb, val_sb):
            w.setStyleSheet(f"background:{BG3};color:{FG};border:1px solid {BORDER};border-radius:3px;")
        q_row.addWidget(op_cb)
        q_row.addWidget(val_sb)
        q_row.addStretch()
        lay.addLayout(q_row)

        # Results table
        results_tbl = QTableWidget(0, 4)
        results_tbl.setHorizontalHeaderLabels(["Map", "Row", "Col", "Value"])
        results_tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        results_tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        results_tbl.setStyleSheet(f"background:{BG2};color:{FG};gridline-color:{BORDER};")
        results_tbl.setAlternatingRowColors(True)
        results_tbl.setStyleSheet(
            f"QTableWidget{{background:{BG2};color:{FG};gridline-color:{BORDER};}}"
            f"QTableWidget::item:alternate{{background:{BG};}}") 
        lay.addWidget(results_tbl)

        status_lbl = QLabel("")
        status_lbl.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
        lay.addWidget(status_lbl)

        btns = QDialogButtonBox(QDialogButtonBox.Close)
        search_btn = btns.addButton("Search", QDialogButtonBox.ActionRole)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)

        def _run_search():
            op  = op_cb.currentText()
            thr = val_sb.value()
            ops = {">": lambda v, t: v > t, "≥": lambda v, t: v >= t,
                   "<": lambda v, t: v < t,  "≤": lambda v, t: v <= t,
                   "=": lambda v, t: abs(v - t) < 0.5, "≠": lambda v, t: abs(v - t) >= 0.5}
            cmp_fn = ops[op]
            from urrom.ecu_profiles import read_map
            v = self._det.variant
            hits = []
            for m in (v.main_maps or []):
                if m.rows <= 1 or m.confidence == "UNCONFIRMED":
                    continue
                raw_data = read_map(bytes(self._main_rom), m)
                decode = m.decode
                for r in range(m.rows):
                    for c in range(m.cols):
                        rv = raw_data[r][c]
                        dv = decode(rv) if decode else float(rv)
                        if dv is not None and cmp_fn(float(dv), thr):
                            hits.append((m.name, r, c, dv, m))
            results_tbl.setRowCount(len(hits))
            for row_i, (name, r, c, dv, m) in enumerate(hits[:500]):
                fmt = f"{dv:.2f} {m.unit}" if isinstance(dv, float) else str(dv)
                for col_i, text in enumerate([name, str(r), str(c), fmt]):
                    it = QTableWidgetItem(text)
                    it.setData(Qt.UserRole, (m, r, c))
                    results_tbl.setItem(row_i, col_i, it)
            over = f"  (showing first 500)" if len(hits) > 500 else ""
            status_lbl.setText(f"{len(hits)} cells match  {op} {thr}{over}")

        def _on_result_click(row, col):
            it = results_tbl.item(row, 0)
            if not it: return
            m, r, c = it.data(Qt.UserRole)
            # Jump to that map
            try:
                map_idx = next(i for i,mm in enumerate(self._main_chip_tab._maps) if mm is m)
                self._tabs.setCurrentWidget(self._main_chip_tab)
                self._main_chip_tab._map_combo.setCurrentIndex(map_idx)
                # Scroll to cell in table
                disp_r = m.rows - 1 - r
                self._main_chip_tab._table.scrollToItem(
                    self._main_chip_tab._table.item(disp_r, c))
                self._main_chip_tab._table.setCurrentCell(disp_r, c)
            except StopIteration:
                pass

        search_btn.clicked.connect(_run_search)
        results_tbl.cellDoubleClicked.connect(_on_result_click)
        _run_search()
        dlg.exec_()

    def _on_export_map_html(self):
        """Export the currently displayed map as a standalone HTML file."""
        if self._main_rom is None or self._det is None:
            QMessageBox.information(self, "Export map", "Load a ROM first.")
            return
        tab = self._main_chip_tab
        if not tab._maps or tab._map_combo.currentIndex() < 0:
            return
        m = tab._maps[tab._map_combo.currentIndex()]
        v = self._det.variant
        path, _ = QFileDialog.getSaveFileName(
            self, "Export map as HTML",
            f"{m.name.replace(' ', '_')}.html",
            "HTML (*.html);;All files (*.*)")
        if not path:
            return
        from urrom.map_export import export_map_html
        html_str = export_map_html(
            bytes(self._main_rom), m, v, include_header=True, include_raw=True)
        # Wrap in a full page
        full = (f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
                f"<title>{m.name}</title>"
                f"<style>body{{background:#0d1117;padding:16px;}}</style></head>"
                f"<body>{html_str}</body></html>")
        Path(path).write_text(full, encoding="utf-8")
        self._update_status(f"Exported → {Path(path).name}")

    def _on_export_rom_html(self):
        """Export all confirmed maps as a single printable HTML reference."""
        if self._main_rom is None or self._det is None:
            QMessageBox.information(self, "Export ROM", "Load a ROM first.")
            return
        v = self._det.variant
        if not v:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export ROM reference",
            f"{v.software_id}_map_reference.html",
            "HTML (*.html);;All files (*.*)")
        if not path:
            return
        from urrom.map_export import export_full_rom_html
        html_str = export_full_rom_html(bytes(self._main_rom), v, self._det)
        Path(path).write_text(html_str, encoding="utf-8")
        n = sum(1 for m in (v.main_maps or [])
                if m.rows > 1 and m.cols > 1
                and m.confidence in ("CONFIRMED", "PROVISIONAL"))
        self._update_status(f"Exported {n} maps → {Path(path).name}")

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

    def _on_show_shortcuts(self):
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QDialogButtonBox
        dlg = QDialog(self)
        dlg.setWindowTitle("Keyboard Shortcuts")
        dlg.setMinimumWidth(400)
        dlg.setStyleSheet(f"background:{BG};color:{FG};")
        lay = QVBoxLayout(dlg)
        shortcuts = [
            ("File", [
                ("Ctrl+O", "Open ROM"),
                ("Ctrl+S", "Save ROM"),
                ("Ctrl+F", "Find in maps"),
            ]),
            ("Map Editor", [
                ("Ctrl+Z", "Undo"),
                ("Ctrl+Y / Ctrl+Shift+Z", "Redo"),
                ("Ctrl+C", "Copy selection (TSV)"),
                ("Ctrl+V", "Paste"),
                ("Ctrl+A", "Select all"),
                ("Del",    "Clear selection"),
                ("Double-click", "Edit cell (type decoded value)"),
                ("Right-click",  "Context menu: scale, interpolate, smooth…"),
            ]),
            ("Navigation", [
                ("Overview tab → double-click map row", "Jump to map editor"),
                ("Ctrl+F result → double-click",        "Jump to cell in map"),
                ("⊞ Grid button", "All-maps thumbnail overview"),
            ]),
        ]
        for section, keys in shortcuts:
            sec_lbl = QLabel(section.upper())
            sec_lbl.setStyleSheet(
                f"color:{FG_DIM};font-size:10px;letter-spacing:1px;"
                f"margin-top:8px;")
            lay.addWidget(sec_lbl)
            for key, desc in keys:
                row = QLabel(f'  <b style="color:{ACCENT};">{key}</b>'
                             f'  <span style="color:{FG};">— {desc}</span>')
                row.setTextFormat(Qt.RichText)
                lay.addWidget(row)
        btns = QDialogButtonBox(QDialogButtonBox.Ok)
        btns.accepted.connect(dlg.accept)
        lay.addWidget(btns)
        dlg.exec_()

    def _on_about(self):
        from PyQt5.QtWidgets import QMessageBox
        from urrom.version import APP_VERSION
        QMessageBox.about(self, "About UrROM",
            f"<b>UrROM v{APP_VERSION}</b><br>"
            "<br>"
            "Open-source ROM editor for Bosch Motronic M2.3 / M2.3.2<br>"
            "Audi 5-cylinder 20v turbo and V8 engines<br>"
            "<br>"
            "Supported variants: 551A/AA/B/C, 551B_D02, 551AA_0202,<br>"
            "404 (3B), 404V8 (PT), 557 (ABH)<br>"
            "<br>"
            "Built with Python + PyQt5<br>"
            '<a href="https://github.com/dspl1236/UrROM">'
            "github.com/dspl1236/UrROM</a>")

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
        self._refresh_title()

    def _refresh_title(self):
        from urrom.version import APP_VERSION, APP_NAME
        if self._main_path and self._det and self._det.variant:
            variant_short = self._det.variant.software_id
            dirty_mark = " •" if self._unsaved else ""
            self.setWindowTitle(
                f"{APP_NAME}  v{APP_VERSION}  —  "
                f"{self._main_path.name}  [{variant_short}]{dirty_mark}")
        else:
            self.setWindowTitle(WINDOW_TITLE)

    def _update_status(self, msg: str):
        self._status.showMessage(msg)

    def _update_title(self, extra: str = ""):
        """Update window title with ROM name and optional context."""
        from urrom.version import WINDOW_TITLE
        if self._main_path:
            dirty = " ●" if self._unsaved else ""
            map_ctx = f"  —  {extra}" if extra else ""
            self.setWindowTitle(f"{WINDOW_TITLE}  —  {self._main_path.name}{dirty}{map_ctx}")
        else:
            self.setWindowTitle(WINDOW_TITLE)

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
