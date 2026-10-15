"""
Overview tab: chip identity, pairing and requirements.

Split out of app/main.py (2026-10-14); the classes and helpers are unchanged.
"""
from PyQt5.QtGui import QBrush, QColor
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtCore import Qt

from urrom.ecu_profiles import CHIP_REQUIREMENTS, DetectionResult, get_boost_pairing
from urrom.ui.theme import ACCENT, AMBER, BG, BG2, BG3, BORDER, FG, FG_DIM, GREEN, RED

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
        self._info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self._info)

        # Chip info block — stacked so it fits a narrow inspector panel
        self._main_chip_lbl = QLabel("Main chip: —")
        self._main_chip_lbl.setStyleSheet(f"color:{FG};font-size:11px;")
        self._main_chip_lbl.setWordWrap(True)
        self._boost_chip_lbl = QLabel("Boost chip: —")
        self._boost_chip_lbl.setStyleSheet(f"color:{FG};font-size:11px;")
        self._boost_chip_lbl.setWordWrap(True)
        self._checksum_lbl = QLabel("")
        self._checksum_lbl.setStyleSheet(f"color:{FG_DIM};font-size:11px;")
        self._checksum_lbl.setWordWrap(True)
        self._health_badge = QLabel("")
        self._health_badge.setStyleSheet(
            f"font-size:10px;padding:2px 8px;border-radius:10px;"
            f"background:{BG3};border:1px solid {BORDER};")
        self._health_badge.setVisible(False)
        layout.addWidget(self._main_chip_lbl)
        layout.addWidget(self._boost_chip_lbl)
        chip_row = QHBoxLayout()
        chip_row.addWidget(self._health_badge)
        chip_row.addSpacing(8)
        chip_row.addWidget(self._checksum_lbl, 1)
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
        self._inventory_widgets = [map_title, hint]

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

    def set_compact(self, compact: bool = True):
        """
        Workbench mode: the map navigator dock already lists every map, so
        hide the inventory table and let the panel fit a narrow inspector.
        """
        self._map_table.setVisible(not compact)
        for w in self._inventory_widgets:
            w.setVisible(not compact)
        if compact:
            self.layout().setContentsMargins(10, 10, 10, 10)

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
        if boost_det is not None:
            from urrom.ecu_profiles import KNOWN_CRCS as _KC
            bl = _KC.get(getattr(boost_det, "crc32", 0), (None, ""))[1]
            what = (boost_det.variant.software_id if getattr(boost_det, "variant", None)
                    else (bl.split(" — ")[0] if bl else "loaded"))
            self._boost_chip_lbl.setText(
                f"Boost chip: {what}  •  CRC 0x{getattr(boost_det, 'crc32', 0):08X}")
            self._boost_chip_lbl.setStyleSheet(f"color:{GREEN};font-size:11px;")
        else:
            self._boost_chip_lbl.setText("Boost chip: not loaded")
            self._boost_chip_lbl.setStyleSheet(f"color:{FG_DIM};font-size:11px;")

        # Checksum state
        sw_id = v.software_id if v else ""
        from urrom.ecu_profiles import checksum_kind
        kind = checksum_kind(v)
        if kind == "551":
            self._checksum_lbl.setText("PRJmod checksum at 0x3FFA: applied on save")
        elif kind == "404":
            self._checksum_lbl.setText("Bosch 16-bit checksum at 0x7F00 (ECU checks it at boot): applied on save")
        else:
            self._checksum_lbl.setText("No software checksum on this chip")

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
        from urrom.ecu_profiles import CHECKSUM_VARIANTS
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

        # Run tuning health check and update badge
        from urrom.tuning_checks import run_all_checks
        try:
            issues = run_all_checks(wh_bytes := bytes(det.crc32.to_bytes(4, 'big'))
                                    if False else b'',
                                    v, crc32=det.crc32)
            # We need the actual ROM bytes — check if passed via _rom_bytes attr
            if hasattr(self, '_rom_bytes') and self._rom_bytes:
                issues = run_all_checks(self._rom_bytes, v, crc32=det.crc32)
                n_err  = sum(1 for i in issues if i.severity == 'error')
                n_warn = sum(1 for i in issues if i.severity == 'warning')
                if n_err:
                    badge_txt = f"✗ {n_err} error{'s' if n_err!=1 else ''}"
                    badge_col = RED
                elif n_warn:
                    badge_txt = f"⚠ {n_warn} warning{'s' if n_warn!=1 else ''}"
                    badge_col = AMBER
                else:
                    badge_txt = "✓ clean"
                    badge_col = GREEN
                self._health_badge.setText(badge_txt)
                self._health_badge.setStyleSheet(
                    f"font-size:10px;padding:2px 8px;border-radius:10px;"
                    f"background:{BG3};border:1px solid {badge_col};"
                    f"color:{badge_col};")
                self._health_badge.setVisible(True)
            else:
                self._health_badge.setVisible(False)
        except Exception:
            self._health_badge.setVisible(False)

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
