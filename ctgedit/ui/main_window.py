"""The main editor window."""

from __future__ import annotations

import array
import bisect

from .main_window_base import (
    WindowState,
    app_settings,
    CanvasView,
    DESCRIPTIONS,
    DiagnosticsMixin,
    EditableDocument,
    icon,
    LibraryPanel,
    ModelessMixin,
    ModelessWindows,
    NEEDS_CUSTOM,
    ObjectsDock,
    os,
    Page,
    PageScene,
    paste_linked,
    PropertyPanel,
    QtCore,
    QtGui,
    QtWidgets,
    SUBMENU_DESCRIPTIONS,
    TITLE_APP,
    TOOL_HELP,
)
from .main_window_base import (  # noqa: F401  (the private names, re-exported)
    _ALIGN,
    _AppEventFilter,
    _AutoChangesBar,
    _INPUT_EVENTS,
    _INSERT_KINDS,
    _LABEL_MODE_NAMES,
    _PaintWatch,
    _PendingPage,
    _SPECIAL_TOOLS,
    _TOOLS,
    _V34_KINDS,
    _WheelGuard,
    _app_filter,
    default_open_dir,
    _install_menu_close_watch,
    _install_popup_key_guard,
    _install_wheel_guard,
    _run_all_post_open,
)
from .style.glyphs_cmd import V34_ICONS
from .window.tabs import DocumentTabs
from .window.files import PageFiles
from .window.workspace import WorkspaceTools
from .window.view import ViewState
from .window.canvas_edits import CanvasEdits
from .window.text_edit import TextEditMixin
from .window.navigation import NavigationCommands
from .window.commands import Commands
from .panels.styles_gallery import StyleCommands
from .window.library import LibraryCommands
from .window.shell import WindowShell
from .panels.history_dock import HistoryCommands
from .panels.changes_dock import ChangesCommands
from .dialog.history_versions import VersionCommands
from .editor.underlay import UnderlayCommands
from .window.libwatch import LibraryWatch


# the commands by topic are mixins in ctgedit/ui/window; construction is here
class MainWindow(
    DocumentTabs,
    PageFiles,
    WorkspaceTools,
    ViewState,
    CanvasEdits,
    TextEditMixin,
    StyleCommands,
    Commands,
    LibraryCommands,
    NavigationCommands,
    WindowShell,
    ModelessMixin,
    DiagnosticsMixin,
    HistoryCommands,
    ChangesCommands,
    VersionCommands,
    UnderlayCommands,
    LibraryWatch,
    WindowState,
    QtWidgets.QMainWindow,
):
    _instances: list["MainWindow"] = []  # Window menu registry
    # every page this process had open (normcase paths): the session list
    # another ctgedit process wrote keeps its own pages (merged_session_files)
    _session_seen: set = set()

    @classmethod
    def editor_windows(cls) -> list:
        """The open editor windows (what Settings and the keyboard map apply to)."""
        return list(cls._instances)

    def __init__(self):
        super().__init__()
        # garbage collection on the GUI thread only: a worker thread's collection would run
        # Qt destructors off it (infra.gcguard)
        from ..infra import gcguard

        gcguard.install()
        gcguard.install_timer(QtWidgets.QApplication.instance())
        # a modal dialog with a parent goes once answered (ui/dialog_release)
        from . import dialog_release

        dialog_release.install(dispatcher=_app_filter())
        MainWindow._instances.append(self)
        self.setWindowTitle(TITLE_APP)
        self.resize(1500, 950)
        self._page: Page | None = None
        self.doc: EditableDocument | None = None
        self._sel: list[int] = []
        self._locked: set[int] = set()  # session lock mode (CtDraw Edit > Lock)
        self._break_lock = False  # Break Lock Mode: ignore ALL locks
        # open modeless windows, keyed (one per target; ui/modeless.py)
        self._dialogs = ModelessWindows(lambda: self.doc)
        self._show_thumb = False
        self._show_grid = True
        self._grid_size = 8  # horizontal pitch (px)
        self._grid_y: int | None = None  # vertical pitch; None = the horizontal one
        self._grid_origin = (0, 0)  # Grid Setup's origin (base point)
        self._nudge_step = 1  # arrow-key move step (px), see Settings
        # (the colours of new objects: ui/editor/draw_styles, the ribbon's swatch pair)
        self._pending_symbol: tuple[str, str, str] | None = None
        # the composite template (guid) Paste Composite Genie armed the
        # symbol tool with: the next click places a copy of it
        self._pending_composite: str | None = None
        # the control Insert ActiveX Control armed the ActiveX tool with:
        # ('kind', factory kind) or ('donor', io.activex.ControlDonor)
        self._pending_control = None
        # @() label mode (literal | native | <language>); 'literal' = CtDraw's
        # editor look. Set the reader global so every page parse honours it.
        self._label_mode = app_settings().value("view/label_mode", "literal", type=str) or "literal"
        from ..io import reader as _reader

        _reader.set_label_mode(self._label_mode)
        # options applied through the environment (Settings dialog) must also
        # hold after a restart
        _st = app_settings()
        for key, env in (("opt/raw_labels", "CTG_RAW_LABELS"), ("opt/dyn_badges", "CTG_DYN_BADGES")):
            if _st.value(key, False, type=bool):
                os.environ[env] = "1"

        from .dialog.about import register_diagnostics

        register_diagnostics()  # (Help > Copy Diagnostics: the build, the user folder)
        self.scene = PageScene(self)
        self.view = CanvasView(self)
        self.view.setScene(self.scene)
        # screen readers: the canvas's objects are reached through the Objects panel
        self.view.setAccessibleName("Page canvas")
        self.view.setAccessibleDescription(
            "The page being edited. To reach its objects with the keyboard, use the "
            "Objects panel; here the arrow keys move the selected objects."
        )
        # open pages as TABS over one canvas (each keeps its own undo history,
        # selection, hidden roots and zoom)
        self._docs: list[dict] = []
        # (doc_tabs.py: the bar, its tab menu, pins, the list and '+' buttons after it)
        from .panels.doc_tabs import DocTabBar

        self.doc_tabs = DocTabBar(self)
        self.doc_tabs.setVisible(False)
        self.doc_tabs.currentChanged.connect(self._on_doc_tab)
        self.doc_tabs.tabCloseRequested.connect(self._close_tab_requested)
        self.doc_tabs.tabMoved.connect(self._on_doc_tab_moved)
        self.doc_tabs.tabMenuRequested.connect(self._tab_menu)
        self.doc_tabs.tabDraggedOut.connect(self._tab_dragged_out)
        self.doc_tabs.tabDropped.connect(self._tab_dropped)
        self.doc_tabs.moveFinished.connect(self._enforce_pin_order)
        self.doc_tabs.tabUnpinRequested.connect(self._unpin_tab)
        central = QtWidgets.QWidget(self)
        cl = QtWidgets.QVBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        # what was updated automatically on this page (open checks, background genie
        # updates): one bar over the canvas, per page (_show_auto_bar)
        self._auto_bar = _AutoChangesBar(self)
        self._auto_bar.undoRequested.connect(self._undo_auto_changes)
        cl.addWidget(self._auto_bar)
        cl.addWidget(self.view, 1)
        self.setCentralWidget(central)

        # their Settings choices, before the panels are made
        from .panels import panel_options
        from .prefs import timing_options

        panel_options.apply()
        timing_options.apply()  # (the pauses and counts)
        self._build_docks()
        self._build_actions()
        from .panels.doc_tabs import build_tab_strip

        cl.insertWidget(0, build_tab_strip(self, self.doc_tabs))
        self._build_menus()
        self._build_ribbon()
        self._wire_objects_dock()
        self._install_styles()
        self._install_new_colours()
        self._build_statusbar()
        self._wire_canvas()
        self._install_canvas_keys()
        # (again with the canvas' own keys known: no command takes an arrow key)
        from .prefs import keymap as _keymap

        _keymap.apply_keymap(self)
        # the application's event filters: ONE installed filter hands the few
        # watched event types to these handlers (_AppEventFilter)
        _install_wheel_guard()
        self._install_help()
        from .style import accessibility

        # names for icon-only buttons, fields, dialogs
        accessibility.install(dispatcher=_app_filter())
        # F4 / Alt+Down in a drop-down open its list (no window command on F4 runs then)
        _install_popup_key_guard()
        # a command picked in a menu knows the pointer is on the menu (paste_point)
        _install_menu_close_watch()
        # a page / library changed by another program while it is open here
        # (the Graphics Builder, another ctgedit, a copy): asked about when
        # it happens instead of only when saving
        self._watcher = QtCore.QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_file_changed)
        self._disk_pending: set = set()
        self._disk_timer = QtCore.QTimer(self)
        self._disk_timer.setSingleShot(True)
        self._disk_timer.setInterval(600)  # a save arrives as several events
        self._disk_timer.timeout.connect(self._check_disk_changes)
        # a library saved by another program: the pages that use it are drawn again
        # (window/libwatch.py; Settings > Pages and libraries > Files and folders)
        self._install_library_watch()
        # recovery copies of unsaved documents (one controller per application)
        from .recovery import controller

        controller()
        from .panels.start_page import StartPage

        self.start_page = StartPage(self.view.viewport())
        self.start_page.openRequested.connect(self.open_page)
        self.start_page.browseRequested.connect(self.open_dialog)
        self.start_page.newRequested.connect(self.new_blank_dialog)
        # hidden until _update_start_page shows it (so isHidden() tells which it is)
        self.start_page.hide()
        # a page or a library dragged from Explorer onto the window opens (window/files.py)
        self.setAcceptDrops(True)
        self._install_page_drop()
        self._install_tab_keys()  # (Ctrl+Tab and Ctrl+PgDown: window/tabs.py)
        # the undo history, the changes since the save and the earlier versions
        self._install_history_ui()
        self._install_changes_ui()
        self._install_versions_ui()
        # the underlay: a page or a picture drawn faded on the canvas (ui/editor/underlay.py)
        self._install_underlay_ui()
        # the tools, the arrow keys, paste, Align, placing, the
        # backups and the recent pages: window/canvas_edits
        self.apply_editing_options()
        # Settings > Canvas and Editing's choices the window keeps (the smart guides, the
        # arrow-key step, the paste offset, the opening zoom): every window reads them, not
        # only the one restore_session restores (window/shell.apply_canvas_options)
        self.apply_canvas_options()
        # the View menu's grid, snap, guideline and smart guide toggles as stored (a window
        # made without restore_session too: Window > New Window, a tab moved to a new window)
        self.apply_view_toggles()
        self._update_actions()
        if not app_settings().value("ui/keys_hint_shown", False, type=bool):
            # (the first run names the two keys that find everything else; the start page
            # says how to begin, so no prompt to open a page here)
            app_settings().setValue("ui/keys_hint_shown", True)
            hint = "%s finds any command, %s lists the keys." % (
                _keymap.native((_keymap.keys_of(self.act_palette) or ["Ctrl+K"])[0]),
                _keymap.native((_keymap.keys_of(self.act_shortcuts_window) or ["Ctrl+/"])[0]),
            )
            # (a hint: not kept in the bell's list of messages, ui/panels/status_items.py)
            self.statusBar().show_hint(hint, 15000)

    # ------------------------------------------------------------------ docks
    def _build_docks(self) -> None:
        self.objects_dock = ObjectsDock(self)
        d1 = QtWidgets.QDockWidget("Objects", self)
        d1.setObjectName("dock_objects")
        d1.setWidget(self.objects_dock)
        self.addDockWidget(QtCore.Qt.LeftDockWidgetArea, d1)

        self.library_dock = LibraryPanel(self)
        d2 = QtWidgets.QDockWidget("Library", self)
        d2.setObjectName("dock_library")
        d2.setWidget(self.library_dock)
        self.addDockWidget(QtCore.Qt.LeftDockWidgetArea, d2)
        self.tabifyDockWidget(d1, d2)
        d1.raise_()

        self.props = PropertyPanel(self)
        d3 = QtWidgets.QDockWidget("Properties", self)
        d3.setObjectName("dock_props")
        d3.setWidget(self.props)
        d3.setMinimumWidth(220)  # (a 1024 px screen keeps a canvas)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, d3)
        self.dock_objects, self.dock_library, self.dock_props = d1, d2, d3

        from .panels.tags_dock import TagsDock, restore_root_choices

        # the compiled projects chosen in the Tags panel, before a page is read
        # (its display formats come from the same projects)
        restore_root_choices()
        self.tags_dock = TagsDock(self)
        d4 = QtWidgets.QDockWidget("Tags", self)
        d4.setObjectName("dock_tags")
        d4.setWidget(self.tags_dock)
        self.addDockWidget(QtCore.Qt.LeftDockWidgetArea, d4)
        self.tabifyDockWidget(d2, d4)
        self.dock_tags = d4
        d1.raise_()

        # Situational Awareness: the workspace checks of the open page (shown
        # when a save of a page with panes finds something)
        from .panels.sa_checks import WorkspaceChecksPanel

        self.sa_checks = WorkspaceChecksPanel(self)
        d5 = QtWidgets.QDockWidget("Workspace Checks", self)
        d5.setObjectName("dock_sa_checks")
        d5.setWidget(self.sa_checks)
        self.addDockWidget(QtCore.Qt.BottomDockWidgetArea, d5)
        d5.hide()
        self.dock_sa_checks = d5
        self.sa_checks.paneActivated.connect(self._goto_sa_finding)
        self.sa_checks.pagePropertiesRequested.connect(self._page_properties_tab)
        self.sa_checks.recheckRequested.connect(lambda: self._check_workspace(show=True))

        # Tools > Check Page: the page checks of the open page
        from .panels.page_checks import PageChecksPanel

        self.page_checks = PageChecksPanel(self)
        d7 = QtWidgets.QDockWidget("Page Checks", self)
        d7.setObjectName("dock_page_checks")
        d7.setWidget(self.page_checks)
        self.addDockWidget(QtCore.Qt.BottomDockWidgetArea, d7)
        d7.hide()
        self.dock_page_checks = d7
        self.page_checks.findingActivated.connect(self._goto_page_finding)
        self.page_checks.propertiesRequested.connect(self._page_finding_properties)
        self.page_checks.recheckRequested.connect(lambda: self._check_page(show=True))
        self.page_checks.displayCheckRequested.connect(lambda: self._check_page_display())

        # the undo history as a list (View > Panels > History; hidden by default)
        from .panels.history_dock import HistoryDock

        self.history_dock = HistoryDock(self)
        d6 = QtWidgets.QDockWidget("History", self)
        d6.setObjectName("dock_history")
        d6.setWidget(self.history_dock)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, d6)
        d6.hide()
        self.dock_history = d6
        self.history_dock.goToRequested.connect(self.go_to_history)
        d6.visibilityChanged.connect(lambda on: self.history_dock.show_document(self.doc) if on else None)
        # what differs from the saved page (View > Panels > Changes; hidden by default,
        # tabbed with the check panels)
        from .panels.changes_dock import ChangesDock

        self.changes_dock = ChangesDock(self)
        d8 = QtWidgets.QDockWidget("Changes", self)
        d8.setObjectName("dock_changes")
        d8.setWidget(self.changes_dock)
        self.addDockWidget(QtCore.Qt.BottomDockWidgetArea, d8)
        self.tabifyDockWidget(self.dock_page_checks, d8)
        d8.hide()
        self.dock_changes = d8
        # the navigator as a panel (View > Panels > Navigator; hidden by default)
        self._build_navigator_dock()
        # every panel: one title bar, dock / float by dragging, double-clicking or its button
        from .panels import docking

        docking.install_all(self)

        self.objects_dock.selectionChanged.connect(self._on_dock_selection)
        self.objects_dock.deleteRequested.connect(self._delete_indices)
        self.objects_dock.zOrderRequested.connect(self._reorder)
        self.objects_dock.hideToggled.connect(self._on_hide_toggled)
        self.objects_dock.frameRequested.connect(self._frame_index)
        self.objects_dock.propertiesRequested.connect(self._open_sheet_of)
        self.objects_dock.genieRequested.connect(self._edit_genie_at)
        # (its other requests and its menu: _wire_objects_dock, once the commands exist)
        self._hidden_roots: set[int] = set()
        self.library_dock.placeRequested.connect(self._arm_symbol_placement)
        self.library_dock.editRequested.connect(lambda path, el: self._open_element(path, el))
        self.library_dock.deleteRequested.connect(self._delete_library_element)
        self.library_dock.renameRequested.connect(self._rename_library_element)
        self.library_dock.usedByRequested.connect(self._show_used_by)
        self.library_dock.formRequested.connect(lambda path, el: self._open_genie_form(path, el))
        # (the actions are built after the docks)
        self.library_dock.linkedToggled.connect(lambda on: self.act_paste_linked.setChecked(on))
        # the element window's hot spot marker, dragged (CtDraw's canvas state
        # 0x100) or double-clicked
        self.view.hotspotMoved.connect(self._on_hotspot_moved)
        self.view.hotspotDialogRequested.connect(self._hotspot_dialog)
        self.props.changed.connect(self._after_edit)
        self.props.statusMessage.connect(lambda m: self.statusBar().showMessage(m, 5000))
        self.props.editGenie.connect(self._edit_genie_at)
        self.props.editInput.connect(self.edit_input)
        self.props.editPage.connect(self.edit_object_page)
        # a genie member / locked template record shows read-only there
        self.props.read_only_reason = self._read_only_reason
        self.props.definition_label = self._owner_definition_label
        self.props.editDefinition.connect(self._edit_owner_definition)
        self.props.showSheet.connect(self.object_properties)

    # ---------------------------------------------------------------- actions
    def _act(self, text, slot, shortcut=None, icon_name=None, checkable=False):
        a = QtGui.QAction(text, self)
        if icon_name:
            a.setIcon(icon(icon_name))
        if shortcut:
            a.setShortcut(shortcut)
        if checkable:
            a.setCheckable(True)
        a.triggered.connect(slot)
        return a

    def _build_actions(self) -> None:
        self.act_new = self._act("&New…", self.new_blank_dialog, "Ctrl+N", "new")
        self.act_open = self._act("&Open…", self.open_dialog, QtGui.QKeySequence.Open, "open")
        self.act_quick_open = self._act("Quick Open…", self.quick_open, "Ctrl+Shift+O", "search")
        self.act_quick_open.setToolTip(
            "Find a page, symbol, genie, tag or Cicode function of the page's project by name"
        )
        self.act_save = self._act("&Save", self.save, QtGui.QKeySequence.Save, "save")
        self.act_save_as = self._act("Save &As…", self.save_as, "Ctrl+Shift+S")
        # Alt+F10 = CtDraw's exact binding (F7 is Snap to Guidelines)
        self.act_compile = self._act("Compile Project", self.compile_project_command, "Alt+F10", "compile")
        self.act_undo = self._act("Undo", self.undo, QtGui.QKeySequence.Undo, "undo")
        self.act_redo = self._act("Redo", self.redo, None, "redo")
        # (the first key is the one the menu shows: the Graphics Builder's; Ctrl+Shift+Z as in
        # the design programs)
        self.act_redo.setShortcuts([QtGui.QKeySequence("Ctrl+Y"), QtGui.QKeySequence("Ctrl+Shift+Z")])
        # Edit > Repeat: the last command that acts on the selection, again (ui/prefs/keymap)
        from .prefs import keymap as _keymap

        self.act_repeat = self._act("Repeat", lambda: _keymap.run_repeat(self), "F4")
        self.act_repeat.setStatusTip(
            "Run the last align, order, rotate, flip, group, lock or text command again on the selection"
        )
        self.act_cut = self._act("Cut", self.cut_selected, QtGui.QKeySequence.Cut, "cut")
        self.act_copy = self._act("Copy", self.copy_selected, QtGui.QKeySequence.Copy, "copy")
        self.act_paste = self._act("Paste", self._paste_command, QtGui.QKeySequence.Paste, "paste")
        self.act_dup = self._act("Duplicate", self.duplicate_selected, "Ctrl+D", "duplicate")
        # node-aware: with nodes of a line / pipe / polygon selected, Delete
        # deletes those nodes (help 1172187)
        self.act_del = self._act("Delete", self._delete_command, QtGui.QKeySequence.Delete, "delete")
        # CtDraw also deletes with a minus key (help 1172189). Not by default here: a slip
        # meant to zoom out would delete the selection. Settings > Keyboard can give
        # Delete the minus keys; they then work on the canvas only (in a list '-' is the
        # list's own: a tree collapses its row)
        self.act_del.setProperty(_keymap.CANVAS_KEYS_PROP, list(_keymap.BUILT_IN_CANVAS_KEYS["act_del"]))
        self.act_sel_all = self._act("Select All", self.select_all, "Ctrl+A")
        # Edit > Select (window/canvas_edits.py): pure selection, the page never changes
        self.act_deselect = self._act("Deselect All", self.deselect_all)
        self.act_invert_sel = self._act("Invert Selection", self.invert_selection, "Ctrl+Shift+I")
        self.act_select_same_element = self._act(
            "Library Element", lambda: self.select_same("element"), "Ctrl+Alt+A"
        )
        self.act_select_same_kind = self._act("Kind", lambda: self.select_same("kind"))
        self.act_select_same_fill = self._act("Fill Colour", lambda: self.select_same("fill"))
        self.act_select_same_line_colour = self._act("Line Colour", lambda: self.select_same("line_colour"))
        self.act_select_same_line_width = self._act("Line Width", lambda: self.select_same("line_width"))
        self.act_select_same_font = self._act("Font and Size", lambda: self.select_same("font"))
        self.act_select_same_size = self._act("Size", lambda: self.select_same("size"))
        self.act_select_same_anim = self._act("Animation Expression", lambda: self.select_same("anim"))
        self.select_same_actions = [
            self.act_select_same_element,
            self.act_select_same_kind,
            self.act_select_same_fill,
            self.act_select_same_line_colour,
            self.act_select_same_line_width,
            self.act_select_same_font,
            self.act_select_same_size,
            self.act_select_same_anim,
        ]
        for _a in self.select_same_actions:
            _a.setProperty(_keymap.KEYWORDS_PROP, ["select same", "similar"])
        self.act_select_tag = self._act("Select Objects Using Tag…", lambda: self.select_using_tag())
        self.act_select_previous = self._act("Select Previous", self.select_previous, "Ctrl+Alt+Shift+A")

        # keyboard / menu zoom keeps the view centre (the wheel anchors under
        # the mouse); they step through round levels and glide (ui/editor/viewnav.py)
        self.act_zoom_in = self._act("Zoom In", self._zoom_in_cmd, None, "zoom_in")
        self.act_zoom_out = self._act("Zoom Out", self._zoom_out_cmd, None, "zoom_out")
        # keys of every keyboard layout: the plus key (Nordic, German) and the keypad too
        _keypad = QtCore.Qt.KeypadModifier | QtCore.Qt.ControlModifier
        self.act_zoom_in.setShortcuts(
            [
                QtGui.QKeySequence("Ctrl+="),
                QtGui.QKeySequence(QtGui.QKeySequence.ZoomIn),
                QtGui.QKeySequence("Ctrl+Shift+="),
                QtGui.QKeySequence(QtCore.QKeyCombination(_keypad, QtCore.Qt.Key_Plus)),
            ]
        )
        self.act_zoom_out.setShortcuts(
            [
                QtGui.QKeySequence("Ctrl+-"),
                QtGui.QKeySequence(QtCore.QKeyCombination(_keypad, QtCore.Qt.Key_Minus)),
            ]
        )
        self.act_zoom_sel = self._act("Zoom to Selection", self._zoom_selection, "Ctrl+2")
        self.act_zoom_fit = self._act("Fit Page", self.view.fit_page, "Ctrl+0", "zoom_fit")
        self.act_zoom_100 = self._act("Zoom 100%", self.view.reset_zoom, "Ctrl+1")
        # the page and everything drawn around it (off-page objects)
        self.act_zoom_all = self._act("Fit All Objects", self.view.fit_all, "Ctrl+3")
        # navigation (window/navigation.py): back and forward through the views, the
        # rulers, the navigator, saved views
        self.act_prev_view = self._act("Previous View", self._prev_view, "Alt+Left")
        self.act_prev_view.setToolTip("Back to the view before the last zoom or pan")
        self.act_next_view = self._act("Next View", self._next_view, "Alt+Right")
        self.act_next_view.setToolTip("Forward to the view Previous View left")
        st_nav = app_settings()
        self.act_rulers = self._act("Rulers", self._toggle_rulers, "Ctrl+R", checkable=True)
        self.act_rulers.setChecked(st_nav.value("view/rulers", True, type=bool))
        self.act_navigator = self._act("Navigator", self._toggle_navigator, "Ctrl+Shift+N", checkable=True)
        self.act_navigator.setChecked(st_nav.value("view/navigator", True, type=bool))
        self.act_navigator.setToolTip("A small map of the page at the bottom right of the canvas")
        self.act_save_view = self._act("Save Current View…", self._save_view_dialog)
        self.act_save_view.setStatusTip("Name the zoom and position shown, to come back to them")
        self.act_manage_views = self._act("Manage Saved Views…", self._manage_saved_views)
        self.act_clear_guides = self._act("Clear All Guidelines", self._clear_guidelines)
        # View > Outside the Page: what the canvas shows around the page
        from .prefs.settings import SURROUND_LABELS, surround_mode

        self._surround_group = QtGui.QActionGroup(self)
        self.surround_actions = {}
        for _mode, _label in SURROUND_LABELS:
            _sa = self._act(_label, lambda _=False, m=_mode: self.set_surround_mode(m), checkable=True)
            self._surround_group.addAction(_sa)
            self.surround_actions[_mode] = _sa
            _sa.setObjectName("surround." + _mode)
            _sa.setStatusTip("Around the page: %s." % (_label[0].lower() + _label[1:]))
        self.set_surround_mode(surround_mode(), store=False)
        self.act_grid = self._act("Show Grid", self._toggle_grid, None, "grid", checkable=True)
        self.act_grid.setChecked(self._show_grid)
        # View > Grid > Pixel Grid: a line at every page pixel from 800 % (with or
        # without the grid; editor-only)
        self.act_pixel_grid = self._act("Pixel Grid", self._toggle_pixel_grid, checkable=True)
        self.act_pixel_grid.setToolTip("At 800 % and more, a faint line at every pixel")
        self.act_pixel_grid.setChecked(app_settings().value("view/pixel_grid", True, type=bool))
        # CtDraw's exact bindings: Snap to Grid F8, Snap to Guidelines F7
        self.act_snap = self._act("Snap to Grid", self._toggle_snap, "F8", "snap", checkable=True)
        self.act_snap.setChecked(True)
        self._build_view_actions()
        self._build_file_and_drawing_actions()
        self._build_picture_and_library_actions()
        self._build_object_actions()

    def _build_view_actions(self) -> None:
        from .prefs import keymap as _keymap

        self.act_guidelines = self._act("Guidelines Setup…", self._guidelines_dialog)
        self.act_snap_guides = self._act("Snap to Guidelines", self._toggle_snap_guides, "F7", checkable=True)
        self.act_snap_guides.setChecked(True)
        self.act_show_guides = self._act("Display Guidelines", self._toggle_show_guides, checkable=True)
        self.act_show_guides.setChecked(True)
        # (the slot says in the status bar what the arrow keys do now)
        self.act_slow_keys = self._act("Cursor Keys Slow Speed", self._slow_keys_toggled, checkable=True)
        self.act_slow_mouse = self._act(
            "Mouse Slow Speed",
            lambda: self.view.set_slow_mouse(self.act_slow_mouse.isChecked()),
            checkable=True,
        )
        self.act_smart_guides = self._act(
            "Smart Guides",
            lambda: self.view.set_smart_guides(self.act_smart_guides.isChecked()),
            None,
            "smart_guides",
            checkable=True,
        )
        self.act_smart_guides.setChecked(True)
        self.act_smart_guides.setToolTip(
            "While you drag, snap the object's edges and centre to other objects "
            "and the page (hold Alt to drag freely)"
        )
        self.act_isolate = self._act("Isolate Selection", self.isolate_selection, "Ctrl+Alt+I")
        self.act_isolate.setStatusTip(
            "Hide every object but the selected ones while you edit "
            "them; again, or Show All, brings the rest back"
        )
        self.act_show_all_roots = self._act("Show All Hidden Objects", self._show_all_roots)
        self.act_mark_changes = self._act("Mark Changes Since Save", self._mark_changes, checkable=True)
        self.act_mark_changes.setStatusTip(
            "Mark the objects that are new, changed or removed since the save (kept up to date as you edit)"
        )
        # step through the changes (ui/panels/changes_dock), also with the Changes panel closed
        self.act_next_change = self._act("Next Change", self.next_change, "Alt+F5", "next_change")
        self.act_prev_change = self._act("Previous Change", self.prev_change, "Alt+Shift+F5", "prev_change")
        self.act_select_changed = self._act(
            "Select All Changed", self.select_all_changed, None, "select_changed"
        )
        # the earlier saved versions of the page (ui/dialog/history_versions)
        self.act_page_history = self._act("Page History…", self.page_history, None, "page_history")
        self.act_focus_mode = self._act("Focus Mode", self._toggle_focus_mode, checkable=True)
        self.act_reset_ui = self._act("Reset UI Layout", self._reset_ui_layout)
        self.act_thumb = self._act(
            "Saved Thumbnail Behind the Page", self._toggle_thumb, None, "thumbnail", checkable=True
        )

        self.act_front = self._act("Bring to Front", lambda: self._reorder(self._sel, "front"), None, "front")
        self.act_back = self._act("Send to Back", lambda: self._reorder(self._sel, "back"), None, "back")
        self.act_fwd = self._act(
            "Bring Forward", lambda: self._reorder(self._sel, "forward"), None, "forward"
        )
        self.act_bwd = self._act(
            "Send Backward", lambda: self._reorder(self._sel, "backward"), None, "backward"
        )
        # (the brackets need AltGr on Nordic and German keyboards: Alt + Page Up / Page Down /
        # Home / End work everywhere; the plain keys stay the diagonal nudges)
        for _a, _keys in (
            (self.act_front, ("Ctrl+Shift+]", "Alt+Home")),
            (self.act_back, ("Ctrl+Shift+[", "Alt+End")),
            (self.act_fwd, ("Ctrl+]", "Alt+PgUp")),
            (self.act_bwd, ("Ctrl+[", "Alt+PgDown")),
        ):
            _a.setShortcuts([QtGui.QKeySequence(k) for k in _keys])
        _align_keys = {
            "left": "Alt+Shift+Left",
            "right": "Alt+Shift+Right",
            "top": "Alt+Shift+Up",
            "bottom": "Alt+Shift+Down",
            "hcentre": "Alt+Shift+C",
            "vcentre": "Alt+Shift+M",
        }
        self.align_actions = [
            self._act(label, lambda _=False, m=mode: self._align(m), _align_keys.get(mode), ic)
            for mode, label, ic in _ALIGN
        ]
        _keymap.name_actions(self.align_actions, "align.", [m for m, _l, _i in _ALIGN])
        self.act_dist_h = self._act(
            "Distribute Horizontally", lambda: self._distribute("h"), "Alt+Shift+H", "distribute_h"
        )
        self.act_dist_v = self._act(
            "Distribute Vertically", lambda: self._distribute("v"), "Alt+Shift+V", "distribute_v"
        )
        self.act_same_size = self._act("Make Same Size", self._same_size, None, "same_size")
        # CtDraw's exact bindings: Group Ctrl+Shift+G, Ungroup Ctrl+Shift+U
        # (Ctrl+G is Goto Object)
        self.act_group = self._act("Group Objects", self._group_selection, "Ctrl+Shift+G", "group")
        self.act_ungroup = self._act("Ungroup Objects", self._ungroup_selection, "Ctrl+Shift+U", "ungroup")
        # direct actions, not CtDraw's Rotate / Mirror radio dialogs; CtDraw's keys kept,
        # the other direction on a free key of its own (L = left, V = vertical)
        self.act_rot_cw = self._act("Rotate 90° Clockwise", lambda: self._rotate(True), "Ctrl+Shift+R")
        self.act_rot_ccw = self._act("Rotate 90° Anticlockwise", lambda: self._rotate(False), "Ctrl+Shift+L")
        self.act_flip_h = self._act("Flip Horizontal", lambda: self._flip(True), "Ctrl+Shift+M")
        self.act_flip_v = self._act("Flip Vertical", lambda: self._flip(False), "Ctrl+Shift+V")
        self.act_rotate, self.act_mirror = self.act_rot_cw, self.act_flip_h
        self.act_adjust = self._act("Adjust Position and Size…", self._adjust_dialog, "Ctrl+Shift+P")
        # CtDraw's Align dialog (both directions in one step), next to the six
        # direct align commands
        self.act_align_dialog = self._act("Align…", self._align_dialog, "Ctrl+Shift+A")
        self.act_space_equally = self._act(
            "Space Equally…", self._space_equally_dialog, None, "space_equally"
        )
        self.act_space_equally.setToolTip("Equal gaps between the selected objects' edges")
        self.act_arrange_grid = self._act("Arrange in Grid…", self._arrange_grid_dialog, None, "arrange_grid")
        self.act_arrange_grid.setToolTip("Lay the selected objects out in columns and rows")
        self.act_align_dialog.setToolTip("Align the selection vertically and horizontally in one step")
        self.act_goto = self._act("Goto Object…", self._goto_dialog, "Ctrl+G")
        # the object's name (Access > General > Name): one in the Objects panel (F2 there),
        # several by a pattern (window/view.py)
        self.act_rename = self._act("Rename…", self.rename_selection, None, "rename")
        self.act_rename_objects = self._act(
            "Rename Objects…", self.rename_objects_dialog, "Ctrl+Alt+N", "rename"
        )
        self.act_find = self._act("Find…", lambda: self._find_dialog(False), "Ctrl+F")
        self.act_replace = self._act("Replace…", lambda: self._find_dialog(True), "Ctrl+H")
        self.act_lock = self._act("Lock Object", self._lock_selection)
        self.act_unlock = self._act("Unlock Object", self._unlock_selection)
        self.act_lock_toggle = self._act("Lock or Unlock Selection", self._lock_toggle, "Ctrl+L")
        self.act_unlock_all = self._act("Unlock All Objects", self._unlock_all, "Ctrl+Alt+L")
        self.act_hide_toggle = self._act(
            "Hide or Show Selection", self._toggle_hidden_selection, "Ctrl+Shift+H"
        )
        self.act_break_lock = self._act("Break Lock Mode", self._break_locks)
        self.act_break_lock.setCheckable(True)

    def _build_file_and_drawing_actions(self) -> None:
        from .prefs import keymap as _keymap

        self.act_print = self._act("Print…", self._print_page, "Ctrl+P")
        self.act_export_page = self._act("Export Page…", self._export_page)
        self.act_export_page.setStatusTip("Save a picture of the page as a PNG, SVG or PDF file")
        self.act_print_setup = self._act("Print Setup…", self._print_setup)
        self.act_grid_setup = self._act("Grid Setup…", self._grid_setup_dialog)
        # one name for the magnifier (the Graphics Builder's 'Show Zoom': a
        # palette keyword)
        self.act_zoom_window = self._act("Magnifier", self._toggle_zoom_window, "F10", checkable=True)
        self.act_zoom_window.setProperty(_keymap.KEYWORDS_PROP, ["show zoom", "zoom window", "loupe"])
        self.act_import = self._act("Import…", self._import_image)
        self.act_import_flash = self._act("Import As Flashing…", self._import_flashing)
        self.act_paste_flash = self._act("Paste Symbol As Flashing…", self._paste_symbol_flashing)
        # CtDraw's Edit > Paste Symbol / Paste Genie / Paste Super Genie: pick
        # the element from the include chain, then click where it goes
        self.act_paste_symbol = self._act("Paste Symbol…", lambda: self._paste_library_dialog("symbol"), "F6")
        self.act_paste_genie = self._act("Paste Genie…", lambda: self._paste_library_dialog("genie"), "F11")
        self.act_paste_super = self._act(
            "Paste Super Genie…", lambda: self._paste_library_dialog("supergenie"), "Ctrl+F11"
        )
        self.act_paste_special = self._act("Paste Special…", self._paste_special)
        # the format painter and the named styles (window/commands.py)
        self.act_copy_appearance = self._act("Copy Appearance", self.copy_appearance, "Ctrl+Alt+C")
        self.act_copy_appearance.setStatusTip(
            "Keep the selected object's line, fill, 3D, font and colours for Paste Appearance"
        )
        self.act_paste_appearance = self._act("Paste Appearance", self.paste_appearance, "Ctrl+Alt+V")
        self.act_paste_appearance.setStatusTip("Give the selected objects the copied appearance")
        self.act_save_style = self._act("Save Appearance as Style…", lambda: self.save_style())
        self.act_manage_styles = self._act("Manage Styles…", self.manage_styles)
        # (its text names the selected object's kind: 'for New Rectangles')
        self.act_style_default = self._act(
            "Use Selection as Default for New Objects", self.style_selection_as_default, None, "style_default"
        )
        from . import help as _style_help

        for a in (self.act_save_style, self.act_manage_styles, self.act_style_default):
            _style_help.set_help(a, "colours#named-styles")
        self._appearance = None
        self.act_to_bitmap = self._act("Convert to Bitmap", self._convert_to_bitmap)
        self.act_options = self._act("Settings…", self._options_dialog, "Ctrl+,", "settings")
        # (the command palette finds it by these words too)
        self.act_options.setProperty(_keymap.KEYWORDS_PROP, ["preferences", "options", "configure"])
        self.act_crosshair = self._act("Cross Hair Cursor", self._toggle_crosshair, checkable=True)
        self.act_crosshair.setToolTip("Lines through the pointer across the whole drawing area")
        self.act_selection_hud = self._act("Selection Toolbar", self._toggle_selection_hud, checkable=True)
        self.act_selection_hud.setToolTip(
            "A small toolbar next to the selected objects "
            "(Properties, Group, Bring to Front, Send to Back, "
            "Delete)"
        )
        self.act_selection_hud.setChecked(True)
        self.act_status_bar = self._act("Show Status Bar", self._toggle_status_bar, checkable=True)
        self.act_status_bar.setChecked(True)
        # the messages of this window again (the status bar's bell: ui/notify.py)
        self.act_messages = self._act("Messages", self._show_messages, icon_name="bell")
        self.act_refresh = self._act("Refresh", self._refresh_view)
        self.act_refresh.setToolTip("Redraw the page, reading the libraries it uses from disk again")
        self.act_show_tools = self._act("Show Tools", self._toggle_toolbox, "F3", checkable=True)
        self.act_show_tools.setToolTip("The floating drawing toolbox")
        self.act_cascade = self._act("Cascade", self._cascade_windows)
        self.act_tile_h = self._act("Tile Horizontally", lambda: self._tile_windows(True))
        self.act_tile_v = self._act("Tile Vertically", lambda: self._tile_windows(False))
        self.act_save_all = self._act("Save All", self.save_all)
        self.act_close_doc = self._act("Close Page", self.close_current_document)
        # CtDraw's File > Close is Ctrl+F4
        self.act_close_doc.setShortcuts([QtGui.QKeySequence("Ctrl+W"), QtGui.QKeySequence("Ctrl+F4")])
        self.act_revert = self._act("Revert to Saved…", lambda: self.revert_document())
        # the document tabs (window/tabs.py): the tab menu's commands act on the tab it was
        # opened on, else the current one
        self.act_close_others = self._act("Close Others", self.close_other_tabs)
        self.act_close_right = self._act("Close to the Right", self.close_tabs_to_the_right)
        self.act_close_saved = self._act("Close Saved", self.close_saved_tabs)
        self.act_close_all = self._act("Close All Pages", self.close_all_tabs, "Ctrl+Shift+W")
        for _a in (self.act_close_others, self.act_close_right, self.act_close_saved, self.act_close_all):
            _a.setStatusTip("Pinned tabs stay open")
        self.act_reopen_closed = self._act("Reopen Closed Page", self.reopen_closed_tab, "Ctrl+Shift+T")
        self.act_reopen_closed.setStatusTip("Open the page you closed last again, at the zoom it had")
        self.act_copy_page_path = self._act("Copy Path", self.copy_tab_path)
        self.act_copy_page_name = self._act("Copy Name", self.copy_tab_name)
        self.act_reveal_page = self._act("Show in Explorer", self.reveal_tab)
        self.act_show_in_browser = self._act("Show in Page Browser…", self.show_tab_in_browser)
        self.act_pin_tab = self._act("Pin Tab", self.toggle_pin_tab)
        self.act_pin_tab.setStatusTip("Keep the tab at the left. Close All and Close Others leave it open")
        self.act_move_to_window = self._act("Move to New Window", self.move_tab_to_new_window)
        self.act_move_to_window.setStatusTip(
            "Move the page, with its undo history and windows, to a new editor window"
        )
        self.act_next_page = self._act("Next Page", lambda: self.switch_page(1))
        self.act_next_page.setShortcuts([QtGui.QKeySequence("Ctrl+Tab"), QtGui.QKeySequence("Ctrl+PgDown")])
        self.act_prev_page = self._act("Previous Page", lambda: self.switch_page(-1))
        self.act_prev_page.setShortcuts(
            [QtGui.QKeySequence("Ctrl+Shift+Tab"), QtGui.QKeySequence("Ctrl+PgUp")]
        )
        self.goto_tab_actions = [
            self._act("Page %d" % n, lambda _=False, k=n - 1: self.go_to_tab(k), "Alt+Shift+%d" % n)
            for n in range(1, 10)
        ]
        for _n, _a in enumerate(self.goto_tab_actions, 1):
            _a.setStatusTip("Show the page in tab %d." % _n)
        self.act_new_from_last = self._act(
            "New Page from the Last Template", self.new_page_from_last_template
        )
        self.act_new_from_last.setStatusTip(
            "A new page in this project from the template File > New used last, without the New window"
        )
        self.act_restore_backup = self._act("Restore Backup…", self._restore_backup_dialog)
        self.act_run = self._act("Run in Plant SCADA", self._run_project, "F5", "run")
        self.act_cut_link = self._act("Cut Link", self._cut_link)
        self.act_swap_colors = self._act("Swap Colours…", self._swap_colors)
        self.act_adjust_colors = self._act("Adjust Colours…", self._adjust_colors)
        self.act_fav_colors = self._act("Edit Favourite Colours…", self._favorite_colors)
        self.act_transparent_paste = self._act("Transparent Paste Colour…", self._transparent_paste_dialog)
        # Tools > Colours > Colours of New Objects (the ribbon's swatch pair: ui/editor/draw_styles)
        self.act_new_fill_colour = self._act(
            "Fill Colour…", lambda: self._colour_popover("fill"), None, "fill_colour"
        )
        self.act_new_line_colour = self._act(
            "Line Colour…", lambda: self._colour_popover("line"), None, "line_colour"
        )
        self.act_new_text_colour = self._act(
            "Text Colour…", lambda: self._colour_popover("text"), None, "text_colour"
        )
        self.act_new_colours_swap = self._act(
            "Swap Fill and Line Colours", self._swap_new_colours, "Shift+X", "swap_new_colours"
        )

    def _build_picture_and_library_actions(self) -> None:
        from .prefs import keymap as _keymap

        # (a capital letter: on the page only, never in a field)
        self.act_new_colours_swap.setShortcutContext(QtCore.Qt.WidgetWithChildrenShortcut)
        self.act_new_colours_swap.setProperty("ctg_canvas_only", True)
        self.act_new_no_fill = self._act("No Fill", self._set_new_no_fill, None, "no_fill", checkable=True)
        self.act_new_colours_auto = self._act(
            "Contrast with the Page", self._set_new_colours_auto, None, "contrast", checkable=True
        )
        from . import help as _style_help

        for a in (
            self.act_new_fill_colour,
            self.act_new_line_colour,
            self.act_new_text_colour,
            self.act_new_colours_swap,
            self.act_new_no_fill,
            self.act_new_colours_auto,
        ):
            _style_help.set_help(a, "drawing#the-look-of-new-objects")
        self.act_bitmap_editor = self._act("Bitmap Editor", self._bitmap_editor, "F9")
        self.act_replace_picture = self._act("Replace Picture…", self._replace_picture)
        self.act_replace_picture.setStatusTip(
            "Put another picture file in the selected picture's place; it keeps its size, "
            "animations and commands"
        )
        self.act_export_picture = self._act("Export Picture…", self._export_picture)
        self.act_export_picture.setStatusTip("Save the selected picture as a file")
        self.act_resample_picture = self._act("Resample Picture to Its Size", self._resample_picture)
        self.act_resample_picture.setStatusTip(
            "Store a stretched bitmap at the size it has on the page, scaled smoothly"
        )
        self.act_dup_pictures = self._act("Find Duplicate Pictures…", self._find_duplicate_pictures)
        self.act_dup_pictures.setStatusTip(
            "Find the pictures the project's pages store more than once, and make each a "
            "library symbol or replace it everywhere"
        )
        self.act_copy_to_lib = self._act("Copy to Library…", self._copy_to_library)
        # CtDraw's Hot Spot Location (X / Y) -- the element window's hot spot; the
        # marker can also be dragged on the canvas
        self.act_hotspot = self._act("Hot Spot Location…", self._hotspot_dialog)
        self.act_used_by = self._act("Used By…", self._used_by_current)
        self.act_compose_nested = self._act("Compose Nested Genies Again", self._compose_nested_again)
        # everything that uses it, directly or through other genies, brought up to date
        # in order (io/genieusage)
        self.act_update_users = self._act("Update Users…", self._update_users_current)
        self.act_update_users.setToolTip(
            (
                "Save the genies that use this element again, nested ones first, then update the pages "
                "that use it"
            )
        )
        # Paste Symbol linked (CtDraw's PasteLibrary_Linked / _Unlinked): one
        # setting shared by the Library panel toggle and the symbol pickers
        self.act_paste_linked = self._act("Paste Symbols Linked", self._toggle_paste_linked)
        self.act_paste_linked.setCheckable(True)
        self.act_paste_linked.setChecked(paste_linked())
        self.act_attach_super = self._act("Attach Super Genies…", self._attach_super_genies)
        self.act_genie_form = self._act("Genie Form…", self._edit_genie_form)
        self.act_replace_genie = self._act("Replace Genie…", self._replace_genie)
        self.act_genie_form.setToolTip("Design the genie's form: the dialog its parameters are entered in")
        self.act_paste_composite = self._act("Paste Composite Genie…", self._paste_composite_genie)
        # named for what it is (the Graphics Builder's 'Variable Tags': a palette
        # keyword)
        self.act_var_tags = self._act("Tags Panel", self._variable_tags, "Ctrl+T")
        self.act_var_tags.setProperty(_keymap.KEYWORDS_PROP, ["variable tags", "tag list"])
        self.act_pack_libs = self._act("Pack Libraries…", self._pack_libraries_dialog)
        self.act_update_pages = self._act("Update Pages…", self._update_pages)
        self.act_rename_pages = self._act(
            "Rename Pages…", lambda: self._rename_pages_dialog(), None, "rename"
        )
        self.act_close_builder = self._act("Close Graphics Builder", self._close_graphics_builder)
        self.act_update_page_template = self._act("Update Template on This Page", self._update_template_here)
        self.act_edit_template = self._act("Edit Page Template", self._edit_page_template)
        self.act_cut_template_link = self._act("Cut Link to Template…", self._cut_template_link)
        self.act_page_defaults = self._act("Page Defaults…", self._page_defaults_dialog)
        # Situational Awareness authoring aids (editor-only: never in the page)
        self.act_check_page = self._act(
            "Check Page", lambda: self._check_page(show=True), "Ctrl+Shift+K", "problems"
        )
        self.act_check_page.setToolTip(
            (
                "Find problems on this page: unknown tags, expressions that do not compile, buttons "
                "without a command, objects off the page, captions wider than their button, missing "
                "library elements and duplicate keys"
            )
        )
        self.act_check_workspace = self._act("Check Workspace", lambda: self._check_workspace(show=True))
        self.act_check_workspace.setToolTip(
            "Check the Situational Awareness panes of this page: names, the default pane, "
            "Workspace_Init / HasPanes, default pages and tab bars"
        )
        self.act_check_display = self._act("Check Page Display", lambda: self._check_page_display())
        self.act_check_display.setToolTip(
            "Read this page again in a separate process and compare its drawing with this window"
        )
        self.act_simulate = self._act("Simulate Page", self._simulate_page, "Ctrl+F5", "simulate")
        self.act_simulate.setToolTip(
            "Show this page running in the Simulate window, with its tags to change and its "
            "output. Nothing is saved or compiled, and Plant SCADA is not started"
        )
        self.act_pane_labels = self._act("Pane Labels", self._toggle_pane_labels, checkable=True)
        self.act_pane_labels.setToolTip(
            "Show each Situational Awareness pane's name, fill "
            "mode and default page on the canvas (never saved "
            "or printed)"
        )
        self.act_pane_labels.setChecked(app_settings().value("view/pane_labels", True, type=bool))
        self.scene.set_pane_labels_visible(self.act_pane_labels.isChecked())
        self.act_aliased_text = self._act("Text Without Smoothing", self._toggle_aliased_text, checkable=True)
        self.act_aliased_text.setToolTip(
            "Draw the page's text unsmoothed, the way the Graphics Builder and the runtime draw it"
        )
        self.act_aliased_text.setChecked(app_settings().value("view/aliased_text", False, type=bool))
        self.view.set_aliased_text(self.act_aliased_text.isChecked())

        # shared actions (one QAction reused by menus and the ribbon)
        self.act_page_props = self._act("Page Properties…", self.page_properties)
        # Trend + Cicode Object are in CtDraw 8.5's normal toolbox -- drawing
        # TOOLS (drag a trend, click a Cicode object; the sheet opens after);
        # the others need 'Show version 3.xx/4.xx tools' (Settings)
        self.special_tool_actions: dict[str, QtGui.QAction] = {}
        self.v34_actions = []
        for kind, label in _V34_KINDS:
            if kind in _SPECIAL_TOOLS:
                a = self._act(
                    label,
                    lambda _=False, t=kind: self._set_tool(t),
                    None,
                    _SPECIAL_TOOLS[kind],
                    checkable=True,
                )
                self.special_tool_actions[kind] = a
            else:
                a = self._act(
                    label, lambda _=False, k=kind: self._create_legacy(k), None, V34_ICONS.get(kind)
                )
                a.setObjectName("legacy." + kind)
            self.v34_actions.append(a)

    def _build_object_actions(self) -> None:
        from .prefs import keymap as _keymap

        self.modern_v34_actions = [
            a for (k, _l), a in zip(_V34_KINDS, self.v34_actions) if k in ("trend", "cicode_object")
        ]
        self.legacy_actions = [a for a in self.v34_actions if a not in self.modern_v34_actions]
        # an Insert entry only for a kind the factory can make (a control: one the install's
        # Example project holds); the Web Content / Process Analyst / Scheduler / ... entries
        # are TOOLS (drag the box, the sheet opens), Insert ActiveX Control first asks which
        # control, and is always there (it lists the controls the project's pages use too)
        from ..format import factory as _factory

        _have = set(_factory.available_kinds()) | {"activex"}
        self.insert_actions = []
        #: (kind, action) of each Insert entry (the command names of _command_actions)
        self.insert_kinds = []
        for kind, label in _INSERT_KINDS:
            if kind not in _have:
                continue
            if kind == "activex":
                a = self._act(label, self._insert_activex_dialog, None, "activex")
                a.setObjectName("insert.activex")
            else:
                a = self._act(
                    label,
                    lambda _=False, t=kind: self._set_tool(t),
                    None,
                    _SPECIAL_TOOLS.get(kind, "activex"),
                    checkable=True,
                )
                self.special_tool_actions[kind] = a
            self.insert_actions.append(a)
            self.insert_kinds.append((kind, a))
        # the ActiveX tool itself (armed by Insert ActiveX Control with the
        # chosen control; no menu entry of its own)
        self.act_activex_tool = self._act(
            "ActiveX Control", lambda: self._set_tool("activex"), None, "activex", checkable=True
        )
        self.special_tool_actions["activex"] = self.act_activex_tool
        # thin launchers onto existing flows
        self.act_genie_params = self._act("Genie Parameters…", self._genie_params_selected)
        self.act_obj_props = self._act("Object Properties…", lambda: self.object_properties())
        # F12 = CtDraw's Tools > Plant SCADA Studio
        self.external_actions = [
            self._act(label, lambda _=False, x=exe: self._launch_external(x), key)
            for label, exe, key in (
                ("Plant SCADA Studio", "CitectIDE.exe", "F12"),
                ("Cicode Editor", "CtCicode.exe", None),
                ("Runtime Manager", "RuntimeManager.exe", None),
                ("Computer Setup Wizard", "CtSetup.exe", None),
                ("Computer Setup Editor", "ComputerSetupEditor.exe", None),
            )
        ]
        _keymap.name_actions(
            self.external_actions,
            "external.",
            ("studio", "cicode_editor", "runtime_manager", "setup_wizard", "setup_editor"),
        )
        self.act_font_dialog = self._act("Font…", self._font_dialog)
        self.act_edit_text = self._act("Edit Text in Place", lambda: self.edit_text_in_place(), "F2")
        self.text_flag_actions = [
            self._act(label, lambda _=False, k=key: self._toggle_font_flag(k), sc)
            for label, key, sc in (
                ("Bold", "bold", "Ctrl+B"),
                ("Italic", "italic", "Ctrl+I"),
                ("Underline", "underline", "Ctrl+U"),
                ("Strikeout", "strikeout", None),
            )
        ]
        _keymap.name_actions(
            self.text_flag_actions, "textflag.", ("bold", "italic", "underline", "strikeout")
        )
        self.justify_actions = [
            self._act(label, lambda _=False, m=mode: self._set_justify(m))
            for label, mode in (
                ("Left Justified", "left"),
                ("Centred", "centre"),
                ("Right Justified", "right"),
            )
        ]
        _keymap.name_actions(self.justify_actions, "justify.", ("left", "centre", "right"))
        self.effect_actions = [
            self._act(label, lambda _=False, n=eff: self._set_text_effect(n))
            for label, eff in (("Normal", 0), ("Shadowed", 3), ("Raised", 1), ("Lowered", 2), ("Embossed", 4))
        ]
        _keymap.name_actions(
            self.effect_actions, "effect.", ("normal", "shadowed", "raised", "lowered", "embossed")
        )

        self._build_window_commands()

        # drawing tools (checkable group)
        self.tool_group = QtGui.QActionGroup(self)
        self.tool_actions: dict[str, QtGui.QAction] = {}
        for tool, label, ic, key in _TOOLS:
            a = self._act(label, lambda _=False, t=tool: self._set_tool(t), key, ic, checkable=True)
            self.tool_group.addAction(a)
            self.tool_actions[tool] = a
        for tool, a in self.special_tool_actions.items():
            self.tool_group.addAction(a)
            self.tool_actions[tool] = a
        self.tool_actions["select"].setChecked(True)
        # View > Collapse the Ribbon (Ctrl+F1): the ribbon's chevron hosts it (_build_ribbon)
        self.act_collapse_ribbon = self._act("Collapse the Ribbon", lambda: None, "Ctrl+F1", checkable=True)
        # View > Underlay (ui/editor/underlay.py)
        self._build_underlay_actions()
        self._apply_command_icons()
        # every command works from the keyboard even when it sits only in a
        # popup / submenu: the window's commands only (a list's own Copy,
        # tables.enable_row_copy, added to the window would take Ctrl+C from the page)
        self.addActions(
            [
                a
                for a in self.findChildren(QtGui.QAction)
                if not a.shortcut().isEmpty()
                and a.shortcutContext()
                not in (QtCore.Qt.WidgetShortcut, QtCore.Qt.WidgetWithChildrenShortcut)
            ]
        )
        # every command's stable id (its object name: the keyboard map, the palette's
        # recent commands, My Commands, help links)
        _keymap.assign_command_ids(self)

    def _build_window_commands(self) -> None:
        """The commands of the File / Window / Help menus, the command palette and the
        panel keys (thin launchers: each one also sits in a menu)."""
        from .prefs import keymap as _keymap
        from .panels import panel_nav

        self.act_exit = self._act("E&xit", self.close, None, "exit")
        self.act_new_window = self._act("New Window", self._new_window, None, "new_window")
        # the ctgedit manual (help/pages); F1 is routed application-wide by
        # ui/help.install, so the menu only shows the key
        self.act_help = self._act("ctgedit Help", lambda: self.show_help())
        # (a real key, not text in the label: the tool tip showed a tab; F1 on the window
        # itself opens the manual, the help filter answers it on a dialog)
        self.act_help.setShortcut(QtGui.QKeySequence("F1"))
        self.act_help.setShortcutContext(QtCore.Qt.WindowShortcut)
        # the key opens the section of the command button under the mouse first (its tip
        # says 'F1 for help'); a click on the menu item opens the context's
        from . import help as _help

        self._help_key = _help.KeyTrigger(self.act_help, self.help_for_key)
        from .style.theme import icon as _icon

        self.act_help.setIcon(_icon("help"))  # (the app's glyph, as every command)
        self.act_getting_started = self._act(
            "Getting Started", lambda: self.show_help("getting-started"), None, "help"
        )
        self.act_shortcuts_window = self._act(
            "Keyboard Shortcuts", self.show_shortcuts_window, "Ctrl+/", "keyboard"
        )
        self.act_shortcuts_window.setToolTip("Search the keys of every command, print them or change them")
        self.act_differences = self._act(
            "Coming from the Graphics Builder", lambda: self.show_help("differences"), None, "help"
        )
        self.act_troubleshooting = self._act(
            "Troubleshooting", lambda: self.show_help("troubleshooting"), None, "problems"
        )
        self.act_cicode_ref = self._act(
            "Cicode Function Reference (AVEVA)", self._open_cicode_reference, None, "cicode_editor"
        )
        self.act_open_logs = self._act("Open Log Folder", self._open_log_folder)
        self.act_copy_diag = self._act("Copy Diagnostics", self._copy_diagnostics)
        self.act_copy_diag.setToolTip(
            "Copy the version, the open documents and the end of "
            "the logs to the clipboard, for a problem report"
        )
        self.act_check_updates = self._act("Check for Updates…", self._check_updates, None, "import")
        self.act_about = self._act("About ctgedit…", self._about, None, "help")
        # the command palette: Ctrl+K anywhere; at the pointer with
        # Shift+S (and S in the ctgedit keymap: Symbol is then A) on the canvas
        self.act_palette = self._act("Command Palette…", lambda: self.palette_popup.open_at(), "Ctrl+K")
        self.act_palette.setProperty(_keymap.KEYWORDS_PROP, ["commands", "search", "find"])
        self.act_palette_here = self._act(
            "Command Palette at the Pointer",
            lambda: self.palette_popup.open_at(QtGui.QCursor.pos(), toolbox=True),
            "Shift+S",
        )
        self.act_palette_here.setShortcutContext(QtCore.Qt.WidgetWithChildrenShortcut)
        self.act_palette_here.setProperty("ctg_canvas_only", True)
        # View > Panels: jump to a panel and back to the page (ui/panels/panel_nav)
        self.panel_go_actions = []
        for k, (name, label, _dock) in enumerate(panel_nav.PANELS):
            a = self._act(label, lambda _=False, n=name: panel_nav.go_to(self, n), "Alt+%d" % k)
            setattr(self, "act_go_" + name, a)
            self.panel_go_actions.append(a)
        self.act_next_panel = self._act("Next Panel", lambda: panel_nav.cycle(self, 1), "Ctrl+F6")
        self.act_prev_panel = self._act("Previous Panel", lambda: panel_nav.cycle(self, -1), "Ctrl+Shift+F6")
        self.act_next_panel.setStatusTip(
            "Move the keyboard to the page, the Properties panel, "
            "the panel at the left and the page tabs in turn"
        )

    def apply_keymap(self) -> list:
        """Every command's keys from the keymap (ui/prefs/keymap.apply_keymap): at the start, and
        again when Settings or an import changed them."""
        from .prefs import keymap as _keymap

        return _keymap.apply_keymap(self)

    def show_shortcuts_window(self) -> None:
        """Help > Keyboard Shortcuts: the searchable list of every key (modeless)."""
        from .dialog.shortcuts_window import ShortcutsWindow

        existing = self._dialogs.get("shortcuts")
        dlg = existing if existing is not None else ShortcutsWindow(self)
        self._show_modeless(dlg, "shortcuts", scope="window", title="Keyboard Shortcuts")

    def _apply_command_icons(self) -> None:
        """An icon for every command (glyphs_cmd.ACTION_ICONS / LIST_ICONS), set on the
        QAction itself: the menus, the palette, the ribbon and the selection toolbar show
        the same one."""
        from .style.glyphs_cmd import ACTION_ICONS, LIST_ICONS

        for attr, name in ACTION_ICONS.items():
            a = getattr(self, attr, None)
            if isinstance(a, QtGui.QAction):
                a.setIcon(icon(name))
        for attr, names in LIST_ICONS.items():
            for a, name in zip(getattr(self, attr, ()) or (), names):
                a.setIcon(icon(name))

    def _command_actions(self):
        """(command name, QAction) of every command with a name in DESCRIPTIONS' scheme:
        'act_x', 'tool.<id>', 'align.<mode>', 'textflag.<key>', 'justify.<mode>',
        'effect.<n>', 'external.<n>', 'insert.<kind>', 'v34.<kind>'."""
        for key, value in vars(self).items():
            if key.startswith("act_") and isinstance(value, QtGui.QAction):
                yield key, value
        for tool, a in self.tool_actions.items():
            yield "tool." + tool, a
        for (mode, _l, _i), a in zip(_ALIGN, self.align_actions):
            yield "align." + mode, a
        for kind, a in zip(("bold", "italic", "underline", "strikeout"), self.text_flag_actions):
            yield "textflag." + kind, a
        for kind, a in zip(("left", "centre", "right"), self.justify_actions):
            yield "justify." + kind, a
        for kind, a in zip(("normal", "shadowed", "raised", "lowered", "embossed"), self.effect_actions):
            yield "effect." + kind, a
        for kind, a in zip(
            ("studio", "cicode_editor", "runtime_manager", "setup_wizard", "setup_editor"),
            self.external_actions,
        ):
            yield "external." + kind, a
        # Insert ActiveX Control (the dialog) and the old V3 / V4 kinds; the ones that are
        # drawing tools came as 'tool.<id>'
        tools = {id(a) for a in self.tool_actions.values()}
        for kind, a in self.insert_kinds:
            if id(a) not in tools:
                yield "insert." + kind, a
        for (kind, _label), a in zip(_V34_KINDS, self.v34_actions):
            if id(a) not in tools:
                yield "v34." + kind, a

    def _describe_actions(self) -> None:
        """Each command's sentence (DESCRIPTIONS: its tool tip and status bar line), what it
        needs while greyed (ViewState.ENABLE_WHEN, NEEDS_CUSTOM) and the help topic of each
        drawing tool and Insert entry (F1 over its button)."""
        from . import help as _help
        from .style import tips
        from .style.tips import DESCRIPTION_PROP, NEEDS_PROP

        rule_of = {n: rule for rule, names in self.ENABLE_WHEN.items() for n in names}
        for name, a in self._command_actions():
            text = DESCRIPTIONS.get(name)
            if text:
                a.setProperty(DESCRIPTION_PROP, text)
                if not a.statusTip():
                    a.setStatusTip(text)
            elif not a.statusTip():
                # a command whose only sentence is its tool tip (a menu never
                # shows one) says it in the status bar too
                own = tips.own_tip(a)
                if own:
                    a.setStatusTip(own)
            family = name.split(".", 1)[0]
            need = NEEDS_CUSTOM.get(name) or rule_of.get(name) or NEEDS_CUSTOM.get(family)
            if need:
                a.setProperty(NEEDS_PROP, need)
                tips.show_need_in_status_tip(a)  # (a greyed menu item says why)
            if family in ("tool", "insert"):
                topic = TOOL_HELP.get(name.split(".", 1)[1])
                if topic and not _help.help_of(a):
                    _help.set_help(a, topic)
        self._describe_submenus(self.menuBar())

    @staticmethod
    def _describe_submenus(bar) -> None:
        """Each submenu of the menu bar has a status bar line (SUBMENU_DESCRIPTIONS,
        by its title); a submenu rebuilt later sets its own (the styles menu)."""
        from .style.tips import command_name

        for a in bar.actions():
            m = a.menu()
            if m is None:
                continue
            text = SUBMENU_DESCRIPTIONS.get(command_name(a))
            if text and not a.statusTip():
                a.setStatusTip(text)
            MainWindow._describe_submenus(m)

    def _slow_keys_toggled(self) -> None:
        """Tools > Advanced > Cursor Keys Slow Speed: say what the arrow keys do now (the
        Settings step is not used while it is on)."""
        from .notify import notify

        if self.act_slow_keys.isChecked():
            notify(
                self,
                "The arrow keys move the selection 1 px per press while Cursor Keys "
                "Slow Speed is on. The arrow key step in Settings is not used.",
                "info",
            )
        else:
            notify(
                self,
                "The arrow keys move the selection %d px per press again." % max(1, int(self._nudge_step)),
                "info",
            )

    def _build_menus(self) -> None:
        """The lean default surface: every command once,
        grouped by task; rarely-needed CtDraw-parity commands under Tools >
        Advanced; V3/V4 creation tools only with 'Show version 3.xx/4.xx
        tools'; diagnostics only with 'Developer / diagnostic tools'."""
        m = self.menuBar().addMenu("&File")
        for a in (self.act_new, self.act_new_from_last, self.act_open, self.act_quick_open):
            m.addAction(a)
        self._mru_menu = m.addMenu("Recent Pages")
        self._mru_menu.aboutToShow.connect(self._fill_mru)
        for a in (
            self.act_save,
            self.act_save_as,
            self.act_save_all,
            self.act_revert,
            self.act_page_history,
            self.act_close_doc,
            self.act_close_all,
        ):
            m.addAction(a)
        m.addSeparator()
        for a in (
            self.act_copy_page_path,
            self.act_copy_page_name,
            self.act_reveal_page,
            self.act_show_in_browser,
        ):
            m.addAction(a)
        m.addSeparator()
        m.addAction(self.act_page_props)
        m.addAction(self.act_page_defaults)
        m.addSeparator()
        m.addAction(self.act_import)
        m.addAction(self.act_import_flash)  # CtDraw: File > Import As Flashing
        self._import_recent_menu = m.addMenu("Import Recent")
        self._import_recent_menu.aboutToShow.connect(self._fill_import_recent)
        m.addAction(self.act_export_picture)
        m.addAction(self.act_export_page)
        m.addSeparator()
        m.addAction(self.act_compile)
        m.addAction(self.act_run)
        m.addSeparator()
        m.addAction(self.act_print)
        m.addAction(self.act_print_setup)
        m.addSeparator()
        m.addAction(self.act_exit)

        e = self.menuBar().addMenu("&Edit")
        for a in (self.act_undo, self.act_redo, self.act_repeat):
            e.addAction(a)
        e.addSeparator()
        for a in (
            self.act_cut,
            self.act_copy,
            self.act_paste,
            self.act_paste_special,
            self.act_paste_symbol,
            self.act_paste_flash,
            self.act_paste_genie,
            self.act_paste_super,
            self.act_dup,
            self.act_del,
            self.act_sel_all,
        ):
            e.addAction(a)
        sel = e.addMenu("Select")
        for a in (self.act_deselect, self.act_invert_sel):
            sel.addAction(a)
        sel.addSeparator()
        same = sel.addMenu("Select Same")
        for a in self.select_same_actions:
            same.addAction(a)
        sel.addAction(self.act_select_tag)
        sel.addAction(self.act_select_previous)
        e.addSeparator()
        e.addAction(self.act_copy_appearance)
        e.addAction(self.act_paste_appearance)
        self.styles_menu = e.addMenu("Styles")
        self.styles_menu.aboutToShow.connect(self._rebuild_styles_menu)
        self._rebuild_styles_menu()
        e.addSeparator()
        for a in (self.act_find, self.act_replace, self.act_goto):
            e.addAction(a)
        e.addSeparator()
        e.addAction(self.act_rename)
        e.addAction(self.act_rename_objects)
        e.addSeparator()
        for a in (
            self.act_lock_toggle,
            self.act_lock,
            self.act_unlock,
            self.act_unlock_all,
            self.act_break_lock,
            self.act_hide_toggle,
        ):
            e.addAction(a)
        e.addSeparator()
        e.addAction(self.act_genie_params)
        e.addAction(self.act_obj_props)
        self._build_insert_to_help_menus(e)

    def _build_insert_to_help_menus(self, e) -> None:
        ins = e.addMenu("Insert")
        for a in self.modern_v34_actions:  # Trend, Cicode Object
            ins.addAction(a)
        ins.addSeparator()
        for a in self.insert_actions:
            ins.addAction(a)
        lib = e.addMenu("Library")
        for a in (
            self.act_copy_to_lib,
            self.act_cut_link,
            self.act_cut_template_link,
            self.act_paste_composite,
            self.act_attach_super,
            self.act_genie_form,
            self.act_replace_genie,
        ):
            lib.addAction(a)
        lib.addSeparator()
        for a in (
            self.act_paste_linked,
            self.act_hotspot,
            self.act_used_by,
            self.act_update_users,
            self.act_compose_nested,
        ):
            lib.addAction(a)

        v = self.menuBar().addMenu("&View")
        v.addAction(self.act_palette)
        v.addAction(self.act_palette_here)
        v.addSeparator()
        for a in (
            self.act_zoom_in,
            self.act_zoom_out,
            self.act_zoom_fit,
            self.act_zoom_all,
            self.act_zoom_100,
            self.act_zoom_sel,
            self.tool_actions["zoom"],
            self.tool_actions["pan"],
            self.act_prev_view,
            self.act_next_view,
            self.act_zoom_window,
        ):
            v.addAction(a)
        self.saved_views_menu = v.addMenu("Saved Views")
        self.saved_views_menu.addAction(self.act_save_view)
        self.saved_views_menu.addSeparator()
        self.saved_views_menu.addAction(self.act_manage_views)
        self.saved_views_menu.aboutToShow.connect(self._sync_saved_views_menu)
        outside = v.addMenu("Outside the Page")
        for a in self.surround_actions.values():
            outside.addAction(a)
        v.addSeparator()
        gm = v.addMenu("Grid")
        gm.addAction(self.act_grid)
        gm.addAction(self.act_pixel_grid)
        gm.addAction(self.act_snap)
        gm.addSeparator()
        gg = QtGui.QActionGroup(self)
        self._grid_actions = {}
        for s in (1, 2, 4, 8, 10, 16, 20):
            a = gm.addAction("%d px" % s, lambda _=False, s=s: self._set_grid_size(s))
            a.setStatusTip("Set the grid pitch to %d px." % s)
            a.setCheckable(True)
            a.setChecked(s == self._grid_size)
            gg.addAction(a)
            self._grid_actions[s] = a
        gm.addSeparator()
        gm.addAction(self.act_grid_setup)
        guides = v.addMenu("Guidelines")
        guides.addAction(self.act_guidelines)
        guides.addAction(self.act_show_guides)
        guides.addAction(self.act_snap_guides)
        guides.addAction(self.act_clear_guides)
        v.addAction(self.act_smart_guides)
        self._build_underlay_menu(v)
        v.addSeparator()
        v.addAction(self.act_rulers)
        v.addAction(self.act_navigator)
        v.addAction(self.act_crosshair)
        v.addAction(self.act_selection_hud)
        v.addAction(self.act_pane_labels)
        v.addAction(self.act_aliased_text)
        v.addSeparator()
        self.labels_menu = v.addMenu("Labels")
        self._rebuild_labels_menu()
        panels = v.addMenu("Panels")

        for dock in (
            self.dock_objects,
            self.dock_library,
            self.dock_tags,
            self.dock_props,
            self.dock_history,
            self.dock_changes,
            self.dock_sa_checks,
            self.dock_page_checks,
            self.dock_navigator,
        ):
            _ta = dock.toggleViewAction()
            _ta.setStatusTip("Show or hide the %s panel." % dock.windowTitle())
            panels.addAction(_ta)
        panels.addSeparator()
        for a in self.panel_go_actions:
            panels.addAction(a)
        panels.addSeparator()
        panels.addAction(self.act_next_panel)
        panels.addAction(self.act_prev_panel)
        v.addAction(self.act_show_tools)
        v.addAction(self.act_status_bar)
        v.addAction(self.act_messages)
        v.addSeparator()
        v.addAction(self.act_isolate)
        v.addAction(self.act_show_all_roots)
        v.addAction(self.act_mark_changes)
        v.addAction(self.act_next_change)
        v.addAction(self.act_prev_change)
        v.addAction(self.act_select_changed)
        v.addSeparator()
        v.addAction(self.act_focus_mode)
        v.addAction(self.act_collapse_ribbon)
        v.addAction(self.act_reset_ui)
        v.addAction(self.act_refresh)
        self._dev_sep = v.addSeparator()
        v.addAction(self.act_thumb)  # developer tool (gated)

        tm = self.menuBar().addMenu("&Text")
        tm.addAction(self.act_edit_text)
        tm.addAction(self.act_font_dialog)
        tm.addSeparator()
        for a in self.text_flag_actions:
            tm.addAction(a)
        tm.addSeparator()
        for a in self.justify_actions:
            tm.addAction(a)
        tm.addSeparator()
        for a in self.effect_actions:
            tm.addAction(a)

        ar = self.menuBar().addMenu("&Arrange")
        ar.addAction(self.act_adjust)
        ar.addAction(self.act_align_dialog)
        ar.addSeparator()
        for a in (self.act_rot_cw, self.act_rot_ccw, self.act_flip_h, self.act_flip_v):
            ar.addAction(a)
        ar.addSeparator()
        ar.addAction(self.act_group)
        ar.addAction(self.act_ungroup)
        ar.addSeparator()
        for a in (self.act_front, self.act_fwd, self.act_bwd, self.act_back):
            ar.addAction(a)
        ar.addSeparator()
        for a in self.align_actions:
            ar.addAction(a)
        ar.addSeparator()
        ar.addAction(self.act_dist_h)
        ar.addAction(self.act_dist_v)
        ar.addAction(self.act_space_equally)
        ar.addAction(self.act_arrange_grid)
        ar.addAction(self.act_same_size)

        t = self.menuBar().addMenu("&Tools")
        draw = t.addMenu("Draw")
        for tool, _label, _ic, _key in _TOOLS:
            if tool not in ("measure", "zoom", "pan"):  # (the view tools: View menu)
                draw.addAction(self.tool_actions[tool])
        t.addAction(self.tool_actions["measure"])
        t.addSeparator()
        colours = t.addMenu("Colours")
        for a in (
            self.act_swap_colors,
            self.act_adjust_colors,
            self.act_fav_colors,
            self.act_transparent_paste,
        ):
            colours.addAction(a)
        colours.addSeparator()
        new_colours = colours.addMenu("Colours of New Objects")
        for a in (
            self.act_new_fill_colour,
            self.act_new_line_colour,
            self.act_new_text_colour,
            self.act_new_colours_swap,
        ):
            new_colours.addAction(a)
        new_colours.addSeparator()
        new_colours.addAction(self.act_new_no_fill)
        new_colours.addAction(self.act_new_colours_auto)
        t.addAction(self.act_to_bitmap)

        t.addAction(self.act_bitmap_editor)
        pictures = t.addMenu("Pictures")
        for a in (
            self.act_replace_picture,
            self.act_export_picture,
            self.act_resample_picture,
            self.act_dup_pictures,
        ):
            pictures.addAction(a)
        t.addSeparator()
        t.addAction(self.act_var_tags)
        t.addSeparator()
        t.addAction(self.act_check_page)
        t.addAction(self.act_check_workspace)
        t.addAction(self.act_check_display)
        t.addAction(self.act_simulate)
        t.addSeparator()
        t.addAction(self.act_edit_template)
        t.addAction(self.act_update_page_template)
        t.addSeparator()
        t.addAction(self.act_update_pages)
        t.addAction(self.act_rename_pages)
        t.addAction(self.act_pack_libs)
        t.addAction(self.act_close_builder)
        t.addAction(self.act_restore_backup)
        t.addSeparator()
        ext = t.addMenu("External Tools")
        for a in self.external_actions:
            ext.addAction(a)
        # CtDraw shows these only with 'Show version 3.xx/4.xx tools'
        self._legacy_menu = t.addMenu("V3/V4 Objects")
        for a in self.legacy_actions:
            self._legacy_menu.addAction(a)
        adv = t.addMenu("Advanced")
        for a in (self.act_slow_keys, self.act_slow_mouse):
            adv.addAction(a)
        t.addSeparator()
        t.addAction(self.act_options)

        w = self.menuBar().addMenu("&Window")
        w.addAction(self.act_next_page)
        w.addAction(self.act_prev_page)
        self._goto_tab_menu = w.addMenu("Go to Page")
        for a in self.goto_tab_actions:
            self._goto_tab_menu.addAction(a)
        self._goto_tab_menu.aboutToShow.connect(self._fill_goto_tab_menu)
        w.addSeparator()
        w.addAction(self.act_pin_tab)
        for a in (self.act_close_others, self.act_close_right, self.act_close_saved, self.act_reopen_closed):
            w.addAction(a)
        w.addSeparator()
        w.addAction(self.act_move_to_window)
        self._move_to_menu = w.addMenu("Move to Window")
        self._move_to_menu.aboutToShow.connect(lambda: self._fill_move_to_menu(self._move_to_menu))
        w.addSeparator()
        w.addAction(self.act_new_window)
        for a in (self.act_cascade, self.act_tile_h, self.act_tile_v):
            w.addAction(a)
        self._window_menu = w
        w.aboutToShow.connect(self._fill_window_list)
        self._window_list_actions: list[QtGui.QAction] = []

        h = self.menuBar().addMenu("&Help")
        for a in (
            self.act_help,
            self.act_getting_started,
            self.act_shortcuts_window,
            self.act_differences,
            self.act_troubleshooting,
        ):
            h.addAction(a)
        h.addSeparator()
        h.addAction(self.act_cicode_ref)
        h.addSeparator()
        h.addAction(self.act_open_logs)
        h.addAction(self.act_copy_diag)
        h.addSeparator()
        h.addAction(self.act_check_updates)
        h.addAction(self.act_about)

        # a checkable action WITH an icon shows its icon instead of the check
        # mark in a menu, which hides its state in the dark theme
        stack = [self.menuBar()]
        seen = set()
        while stack:
            menu = stack.pop()
            for a in menu.actions():
                if a.isCheckable():
                    a.setIconVisibleInMenu(False)
            for sub in menu.findChildren(QtWidgets.QMenu):
                if id(sub) not in seen:
                    seen.add(id(sub))
                    stack.append(sub)
        self._apply_command_icons()
        self._apply_surface_options()
        self.apply_canvas_look()

    def apply_canvas_look(self, scene=None) -> None:
        """The editor look of Settings > Canvas and View > Grid (settings.canvas_look) and
        View > Outside the Page on ``scene``, or on every scene of the window's tabs (a tab's
        own scene is made with it: _use_tab_scene). Editor-only: never saved or printed."""
        from .prefs.settings import canvas_look, surround_mode

        look = canvas_look()
        scenes = (
            [scene]
            if scene is not None
            else [self.scene]
            + [e.get("scene") for e in getattr(self, "_docs", ()) if e.get("scene") is not None]
        )
        mode = surround_mode()
        for sc in scenes:
            if hasattr(sc, "set_look"):
                sc.set_look(**look)
            if scene is not None and hasattr(sc, "set_surround"):
                sc.set_surround(self.scene.surround() if self.scene is not sc else mode)
        if hasattr(self, "act_pixel_grid") and self.act_pixel_grid.isChecked() != look["pixel_grid"]:
            self.act_pixel_grid.setChecked(look["pixel_grid"])

    def _toggle_pixel_grid(self) -> None:
        """View > Grid > Pixel Grid (setting view/pixel_grid)."""
        app_settings().setValue("view/pixel_grid", self.act_pixel_grid.isChecked())
        self.apply_canvas_look()

    def _apply_surface_options(self) -> None:
        """Show / hide the optional surfaces from Settings: V3/V4 creation
        tools and the developer / diagnostic tools."""
        st = app_settings()
        legacy = st.value("opt/show_legacy_tools", False, type=bool)
        dev = st.value("opt/developer_tools", False, type=bool)
        for a in self.legacy_actions:
            a.setVisible(legacy)
        if getattr(self, "_legacy_menu", None) is not None:
            self._legacy_menu.menuAction().setVisible(legacy)
        self.act_thumb.setVisible(dev)
        if getattr(self, "_dev_sep", None) is not None:
            self._dev_sep.setVisible(dev)
        if not dev and self.act_thumb.isChecked():
            self.act_thumb.setChecked(False)
            self._toggle_thumb()

    def _build_ribbon(self) -> None:
        """The ribbon: DESIGN tab, Quick Access, utility
        slot, collapse + priority overflow. Every button hosts an existing
        QAction (reachable from the menus too)."""
        from .ribbon import RibbonBar, VariantButton

        self.ribbon = RibbonBar(self)
        host = self.addToolBar("Ribbon")
        host.setObjectName("tb_ribbon")
        host.setMovable(False)
        host.setFloatable(False)
        host.toggleViewAction().setVisible(False)
        host.addWidget(self.ribbon)
        self._ribbon_host = host

        # Quick Access: Settings > Ribbon > Quick Access (New, Open, Save, Undo, Redo; Save
        # has an arrow of its own: Save As / Save All / MRU); the window's other settings
        # too (ui/prefs/window_options). (The commands the menus made are named first,
        # the grid sizes among them: a stored id must find its command here and in the
        # selection toolbar below, before apply_keymap names them at the end.)
        from .prefs import keymap as _keymap

        _keymap.assign_command_ids(self)
        from .prefs import job_options, window_options

        window_options.load()
        job_options.load()
        window_options.build_quick_access(self)
        # utility slot: the Search field (Ctrl+K: the command palette under it), then
        # compile / run / simulate / settings
        from .dialog.palette import HeaderSearch

        self.header_search = HeaderSearch(self)
        self.ribbon.add_utility(self.header_search)
        utility = []
        for a in (self.act_compile, self.act_run, self.act_simulate, self.act_options):
            b = QtWidgets.QToolButton(self)
            b.setDefaultAction(a)
            b.setAutoRaise(True)
            b.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
            b.setIconSize(QtCore.QSize(16, 16))
            self.ribbon.add_utility(b)
            utility.append(b)
        # (the buttons drop their names before the field narrows)
        self.header_search.set_companions(utility)

        design = self.ribbon.add_tab("Design")
        # (priority 2: its Filter / Box boxes collapse before Modify / Arrange at 1024 px)
        g = self.ribbon.add_group(design, "Select", priority=2)
        # (collapsed, it sits beside the pinned Select tool: not a second 'Select')
        g.fold_caption = "Selection Options"
        g.add_big(self.tool_actions["select"], pinned=True)  # (never behind a pop-up)
        g.add_small(self.act_goto)
        g.add_small(self.act_find)
        g.add_small_menu(
            "Lock",
            [self.act_lock_toggle, self.act_lock, self.act_unlock, self.act_unlock_all, self.act_break_lock],
            icon_name="lock",
        )
        # selection modes: kind filter + marquee behaviour
        self.sel_filter = QtWidgets.QComboBox(self)
        self.sel_filter.addItems(
            [
                "Everything",
                "Lines and pipes",
                "Shapes",
                "Text and numbers",
                "Buttons",
                "Symbols",
                "Genies and groups",
            ]
        )
        self.sel_filter.setMinimumWidth(120)
        self.sel_filter.setToolTip("Select only this kind of object")
        self.sel_filter.currentIndexChanged.connect(self._apply_sel_filter)
        g.add_field("Filter", self.sel_filter)
        self.sel_marquee = QtWidgets.QComboBox(self)
        self.sel_marquee.addItems(["Auto", "Enclose", "Touch"])
        self.sel_marquee.setMinimumWidth(120)
        self.sel_marquee.setToolTip(
            "Enclose selects the objects inside the box, Touch also the ones it "
            "crosses. Auto: Touch when you drag from right to left."
        )
        self.sel_marquee.currentIndexChanged.connect(
            lambda i: self.view.set_marquee_mode(("auto", "enclose", "touch")[i])
        )
        g.add_field("Box", self.sel_marquee)

        g = self.ribbon.add_group(
            design,
            "Place",
            priority=2,
            extras=[self.act_paste_symbol, self.act_paste_flash, self.act_paste_super],
        )
        g.add_big(self.tool_actions["symbol"], pinned=True)
        g.add_small(self.act_paste_genie)
        g.add_small(self.tool_actions["symbol_set"])
        g.add_small(self.act_import)
        g.add_small(self.act_paste_special)
        g.add_small(self.act_paste_composite)

        create = self.ribbon.add_group(design, "Create", priority=1)
        create.add_big(self.tool_actions["text"])
        create.add_big_widget(
            VariantButton([self.tool_actions[k] for k in ("line", "freehand", "pipe")], "line")
        )
        self._build_ribbon_groups(create, design)

    def _build_ribbon_groups(self, create, design) -> None:
        from .ribbon import VariantButton

        create.add_big_widget(
            VariantButton([self.tool_actions[k] for k in ("rectangle", "rectangle_outline")], "rect")
        )
        create.add_big_widget(
            VariantButton([self.tool_actions[k] for k in ("ellipse", "ellipse_outline")], "ellipse")
        )
        create.add_small(self.tool_actions["polygon"])
        create.add_small(self.tool_actions["number"])
        create.add_small(self.tool_actions["button"])
        # Trend + Cicode Object are CtDraw 8.5 toolbox tools; the rest of the
        # V3/V4 kinds appear here only with the setting (invisible actions)
        create.add_small_menu(
            "More", self.modern_v34_actions + self.insert_actions + self.legacy_actions, icon_name="activex"
        )

        # the named styles: a tile each (a click applies it, pointing previews it on the
        # canvas), Save and Manage (ui/panels/styles_gallery)
        from .panels.styles_gallery import StylesGallery

        # (priority 3: it compacts and folds before Create, Modify and Arrange)
        self._styles_group = self.ribbon.add_group(
            design, "Styles", priority=3, extras=[self.act_save_style, self.act_manage_styles]
        )
        self.ribbon_styles = StylesGallery(
            "ribbon",
            self,
            columns=4,
            actions_fn=self.style_actions,
            preview_fn=self.preview_style,
            menu_fn=self.style_menu_for,
            empty_action=self.act_save_style,
            page_colour_fn=self._page_colour,
        )
        self._styles_group.add_gallery(self.ribbon_styles)
        self._styles_group.add_small(self.act_save_style)
        self._styles_group.add_small(self.act_manage_styles)
        self._styles_group.extras.append(self.act_style_default)

        g = self.ribbon.add_group(
            design, "Modify", priority=1, extras=[self.act_rot_ccw, self.act_flip_v, self.act_ungroup]
        )
        g.add_big(self.act_dup)
        # (both directions: the face is the one used last)
        g.add_small_widget(VariantButton([self.act_rot_cw, self.act_rot_ccw], "rotate"))
        g.add_small_widget(VariantButton([self.act_flip_h, self.act_flip_v], "flip"))
        g.add_small(self.act_adjust)
        g.add_small(self.act_to_bitmap)
        g.add_small(self.act_cut_link)
        g.add_small(self.act_del)

        g = self.ribbon.add_group(
            design, "Arrange", priority=1, extras=list(self.align_actions) + [self.act_fwd, self.act_bwd]
        )
        g.add_big_widget(VariantButton(self.align_actions, "align"))
        g.add_small(self.act_align_dialog)
        g.add_small(self.act_dist_h)
        g.add_small(self.act_dist_v)
        g.add_small(self.act_same_size)
        g.add_small(self.act_group)
        g.add_small(self.act_ungroup)
        g.add_small_menu(
            "Order", [self.act_front, self.act_fwd, self.act_bwd, self.act_back], icon_name="front"
        )
        g.add_small_menu("Space", [self.act_space_equally, self.act_arrange_grid], icon_name="space_equally")

        g = self.ribbon.add_group(design, "Data", priority=3)
        g.add_big(self.act_genie_params)
        g.add_small(self.act_obj_props)
        g.add_small(self.act_page_props)
        # (here, not in Modify: a third column there makes the Select group a pop-up at
        # 1366 px)
        g.add_small_menu(
            "Appearance", [self.act_copy_appearance, self.act_paste_appearance], icon_name="paste_appearance"
        )

        # the colours of new objects live in Create: the fill swatch over the line swatch,
        # the swap arrow, No fill and the text colour (ui/swatch_pair, ui/editor/draw_styles)
        from .swatch_pair import SwatchPair

        self.ribbon_colours = SwatchPair(self.new_object_colours, self)
        self.ribbon_colours.partClicked.connect(self._on_swatch_part)
        # (a collapsed Create lists the colour commands in its menu)
        self.ribbon_colours._actions = [
            self.act_new_fill_colour,
            self.act_new_line_colour,
            self.act_new_text_colour,
            self.act_new_colours_swap,
            self.act_new_no_fill,
            self.act_new_colours_auto,
        ]
        create.add_swatch(self.ribbon_colours)
        from . import help as _style_help

        _style_help.set_help(self.ribbon_colours, "drawing#the-look-of-new-objects")
        _style_help.set_help(self.ribbon_styles, "colours#named-styles")
        self._update_colour_button()

        util = self.ribbon.add_tab("Utilities")
        g = self.ribbon.add_group(util, "Colours", priority=2)
        g.add_big(self.act_swap_colors)
        g.add_small(self.act_adjust_colors)
        g.add_small(self.act_fav_colors)

        g = self.ribbon.add_group(util, "Libraries", priority=1)
        g.add_big(self.act_copy_to_lib)
        g.add_small(self.act_pack_libs)
        g.add_small(self.act_update_pages)
        g.add_small(self.act_rename_pages)
        g.add_small(self.act_close_builder)
        g.add_small(self.act_attach_super)
        g.add_small(self.act_genie_form)
        g.add_small(self.act_used_by)
        g.add_small(self.act_update_users)
        g.add_small(self.act_hotspot)
        g.add_small(self.act_paste_linked)

        g = self.ribbon.add_group(util, "Bitmap", priority=2)
        g.add_big(self.act_bitmap_editor)
        g.add_small(self.act_import_flash)
        g.add_small(self.act_replace_picture)
        g.add_small(self.act_export_picture)
        g.add_small(self.act_resample_picture)
        g.add_small(self.act_dup_pictures)

        g = self.ribbon.add_group(util, "External", priority=3)
        for a in self.external_actions:
            g.add_small(a)

        # contextual tabs: in the tab bar only on demand
        self._syncing = False
        textp = self.ribbon.add_context_tab("Text")
        g = self.ribbon.add_group(textp, "Font", priority=1)
        g.add_big(self.act_font_dialog)
        self.font_combo = QtWidgets.QFontComboBox(self)
        self.font_combo.setMinimumWidth(130)
        self.font_combo.currentFontChanged.connect(self._apply_font_face)
        g.add_field("Font", self.font_combo)
        self.font_size = QtWidgets.QSpinBox(self)
        self.font_size.setRange(4, 96)
        self.font_size.setSuffix(" pt")
        self.font_size.valueChanged.connect(self._apply_font_size)
        g.add_field("Size", self.font_size)
        # Bold / Italic / Underline / Strikeout and the justifications: two rows of icons
        g.add_small(self.act_edit_text)
        g.add_toggle_row(self.text_flag_actions)
        g.add_toggle_row(self.justify_actions)
        g = self.ribbon.add_group(textp, "Effects", priority=3)
        g.add_small_menu("Effect", self.effect_actions, icon_name="effect_shadowed")

        shapep = self.ribbon.add_context_tab("Shape")
        g = self.ribbon.add_group(shapep, "Stroke", priority=1)
        self.shape_width = QtWidgets.QSpinBox(self)
        self.shape_width.setRange(1, 32)
        self.shape_width.setMinimumWidth(70)
        self.shape_width.setToolTip("Line or pipe width")
        self.shape_width.setSuffix(" px")
        self.shape_width.valueChanged.connect(self._apply_shape_width)
        g.add_field("Width", self.shape_width)
        self.shape_style = QtWidgets.QComboBox(self)
        self.shape_style.addItems(["Solid", "Dash", "Dot", "Dash Dot", "Dash Dot Dot"])
        self.shape_style.setMinimumWidth(90)
        self.shape_style.currentIndexChanged.connect(self._apply_shape_style)
        g.add_field("Style", self.shape_style)
        self.shape_filled = QtWidgets.QCheckBox(self)
        self.shape_filled.toggled.connect(self._apply_shape_filled)
        g.add_field("Filled", self.shape_filled)

        geniep = self.ribbon.add_context_tab("Genie")
        g = self.ribbon.add_group(geniep, "Parameters", priority=1)
        g.add_big(self.act_genie_params)
        g = self.ribbon.add_group(geniep, "Link", priority=2)
        g.add_small(self.act_cut_link)
        g.add_small(self.act_copy_to_lib)

        # no page open: the Design tab shows New and Open instead of dead commands (the
        # start page already says how to begin: no third prompt here)
        self.ribbon.set_empty_state(design, "", [self.act_new, self.act_open])
        self.act_save.changed.connect(
            lambda: self.ribbon.show_empty(design, getattr(self, "doc", None) is None)
        )
        self.ribbon.show_empty(design, self.doc is None)
        self.ribbon.set_collapse_action(self.act_collapse_ribbon)
        self._describe_actions()
        from . import help as _help

        _help.set_help(self.ribbon, "the-window#the-ribbon")
        self.ribbon.restore_tab()
        self._custom_group = None
        self.apply_ribbon_layout()
        # the bottom-centre canvas navigation bar
        from .editor.navbar import NavBar

        self.navbar = NavBar(self.view, self)
        from .editor.hud import SelectionHUD

        self.hud = SelectionHUD(self.view, self)
        # the rulers, the navigator, Previous / Next View, the Space pan (window/navigation)
        self._install_navigation()

        # command palette: Ctrl+K anywhere, Shift+S and S over the canvas
        # (act_palette, act_palette_here); the keymap (Settings > Keyboard, ui/prefs/keymap) gives
        # every command its keys: 'ctgedit' puts the Symbol tool on A and S opens the palette,
        # 'Graphics Builder' keeps S for Symbol. Applied again, live, by Settings.
        from .dialog.palette import CommandPalette
        from .prefs import keymap as _keymap

        self.palette_popup = CommandPalette(self)
        _keymap.apply_keymap(self)
        _keymap.install_repeat(self)

    def _replace_genie(self) -> None:
        """Edit > Library > Replace Genie: another genie in place of placed ones."""
        from .dialog.replace_genie import replace_genie

        replace_genie(self)

    def _genie_params_selected(self) -> None:
        """Genie Parameters for the current selection."""
        if self.doc is None or not self._sel:
            self.statusBar().showMessage("Select a genie first.", 4000)
            return
        self._on_object_activated(self._sel[0])

    def _show_messages(self) -> None:
        """View > Messages: the bell's list of this window's messages."""
        from .notify import show_messages

        show_messages(self)

    def _build_statusbar(self) -> None:
        """Build the status bar."""
        from .panels import status_items

        status_items.build(self)

    # CtDraw's keyboard move / resize keys (the canvas
    # maps VK_HOME -1,-1 / VK_PRIOR +1,-1 / VK_END -1,+1 / VK_NEXT
    # +1,+1 next to the arrows; plain = move, Shift = resize
    # Ctrl+Shift = resize keeping the aspect ratio)
    _MOVE_KEYS = (
        ("Left", -1, 0),
        ("Right", 1, 0),
        ("Up", 0, -1),
        ("Down", 0, 1),
        ("Home", -1, -1),
        ("PgUp", 1, -1),
        ("End", -1, 1),
        ("PgDown", 1, 1),
    )

    def _install_canvas_keys(self) -> None:
        """Keys that act on the page only while the canvas has focus."""
        ctx = QtCore.Qt.WidgetWithChildrenShortcut
        for key, dx, dy in self._MOVE_KEYS:
            for mod, slot in (
                ("", lambda dx=dx, dy=dy, k=key: self._move_key(dx, dy, k)),
                ("Shift+", lambda dx=dx, dy=dy: self._shift_arrow(dx, dy)),
                (
                    "Ctrl+Shift+",
                    lambda dx=dx, dy=dy: (
                        self._nudge_nodes(dx, dy) or self.resize_by_keys(dx, dy, keep_aspect=True)
                    ),
                ),
            ):
                sc = QtGui.QShortcut(QtGui.QKeySequence(mod + key), self.view, context=ctx)
                sc.activated.connect(slot)
            if key in ("Left", "Right", "Up", "Down"):
                # CtDraw moves with Ctrl + an arrow too (only Shift
                # changes what an arrow does); Settings can make it ten
                # steps or a grid pitch
                sc = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+" + key), self.view, context=ctx)
                sc.activated.connect(lambda dx=dx, dy=dy, k=key: self._ctrl_arrow(dx, dy, k))
                # Ctrl+Alt + an arrow: select the nearest object that way
                sc = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Alt+" + key), self.view, context=ctx)
                sc.activated.connect(lambda dx=dx, dy=dy: self.view.select_direction(dx, dy))
        # Shift+F10 (and the Menu key, CanvasView.contextMenuEvent): the right-click menu
        sc = QtGui.QShortcut(QtGui.QKeySequence("Shift+F10"), self.view, context=ctx)
        sc.activated.connect(self.view.open_context_menu_from_keyboard)
        # (minus = delete is a key of Edit > Delete on the canvas: ui/prefs/keymap.CANVAS_KEYS_PROP)
        for a in self.tool_actions.values():
            seq = a.shortcut()
            if (
                not seq.isEmpty()
                and seq.count() == 1
                and seq[0].keyboardModifiers() in (QtCore.Qt.NoModifier, QtCore.Qt.ShiftModifier)
            ):
                a.setShortcutContext(ctx)
                # attached to the WINDOW too (addActions above), the context
                # would cover every child -- the tree included
                self.removeAction(a)
                self.view.addAction(a)

    def _wire_canvas(self) -> None:
        self.view.selectionChanged.connect(self._on_canvas_selection)
        self.view.selectionChanged.connect(self._on_canvas_selection_noted)
        self.view.objectsMoved.connect(self._on_objects_moved)
        # selecting and transforming: a drag copy, a multi-selection scaled by
        # its box, the rotation grip; the canvas's options from Settings
        self.view.objectsCopied.connect(self._on_objects_copied)
        self.view.selectionScaled.connect(self._on_selection_scaled)
        self.view.rotateRequested.connect(self._rotate_steps)
        self.apply_select_options()
        # the side buttons, a double-click on the empty page, and the
        # pointer, guideline, navigator and text settings: ui/editor/canvas_options
        from .editor.canvas_options import wire_canvas_settings

        wire_canvas_settings(self)
        self.view.objectResized.connect(self._on_object_resized)
        self.view.objectActivated.connect(self._on_canvas_activated)
        self.view.memberActivated.connect(self._on_member_activated)
        self.view.set_member_filter(self._member_reachable)
        self.view.drawCommitted.connect(self._on_draw_committed)
        self.view.textEditFinished.connect(self._on_text_edit_finished)
        # (the pointer position and the zoom fields follow the canvas: ui/panels/status_items.py)
        self.view.contextRequested.connect(self._context_menu)
        self.view.toolResetRequested.connect(lambda: self._set_tool("select"))
        self.view.tagDropped.connect(self._on_tag_dropped)
        self.view.libraryElementDropped.connect(self._on_library_drop)
        self.view.filesDropped.connect(self._on_files_dropped)
        # node editing (reshape): drag / Insert / '+' on the canvas
        self.view.nodesMoved.connect(self._on_nodes_moved)
        self.view.nodeInsertRequested.connect(self._on_node_insert)
        self.view.reshapeRefused.connect(lambda msg: self.statusBar().showMessage(msg, 6000))
        self.view.statusMessage.connect(lambda msg: self.statusBar().showMessage(msg, 6000))
        self.view.set_node_editable(self._node_editable)
        self.view.set_grid(self._grid_size, self._grid_y, self._grid_origin)
        # a guideline dragged / added / deleted on the canvas: stored per page
        self.view.guidelinesChanged.connect(self._on_guidelines_changed)
        self.view.guidelinesDialogRequested.connect(self._guidelines_dialog)
        # the HUD follows zoom / scroll instead of floating at a stale spot
        self.view.zoomChanged.connect(self._schedule_hud)
        self.view.horizontalScrollBar().valueChanged.connect(self._schedule_hud)
        self.view.verticalScrollBar().valueChanged.connect(self._schedule_hud)
        # canvas click -> the top-level assembly (move whole groups)
        self.view.set_unit_resolver(self._unit_for)
        self.view.set_units_key(self._units_state_key)
        # tree/programmatic selection -> the exact node's own subtree
        self.view.set_subtree_resolver(self._subtree_for)

    def _template_locked(self) -> bool:
        """CtDraw locks the page's template-derived records (edit the .ctt,
        not its copies). Default on; Options can disable it."""
        return not app_settings().value("opt/unlock_template", False, type=bool)

    def _is_template_obj(self, index: int) -> bool:
        return (
            self.doc is not None
            and index < len(self.doc.page.objects)
            and bool(self.doc.page.objects[index].raw.get("_from_template"))
        )

    def _ct_locked(self, index: int) -> bool:
        """CtDraw's per-object SOFT-LOCK (dwFlags bit 0x4, persisted in the .ctg):
        the object is non-selectable / non-movable until Break Lock Mode. A project's
        floor-plan background bitmap + the nav-chrome containers carry it."""
        return (
            self.doc is not None
            and index < len(self.doc.page.objects)
            and bool((self.doc.page.objects[index].raw.get("_dwflags") or 0) & 0x4)
        )

    def _units_state_key(self):
        """What the selectable units depend on in the window (canvas.set_units_key): the
        document, its derive and in-place changes, the locks, Break Lock Mode and the
        template lock."""
        doc = self.doc
        if doc is None:
            return None
        # (the first entry: objects compared by identity; the rest: values)
        return (
            (doc,),
            getattr(doc, "derive_serial", 0),
            # (an in-place change, Edit > Lock's flag among them, derives nothing)
            getattr(doc, "touch_serial", 0),
            self._break_lock,
            frozenset(self._locked),
            self._template_locked(),
        )

    def _unit_locked(self, root: int) -> bool:
        """Is a selection unit locked?"""
        if self._break_lock:
            return False
        if root in self._locked:
            return True
        if self._is_template_obj(root):
            return self._template_locked()
        return self._ct_locked(root)

    def _filter_template(self, indices, verb: str = "edit", quiet: bool = False) -> list:
        """Drop locked and template indices from an edit."""
        if self.doc is None:
            return list(indices)
        indices = list(indices)
        # (the records of a top-level unit chosen whole skip the walks below)
        whole = self._whole_unit_roots(indices)
        nw = len(whole) if whole is not None else 0
        rest = indices if whole is None else [i for i in indices if not (0 <= i < nw and whole[i] >= 0)]
        members = self._genie_member_edits(rest)
        if members:
            indices = [i for i in indices if i not in members]
        if members and not quiet:
            from .names import plural
            from .notify import notify

            notify(
                self,
                "%s not %s: they are part of a genie, whose objects come from its "
                "definition. Right-click the genie > Edit Definition to change them."
                % (plural(len(members), "object"), self._done_verb(verb)),
                "warning",
                key="refused:genie member",
            )
        if self._break_lock:
            return indices
        keep, blocked, tpl_only = [], 0, False
        locked = {}  # root -> its unit is locked
        for i in indices:
            root = whole[i] if 0 <= i < nw and whole[i] >= 0 else self.doc.root_of(i)
            lk = locked.get(root)
            if lk is None:
                lk = locked[root] = self._unit_locked(root)
            if not lk:
                keep.append(i)
                continue
            blocked += 1
            # template-ONLY (not user-/soft-locked) gets the .ctt-specific hint
            if root not in self._locked and not self._ct_locked(root) and self._is_template_obj(root):
                tpl_only = True
        if blocked and not quiet:
            from .notify import notify

            if tpl_only and not self._locked:
                notify(
                    self,
                    "Template objects are locked. Edit the template instead, or "
                    "allow editing them in Tools > Settings.",
                    "warning",
                    key="refused:template",
                )
            else:
                from .names import plural

                notify(
                    self,
                    "%s not %s. Turn on Edit > Break Lock Mode to change them."
                    % (plural(blocked, "locked object"), self._done_verb(verb)),
                    "warning",
                    actions=[
                        (
                            "Break Lock Mode",
                            self.act_break_lock.trigger,
                            lambda: not self.act_break_lock.isChecked(),
                        )
                    ],
                    key="refused:locked",
                )
        return keep

    @staticmethod
    def _done_verb(verb: str) -> str:
        return {"flip": "flipped", "cut": "cut"}.get(verb, verb + ("d" if verb.endswith("e") else "ed"))

    #: below this many indices the genie-member walk is cheaper than finding whole units
    WHOLE_UNIT_MIN = 64

    def _whole_unit_roots(self, indices):
        """Per record, the root of the top-level unit ``indices`` hold whole, or -1."""
        if len(indices) < self.WHOLE_UNIT_MIN:
            return None
        page = self.doc.page
        n = len(page.objects)
        cs = sorted({i for i in indices if 0 <= i < n})
        chosen = set(cs)
        roots = page.roots()
        out = None
        for k, r in enumerate(roots):
            if r not in chosen:
                continue
            end = roots[k + 1] if k + 1 < len(roots) else n
            if bisect.bisect_left(cs, end) - bisect.bisect_left(cs, r) == end - r:
                if out is None:
                    out = array.array("l", [-1]) * n
                out[r:end] = array.array("l", [r]) * (end - r)
        return out

    def _genie_member_edits(self, indices) -> set:
        """The placed genies' members ``indices`` would edit without their
        genie (objprops.registry.genie_member_edits)."""
        from .objprops.registry import genie_member_edits

        return genie_member_edits(self.doc, indices)

    def _unit_for(self, index: int) -> list:
        if self.doc is None:
            return [index]
        root = self.doc.root_of(index)
        if self._unit_locked(root):
            return []  # locked (user / CtDraw bit 0x4 / template)
        # the subtree is remembered per derived model: a hover / marquee asks for it
        # many times (the lock above is checked every time: Edit > Lock changes no derive)
        page = self.doc.page
        serial = getattr(self.doc, "derive_serial", 0)
        memo = getattr(self, "_unit_memo", None)
        if memo is None or memo[0] is not page or memo[1] != serial:
            memo = self._unit_memo = (page, serial, {})
        unit = memo[2].get(root)
        if unit is None:
            unit = memo[2][root] = self.doc.subtree(root)
        return list(unit)

    def _subtree_for(self, index: int) -> list:
        if self.doc is None:
            return [index]
        return self.doc.subtree(index)
