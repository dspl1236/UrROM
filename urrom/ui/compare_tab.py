"""
Compare tab: two chips side by side.

Split out of app/main.py (2026-10-14); the classes and helpers are unchanged.
"""
from pathlib import Path

from PyQt5.QtGui import QBrush, QColor
from PyQt5.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtCore import Qt

from urrom.ecu_profiles import detect_rom, normalize_rom, read_map
from urrom.ui.theme import ACCENT, AMBER, BG2, BG3, BORDER, FG, FG_DIM, GREEN, RED
from urrom.ui.colours import fuel_colour, heat, ign_colour, text_colour
from urrom.ui.widgets import make_view_switch

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
        self._map_combo.currentIndexChanged.connect(self._on_a_map_changed)
        tb.addWidget(QLabel("Map:"))
        tb.addWidget(self._map_combo)
        tb.addStretch()
        self._load_b_btn = QPushButton("Load ROM B for compare\u2026")
        self._load_b_btn.clicked.connect(self._on_load_b)
        self._load_b_btn.setEnabled(False)
        tb.addWidget(self._load_b_btn)
        layout.addLayout(tb)

        # Second row: B-side map, view, mode, blend slider, raw toggle
        from PyQt5.QtWidgets import QSlider, QCheckBox
        row2 = QHBoxLayout(); row2.setSpacing(8)
        self._bmap_lbl = QLabel("B map:")
        self._bmap_lbl.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
        self._bmap_combo = QComboBox()
        self._bmap_combo.setMinimumWidth(240)
        self._bmap_combo.currentIndexChanged.connect(lambda _i: self._refresh())
        row2.addWidget(self._bmap_lbl); row2.addWidget(self._bmap_combo)
        row2.addSpacing(10)
        self._view_btns = make_view_switch(self, row2)
        row2.addSpacing(10)
        self._mode_btns = {}
        for mode, label, tip in (("diff", "Difference", "B minus A, in A's units"),
                                 ("blend", "Blend", "Slide from A to B")):
            b = QPushButton(label); b.setCheckable(True); b.setFixedWidth(78); b.setToolTip(tip)
            b.setStyleSheet(
                f"QPushButton{{background:{BG3};color:{FG};border:1px solid {BORDER};"
                f"border-radius:3px;padding:0 6px;font-size:10px;}}"
                f"QPushButton:checked{{background:{AMBER}30;border-color:{AMBER};}}"
                f"QPushButton:hover{{border-color:{AMBER};}}")
            b.clicked.connect(lambda _c, m=mode: self._set_mode(m))
            row2.addWidget(b); self._mode_btns[mode] = b
        self._mode_btns["diff"].setChecked(True)
        self._plot_mode = "diff"
        self._blend_lbl_a = QLabel("A"); self._blend_lbl_a.setStyleSheet(f"color:{ACCENT};font-size:10px;font-weight:bold;")
        self._blend = QSlider(Qt.Horizontal); self._blend.setRange(0, 100); self._blend.setValue(100)
        self._blend.setFixedWidth(160); self._blend.setToolTip("Blend A → B (Blend mode)")
        self._blend.valueChanged.connect(lambda _v: self._refresh_plot())
        self._blend_lbl_b = QLabel("B"); self._blend_lbl_b.setStyleSheet(f"color:{AMBER};font-size:10px;font-weight:bold;")
        row2.addWidget(self._blend_lbl_a); row2.addWidget(self._blend); row2.addWidget(self._blend_lbl_b)
        self._raw_chk = QCheckBox("Raw bytes")
        self._raw_chk.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
        self._raw_chk.setToolTip("Compare undecoded bytes instead of real units")
        self._raw_chk.toggled.connect(lambda _c: self._refresh())
        row2.addWidget(self._raw_chk)
        row2.addStretch()
        layout.addLayout(row2)

        self._status = QLabel("Load a ROM A first, then load ROM B to compare.")
        self._status.setStyleSheet(f"color: {FG_DIM}; font-size: 11px;")
        layout.addWidget(self._status)

        from urrom.ui.map_view import MapPlotView
        self._plot = MapPlotView()
        self._plot.setVisible(False)
        self._plot.setMinimumHeight(380)
        layout.addWidget(self._plot, 1)
        self._view_mode = "table"
        self._variant_b = None
        self._maps_b: list = []
        self._xc = None

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
        self._splitter = splitter

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
            self._populate_b_maps()
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

    def set_rom_b(self, wh: bytes, variant=None) -> None:
        """Public alias — set comparison ROM B directly from bytes."""
        label = variant.name if variant else "ROM B"
        self.set_rom_b_direct(wh, label)

    def set_rom_b_direct(self, wh: bytes, label: str = "stock", variant=None):
        """Load ROM B programmatically without a file dialog."""
        self._rom_b = bytes(wh)
        self._variant_b = variant or self._detect_b(self._rom_b)
        self._status.setText(f"ROM B: {label}  ({len(wh):,} bytes)  [auto-loaded]")
        self._export_btn.setEnabled(True)
        self._populate_b_maps()
        self._refresh()

    def _detect_b(self, wh: bytes):
        from urrom.ecu_profiles import detect_rom
        try:
            det = detect_rom(bytes(wh))
            return det.variant or self._variant
        except Exception:
            return self._variant

    def _same_family(self) -> bool:
        return (self._variant is not None and self._variant_b is not None
                and self._variant.software_id == self._variant_b.software_id)

    def _populate_b_maps(self):
        """Fill the B-map combo; pre-select the counterpart of the current A map."""
        from urrom.xcompare import counterpart_map
        vb = self._variant_b or self._variant
        self._maps_b = [m for m in (vb.main_maps if vb else []) if m.rows > 1] if vb else []
        self._bmap_combo.blockSignals(True)
        self._bmap_combo.clear()
        for m in self._maps_b:
            self._bmap_combo.addItem(f"{m.name}  [{m.rows}\u00d7{m.cols} {m.unit}]  @0x{m.main_addr:04X}")
        idx = self._map_combo.currentIndex()
        if self._maps and 0 <= idx < len(self._maps) and vb is not None:
            cp = counterpart_map(vb, self._maps[idx], self._variant)
            if cp is not None and cp in self._maps_b:
                self._bmap_combo.setCurrentIndex(self._maps_b.index(cp))
        self._bmap_combo.blockSignals(False)
        cross = not self._same_family()
        self._bmap_lbl.setVisible(True)
        self._bmap_combo.setVisible(True)
        self._bmap_lbl.setText("B map (cross-family, resampled onto A):" if cross else "B map:")

    def _b_map(self):
        i = self._bmap_combo.currentIndex()
        if 0 <= i < len(self._maps_b):
            return self._maps_b[i]
        return None

    # ── Table / Heat / 3D + Difference / Blend ───────────────────────────

    def _set_view(self, mode: str, persist: bool = True):
        self._view_mode = mode
        for m, b in self._view_btns.items():
            b.setChecked(m == mode)
        self._splitter.setVisible(mode == "table")
        self._plot.setVisible(mode != "table")
        if mode != "table":
            self._plot.set_mode(mode)
            self._refresh_plot()

    def _set_mode(self, mode: str):
        self._plot_mode = mode
        for m, b in self._mode_btns.items():
            b.setChecked(m == mode)
        self._blend.setEnabled(mode == "blend")
        self._refresh_plot()

    def _refresh_plot(self):
        if self._view_mode == "table" or self._xc is None:
            return
        xc = self._xc
        unit = "raw" if self._raw_chk.isChecked() else (xc.a.map_def.unit or "raw")
        if self._plot_mode == "diff":
            vals = xc.delta
            title = f"B − A: {xc.b.map_def.name} − {xc.a.map_def.name}"
        else:
            t = self._blend.value() / 100.0
            vals = [[xc.a.data[i][j] + t * (xc.b_on_a[i][j] - xc.a.data[i][j])
                     for j in range(len(xc.a.cols))] for i in range(len(xc.a.rows))]
            title = f"Blend {int(t*100)} % B: {xc.a.map_def.name}"
        is_map = xc.a.map_def.map_type in ("fuel", "ign")
        self._plot.set_map(xc.a.rows, xc.a.cols, vals, unit=unit, title=title,
                           row_label="rpm" if is_map else "row", col_label="load" if is_map else "col")

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
        self._rom_b = bytes(wh)
        self._variant_b = self._detect_b(self._rom_b)
        vb = self._variant_b.software_id if self._variant_b else "?"
        self._status.setText(f"ROM B: {Path(path).name}  ({len(wh):,} bytes)  [{vb}]")
        self._populate_b_maps()
        self._refresh()

    def _on_a_map_changed(self, _i=None):
        if self._rom_b:
            self._populate_b_maps()
        self._refresh()

    def _refresh(self):
        idx = self._map_combo.currentIndex()
        if not self._maps or idx < 0 or idx >= len(self._maps) or self._rom_a is None:
            return
        m = self._maps[idx]
        v = self._variant
        from urrom.xcompare import side_from_bytes, compare_sides
        raw_mode = self._raw_chk.isChecked()
        a = side_from_bytes(self._rom_a, v, m, raw_values=raw_mode)
        mb = self._b_map() if self._rom_b else None
        b = None
        if self._rom_b and mb is not None:
            b = side_from_bytes(self._rom_b, self._variant_b or v, mb, raw_values=raw_mode)
        exact = (b is not None and self._same_family() and mb.main_addr == m.main_addr
                 and b.rows == a.rows and b.cols == a.cols)
        self._xc = compare_sides(a, b) if b is not None else None

        nrows, ncols = m.rows, m.cols
        rpm_labels  = [str(r) for r in reversed(a.rows)]
        load_labels = [str(l) for l in a.cols]
        for tbl in (self._table_a, self._table_d, self._table_b):
            tbl.setRowCount(nrows)
            tbl.setColumnCount(ncols)
            tbl.setVerticalHeaderLabels(rpm_labels)
            tbl.setHorizontalHeaderLabels(load_labels)
            tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
            tbl.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)

        raw_a = read_map(self._rom_a, m)
        all_a = [a.data[r][c] for r in range(nrows) for c in range(ncols)]
        vmin, vmax = min(all_a), max(all_a)
        b_vals = self._xc.b_on_a if self._xc else None
        deltas = self._xc.delta if self._xc else None
        decode = None if raw_mode else m.decode
        changed_count = 0
        eps = 1e-9 if exact else 0.05

        def _mk(text, bg, fg=None, bold=False):
            it = QTableWidgetItem(text)
            it.setBackground(QBrush(bg))
            it.setForeground(QBrush(fg or text_colour(bg)))
            it.setTextAlignment(Qt.AlignCenter)
            if bold:
                f = it.font(); f.setBold(True); it.setFont(f)
            it.setFlags(Qt.ItemIsEnabled)
            return it

        def _colour(val, raw_byte):
            if m.map_type == "ign" and decode:
                return ign_colour(val)
            if m.map_type == "fuel" and raw_byte is not None:
                return fuel_colour(raw_byte)
            return heat(val, vmin, vmax)

        fmt = (lambda x: f"{x:.0f}") if raw_mode or not decode else (lambda x: f"{x:.1f}")
        for r in range(nrows):
            disp_r = nrows - 1 - r
            for c in range(ncols):
                av = a.data[r][c]
                bv = b_vals[r][c] if b_vals else av
                d = deltas[r][c] if deltas else 0.0
                changed = abs(d) > eps
                if changed:
                    changed_count += 1
                bg_a = _colour(av, raw_a[r][c])
                bg_b = _colour(bv, None if not exact else read_map(self._rom_b, mb)[r][c]) if b_vals else bg_a
                if changed:
                    d_text = f"{d:+.2f}" if not raw_mode else f"{d:+.0f}"
                    bg_d, fg_d = (QColor("#1a3a1a"), QColor(GREEN)) if d > 0 else (QColor("#3a1a1a"), QColor(RED))
                else:
                    d_text, bg_d, fg_d = "\u2014", QColor(BG2), QColor(FG_DIM)
                self._table_a.setItem(disp_r, c, _mk(fmt(av), bg_a))
                self._table_d.setItem(disp_r, c, _mk(d_text, bg_d, fg_d, bold=changed))
                self._table_b.setItem(disp_r, c, _mk(fmt(bv), bg_b))

        total = nrows * ncols
        same = self._same_family()
        self._export_btn.setEnabled(bool(self._rom_b) and same)
        self._jump_btn.setEnabled(bool(self._rom_b) and same)
        if same:
            self._update_delta_strip()
        else:
            self._delta_strip.setVisible(False)
        if self._xc:
            st = self._xc.summary()
            flat = [x for row in deltas for x in row]
            rms = (sum(x * x for x in flat) / len(flat)) ** 0.5
            unit = "raw" if raw_mode else (m.unit or "raw")
            how = "cell-exact" if exact else f"B ({self._variant_b.software_id if self._variant_b else '?'}: {mb.name}) resampled onto A's axes"
            self._summary.setText(
                f"{m.name}  \u2014  {changed_count}/{total} cells differ ({100*changed_count/total:.0f}%)  |  "
                f"mean \u0394 {st['mean']:+.2f}  rms {rms:.2f}  min {st['min']:+.2f}  max {st['max']:+.2f} {unit}  |  {how}")
        else:
            self._summary.setText(f"Load ROM B to see delta  \u2014  {m.name}")
        self._refresh_plot()

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
