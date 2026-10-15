"""
Hardware tab: firmware patches and hardware-level settings.

Split out of app/main.py (2026-10-14); the classes and helpers are unchanged.
"""
from pathlib import Path

from PyQt5.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtCore import Qt

from urrom.ui.theme import ACCENT, AMBER, BG, BG2, BG3, BORDER, FG, FG_DIM, GREEN

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
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        # ── Header row ────────────────────────────────────────────────────
        title = QLabel("Hardware & Patch Status")
        title.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {FG};")
        outer.addWidget(title)

        # What the loaded chip supports (filled by _refresh)
        self._chip_lbl = QLabel("No ROM loaded.")
        self._chip_lbl.setStyleSheet(
            f"color:{FG};font-size:11px;padding:6px 8px;background:{BG2};"
            f"border-left:2px solid {ACCENT};border-radius:2px;")
        self._chip_lbl.setWordWrap(True)
        outer.addWidget(self._chip_lbl)

        from PyQt5.QtWidgets import QCheckBox
        self._show_all_chk = QCheckBox("Show features that do not apply to this chip")
        self._show_all_chk.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
        self._show_all_chk.toggled.connect(lambda _: self._refresh())
        outer.addWidget(self._show_all_chk)

        self._boost_lbl = QLabel("Boost chip: not loaded")
        self._boost_lbl.setStyleSheet(f"color: {FG_DIM}; font-size: 11px;")
        self._boost_lbl.setWordWrap(True)
        outer.addWidget(self._boost_lbl)

        hdr = QHBoxLayout()
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
        hdr.addStretch()
        outer.addLayout(hdr)

        # ── Subtitle ─────────────────────────────────────────────────────
        sub = QLabel(
            "Detected modifications and firmware patches in the loaded ROM. "
            "Load the matching boost chip to pair it with this fuel/ignition chip."
        )
        sub.setStyleSheet(f"color: {FG_DIM}; font-size: 11px;")
        sub.setWordWrap(True)
        outer.addWidget(sub)
        self._sub_lbl = sub

        # ── MAP sensor info card ──────────────────────────────────────────
        self._sensor_card = self._make_sensor_card()
        outer.addWidget(self._sensor_card)

        # ── Coding plug card ──────────────────────────────────────────────
        self._coding_card = self._make_coding_card()
        outer.addWidget(self._coding_card)

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
        scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self._patch_scroll = scroll
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
        self._qlcc_note = note

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
            n_lbl.setStyleSheet(f"color:{FG};font-size:11px;min-width:120px;")
            n_lbl.setWordWrap(True)
            # Editable spinbox — shows decoded value; accepts decoded input
            from PyQt5.QtWidgets import QSpinBox
            spin = QSpinBox()
            spin.setRange(0, 255 * (40 if "RPM" in unit else 2 if "km/h" in unit else 1))
            spin.setSingleStep(40 if "RPM" in unit else 2 if "km/h" in unit else 1)
            spin.setSuffix(f"  {unit}")
            spin.setValue(0)
            spin.setEnabled(False)
            spin.setFixedWidth(104)
            spin.setStyleSheet(
                f"QSpinBox{{background:{BG2};color:{FG};border:1px solid {BORDER};"
                f"border-radius:3px;padding:1px 4px;font-size:11px;}}"
                f"QSpinBox:disabled{{color:{FG_DIM};}}")
            r_lbl = QLabel("")
            r_lbl.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
            # Working-half address lives in the tooltip to keep the row narrow
            n_lbl.setToolTip(f"WH 0x{wh_off:04X}")
            spin.setToolTip(f"WH 0x{wh_off:04X}")
            row_h.addWidget(n_lbl)
            row_h.addWidget(spin)
            row_h.addStretch()
            row_h.addWidget(r_lbl)
            lc_grid.addWidget(row_w)
            # Wire valueChanged → write back
            _off = wh_off; _enc = encode_fn; _dec = decode_fn
            spin.valueChanged.connect(
                lambda val, o=_off, e=_enc, d=_dec: self._on_lc_scalar_changed(o, d, e, val))
            self._lc_rows.append((spin, r_lbl, wh_off, decode_fn))

        self._lc_inactive = QLabel(
            "LC/NLS scalars require prjmod 0x0202 firmware (551AA_0202 variant).")
        self._lc_inactive.setStyleSheet(f"color:{FG_DIM};font-size:11px;")
        self._lc_inactive.setWordWrap(True)
        lc_grid.addWidget(self._lc_inactive)
        outer.addWidget(lc_card)
        self._lc_card = lc_card
        self._lc_hdr = lc_hdr

        # ── Conversion patches ────────────────────────────────────────────────
        conv_lbl = QLabel("CONVERSION PATCHES")
        conv_lbl.setStyleSheet(
            f"color:{FG_DIM};font-size:9px;letter-spacing:1px;"
            f"margin-top:12px;margin-bottom:2px;")
        outer.addWidget(conv_lbl)

        self._dist_patch_card = self._make_dist_patch_card()
        outer.addWidget(self._dist_patch_card)
        self._conv_lbl = conv_lbl

        # State
        self._boost_bytes: bytes | None = None
        self._wh: bytes | None = None
        self._variant_name: str = ""
        self._patch_apply_callback = None   # fn(patch_name, action, value)
        self._lambda_spin = None            # spinbox for lambda delay value

    # ── Distributor conversion patch card ─────────────────────────────────

    def _make_dist_patch_card(self) -> QFrame:
        """Card: AAN cam sensor → 3B/7A distributor Hall sender conversion."""
        card = QFrame()
        card.setStyleSheet(
            f"QFrame{{background:{BG3};border:1px solid #3a2a10;"
            f"border-radius:4px;padding:4px;}}")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(6)

        # Header row
        hdr = QHBoxLayout()
        badge = QLabel("⚠ EXPERIMENTAL")
        badge.setStyleSheet(
            f"color:#c87020;font-size:9px;font-weight:bold;"
            f"background:#3a2a10;padding:2px 6px;border-radius:3px;"
            f"letter-spacing:0.5px;")
        title = QLabel("AAN Cam Sensor → 3B/7A Distributor Hall Adapter")
        title.setStyleSheet(f"color:{FG};font-size:11px;font-weight:bold;")
        title.setWordWrap(True)
        hdr.addWidget(title, 1)
        hdr.addWidget(badge)
        lay.addLayout(hdr)

        desc = QLabel(
            "Allows fitting a 3B or 7A cylinder head (with distributor camshaft) "
            "to an AAN engine block running a full AAN ECU and coil-pack harness. "
            "The 3B/7A single-vane distributor Hall sender is wired to the AAN cam "
            "sensor harness — the firmware trigger algorithm is identical. "
            "A timing offset compensates for the difference between the distributor "
            "position and the AAN cam sensor reference angle (~60 BTDC).")
        desc.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
        desc.setWordWrap(True)
        lay.addWidget(desc)

        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color:#3a2a10;"); lay.addWidget(sep)

        # Current offset display
        self._dist_offset_lbl = QLabel("No offset applied  (0.0)")
        self._dist_offset_lbl.setStyleSheet(
            f"color:{FG_DIM};font-size:10px;")
        lay.addWidget(self._dist_offset_lbl)

        # Patch button
        btn = QPushButton(
            "Set distributor timing offset…")
        btn.setFixedHeight(28)
        btn.setStyleSheet(
            f"QPushButton{{background:#3a2a10;color:#c87020;"
            f"border:1px solid #6a4a20;border-radius:3px;"
            f"padding:0 12px;font-size:10px;font-weight:bold;}}"
            f"QPushButton:hover{{background:#5a3a10;border-color:#c87020;}}")
        btn.clicked.connect(self._on_dist_patch)
        lay.addWidget(btn)

        note = QLabel(
            "Reference angle not yet confirmed from factory data. "
            "Set offset = 0, verify timing with a timing light, then adjust.")
        note.setStyleSheet(f"color:#6a4a20;font-size:9px;")
        note.setWordWrap(True)
        lay.addWidget(note)

        self._dist_patch_offset = 0.0   # track applied offset
        return card

    # ── Coding plug card ──────────────────────────────────────────────────

    def _make_coding_card(self) -> QFrame:
        card = QFrame()
        card.setStyleSheet(
            f"QFrame{{background:{BG3};border:1px solid {BORDER};"
            f"border-radius:4px;padding:4px;}}")
        lay = QVBoxLayout(card)
        lay.setSpacing(4)
        lay.setContentsMargins(12, 8, 12, 8)
        lbl = QLabel("Coding plug → ignition set")
        lbl.setStyleSheet(f"color:{FG};font-size:12px;font-weight:bold;")
        lay.addWidget(lbl)
        self._coding_lbl = QLabel("Load a ROM to decode the coding-plug bands.")
        self._coding_lbl.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
        self._coding_lbl.setWordWrap(True)
        self._coding_lbl.setTextFormat(Qt.RichText)
        lay.addWidget(self._coding_lbl)
        return card

    def _coding_html(self) -> str:
        from urrom.coding_plug import decode_coding_plug
        dec = decode_coding_plug(self._wh, self._variant_name) if self._wh else None
        if dec is None:
            return "Coding ladder not found in this firmware."
        hdr = (f"ADC channel 4 binned by the ladder at 0x{dec.ladder_addr:04X} "
               f"({dec.structure} structure). The plug resistance depends on the ECU's "
               f"pull-up, which is not in the ROM: measure the voltage at the coding "
               f"pin with the plug fitted, or read the coding number with a tester. "
               f"3B (200 20V): plug on ECU pins 38 / 39 / 54, 5 V from pin 12, ground pin 2. "
               f"AAN (S4/S6): pins 38 / 39, 5 V pin 12, ground pin 30. Back-probe against "
               f"ground with the key on; the analogue pin is the ladder input.")
        rows = [f"<tr><th align=left>band</th><th align=left>ADC</th><th align=left>volts</th>"
                f"<th align=left>code</th><th align=left>no.</th><th align=left>set</th>"
                f"<th align=left>main map</th></tr>"]
        for b in dec.bands:
            code = f"{b.code:02X}" if b.code is not None else "–"
            no = "–" if b.coding_no is None else str(b.coding_no)
            rows.append(
                f"<tr><td>{b.band}</td><td>{b.adc_lo}–{b.adc_hi}</td>"
                f"<td>{b.v_lo:.2f}–{b.v_hi:.2f}</td><td>{code}</td><td>{no}</td>"
                f"<td>{b.ign_set}</td><td>{b.main_map}</td></tr>")
        table = ("<table cellspacing=0 cellpadding=2 style='font-family:Consolas,monospace;"
                 f"font-size:10px;color:{FG};'>" + "".join(rows) + "</table>")
        notes = "<br>".join(f"• {n}" for n in dec.notes)
        return f"<div style='color:{FG_DIM}'>{hdr}</div>{table}<div style='color:{FG_DIM}'>{notes}</div>"

    def _chip_summary(self) -> str:
        from urrom.hw_patches import is_551_family, is_404_family, feature_applies
        sw = self._variant_name or "?"
        if is_404_family(sw):
            fam = ("M2.3 (404 family): 32 KB flat fuel/ignition chip + 8 KB boost-board chip. "
                   "4 fuel + 7 ignition maps with firmware descriptors; 16-bit checksum at 0x7F00 "
                   "(verified by the ECU at boot, maintained by UrROM on save); boost targets and "
                   "knock windows live on the boost chip.")
        elif is_551_family(sw):
            fam = ("M2.3.2 (551/557 family): 64 KB split-bank chip (firmware low, calibration "
                   "high) + 32 KB boost chip. Firmware patches, LC/NLS scalars and SD mode "
                   "apply here where the variant is listed.")
        else:
            fam = "Unknown family."
        avail = [k for k in ("coding_plug", "boost_chip", "map_sensor_detect", "sd_r660",
                             "r201_swap", "lc_nls_scalars", "dist_conversion")
                 if feature_applies(k, sw)]
        names = {"coding_plug": "coding-plug decode", "boost_chip": "boost-chip pairing",
                 "map_sensor_detect": "MAP-sensor detect", "sd_r660": "SD / R660",
                 "r201_swap": "R201 swap", "lc_nls_scalars": "LC/NLS scalars",
                 "dist_conversion": "distributor conversion"}
        return (f"<b>{sw}</b> — {fam}<br><span style='color:{FG_DIM}'>Available here: "
                + ", ".join(names[k] for k in avail) + ". 27C512 images: File → Write 27C512 images.</span>")

    # ── Sensor card ───────────────────────────────────────────────────────

    def _make_sensor_card(self) -> QFrame:
        card = QFrame()
        card.setStyleSheet(
            f"QFrame{{background:{BG3};border:1px solid {BORDER};"
            f"border-radius:4px;padding:4px;}}")
        layout = QVBoxLayout(card)
        layout.setSpacing(4)
        layout.setContentsMargins(12, 8, 12, 8)

        lbl = QLabel("MAP Sensor")
        lbl.setStyleSheet(f"color:{FG};font-size:12px;font-weight:bold;")
        self._sensor_type_lbl = QLabel("—")
        self._sensor_type_lbl.setStyleSheet(f"color:{ACCENT};font-size:12px;font-weight:bold;")
        self._sensor_type_lbl.setWordWrap(True)
        layout.addWidget(lbl)
        layout.addWidget(self._sensor_type_lbl)

        self._sensor_detail_lbl = QLabel(
            "Load boost chip for sensor identification.  "
            "Stock: Bosch 200 kPa.  SD mode requires MPXH6400A 400 kPa.")
        self._sensor_detail_lbl.setStyleSheet(f"color:{FG_DIM};font-size:11px;")
        self._sensor_detail_lbl.setWordWrap(True)
        layout.addWidget(self._sensor_detail_lbl)

        # Sensor comparison grid (2×2 so it fits a narrow inspector)
        from PyQt5.QtWidgets import QGridLayout
        sensors_row = QGridLayout()
        sensors_row.setSpacing(4)
        for _i, (kpa, label, notes) in enumerate([
            ("200 kPa", "Stock Bosch",   "≤1.0 bar gauge\nMAF builds only"),
            ("250 kPa", "MPX4250",        "≤1.5 bar gauge\nQLCC-era"),
            ("300 kPa", "MPX4300",        "≤2.0 bar gauge\n034EFI / R201 swap"),
            ("400 kPa", "MPXH6400A",      "≤2.9 bar gauge\nprjmod SD std"),
        ]):
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
            sensors_row.addWidget(cell, _i // 2, _i % 2)
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

        name_lbl.setWordWrap(True)
        top.addWidget(name_lbl, 1)
        top.addWidget(cat_lbl)
        layout.addLayout(top)

        # Status line (own row so the card fits a narrow inspector)
        st_row = QHBoxLayout()
        st_row.setSpacing(8)
        st_row.addWidget(st_lbl)
        st_row.addWidget(conf_lbl)
        if result.wh_offset is not None:
            off_lbl = QLabel(f"WH 0x{result.wh_offset:04X}")
            off_lbl.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
            st_row.addWidget(off_lbl)
        st_row.addStretch()
        layout.addLayout(st_row)

        detail_lbl = QLabel(result.detail)
        detail_lbl.setStyleSheet(f"color:{FG_DIM};font-size:11px;")
        detail_lbl.setWordWrap(True)
        layout.addWidget(detail_lbl)

        if result.recommended:
            rec_lbl = QLabel("★  Recommended for this build")
            rec_lbl.setStyleSheet(f"color:{AMBER};font-size:10px;")
            layout.addWidget(rec_lbl)

        # ── Editable scalars of an applied patch ──────────────────────
        scalars = getattr(result, "scalars", None) or []
        if scalars and result.status == "PATCHED" and self._wh is not None:
            from PyQt5.QtWidgets import QSpinBox
            for name, off, dec, enc, unit, lo, hi in scalars:
                if off >= len(self._wh):
                    continue
                row = QHBoxLayout(); row.setSpacing(6)
                n_lbl = QLabel(name)
                n_lbl.setStyleSheet(f"color:{FG};font-size:11px;min-width:120px;")
                n_lbl.setToolTip(f"WH 0x{off:04X}")
                spin = QSpinBox()
                spin.setRange(lo, hi)
                step = 40 if unit == "RPM" else 1
                spin.setSingleStep(step)
                spin.setSuffix(f"  {unit}")
                spin.setFixedWidth(110)
                spin.setStyleSheet(
                    f"QSpinBox{{background:{BG2};color:{FG};border:1px solid {BORDER};"
                    f"border-radius:3px;padding:1px 4px;font-size:10px;}}")
                raw0 = self._wh[off]
                spin.blockSignals(True); spin.setValue(int(dec(raw0))); spin.blockSignals(False)
                r_lbl = QLabel(f"raw 0x{raw0:02X}")
                r_lbl.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
                spin.valueChanged.connect(
                    lambda val, o=off, d=dec, e=enc, rl=r_lbl:
                        (self._on_lc_scalar_changed(o, d, e, val),
                         rl.setText(f"raw 0x{e(val):02X} ← edited"),
                         rl.setStyleSheet(f"color:{AMBER};font-size:10px;")))
                row.addWidget(n_lbl); row.addWidget(spin); row.addWidget(r_lbl); row.addStretch()
                layout.addLayout(row)

        # ── Toggle buttons for toggleable firmware patches ────────────
        TOGGLEABLE = {
            "MFTS Boost Cut Bypass",
            "Load Overflow Decap",
            "Lambda Cold-Start Delay",
            "3B Spark-Cut Launch Control",
        }
        if result.name in TOGGLEABLE and result.category == "firmware":
            btn_row = QHBoxLayout()
            btn_row.setSpacing(6)

            if result.name == "Lambda Cold-Start Delay":
                # Spinbox for lambda delay value
                from PyQt5.QtWidgets import QSpinBox
                lam_spin = QSpinBox()
                lam_spin.setRange(1, 255)
                lam_spin.setValue(0xFF)
                lam_spin.setPrefix("delay: 0x")
                lam_spin.setDisplayIntegerBase(16)
                lam_spin.setFixedWidth(110)
                lam_spin.setStyleSheet(
                    f"QSpinBox{{background:{BG2};color:{FG};border:1px solid {BORDER};"
                    f"border-radius:3px;padding:1px 4px;font-size:10px;}}"
                    f"QSpinBox:disabled{{color:{FG_DIM};}}")
                # Pre-fill with current value if known
                if self._wh and result.wh_offset is not None and result.wh_offset < len(self._wh):
                    lam_spin.setValue(self._wh[result.wh_offset])
                self._lambda_spin = lam_spin
                btn_row.addWidget(lam_spin)

            # Variant compatibility gate
            can_apply = getattr(result, 'applicable', True)

            if result.status in ("STOCK", "CUSTOM", "MINIMAL"):
                apply_btn = QPushButton("Apply \u25b6")
                apply_btn.setFixedHeight(24)
                if can_apply:
                    apply_btn.setStyleSheet(
                        f"QPushButton{{background:#1a3a1a;color:{GREEN};"
                        f"border:1px solid #2a5a2a;border-radius:3px;"
                        f"padding:0 10px;font-size:10px;font-weight:bold;}}"
                        f"QPushButton:hover{{background:#2a5a2a;border-color:{GREEN};}}")
                else:
                    apply_btn.setEnabled(False)
                    apply_btn.setToolTip("Patch offsets not confirmed for this firmware variant")
                    apply_btn.setStyleSheet(
                        f"QPushButton{{background:{BG3};color:{FG_DIM};"
                        f"border:1px solid {BORDER};border-radius:3px;"
                        f"padding:0 10px;font-size:10px;font-weight:bold;}}")
                _name = result.name
                apply_btn.clicked.connect(lambda _, n=_name: self._on_patch_apply(n))
                btn_row.addWidget(apply_btn)

            if result.status in ("PATCHED", "EXTENDED"):
                revert_btn = QPushButton("Revert \u25c0")
                revert_btn.setFixedHeight(24)
                if can_apply:
                    revert_btn.setStyleSheet(
                        f"QPushButton{{background:#3a2a10;color:{AMBER};"
                        f"border:1px solid #6a4a20;border-radius:3px;"
                        f"padding:0 10px;font-size:10px;font-weight:bold;}}"
                        f"QPushButton:hover{{background:#5a3a10;border-color:{AMBER};}}")
                else:
                    revert_btn.setEnabled(False)
                    revert_btn.setToolTip("Patch offsets not confirmed for this firmware variant")
                    revert_btn.setStyleSheet(
                        f"QPushButton{{background:{BG3};color:{FG_DIM};"
                        f"border:1px solid {BORDER};border-radius:3px;"
                        f"padding:0 10px;font-size:10px;font-weight:bold;}}")
                _name = result.name
                revert_btn.clicked.connect(lambda _, n=_name: self._on_patch_revert(n))
                btn_row.addWidget(revert_btn)

            if result.status in ("UNKNOWN", "MODIFIED"):
                apply_btn = QPushButton("Apply \u25b6")
                apply_btn.setFixedHeight(24)
                apply_btn.setEnabled(can_apply)
                apply_btn.setStyleSheet(
                    f"QPushButton{{background:{BG3};color:{FG_DIM};"
                    f"border:1px solid {BORDER};border-radius:3px;"
                    f"padding:0 10px;font-size:10px;font-weight:bold;}}"
                    f"QPushButton:hover{{background:{BG2};border-color:{FG_DIM};}}")
                if not can_apply:
                    apply_btn.setToolTip("Patch offsets not confirmed for this firmware variant")
                _name = result.name
                apply_btn.clicked.connect(lambda _, n=_name: self._on_patch_apply(n))
                btn_row.addWidget(apply_btn)

            btn_row.addStretch()
            layout.addLayout(btn_row)

        return card

    # ── Firmware patch apply / revert handlers ──────────────────────────

    def set_patch_apply_callback(self, fn):
        """Register fn(patch_name, action, value) called when a firmware patch is toggled.
        action is 'apply' or 'revert'.  value is the lambda delay spinbox value (or None)."""
        self._patch_apply_callback = fn

    def _on_patch_apply(self, patch_name: str):
        """Confirmation dialog, then apply a firmware patch."""
        msgs = {
            "MFTS Boost Cut Bypass": (
                "Apply MFTS Bypass",
                "This will modify 2 bytes at WH 0x1254.\n"
                "The ECU will no longer cut boost based on coolant sensor signal.\n\n"
                "Apply patch?"),
            "Load Overflow Decap": (
                "Apply Load Overflow Decap",
                "This will modify 2 bytes at WH 0x3679 and 0x367F.\n"
                "Load comparison and clamp raised from 0xF0 to 0xFF.\n"
                "Prevents catastrophic load wrap at high boost.\n\n"
                "Apply patch?"),
            "Lambda Cold-Start Delay": (
                "Apply Lambda Cold-Start Delay",
                "This will modify 1 byte at WH 0x5EFA.\n"
                "Lambda warmup counter set to the value in the spinbox.\n\n"
                "Apply patch?"),
        }
        title, text = msgs.get(patch_name, (patch_name, f"Apply {patch_name}?"))
        reply = QMessageBox.question(self, title, text,
                                     QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        value = None
        if patch_name == "Lambda Cold-Start Delay" and self._lambda_spin is not None:
            value = self._lambda_spin.value()
        if self._patch_apply_callback:
            self._patch_apply_callback(patch_name, "apply", value)

    def _on_patch_revert(self, patch_name: str):
        """Confirmation dialog, then revert a firmware patch."""
        msgs = {
            "MFTS Boost Cut Bypass": (
                "Revert MFTS Bypass",
                "This will restore the stock conditional jump at WH 0x1254.\n"
                "ECU will resume cutting boost based on coolant sensor signal.\n\n"
                "Revert to stock?"),
            "Load Overflow Decap": (
                "Revert Load Overflow Decap",
                "This will restore the stock load clamp at 0xF0.\n"
                "WARNING: At high boost (>1.5 bar), load can wrap to zero.\n\n"
                "Revert to stock?"),
            "Lambda Cold-Start Delay": (
                "Revert Lambda Cold-Start Delay",
                "This will restore the stock lambda warmup counter (0x9F = 159 cycles).\n\n"
                "Revert to stock?"),
        }
        title, text = msgs.get(patch_name, (patch_name, f"Revert {patch_name}?"))
        reply = QMessageBox.question(self, title, text,
                                     QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        if self._patch_apply_callback:
            self._patch_apply_callback(patch_name, "revert", None)

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
            self._chip_lbl.setText("No ROM loaded.")
            self._coding_lbl.setText("Load a ROM to decode the coding-plug bands.")
            return

        from urrom.hw_patches import feature_applies, is_404_family
        show_all = self._show_all_chk.isChecked()
        sw = self._variant_name
        self._chip_lbl.setText(self._chip_summary())
        self._chip_lbl.setTextFormat(Qt.RichText)
        self._coding_lbl.setText(self._coding_html())
        self._coding_card.setVisible(show_all or feature_applies("coding_plug", sw))
        lc_on = feature_applies("lc_nls_scalars", sw)
        self._lc_card.setVisible(show_all or lc_on)
        self._lc_hdr.setVisible(show_all or lc_on)
        dist_on = feature_applies("dist_conversion", sw)
        self._dist_patch_card.setVisible(show_all or dist_on)
        self._conv_lbl.setVisible(show_all or dist_on)
        self._qlcc_note.setVisible(show_all or feature_applies("qlcc_note", sw))

        results = detect_patches(self._wh, self._variant_name, self._boost_bytes)

        # Update sensor card
        sensor_result = next((r for r in results if r.name == "MAP Sensor Type"), None)
        if is_404_family(sw):
            self._sensor_type_lbl.setText("3B/RR boost board — scale assumed 200 kPa")
            self._sensor_type_lbl.setStyleSheet(f"color:{AMBER};font-size:12px;font-weight:bold;")
            self._sensor_detail_lbl.setText(
                "The 404 boost chip's target tables are in kPa at an assumed 200 kPa "
                "full scale (docs/3B_boost_chip_RE.md). A logged boost reading on the car "
                "settles the scale; the 551 CJNE heuristic does not apply to this 8 KB chip.")
        elif sensor_result:
            self._sensor_type_lbl.setText(sensor_result.status)
            col = (GREEN if "400kPa" in sensor_result.status
                   else AMBER if "UNKNOWN" not in sensor_result.status
                   else FG_DIM)
            self._sensor_type_lbl.setStyleSheet(
                f"color:{col};font-size:12px;font-weight:bold;")
            self._sensor_detail_lbl.setText(sensor_result.detail)

        # Insert patch cards (skip the sensor result — shown in card above)
        insert_pos = 0
        hidden = 0
        for r in results:
            if r.name == "MAP Sensor Type":
                continue
            if not getattr(r, "in_scope", True) and not show_all:
                hidden += 1
                continue
            card = self._make_patch_card(r)
            if not getattr(r, "in_scope", True):
                card.setStyleSheet(card.styleSheet() + f"QFrame{{color:{FG_DIM};}}")
                card.setToolTip("Not meaningful on this chip family")
            self._patch_layout.insertWidget(insert_pos, card)
            insert_pos += 1
        if hidden:
            h = QLabel(f"{hidden} check(s) hidden — not applicable to {sw or 'this chip'}. "
                       "Tick the box above to show them.")
            h.setStyleSheet(f"color:{FG_DIM};font-size:10px;font-style:italic;")
            h.setWordWrap(True)
            self._patch_layout.insertWidget(insert_pos, h)
            insert_pos += 1
        # The panel lives inside the inspector's own scroll area, so size this
        # nested list to its content instead of letting it collapse.
        self._patch_container.adjustSize()
        self._patch_scroll.setMinimumHeight(
            min(self._patch_container.sizeHint().height() + 6, 1400))

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

    def _on_dist_patch(self) -> None:
        """Guided wizard: set timing offset for 3B/7A distributor as AAN cam sensor."""
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                                      QDoubleSpinBox, QDialogButtonBox, QFrame,
                                      QCheckBox)

        dlg = QDialog()
        dlg.setWindowTitle(
            "3B/7A Distributor → AAN Cam Sensor Timing Offset")
        dlg.setMinimumWidth(500)
        dlg.setStyleSheet(f"background:{BG};color:{FG};")
        lay = QVBoxLayout(dlg)
        lay.setSpacing(12)

        # Title and context
        ttl = QLabel(
            "AAN Cam Sensor to 3B/7A Distributor Hall Adapter")
        ttl.setStyleSheet(
            f"font-size:13px;font-weight:bold;color:{FG};")
        lay.addWidget(ttl)

        badge = QLabel("⚠  EXPERIMENTAL  —  reference angle unconfirmed")
        badge.setStyleSheet(
            f"color:#c87020;background:#3a2a10;padding:4px 10px;"
            f"border-radius:3px;font-size:10px;")
        lay.addWidget(badge)

        ctx_lines = [
            "The 3B/7A single-vane distributor Hall sender produces the same signal",
            "pattern as the AAN cam Hall sensor (confirmed by M2.3.2 firmware binary",
            "analysis). Wire the distributor Hall sender to the AAN cam sensor harness.",
            "",
            "The AAN cam sensor fires at a specific crank angle (nominally ~60 BTDC",
            "cylinder 1). If your distributor is positioned at a different angle,",
            "this offset corrects the difference without touching the distributor.",
            "",
            "Start at 0. Run engine, check timing with a timing light on cyl 1,",
            "then adjust until commanded = actual timing.",
        ]
        ctx = QLabel("\n".join(ctx_lines))
        ctx.setWordWrap(True)
        ctx.setStyleSheet(
            f"color:{FG_DIM};font-size:10px;background:{BG2};"
            f"padding:10px;border-radius:4px;")
        lay.addWidget(ctx)

        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color:{BORDER}"); lay.addWidget(sep)

        # Offset input
        row = QHBoxLayout()
        row.addWidget(QLabel("Timing offset:"))
        self._dist_spin = QDoubleSpinBox()
        self._dist_spin.setRange(-60.0, 60.0)
        self._dist_spin.setValue(self._dist_patch_offset)
        self._dist_spin.setDecimals(1)
        self._dist_spin.setSingleStep(1.0)
        self._dist_spin.setSuffix("\u00b0")
        self._dist_spin.setToolTip(
            "Positive = more advance (distributor fires later than AAN cam sensor)\n"
            "Negative = more retard (distributor fires earlier)")
        self._dist_spin.setFixedWidth(100)
        self._dist_spin.setStyleSheet(
            f"QDoubleSpinBox{{background:{BG2};color:{FG};"
            f"border:1px solid {BORDER};border-radius:3px;"
            f"padding:3px 6px;font-size:13px;}}")

        preview = QLabel("")
        preview.setStyleSheet(f"color:{ACCENT};font-size:10px;")

        def _upd():
            d = self._dist_spin.value()
            sign = "+" if d >= 0 else ""
            raw_delta = round(d / 0.6491)
            ign_count = len([m for m in (self._dist_maps or [])
                           if m.map_type == "ign" and m.confidence == "CONFIRMED" and m.rows > 1])
            preview.setText(
                f"={sign}{d:.1f}\u00b0  \u2192  {sign}{raw_delta:+d} raw on every ign cell"
                f"  ({abs(raw_delta)} cells \u00d7 {ign_count} maps)")

        self._dist_spin.valueChanged.connect(_upd)

        row.addWidget(self._dist_spin)
        row.addSpacing(12)
        row.addWidget(preview)
        row.addStretch()
        lay.addLayout(row)
        _upd()

        # Quick preset buttons
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Quick:"))
        for label, val in [("-20°", -20.0), ("-10°", -10.0), ("0° (reset)", 0.0),
                           ("+10°", 10.0), ("+20°", 20.0)]:
            pb = QPushButton(label)
            pb.setFixedHeight(22)
            pb.setStyleSheet(
                f"QPushButton{{background:{BG3};color:{FG};"
                f"border:1px solid {BORDER};border-radius:3px;"
                f"padding:0 8px;font-size:10px;}}"
                f"QPushButton:hover{{border-color:{ACCENT};}}")
            _val = val
            pb.clicked.connect(lambda _, v=_val: self._dist_spin.setValue(v))
            preset_row.addWidget(pb)
        preset_row.addStretch()
        lay.addLayout(preset_row)

        warn = QLabel(
            "This modifies all confirmed ignition maps. "
            "Undo (Ctrl+Z) or Revert reverses the change. "
            "Session changelog records every cell.")
        warn.setStyleSheet(f"color:{AMBER};font-size:9px;")
        warn.setWordWrap(True)
        lay.addWidget(warn)

        btns = QDialogButtonBox(QDialogButtonBox.Apply | QDialogButtonBox.Close)
        lay.addWidget(btns)
        btns.rejected.connect(dlg.reject)

        def _apply():
            if not self._scalar_changed_callback:
                return
            # Delegate to MainWindow via callback
            self._scalar_changed_callback(
                '__dist_patch__', self._dist_spin.value())

        btns.button(QDialogButtonBox.Apply).clicked.connect(_apply)
        dlg.exec_()

    def set_dist_maps(self, maps):
        """Pass the variant's map list so the patch dialog can count ign maps."""
        self._dist_maps = maps

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
