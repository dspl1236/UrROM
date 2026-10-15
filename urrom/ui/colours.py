"""
Heat-map colour helpers shared by the map table, the editor tabs and the compare tab.

Split out of app/main.py (2026-10-14); the classes and helpers are unchanged.
"""
from PyQt5.QtGui import QColor


# ── Colour helpers ────────────────────────────────────────────────────────────

def heat(value: float, vmin: float, vmax: float) -> QColor:
    t = max(0.0, min(1.0, (value - vmin) / max(1, vmax - vmin)))
    if t < 0.25:
        u = t / 0.25
        return QColor(0, int(u * 80), int(100 + u * 100))
    elif t < 0.5:
        u = (t - 0.25) / 0.25
        return QColor(0, int(80 + u * 120), int(200 - u * 200))
    elif t < 0.75:
        u = (t - 0.5) / 0.25
        return QColor(int(u * 220), 200, 0)
    else:
        u = (t - 0.75) / 0.25
        return QColor(220, int(200 - u * 200), 0)

def fuel_colour(raw: int) -> QColor:
    # 128 = stoich (neutral blue-ish); <128 lean (cool); >128 rich (warm)
    return heat(raw, 100, 200)

def ign_colour(deg: float) -> QColor:
    # -10° (cold) to 40° (hot)
    return heat(deg, -10, 42)

def text_colour(bg: QColor) -> QColor:
    lum = bg.red() * 0.299 + bg.green() * 0.587 + bg.blue() * 0.114
    return QColor("#111") if lum > 130 else QColor("#eee")
