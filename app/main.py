"""
UrROM — Desktop GUI
PyQt5 ROM editor for Bosch Motronic M2.3 / M2.3.2 ECUs.
Dual-EPROM support: main chip (fuel + ignition) + boost chip.
"""

import sys
from pathlib import Path

from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStatusBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtCore import QTimer, Qt

sys.path.insert(0, str(Path(__file__).parent.parent))
from urrom.version import APP_NAME, APP_VERSION, WINDOW_TITLE
from urrom.ecu_profiles import (
    DetectionResult,
    MAIN_CHIP_PHYSICAL,
    MAIN_CHIP_WORKING,
    check_chip_pair,
    detect_rom,
    normalize_rom,
    read_map,
    write_map,
)
from urrom.kwp import (
    KWPMonitor,
    LiveValues,
    kwpbridge_available,
    kwpbridge_running,
    live_summary as kwp_live_summary,
    status_label as kwp_status_label,
)
from urrom.hw_patches import (
    apply_lambda_delay,
    apply_load_decap,
    apply_mfts_bypass,
    revert_lambda_delay,
    revert_load_decap,
    revert_mfts_bypass,
)
from urrom.ui.theme import ACCENT, AMBER, BG, BG2, BG3, BORDER, FG, FG_DIM, STYLESHEET
from urrom.ui.map_tree import MapTree
from urrom.ui.health_panel import HealthPanel
from urrom.ui.boost_tab import BoostTab
from urrom.ui.compare_tab import CompareTab
from urrom.ui.hardware_tab import HardwareTab
from urrom.ui.main_chip_tab import MainChipTab
from urrom.ui.overview_tab import OverviewTab
from urrom.ui.widgets import InfoStrip

# Re-exported for scripts and tests that import the grid from here.
from urrom.ui.map_table import MapTable  # noqa: E402,F401

# ── Main window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(1100, 720)

        self.setAcceptDrops(True)

        # State
        self._main_path:  Path | None = None
        self._boost_path: Path | None = None
        self._main_rom:   bytearray | None = None   # working half
        self._main_full:  bytes | None = None       # full original image (64KB split-bank)
        self._main_fw:    bytearray | None = None   # firmware half (lower 32KB) — patches/scalars edit this
        self._boost_rom:  bytearray | None = None
        # Session changelog
        from urrom.session_log import SessionLog
        self._session_log = SessionLog()
        # Map edit snapshots for cross-save history
        self._save_count = 0
        self._save_snapshots: list[dict] = []  # [{name, crc, timestamp}]
        # Recent files (stored in memory — persisted on exit via QSettings)
        from PyQt5.QtCore import QSettings
        self._settings = QSettings("UrROM", "UrROM")
        self._recent_files = list(self._settings.value("recent_files", []) or [])
        self._det:        DetectionResult | None = None
        self._unsaved     = False
        self._kwp_matched = False

        self._build_ui()
        self._build_menu()
        self._update_status("Ready — open a ROM file to begin")

        # ── KWPBridge live overlay ────────────────────────────────────────────
        from urrom.livelog import LiveRecorder, TraceAccumulator
        self._recorder = LiveRecorder()
        self._trace = TraceAccumulator()
        self._trace_dirty = 0
        self._bench = None                 # kwpbridge.mock.server.MockServer while bench mode is on
        self._bench_pn = ""
        self._kwp_monitor = KWPMonitor(self)
        self._kwp_monitor.connected.connect(self._on_kwp_connected)
        self._kwp_monitor.disconnected.connect(self._on_kwp_disconnected)
        self._kwp_monitor.live_data.connect(self._on_kwp_live_data)
        self._kwp_monitor.mismatch.connect(self._on_kwp_mismatch)
        # Poll KWP status badge every 2 s even when no ECU connected
        self._kwp_status_timer = QTimer(self)
        self._kwp_status_timer.timeout.connect(self._refresh_kwp_badge)
        self._kwp_status_timer.start(2000)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        self.setStyleSheet(STYLESHEET)
        self.setDockOptions(QMainWindow.AnimatedDocks)

        from PyQt5.QtWidgets import QDockWidget, QStackedWidget

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── File bar ─────────────────────────────────────────────────────────
        file_bar = QFrame()
        file_bar.setFixedHeight(40)
        file_bar.setStyleSheet(
            f"background: {BG2}; border-bottom: 1px solid {BORDER};")
        fb_layout = QHBoxLayout(file_bar)
        fb_layout.setContentsMargins(10, 0, 10, 0)
        fb_layout.setSpacing(8)

        self._open_btn  = QPushButton("Open ROM…")
        self._boost_btn = QPushButton("Open Boost Chip…")
        self._save_btn  = QPushButton("Save ROM…")
        self._save_btn.setEnabled(False)

        self._main_lbl  = QLabel("No main chip")
        self._main_lbl.setStyleSheet(f"color: {FG_DIM}; font-size: 11px;")
        self._boost_lbl = QLabel("No boost chip")
        self._boost_lbl.setStyleSheet(f"color: {FG_DIM}; font-size: 11px;")

        fb_layout.addWidget(self._open_btn)
        fb_layout.addWidget(self._main_lbl)
        fb_layout.addWidget(QLabel("|", styleSheet=f"color: {BORDER};"))
        fb_layout.addWidget(self._boost_btn)
        fb_layout.addWidget(self._boost_lbl)
        fb_layout.addStretch()

        # KWP status badge
        self._kwp_badge = QLabel("● KWPBridge")
        self._kwp_badge.setStyleSheet(
            f"color: {FG_DIM}; font-size: 10px; padding: 0 8px;")
        fb_layout.addWidget(self._kwp_badge)
        fb_layout.addWidget(self._save_btn)
        root.addWidget(file_bar)

        # ── Info strip ───────────────────────────────────────────────────────
        self._info_strip = InfoStrip()
        root.addWidget(self._info_strip)

        # ── Panels ───────────────────────────────────────────────────────────
        self._overview_tab  = OverviewTab()
        self._main_chip_tab = MainChipTab()
        self._boost_tab     = BoostTab()
        self._compare_tab   = CompareTab()
        self._hardware_tab  = HardwareTab()
        self._health_panel  = HealthPanel()

        # Workbench mode: the map tree selects maps, editors show a title only
        self._main_chip_tab.use_external_selector(True)
        self._boost_tab.use_external_selector(True)
        self._overview_tab.set_compact(True)

        # ── Centre: Editor (welcome / main / boost) + Compare ────────────────
        self._editor_stack = QStackedWidget()
        self._welcome = self._make_welcome()
        self._editor_stack.addWidget(self._welcome)         # 0
        self._editor_stack.addWidget(self._main_chip_tab)   # 1
        self._editor_stack.addWidget(self._boost_tab)       # 2

        self._center_tabs = QTabWidget()
        self._center_tabs.setDocumentMode(True)
        self._center_tabs.addTab(self._editor_stack, "Editor")
        self._center_tabs.addTab(self._compare_tab,  "Compare")
        root.addWidget(self._center_tabs, 1)

        # ── Left dock: map navigator ─────────────────────────────────────────
        self._map_tree = MapTree()
        self._map_tree.mapActivated.connect(self._on_tree_map_activated)
        self._maps_dock = QDockWidget("MAPS", self)
        self._maps_dock.setObjectName("MapsDock")
        self._maps_dock.setWidget(self._map_tree)
        self._maps_dock.setFeatures(
            QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable)
        self._maps_dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self._map_tree.setMinimumWidth(200)
        self.addDockWidget(Qt.LeftDockWidgetArea, self._maps_dock)

        # ── Right dock: inspector (ROM / Hardware / Health) ──────────────────
        def _scrolled(w: QWidget) -> QScrollArea:
            sa = QScrollArea()
            sa.setWidgetResizable(True)
            sa.setWidget(w)
            sa.setFrameShape(QFrame.NoFrame)
            return sa

        self._inspector = QTabWidget()
        self._inspector.setDocumentMode(True)
        self._inspector.addTab(_scrolled(self._overview_tab), "ROM")
        self._inspector.addTab(_scrolled(self._hardware_tab), "Hardware")
        self._inspector.addTab(self._health_panel,            "Health")
        self._inspector_dock = QDockWidget("INSPECTOR", self)
        self._inspector_dock.setObjectName("InspectorDock")
        self._inspector_dock.setWidget(self._inspector)
        self._inspector_dock.setFeatures(
            QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable)
        self._inspector_dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self._inspector.setMinimumWidth(300)
        self.addDockWidget(Qt.RightDockWidgetArea, self._inspector_dock)

        self.resizeDocks([self._maps_dock, self._inspector_dock], [250, 400], Qt.Horizontal)

        # ── Cross-wiring ─────────────────────────────────────────────────────
        # Overview inventory double-click → editor
        self._overview_tab.set_jump_callback(self._show_main_map_by_variant_index)
        # Editor combo changes (find / scan / overlays drive it) → tree highlight
        self._main_chip_tab._map_combo.currentIndexChanged.connect(
            lambda _i: self._sync_tree_selection())
        self._boost_tab._map_combo.currentIndexChanged.connect(
            lambda _i: self._sync_tree_selection())
        # Health panel
        self._health_panel.issueActivated.connect(self._on_health_issue)
        self._health_panel.rescanRequested.connect(self._run_health_scan)

        # Hover status + session log + title
        self._main_chip_tab.set_status_fn(self._update_status)

        def _log_cell(map_def, r, c, old_raw, new_raw, row_axis=None, col_axis=None, chip="main"):
            self._session_log.record(map_def, r, c, old_raw, new_raw, row_axis, col_axis, chip,
                                     variant=self._det.variant if self._det else None)
        self._main_chip_tab.set_session_log_fn(_log_cell)
        self._boost_tab.set_session_log_fn(_log_cell)
        self._main_chip_tab.set_title_fn(lambda name: self._update_title(name))

        # Status bar
        self._status = QStatusBar()
        self.setStatusBar(self._status)

        # Buttons
        self._open_btn.clicked.connect(self._on_open_main)
        self._boost_btn.clicked.connect(self._on_open_boost)
        self._save_btn.clicked.connect(self._on_save)

        # Restore window geometry + dock layout from previous session
        geom = self._settings.value("geometry")
        if geom:
            self.restoreGeometry(geom)
        state = self._settings.value("windowState")
        if state:
            self.restoreState(state)

    # ── Workbench plumbing ────────────────────────────────────────────────────

    def _make_welcome(self) -> QWidget:
        """Centre-pane placeholder shown until a ROM is loaded."""
        from PyQt5.QtWidgets import QListWidget
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.addStretch(2)
        box = QWidget()
        box.setMaximumWidth(520)
        lay = QVBoxLayout(box)
        lay.setSpacing(10)
        title = QLabel(f"{APP_NAME}")
        title.setStyleSheet(f"font-size:26px;font-weight:bold;color:{FG};")
        title.setAlignment(Qt.AlignCenter)
        sub = QLabel("Bosch Motronic M2.3 / M2.3.2 ROM editor")
        sub.setStyleSheet(f"font-size:12px;color:{FG_DIM};")
        sub.setAlignment(Qt.AlignCenter)
        hint = QLabel(
            "Open a ROM (Ctrl+O), or drop a .bin / .034 file anywhere in this window.\n"
            "Load the boost chip afterwards to unlock boost maps and MAP-sensor detection.")
        hint.setStyleSheet(f"font-size:11px;color:{FG_DIM};")
        hint.setAlignment(Qt.AlignCenter)
        hint.setWordWrap(True)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        open_btn = QPushButton("Open ROM…")
        open_btn.clicked.connect(self._on_open_main)
        btn_row.addWidget(open_btn)
        btn_row.addStretch()
        recent_lbl = QLabel("RECENT")
        recent_lbl.setStyleSheet(f"color:{FG_DIM};font-size:10px;letter-spacing:1px;")
        self._welcome_recent = QListWidget()
        self._welcome_recent.setStyleSheet(
            f"QListWidget{{background:{BG2};border:1px solid {BORDER};font-size:11px;}}"
            f"QListWidget::item{{padding:4px 8px;}}"
            f"QListWidget::item:selected{{background:{ACCENT}30;color:{FG};}}")
        self._welcome_recent.setMaximumHeight(170)
        self._welcome_recent.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._welcome_recent.setTextElideMode(Qt.ElideMiddle)
        self._welcome_recent.itemActivated.connect(
            lambda it: self._load_main(Path(it.data(Qt.UserRole))))
        lay.addWidget(title)
        lay.addWidget(sub)
        lay.addSpacing(6)
        lay.addWidget(hint)
        lay.addLayout(btn_row)
        lay.addSpacing(10)
        lay.addWidget(recent_lbl)
        lay.addWidget(self._welcome_recent)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(box)
        row.addStretch()
        outer.addLayout(row)
        outer.addStretch(3)
        self._refresh_welcome_recent()
        return w

    def _refresh_welcome_recent(self):
        if not hasattr(self, "_welcome_recent"):
            return
        from PyQt5.QtWidgets import QListWidgetItem
        self._welcome_recent.clear()
        for p in self._recent_files:
            pp = Path(p)
            it = QListWidgetItem(pp.name)
            it.setData(Qt.UserRole, str(pp))
            it.setToolTip(str(pp))
            self._welcome_recent.addItem(it)
        self._welcome_recent.setVisible(bool(self._recent_files))

    def _refresh_map_tree(self):
        """Rebuild the map navigator from the editor tabs' current map lists."""
        if self._det is None or self._det.variant is None:
            self._map_tree.clear()
            return
        main_maps = self._main_chip_tab._maps
        if self._boost_rom is not None and self._boost_tab._maps:
            boost_maps = self._boost_tab._maps
        else:
            boost_maps = [m for m in (self._det.variant.boost_maps or []) if m.rows > 1]
        self._map_tree.populate(main_maps, boost_maps, self._boost_rom is not None)
        self._sync_tree_selection()

    def _show_editor(self, chip: str = "main"):
        """Bring the Editor tab forward on the given chip page."""
        self._center_tabs.setCurrentIndex(0)
        self._editor_stack.setCurrentIndex(1 if chip == "main" else 2)
        self._sync_tree_selection()

    def _sync_tree_selection(self):
        if not hasattr(self, "_map_tree"):
            return
        page = self._editor_stack.currentIndex()
        if page == 1:
            i = self._main_chip_tab.current_index()
            if i >= 0:
                self._map_tree.select("main", i)
        elif page == 2:
            i = self._boost_tab.current_index()
            if i >= 0:
                self._map_tree.select("boost", i)

    def _on_tree_map_activated(self, chip: str, idx: int):
        self._center_tabs.setCurrentIndex(0)
        if chip == "main":
            self._editor_stack.setCurrentIndex(1)
            tab = self._main_chip_tab
        else:
            self._editor_stack.setCurrentIndex(2)
            tab = self._boost_tab
        if 0 <= idx < tab._map_combo.count():
            if tab._map_combo.currentIndex() == idx:
                tab._on_map_selected(idx)      # re-select same map (e.g. after grid)
            else:
                tab._map_combo.setCurrentIndex(idx)

    def _show_main_map_by_variant_index(self, main_map_idx: int):
        """Overview inventory jump: index into variant.main_maps → editor."""
        if main_map_idx < 0 or self._det is None or self._det.variant is None:
            return
        try:
            m = self._det.variant.main_maps[main_map_idx]
        except IndexError:
            return
        tab = self._main_chip_tab
        for i, mm in enumerate(tab._maps):
            if mm is m:
                self._on_tree_map_activated("main", i)
                return

    def _run_health_scan(self):
        """Run tuning checks and publish them to the Health inspector panel."""
        if self._main_rom is None or self._det is None or not self._det.variant:
            self._health_panel.clear()
            return
        from urrom.tuning_checks import run_all_checks
        boost = bytes(self._boost_rom) if self._boost_rom else None
        issues = run_all_checks(bytes(self._main_rom), self._det.variant,
                                boost_rom=boost, crc32=self._det.crc32)
        self._health_panel.set_issues(issues, self._det.variant.name)
        n_err  = sum(1 for i in issues if i.severity == "error")
        n_warn = sum(1 for i in issues if i.severity == "warning")
        label = (f"Health ✗{n_err}" if n_err else
                 f"Health ⚠{n_warn}" if n_warn else "Health ✓")
        self._inspector.setTabText(2, label)

    def _on_health_issue(self, iss):
        """Jump to the cell an issue refers to."""
        if not iss or not iss.cell or iss.cell[1] is None:
            return
        tab = self._main_chip_tab
        try:
            map_idx = next(i for i, m in enumerate(tab._maps) if m.name == iss.map_name)
        except StopIteration:
            return
        self._on_tree_map_activated("main", map_idx)
        try:
            r, c = iss.cell
            disp_r = tab._table._map_def.rows - 1 - r
            tab._table.scrollToItem(tab._table.item(disp_r, c))
            tab._table.setCurrentCell(disp_r, c)
        except Exception:
            pass

    def _reset_layout(self):
        """Restore the default dock arrangement."""
        for d in (self._maps_dock, self._inspector_dock):
            d.setFloating(False)
            d.show()
        self.addDockWidget(Qt.LeftDockWidgetArea,  self._maps_dock)
        self.addDockWidget(Qt.RightDockWidgetArea, self._inspector_dock)
        self.resizeDocks([self._maps_dock, self._inspector_dock], [250, 400], Qt.Horizontal)

    def _build_menu(self):
        mb = self.menuBar()
        mb.setStyleSheet(
            f"QMenuBar {{ background: {BG2}; color: {FG}; }}"
            f"QMenuBar::item:selected {{ background: {BG3}; }}"
            f"QMenu {{ background: {BG2}; color: {FG}; border: 1px solid {BORDER}; }}"
            f"QMenu::item:selected {{ background: {BG3}; }}")

        # ── File ─────────────────────────────────────────────────────────────
        file_menu = mb.addMenu("File")

        open_act = QAction("Open ROM…", self)
        open_act.setShortcut("Ctrl+O")
        open_act.triggered.connect(self._on_open_main)
        file_menu.addAction(open_act)

        boost_act = QAction("Open Boost Chip…", self)
        boost_act.triggered.connect(self._on_open_boost)
        file_menu.addAction(boost_act)

        file_menu.addSeparator()

        save_act = QAction("Save ROM…", self)
        save_act.setShortcut("Ctrl+S")
        save_act.triggered.connect(self._on_save)
        find_act = QAction("Find in maps… Ctrl+F", self)
        find_act.setShortcut("Ctrl+F")
        find_act.triggered.connect(self._on_find_in_maps)
        file_menu.addAction(save_act)

        save_boost_act = QAction("Save Boost Chip…", self)
        save_boost_act.triggered.connect(self._on_save_boost)
        file_menu.addAction(save_boost_act)

        # 27C512s are what you can still buy: fill the 64KB with the native
        # image repeated (32KB ×2, 8KB ×8) so the chip works in the old socket.
        self._act_27c512 = QAction("Write 27C512 images (fill 64KB)", self)
        self._act_27c512.setCheckable(True)
        self._act_27c512.setChecked(
            str(self._settings.value("save_as_27c512", "false")).lower() in ("true", "1"))
        self._act_27c512.setStatusTip(
            "Save every chip as a 64KB 27C512 image: the native image is repeated to fill "
            "the chip (8KB boost ×8, 32KB 3B/RR ×2). 551 main chips are already 64KB.")
        self._act_27c512.toggled.connect(
            lambda v: self._settings.setValue("save_as_27c512", "true" if v else "false"))
        file_menu.addAction(self._act_27c512)

        file_menu.addSeparator()

        quit_act = QAction("Quit\t", self)
        quit_act.setShortcut("Ctrl+Q")
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

        # ── Tools ─────────────────────────────────────────────────────────────
        tools_menu = mb.addMenu("Tools")

        self._act_kwp_status = QAction("KWPBridge: not running", self)
        self._act_kwp_status.setEnabled(False)
        tools_menu.addAction(self._act_kwp_status)

        tools_menu.addSeparator()

        kwp_dlg_act = QAction("Live Data Connection…", self)
        kwp_dlg_act.setShortcut("Ctrl+K")
        kwp_dlg_act.triggered.connect(self._show_kwp_dialog)
        tools_menu.addAction(kwp_dlg_act)

        act_dash = QAction("Dashboard…", self)
        act_dash.setShortcut("Ctrl+D")
        act_dash.triggered.connect(self._toggle_dashboard)
        tools_menu.addAction(act_dash)

        tools_menu.addSeparator()

        fpr_act = QAction("Fuel pressure calculator…", self)
        fpr_act.triggered.connect(self._on_fpr_calculator)
        tools_menu.addAction(fpr_act)

        tools_menu.addSeparator()
        sess_act = QAction("Session log…", self)
        sess_act.setShortcut("Ctrl+L")
        sess_act.triggered.connect(self._on_session_log_dialog)
        tools_menu.addAction(sess_act)
        tools_menu.addSeparator()
        self._bench_act = QAction("Bench mode (simulated engine)", self)
        self._bench_act.setCheckable(True)
        self._bench_act.setToolTip("Run KWPBridge's mock ECU for the loaded chip in-process so the live "
                                   "cursor and trace walk the maps without a car")
        self._bench_act.toggled.connect(self._on_toggle_bench)
        tools_menu.addAction(self._bench_act)
        tools_menu.addSeparator()
        self._rec_act = QAction("Start recording live data…", self)
        self._rec_act.triggered.connect(self._on_toggle_recording)
        tools_menu.addAction(self._rec_act)
        self._trace_act = QAction("Live trace on current map", self)
        self._trace_act.setCheckable(True)
        self._trace_act.toggled.connect(self._on_toggle_trace)
        tools_menu.addAction(self._trace_act)
        clr_act = QAction("Clear live trace", self)
        clr_act.triggered.connect(self._on_clear_trace)
        tools_menu.addAction(clr_act)
        tools_menu.addSeparator()

        log_act = QAction("Overlay data log on map…", self)
        log_act.triggered.connect(self._on_overlay_datalog)
        tools_menu.addAction(log_act)

        wb_act = QAction("Overlay wideband AFR on fuel map…", self)
        wb_act.triggered.connect(self._on_overlay_wideband)
        tools_menu.addAction(wb_act)

        # 2-second timer keeps the menu label current
        self._kwp_menu_timer = QTimer(self)
        self._kwp_menu_timer.timeout.connect(self._refresh_kwp_menu_label)
        self._kwp_menu_timer.start(2000)

        # ── View ─────────────────────────────────────────────────────────────
        view_menu = mb.addMenu("View")
        maps_act = self._maps_dock.toggleViewAction()
        maps_act.setText("Maps panel")
        maps_act.setShortcut("Ctrl+1")
        view_menu.addAction(maps_act)
        insp_act = self._inspector_dock.toggleViewAction()
        insp_act.setText("Inspector panel")
        insp_act.setShortcut("Ctrl+2")
        view_menu.addAction(insp_act)
        view_menu.addSeparator()
        filt_act = QAction("Filter maps…", self)
        filt_act.setShortcut("Ctrl+L")
        filt_act.triggered.connect(
            lambda: (self._maps_dock.show(), self._map_tree.focus_search()))
        view_menu.addAction(filt_act)
        editor_act = QAction("Editor", self)
        editor_act.setShortcut("Ctrl+E")
        editor_act.triggered.connect(lambda: self._center_tabs.setCurrentIndex(0))
        view_menu.addAction(editor_act)
        cmp_act = QAction("Compare", self)
        cmp_act.setShortcut("Ctrl+Shift+C")
        cmp_act.triggered.connect(lambda: self._center_tabs.setCurrentIndex(1))
        view_menu.addAction(cmp_act)
        view_menu.addSeparator()
        reset_act = QAction("Reset layout", self)
        reset_act.triggered.connect(self._reset_layout)
        view_menu.addAction(reset_act)

        # ── Help ─────────────────────────────────────────────────────────────
        help_menu = mb.addMenu("Help")
        shortcuts_act = QAction("Keyboard shortcuts…", self)
        shortcuts_act.triggered.connect(self._on_show_shortcuts)
        help_menu.addAction(shortcuts_act)
        help_menu.addSeparator()
        about_act = QAction("About UrROM", self)
        about_act.triggered.connect(self._on_about)
        help_menu.addAction(about_act)

    def _refresh_kwp_menu_label(self):
        """Update Tools menu KWP label every 2 seconds."""
        if not hasattr(self, '_act_kwp_status'):
            return
        if not kwpbridge_available():
            self._act_kwp_status.setText("KWPBridge: not installed")
        elif kwpbridge_running():
            pn = self._kwp_monitor.current_pn()
            if pn:
                self._act_kwp_status.setText(f"KWPBridge: connected  ·  {pn}")
            else:
                self._act_kwp_status.setText("KWPBridge: running — no ECU")
        else:
            self._act_kwp_status.setText("KWPBridge: not running")

    def _show_kwp_dialog(self):
        """Show KWPBridge connection status and info dialog."""
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                                     QDialogButtonBox)
        dlg = QDialog(self)
        dlg.setWindowTitle("Live Data — KWPBridge Connection")
        dlg.setMinimumWidth(420)
        dlg.setStyleSheet(f"background:{BG2}; color:{FG};")
        lay = QVBoxLayout(dlg)
        lay.setSpacing(12)

        if not kwpbridge_available():
            dot, body = "⚫", (
                "<b>KWPBridge is not installed.</b><br><br>"
                "UrROM works fully standalone without it.<br><br>"
                "KWPBridge adds optional live ECU data overlay:<br>"
                "• Real-time RPM, load, coolant on the map tabs<br>"
                "• Lambda and timing overlaid on active map cells<br>"
                "• Part-number safety gate before writing<br><br>"
                "Install KWPBridge and run it alongside UrROM.")
        elif kwpbridge_running():
            pn = self._kwp_monitor.current_pn()
            det = self._det
            pns = det.variant.ecu_pns if det and det.variant else []
            if pn:
                matched = self._kwp_matched
                dot  = "🟢" if matched else "🟡"
                if matched:
                    body = (f"<b>KWPBridge connected.</b><br><br>"
                            f"ECU: <b>{pn}</b><br>"
                            "ECU matches loaded ROM — live overlay active.")
                else:
                    rom_str = ", ".join(pns) if pns else "(none loaded)"
                    body = (f"<b>KWPBridge connected.</b><br><br>"
                            f"ECU: <b>{pn}</b><br>"
                            f"ROM expects: <b>{rom_str}</b><br>"
                            "Load the matching ROM to enable overlay.")
            else:
                dot  = "🟡"
                body = ("<b>KWPBridge running — no ECU detected.</b><br><br>"
                        "Connect your KL-line interface and turn ignition on.")
        else:
            dot  = "🔴"
            body = ("<b>KWPBridge is installed but not running.</b><br><br>"
                    "UrROM is fully operational without it.<br><br>"
                    "Start KWPBridge to enable live ECU data overlay.<br>"
                    "It will be detected automatically within 2 seconds.")

        icon = QLabel(dot)
        icon.setStyleSheet("font-size: 28px;")
        msg = QLabel(body)
        msg.setWordWrap(True)

        row = QHBoxLayout()
        row.addWidget(icon)
        row.addWidget(msg, 1)
        w = QWidget()
        w.setLayout(row)
        lay.addWidget(w)

        bb = QDialogButtonBox(QDialogButtonBox.Ok)
        bb.accepted.connect(dlg.accept)
        lay.addWidget(bb)
        dlg.exec_()

    # ── File operations ───────────────────────────────────────────────────────

    def _on_open_main(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open ROM file", "",
            "ROM files (*.bin *.BIN *.034 *.rom);;All files (*.*)")
        if not path:
            return
        self._load_main(Path(path))

    def _load_main(self, path: Path):
        try:
            raw = path.read_bytes()
        except OSError as e:
            QMessageBox.critical(self, "Error", f"Cannot read file:\n{e}")
            return

        # Descramble .034 Rip Chip files before normalising
        if path.suffix.lower() == ".034":
            from urrom.descramble import descramble_034, is_valid_034
            if not is_valid_034(raw):
                QMessageBox.warning(
                    self, "Invalid .034 file",
                    f"{path.name} does not appear to be a valid .034 Rip Chip file.\n\n"
                    "The file may be a Wayback Machine HTML page or a corrupt dump.\n"
                    "Expected: 65536-byte scrambled ROM binary.")
                return
            raw = descramble_034(raw)

        if len(raw) == MAIN_CHIP_PHYSICAL - 1:
            raw = bytes(raw) + b"\xff"      # short dump — keep the split-bank logic intact
        wh, notes = normalize_rom(raw)
        det = detect_rom(wh)

        self._main_path = path
        self._main_rom  = bytearray(wh)
        # Keep the whole original image: 551x chips are split-bank (firmware in
        # the lower 32KB) and Save must write that half back untouched.
        self._main_full = bytes(raw) if len(raw) >= MAIN_CHIP_PHYSICAL else None
        # Firmware half: lower 32KB of a split-bank 551 image; for 32KB flat
        # chips (404) the whole file is both firmware and calibration.
        if self._main_full is not None:
            self._main_fw = bytearray(self._main_full[:MAIN_CHIP_WORKING])
        elif det.variant is not None and det.variant.working_half_offset == 0:
            self._main_fw = bytearray(wh)
        else:
            self._main_fw = None
        self._det       = det
        self._unsaved   = False
        self._clear_dirty()

        # Update UI
        fname = path.name
        self._main_lbl.setText(fname)
        self._main_lbl.setStyleSheet(f"color: {FG}; font-size: 11px;")
        # Reset session log for new ROM
        from urrom.session_log import SessionLog
        v_name = det.variant.name if det and det.variant else ""
        self._session_log = SessionLog(rom_name=fname, variant_name=v_name)
        # Update window title to show ROM name
        from urrom.version import APP_VERSION, APP_NAME
        self._refresh_title()
        self._add_recent(path)
        self._info_strip.update(det)
        ov = self._overview_tab
        ov._rom_bytes = bytes(wh)
        ov.update(det, self._boost_det if hasattr(self, "_boost_det") else None)
        self._hardware_tab.update(
            bytes(self._main_fw) if self._main_fw is not None else bytes(wh),
            det.variant.software_id if det.variant else "")
        # Wire LC/NLS scalar edits -> dirty flag + ROM write-back
        def _on_scalar_changed(wh_off, raw):
            if wh_off == '__dist_patch__':
                # Distributor timing offset — shift all ign maps
                deg = raw   # raw is actually a float degree value here
                self._apply_dist_timing_patch(deg)
                return
            target = self._main_fw if self._main_fw is not None else self._main_rom
            if target is not None and isinstance(wh_off, int) and wh_off < len(target):
                target[wh_off] = raw
                self._set_dirty()
        self._hardware_tab.set_scalar_changed_callback(_on_scalar_changed)

        # Wire firmware patch apply/revert callback
        def _on_patch_toggle(patch_name, action, value):
            fw = self._main_fw
            sw_now = self._det.variant.software_id if self._det and self._det.variant else ""
            from urrom.hw_patches import is_404_family
            if fw is None and is_404_family(sw_now) and self._main_rom is not None:
                # 32 KB flat chip: firmware and calibration are the same bytes
                fw = self._main_rom
            if fw is None:
                QMessageBox.warning(
                    self, "No firmware half",
                    "Firmware patches live in the lower 32KB of the 64KB chip image.\n"
                    "This ROM was opened as a bare calibration half, so there is no\n"
                    "firmware to patch. Open the full 64KB chip read.")
                return
            if action == "apply":
                if patch_name == "MFTS Boost Cut Bypass":
                    apply_mfts_bypass(fw)
                elif patch_name == "Load Overflow Decap":
                    apply_load_decap(fw)
                elif patch_name == "Lambda Cold-Start Delay":
                    apply_lambda_delay(fw, value if value is not None else 0xFF)
                elif patch_name == "3B Spark-Cut Launch Control":
                    from urrom.hw_patches import apply_lc3b
                    r = QMessageBox.warning(
                        self, "Spark-cut launch control",
                        "This installs vwnut8392's hard spark-cut launch control (S&M Msport "
                        "V1.01) into the firmware.\n\nIt needs ECU pin 38 cut and wired to a "
                        "clutch switch, and it can damage the engine, turbo or exhaust if "
                        "abused. Off-road use only.\n\nInstall it?",
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                    if r != QMessageBox.Yes:
                        return
                    apply_lc3b(fw)
            elif action == "revert":
                if patch_name == "MFTS Boost Cut Bypass":
                    revert_mfts_bypass(fw)
                elif patch_name == "Load Overflow Decap":
                    revert_load_decap(fw)
                elif patch_name == "Lambda Cold-Start Delay":
                    revert_lambda_delay(fw)
                elif patch_name == "3B Spark-Cut Launch Control":
                    from urrom.hw_patches import revert_lc3b
                    revert_lc3b(fw)
            self._set_dirty()
            # Refresh hardware tab with the updated firmware bytes
            sw = self._det.variant.software_id if self._det and self._det.variant else ""
            self._hardware_tab.update(bytes(fw), sw)
        self._hardware_tab.set_patch_apply_callback(_on_patch_toggle)

        self._hardware_tab.set_dist_maps(
            self._det.variant.main_maps if self._det and self._det.variant else [])
        self._hardware_tab.set_injector_callback(self._on_injector_scaling)
        self._main_chip_tab._on_map_changed_cb = (
            lambda: self._refresh_trace() if self._trace_act.isChecked() else None)
        self._save_btn.setEnabled(True)

        if det.variant:
            # Auto-inject XDF maps for 551B/C if the community XDF is available
            self._try_auto_xdf(det.variant)
            self._main_chip_tab.load(self._main_rom, det.variant)
            self._compare_tab.set_rom_a(bytes(self._main_rom), det.variant)
            self._refresh_map_tree()
            self._show_editor("main")
            self._run_health_scan()
            # Tell KWP monitor which PNs are valid for this variant
            self._kwp_monitor.set_rom_part_numbers(det.variant.ecu_pns)
            self._refresh_kwp_badge()

            # Warn on variants with unconfirmed map addresses
            v_name = det.variant.name or ""
            if "V8" in v_name or "ABH" in v_name or "PT" in v_name:
                QMessageBox.warning(
                    self, "V8 ROM — unconfirmed map addresses",
                    "V8 map addresses have not been verified against real hardware.\n\n"
                    "You can view the ROM in hex and compare files, but do not\n"
                    "write map values until the addresses are confirmed.\n\n"
                    "If you have a V8 chip read, please contribute it — "
                    "it would be the first confirmed V8 ROM in this tool.")
        else:
            self._main_chip_tab.clear()
            self._compare_tab.clear()
            self._kwp_monitor.set_rom_part_numbers([])
            self._map_tree.clear()
            self._health_panel.clear()
            self._editor_stack.setCurrentIndex(0)
            self._inspector.setCurrentIndex(0)
            QMessageBox.warning(
                self, "Unknown ROM",
                f"ROM not recognised.\n\n"
                f"CRC32: 0x{det.crc32:08X}  Build: 0x{det.build_number:04X}\n\n"
                f"Map editing is disabled for unknown variants.")

        note_text = "; ".join(notes) if notes else ""
        self._update_status(
            f"Loaded {fname}  —  {det.method}"
            + (f"  [{note_text}]" if note_text else ""))

    def _on_open_boost(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open boost chip file", "",
            "ROM files (*.bin *.BIN *.034 *.rom);;All files (*.*)")
        if not path:
            return
        self._load_boost(Path(path))

    def _load_boost(self, path: Path):
        if self._det is None or self._det.variant is None:
             QMessageBox.warning(self, "No main chip",
                                 "Load the main chip ROM first.")
             return
        try:
             raw = path.read_bytes()
        except OSError as e:
             QMessageBox.critical(self, "Error", f"Cannot read file:\n{e}")
             return

        # Normalise: a boost chip burned onto a bigger EPROM (27C64 image ×8 or
        # 27C256 image ×2 on a 27C512) is N identical copies — fold it back.
        # 551x boost chips are 32KB (27C256); 3B/RR/S2 and 551A are 8KB (27C64).
        from urrom.ecu_profiles import fold_repeated_image
        folded, copies, fold_notes = fold_repeated_image(raw)
        if copies > 1:
             boost_raw = bytearray(folded)
             self._update_status(fold_notes[0])
        elif len(raw) in (8192, 16384, 32768):
             boost_raw = bytearray(raw)
        elif len(raw) == 65536:
             boost_raw = bytearray(raw[0x8000:])  # non-identical halves: assume upper
        elif len(raw) < 8192:
             boost_raw = bytearray(raw)
        else:
             boost_raw = bytearray(raw[:32768])
        self._boost_native_size = len(boost_raw)

        boost_det = detect_rom(bytes(boost_raw))
        self._boost_det  = boost_det
        self._boost_path = path
        self._boost_rom  = boost_raw
        self._boost_lbl.setText(path.name)
        self._boost_lbl.setStyleSheet(f"color: {FG}; font-size: 11px;")

        suggested = None
        if self._det.variant.software_id.startswith(("551", "557")):
            from urrom.hw_patches import detect_map_sensor_from_boost
            stype, _, _ = detect_map_sensor_from_boost(bytes(boost_raw))
            suggested = {"250kPa_MPX4250": "mpx4250", "300kPa_MPX4300": "lin300",
                         "400kPa_MPXH6400A": "mpxh6400"}.get(stype)
            # The fuel chip's catalogue entry is the better witness: every 034EFI
            # Rip Chip tune requires a 3.0 BAR MAP, prjmod SD a 400 kPa MPXH6400A.
            from urrom.ecu_profiles import KNOWN_CRCS
            label = KNOWN_CRCS.get(self._det.crc32, ("", ""))[1].upper()
            if "3.0 BAR" in label or "300 KPA" in label or "300KPA" in label:
                suggested = "lin300"
            elif "MPXH6400" in label or "400 KPA" in label:
                suggested = "mpxh6400"
        self._boost_tab.load(boost_raw, self._det.variant, suggested)
        self._hardware_tab.set_boost(bytes(raw), path.name)
        self._overview_tab.update(self._det, boost_det)
        self._refresh_map_tree()
        if self._boost_tab._maps:
            self._show_editor("boost")
        self._run_health_scan()

        bld = boost_det.build_number if boost_det else 0
        crc = boost_det.crc32 if boost_det else 0
        status_msg = f"Boost chip: {path.name}  CRC32 0x{crc:08X}  build 0x{bld:04X}"

        # Check fuel+boost pairing
        if self._det and boost_det:
            from urrom.ecu_profiles import check_chip_pair
            pair_status, pair_msg = check_chip_pair(self._det.crc32, boost_det.crc32)
            if pair_status == 'mismatch':
                QMessageBox.warning(self, "Boost Chip Mismatch", pair_msg)
                status_msg += "  ⚠ MISMATCH"
            elif pair_status == 'ok' and 'confirmed' in pair_msg.lower():
                status_msg += "  ✓ paired"
        self._update_status(status_msg)

    def _on_save(self):
        if self._main_rom is None:
            return

        rom_out = bytearray(self._main_rom)
        rom_out = self._main_chip_tab.commit_to_rom(rom_out)

        variant = self._det.variant if self._det else None
        sw_id = getattr(variant, "software_id", "") if variant else ""
        from urrom.ecu_profiles import checksum_kind, apply_checksum_for
        kind = checksum_kind(variant)
        if kind == "551":
            old_cs = bytes(rom_out[0x3FFA:0x3FFE])
            rom_out = apply_checksum_for(rom_out, variant)
            cs_changed = bytes(rom_out[0x3FFA:0x3FFE]) != old_cs
        elif kind == "404":
            # firmware patches on a flat 404 chip live in the same 32 KB
            if getattr(self, "_main_fw", None) is not None and len(self._main_fw) == len(rom_out):
                rom_out = bytearray(self._main_fw)
                rom_out = self._main_chip_tab.commit_to_rom(rom_out)
            old_cs = bytes(rom_out[0x7F00:0x7F02])
            rom_out = apply_checksum_for(rom_out, variant)
            cs_changed = bytes(rom_out[0x7F00:0x7F02]) != old_cs
        else:
            cs_changed = False

        from urrom.ecu_profiles import assemble_output
        out_bytes, asm_notes = assemble_output(
            bytes(rom_out), variant, getattr(self, "_main_full", None),
            firmware=bytes(self._main_fw) if getattr(self, "_main_fw", None) is not None else None)
        if any(n.startswith("WARNING") for n in asm_notes):
            r = QMessageBox.warning(
                self, "No firmware half",
                "This ROM was opened as a bare 32KB calibration half, so UrROM has no "
                "firmware to put in the lower 32KB of the 64KB output.\n\n"
                "The file will be written with the calibration in BOTH halves. It is "
                "fine for comparing and archiving but must NOT be burned to a chip.\n\n"
                "Open the full 64KB chip read instead to get a burnable file.",
                QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Cancel)
            if r != QMessageBox.Ok:
                return

        fill_512 = self._save_as_27c512()
        stem   = self._main_path.stem
        suffix = self._main_path.suffix.lower()
        tag = "_edited" + ("_27c512" if fill_512 and len(out_bytes) < MAIN_CHIP_PHYSICAL else "")
        default_name = stem + tag + (suffix if suffix in (".bin", ".034") else ".bin")
        path, sel_filter = QFileDialog.getSaveFileName(
            self, "Save ROM", default_name,
            "Binary ROM (*.bin *.BIN);;034 Rip Chip (*.034);;All files (*.*)")
        if not path:
            return

        if path.lower().endswith(".034") or "034" in sel_filter:
            from urrom.descramble import scramble_034
            out_bytes = bytes(scramble_034(out_bytes))
        elif fill_512 and len(out_bytes) < MAIN_CHIP_PHYSICAL:
            from urrom.ecu_profiles import expand_to_chip
            try:
                out_bytes = expand_to_chip(out_bytes, "27C512")
                asm_notes.append(f"27C512 image ({MAIN_CHIP_PHYSICAL // len(rom_out)}× native)")
            except ValueError as e:
                QMessageBox.warning(self, "27C512 image", str(e))
                return

        try:
            Path(path).write_bytes(out_bytes)
        except OSError as e:
            QMessageBox.critical(self, "Error", f"Cannot save:\n{e}")
            return

        notes = []
        notes.extend(n for n in asm_notes if not n.startswith("WARNING"))
        if cs_changed:     notes.append("checksum updated")
        elif kind:         notes.append("checksum ok")
        if path.lower().endswith(".034"): notes.append(".034 scrambled")

        # Save boost chip if it has edits
        if (hasattr(self, '_boost_tab') and self._boost_tab.has_changes()):
            boost_stem = Path(path).stem.replace("_edited", "").replace("_27c512", "") + "_boost_edited"
            if self._write_boost_chip(boost_stem):
                notes.append("boost chip saved")

        note_str = f"  ({', '.join(notes)})" if notes else ""
        self._main_chip_tab._table.accept_current_as_baseline()
        self._unsaved = False
        self._clear_dirty()
        # Record save snapshot in session log
        self._save_count += 1
        if self._session_log.count > 0:
            snap_note = (f"Save #{self._save_count}: "
                        f"{self._session_log.count} edits in "
                        f"{len(self._session_log.changed_maps)} maps")
            self._save_snapshots.append({
                'save': self._save_count,
                'path': str(path),
                'edits': self._session_log.count,
                'maps': list(self._session_log.changed_maps),
            })
        self._update_status(f"Saved → {Path(path).name}  ({len(out_bytes):,} bytes){note_str}")

    def _save_as_27c512(self) -> bool:
        act = getattr(self, "_act_27c512", None)
        return bool(act.isChecked()) if act is not None else False

    def _write_boost_chip(self, default_stem: str) -> bool:
        """Ask for a path and write the boost chip (optionally as a 27C512 image)."""
        boost_rom = self._boost_tab.get_boost_rom() if hasattr(self, "_boost_tab") else None
        if boost_rom is None:
            QMessageBox.information(self, "Save Boost Chip", "No boost chip loaded.")
            return False
        out = bytes(boost_rom)
        note = ""
        if self._save_as_27c512() and len(out) < MAIN_CHIP_PHYSICAL:
            from urrom.ecu_profiles import expand_to_chip, chip_name_for_size
            try:
                native = chip_name_for_size(len(out))
                out = expand_to_chip(out, "27C512")
                default_stem += "_27c512"
                note = f"  ({native} image ×{MAIN_CHIP_PHYSICAL // len(boost_rom)} → 27C512)"
            except ValueError as e:
                QMessageBox.warning(self, "27C512 image", str(e))
                return False
        boost_path, _ = QFileDialog.getSaveFileName(
            self, "Save Boost Chip", default_stem + ".bin",
            "Binary ROM (*.bin *.BIN);;All files (*.*)")
        if not boost_path:
            return False
        try:
            Path(boost_path).write_bytes(out)
        except OSError as e:
            QMessageBox.warning(self, "Boost Save Error", str(e))
            return False
        self._update_status(f"Boost chip saved → {Path(boost_path).name}  ({len(out):,} bytes){note}")
        return True

    def _on_save_boost(self):
        stem = (self._boost_path.stem if getattr(self, "_boost_path", None) else "boost") + "_edited"
        self._write_boost_chip(stem)

    # ── KWPBridge overlay ──────────────────────────────────────────────────────

    def _on_injector_scaling(self):
        """Guided injector scaling wizard — rescales all fuel maps proportionally."""
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
                                      QLabel, QSpinBox, QDoubleSpinBox, QCheckBox,
                                      QDialogButtonBox, QFrame)

        dlg = QDialog(self)
        dlg.setWindowTitle("Injector scaling wizard")
        dlg.setMinimumWidth(420)
        dlg.setStyleSheet(f"background:{BG};color:{FG};")
        lay = QVBoxLayout(dlg)
        lay.setSpacing(10)

        # Info
        info = QLabel(
            "Rescales all fuel map values when changing injector size.\n"
            "Formula: new_raw = old_raw x (stock_cc / new_cc)\n\n"
            "Common injector sizes:\n"
            "  Stock ABY/AAN: 293 cc/min\n"
            "  RS2 / 034 Stage 1: 440 cc/min (Bosch) or 550 cc/min\n"
            "  034 Stage 1 EV14: 550 cc/min\n"
            "  034 Stage 1 440cc Siemens: 440 cc/min")
        info.setStyleSheet(f"color:{FG_DIM};font-size:11px;background:{BG2};"
                           f"padding:8px;border-radius:4px;")
        info.setWordWrap(True)
        lay.addWidget(info)

        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color:{BORDER};"); lay.addWidget(sep)

        form = QFormLayout()
        form.setSpacing(8)

        stock_spin = QSpinBox()
        stock_spin.setRange(100, 2000); stock_spin.setValue(293)
        stock_spin.setSuffix(" cc/min")
        stock_spin.setStyleSheet(f"background:{BG2};color:{FG};border:1px solid {BORDER};border-radius:3px;padding:2px 4px;")

        new_spin = QSpinBox()
        new_spin.setRange(100, 2000); new_spin.setValue(440)
        new_spin.setSuffix(" cc/min")
        new_spin.setStyleSheet(f"background:{BG2};color:{FG};border:1px solid {BORDER};border-radius:3px;padding:2px 4px;")

        factor_lbl = QLabel("Scale factor: 0.666×")
        factor_lbl.setStyleSheet(f"color:{ACCENT};font-weight:bold;")

        def _update_factor():
            s, n = stock_spin.value(), new_spin.value()
            if n > 0:
                f = s / n
                factor_lbl.setText(f"Scale factor: {f:.4f}×  ({f*100:.1f}% of original)")

        stock_spin.valueChanged.connect(_update_factor)
        new_spin.valueChanged.connect(_update_factor)
        _update_factor()

        # FPR scaling option
        fpr_check = QCheckBox("Also adjust for different fuel pressure")
        fpr_check.setStyleSheet(f"color:{FG};")
        fpr_frame = QFrame()
        fpr_lay = QHBoxLayout(fpr_frame)
        fpr_lay.setContentsMargins(20, 0, 0, 0)
        stock_fpr = QDoubleSpinBox(); stock_fpr.setRange(1.0, 10.0); stock_fpr.setValue(3.0)
        stock_fpr.setSuffix(" bar stock")
        new_fpr = QDoubleSpinBox(); new_fpr.setRange(1.0, 10.0); new_fpr.setValue(5.0)
        new_fpr.setSuffix(" bar new")
        for w in (stock_fpr, new_fpr):
            w.setStyleSheet(f"background:{BG2};color:{FG};border:1px solid {BORDER};border-radius:3px;padding:2px 4px;")
            fpr_lay.addWidget(w)
        fpr_frame.setEnabled(False)
        fpr_check.toggled.connect(fpr_frame.setEnabled)
        fpr_check.toggled.connect(_update_factor)

        form.addRow("Stock injector size:", stock_spin)
        form.addRow("New injector size:", new_spin)
        form.addRow("", factor_lbl)
        form.addRow("", fpr_check)
        form.addRow("", fpr_frame)
        lay.addLayout(form)

        warn = QLabel("\u26a0 This modifies ALL confirmed fuel maps in the ROM.\n"
                      "Use Revert or Undo (Ctrl+Z) to roll back.")
        warn.setStyleSheet(f"color:{AMBER};font-size:10px;")
        warn.setWordWrap(True)
        lay.addWidget(warn)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)

        def _apply():
            s = stock_spin.value(); n = new_spin.value()
            if n == 0: return
            factor = s / n
            if fpr_check.isChecked() and stock_fpr.value() > 0:
                # Flow scales as sqrt(pressure ratio)
                import math
                factor *= math.sqrt(new_fpr.value() / stock_fpr.value())
            # Apply to all confirmed fuel maps
            from urrom.ecu_profiles import read_map, write_map
            v = self._det.variant if self._det else None
            if not v: return
            fuel_maps = [m for m in v.main_maps
                         if m.map_type == 'fuel' and m.confidence == 'CONFIRMED' and m.rows > 1]
            for m in fuel_maps:
                data = read_map(bytes(self._main_rom), m)
                scaled = [[max(0, min(255, round(data[r][c] * factor)))
                           for c in range(m.cols)] for r in range(m.rows)]
                self._main_rom = bytearray(write_map(bytes(self._main_rom), m, scaled))
            # Reload the map editor
            self._main_chip_tab.load(self._main_rom, v)
            self._compare_tab.set_rom_a(bytes(self._main_rom), v)
            self._set_dirty()
            n_maps = len(fuel_maps)
            self._update_status(
                f"Injector scaling applied: {s}cc → {n}cc  (×{factor:.4f}) "
                f"across {n_maps} fuel maps")
            dlg.accept()

        btns.accepted.connect(_apply)
        dlg.exec_()

    def _on_scan_issues(self):
        """Run automated tuning health checks and show them in the inspector."""
        if self._main_rom is None or self._det is None or not self._det.variant:
            QMessageBox.information(self, "Scan for issues", "Load a ROM first.")
            return
        self._run_health_scan()
        self._inspector_dock.show()
        self._inspector.setCurrentWidget(self._health_panel)

    def _try_auto_xdf(self, variant) -> None:
        """
        Silently inject XDF maps for known variants if the community XDF is present.
        Runs transparently — no dialogs, no errors if XDF not found.
        """
        # This XDF uses prjmod firmware layout (64KB doubled ROM, 0x0202 firmware)
        # Only inject into 551AA_0202 (prjmod) variants — stock 551B/C have different map offsets
        XDF_MAP = {
            "551AA_0202": "rs2_xdf/m232.xdf",      # prj's own definition (GPL-3), see rs2_xdf/README.md
        }
        sw_id = variant.software_id
        xdf_rel = XDF_MAP.get(sw_id)
        if not xdf_rel:
            return
        # Look relative to app and cwd
        for base in [Path(__file__).parent.parent, Path(".")]:
            xdf_path = base / xdf_rel
            if xdf_path.exists():
                break
        else:
            return  # XDF not found — silently skip

        try:
            from urrom.xdf_import import parse_xdf, xdf_to_mapdefs
            result = parse_xdf(xdf_path)
            new_maps = xdf_to_mapdefs(result, filter_min_cells=16)
        except Exception:
            return

        if not new_maps:
            return

        existing_addrs = {m.main_addr for m in variant.main_maps + variant.boost_maps}
        added = [m for m in new_maps if m.main_addr not in existing_addrs]
        if added:
            variant.main_maps = variant.main_maps + added
            self._update_status(
                f"Auto-imported {len(added)} maps from {xdf_path.name} ({sw_id})")

    def _apply_dist_timing_patch(self, deg: float) -> None:
        """Apply distributor timing offset: shift all confirmed ign map cells by deg."""
        if self._main_rom is None or self._det is None or not self._det.variant:
            return
        v = self._det.variant
        ign_maps = [m for m in (v.main_maps or [])
                    if m.map_type == 'ign' and m.confidence == 'CONFIRMED'
                    and m.rows > 1 and m.decode and m.encode]
        if not ign_maps or deg == 0.0:
            return

        from urrom.ecu_profiles import read_map, write_map
        total_cells = 0
        for m in ign_maps:
            data = read_map(bytes(self._main_rom), m)
            new_data = []
            for r in range(m.rows):
                row = []
                for c in range(m.cols):
                    raw = data[r][c]
                    new_raw = max(0, min(255, m.encode(m.decode(raw) + deg)))
                    row.append(new_raw)
                    if raw != new_raw:
                        total_cells += 1
                new_data.append(row)
            self._main_rom = bytearray(write_map(bytes(self._main_rom), m, new_data))

        # Update offset label in hardware tab
        prev = self._hardware_tab._dist_patch_offset
        self._hardware_tab._dist_patch_offset = deg
        sign = "+" if deg >= 0 else ""
        self._hardware_tab._dist_offset_lbl.setText(
            f"Applied: {sign}{deg:.1f}°  ({total_cells} cells in {len(ign_maps)} maps)")
        self._hardware_tab._dist_offset_lbl.setStyleSheet(
            f"color:{'#c87020' if deg != 0 else FG_DIM};font-size:10px;font-weight:bold;")

        self._main_chip_tab.load(self._main_rom, v)
        self._compare_tab.set_rom_a(bytes(self._main_rom), v)
        self._set_dirty()
        sign2 = "+" if deg >= 0 else ""
        self._update_status(
            f"Distributor adapter: {sign2}{deg:.1f}° offset applied "
            f"({total_cells} ign cells across {len(ign_maps)} maps)")

    def _on_compare_to_stock(self):
        """Auto-load the matching stock ROM from roms/ and open Compare tab."""
        if self._main_rom is None or self._det is None or not self._det.variant:
            QMessageBox.information(self, "Compare to stock", "Load a ROM first.")
            return
        import sys as _sys
        sw_id = self._det.variant.software_id
        # Map software IDs to bundled stock ROM files
        # Stock baseline ROMs bundled with UrROM
        # Confirmed real direct chip reads: ABY 551B and ADU 551C only
        # 551A and 551AA chips are factory-erased (blank calibration)
        # RS2 D02 (551B_D02) is partial (high-RPM area blank)
        STOCK_ROMS = {
            "551B":       "roms/aby_fuel-ign_551aa.bin",    # ABY S2 Coupe — REAL ✓
            "551C":       "roms/adu_fuel-ign_551c.bin",      # ADU RS2 Avant — REAL ✓
            "551AA":      None,                               # BLANK — no usable baseline
            "551A":       None,                               # BLANK — no usable baseline
            "551AA_0202": "roms/aan_fuel-ign_551aa.bin",    # PRJmod — compare to prjmod base
            "551B_D02":   "roms/rs2_d02_fuel-ign_551b.bin", # RS2 D02 — PARTIAL baseline
            "404":        "roms/3b_fuel-ign_404aa.bin",      # 3B 200 20vT — REAL ✓
            # Note: rr_fuel-ign_404b.bin is now in roms/ (was mislabelled as rr_boost)
        }
        # Find the app base directory
        app_base = Path(__file__).parent.parent
        stock_rel = STOCK_ROMS.get(sw_id)
        if stock_rel is None and sw_id in STOCK_ROMS:
            # Explicitly mapped to None = blank chip, no calibration baseline
            QMessageBox.information(self, "Compare to stock",
                f"No calibration baseline available for {sw_id}.\n"
                f"The {sw_id} chips in our collection have factory-erased "
                "calibration (all 0x02).\n"
                "Use the ADU 551C or ABY 551B as the nearest reference.")
            return
        if not stock_rel:
            QMessageBox.information(self, "Compare to stock",
                f"No bundled stock ROM for variant {sw_id}.\n"
                "Use File → Load ROM then Compare tab to compare manually.")
            return
        stock_path = app_base / stock_rel
        if not stock_path.exists():
            # Try relative to cwd
            stock_path = Path(stock_rel)
        if not stock_path.exists():
            QMessageBox.warning(self, "Stock ROM not found",
                f"Could not find: {stock_rel}\n"
                "Ensure you're running from the UrROM directory.")
            return
        try:
            stock_raw = stock_path.read_bytes()
        except OSError as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        from urrom.ecu_profiles import normalize_rom
        stock_wh, _ = normalize_rom(stock_raw)
        self._compare_tab.set_rom_b_direct(bytes(stock_wh), stock_path.name)
        self._center_tabs.setCurrentWidget(self._compare_tab)
        self._update_status(f"Comparing to stock: {stock_path.name}")

    def _on_find_in_maps(self):
        """Search all confirmed maps for cells matching a value condition."""
        if self._main_rom is None or self._det is None or not self._det.variant:
            QMessageBox.information(self, "Find in maps", "Load a ROM first.")
            return
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                                      QLabel, QComboBox, QDoubleSpinBox,
                                      QDialogButtonBox, QTableWidget,
                                      QTableWidgetItem, QHeaderView)
        dlg = QDialog(self)
        dlg.setWindowTitle("Find in maps")
        dlg.setMinimumWidth(560)
        dlg.setStyleSheet(f"background:{BG};color:{FG};")
        lay = QVBoxLayout(dlg)
        lay.setSpacing(8)

        # Query row
        q_row = QHBoxLayout()
        q_row.addWidget(QLabel("Find cells where decoded value"))
        op_cb = QComboBox()
        op_cb.addItems([">", "≥", "<", "≤", "=", "≠"])
        op_cb.setFixedWidth(50)
        val_sb = QDoubleSpinBox()
        val_sb.setRange(-999, 9999)
        val_sb.setDecimals(2)
        val_sb.setValue(0.0)
        val_sb.setFixedWidth(90)
        for w in (op_cb, val_sb):
            w.setStyleSheet(f"background:{BG3};color:{FG};border:1px solid {BORDER};border-radius:3px;")
        q_row.addWidget(op_cb)
        q_row.addWidget(val_sb)
        q_row.addStretch()
        lay.addLayout(q_row)

        # Results table
        results_tbl = QTableWidget(0, 4)
        results_tbl.setHorizontalHeaderLabels(["Map", "Row", "Col", "Value"])
        results_tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        results_tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        results_tbl.setStyleSheet(f"background:{BG2};color:{FG};gridline-color:{BORDER};")
        results_tbl.setAlternatingRowColors(True)
        results_tbl.setStyleSheet(
            f"QTableWidget{{background:{BG2};color:{FG};gridline-color:{BORDER};}}"
            f"QTableWidget::item:alternate{{background:{BG};}}") 
        lay.addWidget(results_tbl)

        status_lbl = QLabel("")
        status_lbl.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
        lay.addWidget(status_lbl)

        btns = QDialogButtonBox(QDialogButtonBox.Close)
        search_btn = btns.addButton("Search", QDialogButtonBox.ActionRole)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)

        def _run_search():
            op  = op_cb.currentText()
            thr = val_sb.value()
            ops = {">": lambda v, t: v > t, "≥": lambda v, t: v >= t,
                   "<": lambda v, t: v < t,  "≤": lambda v, t: v <= t,
                   "=": lambda v, t: abs(v - t) < 0.5, "≠": lambda v, t: abs(v - t) >= 0.5}
            cmp_fn = ops[op]
            from urrom.ecu_profiles import read_map
            v = self._det.variant
            hits = []
            for m in (v.main_maps or []):
                if m.rows <= 1 or m.confidence == "UNCONFIRMED":
                    continue
                raw_data = read_map(bytes(self._main_rom), m)
                decode = m.decode
                for r in range(m.rows):
                    for c in range(m.cols):
                        rv = raw_data[r][c]
                        dv = decode(rv) if decode else float(rv)
                        if dv is not None and cmp_fn(float(dv), thr):
                            hits.append((m.name, r, c, dv, m))
            results_tbl.setRowCount(len(hits))
            for row_i, (name, r, c, dv, m) in enumerate(hits[:500]):
                fmt = f"{dv:.2f} {m.unit}" if isinstance(dv, float) else str(dv)
                for col_i, text in enumerate([name, str(r), str(c), fmt]):
                    it = QTableWidgetItem(text)
                    it.setData(Qt.UserRole, (m, r, c))
                    results_tbl.setItem(row_i, col_i, it)
            over = f"  (showing first 500)" if len(hits) > 500 else ""
            status_lbl.setText(f"{len(hits)} cells match  {op} {thr}{over}")

        def _on_result_click(row, col):
            it = results_tbl.item(row, 0)
            if not it: return
            m, r, c = it.data(Qt.UserRole)
            # Jump to that map
            try:
                map_idx = next(i for i,mm in enumerate(self._main_chip_tab._maps) if mm is m)
                self._show_editor("main")
                self._main_chip_tab._map_combo.setCurrentIndex(map_idx)
                # Scroll to cell in table
                disp_r = m.rows - 1 - r
                self._main_chip_tab._table.scrollToItem(
                    self._main_chip_tab._table.item(disp_r, c))
                self._main_chip_tab._table.setCurrentCell(disp_r, c)
            except StopIteration:
                pass

        search_btn.clicked.connect(_run_search)
        results_tbl.cellDoubleClicked.connect(_on_result_click)
        _run_search()
        dlg.exec_()

    def _on_export_map_html(self):
        """Export the currently displayed map as a standalone HTML file."""
        if self._main_rom is None or self._det is None:
            QMessageBox.information(self, "Export map", "Load a ROM first.")
            return
        tab = self._main_chip_tab
        if not tab._maps or tab._map_combo.currentIndex() < 0:
            return
        m = tab._maps[tab._map_combo.currentIndex()]
        v = self._det.variant
        path, _ = QFileDialog.getSaveFileName(
            self, "Export map as HTML",
            f"{m.name.replace(' ', '_')}.html",
            "HTML (*.html);;All files (*.*)")
        if not path:
            return
        from urrom.map_export import export_map_html
        html_str = export_map_html(
            bytes(self._main_rom), m, v, include_header=True, include_raw=True)
        # Wrap in a full page
        full = (f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
                f"<title>{m.name}</title>"
                f"<style>body{{background:#0d1117;padding:16px;}}</style></head>"
                f"<body>{html_str}</body></html>")
        Path(path).write_text(full, encoding="utf-8")
        self._update_status(f"Exported → {Path(path).name}")

    # ── Session log as sentences (roadmap item 5) ─────────────────────────

    def _undo_logged_edit(self) -> str:
        """Revert the most recent logged edit in whichever tab holds that map."""
        rec = self._session_log.undo_last()
        if rec is None:
            return "Nothing to undo."
        tab = self._boost_tab if rec.chip == "boost" else self._main_chip_tab
        tbl = tab._table
        if tbl._map_def is None or tbl._map_def.main_addr != rec.map_addr:
            self._session_log._records.append(rec)
            return f"Select {rec.map_name} in the {'boost' if rec.chip == 'boost' else 'editor'} tab first, then undo."
        tbl._push_undo()
        tbl._current_raw[rec.row][rec.col] = rec.old_raw
        tbl._redraw()
        tbl.dataEdited.emit()
        if hasattr(tab, "on_table_changed"):
            tab.on_table_changed()
        self._set_dirty()
        return f"Undone: {rec.sentence()}"

    def _on_session_log_dialog(self):
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton, QLabel, QApplication
        dlg = QDialog(self)
        dlg.setWindowTitle("Session log")
        dlg.setMinimumSize(720, 480)
        dlg.setStyleSheet(f"background:{BG};color:{FG};")
        lay = QVBoxLayout(dlg)
        head = QLabel("")
        head.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
        lay.addWidget(head)
        txt = QTextEdit(); txt.setReadOnly(True)
        txt.setStyleSheet(f"QTextEdit{{background:{BG2};color:{FG};border:1px solid {BORDER};"
                          f"font-family:Consolas,monospace;font-size:11px;}}")
        lay.addWidget(txt, 1)

        def refresh():
            log = self._session_log
            cells = log.collapsed()
            head.setText(f"{log.rom_name or 'ROM'}  ·  {log.variant_name}  ·  started {log.started_at}  ·  "
                         f"{log.count} edits, {len(cells)} cells net")
            txt.setPlainText(log.commit_message(max_lines=500) if cells else "No edits recorded in this session yet.")

        row = QHBoxLayout()
        def _btn(text, fn):
            b = QPushButton(text); b.setFixedHeight(26)
            b.setStyleSheet(f"QPushButton{{background:{BG3};color:{FG};border:1px solid {BORDER};"
                            f"border-radius:3px;padding:0 10px;font-size:10px;}}"
                            f"QPushButton:hover{{border-color:{ACCENT};}}")
            b.clicked.connect(fn); row.addWidget(b); return b
        def copy_msg():
            QApplication.clipboard().setText(self._session_log.commit_message())
            self._update_status("Commit message copied to the clipboard")
        def undo():
            self._update_status(self._undo_logged_edit()); refresh()
        def save():
            path, _ = QFileDialog.getSaveFileName(self, "Save session log", f"tune_{self._session_log.rom_name}.txt",
                                                  "Text (*.txt);;HTML (*.html)")
            if not path:
                return
            Path(path).write_text(self._session_log.to_html() if path.endswith(".html")
                                  else self._session_log.commit_message(max_lines=10000), encoding="utf-8")
            self._update_status(f"Session log saved → {Path(path).name}")
        _btn("Undo last edit", undo)
        _btn("Copy as commit message", copy_msg)
        _btn("Save…", save)
        row.addStretch()
        _btn("Close", dlg.accept)
        lay.addLayout(row)
        refresh()
        dlg.exec_()

    def _on_export_changelog(self):
        """Export the session edit changelog as HTML or text."""
        if self._session_log.count == 0:
            QMessageBox.information(self, "Session changelog",
                "No edits recorded in this session yet.")
            return
        path, filt = QFileDialog.getSaveFileName(
            self, "Export session changelog",
            f"changelog_{self._session_log.rom_name}.html",
            "HTML (*.html);;Text (*.txt);;All files (*.*)")
        if not path:
            return
        if path.endswith('.txt'):
            Path(path).write_text(self._session_log.to_text(), encoding="utf-8")
        else:
            Path(path).write_text(self._session_log.to_html(), encoding="utf-8")
        n = self._session_log.count
        m = len(self._session_log.changed_maps)
        self._update_status(f"Changelog exported: {n} edits in {m} maps → {Path(path).name}")

    def _on_export_rom_html(self):
        """Export all confirmed maps as a single printable HTML reference."""
        if self._main_rom is None or self._det is None:
            QMessageBox.information(self, "Export ROM", "Load a ROM first.")
            return
        v = self._det.variant
        if not v:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export ROM reference",
            f"{v.software_id}_map_reference.html",
            "HTML (*.html);;All files (*.*)")
        if not path:
            return
        from urrom.map_export import export_full_rom_html
        html_str = export_full_rom_html(bytes(self._main_rom), v, self._det)
        Path(path).write_text(html_str, encoding="utf-8")
        n = sum(1 for m in (v.main_maps or [])
                if m.rows > 1 and m.cols > 1
                and m.confidence in ("CONFIRMED", "PROVISIONAL"))
        self._update_status(f"Exported {n} maps → {Path(path).name}")

    def _on_import_xdf(self):
        """Import a TunerPro XDF v1.50 file to add map entries to current variant."""
        if self._det is None or self._det.variant is None:
            QMessageBox.information(self, "Import XDF",
                "Load a ROM first, then import an XDF to add its map addresses.")
            return

        path, _ = QFileDialog.getOpenFileName(
            self, "Import XDF", "",
            "TunerPro XDF (*.xdf *.XDF);;All files (*.*)")
        if not path:
            return

        try:
            from urrom.xdf_import import parse_xdf, xdf_to_mapdefs
            result = parse_xdf(path)
            maps   = xdf_to_mapdefs(result, filter_min_cells=4)
        except Exception as e:
            QMessageBox.critical(self, "XDF Import Error", str(e))
            return

        if not maps:
            QMessageBox.information(self, "XDF Import",
                "No usable maps found in this XDF file.")
            return

        v = self._det.variant
        existing_addrs = {m.main_addr for m in v.main_maps + v.boost_maps}
        new_maps = [m for m in maps if m.main_addr not in existing_addrs]
        dup_count = len(maps) - len(new_maps)

        msg = (
            f"XDF: {result.title or Path(path).name}\n"
            f"Author: {result.author or chr(8212)}\n"
            f"Found {len(maps)} maps, {dup_count} already known.\n\n"
            f"Add {len(new_maps)} new PROVISIONAL maps to {v.name}?\n\n"
            "These will be available in Map editor for this session only.\n"
            "To persist them, edit ecu_profiles.py."
        )
        # Inject into variant (session only — not persisted)
        v.main_maps = v.main_maps + new_maps

        # Reload tabs
        if self._main_rom is not None:
            self._main_chip_tab.load(self._main_rom, v)
            self._compare_tab.set_rom_a(bytes(self._main_rom), v)
            self._overview_tab.update(self._det, self._boost_det if hasattr(self, "_boost_det") else None)

        self._update_status(
            f"XDF imported: {len(new_maps)} maps added from {Path(path).name}")

    def _on_show_shortcuts(self):
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QDialogButtonBox
        dlg = QDialog(self)
        dlg.setWindowTitle("Keyboard Shortcuts")
        dlg.setMinimumWidth(400)
        dlg.setStyleSheet(f"background:{BG};color:{FG};")
        lay = QVBoxLayout(dlg)
        shortcuts = [
            ("File", [
                ("Ctrl+O", "Open ROM"),
                ("Ctrl+S", "Save ROM"),
                ("Ctrl+F", "Find in maps"),
            ]),
            ("Map Editor", [
                ("Ctrl+Z", "Undo"),
                ("Ctrl+Y / Ctrl+Shift+Z", "Redo"),
                ("Ctrl+C", "Copy selection (TSV)"),
                ("Ctrl+V", "Paste"),
                ("Ctrl+A", "Select all"),
                ("Del",    "Clear selection"),
                ("Double-click", "Edit cell (type decoded value)"),
                ("Right-click",  "Context menu: scale, interpolate, smooth…"),
            ]),
            ("Navigation", [
                ("Overview tab → double-click map row", "Jump to map editor"),
                ("Ctrl+F result → double-click",        "Jump to cell in map"),
                ("⊞ Grid button", "All-maps thumbnail overview"),
            ]),
        ]
        for section, keys in shortcuts:
            sec_lbl = QLabel(section.upper())
            sec_lbl.setStyleSheet(
                f"color:{FG_DIM};font-size:10px;letter-spacing:1px;"
                f"margin-top:8px;")
            lay.addWidget(sec_lbl)
            for key, desc in keys:
                row = QLabel(f'  <b style="color:{ACCENT};">{key}</b>'
                             f'  <span style="color:{FG};">— {desc}</span>')
                row.setTextFormat(Qt.RichText)
                lay.addWidget(row)
        btns = QDialogButtonBox(QDialogButtonBox.Ok)
        btns.accepted.connect(dlg.accept)
        lay.addWidget(btns)
        dlg.exec_()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            exts = {Path(u.toLocalFile()).suffix.lower() for u in urls}
            if exts & {".bin", ".034", ".rom", ".BIN"}:
                event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        for url in urls:
            p = Path(url.toLocalFile())
            if p.suffix.lower() in {".bin", ".034", ".rom"}:
                self._load_main(p)
                break

    def _toggle_dashboard(self):
        """Open or close the live ECU dashboard window."""
        from urrom.kwp import DashboardWindow
        if not hasattr(self, '_dashboard') or self._dashboard is None:
            self._dashboard = DashboardWindow(self._kwp_monitor, parent=self)
        if self._dashboard.is_visible():
            self._dashboard.hide()
        else:
            self._dashboard.show()

    # ── Misc ──────────────────────────────────────────────────────────────────

    def _mark_dirty(self):
        self._unsaved = True
        if not self.windowTitle().startswith("*"):
            self.setWindowTitle("* " + WINDOW_TITLE)

    # Alias used throughout the codebase
    _set_dirty = _mark_dirty

    def _clear_dirty(self):
        self._unsaved = False
        self._refresh_title()

    def _refresh_title(self):
        from urrom.version import APP_VERSION, APP_NAME
        if self._main_path and self._det and self._det.variant:
            variant_short = self._det.variant.software_id
            dirty_mark = " •" if self._unsaved else ""
            self.setWindowTitle(
                f"{APP_NAME}  v{APP_VERSION}  —  "
                f"{self._main_path.name}  [{variant_short}]{dirty_mark}")
        else:
            self.setWindowTitle(WINDOW_TITLE)

    def _update_status(self, msg: str):
        self._status.showMessage(msg)

    def _update_title(self, extra: str = ""):
        """Update window title with ROM name and optional context."""
        from urrom.version import WINDOW_TITLE
        if self._main_path:
            dirty = " ●" if self._unsaved else ""
            map_ctx = f"  —  {extra}" if extra else ""
            self.setWindowTitle(f"{WINDOW_TITLE}  —  {self._main_path.name}{dirty}{map_ctx}")
        else:
            self.setWindowTitle(WINDOW_TITLE)

    def _on_overlay_datalog(self):
        """Load a CSV data log and overlay cell coverage on the current map."""
        if self._main_rom is None or self._det is None or not self._det.variant:
            QMessageBox.information(self, "Data log overlay", "Load a ROM first.")
            return
        tab = self._main_chip_tab
        if not tab._maps or tab._map_combo.currentIndex() < 0:
            QMessageBox.information(self, "Data log overlay",
                "Select a map first (Main Chip Maps tab).")
            return

        path, _ = QFileDialog.getOpenFileName(
            self, "Load data log CSV", "",
            "CSV files (*.csv *.CSV *.txt);;All files (*.*)")
        if not path:
            return

        try:
            from urrom.datalog import load_log, compute_coverage, coverage_stats
            log = load_log(Path(path))
        except Exception as e:
            QMessageBox.critical(self, "Error loading log", str(e))
            return

        if not log.rows:
            QMessageBox.warning(self, "Data log", "No data rows parsed from log.")
            return

        m = tab._maps[tab._map_combo.currentIndex()]
        v = self._det.variant
        hits = compute_coverage(log, m, v, bytes(self._main_rom))
        stats = coverage_stats(hits, m)

        if not hits:
            QMessageBox.information(self, "Data log overlay",
                "No matching data points found.\n"
                "Check that the log contains RPM data in the expected column name.")
            return

        # Show summary dialog and apply overlay
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QDialogButtonBox
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Data log coverage — {m.name}")
        dlg.setMinimumWidth(420)
        dlg.setStyleSheet(f"background:{BG};color:{FG};")
        lay = QVBoxLayout(dlg)

        pct = stats['pct_covered']
        n_zero = len(stats['unvisited'])
        rpm_lo, rpm_hi = log.rpm_range

        summary_parts = [
            f"Log: {Path(path).name}  ({log.duration_s:.1f}s)",
            f"Format: {log.format}  |  {len(log.rows)} rows",
            f"RPM range: {log.rpm_range[0]:.0f} - {log.rpm_range[1]:.0f}",
            "",
            f"Map coverage: {pct:.0f}%  ({stats['hit_cells']}/{stats['total_cells']} cells)",
            f"Never visited: {n_zero} cells  |  Max hits: {stats['max_hits']}",
            f"Under-sampled (<5 hits): {len(stats['sparse_cells'])} cells",
        ]
        summary = QLabel("\n".join(summary_parts))
        summary.setStyleSheet(
            f"background:{BG2};padding:12px;border-radius:4px;font-size:11px;")
        summary.setWordWrap(True)
        lay.addWidget(summary)

        if n_zero > 0:
            warn = QLabel(
                f"⚠ {n_zero} cells were never driven — those map regions "
                "may need more data logging.")
            warn.setStyleSheet(f"color:{AMBER};font-size:10px;margin-top:4px;")
            warn.setWordWrap(True)
            lay.addWidget(warn)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("Apply overlay to map")
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)

        def _apply_overlay():
            # Apply hit-count overlay to the MapTable as cell annotations
            tbl = tab._table
            if not tbl._map_def:
                return
            max_h = stats['max_hits'] or 1
            for (raw_r, c), count in hits.items():
                # Annotate — bright for high coverage, dim for low
                intensity = count / max_h
                if intensity >= 0.5:
                    note = f"✓ {count} hits"
                elif intensity >= 0.1:
                    note = f"~ {count} hits"
                else:
                    note = f"⚠ {count} hit{'s' if count!=1 else ''}"
                tbl._annotations[(raw_r, c)] = note
            for (raw_r, c) in stats['unvisited']:
                tbl._annotations[(raw_r, c)] = "✗ never logged"
            tbl._redraw()
            tab.set_trace(hits)
            self._show_editor("main")
            self._update_status(
                f"Log overlay applied: {pct:.0f}% coverage — "
                f"{n_zero} unvisited cells — hover cells for hit count")
            dlg.accept()

        btns.accepted.connect(_apply_overlay)
        dlg.exec_()

    def _on_global_timing_offset(self):
        """Shift ALL confirmed ignition map cells by a fixed degree offset.

        Useful for distributor-as-cam-sensor setups where the physical trigger
        point differs from the AAN cam sensor reference (nominally ~60 BTDC).
        A positive offset advances timing globally; negative retards.
        """
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLabel,
                                      QDoubleSpinBox, QDialogButtonBox, QFrame,
                                      QGroupBox)
        if self._main_rom is None or self._det is None or not self._det.variant:
            QMessageBox.information(self, "Timing offset", "Load a ROM first.")
            return

        v = self._det.variant
        ign_maps = [m for m in getattr(v, 'main_maps', [])
                    if m.map_type == 'ign' and m.confidence == 'CONFIRMED'
                    and m.rows > 1 and m.decode and m.encode]
        if not ign_maps:
            QMessageBox.information(self, "Timing offset",
                "No confirmed ignition maps with encode function for this variant.")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Global ignition timing offset")
        dlg.setMinimumWidth(430)
        dlg.setStyleSheet(f"background:{BG};color:{FG};")
        lay = QVBoxLayout(dlg)
        lay.setSpacing(10)

        ctx_lbl = QLabel(
            "Shifts all ignition map values by a fixed degree offset."
            "\n"
            "\nUse case: distributor-as-cam-sensor adapter where the Hall"
            "\nsender fires at a different crank angle than the AAN cam"
            "\nsensor (nominally ~60 BTDC). Correct in software rather than"
            "\nphysically repositioning the distributor."
            "\n"
            "\n+ offset = more advance  |  - offset = more retard")
        ctx_lbl.setStyleSheet(
            f"color:{FG_DIM};font-size:10px;background:{BG2};"
            f"padding:8px;border-radius:4px;")
        ctx_lbl.setWordWrap(True)
        lay.addWidget(ctx_lbl)

        form = QFormLayout(); form.setSpacing(8)
        offset_spin = QDoubleSpinBox()
        offset_spin.setRange(-60.0, 60.0)
        offset_spin.setValue(0.0)
        offset_spin.setDecimals(1)
        offset_spin.setSingleStep(0.5)
        offset_spin.setSuffix("  ° (BTDC positive)")
        offset_spin.setStyleSheet(
            f"background:{BG2};color:{FG};border:1px solid {BORDER};"
            f"border-radius:3px;padding:2px 4px;font-size:12px;")
        form.addRow("Timing offset:", offset_spin)
        lay.addLayout(form)

        # Live preview
        preview_lbl = QLabel("")
        preview_lbl.setStyleSheet(f"color:{ACCENT};font-size:11px;")
        lay.addWidget(preview_lbl)

        def _update_preview():
            deg = offset_spin.value()
            if not ign_maps[0].decode or not ign_maps[0].encode:
                return
            # raw delta = floor(deg / 0.6491) approximately
            sample_raw = 64  # ~33° BTDC
            sample_dec = ign_maps[0].decode(sample_raw)
            new_dec = sample_dec + deg
            new_raw = ign_maps[0].encode(new_dec)
            raw_delta = new_raw - sample_raw
            sign = "+" if deg >= 0 else ""
            preview_lbl.setText(
                f"Example: {sample_dec:.1f} BTDC → {new_dec:.1f} BTDC  "
                f"(raw {sign}{raw_delta:+d} per cell across {len(ign_maps)} maps)")

        offset_spin.valueChanged.connect(_update_preview)
        _update_preview()

        warn = QLabel(
            "This modifies ALL confirmed ign maps. Use Undo (Ctrl+Z) or"
            "\nRevert to roll back. Session changelog records every cell change.")
        warn.setStyleSheet(f"color:{AMBER};font-size:10px;")
        warn.setWordWrap(True)
        lay.addWidget(warn)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)

        def _apply():
            deg = offset_spin.value()
            if deg == 0.0:
                dlg.accept()
                return
            from urrom.ecu_profiles import read_map, write_map
            total_cells = 0
            for m in ign_maps:
                data = read_map(bytes(self._main_rom), m)
                new_data = []
                for r in range(m.rows):
                    row = []
                    for c in range(m.cols):
                        raw = data[r][c]
                        decoded = m.decode(raw)
                        new_decoded = decoded + deg
                        new_raw = max(0, min(255, m.encode(new_decoded)))
                        row.append(new_raw)
                        if raw != new_raw:
                            total_cells += 1
                    new_data.append(row)
                self._main_rom = bytearray(write_map(bytes(self._main_rom), m, new_data))
            self._main_chip_tab.load(self._main_rom, v)
            self._compare_tab.set_rom_a(bytes(self._main_rom), v)
            self._set_dirty()
            sign = "+" if deg >= 0 else ""
            self._update_status(
                f"Timing offset {sign}{deg:.1f} applied: {total_cells} cells across "
                f"{len(ign_maps)} ign maps")
            dlg.accept()

        btns.accepted.connect(_apply)
        dlg.exec_()

    def _on_overlay_wideband(self):
        """Load a CSV with AFR data and overlay deviation from target on the fuel map."""
        if self._main_rom is None or self._det is None or not self._det.variant:
            QMessageBox.information(self, "Wideband overlay", "Load a ROM first.")
            return
        tab = self._main_chip_tab
        # Find the primary fuel map
        v = self._det.variant
        fuel_maps = [m for m in tab._maps if m.map_type == 'fuel' and m.rows > 1]
        if not fuel_maps:
            QMessageBox.information(self, "Wideband overlay",
                "No fuel map available. Open Main Chip Maps tab first.")
            return

        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLabel,
                                      QDoubleSpinBox, QDialogButtonBox)
        # Get target AFR
        cfg_dlg = QDialog(self)
        cfg_dlg.setWindowTitle("Wideband overlay settings")
        cfg_dlg.setMinimumWidth(320)
        cfg_dlg.setStyleSheet(f"background:{BG};color:{FG};")
        cfg_lay = QVBoxLayout(cfg_dlg)
        form = QFormLayout()
        target_spin = QDoubleSpinBox()
        target_spin.setRange(10.0, 20.0); target_spin.setValue(14.7); target_spin.setDecimals(1)
        target_spin.setSuffix("  AFR target")
        target_spin.setStyleSheet(
            f"background:{BG2};color:{FG};border:1px solid {BORDER};border-radius:3px;padding:2px 4px;")
        form.addRow("Target AFR (stoich=14.7):", target_spin)
        cfg_lay.addLayout(form)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.rejected.connect(cfg_dlg.reject)
        btns.accepted.connect(cfg_dlg.accept)
        cfg_lay.addWidget(btns)
        if cfg_dlg.exec_() != QDialog.Accepted:
            return
        target_afr = target_spin.value()

        path, _ = QFileDialog.getOpenFileName(
            self, "Load wideband log CSV", "",
            "CSV files (*.csv *.CSV *.txt);;All files (*.*)")
        if not path:
            return
        try:
            from urrom.datalog import load_log, compute_afr_overlay
            log = load_log(Path(path))
        except Exception as e:
            QMessageBox.critical(self, "Error loading log", str(e))
            return
        if not log.rows or not any(r.afr for r in log.rows if r.afr):
            QMessageBox.warning(self, "Wideband log",
                "No AFR data found in log.\n"
                "Check that the file has a column named 'AFR', 'WBO2', or 'Lambda'.")
            return

        m = fuel_maps[0]
        # Switch to fuel map in editor
        if m in tab._maps:
            tab._map_combo.setCurrentIndex(tab._maps.index(m))

        overlay = compute_afr_overlay(log, m, v, bytes(self._main_rom), target_afr)
        if not overlay:
            QMessageBox.information(self, "Wideband overlay",
                "No matching data points with AFR measurements found.")
            return

        # Apply as colour-coded annotations
        tbl = tab._table
        lean_count = sum(1 for d in overlay.values() if d['delta'] > 1.0)
        rich_count = sum(1 for d in overlay.values() if d['delta'] < -1.0)
        on_target  = len(overlay) - lean_count - rich_count

        for (raw_r, c), data in overlay.items():
            d = data['delta']
            afr = data['afr_mean']
            count = data['count']
            if d > 2.0:
                prefix = "⚠ LEAN"
            elif d > 1.0:
                prefix = "↑ lean"
            elif d < -2.0:
                prefix = "⚠ RICH"
            elif d < -1.0:
                prefix = "↓ rich"
            else:
                prefix = "✓ ok"
            tbl._annotations[(raw_r, c)] = (
                f"{prefix} {afr:.1f} AFR  Δ{d:+.1f}  n={count}")

        tbl._redraw()
        self._show_editor("main")
        self._update_status(
            f"Wideband overlay: {len(overlay)} cells logged  —  "
            f"lean: {lean_count}  rich: {rich_count}  on-target: {on_target}  "
            f"target={target_afr} AFR")

    def _on_fpr_calculator(self):
        """Standalone fuel pressure / injector sizing calculator."""
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLabel,
                                      QDoubleSpinBox, QDialogButtonBox, QFrame)
        dlg = QDialog(self)
        dlg.setWindowTitle("Fuel pressure & injector calculator")
        dlg.setMinimumWidth(380)
        dlg.setStyleSheet(f"background:{BG};color:{FG};")
        lay = QVBoxLayout(dlg)

        def _make_spin(lo, hi, val, dec, suffix):
            s = QDoubleSpinBox()
            s.setRange(lo, hi); s.setDecimals(dec); s.setValue(val)
            s.setSuffix(f"  {suffix}")
            s.setStyleSheet(f"background:{BG2};color:{FG};border:1px solid {BORDER};"
                            f"border-radius:3px;padding:2px 4px;")
            return s

        form = QFormLayout(); form.setSpacing(8); lay.addLayout(form)
        inj_stock = _make_spin(50, 2000, 293, 0, "cc/min  stock injectors")
        inj_new   = _make_spin(50, 2000, 440, 0, "cc/min  new injectors")
        fpr_stock = _make_spin(0.5, 10, 3.0, 1, "bar  stock FPR")
        fpr_new   = _make_spin(0.5, 10, 5.0, 1, "bar  new FPR")
        form.addRow("Stock injectors:", inj_stock)
        form.addRow("New injectors:",   inj_new)

        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color:{BORDER}"); lay.addWidget(sep)

        form2 = QFormLayout(); form2.setSpacing(8); lay.addLayout(form2)
        form2.addRow("Stock FPR:", fpr_stock)
        form2.addRow("New FPR:",   fpr_new)

        result_lbl = QLabel("")
        result_lbl.setStyleSheet(
            f"background:{BG2};padding:10px;border-radius:4px;"
            f"font-size:12px;color:{ACCENT};border:1px solid {BORDER};")
        result_lbl.setWordWrap(True)
        lay.addWidget(result_lbl)

        def _calc():
            import math
            si, ni = inj_stock.value(), inj_new.value()
            sp, np_ = fpr_stock.value(), fpr_new.value()
            if ni == 0 or np_ == 0: return
            # Injector scaling: new_cc_effective = ni * sqrt(np_/sp)
            ni_eff = ni * math.sqrt(np_ / sp)
            # Fuel scale factor to maintain same AFR
            factor = si / ni_eff
            # Duty cycle at max (assuming 85% at stock)
            dc = 0.85 * factor * 100
            parts2 = [
                f"Effective injector flow: {ni_eff:.0f} cc/min",
                f"Fuel map scale factor:   {factor:.4f}x  ({factor*100:.1f}%)",
                f"Max duty cycle estimate: {dc:.0f}%",
                ("\u26a0 Duty cycle > 90% - marginal" if dc > 90 else "\u2713 Duty cycle OK"),
            ]
            result_lbl.setText("\n".join(parts2))
        for w in (inj_stock, inj_new, fpr_stock, fpr_new):
            w.valueChanged.connect(_calc)
        _calc()

        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)
        dlg.exec_()

    def _add_recent(self, path: Path) -> None:
        """Add a file to the recent list, keeping max 8 unique entries."""
        p = str(path)
        if p in self._recent_files:
            self._recent_files.remove(p)
        self._recent_files.insert(0, p)
        self._recent_files = self._recent_files[:8]
        self._settings.setValue("recent_files", self._recent_files)

    def _refresh_recent_menu(self) -> None:
        """Rebuild the Recent files submenu (and the welcome-page list)."""
        self._refresh_welcome_recent()
        self._recent_menu.clear()
        if not self._recent_files:
            self._recent_menu.addAction("(no recent files)").setEnabled(False)
            return
        for p_str in self._recent_files:
            p = Path(p_str)
            act = self._recent_menu.addAction(p.name)
            act.setToolTip(p_str)
            act.triggered.connect(lambda checked, pp=p: self._load_main(pp))
        self._recent_menu.addSeparator()
        self._recent_menu.addAction("Clear recent files").triggered.connect(
            lambda: (self._recent_files.clear(),
                     self._settings.setValue("recent_files", []),
                     self._update_status("Recent files cleared")))

    def _on_about(self):
        QMessageBox.about(
            self, f"About {APP_NAME}",
            f"<b>{APP_NAME}</b> v{APP_VERSION}<br><br>"
            "Open-source ROM editor for Bosch Motronic M2.3 / M2.3.2.<br>"
            "Supports: 3B, AAN, ABY, ADU (RS2), PT / ABH V8.<br><br>"
            "Map addresses confirmed against real ROM dumps and RS2.xdf.<br>"
            "Use at your own risk. Verify all changes on a wideband O2.<br><br>"
            "<a href='https://github.com/dspl1236/UrROM' "
            "style='color:#569cd6'>github.com/dspl1236/UrROM</a>")

    def closeEvent(self, event):
        fw_dirty = (self._main_fw is not None and self._main_full is not None
                    and bytes(self._main_fw) != self._main_full[:MAIN_CHIP_WORKING])
        if self._unsaved or fw_dirty or self._main_chip_tab.has_changes():
            r = QMessageBox.question(
                self, "Unsaved changes",
                "You have unsaved map edits. Quit anyway?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                event.ignore()
                return
        self._settings.setValue("geometry", self.saveGeometry())
        self._settings.setValue("windowState", self.saveState())
        self._recorder.stop()
        if getattr(self, "_bench", None) is not None:
            try: self._bench.stop()
            except Exception: pass
        self._kwp_monitor.stop()
        event.accept()

    # ── KWPBridge live overlay ────────────────────────────────────────────────

    # ── Bench mode (roadmap item 6) ───────────────────────────────────────

    def _bench_profile(self) -> tuple[str, str]:
        """(mock ecu name, its part number) for the loaded chip family."""
        sw = self._det.variant.software_id if self._det and self._det.variant else ""
        from urrom.hw_patches import is_404_family
        if is_404_family(sw):
            return "3b", "447907404AA"
        return "aan", "4A0907551AA"

    def _start_bench(self, port: int | None = None, hz: float = 5.0) -> str:
        """Start the mock ECU for the loaded chip. Returns a status message."""
        if self._det is None or self._det.variant is None:
            return "Load a chip first."
        if not kwpbridge_available():
            return "KWPBridge is not installed (pip install -e <KWPBridge checkout>)."
        try:
            from kwpbridge.mock.server import MockServer
            from kwpbridge.constants import DEFAULT_PORT
        except ImportError as e:
            return f"KWPBridge mock not available: {e}"
        port = port or DEFAULT_PORT
        if self._bench is None and kwpbridge_running():
            return "KWPBridge is already running on this port — bench mode is not needed."
        ecu, pn = self._bench_profile()
        try:
            srv = MockServer(ecu=ecu, port=port, poll_hz=hz)
            srv.start()
        except Exception as e:
            return f"Bench mode could not start: {e}"
        self._bench, self._bench_pn = srv, pn
        rom_pns = list(self._det.variant.ecu_pns) + [pn]
        self._kwp_monitor.set_rom_part_numbers(rom_pns)
        self._kwp_monitor.start()
        if hasattr(self, "_trace_act"):
            self._trace_act.setChecked(True)
        return (f"Bench mode: simulated {ecu.upper()} ({pn}) at {hz:.0f} Hz — cold start, warm idle, "
                f"cruise, boost run, decel. Live cursor and trace are driven by the mock.")

    def _stop_bench(self) -> str:
        if self._bench is None:
            return "Bench mode is off."
        try:
            self._bench.stop()
        except Exception:
            pass
        self._bench = None
        self._bench_pn = ""
        self._kwp_monitor.stop()
        self._kwp_matched = False
        self._main_chip_tab.detach_kwp()
        if self._det and self._det.variant:
            self._kwp_monitor.set_rom_part_numbers(list(self._det.variant.ecu_pns))
        self._kwp_monitor.start()
        self._refresh_kwp_badge()
        return "Bench mode off."

    def _on_toggle_bench(self, on: bool):
        msg = self._start_bench() if on else self._stop_bench()
        if on and self._bench is None:            # failed to start: uncheck quietly
            self._bench_act.blockSignals(True); self._bench_act.setChecked(False); self._bench_act.blockSignals(False)
            QMessageBox.information(self, "Bench mode", msg)
        self._update_status(msg)

    def _on_kwp_connected(self, ecu_pn: str):
        self._kwp_matched = self._kwp_monitor.is_matched()
        self._refresh_kwp_badge()
        if self._kwp_matched:
            self._main_chip_tab.attach_kwp()
            self._update_status(
                f"KWPBridge  ·  {ecu_pn}  ·  ECU matches ROM  ·  live overlay active")
        else:
            rom_pns = (self._det.variant.ecu_pns
                       if self._det and self._det.variant else [])
            rom_str = rom_pns[0] if rom_pns else "no ROM"
            self._update_status(
                f"KWPBridge  ·  ECU {ecu_pn}  ≠  {rom_str}  ·  overlay locked")

    def _on_kwp_disconnected(self):
        self._kwp_matched = False
        self._main_chip_tab.detach_kwp()
        self._refresh_kwp_badge()
        self._update_status("KWPBridge disconnected")

    def _on_kwp_mismatch(self, ecu_pn: str, rom_pn: str):
        self._kwp_matched = False
        self._main_chip_tab.detach_kwp()
        self._refresh_kwp_badge()

    def _on_kwp_live_data(self, lv: "LiveValues"):
        if not self._kwp_matched:
            # a match can appear with the first state message, after connect
            if self._kwp_monitor.is_matched():
                self._on_kwp_connected(self._kwp_monitor.current_pn())
            else:
                return
        self._main_chip_tab.update_overlay(lv)
        self._trace.add(lv)
        if self._recorder.active:
            self._recorder.add(lv)
        if self._trace_act.isChecked():
            self._trace_dirty += 1
            if self._trace_dirty >= 5:          # repaint every 5 samples (~0.5 s)
                self._trace_dirty = 0
                self._refresh_trace()
        summary = kwp_live_summary(lv)
        if summary:
            rec = f"  ● REC {self._recorder.rows}" if self._recorder.active else ""
            self._update_status(f"🟢  {summary}{rec}")
        self._refresh_kwp_badge(lv)

    # ── Live recording / trace ────────────────────────────────────────────

    def _on_toggle_recording(self):
        if self._recorder.active:
            p = self._recorder.stop()
            self._rec_act.setText("Start recording live data…")
            self._update_status(f"Recording stopped: {p.name} ({self._recorder.rows} rows). "
                                "Tools → Overlay data log on map… replays it onto any map.")
            return
        from datetime import datetime
        pn = (self._det.variant.software_id if self._det and self._det.variant else "ecu")
        default = str(Path.home() / f"urrom_log_{pn}_{datetime.now():%Y%m%d_%H%M%S}.csv")
        path, _ = QFileDialog.getSaveFileName(self, "Record live data to CSV", default, "CSV (*.csv)")
        if not path:
            return
        self._recorder.start(path)
        self._rec_act.setText("Stop recording live data")
        self._update_status(f"Recording live data to {Path(path).name} — "
                            f"{'connected' if self._kwp_matched else 'waiting for KWPBridge'}")

    def _on_toggle_trace(self, on: bool):
        if on:
            self._refresh_trace()
        else:
            self._main_chip_tab.set_trace(None)

    def _on_clear_trace(self):
        self._trace.clear()
        self._main_chip_tab.set_trace(None)
        self._update_status("Live trace cleared")

    def _refresh_trace(self):
        rows, cols = self._main_chip_tab.current_axes()
        if rows is None or not len(self._trace):
            self._main_chip_tab.set_trace(None)
            return
        hits = self._trace.hits_for(rows, cols)
        self._main_chip_tab.set_trace(hits)
        self._update_status(f"Live trace: {len(self._trace)} samples over {len(hits)} cells "
                            f"of {self._main_chip_tab._table._map_def.name}")

    def _refresh_kwp_badge(self, lv=None):
        """Update the KWP status badge in the file bar and Tools menu label."""
        self._refresh_kwp_menu_label()
        rom_pns = (self._det.variant.ecu_pns
                   if self._det and self._det.variant else [])
        text, colour = kwp_status_label(self._kwp_monitor, rom_pns)
        if lv and self._kwp_matched:
            summary = kwp_live_summary(lv)
            if summary:
                text = f"🟢  {summary}"
        if getattr(self, "_bench", None) is not None:
            text = "🧪 BENCH  " + text.replace("ECU matches ROM", "simulated ECU")
            colour = AMBER
        self._kwp_badge.setText(text)
        self._kwp_badge.setStyleSheet(
            f"color: {colour}; font-size: 10px; padding: 0 8px;")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    win = MainWindow()
    win.show()
    # If a file is passed on the command line, load it
    if len(sys.argv) > 1:
        p = Path(sys.argv[1])
        if p.exists():
            win._load_main(p)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
