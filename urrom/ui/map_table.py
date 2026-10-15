"""
The editable map grid (MapTable).

Split out of app/main.py (2026-10-14); the classes and helpers are unchanged.
"""
import copy
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QFileDialog,
    QHeaderView,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
)
from PyQt5.QtGui import QBrush, QColor
from PyQt5.QtCore import Qt

from urrom.ecu_profiles import MapDef, normalize_rom, read_map, write_map
from urrom.kwp import LiveValues  # used in the update_overlay annotation
from urrom.ui.theme import AMBER, BG2, BG3, BORDER, FG, RED
from urrom.ui.colours import fuel_colour, heat, ign_colour, text_colour
from urrom.ui.widgets import ChangedCellDelegate

# ── Map editor table ──────────────────────────────────────────────────────────

class MapTable(QTableWidget):
    """
    16×16 (or N×M) heat-mapped editable table for a single ROM map.
    Rows = RPM axis (index 0 = lowest RPM at bottom, displayed inverted).
    Cols = Load axis (index 0 = lowest load at left).
    """
    from PyQt5.QtCore import pyqtSignal as _sig
    dataEdited = _sig()          # a cell value was edited by the user

    def plot_snapshot(self) -> dict | None:
        """Rows/cols/values/changed-mask as the heat-map and 3D views need them."""
        if self._map_def is None or not self._current_raw:
            return None
        decode = getattr(self, "_display_decode", self._map_def.decode)
        raw = self._current_raw
        vals = [[(decode(v) if decode else v) for v in row] for row in raw]
        changed = [[raw[r][c] != self._original_raw[r][c] for c in range(len(raw[0]))]
                   for r in range(len(raw))]
        return {"rows": list(self._row_axis), "cols": list(self._col_axis), "values": vals,
                "changed": changed,
                "unit": (self._map_def.unit or "raw") if decode else "raw",
                "title": self._map_def.name,
                "cursor": (getattr(self, "_kwp_row", None), getattr(self, "_kwp_col", None))}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._map_def: MapDef | None = None
        self._rom: bytearray | None = None
        self._original_raw: list[list[int]] = []
        self._current_raw:  list[list[int]] = []
        self._undo_stack:   list[list[list[int]]] = []   # max 30 states
        self._redo_stack:   list[list[list[int]]] = []
        self._annotations:  dict[tuple[int,int], str] = {}  # (raw_r, col) → note
        self._trace: dict[tuple[int,int], int] | None = None  # live/log hit counts
        self._guards: dict[tuple[int,int], list] = {}         # (r, c) → [Guard] from the last edit
        self._edit_cb = None                                  # fn(summary_text, guards)
        self._log_fn = None   # fn(map_def, r, c, old_raw, new_raw) for session log
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

    def set_edit_callback(self, fn) -> None:
        """fn(summary: str, guards: list[Guard]) after every accepted edit."""
        self._edit_cb = fn

    def set_variant(self, variant) -> None:
        """Variant of the loaded chip — enables provenance in cell tooltips."""
        self._variant = variant
        self._prov_lines = None

    def _provenance_tip(self) -> str:
        if getattr(self, "_prov_lines", None) is None:
            v = getattr(self, "_variant", None)
            if v is None or self._map_def is None:
                self._prov_lines = ""
            else:
                from urrom.provenance import provenance_for
                self._prov_lines = "\n".join("· " + ln for ln in provenance_for(self._map_def, v).lines())
        return self._prov_lines

    def load(self, rom: bytearray, map_def: MapDef,
             rpm_axis: list | None = None, load_axis: list | None = None):
        self._loading = True
        self._rom = rom
        self._map_def = map_def
        self._prov_lines = None
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
                    bg = ign_colour(val)
                elif self._is_fuel:
                    bg = fuel_colour(cell_raw)
                else:
                    bg = heat(cell_raw, vmin, vmax)

                fg = text_colour(bg)
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
                    decoded_val = decode(cell_raw)
                    dec_str = (f"{decoded_val:.2f}" if isinstance(decoded_val, float)
                               else str(decoded_val))
                    tip_parts.append(f"raw: {cell_raw}  decoded: {dec_str} {self._map_def.unit or ''}")
                else:
                    tip_parts.append(f"raw: {cell_raw}")
                if ann:
                    tip_parts.append(f"📝 {ann}")
                    # Add asterisk to annotated cells
                    item.setText(item.text() + " *")
                prov = self._provenance_tip()
                if prov:
                    tip_parts.append("")
                    tip_parts.append(f"{self._map_def.name}  @0x{self._map_def.main_addr:04X}  cell r{r} c{c}")
                    tip_parts.append(prov)
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

        old_byte = self._current_raw[r_log][c_log]
        self._current_raw[r_log][c_log] = raw_byte
        # Record to session log if registered
        if self._log_fn and old_byte != raw_byte:
            self._log_fn(self._map_def, r_log, c_log, old_byte, raw_byte)

        # Update colour/changed marker
        self._loading = True
        orig = self._original_raw[r_log][c_log]
        changed = raw_byte != orig
        decode = self._map_def.decode
        display_val = decode(raw_byte) if decode else raw_byte
        display_text = f"{display_val:.1f}" if isinstance(display_val, float) else str(display_val)
        item.setText(display_text)
        if self._is_ign and decode:
            bg = ign_colour(display_val)
        elif self._is_fuel:
            bg = fuel_colour(raw_byte)
        else:
            all_vals = [self._current_raw[r][c]
                        for r in range(self._map_def.rows)
                        for c in range(self._map_def.cols)]
            bg = heat(raw_byte, min(all_vals), max(all_vals))
        item.setBackground(QBrush(bg))
        item.setForeground(QBrush(text_colour(bg)))
        item.setData(Qt.UserRole, "changed" if changed else None)

        # Guard rails (roadmap item 4): what the edit means, and what it risks
        try:
            from urrom.guards import check_edit, describe_edit, worst
            guards = check_edit(self._map_def, getattr(self, "_variant", None), r_log, c_log,
                                old_byte, raw_byte, orig, self._row_axis, self._col_axis,
                                grid=self._current_raw, typed_value=val)
            summary = describe_edit(self._map_def, getattr(self, "_variant", None), old_byte, raw_byte)
        except Exception:
            guards, summary = [], f"raw {old_byte} → {raw_byte}"
        self._guards[(r_log, c_log)] = guards
        level = worst(guards) if guards else ""
        if level in ("warn", "stop"):
            item.setForeground(QBrush(QColor(RED if level == "stop" else AMBER)))
            f = item.font(); f.setBold(True); item.setFont(f)
        tip = [f"raw: {raw_byte}  decoded: {display_text} {self._map_def.unit or ''}", summary]
        tip += [("⛔ " if g.level == "stop" else "⚠ " if g.level == "warn" else "ⓘ ") + g.text for g in guards]
        prov = self._provenance_tip()
        if prov:
            tip += ["", prov]
        item.setToolTip("\n".join(tip))
        self._loading = False
        if self._edit_cb:
            try:
                self._edit_cb(f"r{r_log} c{c_log}: {summary}", guards)
            except Exception:
                pass
        self.dataEdited.emit()

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
                    raw_r = self._disp_to_raw(r)
                    encode = self._map_def.encode
                    if encode and getattr(self, "_display_decode", None) is not None:
                        raw_val = encode(float(val_str.strip()))
                    else:
                        raw_val = int(float(val_str.strip()))
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

        # row_axis = RPM axis (MapTable rows map to RPM)
        # col_axis = load axis (MapTable columns map to load/MAF)
        # Match lv.rpm → closest row, lv.load → closest column
        col_axis = getattr(self, "_col_axis", [])
        row_axis = getattr(self, "_row_axis", [])

        if row_axis and lv.rpm is not None and len(row_axis) > 1:
            new_row = min(range(len(row_axis)),
                         key=lambda i: abs(row_axis[i] - lv.rpm))

        if col_axis and lv.load is not None and len(col_axis) > 1:
            # lv.load is raw KWP MAF load value (1-255).
            # col_axis stores the load axis values from the ROM descriptor.
            new_col = min(range(len(col_axis)),
                         key=lambda i: abs(col_axis[i] - lv.load))

        changed = (new_col != self._kwp_col or
                   new_row != self._kwp_row or
                   lv.lambda_ != self._kwp_lambda)
        self._kwp_col    = new_col
        self._kwp_row    = new_row
        self._kwp_lambda = lv.lambda_
        if changed:
            self._refresh_overlay()

    def set_trace(self, hits: dict | None) -> None:
        """Hit counts per logical (row, col); None clears.  Also written to tooltips."""
        self._trace = dict(hits) if hits else None
        if self._map_def is None:
            return
        for (r, c), n in (self._trace or {}).items():
            self._annotations[(r, c)] = f"trace: {n} sample{'s' if n != 1 else ''}"
        if not self._trace:
            for k in [k for k, v in self._annotations.items() if str(v).startswith("trace:")]:
                del self._annotations[k]
        self._redraw()
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

        # Repainting backgrounds fires itemChanged for every cell; without the
        # loading guard each live sample looked like 256 edits and made the
        # plot views rebuild their whole figure (bench mode + Heat froze).
        was_loading = self._loading
        self._loading = True
        try:
            self._paint_overlay_cells(nrows, ncols, active, kwp_col, kwp_row, tint)
        finally:
            self._loading = was_loading

    def _paint_overlay_cells(self, nrows, ncols, active, kwp_col, kwp_row, tint):
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
                    base = ign_colour(val)
                elif self._is_fuel:
                    base = fuel_colour(cell_raw)
                else:
                    all_v = [self._current_raw[rr][cc]
                             for rr in range(nrows) for cc in range(ncols)]
                    base = heat(cell_raw, min(all_v), max(all_v))

                bg = QColor(base)

                # Trace: lighten cells in proportion to how often the engine sat there
                if self._trace:
                    n = self._trace.get((r, c), 0)
                    if n:
                        mx = max(self._trace.values()) or 1
                        a = 0.18 + 0.42 * (n / mx) ** 0.5
                        bg = QColor(int(bg.red() * (1-a) + 255 * a),
                                    int(bg.green() * (1-a) + 255 * a),
                                    int(bg.blue() * (1-a) + 255 * a))

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
                item.setForeground(QBrush(text_colour(bg)))
