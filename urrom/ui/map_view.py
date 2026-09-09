"""
urrom/ui/map_view.py — 2D heat-map and 3D surface views of a calibration map.

matplotlib renders in software (no OpenGL) into a Qt canvas, so it works on
every machine the PyQt5 app already runs on.  The widget mirrors whatever the
MapTable currently shows: decoded values (or raw when the table is in raw
mode), the real axes, changed cells, and the live KWP cursor.

    view = MapPlotView()
    view.set_mode("3d")           # or "heat"
    view.set_map(rows, cols, values, unit="°BTDC", title="Ign Map 2",
                 row_label="rpm", col_label="load", changed=mask)
    view.set_cursor(r, c)         # live overlay marker, None to clear
"""
from __future__ import annotations

from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QSizePolicy
from PyQt5.QtCore import Qt

try:
    import matplotlib
    matplotlib.use("QtAgg", force=False)
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure
    from matplotlib.colors import LinearSegmentedColormap
    import numpy as np
    _MPL = True
except Exception:   # pragma: no cover - matplotlib missing
    _MPL = False

from urrom.ui.theme import BG, BG2, BORDER, FG, FG_DIM, ACCENT, AMBER, CHANGED

# Same run as the table's heat colours: cool blue -> green -> yellow -> red.
_HEAT = [(0.00, "#1f4e9c"), (0.35, "#1c9a5c"), (0.65, "#e6c22a"), (1.00, "#c8342a")]


def plotting_available() -> bool:
    return _MPL


class MapPlotView(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mode = "heat"
        self._rows: list = []
        self._cols: list = []
        self._vals: list[list[float]] = []
        self._changed: list[list[bool]] | None = None
        self._unit = ""
        self._title = ""
        self._row_label = "rpm"
        self._col_label = "load"
        self._cursor: tuple[int, int] | None = None
        self._trace: dict | None = None      # (r, c) -> hit count
        self._azim, self._elev = -60.0, 28.0

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        if not _MPL:
            lbl = QLabel("matplotlib is not installed — pip install matplotlib to enable the heat-map and 3D views.")
            lbl.setStyleSheet(f"color:{FG_DIM};padding:12px;")
            lbl.setWordWrap(True)
            lay.addWidget(lbl)
            return
        self._fig = Figure(figsize=(6, 4), dpi=100, facecolor=BG)
        self._canvas = FigureCanvas(self._fig)
        self._canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._canvas.setStyleSheet(f"background:{BG};border:1px solid {BORDER};")
        lay.addWidget(self._canvas)
        self._cmap = LinearSegmentedColormap.from_list("urrom_heat", _HEAT)
        self._ax = None
        self._canvas.mpl_connect("button_release_event", self._remember_camera)

    # ── public API ────────────────────────────────────────────────────────

    def set_mode(self, mode: str) -> None:
        if mode not in ("heat", "3d"):
            raise ValueError(mode)
        self._mode = mode
        self._draw()

    def mode(self) -> str:
        return self._mode

    def set_map(self, rows, cols, values, unit: str = "", title: str = "",
                row_label: str = "rpm", col_label: str = "load",
                changed=None) -> None:
        self._rows, self._cols = list(rows), list(cols)
        self._vals = [list(map(float, r)) for r in values]
        self._changed = changed
        self._unit, self._title = unit or "", title or ""
        self._row_label, self._col_label = row_label, col_label
        self._draw()

    def set_cursor(self, r: int | None, c: int | None = None) -> None:
        """Highlight logical cell (r, c) — the live-data position."""
        new = None if r is None or c is None else (int(r), int(c))
        if new != self._cursor:
            self._cursor = new
            self._draw()

    def set_trace(self, hits: dict | None) -> None:
        """Per-cell hit counts painted over the map (None clears)."""
        self._trace = dict(hits) if hits else None
        self._draw()

    def clear(self) -> None:
        self._vals = []
        self._cursor = None
        self._trace = None
        self._draw()

    # ── drawing ───────────────────────────────────────────────────────────

    def _remember_camera(self, _evt):
        ax = self._ax
        if ax is not None and hasattr(ax, "azim"):
            self._azim, self._elev = ax.azim, ax.elev

    def _draw(self) -> None:
        if not _MPL:
            return
        fig = self._fig
        fig.clear()
        if not self._vals:
            ax = fig.add_subplot(111)
            ax.set_facecolor(BG)
            ax.text(0.5, 0.5, "No map loaded", ha="center", va="center", color=FG_DIM)
            ax.set_axis_off()
            self._ax = ax
            self._canvas.draw_idle()
            return
        Z = np.array(self._vals, dtype=float)
        nrows, ncols = Z.shape
        rows = self._rows if len(self._rows) == nrows else list(range(nrows))
        cols = self._cols if len(self._cols) == ncols else list(range(ncols))
        vmin, vmax = float(Z.min()), float(Z.max())
        if vmax <= vmin:
            vmax = vmin + 1.0

        if self._mode == "heat":
            ax = fig.add_subplot(111)
            ax.set_facecolor(BG)
            # row 0 = lowest rpm at the bottom, like the table
            im = ax.imshow(Z, origin="lower", aspect="auto", cmap=self._cmap,
                           vmin=vmin, vmax=vmax, interpolation="nearest")
            ax.set_xticks(range(ncols)); ax.set_xticklabels([_fmt_axis(v) for v in cols], rotation=90, fontsize=7)
            ax.set_yticks(range(nrows)); ax.set_yticklabels([_fmt_axis(v) for v in rows], fontsize=7)
            ax.set_xlabel(self._col_label, color=FG_DIM, fontsize=8)
            ax.set_ylabel(self._row_label, color=FG_DIM, fontsize=8)
            ax.tick_params(colors=FG_DIM, length=0)
            for s in ax.spines.values():
                s.set_color(BORDER)
            # value labels when the grid is small enough to read
            if nrows * ncols <= 256:
                for r in range(nrows):
                    for c in range(ncols):
                        v = Z[r, c]
                        f = (v - vmin) / (vmax - vmin)
                        ax.text(c, r, _fmt_val(v), ha="center", va="center", fontsize=6.5,
                                color="#111111" if 0.3 < f < 0.8 else "#f2f2f2")
            if self._changed is not None:
                for r in range(nrows):
                    for c in range(ncols):
                        if self._changed[r][c]:
                            ax.add_patch(matplotlib.patches.Rectangle(
                                (c - 0.5, r - 0.5), 1, 1, fill=False, lw=1.4, ec=CHANGED))
            if self._trace:
                mx = max(self._trace.values()) or 1
                xs = [c for (_, c) in self._trace]; ys = [r for (r, _) in self._trace]
                sz = [30 + 260 * (n / mx) for n in self._trace.values()]
                ax.scatter(xs, ys, s=sz, facecolors="none", edgecolors="#FFFFFF", linewidths=1.2, alpha=0.9, zorder=5)
            if self._cursor is not None:
                r, c = self._cursor
                ax.add_patch(matplotlib.patches.Rectangle(
                    (c - 0.5, r - 0.5), 1, 1, fill=False, lw=2.2, ec=AMBER))
            cb = fig.colorbar(im, ax=ax, fraction=0.035, pad=0.02)
            cb.ax.tick_params(colors=FG_DIM, labelsize=7)
            cb.outline.set_edgecolor(BORDER)
            cb.set_label(self._unit, color=FG_DIM, fontsize=8)
            ax.set_title(self._title, color=FG, fontsize=10, loc="left")
            self._ax = ax
        else:
            ax = fig.add_subplot(111, projection="3d")
            ax.set_facecolor(BG)
            X, Y = np.meshgrid(range(ncols), range(nrows))
            surf = ax.plot_surface(X, Y, Z, cmap=self._cmap, vmin=vmin, vmax=vmax,
                                   rstride=1, cstride=1, linewidth=0.25,
                                   edgecolor=BG2, antialiased=True, alpha=0.96)
            # projected contours on the floor give the 2D shape at a glance
            try:
                ax.contour(X, Y, Z, zdir="z", offset=vmin - (vmax - vmin) * 0.15,
                           cmap=self._cmap, levels=8, linewidths=0.8)
            except Exception:
                pass
            if self._trace:
                mx = max(self._trace.values()) or 1
                pts = [(c, r, Z[r, c], n) for (r, c), n in self._trace.items() if 0 <= r < nrows and 0 <= c < ncols]
                if pts:
                    ax.scatter([p[0] for p in pts], [p[1] for p in pts], [p[2] + (vmax - vmin) * 0.02 for p in pts],
                               s=[12 + 120 * (p[3] / mx) for p in pts], c="#FFFFFF", alpha=0.85, depthshade=False, zorder=9)
            if self._cursor is not None:
                r, c = self._cursor
                if 0 <= r < nrows and 0 <= c < ncols:
                    ax.scatter([c], [r], [Z[r, c]], s=60, c=AMBER, depthshade=False, zorder=10)
                    ax.plot([c, c], [r, r], [vmin - (vmax - vmin) * 0.15, Z[r, c]], color=AMBER, lw=1.2)
            step_c = max(1, ncols // 8); step_r = max(1, nrows // 8)
            ax.set_xticks(range(0, ncols, step_c)); ax.set_xticklabels([_fmt_axis(cols[i]) for i in range(0, ncols, step_c)], fontsize=7)
            ax.set_yticks(range(0, nrows, step_r)); ax.set_yticklabels([_fmt_axis(rows[i]) for i in range(0, nrows, step_r)], fontsize=7)
            ax.set_xlabel(self._col_label, color=FG_DIM, fontsize=8, labelpad=6)
            ax.set_ylabel(self._row_label, color=FG_DIM, fontsize=8, labelpad=6)
            ax.set_zlabel(self._unit, color=FG_DIM, fontsize=8, labelpad=6)
            ax.tick_params(colors=FG_DIM, labelsize=7)
            for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
                axis.set_pane_color((0, 0, 0, 0))
                axis.line.set_color(BORDER)
                axis._axinfo["grid"]["color"] = BORDER
            ax.view_init(elev=self._elev, azim=self._azim)
            ax.set_title(self._title, color=FG, fontsize=10, loc="left")
            cb = fig.colorbar(surf, ax=ax, fraction=0.03, pad=0.04, shrink=0.7)
            cb.ax.tick_params(colors=FG_DIM, labelsize=7)
            cb.outline.set_edgecolor(BORDER)
            self._ax = ax
        try:
            fig.tight_layout()
        except Exception:
            pass
        self._canvas.draw_idle()


def _fmt_axis(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(f)) if f == int(f) else f"{f:.1f}"


def _fmt_val(v: float) -> str:
    return str(int(v)) if abs(v - round(v)) < 1e-9 else f"{v:.1f}"
