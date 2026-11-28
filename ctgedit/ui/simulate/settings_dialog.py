"""Simulate option controls, for the Settings page and a standalone dialog."""

from __future__ import annotations

from ..dialog_buttons import dialog_buttons

import json
import os

from PySide6 import QtCore, QtWidgets

from .. import help as _help
from . import options
from .outside_access import AccessControls


class SimSettingsControls(QtCore.QObject):
    """The Simulate options' controls: :meth:`lay_out` puts them on a settings page,
    :meth:`load` / :meth:`defaults` fill them, :meth:`store` saves them (the Simulate window
    applies them: SimulateWindow.apply_settings)."""

    def __init__(self, parent=None, engine_path: str = ""):
        super().__init__(parent)
        self._make_start_controls()
        self._make_reload_controls()
        self._make_debug_controls()
        self._make_timing_controls()
        self._make_engine_controls(engine_path)
        self.reload_page.toggled.connect(self.reload_delay.setEnabled)
        self.reload_cicode.toggled.connect(self.cicode_delay.setEnabled)
        self.load()

    def _make_start_controls(self) -> None:
        """Tabs and windows, the start, closing, the user, undefined tags and the startup
        code."""
        Q = QtWidgets
        self.new_tab = Q.QCheckBox("Open each page in a new tab")
        self.new_tab.setToolTip(
            "When page code shows another page (PageDisplay, a menu, Back). The old page keeps running"
        )
        self.tab_window = Q.QCheckBox("Open each window in a tab of its own")
        self.tab_window.setToolTip("Panes, popups and WinNew windows, instead of inside their parent")
        self.autorun = Q.QCheckBox("Run the clock when a page opens")
        self.autorun.setToolTip("Off: wait for Run")
        self.confirm_close = Q.QCheckBox("Ask before closing a simulation with a recording or changed tags")
        self.confirm_close.setToolTip("Closing the Simulate window stops all its simulations")
        self.user = Q.QComboBox()
        self.user.addItem("Full access", "full")
        self.user.addItem("Not logged in", "none")
        cur = str(options.get("user"))
        if cur.startswith("user:"):
            self.user.addItem(cur[5:], cur)
        self.user.setToolTip("The toolbar's User box changes it while it runs")
        self.undefined = Q.QCheckBox("Simulate undefined tags")
        self.undefined.setToolTip("Setting a value for an unknown tag in the Tags panel creates it")
        self.startup = Q.QCheckBox("Run the project's startup code")
        self.startup.setToolTip(
            "As a client does at start: the startup page, _CitectBegin and StartupCode, then your page"
        )
        self.fast_startup = Q.QCheckBox("Run the startup code on a fast clock")
        self.fast_startup.setToolTip("20 times faster until it settles, so the page shows sooner")
        self.workspace = Q.QCheckBox("Run SA pages inside their workspace")
        self.workspace.setToolTip(
            "The startup page in its own tab, the page in its workspace pane. Needs the startup code"
        )
        self.startup.toggled.connect(self.workspace.setEnabled)
        self.startup.toggled.connect(self.fast_startup.setEnabled)

    def _make_reload_controls(self) -> None:
        """Trend history, what a text shows until its value comes, and live reload."""
        Q = QtWidgets
        self.trend_history = Q.QCheckBox("Show trend history from the start")
        self.trend_history.setToolTip("Each pen's first value fills the time before the simulation started")
        self.placeholder = Q.QComboBox()
        self.placeholder.addItem("abc", "abc")
        self.placeholder.addItem("Nothing", "")
        self.placeholder.setToolTip(
            "Shown until the simulator sends a value. A text that keeps showing it never got one"
        )
        self.reload_cicode = Q.QCheckBox("Reload Cicode when a .ci file changes")
        self.reload_cicode.setToolTip(
            "The page keeps running with the new code. Not for a simulation another program started"
        )
        self.reload_page = Q.QCheckBox("Reload the page when it changes in the editor or on disk")
        self.reload_page.setToolTip(
            "Also when a library it uses is saved. Tags, alarms and the clock keep their state"
        )
        self.reload_auto = Q.QCheckBox("Also when the editor updates it from its libraries")
        self.reload_auto.setToolTip("Off: the page runs on as it was, and Reload Page shows the update")
        self.reload_page.toggled.connect(self.reload_auto.setEnabled)
        # (the pauses under the switches they need, made below)

    def _make_debug_controls(self) -> None:
        """Debugging (errors, object numbers, text smoothing, breakpoints), DLLs and SQL
        databases, and the log's length."""
        Q = QtWidgets
        self.errors = Q.QCheckBox("Show errors on the page")
        self.errors.setToolTip("A badge on each object whose code raised a hardware error")
        self.numbers = Q.QCheckBox("Show object numbers (AN) in tool tips")
        self.numbers.setToolTip("The number Outputs and the Inspector use")
        self.aliased = Q.QCheckBox("Draw text without smoothing, like the runtime")
        self.aliased.setToolTip("Off: smoothed, like the editor")
        self.fg_break = Q.QCheckBox("Stop at breakpoints in page code")
        self.fg_break.setToolTip(
            "VS Code's debugger stops page expressions and the "
            "functions they call. The whole simulation waits"
        )
        self.fg_error_break = Q.QCheckBox("Also stop page code on its hardware errors")
        self.fg_error_break.setToolTip(
            "Like a breakpoint, with a debugger attached. Some pages raise errors every scan"
        )
        self.fg_break.toggled.connect(self.fg_error_break.setEnabled)
        # DLLs and SQL databases: Off (the default), Ask or On, each with its notice and its
        # allowed list (outside_access.py)
        self.dll_access = AccessControls("dll", self)
        self.sql_access = AccessControls("sql", self)
        self.log_lines = Q.QSpinBox()
        self.log_lines.setRange(*options.LOG_LINES_RANGE)
        self.log_lines.setSingleStep(500)
        self.log_lines.setSuffix(" lines")
        self.log_lines.setToolTip(
            "The Outputs panel keeps this many, and each simulation "
            "keeps as many of its own. An open Simulate window takes "
            "the new number at once"
        )

    def _make_timing_controls(self) -> None:
        """The pauses and counts: the reload pauses, the folder check, the Inspector's and
        the Watch tab's refresh, the tags listed."""
        Q = QtWidgets
        # the pauses and counts
        self.reload_delay = Q.QDoubleSpinBox()
        self.reload_delay.setRange(*options.RELOAD_DELAY_RANGE)
        self.reload_delay.setDecimals(1)
        self.reload_delay.setSingleStep(0.5)
        self.reload_delay.setSuffix(" s")
        self.reload_delay.setToolTip(
            "After your last edit. Longer: a slow run of edits reloads the page once"
        )
        self.cicode_delay = Q.QSpinBox()
        self.cicode_delay.setRange(*options.CICODE_DELAY_RANGE)
        self.cicode_delay.setSingleStep(100)
        self.cicode_delay.setSuffix(" ms")
        self.cicode_delay.setToolTip("After the file's last change on disk")
        self.folder_check = Q.QSpinBox()
        self.folder_check.setRange(*options.FOLDER_CHECK_RANGE)
        self.folder_check.setSuffix(" s")
        self.folder_check.setToolTip(
            "The project folders are also compared this often, in case a change notice was missed"
        )
        self.inspector_refresh = Q.QSpinBox()
        self.inspector_refresh.setRange(*options.REFRESH_RANGE)
        self.inspector_refresh.setSingleStep(100)
        self.inspector_refresh.setSuffix(" ms")
        self.inspector_refresh.setToolTip("How often the Inspector asks the simulator for its values")
        self.watch_refresh = Q.QSpinBox()
        self.watch_refresh.setRange(*options.REFRESH_RANGE)
        self.watch_refresh.setSingleStep(100)
        self.watch_refresh.setSuffix(" ms")
        self.watch_refresh.setToolTip("How often the Watch tab reads its watches, one call each at a time")
        self.tag_rows = Q.QSpinBox()
        self.tag_rows.setRange(*options.TAG_ROWS_RANGE)
        self.tag_rows.setSingleStep(500)
        self.tag_rows.setSuffix(" tags")
        self.tag_rows.setToolTip(
            "The Inputs panel's list, and the tags one search fetches. "
            "Typing narrows the list before the limit"
        )

    def _make_engine_controls(self, engine_path: str) -> None:
        """The simulator, the page's view (opening zoom, Space + drag) and the clock (speed
        at start, Step)."""
        Q = QtWidgets
        self.engine = Q.QLineEdit(engine_path)
        self.engine.setPlaceholderText("The one installed with ctgedit")
        self.engine.setToolTip("Path to ctsim.exe")
        self.engine.setMinimumWidth(280)
        self.browse = Q.QPushButton("Browse…")
        self.browse.setAutoDefault(False)
        self.browse.clicked.connect(self._choose)
        self.open_zoom = Q.QComboBox()
        for value, label in options.OPEN_ZOOMS:
            self.open_zoom.addItem(label, value)
        self.open_zoom.setToolTip(
            "For each page opened from now. A fitted page follows the "
            "tab's size until you zoom or pan. 100 % is the size the "
            "runtime shows it at"
        )
        self.space_pans = Q.QCheckBox("Space + drag pans the page")
        self.space_pans.setToolTip(
            "Off: Space goes to the page's keyboard commands, as in the runtime. The middle button still pans"
        )
        self.start_speed = Q.QComboBox()
        for v in options.SPEEDS:
            self.start_speed.addItem(options.speed_label(v), v)
        self.start_speed.setToolTip(
            "Each new simulation starts at this speed. The toolbar's Speed box changes it while it runs"
        )
        self.step = Q.QComboBox()
        for value, label, _n in options.STEPS:
            self.step.addItem(label, value)
        self.step.setToolTip(
            "How far the Step button moves the stopped clock. One scan is a quarter of a second"
        )

    def lay_out(self, page) -> None:
        """The controls as sections of ``page`` (a settings_pages.Page)."""
        s = page.section("Pages and windows")
        s.check(self.new_tab)
        s.check(self.tab_window)
        s.check(self.autorun)
        s.check(self.confirm_close)
        s.check(self.space_pans)
        s = page.section("Starting a simulation")
        s.row("User at start:", self.user)
        s.check(self.startup)
        s.sub(self.fast_startup)
        s.sub(self.workspace)
        s.check(self.undefined)
        s.check(self.trend_history)
        s = page.section("The clock")
        s.row("Clock speed at start:", self.start_speed)
        s.row("Step runs:", self.step)
        s = page.section("What the page shows")
        s.row("Open a page at:", self.open_zoom)
        s.row("A text before its value:", self.placeholder)
        s.check(self.errors)
        s.check(self.numbers)
        s.check(self.aliased)
        s = page.section("Editing while simulating")
        s.check(self.reload_cicode)
        s.sub(self.cicode_delay, label="Send the change after:")
        s.check(self.reload_page)
        s.sub(self.reload_auto)
        s.sub(self.reload_delay, label="Reload after a pause of:")
        s.row("Check the folders every:", self.folder_check)
        s = page.section("DLLs and databases")
        self.dll_access.lay_out(s, "DLLs:")
        self.sql_access.lay_out(s, "SQL databases:")
        s = page.section("Debugging with VS Code")
        s.check(self.fg_break)
        s.sub(self.fg_error_break)
        s = page.section("Panels")
        s.row("Inputs lists at most:", self.tag_rows)
        s.row("Outputs keeps:", self.log_lines)
        s.row("Inspector refreshes every:", self.inspector_refresh)
        s.row("Watch refreshes every:", self.watch_refresh)
        s = page.section("The simulator")
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.engine, 1)
        row.addWidget(self.browse)
        s.row("Simulator:", row)

    # ------------------------------------------------------------------------------
    def _fill(self, get) -> None:
        self.new_tab.setChecked(bool(get("new_tab_per_page")))
        self.tab_window.setChecked(bool(get("tab_per_window")))
        self.autorun.setChecked(bool(get("autorun")))
        self.confirm_close.setChecked(bool(get("confirm_close")))
        k = self.user.findData(get("user"))
        self.user.setCurrentIndex(k if k >= 0 else 0)
        self.undefined.setChecked(bool(get("simulate_undefined")))
        self.startup.setChecked(bool(get("run_startup")))
        self.trend_history.setChecked(bool(get("seed_trend_history")))
        self.workspace.setChecked(bool(get("sa_workspace")))
        self.workspace.setEnabled(self.startup.isChecked())
        self.reload_cicode.setChecked(bool(get("reload_cicode")))
        self.reload_page.setChecked(bool(get("reload_page")))
        self.reload_auto.setChecked(bool(get("reload_auto_updates")))
        self.reload_auto.setEnabled(self.reload_page.isChecked())
        self.errors.setChecked(bool(get("show_errors")))
        self.numbers.setChecked(bool(get("show_numbers")))
        self.aliased.setChecked(bool(get("aliased_text")))
        self.fg_break.setChecked(bool(get("foreground_break")))
        self.fg_error_break.setChecked(bool(get("foreground_error_break")))
        self.fg_error_break.setEnabled(self.fg_break.isChecked())
        self.fast_startup.setChecked(bool(get("fast_startup")))
        self.fast_startup.setEnabled(self.startup.isChecked())
        k = self.placeholder.findData(get("value_placeholder"))
        self.placeholder.setCurrentIndex(k if k >= 0 else 0)
        self.log_lines.setValue(int(get("log_lines")))
        self.dll_access.set_value(str(get("dll_access")))
        self.sql_access.set_value(str(get("sql_access")))
        self.dll_access.timeout.setValue(max(0, min(options.DLL_TIMEOUT_MAX, int(get("dll_timeout")))))
        # (each as the window reads it: ' Actual' is 100 %, 'TEN' is ten scans and anything
        # else the default, so OK never writes back a choice the window did not make)
        for box, name, read in (
            (self.open_zoom, "open_zoom", options.open_zoom),
            (self.start_speed, "start_speed", options.start_speed),
            (self.step, "step", options.step),
        ):
            box.setCurrentIndex(max(0, box.findData(read(get(name)))))
        self.space_pans.setChecked(bool(get("space_pans")))
        # each within its span, as the window reads it
        for box, name in (
            (self.reload_delay, "reload_delay_s"),
            (self.cicode_delay, "cicode_delay_ms"),
            (self.folder_check, "folder_check_s"),
            (self.inspector_refresh, "inspector_refresh_ms"),
            (self.watch_refresh, "watch_refresh_ms"),
            (self.tag_rows, "tag_rows"),
        ):
            lo, hi = options.SPANS[name]
            try:
                v = float(get(name))
            except TypeError, ValueError:  # swallow-ok: a garbled value shows the default
                v = options.OPTIONS[name][1]
            v = max(lo, min(hi, v))
            box.setValue(v if isinstance(box, QtWidgets.QDoubleSpinBox) else int(v))
        self.reload_delay.setEnabled(self.reload_page.isChecked())
        self.cicode_delay.setEnabled(self.reload_cicode.isChecked())

    def load(self) -> None:
        self._fill(options.get)
        self.dll_access.set_allowed(options.get_list("dll_allowed"))
        self.sql_access.set_allowed(options.get_list("sql_allowed"))

    def defaults(self) -> None:
        # (DLLs and SQL databases go back to Off; the allowed lists are kept: they are the
        # user's data, edited in their own window)
        self._fill(lambda n: options.OPTIONS[n][1])
        self.engine.setText("")

    def choices(self) -> dict:
        """The controls as settings keys and values (as :meth:`store` saves them: Export)."""
        vals = {
            "new_tab_per_page": self.new_tab.isChecked(),
            "tab_per_window": self.tab_window.isChecked(),
            "autorun": self.autorun.isChecked(),
            "user": self.user.currentData(),
            "confirm_close": self.confirm_close.isChecked(),
            "simulate_undefined": self.undefined.isChecked(),
            "run_startup": self.startup.isChecked(),
            "seed_trend_history": self.trend_history.isChecked(),
            "sa_workspace": self.workspace.isChecked(),
            "reload_cicode": self.reload_cicode.isChecked(),
            "reload_page": self.reload_page.isChecked(),
            "reload_auto_updates": self.reload_auto.isChecked(),
            "show_errors": self.errors.isChecked(),
            "show_numbers": self.numbers.isChecked(),
            "aliased_text": self.aliased.isChecked(),
            "foreground_break": self.fg_break.isChecked(),
            "foreground_error_break": self.fg_error_break.isChecked(),
            "fast_startup": self.fast_startup.isChecked(),
            "value_placeholder": self.placeholder.currentData(),
            "log_lines": self.log_lines.value(),
            "dll_access": self.dll_access.value(),
            "sql_access": self.sql_access.value(),
            "dll_allowed": json.dumps(self.dll_access.allowed),
            "sql_allowed": json.dumps(self.sql_access.allowed),
            "dll_timeout": self.dll_access.timeout.value(),
            "open_zoom": self.open_zoom.currentData(),
            "space_pans": self.space_pans.isChecked(),
            "start_speed": self.start_speed.currentData(),
            "step": self.step.currentData(),
            "reload_delay_s": self.reload_delay.value(),
            "cicode_delay_ms": self.cicode_delay.value(),
            "folder_check_s": self.folder_check.value(),
            "inspector_refresh_ms": self.inspector_refresh.value(),
            "watch_refresh_ms": self.watch_refresh.value(),
            "tag_rows": self.tag_rows.value(),
        }
        out = {}
        for name, v in vals.items():
            key, default = options.OPTIONS[name]
            out[key] = ("true" if v else "false") if isinstance(default, bool) else v
        return out

    def store(self) -> None:
        options.put("new_tab_per_page", self.new_tab.isChecked())
        options.put("tab_per_window", self.tab_window.isChecked())
        options.put("autorun", self.autorun.isChecked())
        options.put("confirm_close", self.confirm_close.isChecked())
        options.put("user", self.user.currentData())
        options.put("simulate_undefined", self.undefined.isChecked())
        options.put("run_startup", self.startup.isChecked())
        options.put("seed_trend_history", self.trend_history.isChecked())
        options.put("sa_workspace", self.workspace.isChecked())
        options.put("reload_cicode", self.reload_cicode.isChecked())
        options.put("reload_page", self.reload_page.isChecked())
        options.put("reload_auto_updates", self.reload_auto.isChecked())
        options.put("show_errors", self.errors.isChecked())
        options.put("show_numbers", self.numbers.isChecked())
        options.put("aliased_text", self.aliased.isChecked())
        options.put("foreground_break", self.fg_break.isChecked())
        options.put("foreground_error_break", self.fg_error_break.isChecked())
        options.put("fast_startup", self.fast_startup.isChecked())
        options.put("value_placeholder", self.placeholder.currentData())
        options.put("log_lines", self.log_lines.value())
        options.put("dll_access", self.dll_access.value())
        options.put("sql_access", self.sql_access.value())
        options.put_list("dll_allowed", self.dll_access.allowed)
        options.put_list("sql_allowed", self.sql_access.allowed)
        options.put("dll_timeout", self.dll_access.timeout.value())
        options.put("open_zoom", self.open_zoom.currentData())
        options.put("space_pans", self.space_pans.isChecked())
        options.put("start_speed", self.start_speed.currentData())
        options.put("step", self.step.currentData())
        options.put("reload_delay_s", self.reload_delay.value())
        options.put("cicode_delay_ms", self.cicode_delay.value())
        options.put("folder_check_s", self.folder_check.value())
        options.put("inspector_refresh_ms", self.inspector_refresh.value())
        options.put("watch_refresh_ms", self.watch_refresh.value())
        options.put("tag_rows", self.tag_rows.value())

    def engine_path(self) -> str:
        p = self.engine.text().strip()
        return os.path.normpath(p) if p else ""

    def _choose(self) -> None:
        start = self.engine.text().strip()
        path, _f = QtWidgets.QFileDialog.getOpenFileName(
            self.engine.window(),
            "Choose the Cicode Simulator",
            start,
            "ctsim.exe (ctsim.exe);;Programs (*.exe)",
        )
        if path:
            self.engine.setText(os.path.normpath(path))


class SimSettingsDialog(QtWidgets.QDialog):
    """The Simulate options on their own (a Simulate window with no editor window: the
    Settings window's Simulate page otherwise). OK stores them (``store``); the window
    applies them. The controls answer as attributes of the dialog too (``dlg.new_tab`` ...)."""

    def __init__(self, parent=None, engine_path: str = ""):
        super().__init__(parent)
        from ..prefs.settings_pages import Page

        self.setWindowTitle("Simulate Settings")
        _help.set_help(self, "simulate#simulate-settings")
        self.controls = SimSettingsControls(self, engine_path)
        from ..style.theme import scroll_body

        # (the options scroll: the whole page is taller than a screen; the buttons stay)
        lay, outer = scroll_body(self)
        page = Page("Simulate")
        page.heading.hide()
        self.controls.lay_out(page)
        lay.addWidget(page, 1)
        row = QtWidgets.QHBoxLayout()
        # (named as the Settings window's reset, for these options only)
        self.defaults_button = QtWidgets.QPushButton("Reset Simulate Settings")
        self.defaults_button.setAutoDefault(False)
        self.defaults_button.setToolTip("Set every Simulate option back to its default (saved with OK)")
        self.defaults_button.clicked.connect(self.defaults)
        row.addWidget(self.defaults_button)
        row.addStretch(1)
        bb = dialog_buttons(self)
        row.addWidget(bb)
        outer.addLayout(row)
        self.resize(640, 640)

    def __getattr__(self, name):
        # (the controls as the dialog's own attributes)
        controls = self.__dict__.get("controls")
        if controls is not None and not name.startswith("__"):
            return getattr(controls, name)
        raise AttributeError(name)

    def load(self) -> None:
        self.controls.load()

    def defaults(self) -> None:
        self.controls.defaults()

    def store(self) -> None:
        self.controls.store()

    def engine_path(self) -> str:
        return self.controls.engine_path()
