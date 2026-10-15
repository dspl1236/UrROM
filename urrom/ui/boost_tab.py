"""
Boost chip editor tab.

Split out of app/main.py (2026-10-14); the classes and helpers are unchanged.
"""
from PyQt5.QtWidgets import QComboBox, QHBoxLayout, QLabel, QMessageBox, QVBoxLayout, QWidget
from PyQt5.QtCore import QTimer, Qt

from urrom.ecu_profiles import get_axes, read_map
from urrom.ui.theme import AMBER, BG3, BORDER, FG, FG_DIM, confidence_colour
from urrom.ui.map_table import MapTable
from urrom.ui.widgets import make_edit_line, make_view_switch, show_edit_line, view_setting_key

# ── Boost chip tab ────────────────────────────────────────────────────────────

class BoostTab(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # ── Toolbar ───────────────────────────────────────────────────────
        tb = QHBoxLayout()
        self._map_combo = QComboBox()
        self._map_combo.setMinimumWidth(300)
        self._map_combo.currentIndexChanged.connect(self._on_map_selected)
        tb.addWidget(self._map_combo)
        self._map_title = QLabel("")
        self._map_title.setStyleSheet(f"font-size:13px;font-weight:bold;color:{FG};")
        self._map_title.setVisible(False)
        tb.addWidget(self._map_title)
        tb.addStretch()

        # Sensor scale + display unit for the pressure tables
        from urrom import boost_sensor
        sel_style = (f"QComboBox{{background:{BG3};color:{FG};border:1px solid {BORDER};"
                     f"border-radius:3px;padding:1px 6px;font-size:11px;}}")
        s_lbl = QLabel("Sensor")
        s_lbl.setStyleSheet(f"color:{FG_DIM};font-size:11px;")
        tb.addWidget(s_lbl)
        self._sensor_combo = QComboBox()
        self._sensor_combo.setStyleSheet(sel_style)
        for sens in boost_sensor.sensors():
            self._sensor_combo.addItem(sens.name, sens.key)
        self._sensor_combo.addItem("Custom span / offset…", "custom")
        self._sensor_combo.addItem("Identify from a voltage reading…", "identify")
        self._sensor_combo.currentIndexChanged.connect(self._on_sensor_changed)
        tb.addWidget(self._sensor_combo)
        self._unit_combo = QComboBox()
        self._unit_combo.setStyleSheet(sel_style)
        self._unit_combo.addItem("kPa abs", "kpa")
        self._unit_combo.addItem("bar gauge", "bar")
        self._unit_combo.addItem("psi gauge", "psi")
        self._unit_combo.currentIndexChanged.connect(self._on_unit_changed)
        tb.addWidget(self._unit_combo)
        tb.addSpacing(8)
        self._view_btns = make_view_switch(self, tb)
        layout.addLayout(tb)

        self._peak_lbl = QLabel("")
        self._peak_lbl.setStyleSheet(f"color:{AMBER};font-size:11px;")
        self._peak_lbl.setVisible(False)
        layout.addWidget(self._peak_lbl)

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

        layout.addStretch()
        self._boost_rom = None
        self._maps = []
        self._variant = None
        self._family = "404"
        self._committed_dirty = False
        self._in_sensor_ui = False

    # ── Sensor scale ─────────────────────────────────────────────────────

    def _settings(self):
        from PyQt5.QtCore import QSettings
        return QSettings("UrROM", "UrROM")

    def _sync_sensor_ui(self):
        """Reflect the module state for the current family in the combos."""
        from urrom import boost_sensor
        self._in_sensor_ui = True
        sens = boost_sensor.get_sensor(self._family)
        i = self._sensor_combo.findData(sens.key)
        if i < 0:   # custom
            i = self._sensor_combo.findData("custom")
            self._sensor_combo.setItemText(i, sens.name)
        self._sensor_combo.setCurrentIndex(i)
        self._unit_combo.setCurrentIndex(
            self._unit_combo.findData(boost_sensor.get_display(self._family)))
        self._in_sensor_ui = False

    def _restore_sensor_setting(self, suggested_key: str | None = None):
        from urrom import boost_sensor
        st = self._settings()
        key = st.value(f"boost_sensor/{self._family}/key", "", type=str)
        if key == "custom":
            span = st.value(f"boost_sensor/{self._family}/span", 0.0, type=float)
            off = st.value(f"boost_sensor/{self._family}/offset", 0.0, type=float)
            if span > 0:
                boost_sensor.set_custom(self._family, span, off)
        elif key:
            try:
                boost_sensor.set_sensor(self._family, key)
            except KeyError:
                pass
        elif suggested_key:
            try:
                boost_sensor.set_sensor(self._family, suggested_key)
            except KeyError:
                pass
        mode = st.value(f"boost_sensor/{self._family}/display", "kpa", type=str)
        boost_sensor.set_display(self._family, mode if mode in ("kpa", "bar", "psi") else "kpa")
        self._sync_sensor_ui()

    def _save_sensor_setting(self):
        from urrom import boost_sensor
        st = self._settings()
        sens = boost_sensor.get_sensor(self._family)
        st.setValue(f"boost_sensor/{self._family}/key", sens.key)
        if sens.key == "custom":
            st.setValue(f"boost_sensor/{self._family}/span", sens.span_kpa)
            st.setValue(f"boost_sensor/{self._family}/offset", sens.offset_kpa)
        st.setValue(f"boost_sensor/{self._family}/display", boost_sensor.get_display(self._family))

    def _reload_current_map(self):
        """Re-decode the visible table after a scale change without losing edits."""
        if self._boost_rom is None or self._table._map_def is None:
            return
        if self._table.has_changes():
            self._boost_rom = self._table.commit_to_rom(self._boost_rom)
            self._committed_dirty = True
        self._on_map_selected(self.current_index())

    def _on_sensor_changed(self, idx: int):
        if self._in_sensor_ui:
            return
        from urrom import boost_sensor
        key = self._sensor_combo.itemData(idx)
        if key == "custom":
            from PyQt5.QtWidgets import QInputDialog
            sens = boost_sensor.get_sensor(self._family)
            span, ok = QInputDialog.getDouble(
                self, "Custom sensor", "kPa at full ADC scale (5 V):",
                sens.span_kpa, 50, 1000, 1)
            if not ok:
                self._sync_sensor_ui(); return
            off, ok = QInputDialog.getDouble(
                self, "Custom sensor", "kPa at 0 V (offset):", sens.offset_kpa, -100, 100, 1)
            if not ok:
                self._sync_sensor_ui(); return
            boost_sensor.set_custom(self._family, span, off)
        elif key == "identify":
            self._identify_sensor()
            return
        else:
            boost_sensor.set_sensor(self._family, key)
        self._save_sensor_setting()
        self._sync_sensor_ui()
        self._reload_current_map()

    def _identify_sensor(self):
        """Rank known sensors against a measured output voltage at a known pressure."""
        from urrom import boost_sensor
        from PyQt5.QtWidgets import QInputDialog
        v, ok = QInputDialog.getDouble(
            self, "Identify sensor",
            "Sensor output voltage, key on / engine off (≈ atmospheric):", 2.5, 0.0, 5.0, 2)
        if not ok:
            self._sync_sensor_ui(); return
        kpa, ok = QInputDialog.getDouble(
            self, "Identify sensor", "Pressure at that moment (kPa abs):",
            boost_sensor.ATM_KPA, 20.0, 400.0, 1)
        if not ok:
            self._sync_sensor_ui(); return
        rows = boost_sensor.identify(v, kpa)
        lines = [f"{'sensor':44s} predicted   error", ""]
        for sens, pv, err in rows:
            lines.append(f"{sens.name[:44]:44s} {pv:5.2f} V   {err:4.2f} V")
        best = rows[0][0]
        reply = QMessageBox.question(
            self, "Identify sensor",
            "<pre style='font-size:11px'>" + "\n".join(lines) + "</pre>"
            f"Best match: <b>{best.name}</b><br>Use it for the {self._family} boost tables?",
            QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            boost_sensor.set_sensor(self._family, best.key)
            self._save_sensor_setting()
        self._sync_sensor_ui()
        self._reload_current_map()

    def _on_unit_changed(self, idx: int):
        if self._in_sensor_ui:
            return
        from urrom import boost_sensor
        boost_sensor.set_display(self._family, self._unit_combo.itemData(idx))
        self._save_sensor_setting()
        self._reload_current_map()

    def _update_peak(self, m):
        from urrom import boost_sensor
        from urrom.ecu_profiles import read_map
        if m.map_type != "boost" or self._boost_rom is None:
            self._peak_lbl.setVisible(False); return
        raw = read_map(bytes(self._boost_rom), m)
        flat = [v for row in raw for v in row] if raw and isinstance(raw[0], list) else list(raw)
        if not flat:
            self._peak_lbl.setVisible(False); return
        sens = boost_sensor.get_sensor(self._family)
        hi, lo = max(flat), min(flat)
        self._peak_lbl.setText(
            f"{sens.name}  ·  peak raw {hi} = {sens.kpa(hi):.0f} kPa abs = "
            f"{boost_sensor.kpa_to_bar_gauge(sens.kpa(hi)):+.2f} bar / {boost_sensor.kpa_to_psi_gauge(sens.kpa(hi)):+.1f} psi gauge  ·  "
            f"min raw {lo} = {sens.kpa(lo):.0f} kPa abs  ·  1 raw = {sens.span_kpa/255:.2f} kPa")
        self._peak_lbl.setVisible(True)

    def load(self, boost_rom: bytearray, variant, suggested_sensor: str | None = None):
        from urrom import boost_sensor
        self._variant = variant
        self._boost_rom = boost_rom
        self._committed_dirty = False
        self._family = boost_sensor.family_of(variant.software_id if variant else "")
        self._restore_sensor_setting(suggested_sensor)
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
        self._table.set_variant(self._variant)
        self._table.load(self._boost_rom, m, rpm_axis, load_axis)
        self._table.setVisible(self._view_mode == "table")
        self._plot.setVisible(self._view_mode != "table")
        self._note.setVisible(False)
        self._refresh_plot()
        from urrom import boost_sensor
        unit = boost_sensor.unit(self._family) if m.map_type == "boost" else m.unit
        is_pressure = m.map_type == "boost"
        self._sensor_combo.setVisible(is_pressure)
        self._unit_combo.setVisible(is_pressure)
        self._update_peak(m)
        self._map_title.setText(
            f"{m.name}   <span style='color:{FG_DIM};font-weight:normal;font-size:11px;'>"
            f"{m.rows}×{m.cols}  {unit}</span>")
        conf_col = confidence_colour(m.confidence)
        self._status.setText(
             f"Boost chip  ·  WH 0x{m.main_addr:04X}  ·  "
             f"<span style='color:{conf_col};'>{m.confidence}</span>")
        self._status.setTextFormat(Qt.RichText)

    def use_external_selector(self, external: bool = True):
        self._map_combo.setVisible(not external)
        self._map_title.setVisible(external)

    def set_session_log_fn(self, fn):
        """Boost-chip edits go to the same session log, tagged chip='boost'."""
        if fn:
            self._table._log_fn = lambda m, r, c, old, new: fn(
                m, r, c, old, new, list(self._table._row_axis), list(self._table._col_axis), "boost")
        else:
            self._table._log_fn = None

    # ── Table / Heat / 3D views ───────────────────────────────────────────

    def _restore_view(self):
        from PyQt5.QtCore import QSettings
        mode = QSettings("UrROM", "UrROM").value(view_setting_key(self), "table", type=str)
        self._set_view(mode if mode in ("table", "heat", "3d") else "table", persist=False)

    def _set_view(self, mode: str, persist: bool = True):
        self._view_mode = mode
        for m, b in self._view_btns.items():
            b.setChecked(m == mode)
        loaded = self._table._map_def is not None
        self._table.setVisible(mode == "table" and loaded)
        self._plot.setVisible(mode != "table" and loaded)
        if mode != "table":
            self._plot.set_mode(mode)
            self._refresh_plot()
        if persist:
            from PyQt5.QtCore import QSettings
            QSettings("UrROM", "UrROM").setValue(view_setting_key(self), mode)

    def _refresh_plot(self):
        if getattr(self, "_view_mode", "table") == "table":
            return
        snap = self._table.plot_snapshot()
        if snap is None:
            self._plot.clear(); return
        from urrom import boost_sensor
        m = self._table._map_def
        unit = snap["unit"]
        if m.map_type == "boost" and unit != "raw":
            unit = boost_sensor.unit(self._family)
        self._plot.set_map(snap["rows"], snap["cols"], snap["values"], unit=unit,
                           title=snap["title"], row_label="TPS (raw)", col_label="rpm",
                           changed=snap["changed"])

    def current_index(self) -> int:
        m = self._table._map_def
        if m is None:
            return -1
        for i, mm in enumerate(self._maps):
            if mm is m:
                return i
        return -1

    def commit_to_rom(self, rom: bytearray) -> bytearray:
        """Write current boost chip edits back into the boost ROM bytearray."""
        # Flush the currently visible map's edits
        return self._table.commit_to_rom(rom)

    def has_changes(self) -> bool:
        if self._boost_rom is None:
            return False
        return self._table.has_changes() or self._committed_dirty

    def get_boost_rom(self) -> bytearray | None:
        """Return the boost ROM with all edits applied, or None if not loaded."""
        if self._boost_rom is None:
            return None
        # Flush current map edits into the boost ROM bytearray
        rom = bytearray(self._boost_rom)
        rom = self.commit_to_rom(rom)
        return rom

    def clear(self):
        self._boost_rom = None
        self._maps = []
        self._committed_dirty = False
        self._peak_lbl.setVisible(False)
        self._map_combo.clear()
        self._status.setText("No boost chip loaded")
        self._note.setVisible(True)
        self._table.setVisible(False)
