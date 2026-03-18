"""
urrom/kwp.py — KWPBridge integration for UrROM.

Thin wrapper around kwpbridge.client that:
  - polls KWPBridge on localhost:50266
  - emits Qt signals on state changes
  - safety gate: part number match check before overlay activates
  - extracts M2.3.2-specific values from state dicts
    (group 1: RPM/ECT/λ/IGN, group 3: RPM/load/TPS/IAT,
     group 6: N75/MAP — prjmod firmware only)

Degrades gracefully — if kwpbridge is not installed or not running,
UrROM works exactly as before.

M2.3.2 group layout (4A0-907-551-AA.lbl, WinlogDriver.cpp confirmed):
  Group 1: cell1=RPM (×40), cell2=ECT (−70°C), cell3=λ (/128), cell4=IGN (°BTDC)
  Group 3: cell1=RPM, cell2=load (/25), cell3=TPS (×0.416%), cell4=IAT (−70°C)
  Group 6: cell1=N75 DC, cell2=N75 req, cell3=MAP kPa, cell4=MAP req  [prjmod]
"""

import logging
from typing import Optional

log = logging.getLogger(__name__)

# ── Optional imports ──────────────────────────────────────────────────────────

try:
    from kwpbridge.client import KWPClient, is_running as _kwp_is_running
    from kwpbridge.constants import DEFAULT_PORT
    _KWP_AVAILABLE = True
except ImportError:
    _KWP_AVAILABLE = False
    DEFAULT_PORT   = 50266

try:
    from PyQt5.QtCore import QObject, QTimer, pyqtSignal
    _QT_AVAILABLE = True
except ImportError:
    _QT_AVAILABLE = False


def kwpbridge_available() -> bool:
    return _KWP_AVAILABLE


def kwpbridge_running() -> bool:
    if not _KWP_AVAILABLE:
        return False
    try:
        return _kwp_is_running(port=DEFAULT_PORT)
    except Exception:
        return False


# ── Live values for M2.3.2 ───────────────────────────────────────────────────

class LiveValues:
    """
    Decoded M2.3.2 measuring block values from a KWPBridge state dict.

    The overlay uses:
      rpm   — to find the current column in the fuel/ign map (RPM axis)
      load  — to find the current row (load axis, raw /25)
      map_kpa — for SD-mode maps (VE table, MAP target)
      lambda_ — for tint colour (rich=red, lean=blue, stoich=green)
      timing  — displayed in status strip

    All values None if KWPBridge not connected or cell absent.
    """

    def __init__(self, state: dict):
        self.rpm:      Optional[float] = None
        self.load:     Optional[float] = None   # raw 1-255
        self.ect:      Optional[float] = None   # °C
        self.iat:      Optional[float] = None   # °C
        self.lambda_:  Optional[float] = None   # λ
        self.timing:   Optional[float] = None   # °BTDC
        self.tps:      Optional[float] = None   # %
        self.map_kpa:  Optional[float] = None   # kPa abs
        self.n75_dc:   Optional[float] = None   # %DC
        self.vss:      Optional[float] = None   # km/h
        self.battery:  Optional[float] = None   # V
        self.ecu_pn:   str = ""

        if not state or not state.get("connected"):
            return

        self.ecu_pn = state.get("ecu_id", {}).get("part_number", "")
        groups = state.get("groups", {})

        def _cells(grp_key) -> dict:
            g = groups.get(str(grp_key), groups.get(grp_key, {}))
            return {c["index"]: c for c in g.get("cells", [])}

        def _v(cells, idx) -> Optional[float]:
            c = cells.get(idx)
            return c["value"] if c else None

        # Group 1: RPM, ECT, lambda, ignition
        g1 = _cells(1) or _cells("0")   # mock sends group 1 as "0"
        self.rpm     = _v(g1, 1)
        self.ect     = _v(g1, 2)
        self.lambda_ = _v(g1, 3)
        self.timing  = _v(g1, 4)

        # Group 3: RPM, load, TPS, IAT
        g3 = _cells(3)
        if g3:
            self.load = _v(g3, 2)   # raw /25 already decoded by mock
            self.tps  = _v(g3, 3)
            self.iat  = _v(g3, 4)

        # Group 6: N75/MAP (prjmod firmware — absent on stock)
        g6 = _cells(6)
        if g6:
            self.n75_dc  = _v(g6, 1)
            self.map_kpa = _v(g6, 3)

        # Battery from group 2
        g2 = _cells(2)
        if g2:
            self.battery = _v(g2, 3)

        # VSS from group 4
        g4 = _cells(4)
        if g4:
            self.vss = _v(g4, 3)

    @property
    def valid(self) -> bool:
        return self.rpm is not None

    def lambda_colour(self) -> str:
        if self.lambda_ is None:
            return "#444444"
        if 0.97 <= self.lambda_ <= 1.03:
            return "#2dff6e"   # green — stoich
        if 0.88 <= self.lambda_ < 0.97:
            return "#ff9900"   # orange — slightly rich
        if 1.03 < self.lambda_ <= 1.15:
            return "#00aaff"   # blue — lean
        if self.lambda_ < 0.88:
            return "#ff3300"   # red — very rich
        return "#ff4466"       # pink — very lean


# ── Qt monitor ────────────────────────────────────────────────────────────────

if _QT_AVAILABLE and _KWP_AVAILABLE:

    class KWPMonitor(QObject):
        """
        Qt wrapper around KWPClient — emits signals for UrROM's overlay.

        Signals
        -------
        connected(str)        — ecu part number on connection
        disconnected()
        live_data(LiveValues) — new state (fires at KWPBridge poll rate)
        mismatch(str, str)    — (ecu_pn, rom_pn)
        """

        connected    = pyqtSignal(str)
        disconnected = pyqtSignal()
        live_data    = pyqtSignal(object)
        mismatch     = pyqtSignal(str, str)

        def __init__(self, parent=None):
            super().__init__(parent)
            self._client:   Optional[KWPClient] = None
            self._rom_pns:  list[str] = []   # all PN variants from ECUDef
            self._matched   = False

            self._timer = QTimer(self)
            self._timer.timeout.connect(self._poll)
            self._timer.start(1000)

        def set_rom_part_numbers(self, pns: list[str]):
            """All PN variants for the loaded variant — any match is OK."""
            self._rom_pns = [p.upper().replace("-", "") for p in pns]
            self._check_match()

        def start(self):
            self._timer.start(1000)

        def stop(self):
            self._timer.stop()
            self._disconnect_client()

        def is_matched(self) -> bool:
            return self._matched

        def current_pn(self) -> str:
            if self._client and self._client.state:
                return self._client.state.get("ecu_id", {}).get("part_number", "")
            return ""

        def _poll(self):
            if self._client and self._client.connected:
                state = self._client.state
                if state:
                    lv = LiveValues(state)
                    if lv.valid:
                        self.live_data.emit(lv)
                    self._check_match()
                return
            if kwpbridge_running():
                self._connect_client()

        def _connect_client(self):
            try:
                self._client = KWPClient(port=DEFAULT_PORT)
                self._client.on_connect(self._on_kwp_connect)
                self._client.on_disconnect(self._on_kwp_disconnect)
                self._client.on_state(self._on_kwp_state)
                self._client.connect(auto_reconnect=False)
            except Exception as e:
                log.debug(f"KWPMonitor: connect error: {e}")
                self._client = None

        def _disconnect_client(self):
            if self._client:
                try:
                    self._client.disconnect()
                except Exception:
                    pass
                self._client = None
            self._matched = False

        def _on_kwp_connect(self):
            self.connected.emit(self.current_pn())
            self._check_match()

        def _on_kwp_disconnect(self):
            self._matched = False
            self.disconnected.emit()

        def _on_kwp_state(self, state: dict):
            lv = LiveValues(state)
            if lv.valid:
                self.live_data.emit(lv)
            self._check_match()

        def _check_match(self):
            if not self._client or not self._client.state:
                self._matched = False
                return
            ecu_pn = self.current_pn().upper().replace("-", "")
            if not ecu_pn or not self._rom_pns:
                self._matched = False
                return
            new_match = ecu_pn in self._rom_pns
            if not new_match and ecu_pn:
                self.mismatch.emit(ecu_pn, self._rom_pns[0] if self._rom_pns else "")
            self._matched = new_match

else:
    class _NoOpSignal:
        def connect(self, *a, **kw):    pass
        def disconnect(self, *a, **kw): pass
        def emit(self, *a, **kw):       pass

    class KWPMonitor:  # type: ignore
        connected    = _NoOpSignal()
        disconnected = _NoOpSignal()
        live_data    = _NoOpSignal()
        mismatch     = _NoOpSignal()

        def __init__(self, parent=None): pass
        def set_rom_part_numbers(self, pns): pass
        def start(self): pass
        def stop(self):  pass
        def is_matched(self) -> bool: return False
        def current_pn(self) -> str:  return ""


# ── Status helpers ────────────────────────────────────────────────────────────

def status_label(monitor: "KWPMonitor", rom_pns: list[str]) -> tuple[str, str]:
    """Return (text, colour) for the KWP status badge."""
    if not _KWP_AVAILABLE:
        return "KWPBridge not installed", "#555555"
    if not kwpbridge_running():
        return "KWPBridge not running", "#555555"
    ecu_pn = monitor.current_pn() if monitor else ""
    if not ecu_pn:
        return "KWPBridge running — no ECU", "#ffaa00"
    if monitor and monitor.is_matched():
        return f"🟢  {ecu_pn}  ·  ECU matches ROM", "#2dff6e"
    rom_str = rom_pns[0] if rom_pns else "?"
    return f"🟡  {ecu_pn}  ≠  {rom_str}  ·  mismatch", "#ffaa00"


def live_summary(lv: "LiveValues") -> str:
    if lv is None or not lv.valid:
        return ""
    parts = []
    if lv.rpm     is not None: parts.append(f"{lv.rpm:.0f} RPM")
    if lv.ect     is not None: parts.append(f"{lv.ect:.0f}°C")
    if lv.lambda_ is not None: parts.append(f"λ {lv.lambda_:.3f}")
    if lv.timing  is not None: parts.append(f"{lv.timing:.1f}° ign")
    if lv.map_kpa is not None: parts.append(f"{lv.map_kpa:.0f} kPa")
    return "  ·  ".join(parts)


# ── DashboardWindow ───────────────────────────────────────────────────────────

class DashboardWindow:
    """
    Floating live-data dashboard for UrROM / M2.3.2.

    Shows the values most relevant while editing M2.3.2 maps:
    RPM, ECT, load %, lambda, ignition, MAP kPa, N75 duty, IAT.
    Wires directly to a KWPMonitor — works with real ECU or mock.
    """

    _C_BG     = "#0d1117"
    _C_PANEL  = "#131920"
    _C_BORDER = "#1a2332"
    _C_TEXT   = "#c9d1d9"
    _C_DIM    = "#6e7681"
    _C_GREEN  = "#2dff6e"
    _C_AMBER  = "#ffaa00"
    _C_RED    = "#ff4444"
    _C_BLUE   = "#4488ff"
    _C_PURPLE = "#aa66ff"

    def __init__(self, monitor, parent=None):
        from PyQt5.QtWidgets import (
            QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
            QLabel, QFrame, QProgressBar,
        )
        from PyQt5.QtCore import Qt

        self._monitor = monitor
        self._win = QWidget(parent, Qt.Window)
        self._win.setWindowTitle("Live ECU — UrROM / M2.3.2")
        self._win.setMinimumSize(640, 380)
        self._win.setStyleSheet(
            f"background:{self._C_BG}; color:{self._C_TEXT};"
        )

        root = QVBoxLayout(self._win)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # Status bar
        sr = QHBoxLayout()
        self._lbl_status = QLabel("● Waiting for data…")
        self._lbl_status.setStyleSheet(
            f"color:{self._C_DIM}; font-size:10px; letter-spacing:1px;"
        )
        self._lbl_scenario = QLabel("")
        self._lbl_scenario.setStyleSheet(
            f"color:{self._C_PURPLE}; font-size:10px; font-style:italic;"
        )
        sr.addWidget(self._lbl_status)
        sr.addStretch()
        sr.addWidget(self._lbl_scenario)
        root.addLayout(sr)

        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color:{self._C_BORDER};"); root.addWidget(sep)

        # 2 × 4 gauge grid — M2.3.2 has more channels than 7A
        grid = QGridLayout(); grid.setSpacing(8); root.addLayout(grid)
        self._gauges = {}

        # (key, label, unit, min, max, warn_lo, warn_hi, crit_hi, row, col)
        specs = [
            ("rpm",     "RPM",      "",    400, 7500, None,  6800, 7200, 0, 0),
            ("ect",     "ECT",      "°C",  -10,  120,   60,   105,  115, 0, 1),
            ("load",    "LOAD",     "%",     0,  100, None,    90,   98, 0, 2),
            ("lambda",  "LAMBDA",   "λ",  0.70, 1.30, None,  None, None, 0, 3),
            ("timing",  "TIMING",   "°",    -5,   50, None,  None, None, 1, 0),
            ("map_kpa", "MAP",      "kPa",  20,  300, None,  None, None, 1, 1),
            ("n75_dc",  "N75",      "% DC",  0,  100, None,    95, None, 1, 2),
            ("iat",     "IAT",      "°C",  -20,   60, None,    50,   55, 1, 3),
        ]

        for key, label, unit, vmin, vmax, wl, wh, ch, row, col in specs:
            panel = self._make_panel(key, label, unit, vmin, vmax, wl, wh, ch)
            grid.addWidget(panel, row, col)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet(f"color:{self._C_BORDER};"); root.addWidget(sep2)

        self._lbl_strip = QLabel("")
        self._lbl_strip.setStyleSheet(
            f"color:{self._C_DIM}; font-size:10px; font-family:Consolas;"
        )
        root.addWidget(self._lbl_strip)

        monitor.live_data.connect(self._on_live)
        monitor.disconnected.connect(self._on_disconnect)
        monitor.connected.connect(self._on_connect)

    def _make_panel(self, key, label, unit, vmin, vmax, wl, wh, ch):
        from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QProgressBar
        from PyQt5.QtCore import Qt

        panel = QWidget()
        panel.setStyleSheet(
            f"background:{self._C_PANEL}; border:1px solid {self._C_BORDER}; border-radius:4px;"
        )
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(8, 6, 8, 6); lay.setSpacing(2)

        ln = QLabel(label)
        ln.setStyleSheet(f"color:{self._C_DIM}; font-size:9px; letter-spacing:2px; border:none;")
        lv = QLabel("—"); lv.setAlignment(Qt.AlignCenter)
        lv.setStyleSheet(f"color:{self._C_TEXT}; font-size:24px; font-weight:bold; border:none;")
        lu = QLabel(unit); lu.setAlignment(Qt.AlignCenter)
        lu.setStyleSheet(f"color:{self._C_DIM}; font-size:10px; border:none;")
        bar = QProgressBar()
        bar.setRange(int(vmin * 10), int(vmax * 10))
        bar.setValue(int(vmin * 10)); bar.setTextVisible(False); bar.setFixedHeight(4)
        bar.setStyleSheet(
            f"QProgressBar{{background:{self._C_BG}; border-radius:2px; border:none;}}"
            f"QProgressBar::chunk{{background:{self._C_GREEN}; border-radius:2px;}}"
        )

        lay.addWidget(ln); lay.addWidget(lv); lay.addWidget(lu); lay.addWidget(bar)
        self._gauges[key] = dict(lv=lv, bar=bar, unit=unit, vmin=vmin, vmax=vmax,
                                  wl=wl, wh=wh, ch=ch)
        return panel

    def _colour(self, key, val):
        if val is None: return self._C_DIM
        g = self._gauges[key]
        if g["ch"] is not None and val >= g["ch"]: return self._C_RED
        if g["wh"] is not None and val >= g["wh"]: return self._C_AMBER
        if g["wl"] is not None and val <= g["wl"]: return self._C_AMBER
        if key == "lambda":
            lv_col = self._C_GREEN if 0.97 <= val <= 1.03 else \
                     self._C_AMBER if abs(val - 1.0) <= 0.12 else self._C_RED
            return lv_col
        return self._C_GREEN

    def _update(self, key, val):
        if key not in self._gauges: return
        g = self._gauges[key]; col = self._colour(key, val)
        if val is None:
            g["lv"].setText("—")
            g["lv"].setStyleSheet(f"color:{self._C_DIM}; font-size:24px; font-weight:bold; border:none;")
            return
        u = g["unit"]
        txt = f"{val:.3f}" if u == "λ" else f"{val:.1f}" if u in ("°C","°","V","% DC") else f"{val:.0f}"
        g["lv"].setText(txt)
        g["lv"].setStyleSheet(f"color:{col}; font-size:24px; font-weight:bold; border:none;")
        clamped = max(g["vmin"], min(g["vmax"], val))
        g["bar"].setValue(int(clamped * 10))
        g["bar"].setStyleSheet(
            f"QProgressBar{{background:{self._C_BG}; border-radius:2px; border:none;}}"
            f"QProgressBar::chunk{{background:{col}; border-radius:2px;}}"
        )

    def _on_live(self, lv):
        self._update("rpm",     lv.rpm)
        self._update("ect",     lv.ect)
        self._update("lambda",  lv.lambda_)
        self._update("timing",  lv.timing)
        self._update("map_kpa", lv.map_kpa)
        self._update("n75_dc",  lv.n75_dc)
        self._update("iat",     lv.iat)
        if lv.load is not None:
            load_pct = (lv.load / 255.0) * 100.0
            self._update("load", load_pct)
        else:
            self._update("load", None)
        parts = []
        if lv.rpm     is not None: parts.append(f"{lv.rpm:.0f} RPM")
        if lv.ect     is not None: parts.append(f"{lv.ect:.0f}°C")
        if lv.lambda_ is not None: parts.append(f"λ {lv.lambda_:.3f}")
        if lv.timing  is not None: parts.append(f"{lv.timing:.1f}°")
        if lv.map_kpa is not None: parts.append(f"{lv.map_kpa:.0f} kPa")
        self._lbl_strip.setText("  ·  ".join(parts))
        self._lbl_status.setText(f"● Live  ·  {lv.ecu_pn or '—'}")
        self._lbl_status.setStyleSheet(f"color:{self._C_GREEN}; font-size:10px; letter-spacing:1px;")

    def _on_connect(self, ecu_pn):
        self._lbl_status.setText(f"● Connected  ·  {ecu_pn}")
        self._lbl_status.setStyleSheet(f"color:{self._C_GREEN}; font-size:10px; letter-spacing:1px;")

    def _on_disconnect(self):
        self._lbl_status.setText("● Disconnected")
        self._lbl_status.setStyleSheet(f"color:{self._C_RED}; font-size:10px; letter-spacing:1px;")
        self._lbl_strip.setText("")
        for k in self._gauges: self._update(k, None)

    def show(self):
        self._win.show(); self._win.raise_(); self._win.activateWindow()

    def hide(self): self._win.hide()
    def is_visible(self): return self._win.isVisible()

    def close(self):
        try:
            self._monitor.live_data.disconnect(self._on_live)
            self._monitor.disconnected.disconnect(self._on_disconnect)
            self._monitor.connected.disconnect(self._on_connect)
        except Exception: pass
        self._win.close()
