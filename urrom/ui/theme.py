"""
urrom/ui/theme.py — Shared palette and application stylesheet.

Single source of truth for colours used by app/main.py and the workbench
widgets in urrom/ui/.  Keep everything here so a future light theme is a
one-file change.
"""

BG       = "#1e1e1e"
BG2      = "#252526"
BG3      = "#2d2d2d"
BORDER   = "#3c3c3c"
FG       = "#d4d4d4"
FG_DIM   = "#666666"
ACCENT   = "#569cd6"
GREEN    = "#2dff6e"
AMBER    = "#ff9900"
RED      = "#ff4444"
CHANGED  = "#2dff6e"


def confidence_colour(conf: str) -> str:
    """Map a MapDef.confidence string to a palette colour."""
    if conf == "CONFIRMED":
        return GREEN
    if conf == "PROVISIONAL":
        return AMBER
    return RED


STYLESHEET = f"""
QMainWindow, QWidget {{
    background: {BG};
    color: {FG};
    font-family: "Segoe UI", "SF Pro Display", sans-serif;
    font-size: 12px;
}}
QTabWidget::pane {{
    border: 1px solid {BORDER};
    background: {BG2};
}}
QTabBar::tab {{
    background: {BG};
    color: {FG_DIM};
    padding: 6px 14px;
    border: 1px solid {BORDER};
    border-bottom: none;
    margin-right: 2px;
    font-size: 11px;
}}
QTabBar::tab:selected {{
    background: {BG2};
    color: {FG};
    border-bottom: 2px solid {ACCENT};
}}
QPushButton {{
    background: {BG3};
    color: {FG};
    border: 1px solid {BORDER};
    padding: 5px 14px;
    border-radius: 3px;
    font-size: 11px;
}}
QPushButton:hover {{ background: #3a3a3a; border-color: {ACCENT}; }}
QPushButton:pressed {{ background: #444; }}
QPushButton:disabled {{ color: {FG_DIM}; }}
QComboBox {{
    background: {BG3};
    color: {FG};
    border: 1px solid {BORDER};
    padding: 3px 8px;
    border-radius: 3px;
    min-width: 220px;
}}
QComboBox QAbstractItemView {{
    background: {BG2};
    color: {FG};
    border: 1px solid {BORDER};
    selection-background-color: #3a3a3a;
}}
QTableWidget {{
    background: {BG2};
    gridline-color: {BORDER};
    font-size: 11px;
    border: 1px solid {BORDER};
}}
QHeaderView::section {{
    background: {BG3};
    color: {FG_DIM};
    font-size: 10px;
    padding: 2px 4px;
    border: 1px solid {BORDER};
}}
QStatusBar {{ background: {BG}; color: {FG_DIM}; font-size: 11px; }}
QLabel {{ color: {FG}; }}
QFrame {{ color: {BORDER}; }}
QScrollBar:vertical {{
    background: {BG};
    width: 8px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 4px;
}}
QScrollBar:horizontal {{
    background: {BG};
    height: 8px;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER};
    border-radius: 4px;
}}
QScrollArea {{ border: none; background: {BG}; }}

/* ── Workbench chrome ─────────────────────────────────────────────────── */
QDockWidget {{
    color: {FG_DIM};
    font-size: 10px;
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
}}
QDockWidget::title {{
    background: {BG2};
    border-bottom: 1px solid {BORDER};
    padding: 4px 8px;
    text-align: left;
}}
QMainWindow::separator {{
    background: {BORDER};
    width: 2px;
    height: 2px;
}}
QMainWindow::separator:hover {{ background: {ACCENT}; }}
QTreeWidget {{
    background: {BG2};
    color: {FG};
    border: none;
    font-size: 11px;
    outline: none;
}}
QTreeWidget::item {{ padding: 2px 0; }}
QTreeWidget::item:selected {{
    background: rgba(86, 156, 214, 0.28);
    color: {FG};
}}
QTreeWidget::item:disabled {{ color: {FG_DIM}; }}
QTreeWidget::item:hover {{ background: {BG3}; }}
QTreeWidget::branch {{ background: {BG2}; }}
QLineEdit {{
    background: {BG2};
    color: {FG};
    border: 1px solid {BORDER};
    border-radius: 3px;
    padding: 3px 6px;
    font-size: 11px;
}}
QLineEdit:focus {{ border-color: {ACCENT}; }}
QSplitter::handle {{ background: {BORDER}; }}
QToolTip {{
    background: {BG3};
    color: {FG};
    border: 1px solid {BORDER};
    padding: 4px;
}}
"""
