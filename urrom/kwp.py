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
import threading
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
        self.knock:    Optional[list]  = None   # [V × 5 cylinders] from group 5
        self.lambda_ctrl: Optional[float] = None  # 3B block cell 8 (128 = centre)
        self.ign_raw53: Optional[float] = None    # 3B block cell 10, ECU units
        self.family:   str = "551"
        self.ecu_pn:   str = ""

        if not state or not state.get("connected"):
            return

        self.ecu_pn = state.get("ecu_id", {}).get("part_number", "")
        groups = state.get("groups", {})

        # KWPBridge cells are dicts whose "value" is ALREADY DECODED by the
        # bridge's KWP1281 formula table (RPM in rpm, ECT in °C, λ as a ratio,
        # timing in °BTDC; load stays the raw 1..255 byte because its formula
        # is "no units").  Only the legacy tools/mock_engine.py list-of-ints
        # format carries raw bytes, so the raw decode formulas apply to that
        # format alone.  (2026-09-09: previously both were decoded, which made
        # the bridge's 3000 rpm read as 120000.)
        self._raw_format = False

        def _cells(grp_key) -> dict:
            g = groups.get(str(grp_key), groups.get(grp_key, {}))
            raw = g.get("cells", []) if isinstance(g, dict) else g
            if not raw:
                return {}
            # KWPBridge format: [{index:N, value:X, unit:U}, ...]  (decoded)
            if isinstance(raw[0], dict):
                return {c["index"]: c for c in raw}
            # Legacy mock format: [int, int, int, int]  (raw bytes, 1-based)
            self._raw_format = True
            return {i + 1: {"index": i + 1, "value": float(v)} for i, v in enumerate(raw)}

        def _v(cells, idx) -> Optional[float]:
            c = cells.get(idx)
            if c is None:
                return None
            return c["value"] if isinstance(c, dict) else float(c)

        # Raw-byte decode formulas, used ONLY for the legacy int-list format
        # (confirmed from .lbl / WinlogDriver):
        # Group 1: cell1=RPM×40, cell2=ECT−70°C, cell3=λ/128, cell4=IGN raw
        # Group 3: cell1=RPM×40, cell2=load raw, cell3=TPS×0.416%, cell4=IAT−70°C
        # Group 6: cell1=N75 DC raw, cell3=MAP kPa raw  [prjmod only]
        raw = lambda: self._raw_format   # evaluated after _cells() ran

        def _rpm(v):   return None if v is None else (v * 40 if raw() else v)
        def _ect(v):   return None if v is None else (v - 70 if raw() else v)
        def _lam(v):   return None if v is None else (v / 128 if raw() else v)
        def _ign(v):   return None if v is None else (v * 0.6491 - 8.2186 if raw() else v)
        def _tps(v):   return None if v is None else (v * 0.416 if raw() else v)
        def _iat(v):   return None if v is None else (v - 70 if raw() else v)

        # ── Bosch M2.3 (3B / RR / S2-3B, 447907404): early KW1281 dialect ──
        # KWPBridge serves group 0 = the ECU's 10-byte raw block and group 100 =
        # a read-RAM window (36h..3Fh, 53h/54h).  Prefer the RAM window: its RPM
        # (3Ah x40) does not saturate at 2550 like the block's 3Bh/10 byte, and
        # its load is RAM 3Fh, the fuel/ignition map's own load axis.
        self.family = "404" if (self.ecu_pn.startswith(("447907404", "857907404", "895907404"))
                                or ("100" in groups and "1" not in groups)) else "551"
        if self.family == "404":
            g100 = _cells(100)
            g0 = _cells(0)
            if g100:
                self.battery = _v(g100, 1)
                self.iat     = _v(g100, 2)
                self.ect     = _v(g100, 3)
                self.rpm     = _v(g100, 4)
                self.load    = _v(g100, 5)
                # cells 6-8 are raw 53h / 54h / XRAM 5Fh; cell 9 (KWPBridge-derived)
                # is the ECU's own formula 0.75*|54h + max(0,127-5Fh) - 127|.
                self.timing = _v(g100, 9)
                if self.timing is None:
                    b54, x5f = _v(g100, 7), _v(g100, 8)
                    if b54 is not None and x5f is not None:
                        self.timing = round(0.75 * abs(min(255, b54 + max(0, 127 - x5f)) - 127), 2)
            if g0:
                if self.ect is None:    self.ect    = _v(g0, 1)
                if self.load is None:   self.load   = _v(g0, 2)
                if self.rpm is None:    self.rpm    = _v(g0, 3)
                self.ign_raw53 = _v(g0, 10)      # ECU units (idle 35-37); not degrees
                lc = _v(g0, 8)
                self.lambda_ctrl = lc                       # 128 = no correction
                # the 3B has no wideband/lambda-factor cell; approximate the
                # control deviation as a factor so the overlay colour still works
                self.lambda_ = None if lc is None else round(1.0 + (lc - 128) / 256.0, 3)
            return

        g1 = _cells(1)
        if g1:
            self.rpm     = _rpm(_v(g1, 1))
            self.ect     = _ect(_v(g1, 2))
            self.lambda_ = _lam(_v(g1, 3))
            self.timing  = _ign(_v(g1, 4))

        # Group 3: RPM, load (raw), TPS, IAT
        g3 = _cells(3)
        if g3:
            if self.rpm is None:
                self.rpm = _rpm(_v(g3, 1))
            self.load = _v(g3, 2)       # raw 0-255, keep raw for map overlay
            self.tps  = _tps(_v(g3, 3))
            self.iat  = _iat(_v(g3, 4))

        # Group 6: N75/MAP (prjmod firmware — absent on stock)
        g6 = _cells(6)
        if g6:
            n75_raw      = _v(g6, 1)
            map_raw      = _v(g6, 3)
            if self._raw_format:
                self.n75_dc  = n75_raw / 2.55 if n75_raw is not None else None
                self.map_kpa = map_raw / 255 * 300 if map_raw is not None else None
            else:
                self.n75_dc  = n75_raw
                self.map_kpa = map_raw

        # Battery from group 2
        g2 = _cells(2)
        if g2:
            self.battery = _v(g2, 3)

        # VSS from group 4
        g4 = _cells(4)
        if g4:
            self.vss = _v(g4, 3)

        # Knock (5 cylinders) from group 5 — cells 1-5, each in Volts
        g5 = _cells(5)
        if g5:
            vals = [_v(g5, i) for i in range(1, 6)]
            if any(v is not None for v in vals):
                self.knock = vals

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
            ("rpm",     "RPM",      "",     400, 7500, None,  6800, 7200, 0, 0),
            ("ect",     "ECT",      "°C",   -10,  120,   60,   105,  115, 0, 1),
            ("load",    "LOAD",     "",       0,  200, None,   160,  190, 0, 2),
            ("lambda",  "LAMBDA",   "λ",   0.70, 1.30, None,  None, None, 0, 3),
            ("timing",  "TIMING",   "°",     -5,   50, None,  None, None, 1, 0),
            ("map_kpa", "MAP",      "kPa",   20,  300, None,  None, None, 1, 1),
            ("n75_dc",  "N75",      "% DC",   0,  100, None,    95, None, 1, 2),
            ("iat",     "IAT",      "°C",   -20,   60, None,    50,   55, 1, 3),
            ("vss",     "SPEED",    "km/h",   0,  300, None,  None, None, 2, 0),
            ("knock",   "KNOCK",    "V",    0.0,  3.0, None,   0.8,  1.8, 2, 1),
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
        self._lbl_knock = QLabel("Knock  —")
        self._lbl_knock.setStyleSheet(f"color:{self._C_DIM}; font-size:10px;")
        root.addWidget(self._lbl_knock)

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
        self._update("vss",     lv.vss)
        # Load: already decoded as float by LiveValues
        self._update("load", lv.load)
        # Knock: max of 5 channels — show worst cylinder
        knock_vals = [v for v in (lv.knock or []) if v is not None]
        knock_max = max(knock_vals) if knock_vals else None
        self._update("knock", knock_max)
        # Knock indicator label: show per-cylinder compact view
        if knock_vals:
            kstr = "  ".join(f"{v:.1f}" for v in knock_vals[:5])
            self._lbl_knock.setText(f"Knock  {kstr}  V")
            col = self._C_RED if knock_max and knock_max > 1.8 else                   self._C_AMBER if knock_max and knock_max > 0.8 else self._C_GREEN
            self._lbl_knock.setStyleSheet(f"color:{col}; font-size:10px;")
        else:
            self._lbl_knock.setText("Knock  —")
            self._lbl_knock.setStyleSheet(f"color:{self._C_DIM}; font-size:10px;")
        parts = []
        if lv.rpm     is not None: parts.append(f"{lv.rpm:.0f} RPM")
        if lv.ect     is not None: parts.append(f"{lv.ect:.0f}°C")
        if lv.lambda_ is not None: parts.append(f"λ {lv.lambda_:.3f}")
        if lv.timing  is not None: parts.append(f"{lv.timing:.1f}°")
        if lv.map_kpa is not None: parts.append(f"{lv.map_kpa:.0f} kPa")
        if lv.vss     is not None: parts.append(f"{lv.vss:.0f} km/h")
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


# ── Mock KWP bridge (for testing without real ECU/KWPBridge) ─────────────────

class MockKWPClient:
    """
    Drop-in KWPClient replacement that reads from tools/mock_engine.py
    over a local TCP socket. Enables KWP overlay testing without hardware.

    Usage:
        server = MockECUServer(port=50266, scenario=CycleScenario(), ...)
        server.start()
        # UrROM KWPMonitor will auto-connect as if KWPBridge were running
    """

    def __init__(self, port: int = DEFAULT_PORT):
        self._port     = port
        self._sock:   Optional[socket.socket] = None
        self._state:  dict = {}
        self._connected = False
        self._on_connect_cb    = None
        self._on_disconnect_cb = None
        self._on_state_cb      = None
        self._thread:  Optional[threading.Thread] = None
        self._running  = False

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def state(self) -> dict:
        return self._state

    def on_connect(self, cb):    self._on_connect_cb    = cb
    def on_disconnect(self, cb): self._on_disconnect_cb = cb
    def on_state(self, cb):      self._on_state_cb      = cb

    def connect(self, **kw):
        self._running = True
        self._thread  = threading.Thread(target=self._recv_loop, daemon=True)
        self._thread.start()

    def disconnect(self):
        self._running = False
        if self._sock:
            try: self._sock.close()
            except Exception: pass
        self._connected = False

    def _recv_loop(self):
        import socket as _socket, json as _json, time as _time
        buf = b""
        while self._running:
            if not self._connected:
                try:
                    s = _socket.create_connection(("127.0.0.1", self._port), timeout=2)
                    self._sock = s
                    self._connected = True
                    if self._on_connect_cb:
                        self._on_connect_cb()
                except Exception:
                    _time.sleep(1)
                    continue
            try:
                chunk = self._sock.recv(4096)
                if not chunk:
                    raise ConnectionResetError("server closed")
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    try:
                        self._state = _json.loads(line)
                        if self._on_state_cb:
                            self._on_state_cb(self._state)
                    except Exception:
                        pass
            except Exception:
                self._connected = False
                self._sock = None
                if self._on_disconnect_cb:
                    self._on_disconnect_cb()
                _time.sleep(0.5)


def mock_kwpbridge_running(port: int = DEFAULT_PORT) -> bool:
    """Return True if a mock engine server is listening on port."""
    import socket as _socket
    try:
        s = _socket.create_connection(("127.0.0.1", port), timeout=0.3)
        s.close()
        return True
    except Exception:
        return False
