"""
Small shared widgets: the changed-cell delegate, the chip info strip, the edit line and the view switch.

Split out of app/main.py (2026-10-14); the classes and helpers are unchanged.
"""
from PyQt5.QtGui import QColor, QPen
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QStyledItemDelegate
from PyQt5.QtCore import Qt

from urrom.ecu_profiles import DetectionResult, get_boost_pairing
from urrom.ui.theme import ACCENT, AMBER, BG2, BG3, BORDER, CHANGED, FG, FG_DIM, GREEN, RED

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
        self._pair_lbl = QLabel("")
        self._pair_lbl.setStyleSheet(f"color: {AMBER}; font-size: 10px;")
        self._req_lbl.setStyleSheet(f"color: {AMBER}; font-size: 10px;")
        self._req_lbl.setWordWrap(True)
        self._req_lbl.setVisible(False)

        layout.addWidget(self._variant_lbl)
        layout.addWidget(self._build_lbl)
        layout.addWidget(self._cs_lbl)
        layout.addWidget(self._conf_lbl)
        layout.addStretch()
        layout.addWidget(self._req_lbl)
        layout.addWidget(self._pair_lbl)

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
        from urrom.ecu_profiles import checksum_kind
        kind = checksum_kind(det.variant)
        if kind is not None:
            # 551: PRJmod sum at 0x3FFA; 404: Bosch 16-bit sum at 0x7F00
            where = "0x7F00" if kind == "404" else "0x3FFA"
            if det.checksum_ok:
                self._cs_lbl.setText(f"✓ checksum OK ({where})")
                self._cs_lbl.setStyleSheet(f"color: {GREEN}; font-size: 11px;")
            else:
                self._cs_lbl.setText(f"✗ checksum BAD ({where})")
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


# ── Edit line: what the last edit changed + guard rails ───────────────────────

def make_edit_line(layout) -> QLabel:
    lbl = QLabel("")
    lbl.setStyleSheet(
        f"color:{FG};font-size:10px;padding:3px 8px;background:{BG2};"
        f"border-left:2px solid {BORDER};border-radius:2px;")
    lbl.setWordWrap(True)
    lbl.setTextFormat(Qt.RichText)
    lbl.setVisible(False)
    layout.addWidget(lbl)
    return lbl


def show_edit_line(lbl: QLabel, summary: str, guards) -> None:
    from urrom.guards import worst
    level = worst(guards) if guards else ""
    border = RED if level == "stop" else AMBER if level == "warn" else BORDER
    lbl.setStyleSheet(
        f"color:{FG};font-size:10px;padding:3px 8px;background:{BG2};"
        f"border-left:3px solid {border};border-radius:2px;")
    parts = [f"<b>Edit</b> {summary}"]
    for g in guards:
        col = RED if g.level == "stop" else AMBER if g.level == "warn" else FG_DIM
        mark = "⛔" if g.level == "stop" else "⚠" if g.level == "warn" else "ⓘ"
        parts.append(f"<span style='color:{col}'>{mark} {g.text}</span>")
    lbl.setText("<br>".join(parts))
    lbl.setVisible(True)


# ── Map view switch (Table / Heat / 3D) shared by the editor tabs ─────────────

def make_view_switch(owner, toolbar) -> dict:
    """Three checkable buttons; owner._set_view(mode) is called on click."""
    btns = {}
    for mode, label, tip in (("table", "Table", "Editable cell grid"),
                             ("heat", "Heat", "2D heat map of the decoded values"),
                             ("3d", "3D", "Rotatable surface (drag to orbit)")):
        b = QPushButton(label)
        b.setCheckable(True)
        b.setFixedWidth(52)
        b.setToolTip(tip)
        b.setStyleSheet(
            f"QPushButton{{background:{BG3};color:{FG};border:1px solid {BORDER};"
            f"border-radius:3px;padding:0 6px;font-size:10px;}}"
            f"QPushButton:checked{{background:{ACCENT}30;border-color:{ACCENT};}}"
            f"QPushButton:hover{{border-color:{ACCENT};}}")
        b.clicked.connect(lambda _c, m=mode: owner._set_view(m))
        toolbar.addWidget(b)
        btns[mode] = b
    btns["table"].setChecked(True)
    return btns


def view_setting_key(owner) -> str:
    return f"view/{type(owner).__name__}"
