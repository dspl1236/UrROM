"""
Main (fuel / ignition) chip editor tab.

Split out of app/main.py (2026-10-14); the classes and helpers are unchanged.
"""
from PyQt5.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtCore import QTimer, Qt

from urrom.ecu_profiles import MapDef, ROMVariant, get_axes, read_map
from urrom.ui.theme import ACCENT, AMBER, BG, BG2, BG3, BORDER, FG, FG_DIM, GREEN, RED
from urrom.ui.colours import fuel_colour, heat, ign_colour
from urrom.ui.map_table import MapTable
from urrom.ui.widgets import make_edit_line, make_view_switch, show_edit_line, view_setting_key

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
        self._map_lbl = QLabel("Map:")
        toolbar.addWidget(self._map_lbl)
        toolbar.addWidget(self._map_combo)

        # Current map title — shown in workbench mode where the combo is hidden
        self._map_title = QLabel("")
        self._map_title.setStyleSheet(f"font-size:13px;font-weight:bold;color:{FG};")
        self._map_title.setVisible(False)
        toolbar.addWidget(self._map_title)

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

        self._view_btns = make_view_switch(self, toolbar)
        toolbar.addSpacing(8)

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

        # Provenance: where the address, axes and decode come from (roadmap item 3)
        self._prov_lbl = QLabel("")
        self._prov_lbl.setStyleSheet(
            f"color:{FG_DIM};font-size:10px;padding:3px 8px;background:{BG2};"
            f"border-left:2px solid {BORDER};border-radius:2px;")
        self._prov_lbl.setWordWrap(True)
        self._prov_lbl.setTextFormat(Qt.RichText)
        self._prov_lbl.setVisible(False)
        layout.addWidget(self._prov_lbl)

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

        # Map table + the heat-map / 3D views of the same data
        self._table = MapTable()
        layout.addWidget(self._table)
        from urrom.ui.map_view import MapPlotView
        self._plot = MapPlotView()
        self._plot.setVisible(False)
        self._plot.setMinimumHeight(420)
        layout.addWidget(self._plot, 1)
        self._view_mode = "table"
        self._table.dataEdited.connect(self._refresh_plot)
        QTimer.singleShot(0, self._restore_view)
        self._edit_lbl = make_edit_line(layout)
        self._table.set_edit_callback(lambda summ, g: show_edit_line(self._edit_lbl, summ, g))

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
        self._table.set_variant(variant)
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

    def use_external_selector(self, external: bool = True):
        """
        Workbench mode: the MapTree dock selects maps, so hide the inline
        combo/search and show a plain title label instead.  The combo stays
        alive (hidden) because find/scan/overlay features drive it.
        """
        self._map_lbl.setVisible(not external)
        self._map_combo.setVisible(not external)
        self._search_box.setVisible(not external)
        self._map_title.setVisible(external)

    def current_map(self) -> MapDef | None:
        return self._table._map_def

    def current_index(self) -> int:
        m = self._table._map_def
        if m is None:
            return -1
        for i, mm in enumerate(self._maps):
            if mm is m:
                return i
        return -1

    def set_session_log_fn(self, fn):
        """Register fn(map_def, r, c, old_raw, new_raw, row_axis, col_axis, chip) for the session log."""
        if fn:
            self._table._log_fn = lambda m, r, c, old, new: fn(
                m, r, c, old, new, list(self._table._row_axis), list(self._table._col_axis), "main")
        else:
            self._table._log_fn = None

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
            # Flush current map edits back to ROM before switching
            if self._table._map_def is not None:
                self._table.commit_to_rom(self._rom)
            # Temporarily update _on_map_selected to use real idx
            m = self._maps[real_idx]
            from urrom.ecu_profiles import get_axes
            rpm_axis, load_axis = get_axes(bytes(self._rom), m, self._variant)
            self._table.load(self._rom, m, rpm_axis, load_axis)
            self._ensure_table_wired()
            if hasattr(self, "_status_fn"):
                self._table.set_status_callback(self._status_fn)
            self._map_title.setText(f"{m.name}   <span style='color:{FG_DIM};font-weight:normal;font-size:11px;'>{m.rows}\xd7{m.cols}  {m.unit}</span>")
            self._desc_lbl.setText(m.description)
            self._show_provenance(m)
            self._addr_lbl.setText(f"Address: 0x{m.main_addr:04X}  (working half offset)")
            self._size_lbl.setText(f"{m.rows}\xd7{m.cols} = {m.size} bytes")
            conf = m.confidence
            conf_colour = (GREEN if conf=="CONFIRMED" else AMBER if conf=="PROVISIONAL" else RED)
            self._conf_badge.setText(conf)
            self._conf_badge.setStyleSheet(
                f"font-size:10px;padding:2px 6px;border-radius:3px;"
                f"color:{conf_colour};background:{BG3};border:1px solid {conf_colour};")
            self._revert_btn.setEnabled(False)
            self._refresh_plot()
            cb = getattr(self, "_on_map_changed_cb", None)
            if cb:
                cb()
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
        self._map_title.setText("")
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
        # Flush current map edits back to ROM before switching
        if self._table._map_def is not None:
            self._table.commit_to_rom(self._rom)

        m = self._maps[idx]
        v = self._variant

        # Determine axes via ecu_profiles helper
        from urrom.ecu_profiles import get_axes
        rpm_axis, load_axis = get_axes(bytes(self._rom), m, v)

        self._table.load(self._rom, m, rpm_axis, load_axis)
        # Re-wire the signal each time a new map is loaded
        self._ensure_table_wired()
        # Wire hover → status via callback set by MainWindow
        if hasattr(self, "_status_fn"):
            self._table.set_status_callback(self._status_fn)
        self._map_title.setText(f"{m.name}   <span style='color:{FG_DIM};font-weight:normal;font-size:11px;'>{m.rows}×{m.cols}  {m.unit}</span>")
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
            def _make_click(idx):
                def _handler(e):
                    self._grid_btn.setChecked(False)
                    self._on_toggle_grid()
                    self._map_combo.setCurrentIndex(idx)
                return _handler
            frame.mousePressEvent = _make_click(map_idx)
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
                                bg_hex = ign_colour(float(dv) if dv else 0).name()
                            elif m.map_type == "fuel":
                                bg_hex = fuel_colour(rv).name()
                            else:
                                bg_hex = heat(rv, vmin, vmax).name()
                        else:
                            bg_hex = heat(rv, vmin, vmax).name()
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
        self._refresh_plot()

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

    def _ensure_table_wired(self):
        """Connect the tab's dirty-tracking to the table exactly once.

        The old code called itemChanged.disconnect() on every map switch, which
        also removed the table's own _on_cell_changed handler, so edits made
        after switching maps were never stored (fixed 2026-09-09)."""
        if not getattr(self, "_table_wired", False):
            self._table.itemChanged.connect(lambda _: self.on_table_changed())
            self._table_wired = True

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
        self._plot.set_cursor(None)

    def update_overlay(self, lv):
        self._table.update_overlay(lv)
        if self._view_mode != "table":
            self._plot.set_cursor(getattr(self._table, "_kwp_row", None),
                                  getattr(self._table, "_kwp_col", None))

    # ── Table / Heat / 3D views ───────────────────────────────────────────

    def _restore_view(self):
        from PyQt5.QtCore import QSettings
        mode = QSettings("UrROM", "UrROM").value(view_setting_key(self), "table", type=str)
        self._set_view(mode if mode in ("table", "heat", "3d") else "table", persist=False)

    def _set_view(self, mode: str, persist: bool = True):
        self._view_mode = mode
        for m, b in self._view_btns.items():
            b.setChecked(m == mode)
        self._table.setVisible(mode == "table")
        self._plot.setVisible(mode != "table")
        if mode != "table":
            self._plot.set_mode(mode)
            self._refresh_plot()
        if persist:
            from PyQt5.QtCore import QSettings
            QSettings("UrROM", "UrROM").setValue(view_setting_key(self), mode)

    def _show_provenance(self, m):
        try:
            from urrom.provenance import provenance_for
            pr = provenance_for(m, self._variant)
        except Exception:
            self._prov_lbl.setVisible(False); return
        colour = {"CONFIRMED": GREEN, "PROVISIONAL": AMBER}.get(pr.confidence, RED)
        html = (f"<b style='color:{colour}'>{pr.confidence or 'UNRATED'}</b> &nbsp;"
                f"<b>Address</b> {pr.address_source}")
        if pr.confirmed_on:
            html += f" &nbsp;<b>Confirmed on</b> {', '.join(pr.confirmed_on)}"
        html += f" &nbsp;<b>Decode</b> {pr.decode}"
        if pr.decode_source:
            html += f" <span style='color:{FG_DIM}'>({pr.decode_source})</span>"
        if pr.caveat:
            html += f" &nbsp;<span style='color:{AMBER}'>Caveat: {pr.caveat}</span>"
        if pr.doc:
            html += f" &nbsp;<span style='color:{FG_DIM}'>{pr.doc}</span>"
        self._prov_lbl.setText(html)
        self._prov_lbl.setVisible(True)
        self._table.set_variant(self._variant)

    def set_trace(self, hits: dict | None):
        """Show per-cell hit counts on the table and the plot views."""
        self._trace_hits = dict(hits) if hits else None
        self._table.set_trace(self._trace_hits)
        self._plot.set_trace(self._trace_hits)

    def current_axes(self):
        if self._table._map_def is None:
            return None, None
        return list(self._table._row_axis), list(self._table._col_axis)

    def _refresh_plot(self):
        if getattr(self, "_view_mode", "table") == "table":
            return
        snap = self._table.plot_snapshot()
        if snap is None:
            self._plot.clear(); return
        m = self._table._map_def
        row_label = "rpm" if m.map_type in ("fuel", "ign") else "row"
        col_label = "load" if m.map_type in ("fuel", "ign") else "col"
        self._plot.set_map(snap["rows"], snap["cols"], snap["values"], unit=snap["unit"],
                           title=snap["title"], row_label=row_label, col_label=col_label,
                           changed=snap["changed"])
        r, c = snap["cursor"]
        self._plot.set_cursor(r, c)
        self._plot.set_trace(getattr(self, "_trace_hits", None))
