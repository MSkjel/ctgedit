"""The Simulate window."""

from __future__ import annotations
from ..style.theme import sp as _sp

import collections
import contextlib
import copy
import json
import os
import time
from typing import Dict, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from .. import help as _help
from ..style import theme
from ...render.scene import PageScene
from . import modeljob
from .modeljob import ModelJob
from . import options
from . import sources as S
from . import winhost
from .canvas import SimCanvas
from .controls import ControlFields
from .panels import InputsPanel, OutputsPanel
from ...editing import ui_state
from ...infra import logsetup
from ...infra.paths import data_dir

#: the window polls its simulations this often (ms)
TICK_MS = 40
SPEEDS = options.SPEEDS
#: the note of a tab on the demo source alone
DEMO_NOTE = (
    "Demo values: the Cicode simulator is not connected, so the page moves on demo values, not the project's."
)
#: the window without a page
EMPTY_NOTE = "No page is simulated. Simulate Page in the editor (Ctrl+F5) opens one here."


def overlay_qss(name: str, padded: bool = True) -> str:
    """A note over the page (#<name>: Reloading the page, the simulator not found): the
    surface of the editor canvas' own overlays (ui/editor/navbar.surface_qss: the theme's popup
    colour, see-through by Settings > Canvas > Overlay opacity); ``padded`` = a line of text."""
    from ..editor.navbar import surface_qss

    out = surface_qss(name)
    if padded:
        out += "#%s { color: %s; padding: 6px 14px; }" % (name, theme.COLORS["text"])
    return out


def pause_qss(name: str) -> str:
    """'Paused by the debugger' (#<name>): the theme's banner colours (a notice that needs
    the user), see-through as the other notes over the page (Overlay opacity)."""
    from ..editor.navbar import overlay_opacity

    bg = QtGui.QColor(theme.COLORS["banner_bg"])
    return (
        "%s#%s { background: rgba(%d, %d, %d, %d); color: %s; border: 1px solid %s; "
        "border-radius: 8px; padding: 6px 14px; }"
        % (
            theme.DATA_MARK,
            name,
            bg.red(),
            bg.green(),
            bg.blue(),
            round(overlay_opacity() * 2.55),
            theme.COLORS["banner_text"],
            theme.COLORS["banner_edge"],
        )
    )


class _OverlayNote(QtWidgets.QLabel):
    """A note over the page ('Reloading the page…', 'Paused by the debugger'): styled again
    when the theme or Settings > Canvas > Overlay opacity changes."""

    def __init__(self, name: str, parent, pause: bool = False):
        super().__init__(parent)
        self.setObjectName(name)
        self._pause = pause
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.restyle()

    def restyle(self) -> None:
        name = self.objectName()
        self.setStyleSheet(pause_qss(name) if self._pause else overlay_qss(name))

    def _theme_changed(self) -> None:
        self.restyle()


#: a line over the page that needs the user (the simulator stopped, the page changed in the
#: editor): framed in its note colour, as the Properties panel's read-only notice
BANNER_QSS = "QFrame#%s { border: 1px solid %s; border-radius: 4px; }"


def _speed_label(v: float) -> str:
    return options.speed_label(v)


def engine_setting() -> str:
    """The ctsim.exe the user chose (setting 'sim/ctsim_path'), '' for none."""
    try:
        from ..prefs.settings import app_settings
        from ...sim.client import SETTING

        return str(app_settings().value(SETTING, "", type=str) or "")
    except Exception:  # noqa: BLE001 -- a setting is a convenience only
        return ""


def runtime_label_mode() -> str:
    """The label mode a simulated page is read in: the editor's language when it shows one,
    else the native strings. The editor's 'literal' mode draws the Graphics Builder's own
    text ('@(Menu)', a string display's '="@(Menu)"' echo), which the runtime never shows:
    it draws the label's native (or translated) string."""
    from ...io import reader

    mode = reader.get_label_mode()
    return "native" if mode in ("literal", "") else mode


def page_model(page_path: str, doc=None) -> Optional[dict]:
    """The page as the Cicode engine reads it (sim/pagemodel: ctsim-page/0): the editor's
    document ``doc`` (unsaved edits included), else the file at ``page_path`` read on its own
    (nothing is written); None when neither can be read. GUI thread (the export resolves
    library elements on the read path)."""
    try:
        if doc is None:
            if not page_path or not os.path.isfile(page_path):
                return None
            from ...editing import EditableDocument

            doc = EditableDocument.open(page_path)
            return doc.export_page_model()
        # (an editor document's model, kept while it is not edited: Reload Page, a second
        # tab, a popup of the same page export it once)
        key = _model_key(doc)
        hit = ui_state(doc).sim_model_cache
        if hit is not None and hit[0] == key:
            if isinstance(hit[1], ModelJob):
                # (a helper is making it, page_model_job: it is usually done by now)
                got = hit[1].result()
                if got is not None:
                    doc.ui.sim_model_cache = (key, got)
                    return got
            else:
                return hit[1]
        model = doc.export_page_model()
        if getattr(doc, "ui", None) is not None:
            doc.ui.sim_model_cache = (key, model)
        return model
    except Exception:  # noqa: BLE001 -- the page then runs from its .RDB alone
        from ...infra import logsetup

        logsetup.note_exception("sim.window.page_model")
        return None


def page_model_job(page_path: str, doc):
    """:func:`page_model` of the editor document ``doc`` made in a helper process
    (modeljob.ModelJob, P7): the job, or the model when the document's is known already. The
    engine connection resolves a job on its own thread (EngineSimulation._model_value); the
    document keeps it for the next tab / Reload Page. Made here when no helper can start."""
    try:
        key = _model_key(doc)
        hit = ui_state(doc).sim_model_cache
        if hit is not None and hit[0] == key:
            return hit[1]
        if os.environ.get("CTG_SIM_MODEL_INPROCESS") == "1" or not getattr(doc, "path", ""):
            return page_model(page_path, doc)
        job = modeljob.start(doc)
        if job.error:
            return page_model(page_path, doc)
        if getattr(doc, "ui", None) is not None:
            doc.ui.sim_model_cache = (key, job)
        return job
    except Exception:  # noqa: BLE001 -- made here, as before
        from ...infra import logsetup

        logsetup.note_exception("sim.window.page_model_job")
        return page_model(page_path, doc)


#: page models read from files, by (file, its stamp): a Cicode-opened page's model goes to
#: the engine when it asks (modelWanted) and the tab that shows it reads the same one
_MODEL_CACHE: "collections.OrderedDict" = collections.OrderedDict()
MODEL_CACHE_SIZE = 8


def page_model_cached(page_path: str) -> Optional[dict]:
    """:func:`page_model` of a file (no document), kept for the file's current version."""
    try:
        st = os.stat(page_path)
    except OSError:
        return None
    key = (os.path.normcase(os.path.abspath(page_path)), st.st_mtime_ns, st.st_size)
    if key in _MODEL_CACHE:
        hit = _MODEL_CACHE[key]
        _MODEL_CACHE.move_to_end(key)
        if not isinstance(hit, ModelJob):
            return hit
        # (page_model_file_job is making it: its model, made once)
        got = hit.result()
        if got is not None:
            _MODEL_CACHE[key] = got
            return got
        _MODEL_CACHE.pop(key, None)
    model = page_model(page_path)
    if model is not None:
        _MODEL_CACHE[key] = model
        while len(_MODEL_CACHE) > MODEL_CACHE_SIZE:
            _MODEL_CACHE.popitem(last=False)
    return model


#: a page file this large has its model made in a helper process (page_model_file_job);
#: below it the export here is quicker than the helper's start: a popup's page shows
#: without the helper's delay
FILE_JOB_MIN_BYTES = 512 * 1024


def page_model_file_job(page_path: str):
    """Build the page model in a helper process for big files."""
    try:
        st = os.stat(page_path)
    except OSError:
        return None
    if st.st_size < FILE_JOB_MIN_BYTES:
        return page_model_cached(page_path)
    key = (os.path.normcase(os.path.abspath(page_path)), st.st_mtime_ns, st.st_size)
    hit = _MODEL_CACHE.get(key)
    if hit is not None:
        _MODEL_CACHE.move_to_end(key)
        if not isinstance(hit, ModelJob) or not hit.done():
            return hit
        got = hit.result(0)
        if got is not None:
            _MODEL_CACHE[key] = got
            return got
        _MODEL_CACHE.pop(key, None)  # (it failed: made here)
        return page_model_cached(page_path)
    if os.environ.get("CTG_SIM_MODEL_INPROCESS") == "1":
        return page_model_cached(page_path)
    # (the path as it is spelt: the model names the project and the page by it)
    job = ModelJob(os.path.abspath(page_path), b"")
    if job.error:
        return page_model_cached(page_path)
    _MODEL_CACHE[key] = job
    while len(_MODEL_CACHE) > MODEL_CACHE_SIZE:
        _MODEL_CACHE.popitem(last=False)
    return job


def _model_known(sim, w: dict) -> bool:
    """The engine has (or asked for, and gets from the window) the drawing of the page a
    window shows: a tab sends no second model for it."""
    if w.get("hasModel"):
        return True
    fn = getattr(sim, "viewer", None)
    svc = fn() if fn is not None else None
    return bool(svc is not None and svc.model_wanted_or_sent(w.get("window"), w.get("page")))


def _doc_key(doc):
    """A key that changes when the document is edited."""
    if doc is None:
        return None
    return (
        getattr(doc, "edit_serial", 0),
        getattr(doc, "derive_serial", 0) - getattr(doc, "library_derives", 0),
    )


def _model_key(doc):
    """What the document's page model (:func:`page_model`) is kept by: every derive counts
    (one that read changed libraries again may give the records' owners other library
    facts)."""
    if doc is None:
        return None
    return (getattr(doc, "edit_serial", 0), getattr(doc, "derive_serial", 0))


def _doc_steps(doc):
    """Where the document's undo history stands (the document, its length, its top step, how
    many steps were made part of that step): what tells the steps added since (_auto_only).
    None: no history to tell by."""
    undo = getattr(doc, "_undo", None) if doc is not None else None
    if undo is None:
        return None
    top = undo[-1] if undo else None
    return (doc, len(undo), top, getattr(top, "folded", 0))


def _auto_only(sp) -> bool:
    """The document of the tab ``sp`` changed since the tab read it by the editor's automatic
    steps alone (its background genie update, an open check: ui.auto_changes), not by an
    edit, Undo or Redo of the user's. A running simulation is not reloaded for them: the page
    it runs is the one the user saw when it started (the note offers Reload Page)."""
    doc, steps = sp.doc, getattr(sp, "doc_steps", None)
    undo = getattr(doc, "_undo", None) if doc is not None else None
    if steps is None or undo is None or steps[0] is not doc:
        return False
    _d, n, top = steps[:3]
    auto = {
        id(c) for _w, c in (getattr(getattr(doc, "ui", None), "auto_changes", None) or ()) if c is not None
    }
    if (
        n
        and len(undo) == n
        and undo[-1] is top
        and getattr(top, "folded", 0) > (steps[3] if len(steps) > 3 else 0)
    ):
        # (an automatic update became part of the step on top: the end of the run after a
        # genie save, files._fold_into_save_step)
        return id(top) in auto
    if len(undo) <= n or (n and undo[n - 1] is not top):
        return False
    return all(id(c) in auto for c in undo[n:])


def runtime_page(page_path: str, parsed=None, doc=None):
    """The page model the Simulate window draws."""
    from ...io import reader
    from . import options as _options

    with reader.derive_options(
        label_mode=runtime_label_mode(),
        composite_members=True,
        value_placeholder=_options.get("value_placeholder"),
    ):
        if parsed is not None:
            der0 = getattr(doc, "_derivation", None) if doc is not None else None
            page, der = reader.derive_page(parsed, page_path, shadows=der0)
        else:
            page, der = reader.derive_page(reader.parse_ctg(page_path), page_path, records_fresh=True)
        # the library files its drawing came from (live reload: a saved library reloads the
        # pages that use it)
        from .live_reload import resolver_libraries

        try:
            page._sim_libraries = resolver_libraries(getattr(der, "resolver", None))
        except Exception:  # noqa: BLE001 -- not known: any library change reloads it
            page._sim_libraries = None
        # (never cached: a Simulate tab changes its page model, so two tabs cannot share one)
        return page


def _banner(parent, name: str, role: str = "warn"):
    """A line over the page that needs the user: (the row shown or hidden, its frame, the
    frame's layout for the note and its buttons)."""
    row = QtWidgets.QWidget(parent)
    outer = QtWidgets.QVBoxLayout(row)
    outer.setContentsMargins(_sp(2), _sp(1), _sp(2), _sp(1))
    frame = QtWidgets.QFrame(row)
    frame.setObjectName(name)
    set_banner_role(frame, role)
    outer.addWidget(frame)
    lay = QtWidgets.QHBoxLayout(frame)
    lay.setContentsMargins(_sp(2), _sp(1), _sp(1), _sp(1))
    lay.setSpacing(_sp(1))
    return row, frame, lay


def set_banner_role(frame: QtWidgets.QFrame, role: str) -> None:
    """The banner framed in the colour of its note's role ('warn', 'danger', 'text_dim')."""
    role = role if role in theme.DARK else "text_dim"
    colour = theme.DARK["border"] if role == "text_dim" else theme.DARK[role]
    # (a design colour: the stylesheet hook maps it to Light / high contrast)
    frame.setStyleSheet(BANNER_QSS % (frame.objectName(), colour))


class _MissingPanel(QtWidgets.QFrame):
    """'The Cicode simulator is not installed' over the page (the page shows as it is saved)."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("SimMissingPanel")
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.restyle()
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(_sp(4), _sp(3), _sp(4), _sp(3))
        title = QtWidgets.QLabel("No Cicode simulator was found")
        f = title.font()
        f.setBold(True)
        title.setFont(f)
        lay.addWidget(title)
        # (one short sentence; the line over the page hides while this panel shows)
        self.text = QtWidgets.QLabel(
            "The page shows as it is saved. Choose the simulator "
            "(ctsim.exe), or reconnect once it is installed."
        )
        self.text.setWordWrap(True)
        lay.addWidget(self.text)
        row = QtWidgets.QHBoxLayout()
        self.choose = QtWidgets.QPushButton("Choose Simulator…")
        self.choose.setToolTip("Choose ctsim.exe, the Cicode simulator")
        # (the same command as Reconnect on the line over the page: one name)
        self.retry = QtWidgets.QPushButton("Reconnect")
        self.retry.setToolTip("Connect to the Cicode simulator again")
        row.addWidget(self.choose)
        row.addWidget(self.retry)
        row.addStretch(1)
        lay.addLayout(row)
        self.setMaximumWidth(460)
        self.hide()

    def restyle(self) -> None:
        """The overlays' surface (overlay_qss), again after a theme or Overlay opacity change."""
        self.setStyleSheet(overlay_qss("SimMissingPanel", padded=False))

    def _theme_changed(self) -> None:
        self.restyle()


class SimulatedPage(QtWidgets.QWidget):
    """One simulated page: its own scene (the runtime look: no editor marks), the page view
    and its simulation."""

    #: a PageState pushed by the simulation from any thread (moved to the GUI thread)
    _pushed = QtCore.Signal(object)
    #: the tab's title changed (the window navigated to another page)
    titleChanged = QtCore.Signal()

    def __init__(
        self,
        page_path: str,
        page=None,
        sim=None,
        parent=None,
        doc=None,
        model=None,
        attach_window: Optional[int] = None,
        project: str = "",
        run: bool = False,
    ):
        super().__init__(parent)
        self.page_path = page_path or ""
        if page is None:
            page = runtime_page(page_path)
        self.page = page
        # the page this tab was opened with (path, drawing): an SA page opened in its
        # workspace leaves this tab for the startup page, and the pane's tab draws it from
        # here (the editor's drawing, unsaved edits included: what the engine runs)
        self.opened_with = (self.page_path, page)
        # the editor's document of this page (its edits are offered as a reload)
        self.doc = doc
        self.doc_key = _doc_key(doc)
        # its undo history then (_auto_only), and whether the editor updated the page by
        # itself since (its background genie update: not reloaded, the note says so)
        self.doc_steps = _doc_steps(doc)
        self.auto_behind = False
        # the document this tab was opened with, kept when the tab moved on to its startup page
        # (an SA page in its workspace): the pane's view takes it, and its edits reload it
        self.opened_doc = None
        # live reload (live_reload.py): the library files the drawing came from with their
        # stamps (None: not known), when a reload is due (monotonic s) and why, the document
        # state that scheduled it
        self.lib_stamps = None
        self.reload_due: Optional[float] = None
        self.reload_why = ""
        self.reload_key = None
        # the reload due is for a library change (it waits while the editor runs something
        # that writes libraries: _live_tick)
        self.reload_lib = False
        # a library it uses changed while the editor's genie update of its document had not
        # landed: why it reloads once that update has landed ('' = none waits)
        self.lib_wait = ""
        # the page file as this tab last read it ((mtime, size); None: not read from a file),
        # and whether another program saved it since (the note, Reload Page); why the next
        # editor-document reload is due (the editor reloaded the page from disk)
        self.file_stamp = None
        self.file_changed = False
        self.pending_why = ""
        self.attach_window = attach_window
        self._windows_seen = -1
        # where it is shown (winhost): a tab, inside its parent window (a pinned pane) or as
        # a popup over the Simulate window; the windows shown inside it or opened from it;
        # the engine's last entry of its window (windowsChanged: x, y, w, h, mode, parent)
        self.host = winhost.TAB
        self.parent_view: Optional["SimulatedPage"] = None
        self.child_views: List["SimulatedPage"] = []
        self.engine_info: Optional[dict] = None
        self.frame = None
        self.scene = PageScene(self)
        # Simulate Settings: what a code-valued text shows until its value comes (the genies
        # the page's code places read it from the scene: render/sim_states, sim_drawn)
        self.scene.value_placeholder = options.get("value_placeholder")
        # (the runtime look before the page: the scene builds every copy of a stacked
        # label, the simulator's states decide which one shows)
        self.scene.set_runtime_look(True)
        self.scene.set_page(page)
        self.canvas = SimCanvas(self.scene, self)
        # 'Reloading the page...' over the viewport while a reload derives the page and the
        # simulator opens it again (show_reloading; tick hides it)
        self._reload_overlay: Optional[QtWidgets.QLabel] = None
        self._reloading_since: Optional[float] = None
        # the page's ActiveX text boxes: a field at each (the engine keeps their text)
        self.fields = ControlFields(self.scene, send=self._send_control_property)
        self.fields.rebuild(page)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.canvas)
        new_simulation = sim is None and attach_window is None
        self.sim = sim if sim is not None else S.default_simulation(engine_path=engine_setting() or None)
        if hasattr(self.sim, "want_user"):
            self.sim.want_user = user_setting()
        if hasattr(self.sim, "run_startup"):
            self.sim.run_startup = bool(options.get("run_startup"))
        if hasattr(self.sim, "seed_trend_history"):
            self.sim.seed_trend_history = bool(options.get("seed_trend_history"))
        if hasattr(self.sim, "foreground_break"):
            self.sim.foreground_break = bool(options.get("foreground_break"))
        if hasattr(self.sim, "foreground_error_break"):
            self.sim.foreground_error_break = bool(options.get("foreground_error_break"))
        if hasattr(self.sim, "fast_startup"):
            self.sim.fast_startup = bool(options.get("fast_startup"))
        if hasattr(self.sim, "set_outside"):
            self.sim.set_outside(
                options.access("dll_access"),
                options.access("sql_access"),
                options.get_list("dll_allowed"),
                options.get_list("sql_allowed"),
                options.dll_timeout(),
            )
        if hasattr(self.sim, "demo_values") and options.get("demo_values"):
            self.sim.demo_values = True  # (the developer option: made-up values)
        if hasattr(self.sim, "sa_workspace"):
            self.sim.sa_workspace = bool(options.get("sa_workspace"))
        if hasattr(self.sim, "model"):
            # the engine runs the page as it is here: the model carries the drawing summary
            # and, for a page that is not saved and compiled, its rows
            from ...sim import client as C

            if model is None and not C.disabled():
                # (a file's model is cached: the window may have sent it already for a
                # modelWanted, and this tab then reads the same one, not a second export)
                # (the model is made in a helper process while the server boots and the page
                # is drawn: the connection waits for it)
                model = (
                    page_model_job(self.page_path, doc)
                    if doc is not None
                    else page_model_file_job(self.page_path)
                )
            self.sim.model = model
            if attach_window is not None:
                self.sim.attach_window = attach_window
            if project:
                self.sim._project = project
        if new_simulation:
            # Simulate Settings > Clock speed at start, before it connects (the connection
            # sends it with the clock; a tab that joins a running simulation keeps its speed)
            speed = options.start_speed()
            if speed != 1.0:
                try:
                    self.sim.clock("speed", speed)
                except Exception:  # noqa: BLE001 -- the clock starts at real time
                    from ...infra import logsetup

                    logsetup.note_exception("sim.window.start_speed")
        if run:
            # (before it connects: the connection sends the clock it finds, else the startup
            # code would run on a paused clock when the server is up already)
            try:
                self.sim.clock("run")
            except Exception:  # noqa: BLE001 -- swallow-ok: the window's own run follows
                pass
        self.sim.start(self.page_path, page)
        self._note_file()
        self._note_libraries()
        self.log: List[S.LogEntry] = []
        self.scans = 0
        self.last = None
        self.canvas.inputEvent.connect(self._input)
        self._pushed.connect(self.apply, QtCore.Qt.QueuedConnection)
        setter = getattr(self.sim, "set_listener", None)
        if setter is not None:
            setter(self._pushed.emit)

    def apply_canvas_look(self) -> None:
        """Settings after OK: the canvas's scroll bars (Settings > Canvas), the notes over the
        page in the overlays' look (Overlay opacity) and the Show on Page mark (Selection
        colour, Selection outlines)."""
        self.canvas.apply_canvas_options()
        for w in (
            self._reload_overlay,
            getattr(self, "_pause_overlay", None),
            getattr(self, "_missing", None),
        ):
            if w is not None:
                w.restyle()
        self.canvas.viewport().update()

    def show_missing_panel(self, on: bool, text: str, choose, retry) -> None:
        """The 'simulator not installed' panel centred over the page (on), or hidden."""
        p = getattr(self, "_missing", None)
        if not on:
            if p is not None:
                p.hide()
            return
        if p is None:
            p = self._missing = _MissingPanel(self.canvas.viewport())
            p.choose.clicked.connect(lambda: choose())
            p.retry.clicked.connect(lambda: retry())
            self.canvas.viewport().installEventFilter(self)
        p.setToolTip(text)  # (the panel's own two sentences stay)
        p.adjustSize()
        self._place_missing()
        p.show()
        p.raise_()

    def _place_missing(self) -> None:
        p = getattr(self, "_missing", None)
        if p is None:
            return
        vp = self.canvas.viewport()
        p.move(max(8, (vp.width() - p.width()) // 2), max(8, (vp.height() - p.height()) // 2))

    def eventFilter(self, obj, ev):  # noqa: N802 -- Qt naming
        if (
            ev.type() == QtCore.QEvent.Resize
            and getattr(self, "_missing", None) is not None
            and obj is self.canvas.viewport()
        ):
            self._place_missing()
        return super().eventFilter(obj, ev)

    def title(self) -> str:
        t = getattr(self.page, "name", "") or os.path.splitext(os.path.basename(self.page_path))[0]
        # an SA page shown in its workspace: the tab is the page asked for (its pane), not
        # the workspace's master page
        asked = getattr(self.sim, "page_name", "") or ""
        if asked and getattr(self.sim, "focus_window", None) is not None and asked.lower() != t.lower():
            t = asked
        return t + " (left)" if getattr(self, "left", False) else t

    def window_title(self) -> str:
        """The caption of the engine window it shows (WinNewAt's / AssPopUp's title, else the
        page's title in the runtime), else its tab title."""
        t = (self.engine_info or {}).get("title") or ""
        return t if t else self.title()

    # -- the engine's windows and the editor's document ------------------------------------
    def engine_window(self) -> Optional[int]:
        """The engine window this tab shows (None: no engine, or not open yet)."""
        w = getattr(self.sim, "window", None)
        return w if isinstance(w, int) else None

    def doc_changed(self) -> bool:
        """The page was edited in the editor since this tab read it."""
        return self.doc is not None and _doc_key(self.doc) != self.doc_key

    def reload_from_doc(self) -> None:
        """Reload Page: show and simulate the page as the editor has it now, else as its file
        is now (its open events run again)."""
        self.reload_page()

    def _note_file(self) -> None:
        """Remember the page file as it is now (what a later change on disk is told by)."""
        from .live_reload import file_stamp

        self.file_stamp = file_stamp(self.page_path) if self.page_path else None
        self.file_changed = False

    #: the overlay goes at the latest after this many seconds (a simulator that never answers)
    RELOAD_OVERLAY_MAX_S = 20.0

    def show_reloading(self, on: bool = True, text: str = "Reloading the page…") -> None:
        """'Reloading the page…' over the viewport (top centre), painted at once: the reload
        derives the page on this thread before anything else is drawn. ``text``: another
        note of the same look ('Starting the Cicode simulator…' while it starts)."""
        if not on:
            self._reloading_since = None
            if self._reload_overlay is not None:
                self._reload_overlay.hide()
            return
        if self._reload_overlay is not None and self._reload_overlay.text() != text:
            self._reload_overlay.setText(text)
        if self._reload_overlay is None:
            lab = _OverlayNote("SimReloadOverlay", self.canvas.viewport())
            lab.setText(text)
            lab.setAccessibleName("Reloading the page")
            self._reload_overlay = lab
        lab = self._reload_overlay
        lab.adjustSize()
        vp = self.canvas.viewport()
        lab.move(max(0, (vp.width() - lab.width()) // 2), 12)
        lab.show()
        lab.raise_()
        lab.repaint()
        self._reloading_since = time.monotonic()

    def _show_debug_pause(self) -> None:
        """'Paused by the debugger in TankLevel, tanks.ci:18 (AN 44 on Mixer)' over the
        viewport while the debugger holds the simulation in page code (ctsim/debugPaused)."""
        fn = getattr(self.sim, "debug_paused", None)
        body = fn() if callable(fn) else None
        lab = getattr(self, "_pause_overlay", None)
        if body is None:
            if lab is not None and not lab.isHidden():
                lab.hide()
            return
        from .ctsim_source import debug_pause_where

        text = "Paused by the debugger in %s" % debug_pause_where(body)
        if lab is None:
            lab = _OverlayNote("SimPauseOverlay", self.canvas.viewport(), pause=True)
            self._pause_overlay = lab
        if lab.text() != text:
            lab.setText(text)
            lab.setAccessibleName(text)
            lab.adjustSize()
        vp = self.canvas.viewport()
        lab.move(max(0, (vp.width() - lab.width()) // 2), 12)
        if lab.isHidden():
            lab.show()
        lab.raise_()

    def _reload_overlay_done(self, drew: bool) -> None:
        """Hide the overlay once the simulation draws the reloaded page (a state came and it
        runs the page again), or the simulation is the demo, or it took too long."""
        if self._reloading_since is None:
            return
        live = getattr(self.sim, "page_active", None)
        running = getattr(self.sim, "session", None) is not None
        if (
            (drew and (live is None or live()))
            or not running
            or time.monotonic() - self._reloading_since > self.RELOAD_OVERLAY_MAX_S
        ):
            self.show_reloading(False)

    def reload_page(self) -> None:
        """Show and simulate the page again: as the editor has it (its document), else as its
        file is now (a library it uses changed); its open events run again, the simulation
        (tags, alarms, clock) goes on."""
        self.show_reloading()
        doc = self.doc
        path = (getattr(doc, "path", "") if doc is not None else "") or self.page_path
        if doc is not None:
            # (made in a helper process while this thread derives the page; the reload's
            # connection thread waits for it, P7)
            model = page_model_job(path, doc)
            page = runtime_page(path, getattr(doc, "parsed", None), doc)
        else:
            if not path or not os.path.isfile(path):
                self.show_reloading(False)
                return
            page = runtime_page(path)
            model = page_model(path)
        self.page = page
        self.canvas.clear_errors()
        self.scene.set_runtime_look(True)
        self.scene.set_page(page)
        self.fields.rebuild(page)
        if doc is not None:
            self.doc_key = _doc_key(doc)
            self.doc_steps = _doc_steps(doc)
        self.auto_behind = False
        self.reload_due = None
        self.reload_key = None
        self._note_file()
        self._note_libraries()
        reload = getattr(self.sim, "reload", None)
        if reload is not None:
            reload(model, page)
        self.titleChanged.emit()

    def _note_libraries(self) -> None:
        try:
            from .live_reload import page_libraries

            self.lib_stamps = page_libraries(self.page, self.doc)
        except Exception:  # noqa: BLE001 -- not known: any library change reloads it
            self.lib_stamps = None

    def libraries_changed(self, paths=None) -> bool:
        """A library file it may use changed (``paths``; None = check every one it used):
        True when its drawing should be derived again."""
        stamps = self.lib_stamps
        if stamps is None:
            return paths is not None
        from .live_reload import _key, library_stamp

        if paths is not None:
            keys = {_key(p) for p in paths}
            return any(_key(p) in keys for p in stamps)
        return any(library_stamp(p) != st for p, st in stamps.items())

    def follow_navigation(self) -> bool:
        """The engine shows another page in this tab's window (PageDisplay ...): the tab shows
        it too. True when it changed."""
        take = getattr(self.sim, "take_navigation", None)
        nav = take() if take is not None else None
        if not nav:
            return False
        handler = getattr(self, "navigation_handler", None)
        if handler is not None and options.get("new_tab_per_page"):
            try:
                if handler(self, nav):
                    return True
            except Exception:  # noqa: BLE001 -- follow it in this tab instead
                from ...infra import logsetup

                logsetup.note_exception("sim.window.navigation_handler")
        path = nav.get("path") or ""
        try:
            page = runtime_page(path)
        except Exception:  # noqa: BLE001 -- the page cannot be read: say so, keep the old one
            from ...infra import logsetup

            logsetup.note_exception("sim.window.follow_navigation")
            return False
        if self.doc is not None and self.opened_with is not None:
            # (an SA page went to its workspace pane: the pane's view takes its document)
            self.opened_doc = (self.doc, self.doc_key)
        self.page_path = path
        self.page = page
        self.doc = None
        self.doc_key = None
        self.doc_steps = None
        self.auto_behind = False
        self.reload_due = None
        self._note_file()
        self._note_libraries()
        self.canvas.clear_errors()
        self.scene.set_runtime_look(True)
        self.scene.set_page(page)
        self.fields.rebuild(page)
        self.sim.navigated(
            nav.get("page") or self.title(),
            path,
            page,
            None if _model_known(self.sim, nav) else page_model_file_job(path),
        )
        self.titleChanged.emit()
        return True

    def _send_control_property(self, an: int, prop: str, value: str) -> bool:
        """The user typed in a control's field: the simulation's control takes the text."""
        fn = getattr(self.sim, "set_object_property", None)
        return bool(fn(an, prop, value)) if fn is not None else False

    def _input(self, event) -> None:
        try:
            self.sim.input(event)
        except Exception:  # noqa: BLE001 -- a source problem must not break the view
            pass

    def apply(self, state) -> None:
        """Draw one PageState."""
        if state is None:
            return
        replace = bool(getattr(state, "replace", False))
        self.scene.apply_states(state.objects, replace=replace)
        drawn = getattr(state, "drawn", None)
        if drawn is not None or replace:
            # the page's Cicode output: the whole list (a whole state without one, the demo
            # values: nothing drawn)
            self.scene.sim_layer().apply_drawn(drawn or {})
        controls = getattr(state, "controls", None)
        if controls is not None:
            # the text the page's controls hold now (what the code wrote shows in the field)
            self.fields.apply(controls)
        self.scene.set_flash_phase(getattr(state, "flash_on", True))
        self.scans += 1
        self.last = state

    def tick(self) -> None:
        """Poll the simulation (draws a new state, keeps its new log lines)."""
        if getattr(self, "left", False):
            return []
        try:
            self.follow_navigation()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._show_debug_pause()
        except Exception:  # noqa: BLE001 -- a hint only
            from ...infra import logsetup

            logsetup.note_exception("sim.window._show_debug_pause")
        try:
            st = self.sim.poll()
        except Exception:  # noqa: BLE001
            st = None
        if st is not None:
            self.apply(st)
        self._reload_overlay_done(st is not None)
        drain = getattr(self.sim, "drain", None)
        if drain is not None:
            try:
                new = drain()
            except Exception:  # noqa: BLE001
                new = []
            if new:
                self.log.extend(new)
                keep = options.log_lines()  # (any value of its span)
                if len(self.log) > keep:
                    del self.log[: len(self.log) - keep]
                self._mark_errors(new)
                return new
        return []

    def expressions_of(self, an: int, source: str = "") -> List[str]:
        """The page's own properties of object ``an`` that match the code ``source`` named
        ('disable' -> DISABLED, 'visible' -> VISIBLE ...), as 'KEY = value'; every
        expression-like property when none match."""
        layer = self.scene.sim_layer() if hasattr(self.scene, "sim_layer") else None
        i = layer.index_of(an) if layer is not None else None
        objs = getattr(self.page, "objects", None) or []
        if i is None or not 0 <= i < len(objs):
            return []
        props = objs[i].raw.get("_props") or {}
        want = (source or "").split(".")[0].lower()
        hit = [k for k in props if want and (k.lower() == want or k.lower().startswith(want))]
        if not hit:
            hit = [k for k in props if str(props[k] or "").strip() and "(" in str(props[k])]
        return ["%s = %s" % (k, props[k]) for k in hit[:8]]

    def owns(self, e) -> bool:
        """The log line ``e`` comes from this tab's page (its window and page)."""
        w = self.engine_window()
        if getattr(e, "window", -1) is not None and e.window >= 0 and w is not None and e.window != w:
            return False
        page = getattr(e, "page", "") or ""
        return not page or page.lower() == (self.title() or "").lower()

    def _mark_errors(self, entries) -> None:
        for e in entries:
            lv = getattr(e, "level", "") or ("error" if e.kind == "error" else "")
            if (getattr(e, "an", -1) or -1) > 0 and lv in ("error", "warning") and self.owns(e):
                self.canvas.note_error(e.an, e.text, lv)

    def leave(self, to_page: str) -> None:
        """The window this tab showed moved on to ``to_page`` in a tab of its own: this tab's
        page opens again in a free window and keeps running; a simulation that cannot do
        that keeps the last picture (the tab says '(left)')."""
        self.left_to = to_page
        self.attach_window = None
        reopen = getattr(self.sim, "reopen_in_new_window", None)
        if reopen is not None and reopen():
            self.titleChanged.emit()
            return
        self.left = True
        try:
            with self.sim._lock:
                self.sim._window_gone = True  # the page left the window: no closePage
        except Exception:  # noqa: BLE001 -- another source
            pass
        self.stop()
        self.titleChanged.emit()

    def stop(self) -> None:
        setter = getattr(self.sim, "set_listener", None)
        if setter is not None:
            setter(None)
        try:
            self.sim.stop()
        except Exception:  # noqa: BLE001
            pass


def set_docked_setting(on: bool) -> None:
    from ..prefs.settings import app_settings

    try:
        app_settings().setValue("sim/docked", bool(on))
    except Exception:  # noqa: BLE001 -- a stored choice is a convenience only
        pass


class _KeyEater(QtCore.QObject):
    """The key press after a ShortcutOverride the docked Simulate window took to run its own
    command (SimulateWindow._run_own_key) goes nowhere: the focus widget gets it otherwise
    (the simulated page as a key of its own, a list as a move). Armed on the focus widget
    for that one press; any other key press disarms it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._target: Optional[QtWidgets.QWidget] = None
        self._key = None

    def arm(self, widget: QtWidgets.QWidget, ev) -> None:
        self.disarm()
        self._target = widget
        self._key = (int(ev.key()), int(ev.modifiers().value))
        widget.installEventFilter(self)

    def disarm(self) -> None:
        w, self._target, self._key = self._target, None, None
        if w is not None and _alive(w):
            w.removeEventFilter(self)

    def eventFilter(self, obj, ev):  # noqa: N802 -- Qt naming
        if ev.type() == QtCore.QEvent.KeyPress:  # (it filters its target alone)
            same = (int(ev.key()), int(ev.modifiers().value)) == self._key
            self.disarm()
            if same:
                ev.accept()
                return True
        return False


class SimDock(QtWidgets.QDockWidget):
    """The editor window's dock that holds the Simulate window, like the editor's other
    panels: it docks on any side of the editor window and floats as a window of its own
    (drag its title bar out, or its float button; drag it back or double-click its title to
    dock it again). Closing it stops the simulations, like closing the Simulate window."""

    def __init__(self, host):
        super().__init__("Simulate", host)
        self.setObjectName("dock_simulate")
        self.setFeatures(
            QtWidgets.QDockWidget.DockWidgetClosable
            | QtWidgets.QDockWidget.DockWidgetMovable
            | QtWidgets.QDockWidget.DockWidgetFloatable
        )
        self.setAllowedAreas(QtCore.Qt.AllDockWidgetAreas)
        # (shown and hidden by the Simulate window only, not by the editor's dock menu)
        self.toggleViewAction().setVisible(False)
        _help.set_help(self, "simulate#docking-the-simulate-window")
        from ..panels import docking

        docking.install(self)

    def closeEvent(self, ev):  # noqa: N802 -- Qt naming
        w = self.widget()
        if isinstance(w, SimulateWindow):
            if not w.confirm_close():
                ev.ignore()
                return
            w._save_layout()
            w.shut_down()
        super().closeEvent(ev)


def _with_key(tip: str, action: QtGui.QAction) -> str:
    """A tool tip with the action's shortcut after it: 'Copy a picture ... (Ctrl+Shift+C)'."""
    key = action.shortcut().toString(QtGui.QKeySequence.NativeText)
    return "%s (%s)" % (tip, key) if key else tip


#: the action property holding a command's tool tip without its key (_set_tip: the key after
#: it follows Settings > Keyboard)
TIP_PROP = "ctg_tip_text"
#: the Step button's tool tip by Simulate Settings > Step runs (options.STEPS)
STEP_TIPS = {
    "scan": "Run one page scan while the clock is stopped",
    "ten": "Run ten page scans while the clock is stopped",
    "second": "Run one second of simulated time while the clock is stopped",
}


def _set_tip(action: QtGui.QAction, tip: str = "") -> None:
    """``action``'s tool tip ``tip`` (else the one it had) with its key after it."""
    if tip:
        action.setProperty(TIP_PROP, tip)
    tip = str(action.property(TIP_PROP) or "")
    if tip:
        action.setToolTip(_with_key(tip, action))


class ClockBar(QtWidgets.QToolBar):
    """Run, Pause, Step and the speed of the current tab's simulated clock."""

    def __init__(self, parent=None):
        super().__init__("Clock", parent)
        self.setObjectName("SimulateClock")
        self.setMovable(False)
        self.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        # (the editor window's buttons: 16 px icons beside their text)
        self.setIconSize(QtCore.QSize(theme.px(16), theme.px(16)))
        # (keys, as Open Page and the Inspector have; WindowShortcut, so Page Keys still
        # sends the page's own keys first)
        # (their keys: Settings > Keyboard, SimulateWindow.apply_keys)
        self.act_run = self.addAction(theme.icon("run"), "Run")
        self.act_run.setShortcut(QtGui.QKeySequence("F5"))
        self.act_run.setShortcutContext(QtCore.Qt.WindowShortcut)
        _set_tip(self.act_run, "Run the simulated clock")
        self.act_pause = self.addAction(theme.icon("pause"), "Pause")
        self.act_pause.setShortcut(QtGui.QKeySequence("Shift+F5"))
        self.act_pause.setShortcutContext(QtCore.Qt.WindowShortcut)
        _set_tip(self.act_pause, "Stop the simulated clock")
        self.act_step = self.addAction(theme.icon("step"), "Step")
        self.act_step.setShortcut(QtGui.QKeySequence("F10"))
        self.act_step.setShortcutContext(QtCore.Qt.WindowShortcut)
        _set_tip(self.act_step, STEP_TIPS[options.step()])
        self.addSeparator()
        self.speed = QtWidgets.QComboBox()
        self.speed.setAccessibleName("Speed")
        for v in SPEEDS:
            self.speed.addItem(_speed_label(v), v)
        # (Simulate Settings > Clock speed at start: what a new simulation starts at)
        self.speed.setCurrentIndex(SPEEDS.index(options.start_speed()))
        self.addWidget(self._label("Speed:", self.speed))
        self.addWidget(self.speed)
        self.addSeparator()
        self.status = QtWidgets.QLabel("")
        self.status.setAccessibleName("Clock")
        theme.set_note_role(self.status, "text_dim", "padding: 0px 6px;")
        self.addWidget(self.status)
        # the debugging options live in Simulate Settings (kept as actions: the window reads them)
        self.act_errors = QtGui.QAction("Show errors on the page", self)
        self.act_errors.setCheckable(True)
        self.act_numbers = QtGui.QAction("Show object numbers (AN) in tool tips", self)
        self.act_numbers.setCheckable(True)
        self.addSeparator()
        self.user = QtWidgets.QComboBox()
        self.user.setAccessibleName("User")
        self.user.setToolTip(
            "Who is logged in to the simulation: Full access has every "
            "privilege in every area, so every button works"
        )
        self.user.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToContents)
        self.addWidget(self._label("User:", self.user))
        self.addWidget(self.user)
        # the page's commands on a toolbar of their own (one bar is too wide for a laptop or
        # a docked window). The window puts it on a second row when the two do not fit side
        # by side (SimulateWindow._lay_out_toolbars)
        self.page_bar = QtWidgets.QToolBar("Page", parent)
        self.page_bar.setObjectName("SimulatePageBar")
        self.page_bar.setMovable(False)
        self.page_bar.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.page_bar.setIconSize(QtCore.QSize(theme.px(16), theme.px(16)))
        bar = self.page_bar
        # the simulation: another page in it, the project's code again
        self.act_open_page = bar.addAction(theme.icon("open"), "Open Page…")
        self.act_open_page.setShortcut(QtGui.QKeySequence("Ctrl+O"))
        _set_tip(
            self.act_open_page,
            "Open another page of the project in a new tab, in the same simulation (its "
            "tags, alarms and clock)",
        )
        self.act_open_page.setShortcutContext(QtCore.Qt.WindowShortcut)
        _help.set_help(self.act_open_page, "simulate#opening-other-pages")
        self.act_reload_cicode = bar.addAction("Reload Cicode")
        self.act_reload_cicode.setToolTip(
            "Compile the project's changed .ci files again; the page keeps running with the new code"
        )
        _help.set_help(self.act_reload_cicode, "simulate#editing-while-simulating")
        # scenarios: record what you do, play a scenario, snapshots of the simulation's state
        self.scenario_menu = QtWidgets.QMenu("Scenario", self)
        self.act_record = self.scenario_menu.addAction("Record")
        self.act_record.setCheckable(True)
        self.act_record.setToolTip(
            "Record the tag writes and clicks you make, with their "
            "simulated times; stop to save them as a scenario"
        )
        self.act_play = self.scenario_menu.addAction("Play Scenario…")
        self.act_play.setToolTip(
            "Play a scenario file (ctsim-scenario/0) from now; the tags you drive go on beside it"
        )
        self.act_stop_scenario = self.scenario_menu.addAction("Stop Scenario")
        self.scenario_menu.addSeparator()
        self.act_save_snapshot = self.scenario_menu.addAction("Save Snapshot…")
        self.act_save_snapshot.setToolTip(
            "Save the tag values, the tag property overrides and the page variables to a file"
        )
        self.act_load_snapshot = self.scenario_menu.addAction("Load Snapshot…")
        self.act_load_snapshot.setToolTip(
            "Put a saved snapshot back: its tag values, property overrides and page variables"
        )
        self.scenario_button = QtWidgets.QToolButton(self)
        self.scenario_button.setText("Scenario")
        self.scenario_button.setAccessibleName("Scenario")
        self.scenario_button.setToolTip(
            "Record, play, stop a scenario; save and load a snapshot of the simulation"
        )
        self.scenario_button.setMenu(self.scenario_menu)
        self.scenario_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.scenario_button.setProperty("ctgMenu", True)  # (room for its arrow)
        self.scenario_button.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        bar.addWidget(self.scenario_button)
        _help.set_help(self.scenario_button, "simulate#scenarios-and-snapshots")
        bar.addSeparator()
        # the page: a picture of it, its keys, what it is made of
        self.act_copy = bar.addAction(theme.icon("copy"), "Copy Image")
        self.act_copy.setShortcut(QtGui.QKeySequence("Ctrl+Shift+C"))
        _set_tip(
            self.act_copy,
            "Copy a picture of the page to the clipboard, the windows it shows inside it "
            "and its popups included",
        )
        self.act_copy.setShortcutContext(QtCore.Qt.WindowShortcut)
        _help.set_help(self.act_copy, "simulate#copying-a-picture-of-the-page")
        # the editor's zoom commands on the editor's keys (the way back to the fitted page or
        # to 100 % after a wheel zoom or a pan)
        self.act_zoom_fit = bar.addAction(theme.icon("zoom_fit"), "Fit Page")
        self.act_zoom_fit.setShortcut(QtGui.QKeySequence("Ctrl+0"))
        _set_tip(self.act_zoom_fit, "Show the whole page, as View > Fit Page does")
        self.act_zoom_100 = bar.addAction("Zoom 100%")
        self.act_zoom_100.setShortcut(QtGui.QKeySequence("Ctrl+1"))
        _set_tip(self.act_zoom_100, "Show the page at its own size, as the runtime does")
        self.act_zoom_in = bar.addAction(theme.icon("zoom_in"), "Zoom In")
        self.act_zoom_in.setShortcuts([QtGui.QKeySequence("Ctrl+="), QtGui.QKeySequence("Ctrl++")])
        _set_tip(self.act_zoom_in, "Zoom in one level")
        self.act_zoom_out = bar.addAction(theme.icon("zoom_out"), "Zoom Out")
        self.act_zoom_out.setShortcut(QtGui.QKeySequence("Ctrl+-"))
        _set_tip(self.act_zoom_out, "Zoom out one level")
        self.zoom_actions = (self.act_zoom_fit, self.act_zoom_100, self.act_zoom_in, self.act_zoom_out)
        for a in self.zoom_actions:
            a.setShortcutContext(QtCore.Qt.WindowShortcut)
            _help.set_help(a, "simulate#the-page")
        bar.addSeparator()
        self.act_page_keys = bar.addAction("Page Keys")
        self.act_page_keys.setCheckable(True)
        self.act_page_keys.setToolTip(
            (
                "On: keys typed while the page has the focus go to its keyboard commands, as in the "
                "runtime. Off: they go to ctgedit's shortcuts (mouse clicks still reach the page)"
            )
        )
        _help.set_help(self.act_page_keys, "simulate#the-page")
        self.act_inspect = bar.addAction("Inspector")
        self.act_inspect.setShortcut(QtGui.QKeySequence("Ctrl+I"))
        _set_tip(
            self.act_inspect,
            "What the page is made of and what it uses now: its objects and their state, "
            "tags, associations, page variables and windows",
        )
        self.act_inspect.setShortcutContext(QtCore.Qt.WindowShortcut)
        _help.set_help(self.act_inspect, "simulate#the-inspector")
        bar.addSeparator()
        # the Inputs / Outputs panels (closed with their X, they come back from here)
        self.panels_menu = QtWidgets.QMenu("Panels", self)
        self.panels_button = QtWidgets.QToolButton(self)
        self.panels_button.setText("Panels")
        self.panels_button.setAccessibleName("Panels")
        self.panels_button.setToolTip("Show or hide the Inputs and Outputs panels")
        self.panels_button.setMenu(self.panels_menu)
        self.panels_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.panels_button.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        bar.addWidget(self.panels_button)
        self.act_settings = bar.addAction("Settings…")
        self.act_settings.setToolTip(
            "The Simulate settings (Settings > Simulate): tabs, the "
            "start, the user, error marks, debugging, the simulator"
        )
        self._users_shown: Optional[tuple] = None
        self.set_users([], {"mode": "full"})
        for a in (self.act_run, self.act_pause, self.act_step):
            _help.set_help(a, "simulate#the-clock")

    @staticmethod
    def _label(text: str, buddy: QtWidgets.QWidget) -> QtWidgets.QLabel:
        """A field's label in the toolbar, as the ribbon's (a screen reader names the field
        by it)."""
        lb = QtWidgets.QLabel(text)
        theme.set_note_role(lb, "label", "padding: 0px 4px 0px 6px;")
        lb.setBuddy(buddy)
        return lb

    def set_users(self, users, current) -> None:
        """The User choice: Full access, Not logged in, then the project's users; the
        current one selected (no signal)."""
        key = (
            tuple(str(u.get("name")) for u in users),
            str((current or {}).get("mode")),
            str((current or {}).get("name")),
        )
        if key == self._users_shown:
            return
        self._users_shown = key
        self.user.blockSignals(True)
        try:
            self.user.clear()
            self.user.addItem("Full access", "full")
            self.user.addItem("Not logged in", "none")
            for u in users:
                label = str(u.get("name"))
                if u.get("fullName") and u.get("fullName") != u.get("name"):
                    label += " (%s)" % u.get("fullName")
                self.user.addItem(label, "user:" + str(u.get("name")))
            mode = str((current or {}).get("mode") or "full")
            want = "user:" + str(current.get("name")) if mode == "user" else mode
            k = self.user.findData(want)
            self.user.setCurrentIndex(k if k >= 0 else 0)
        finally:
            self.user.blockSignals(False)

    def show_state(self, cs: Optional[S.ClockState]) -> None:
        on = cs is not None
        running = bool(cs and cs.running)
        self.act_run.setEnabled(on and not running)
        self.act_pause.setEnabled(on and running)
        self.act_step.setEnabled(on and not running)
        self.speed.setEnabled(on)
        if cs is None:
            last = getattr(self, "_last_state", None)
            # (a simulator that stopped: the clock says where it stopped, not nothing)
            self.status.setText("stopped at %.1f s" % last.time if last is not None else "")
            return
        self._last_state = cs
        self.status.setText(
            "Scan %d  ·  %.1f s  ·  %s" % (cs.scan, cs.time, "running" if running else "stopped")
        )
        k = self.speed.findData(cs.speed)
        if k >= 0 and k != self.speed.currentIndex():
            self.speed.blockSignals(True)
            self.speed.setCurrentIndex(k)
            self.speed.blockSignals(False)


#: how often the Inputs / Outputs / alarm panels and the status line follow the simulation
PANEL_PERIOD_S = 0.2


class _SimTabBar(QtWidgets.QTabBar):
    """The Simulate window's tab bar: a middle click closes the tab under it (on the release,
    under the same tab) and a right click asks for the tab's menu, as the editor's document
    tabs (ui/panels/doc_tabs.DocTabBar)."""

    tabMenuRequested = QtCore.Signal(int, QtCore.QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._middle = -1

    def mousePressEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        if e.button() == QtCore.Qt.MiddleButton:
            self._middle = self.tabAt(e.position().toPoint())
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        if e.button() == QtCore.Qt.MiddleButton:
            k = self.tabAt(e.position().toPoint())
            if k >= 0 and k == self._middle:
                self.tabCloseRequested.emit(k)
            self._middle = -1
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def contextMenuEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        k = self.tabAt(e.pos())
        if k >= 0:
            self.tabMenuRequested.emit(k, e.globalPos())
            e.accept()
        else:
            e.ignore()


class _SimTabs(QtWidgets.QTabWidget):
    """The tabs with :class:`_SimTabBar`."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTabBar(_SimTabBar(self))


def _fit_to_screen(win: QtWidgets.QWidget) -> None:
    """A window of its own kept on its screen: no larger than the screen's free area (the
    default 1280 x 820 is taller than a 768 px screen, and a size restored from a larger
    monitor can be too), and moved inside it. A maximized window is left as it is."""
    if not win.isWindow() or win.isMaximized() or win.isFullScreen():
        return
    screen = win.screen() or QtWidgets.QApplication.primaryScreen()
    if screen is None:
        return
    avail = screen.availableGeometry()
    frame = win.frameGeometry().size() - win.size()
    if frame.width() <= 0 or frame.height() <= 0:
        frame = QtCore.QSize(16, 39)  # (before the frame is known: a caption and borders)
    w = min(win.width(), avail.width() - frame.width())
    h = min(win.height(), avail.height() - frame.height())
    if (w, h) != (win.width(), win.height()):
        win.resize(max(w, win.minimumSizeHint().width()), max(h, win.minimumSizeHint().height()))
    fw, fh = win.width() + frame.width(), win.height() + frame.height()
    pos = win.frameGeometry().topLeft()
    x = max(avail.left(), min(pos.x(), avail.right() + 1 - fw))
    y = max(avail.top(), min(pos.y(), avail.bottom() + 1 - fh))
    if (x, y) != (pos.x(), pos.y()):
        win.move(x, y)


def _scrolling(widget: QtWidgets.QWidget) -> QtWidgets.QScrollArea:
    """``widget`` in a frameless scroll area: a panel too tall for the window (a large text
    size on a 768 px screen) scrolls instead of pushing the window's bottom off the screen."""
    area = QtWidgets.QScrollArea()
    area.setFrameShape(QtWidgets.QFrame.NoFrame)
    area.setWidgetResizable(True)
    area.setWidget(widget)
    return area


class SimulateWindow(QtWidgets.QMainWindow):
    """The simulated pages (tabs), the clock bar and the Inputs / Outputs panels."""

    pageOpened = QtCore.Signal(object)
    #: (path, error or '') of Save Snapshot, from the reader thread to this one
    _snapshot_saved = QtCore.Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dock = None  # (eventFilter() reads them from the first event)
        self._host = None
        self.setObjectName("SimulateWindow")
        self.setWindowTitle("Simulate - ctgedit")
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose, False)
        _help.set_help(self, "simulate")
        central = QtWidgets.QWidget(self)
        v = QtWidgets.QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        # the status line: which simulator and project, or why the page is on demo values
        self.status_row, self._status_banner, row = _banner(central, "SimStatusBanner")
        self.note = theme.note_label(DEMO_NOTE, role="warn")
        self.note.setAccessibleName("Simulation status")
        row.addWidget(self.note, 1)
        self.reconnect_button = QtWidgets.QPushButton("Reconnect")
        self.reconnect_button.setToolTip("Connect to the Cicode simulator again")
        self.reconnect_button.clicked.connect(self.reconnect_current)
        row.addWidget(self.reconnect_button)
        self.engine_button = QtWidgets.QPushButton("Choose Simulator…")
        self.engine_button.setToolTip("Choose ctsim.exe, the Cicode simulator")
        self.engine_button.clicked.connect(self.choose_engine)
        row.addWidget(self.engine_button)
        self.status_row.setVisible(False)
        _help.set_help(self.status_row, "simulate#the-cicode-simulator")
        v.addWidget(self.status_row)
        # which simulator and project, when all is well: in the status bar, not over the page
        self.sim_status = QtWidgets.QLabel("")
        self.sim_status.setAccessibleName("Simulation status")
        # (as the editor window's project label in its status bar)
        theme.set_note_role(self.sim_status, "text_dim", "padding: 0px 6px;")
        _help.set_help(self.sim_status, "simulate#the-cicode-simulator")
        # (its messages stay as long as Settings > Appearance > Messages stay says)
        from ..notify import ScaledStatusBar

        self.setStatusBar(ScaledStatusBar(self))
        self.statusBar().addPermanentWidget(self.sim_status)
        # the page was edited in the editor: Reload Page simulates it as it is now
        self.edit_row, _frame, er = _banner(central, "SimEditBanner")
        self.edit_note = theme.note_label("The page changed in the editor.", role="warn")
        self.edit_note.setAccessibleName("Page changed")
        er.addWidget(self.edit_note, 1)
        self.reload_button = QtWidgets.QPushButton("Reload Page")
        self.reload_button.setToolTip("Simulate the page as it is in the editor now")
        self.reload_button.clicked.connect(self.reload_current)
        er.addWidget(self.reload_button)
        self.edit_row.setVisible(False)
        _help.set_help(self.edit_row, "simulate#editing-while-simulating")
        v.addWidget(self.edit_row)
        self.tabs = _SimTabs(central)
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setAccessibleName("Simulated pages")
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.tabs.tabBar().tabMenuRequested.connect(self._tab_menu)
        v.addWidget(self.tabs, 1)
        # no page simulated: say how to simulate one (the editor's start page does the same)
        self.empty_note = theme.note_label(EMPTY_NOTE, parent=self.tabs)
        self.empty_note.setAlignment(QtCore.Qt.AlignCenter)
        self.empty_note.setAccessibleName("No page is simulated")
        self.tabs.installEventFilter(self)  # (the note stays in the middle: eventFilter)
        self.setCentralWidget(central)
        self.clock = ClockBar(self)
        self.addToolBar(QtCore.Qt.TopToolBarArea, self.clock)
        self.addToolBar(QtCore.Qt.TopToolBarArea, self.clock.page_bar)
        self.clock.act_run.triggered.connect(lambda: self._clock("run"))
        self.clock.act_pause.triggered.connect(lambda: self._clock("pause"))
        self.clock.act_step.triggered.connect(lambda: self._clock("step"))
        self.clock.speed.currentIndexChanged.connect(
            lambda k: self._clock("speed", self.clock.speed.itemData(k))
        )
        self.clock.user.currentIndexChanged.connect(lambda k: self._set_user(self.clock.user.itemData(k)))
        self.clock.act_errors.setChecked(bool(options.get("show_errors")))
        self.clock.act_numbers.setChecked(bool(options.get("show_numbers")))
        self.clock.act_errors.toggled.connect(self._debug_toggled)
        self.clock.act_settings.triggered.connect(self.open_settings)
        self.clock.act_reload_cicode.triggered.connect(self.reload_cicode)
        self.clock.act_open_page.triggered.connect(lambda: self.open_other_page())
        self.clock.act_inspect.triggered.connect(lambda: self.open_inspector())
        self.clock.act_zoom_fit.triggered.connect(lambda: self._zoom_command("fit"))
        self.clock.act_zoom_100.triggered.connect(lambda: self._zoom_command("100"))
        self.clock.act_zoom_in.triggered.connect(lambda: self._zoom_command("in"))
        self.clock.act_zoom_out.triggered.connect(lambda: self._zoom_command("out"))
        self.clock.act_record.toggled.connect(self._record_toggled)
        self.clock.act_play.triggered.connect(lambda: self.play_scenario())
        self.clock.act_stop_scenario.triggered.connect(self.stop_scenario)
        self.clock.act_save_snapshot.triggered.connect(lambda: self.save_snapshot())
        self.clock.act_load_snapshot.triggered.connect(lambda: self.load_snapshot())
        from .canvas import SimCanvas

        SimCanvas.keys_to_page = bool(options.get("keys_to_page"))
        self.clock.act_page_keys.setChecked(SimCanvas.keys_to_page)
        self.clock.act_page_keys.toggled.connect(self._page_keys_toggled)
        self._inspector = None
        # live reload: .ci files and libraries of the simulated projects, edits in the editor
        from .live_reload import LiveReload

        self.live = LiveReload(self._log_target, self)
        self.live.libraryChanged.connect(self._libraries_changed)
        self.live.pageChanged.connect(self._pages_changed)
        self._lib_generation = self._library_generation()
        # (the generation the pages that follow an editor document have followed: during the
        # chain after a library save in its helper they follow it, the others at its end)
        self._doc_lib_generation = self._lib_generation
        # (normcase path -> path: library changes seen while the editor ran the chain after a
        # library save or Update Pages' chain, looked at when it has ended: _live_tick)
        self._lib_held: dict = {}
        self._adopt_at = 0.0
        # docked in an editor window (_host, in its SimDock _dock) or a window of its own;
        # _opener = the editor window that last opened a simulation (Dock in Editor docks there)
        self._host = None
        self._dock: Optional[SimDock] = None
        self.installEventFilter(self)  # (the docked shortcut rule: eventFilter)
        self._opener = None
        self.clock.act_copy.triggered.connect(self.copy_image)
        # (docked in the editor window, Ctrl+Shift+C only while the focus is in here)
        self.addAction(self.clock.act_copy)
        self.clock.act_copy.setShortcutContext(QtCore.Qt.WidgetWithChildrenShortcut)
        # (Ctrl+O likewise: docked, the editor's File > Open keeps it outside this window)
        self.addAction(self.clock.act_open_page)
        self.clock.act_open_page.setShortcutContext(QtCore.Qt.WidgetWithChildrenShortcut)
        # (Ctrl+I likewise: the editor's Ctrl+I is Italic)
        self.addAction(self.clock.act_inspect)
        self.clock.act_inspect.setShortcutContext(QtCore.Qt.WidgetWithChildrenShortcut)
        self.clock.act_numbers.toggled.connect(self._debug_toggled)
        self.inputs = InputsPanel(self)
        # the Inputs dock: Tags, Alarms, Equipment (the alarm server and the equipment's
        # runtime fields of the simulation)
        from .alarm_panels import AlarmsPanel, EquipmentPanel

        self.alarms = AlarmsPanel(self)
        self.equipment = EquipmentPanel(self)
        self.input_tabs = QtWidgets.QTabWidget(self)
        self.input_tabs.setDocumentMode(True)
        self.input_tabs.setAccessibleName("Inputs")
        self.input_tabs.addTab(self.inputs, "Tags")
        self.input_tabs.addTab(self.alarms, "Alarms")
        self.input_tabs.addTab(self.equipment, "Equipment")
        from .watch_panel import WatchPanel

        self.watch = WatchPanel(self)
        self.input_tabs.addTab(self.watch, "Watch")
        _help.set_help(self.watch, "simulate#watch")
        _help.set_help(self.alarms, "simulate#alarms-and-equipment")
        _help.set_help(self.equipment, "simulate#alarms-and-equipment")
        self.input_tabs.currentChanged.connect(lambda _k: self._refresh_alarm_panels(True))
        self.dock_inputs = QtWidgets.QDockWidget("Inputs", self)
        self.dock_inputs.setObjectName("SimulateInputs")
        self.dock_inputs.setWidget(_scrolling(self.input_tabs))
        self.addDockWidget(QtCore.Qt.LeftDockWidgetArea, self.dock_inputs)
        _help.set_help(self.dock_inputs, "simulate#inputs")
        self.outputs = OutputsPanel(self)
        self.dock_outputs = QtWidgets.QDockWidget("Outputs", self)
        self.dock_outputs.setObjectName("SimulateOutputs")
        # (not in a scroll area: its own table scrolls, and its height is modest; the Inputs
        # panel, taller, keeps one)
        self.dock_outputs.setWidget(self.outputs)
        self.addDockWidget(QtCore.Qt.BottomDockWidgetArea, self.dock_outputs)
        self.outputs.showObject.connect(self.show_log_object)
        self.outputs.cleared.connect(self._log_cleared)
        self.outputs.inspectObject.connect(lambda e: self.open_inspector(getattr(e, "an", None)))
        self.outputs.editObject.connect(self.edit_log_object)
        _help.set_help(self.dock_outputs, "simulate#outputs")
        # (Alt+1 / Alt+2, as the editor's View > Panels keys: Ctrl+1 is Zoom 100% here too)
        for dock, key in ((self.dock_inputs, "Alt+1"), (self.dock_outputs, "Alt+2")):
            a = dock.toggleViewAction()
            a.setShortcut(QtGui.QKeySequence(key))
            a.setShortcutContext(QtCore.Qt.WindowShortcut)
            _set_tip(a, "Show or hide the %s panel" % dock.windowTitle())
            self.clock.panels_menu.addAction(a)
        # (Run, Pause, Step and the panels are the window's too: docked, their keys are its
        # own, as Copy Image's, Open Page's and the Inspector's: _key_scope; the key press
        # that ran one of them docked goes nowhere else: _KeyEater)
        for a in self._docked_scope_actions():
            self.addAction(a)
        self._key_eater = _KeyEater(self)
        # the commands' keys by Settings > Keyboard (keymap.SIM_COMMANDS), again when they
        # change there; the page's view and Space by Simulate Settings
        SimCanvas.open_zoom = options.open_zoom()
        SimCanvas.space_pans = bool(options.get("space_pans"))
        self.apply_keys()
        from ..prefs import keymap as _keymap

        _keymap.notifier().simKeysChanged.connect(self.apply_keys)
        from ..panels import docking

        docking.install_all(self)
        # the simulation's popup menus and Message / Input / Form dialogs (ctsim/dialog)
        from .sim_dialogs import DialogHost

        self.dialogs = DialogHost(self, self._page_of_window)
        self.dialogs.on_always = self._always_allow
        # the windows the page's Cicode opened that are not tabs: drawn inside their parent
        # window (pinned panes) or as popups (winhost); the view the Inputs panel follows (the
        # one last clicked in the current tab, else the tab)
        self._views: List[SimulatedPage] = []
        self._panel_view: Optional[SimulatedPage] = None
        # {(session, window): its entry} of the windows closed here while the engine's list
        # still has them as they were (their closePage is on its way): not opened again
        self._closed_here: Dict[tuple, dict] = {}
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self.tick)
        self.resize(1280, 820)
        # (at least as wide as its tabs and fields at the text size now)
        self.resizeDocks(
            [self.dock_inputs],
            [max(340, self.input_tabs.minimumSizeHint().width() + 24)],
            QtCore.Qt.Horizontal,
        )
        # (at least as tall as its content and its title bar: shorter, its own scroll bar
        # would show from the start)
        title = self.dock_outputs.fontMetrics().height() + 24  # (its title bar and frame)
        # (empty, it takes only what its controls need; the first lines give it its usual
        # height, once, unless it was resized by hand meanwhile)
        self._outputs_compact = self.outputs.minimumSizeHint().height() + title
        self._outputs_usual = max(200, self._outputs_compact)
        self.resizeDocks([self.dock_outputs], [self._outputs_compact], QtCore.Qt.Vertical)
        self._outputs_grown = False
        self.outputs.log.model().rowsInserted.connect(self._grow_outputs_once)
        # (its own-window size and panels are restored only when it shows as a window of its
        # own, never before it goes into a dock: Qt aborts showing a docked main window that
        # was given a maximized geometry before it was ever shown)
        self._own_layout_restored = False
        self.clock.show_state(None)

    # -- the window's size and panels, remembered ----------------------------------------
    def _restore_layout(self) -> None:
        try:
            from ..prefs.settings import app_settings

            s = app_settings()
            g = s.value("simulate/geometry")
            if g is not None:
                self.restoreGeometry(g)
            st = s.value("simulate/state")
            if st is not None:
                self.restoreState(st)
        except Exception:  # noqa: BLE001 -- a stored layout is a convenience only
            pass

    def _save_layout(self) -> None:
        """The window's size and panels as they are now: docked = the panels inside the
        dock and the dock's side and size; a window of its own = its geometry and panels."""
        try:
            from ..prefs.settings import app_settings

            s = app_settings()
            if self._dock is not None:
                s.setValue("simulate/docked_state", self.saveState())
                host = self._host
                if self._dock.isVisible():
                    s.setValue("simulate/dock_floating", bool(self._dock.isFloating()))
                    if self._dock.isFloating():
                        g = self._dock.geometry()
                        s.setValue(
                            "simulate/float_rect", "%d,%d,%d,%d" % (g.x(), g.y(), g.width(), g.height())
                        )
                if host is not None and self._dock.isVisible() and not self._dock.isFloating():
                    area = host.dockWidgetArea(self._dock)
                    if area != QtCore.Qt.NoDockWidgetArea:
                        s.setValue(
                            "simulate/dock_area", int(area.value) if hasattr(area, "value") else int(area)
                        )
                        across = area in (QtCore.Qt.LeftDockWidgetArea, QtCore.Qt.RightDockWidgetArea)
                        s.setValue(
                            "simulate/dock_size", self._dock.width() if across else self._dock.height()
                        )
            elif self.isWindow() and self.isVisible():
                s.setValue("simulate/geometry", self.saveGeometry())
                s.setValue("simulate/state", self.saveState())
        except Exception:  # noqa: BLE001
            pass

    # -- docked in the editor or a window of its own ------------------------------------
    def is_docked(self) -> bool:
        return self._dock is not None

    def host_window(self):
        """The editor window the Simulate window is docked in, or None."""
        return self._host if self._dock is not None else None

    def present(self, opener=None) -> None:
        """Show the window in the dock of ``opener`` (the editor window that opened the
        simulation), docked or floating as it was last (setting 'simulate/dock_floating');
        a window of its own only when no editor window is open."""
        if opener is not None and _alive(opener):
            self._opener = opener
        host = opener if opener is not None and _alive(opener) else self.host_window()
        if host is None:
            host = self._default_host()
        if host is not None:
            self.dock_into(host)
            return
        if not self._own_layout_restored:
            self._restore_layout()
            self._own_layout_restored = True
        self.show()
        _fit_to_screen(self)
        self.raise_()
        self.activateWindow()

    def status_message(self, text: str, level: str = "info", at_least_ms: int = 0) -> None:
        """``text`` in the window's status bar for its reading time, as the editor window's
        messages (ui/notify.reading_ms: longer for a longer text, times Settings >
        Appearance > Messages stay; a warning at least 10 s), and at least ``at_least_ms``
        times the setting (the time the message stayed before: no shorter at Normal)."""
        from ..notify import message_ms

        # (the status bar scales by the setting: ScaledStatusBar)
        self.statusBar().showMessage(text, message_ms(text, level, at_least_ms, scaled=False))

    @staticmethod
    def _default_host():
        """The editor window to dock in when none opened the simulation: the active one,
        else the first one shown."""
        try:
            from ..main_window import MainWindow
        except Exception:  # noqa: BLE001
            return None
        act = QtWidgets.QApplication.activeWindow()
        if isinstance(act, MainWindow) and _alive(act):
            return act
        for w in MainWindow._instances:
            if _alive(w) and w.isVisible():
                return w
        return None

    def dock_into(self, host) -> None:
        """Dock the window in ``host`` (an editor window): its tabs, clock bar and Inputs /
        Outputs panels go with it; a dock in another editor window is left."""
        if self._dock is not None and self._host is host:
            self._show_dock()
            return
        self._save_layout()  # this state's layout, before it changes
        self._leave_dock()
        from ..prefs.settings import app_settings

        s = app_settings()
        # the editor's size before: the Simulate window's minimum size, docked even for a
        # moment, can grow the editor past the screen; it is put back below
        host_geom = QtCore.QRect(host.geometry()) if not host.isMaximized() else None
        dock = getattr(host, "_sim_dock", None)
        if dock is None or not _alive(dock):
            dock = SimDock(host)
            host._sim_dock = dock
            try:
                area = QtCore.Qt.DockWidgetArea(
                    int(s.value("simulate/dock_area", int(QtCore.Qt.BottomDockWidgetArea.value)))
                )
            except Exception:  # noqa: BLE001
                area = QtCore.Qt.BottomDockWidgetArea
            if area not in (
                QtCore.Qt.LeftDockWidgetArea,
                QtCore.Qt.RightDockWidgetArea,
                QtCore.Qt.TopDockWidgetArea,
                QtCore.Qt.BottomDockWidgetArea,
            ):
                area = QtCore.Qt.BottomDockWidgetArea
            host.addDockWidget(area, dock)
        self.hide()
        dock.setWidget(self)  # (a child widget now: no window of its own)
        self._dock, self._host = dock, host
        self.windowTitleChanged.connect(self._dock_title)
        self._dock_title(self.windowTitle())
        host.installEventFilter(self)
        dock.installEventFilter(self)
        self._key_scope()
        if not getattr(dock, "_ctg_float_watch", False):
            # (floated or docked by its title bar: remembered at once)
            dock.topLevelChanged.connect(lambda _f: self._dock_floated(dock))
            dock._ctg_float_watch = True
        first = s.value("simulate/dock_floating") is None
        try:
            st = s.value("simulate/docked_state")
            if st is not None:
                self.restoreState(st)
            else:
                # the first layout: the page and the tag list get the room, Outputs a strip
                self.resizeDocks([self.dock_outputs], [120], QtCore.Qt.Vertical)
                self.resizeDocks([self.dock_inputs], [300], QtCore.Qt.Horizontal)
        except Exception:  # noqa: BLE001 -- a stored layout is a convenience only
            pass
        self.show()
        self._show_dock()
        try:
            size = int(s.value("simulate/dock_size", 0) or 0)
        except TypeError, ValueError:
            size = 0
        area = host.dockWidgetArea(dock)
        across = area in (QtCore.Qt.LeftDockWidgetArea, QtCore.Qt.RightDockWidgetArea)
        if size <= 0:
            size = max(240, int((host.width() if across else host.height()) * 0.4))
        host.resizeDocks([dock], [size], QtCore.Qt.Horizontal if across else QtCore.Qt.Vertical)
        # floating last time: floating again, where it was. The FIRST time a floating window
        # of a good size on the editor's screen (docked at the bottom it would squeeze the
        # editor's canvas and the page to a strip); drag it onto the editor to dock
        floating = first or str(s.value("simulate/dock_floating", "false")).lower() in ("1", "true")
        if first and not dock.isFloating():
            scr = (
                host.screen() if hasattr(host, "screen") else None
            ) or QtWidgets.QApplication.primaryScreen()
            g = scr.availableGeometry() if scr is not None else QtCore.QRect(0, 0, 1600, 900)
            w, h = int(g.width() * 0.7), int(g.height() * 0.75)
            c = host.frameGeometry().center() if host.isVisible() else g.center()
            x = max(g.left(), min(c.x() - w // 2, g.right() - w))
            y = max(g.top(), min(c.y() - h // 2, g.bottom() - h))
            docked_size = QtCore.QSize(dock.size())
            dock.setFloating(True)
            dock.setGeometry(x, y, w, h)

            def again(dock=dock, docked_size=docked_size, r=(x, y, w, h)):
                # (Qt gives a newly floated dock its docked size once shown; a size someone
                # set meanwhile is kept)
                if _alive(dock) and dock.isFloating() and dock.size() == docked_size:
                    dock.setGeometry(*r)
                if _alive(self) and s.value("simulate/docked_state") is None:
                    # (the first layout again at the window's own size: Outputs a strip)
                    self.resizeDocks([self.dock_outputs], [120], QtCore.Qt.Vertical)

            QtCore.QTimer.singleShot(0, again)
        elif floating and not dock.isFloating():
            try:  # (read first: floating saves the place it has)
                rect = tuple(int(v) for v in str(s.value("simulate/float_rect", "")).split(","))
            except TypeError, ValueError:
                rect = ()
            dock.setFloating(True)
            if len(rect) == 4 and rect[2] > 100 and rect[3] > 100:
                dock.setGeometry(*rect)
                # (and once it is shown: Qt gives a newly floated dock its docked size)
                QtCore.QTimer.singleShot(
                    0, dock, lambda: _alive(dock) and dock.isFloating() and dock.setGeometry(*rect)
                )
        set_docked_setting(True)
        self._show_dock_action()
        self._show_popups()
        if host_geom is not None:

            def keep_host(host=host, g=host_geom):
                if (
                    _alive(host)
                    and not host.isMaximized()
                    and (host.height() > g.height() or host.width() > g.width())
                ):
                    host.setGeometry(g)

            keep_host()
            QtCore.QTimer.singleShot(0, keep_host)

    def pop_out(self) -> None:
        """Make the window a window of its own again (at its last place and size)."""
        if self._dock is None:
            return
        self._save_layout()
        visible = self._dock.isVisible()
        self._leave_dock()
        self._restore_layout()
        self._own_layout_restored = True
        set_docked_setting(False)
        self._show_dock_action()
        if visible or self.tabs.count():
            self.show()
            _fit_to_screen(self)
            self.raise_()
            self.activateWindow()
        self._show_popups()

    def release_host(self, host) -> None:
        """``host`` (an editor window) closes: a simulation docked in it stops, like closing
        the Simulate window, and the window leaves the dock (it is kept, hidden, for the next
        Simulate Page; 'sim/docked' stays, so that one docks again)."""
        if self._dock is None or self._host is not host:
            if self._opener is host:
                self._opener = None
            return
        self._save_layout()
        self.shut_down()
        self._leave_dock()
        if self._opener is host:
            self._opener = None

    def _leave_dock(self) -> None:
        """Out of the dock it is in (if any): a hidden window of its own."""
        dock, host = self._dock, self._host
        self._dock = self._host = None
        if dock is None:
            return
        try:
            self.windowTitleChanged.disconnect(self._dock_title)
        except RuntimeError, TypeError:
            pass
        if host is not None and _alive(host):
            host.removeEventFilter(self)
        if _alive(dock):
            dock.removeEventFilter(self)
        self._key_scope()
        self._key_eater.disarm()
        self.hide()
        self.setParent(None)
        self.setWindowFlags(QtCore.Qt.Window)
        if _alive(dock):
            dock.hide()
        self._show_dock_action()

    def _dock_floated(self, dock) -> None:
        """The dock floated or docked (its title bar): remembered at once, and the popups,
        hidden while Qt moved the window, shown over the page again once it is placed."""
        if self._dock is not dock:
            return
        self._save_layout()

        def again():
            if self._dock is dock and _alive(dock):
                self._layout_views()
                self._show_popups()

        QtCore.QTimer.singleShot(0, again)

    def _show_dock(self) -> None:
        dock = self._dock
        if dock is None:
            return
        if not self.isVisible():
            self.show()  # (closed while docked: close() hid it)
        dock.show()
        dock.raise_()

    def _dock_title(self, title: str) -> None:
        if self._dock is not None:
            t = title[: -len(" - ctgedit")] if title.endswith(" - ctgedit") else title
            self._dock.setWindowTitle(t or "Simulate")

    def _show_dock_action(self) -> None:
        """Nothing to show: the dock floats and docks by itself."""

    def keyPressEvent(self, ev):  # noqa: N802 -- Qt naming
        if self._dock is not None:
            ev.accept()  # (not on to the editor window)
            return
        super().keyPressEvent(ev)

    def eventFilter(self, obj, ev):  # noqa: N802 -- Qt naming
        t = ev.type()
        # docked, the window is part of the editor window, whose menu shortcuts (Delete,
        # Ctrl+Z, Ctrl+C, the tool letters ...) would act on the page being edited: while the
        # focus is in the Simulate window (or a popup of it) its keys are the simulated
        # page's. (A filter on the window itself: with an event() override a popup's
        # simulation does not connect, tests/test_sim_windows.py popup_floats.)
        # A key of the window's own commands (Settings > Keyboard) runs that command here
        # (_run_own_key: Qt's shortcut met the editor's command on the same key).
        if (
            t == QtCore.QEvent.ShortcutOverride
            and self._dock is not None
            and (obj is self or isinstance(obj, winhost.PopupFrame))
        ):
            ev.accept()
            self._run_own_key(ev)
            return True
        if t == QtCore.QEvent.Resize and obj is getattr(self, "tabs", None):
            self._show_empty_note()
        if (
            obj is not None
            and (obj is self._host or obj is self._dock)
            and t in (QtCore.QEvent.Move, QtCore.QEvent.WindowStateChange)
        ):
            # (the popups stay over the page they are measured from)
            self._layout_views()
            self._show_popups()
        return super().eventFilter(obj, ev)

    # -- pages -----------------------------------------------------------------------
    def pages(self) -> List[SimulatedPage]:
        """Every simulated page shown: the tabs, then the windows shown inside a tab's page
        or over the window (views)."""
        return self.tab_pages() + list(self._views)

    def tab_pages(self) -> List[SimulatedPage]:
        return [self.tabs.widget(k) for k in range(self.tabs.count())]

    def views(self) -> List[SimulatedPage]:
        """The windows shown inside their parent window or as popups."""
        return list(self._views)

    def embedded(self) -> List[SimulatedPage]:
        return [v for v in self._views if v.host == winhost.EMBEDDED]

    def popups(self) -> List[SimulatedPage]:
        return [v for v in self._views if v.host == winhost.POPUP]

    def root_of(self, sp: SimulatedPage) -> SimulatedPage:
        """The tab ``sp`` is shown in or opened from."""
        guard = 0
        while sp.host != winhost.TAB and sp.parent_view is not None and guard < 64:
            sp = sp.parent_view
            guard += 1
        return sp

    def _grow_outputs_once(self, *_a) -> None:
        """The log's first lines: Outputs gets its usual height (it started compact while
        empty), unless it was resized by hand or floated meanwhile."""
        if self._outputs_grown:
            return
        self._outputs_grown = True
        dock = self.dock_outputs
        if dock.isFloating() or dock.isHidden() or abs(dock.height() - self._outputs_compact) > 4:
            return
        self.resizeDocks([dock], [self._outputs_usual], QtCore.Qt.Vertical)

    def _log_cleared(self) -> None:
        """Clear in Outputs: the error marks of the lines it showed go too (the current tab and
        the windows shown in it or opened from it, whose lines the log shows); an error that
        happens again marks its object again."""
        for v in self.family(self.current()):
            v.canvas.clear_errors()

    def family(self, tab: Optional[SimulatedPage]) -> List[SimulatedPage]:
        """``tab`` and the windows shown in it or opened from it."""
        if tab is None:
            return []
        return [tab] + [v for v in self._views if self.root_of(v) is tab]

    def current(self) -> Optional[SimulatedPage]:
        w = self.tabs.currentWidget()
        return w if isinstance(w, SimulatedPage) else None

    def tab_image(self, tab: Optional[SimulatedPage] = None) -> Optional[QtGui.QImage]:
        """The picture of tab ``tab`` (default the current one) at its page's own size, with
        the windows shown inside it and its popups (snapshot.tab_image)."""
        from .snapshot import tab_image

        tab = tab if tab is not None else self.current()
        return tab_image(self, self.root_of(tab)) if tab is not None else None

    def copy_image(self) -> bool:
        """Copy Image: the current tab's picture to the clipboard."""
        img = self.tab_image()
        if img is None:
            self.status_message("There is no page to copy.", at_least_ms=4000)
            return False
        QtGui.QGuiApplication.clipboard().setImage(img)
        self.status_message(
            "Copied a picture of the page (%d x %d)." % (img.width(), img.height()), at_least_ms=4000
        )
        return True

    def live_tools(self, project_dir: str):
        """The tools of a running simulation of a page in ``project_dir`` (the page the
        panels follow first), None when none runs: an editor field's hover shows its
        values."""
        if not project_dir or not self.isVisible():
            return None
        from .sim_tools import tools_of

        key = os.path.normcase(os.path.abspath(project_dir))
        for p in [self.panel_view()] + self.tab_pages() + list(self._views):
            if p is None or getattr(p, "left", False) or not getattr(p, "page_path", ""):
                continue
            if os.path.normcase(os.path.dirname(os.path.abspath(p.page_path))) != key:
                continue
            t = tools_of(p.sim)
            if t is not None and t.available():
                return t
        return None

    def find(self, page_path: str, views: bool = False) -> Optional[SimulatedPage]:
        """The tab showing ``page_path``; ``views`` = else a window shown inside a tab or
        over the window (an SA page opens in its workspace's pane, not a tab of its own)."""
        key = os.path.normcase(os.path.abspath(page_path)) if page_path else None
        if not key:
            return None
        for p in self.tab_pages() + (list(self._views) if views else []):
            if os.path.normcase(os.path.abspath(p.page_path)) == key and not getattr(p, "left", False):
                return p
        return None

    def open_page(
        self,
        page_path: str,
        page=None,
        sim=None,
        run: bool = True,
        demo: Optional[bool] = None,
        parsed=None,
        doc=None,
    ) -> SimulatedPage:
        """Simulate the page at ``page_path``."""
        have = self.find(page_path, views=True)
        if have is not None:
            if doc is not None and have.doc is None and have.attach_window is None:
                have.doc = doc
                # the tab shows the saved page: the editor's edits are new to it, a clean
                # document is what it shows
                have.doc_key = None if getattr(doc, "dirty", False) else _doc_key(doc)
            tab = self.root_of(have)
            if self.tabs.indexOf(tab) >= 0:
                self.tabs.setCurrentWidget(tab)
                self._tab_changed(self.tabs.currentIndex())
            self._show_edit_note()
            return have
        if sim is None:
            # the server boots while this thread derives the page and exports its model
            try:
                from .ctsim_source import prestart

                if not getattr(S, "DEMO", False):
                    prestart(page_path, engine_setting() or None)
            except Exception:  # noqa: BLE001
                pass
            # ... and the helper process makes the page's model meanwhile (the tab takes the
            # same job: page_model_job keeps it for the document)
            try:
                from ...sim import client as C

                if doc is not None and not getattr(S, "DEMO", False) and not C.disabled():
                    page_model_job(page_path, doc)
            except Exception:  # noqa: BLE001 -- the tab makes it, as before
                from ...infra import logsetup

                logsetup.note_exception("sim.window.open_page_model")
        if parsed is not None and page_path:
            try:
                page = runtime_page(
                    page_path, parsed, doc if getattr(doc, "parsed", None) is parsed else None
                )
            except Exception:  # noqa: BLE001 -- fall back to the model / the file
                if page is not None:
                    page = copy.deepcopy(page)
        elif page is not None:
            try:
                page = copy.deepcopy(page)
            except Exception:  # noqa: BLE001 -- read the file instead
                page = None
        autorun = bool(run and options.get("autorun"))
        sp = SimulatedPage(
            page_path, page=page, sim=sim, parent=self.tabs, doc=doc, run=autorun and sim is None
        )
        sp.demo = (sim is None and S.DEMO) if demo is None else bool(demo)
        self._add_tab(sp, page_path)
        if autorun:
            try:
                sp.sim.clock("run")
            except Exception:  # noqa: BLE001
                pass
        sp.tick()
        self._tab_changed(self.tabs.currentIndex())
        if not self._timer.isActive():
            self._timer.start()
        self.pageOpened.emit(sp)
        return sp

    def _navigate_in_new_tab(self, old: SimulatedPage, nav: dict) -> bool:
        """Simulate Settings > Open each page in a new tab: the page the window changed to
        gets a tab of its own on that window; the old tab keeps its last picture."""
        w = {
            "window": nav.get("window", old.engine_window()),
            "page": nav.get("page") or "",
            "path": nav.get("path") or "",
            "hasModel": nav.get("hasModel"),
        }
        if w["window"] is None:
            return False
        sp = self.open_window(old, w, how=winhost.TAB)
        if sp is None:
            return False
        sp.sim.navigated(
            w["page"] or sp.title(),
            w["path"],
            sp.page,
            None if _model_known(old.sim, w) else page_model_file_job(w["path"]),
        )
        old.leave(w["page"] or sp.title())
        k = self.tabs.indexOf(old)
        if k >= 0:
            self.tabs.setTabToolTip(
                k,
                "%s\nThe window moved on to %s (a tab of its own). "
                "%s"
                % (
                    old.page_path,
                    w["page"] or sp.title(),
                    "This tab shows the page as it was."
                    if getattr(old, "left", False)
                    else "This page runs on in a window of its own.",
                ),
            )
        self.tabs.setCurrentWidget(sp)
        return True

    def open_settings(self) -> None:
        """The Simulate options: the editor's Settings window at its Simulate page (the
        options apply when it closes with OK); with no editor window, a dialog of their own."""
        host = self._host if self._host is not None and _alive(self._host) else self._default_host()
        if host is not None:
            from ..prefs.settings_dialog import SettingsDialog

            dlg = SettingsDialog(host, page="Simulate")
            try:
                dlg.exec()  # (its OK stores and applies: apply_settings)
            finally:
                dlg.deleteLater()  # (not a hidden child of the editor from now on)
            return
        from .settings_dialog import SimSettingsDialog

        dlg = SimSettingsDialog(self, engine_path=engine_setting())
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        dlg.store()
        self.apply_settings(dlg.engine_path())

    def apply_settings(self, engine_path: Optional[str] = None) -> None:
        """The stored Simulate options to the open tabs and windows (after Settings' OK);
        ``engine_path`` = the ctsim.exe chosen (None: unchanged)."""
        # the options shown elsewhere follow
        self.clock.act_errors.setChecked(bool(options.get("show_errors")))
        self.clock.act_numbers.setChecked(bool(options.get("show_numbers")))
        self.inputs.simulate_undefined.setChecked(bool(options.get("simulate_undefined")))
        aliased = bool(options.get("aliased_text"))
        for sp in self.pages():
            with contextlib.suppress(Exception):
                sp.canvas.set_aliased_text(aliased)
            # (Settings > Canvas and Appearance: the scroll bars, the notes over the page)
            try:
                sp.apply_canvas_look()
            except RuntimeError:  # swallow-ok: a page closing while the settings apply
                continue
        fgb = bool(options.get("foreground_break"))
        for sp in self.pages():
            fn = getattr(sp.sim, "set_foreground_break", None)
            if fn is not None and getattr(sp.sim, "foreground_break", fgb) != fgb:
                fn(fgb)
        feb = bool(options.get("foreground_error_break"))
        fast = bool(options.get("fast_startup"))
        holder = options.get("value_placeholder")
        for sp in self.pages():
            fn = getattr(sp.sim, "set_foreground_error_break", None)
            if fn is not None and getattr(sp.sim, "foreground_error_break", feb) != feb:
                fn(feb)
            if hasattr(sp.sim, "fast_startup"):
                sp.sim.fast_startup = fast  # (the next start of its simulation)
            if getattr(sp.scene, "value_placeholder", holder) != holder:
                # (the page is read again with the new placeholder)
                sp.scene.value_placeholder = holder
                fn = getattr(sp, "reload_page", None) or getattr(sp, "reload", None)
                if fn is not None:
                    with contextlib.suppress(Exception):
                        fn()
        seed = bool(options.get("seed_trend_history"))
        for sp in self.pages():
            fn = getattr(sp.sim, "set_seed_trend_history", None)
            if fn is not None and getattr(sp.sim, "seed_trend_history", seed) != seed:
                fn(seed)
        self.apply_outside_settings()
        want = options.get("user")
        k = self.clock.user.findData(want)
        if k >= 0 and k != self.clock.user.currentIndex():
            self.clock.user.setCurrentIndex(k)
        # (the view of the pages opened from now; Space at once; Step's tool tip; with no
        # simulation shown, the Speed box shows what the next one starts at)
        SimCanvas.open_zoom = options.open_zoom()
        SimCanvas.space_pans = bool(options.get("space_pans"))
        _set_tip(self.clock.act_step, STEP_TIPS[options.step()])
        if self.current() is None:
            k = self.clock.speed.findData(options.start_speed())
            if k >= 0 and k != self.clock.speed.currentIndex():
                self.clock.speed.blockSignals(True)
                self.clock.speed.setCurrentIndex(k)
                self.clock.speed.blockSignals(False)
        if engine_path is not None and engine_path != engine_setting():
            self.set_engine_path(engine_path)
        # the log lines kept, the reload pauses, the Inspector's and the
        # Watch tab's refresh, the panels' tabs and title bars
        self.outputs.apply_settings()
        self.live.apply_settings()
        self.watch.apply_settings()
        if self._inspector is not None:
            with contextlib.suppress(RuntimeError):  # swallow-ok: an Inspector being closed
                self._inspector.apply_settings()
        from ..panels import docking

        docking.apply_settings(self)

    def sim_actions(self) -> dict:
        """The window's commands that Settings > Keyboard lists (keymap.SIM_COMMANDS): id ->
        QAction."""
        return {
            "sim.run": self.clock.act_run,
            "sim.pause": self.clock.act_pause,
            "sim.step": self.clock.act_step,
            "sim.open_page": self.clock.act_open_page,
            "sim.copy_image": self.clock.act_copy,
            "sim.inspector": self.clock.act_inspect,
            "sim.inputs": self.dock_inputs.toggleViewAction(),
            "sim.outputs": self.dock_outputs.toggleViewAction(),
            "sim.zoom_fit": self.clock.act_zoom_fit,
            "sim.zoom_100": self.clock.act_zoom_100,
            "sim.zoom_in": self.clock.act_zoom_in,
            "sim.zoom_out": self.clock.act_zoom_out,
        }

    def apply_keys(self) -> None:
        """Settings > Keyboard's keys of the window's commands (their built-in keys, else the
        user's own: keymap.sim_resolve) on the commands and their tool tips; the keys of Open
        Page, Copy Image and the Inspector stay the window's while the page has the keyboard
        (SimCanvas.RESERVED_KEYS)."""
        from ..prefs import keymap

        try:
            eff, _notes = keymap.sim_resolve()
        except Exception:  # noqa: BLE001 -- the keys the window was built with stay
            from ...infra import logsetup

            logsetup.note_exception("sim.window.apply_keys")
            return
        for cid, a in self.sim_actions().items():
            keys = eff.get(cid, ([], ""))[0]
            if [keymap.norm(s) for s in a.shortcuts()] != keys:
                a.setShortcuts([QtGui.QKeySequence(k) for k in keys])
            _set_tip(a)
        SimCanvas.RESERVED_KEYS = frozenset(
            k for cid in keymap.SIM_RESERVED for k in eff.get(cid, ([], ""))[0]
        )

    def _docked_scope_actions(self) -> list:
        """Run, Pause, Step, the two panels and the zoom commands: their keys work in the
        whole window when it is a window of its own, and in it alone when it is in the
        editor's dock (:meth:`_key_scope`). The keys of Open Page, Copy Image and the
        Inspector work in it alone always."""
        return [
            self.clock.act_run,
            self.clock.act_pause,
            self.clock.act_step,
            self.dock_inputs.toggleViewAction(),
            self.dock_outputs.toggleViewAction(),
            *self.clock.zoom_actions,
        ]

    def _key_scope(self) -> None:
        """In the editor's dock (floating or not) the keys of Run, Pause, Step and the panels
        work only while the focus is in this window: the editor's commands keep the same keys
        in the editor (Ctrl+1 is View > Zoom 100% there, F5 is File > Run); Qt would find
        both and run neither. In a window of its own they work in all of it."""
        ctx = QtCore.Qt.WidgetWithChildrenShortcut if self._dock is not None else QtCore.Qt.WindowShortcut
        for a in self._docked_scope_actions():
            if a.shortcutContext() != ctx:
                a.setShortcutContext(ctx)

    def _run_own_key(self, ev) -> None:
        """When docked, run the window's own command for a key nothing else took."""
        from ..prefs import keymap

        try:
            seq = keymap.norm(QtGui.QKeySequence(ev.keyCombination()))
            hit = next((a for a in self.sim_actions().values() if seq and seq in keymap.keys_of(a)), None)
        except RuntimeError:  # swallow-ok: a window being deleted runs nothing
            return
        if hit is None:
            return
        self._key_eater.arm(QtWidgets.QApplication.focusWidget() or self, ev)
        if hit.isEnabled() and hit.isVisible() and (hit.autoRepeat() or not ev.isAutoRepeat()):
            # (after the key event, as a shortcut's command: none of its own runs inside it)
            QtCore.QTimer.singleShot(0, hit, hit.trigger)

    def apply_outside_settings(self) -> None:
        """Simulate Settings > DLLs and databases (the two modes and the allowed lists) to
        every tab's simulation."""
        want = (
            options.access("dll_access"),
            options.access("sql_access"),
            tuple(options.get_list("dll_allowed")),
            tuple(options.get_list("sql_allowed")),
            options.dll_timeout(),
        )
        for sp in self.pages():
            fn = getattr(sp.sim, "set_outside", None)
            have = getattr(sp.sim, "outside_settings", None)
            if fn is not None and (have is None or have() != want):
                fn(want[0], want[1], list(want[2]), list(want[3]), want[4])

    def _always_allow(self, access: str, target: str) -> None:
        """A permission question was answered Always Allow: the DLL or database goes on the
        allowed list of the settings, for every simulation."""
        name = "dll_allowed" if access == "dll" else "sql_allowed"
        if not options.add_to_list(name, target):
            return
        # the lists only: the modes of a simulation another program started are its own
        dll, sql = options.get_list("dll_allowed"), options.get_list("sql_allowed")
        for sp in self.pages():
            fn = getattr(sp.sim, "set_outside_allowed", None)
            if fn is not None:
                fn(list(dll), list(sql))
        if hasattr(self, "status_message"):
            self.status_message(
                "%s added to %s (Settings > Simulate > DLLs and databases)."
                % (target, "Allowed DLLs" if access == "dll" else "Allowed Databases"),
                at_least_ms=8000,
            )

    # -- the zoom commands (the editor's, on the tab shown) -------------------------------
    def _zoom_command(self, what: str) -> None:
        """Fit Page / Zoom 100% / Zoom In / Zoom Out on the current tab's page (the editor's
        commands and keys)."""
        cur = self.current()
        if cur is None:
            return
        cv = cur.canvas
        if what == "fit":
            cv.fit_page(animate=True)
        elif what == "100":
            cv.reset_zoom()
        else:
            cv.zoom_step(1 if what == "in" else -1)

    # -- the tabs' menu (as the editor's document tabs) ---------------------------------
    def _tab_menu(self, k: int, pos: QtCore.QPoint) -> None:
        """Right-click on a tab: Close, Close Others, Reload Page, Edit the Page in the
        Editor (as the editor's tabs)."""
        m = self.build_tab_menu(k)
        if m is None:
            return
        m.exec(pos)
        m.deleteLater()

    def build_tab_menu(self, k: int) -> Optional[QtWidgets.QMenu]:
        """The menu of tab ``k`` (None: no simulated page there)."""
        sp = self.tabs.widget(k)
        if not isinstance(sp, SimulatedPage):
            return None
        m = QtWidgets.QMenu(self)
        m.addAction("Close").triggered.connect(lambda: self.close_tab(k))
        others = m.addAction("Close Others")
        others.setEnabled(self.tabs.count() > 1)
        others.triggered.connect(lambda: self._close_other_tabs(sp))
        m.addSeparator()
        m.addAction("Reload Page").triggered.connect(lambda: self._reload_tab(sp))
        m.addAction("Edit Page in Editor").triggered.connect(lambda: self.edit_in_editor(sp, None))
        return m

    def _close_other_tabs(self, keep: SimulatedPage) -> None:
        for sp in list(self.tab_pages()):
            if sp is not keep:
                self.close_view(sp)

    def _reload_tab(self, sp: SimulatedPage) -> None:
        self.tabs.setCurrentWidget(sp)
        self.reload_current()

    def _add_tab(self, sp: SimulatedPage, tip: str) -> int:
        sp.navigation_handler = self._navigate_in_new_tab
        sp.canvas.set_show_errors(self.clock.act_errors.isChecked())
        sp.canvas.show_numbers = self.clock.act_numbers.isChecked()
        k = self.tabs.addTab(sp, sp.title())
        sp.titleChanged.connect(lambda sp=sp: self._retitle(sp))
        sp.canvas.objectMenuRequested.connect(lambda ans, pos, sp=sp: self._object_menu(sp, ans, pos))
        self.tabs.setTabToolTip(k, tip)
        self.tabs.setCurrentIndex(k)
        return k

    # -- the object menu: the simulated page back to the editor ------------------------------
    def _object_menu(self, sp: SimulatedPage, ans, pos) -> None:
        """Shift + right-click on the page: edit or show the object in the editor, inspect it."""
        ans = tuple(a for a in (ans or ()) if isinstance(a, int) and a > 0)
        m = QtWidgets.QMenu(self)
        if ans:
            an = ans[0]
            m.addAction("Edit in Editor (AN %d)" % an).triggered.connect(lambda: self.edit_in_editor(sp, an))
            m.addAction("Show in Editor").triggered.connect(lambda: self.edit_in_editor(sp, an, sheet=False))
            m.addAction("Inspect Object").triggered.connect(lambda: self.open_inspector(an))
            if len(ans) > 1:
                under = m.addMenu("Objects Under the Pointer")
                for a in ans[1:9]:
                    under.addAction("Edit AN %d" % a).triggered.connect(
                        lambda _=False, a=a: self.edit_in_editor(sp, a)
                    )
        else:
            m.addAction("Edit Page in Editor").triggered.connect(lambda: self.edit_in_editor(sp, None))
        if sp is self.current():
            m.addSeparator()
            for a in self.clock.zoom_actions:
                m.addAction(a)
        m.exec(pos)
        m.deleteLater()

    def edit_in_editor(self, sp: SimulatedPage, an: Optional[int], sheet: bool = True) -> bool:
        """The object ``an`` of ``sp``'s page in the editor: its page opened (or shown) in the
        editor window the simulation came from, the object selected and centred, and with
        ``sheet`` its properties sheet open. The reverse of Outputs' Show on Page."""
        host = self.host_window()
        opener = getattr(self, "_opener", None)
        if host is None and opener is not None and _alive(opener):
            host = opener
        if host is None:
            host = self._default_host()
        path = getattr(sp, "page_path", "") or ""
        if host is None or not path or not os.path.isfile(path):
            self.status_message("No editor window is open to edit the page in.", at_least_ms=6000)
            return False
        try:
            host.open_document(path)
        except Exception:  # noqa: BLE001 -- said below
            from ...infra import logsetup

            logsetup.note_exception("simulate.edit_in_editor")
        doc = getattr(host, "doc", None)
        if doc is None or os.path.normcase(
            os.path.abspath(getattr(doc, "path", "") or "")
        ) != os.path.normcase(os.path.abspath(path)):
            self.status_message("The editor could not open %s." % os.path.basename(path), at_least_ms=6000)
            return False
        host.show()
        host.raise_()
        host.activateWindow()
        if an is None:
            return True
        index = next((i for i, o in enumerate(doc.page.objects) if o.an == an), None)
        if index is None:
            self.status_message(
                "Object AN %d is not on the page in the editor (the page "
                "changed since it was simulated)." % an,
                at_least_ms=6000,
            )
            return False
        host._goto_preview(index)
        if sheet:
            host._open_sheet_of(index)
        return True

    def edit_log_object(self, e) -> bool:
        """Outputs' Edit in Editor: the line's object in the editor."""
        an = getattr(e, "an", -1) or -1
        if an <= 0:
            return False
        cur = self.current()
        order = ([cur] if cur is not None else []) + [p for p in self.pages() if p is not cur]
        for p in order:
            if p.owns(e):
                return self.edit_in_editor(p, an)
        self.status_message("Object AN %d is not on an open page." % an, at_least_ms=5000)
        return False

    def _retitle(self, sp: SimulatedPage) -> None:
        if sp.frame is not None:
            sp.frame.set_title(sp.window_title())
        k = self.tabs.indexOf(sp)
        if k >= 0:
            self.tabs.setTabText(k, self._tab_text(sp))
            self.tabs.setTabToolTip(k, sp.page_path)
        if sp is self.current():
            self.setWindowTitle("Simulate %s - ctgedit" % sp.title())
        self._show_edit_note()

    def open_window(
        self, owner: SimulatedPage, w: dict, how: Optional[str] = None
    ) -> Optional[SimulatedPage]:
        """Show a window the engine opened, the way the runtime would."""
        from .ctsim_source import EngineSimulation

        path = w.get("path") or ""
        if not path or not os.path.isfile(path):
            return None
        if how is None:
            how = winhost.placement(w, bool(options.get("tab_per_window")))
        osim = owner.sim
        # the pane an SA page went to (its tab's own page, opened in its workspace): drawn as
        # the tab had it, not read again from the file
        page = None
        doc = None
        opened = getattr(owner, "opened_with", None)
        if (
            opened
            and opened[1] is not None
            and getattr(osim, "workspace_page", "")
            and os.path.normcase(os.path.abspath(opened[0])) == os.path.normcase(os.path.abspath(path))
        ):
            page = opened[1]
            owner.opened_with = None  # one scene draws it (a second window reads the file)
            # ... with the editor's document it came from (its edits reload the pane)
            doc = getattr(owner, "opened_doc", None) or (owner.doc, owner.doc_key)
            owner.opened_doc = None
        sim = EngineSimulation(
            engine_path=getattr(osim, "engine_path", None),
            hub=getattr(osim, "_hub", None),
            location=getattr(osim, "location", None),
            folder=getattr(osim, "folder", None),
            page_name=w.get("page") or "",
        )
        try:
            sp = SimulatedPage(
                path,
                page=page,
                sim=sim,
                parent=self.tabs if how == winhost.TAB else None,
                attach_window=int(w["window"]),
                project=getattr(osim, "project", "") or "",
                model=None if _model_known(osim, w) else page_model_file_job(path),
            )
        except Exception:  # noqa: BLE001 -- a page that cannot be read is not shown
            from ...infra import logsetup

            logsetup.note_exception("sim.window.open_window")
            return None
        sp.demo = False
        if doc is not None and doc[0] is not None:
            sp.doc, sp.doc_key = doc
            sp._note_libraries()
        # the SA page's own workspace pane: a reload shows it again through the workspace
        sim.sa_pane = bool(getattr(osim, "workspace_page", "")) and (
            doc is not None or w.get("window") == getattr(osim, "focus_window", None)
        )
        sp.owner = owner
        sp.owner_session = getattr(osim, "session", None)
        sp.engine_info = dict(w)
        if how == winhost.TAB:
            self._add_tab(sp, "%s (window %s, opened by %s)" % (path, w["window"], owner.title()))
        else:
            sp.host = how
            sp.parent_view = owner
            owner.child_views.append(sp)
            self._views.append(sp)
            sp.canvas.set_show_errors(self.clock.act_errors.isChecked())
            sp.canvas.show_numbers = self.clock.act_numbers.isChecked()
            sp.canvas.inputEvent.connect(lambda e, sp=sp: self._view_used(sp, e))
            sp.titleChanged.connect(lambda sp=sp: self._retitle(sp))
            if how == winhost.EMBEDDED:
                sp.setParent(owner.canvas.viewport())
                # the wheel over a pane goes to the window it is drawn in: the tab, or the
                # popup it is a pane of (which scales itself)
                sp.canvas.forward = self._wheel_owner(owner).canvas
                sp.setToolTip("")
                self._watch_canvas(owner)
                self._layout_view(sp)
                sp.show()
                sp.raise_()
            else:
                frame = winhost.PopupFrame(sp, sp.window_title(), self)
                sp.frame = frame
                # its own zoom once the user scales it (the wheel, the corner grip); a
                # double-click on its caption follows the page's zoom again
                sp.own_zoom = None
                sp.canvas.zoomRequested.connect(lambda steps, sp=sp: self._zoom_popup(sp, steps))
                frame.resizedByUser.connect(lambda size, sp=sp: self._popup_resized(sp, size))
                frame.resetZoom.connect(lambda sp=sp: self._reset_popup_zoom(sp))
                frame.installEventFilter(self)  # (docked: its keys are not the editor's)
                frame.closeRequested.connect(lambda sp=sp: self.close_view(sp))
                # (it follows the zoom and scrolling of the tab it is measured from)
                self._watch_canvas(self.root_of(sp))
                self._layout_view(sp)
                if self.root_of(sp) is self.current() and self.isVisible():
                    frame.show()
                    frame.raise_()
        sp.tick()
        self._tab_changed(self.tabs.currentIndex())
        return sp

    def _wheel_owner(self, view: SimulatedPage) -> SimulatedPage:
        """The window a pane's wheel goes to: up its pinned parents to the first that is not
        pinned (a tab or a popup)."""
        v = view
        while v.host == winhost.EMBEDDED and v.parent_view is not None:
            v = v.parent_view
        return v

    #: a popup's own zoom stays in this range
    POPUP_ZOOM = (0.1, 8.0)

    def _popup_zoom_now(self, sp: SimulatedPage) -> float:
        """The zoom a popup is drawn at now: its own, else the page's it opened over."""
        own = getattr(sp, "own_zoom", None)
        if own is not None:
            return own
        info = sp.engine_info or {}
        anchor = sp.parent_view if sp.parent_view is not None and winhost.is_child(info) else self.root_of(sp)
        return anchor.canvas.transform().m11()

    def _set_popup_zoom(self, sp: SimulatedPage, z: Optional[float]) -> None:
        lo, hi = self.POPUP_ZOOM
        sp.own_zoom = None if z is None else max(lo, min(hi, float(z)))
        self._layout_view(sp)
        self._layout_children(sp)

    def _zoom_popup(self, sp: SimulatedPage, steps: float) -> None:
        """The wheel over a popup: it scales itself, the page under it stays (by the wheel
        zoom step of Settings > Canvas: ``steps`` = notches)."""
        from ..editor.viewnav import WheelPrefs

        step = 1.0 + WheelPrefs.from_settings().step / 100.0
        self._set_popup_zoom(sp, self._popup_zoom_now(sp) * (step**steps))

    def _popup_resized(self, sp: SimulatedPage, size: QtCore.QSize) -> None:
        """The corner grip: the popup's page fits the new size (its shape kept)."""
        pr = sp.scene.page_rect()
        info = sp.engine_info or {}
        vw, vh = winhost.view_size(info, pr.width(), pr.height())
        if vw <= 0 or vh <= 0 or size.width() < 8 or size.height() < 8:
            return
        self._set_popup_zoom(sp, min(size.width() / vw, size.height() / vh))

    def _reset_popup_zoom(self, sp: SimulatedPage) -> None:
        self._set_popup_zoom(sp, None)

    def _watch_canvas(self, owner: SimulatedPage) -> None:
        """The windows drawn inside ``owner``'s page follow its zoom and scrolling at once."""
        if getattr(owner, "_children_watched", False):
            return
        owner._children_watched = True
        again = lambda *_a, o=owner: self._layout_children(o)  # noqa: E731
        owner.canvas.zoomChanged.connect(again)
        owner.canvas.horizontalScrollBar().valueChanged.connect(again)
        owner.canvas.verticalScrollBar().valueChanged.connect(again)

    def _layout_children(self, owner: SimulatedPage) -> None:
        for c in list(owner.child_views):
            try:
                self._layout_view(c)
            except RuntimeError:  # (deleted while a signal was queued)
                continue
            if c.host in (winhost.EMBEDDED, winhost.POPUP):
                # (a popup's panes follow the popup's own zoom)
                self._layout_children(c)

    def _layout_views(self) -> None:
        # (a move / resize event can come before __init__ made the list)
        for sp in list(getattr(self, "_views", ())):
            try:
                self._layout_view(sp)
            except RuntimeError:
                continue

    def _layout_view(self, sp: SimulatedPage) -> None:
        """Put a pinned child at its place in its parent's page (and its page at the parent's
        zoom), a popup at its place on the screen (measured from the tab's page)."""
        info = sp.engine_info or {}
        pr = sp.scene.page_rect()
        pw, ph = (pr.width(), pr.height()) if not pr.isNull() else (0.0, 0.0)
        vw, vh = winhost.view_size(info, pw, ph)
        if vw <= 0 or vh <= 0:
            return
        sx, sy = winhost.content_scale(info, pw, ph)
        shown = QtCore.QRectF(0, 0, vw / sx, vh / sy)
        r = winhost.window_rect(info)
        if sp.host == winhost.EMBEDDED and sp.parent_view is not None:
            oc = sp.parent_view.canvas
            zx, zy = oc.transform().m11(), oc.transform().m22()
            tl = oc.mapFromScene(QtCore.QPointF(r.x(), r.y()))
            geom = QtCore.QRect(tl, QtCore.QSize(max(1, int(round(vw * zx))), max(1, int(round(vh * zy)))))
            sp.canvas.set_fixed_view(shown, zx * sx, zy * sy)
            if sp.geometry() != geom:
                sp.setGeometry(geom)
            return
        if sp.host == winhost.POPUP and sp.frame is not None:
            # a child window's (x, y) is in its parent window's page (AssPopUp: the pointer
            # in the window), any other window's on the screen = the root tab's page
            # (less the root window's own place on that screen)
            anchor = sp.parent_view if sp.parent_view is not None and winhost.is_child(info) else None
            if anchor is None:
                root = self.root_of(sp)
                rinfo = root.engine_info or {}
                ox = winhost._int(rinfo.get("x"))
                oy = winhost._int(rinfo.get("y"))
                rc = root.canvas
            else:
                ox = oy = 0
                rc = anchor.canvas
            # (its own zoom once the user scaled it; its place still follows the page)
            own = getattr(sp, "own_zoom", None)
            z = own if own is not None else rc.transform().m11()
            at = rc.viewport().mapToGlobal(rc.mapFromScene(QtCore.QPointF(r.x() - ox, r.y() - oy)))
            sp.canvas.set_fixed_view(shown, z * sx, z * sy)
            sp.frame.place(at, QtCore.QSize(max(1, int(round(vw * z))), max(1, int(round(vh * z)))))

    def _show_popups(self) -> None:
        """The popups of the current tab are shown (over the window), the others hidden."""
        cur = self.current()
        visible = self.isVisible() and not self.window().isMinimized()
        for sp in self.popups():
            if sp.frame is None:
                continue
            on = visible and self.root_of(sp) is cur
            if on != sp.frame.isVisible():
                sp.frame.setVisible(on)

    def _view_used(self, sp: SimulatedPage, e) -> None:
        """A click in a window shown inside a tab or as a popup: the Inputs panel follows that
        window (its page's tags, its alarms)."""
        if getattr(e, "kind", "") != "down" or self._panel_view is sp:
            return
        self._panel_view = sp
        self._set_panel_sources()

    def panel_view(self) -> Optional[SimulatedPage]:
        """The page the Inputs panel follows: the window last clicked in the current tab,
        else the tab."""
        cur = self.current()
        v = self._panel_view
        if v is not None and v in self._views and cur is not None and self.root_of(v) is cur:
            return v
        return cur

    def _set_panel_sources(self) -> None:
        v = self.panel_view()
        sim = v.sim if v is not None else None
        self.outputs.expressions = v.expressions_of if v is not None else None
        self.inputs.set_source(sim)
        self.alarms.set_source(sim)
        self.equipment.set_source(sim)
        self.watch.set_source(sim, v.page_path if v is not None else None)

    @staticmethod
    def _tab_key(p: "SimulatedPage"):
        """(session, engine window) a view shows. A view on a window Cicode opened counts for
        that window from the moment it is made (its own connection, and so its session and
        window, come a moment later): keyed by what its connection said alone it would not
        be found, and the next windowsChanged would open the same window again (a second
        tab)."""
        s = getattr(p.sim, "session", None) or getattr(p, "owner_session", None)
        w = getattr(p, "attach_window", None)
        if w is None:
            w = p.engine_window()
        return (id(s), w) if w is not None and s is not None else None

    def _follow_windows(self) -> None:
        """Views for the windows the engine opened from a view's page (pinned panes inside
        it, popups over the window, or tabs); the views of windows the engine freed close."""
        for p in list(self.pages()):
            if p not in self.pages():
                continue
            if p.attach_window is not None and getattr(p.sim, "window_closed", lambda: False)():
                self.close_view(p, gone=True)
                continue
            if p.host != winhost.TAB:
                # a pane or popup whose connection ended (the simulator stopped) closes: it
                # would only show demo values over its parent
                if getattr(p.sim, "session", None) is not None:
                    p._had_session = True
                elif getattr(p, "_had_session", False):
                    self.close_view(p, gone=True)
        shown = {k for k in (self._tab_key(p) for p in self.pages()) if k is not None}
        opening = {
            id(getattr(p.sim, "session", None) or getattr(p, "owner_session", None))
            for p in self.pages()
            if self._tab_key(p) is None
        }
        for p in list(self.pages()):
            if p not in self.pages():
                continue
            fn = getattr(p.sim, "engine_windows", None)
            if fn is None:
                continue
            serial, wins = fn()
            mine = p.engine_window()
            key = id(getattr(p.sim, "session", None))
            by_number = {w["window"]: w for w in wins}
            # the windows of their own that showed a page before this page opened: another
            # viewer's (a simulator shared with VS Code or another ctgedit), never shown here
            before = getattr(p.sim, "windows_before", lambda: None)() or frozenset()
            if mine in by_number:
                p.engine_info = dict(by_number[mine])
            # a window closed here leaves the engine's list once its closePage arrived (or the
            # number is a new window's)
            for c, info in list(self._closed_here.items()):
                if c[0] == key and by_number.get(c[1]) != info:
                    del self._closed_here[c]
            # the windows shown from this page: their place now; gone (freed, or the number
            # now another window's child) -> closed
            for c in list(p.child_views):
                cw = by_number.get(c.attach_window)
                if cw is None or cw.get("parent", -1) not in (mine, -1):
                    self.close_view(c, gone=True)
                    shown.discard((key, c.attach_window))
                    continue
                if c.engine_info != cw:
                    c.engine_info = dict(cw)
                    if c.frame is not None:
                        c.frame.set_title(c.window_title())
            for w in wins:
                n = w["window"]
                if n == mine or (key, n) in shown or (key, n) in self._closed_here:
                    continue
                parent = w.get("parent", -1)
                if parent != mine:
                    # a window of its own nobody shows (WinNew): from the tab, once every
                    # page of the simulation knows its window (a tab still opening would get
                    # its own page twice)
                    if (
                        parent != -1
                        or p.host != winhost.TAB
                        or key in opening
                        or not w.get("page")
                        or n in before
                    ):
                        continue
                if self.open_window(p, w) is not None:
                    shown.add((key, n))
            # an SA page opened in its workspace (sa_start.py): the tab of the window it went
            # to is the one shown, not the last pane opened
            focus = getattr(p.sim, "focus_window", None)
            if focus is not None:
                tab = next((t for t in self.tab_pages() if self._tab_key(t) == (key, focus)), None)
                if tab is not None:
                    p.sim.focus_window = None
                    self.tabs.setCurrentWidget(tab)
                    self._tab_changed(self.tabs.currentIndex())
        self._layout_views()
        self._show_popups()

    def close_tab(self, k: int) -> None:
        w = self.tabs.widget(k)
        if w is None:
            return
        if isinstance(w, SimulatedPage):
            self.close_view(w)
            return
        self.tabs.removeTab(k)
        w.deleteLater()
        if not self.tabs.count():
            self._timer.stop()
            self._tab_changed(-1)

    def close_view(self, sp: SimulatedPage, gone: bool = False) -> None:
        """Close a tab or a window shown inside one or over the window, with the windows shown
        in it or opened from it (their engine windows close with it); ``gone`` = the engine
        freed its window already (no closePage)."""
        closing = self._tab_key(sp) if not gone and sp.attach_window is not None else None
        for c in list(sp.child_views):
            self.close_view(c, gone=gone)
        for v in list(self._views):
            if v.parent_view is sp:
                self.close_view(v, gone=gone)
        k = self.tabs.indexOf(sp)
        if k >= 0:
            self.tabs.removeTab(k)
        if sp in self._views:
            self._views.remove(sp)
        if sp.parent_view is not None and sp in sp.parent_view.child_views:
            sp.parent_view.child_views.remove(sp)
        if self._panel_view is sp:
            self._panel_view = None
        # the dialogs a task of its window waits on are cancelled (the task goes on)
        fn = getattr(sp.sim, "viewer", None)
        svc = fn() if fn is not None else None
        if svc is not None:
            self.dialogs.cancel_window(svc, sp.engine_window())
        if gone:
            try:
                with sp.sim._lock:
                    sp.sim._window_gone = True  # freed in the engine: no closePage
            except Exception:  # noqa: BLE001 -- another source
                pass
        sp.stop()
        if closing is not None:
            self._closed_here[closing] = dict(sp.engine_info)
        frame, sp.frame = sp.frame, None
        if frame is not None:
            frame.hide()
            frame.deleteLater()
        else:
            sp.hide()
            sp.deleteLater()
        self.live.sync(self._sessions())
        if not self.tabs.count():
            self._timer.stop()
            self._tab_changed(-1)
        elif k < 0:
            self._set_panel_sources()

    def reload_current(self) -> None:
        """Reload Page: the current tab simulates its page as the editor has it now."""
        cur = self.current()
        if cur is None:
            return
        try:
            cur.reload_from_doc()
        except Exception as exc:  # noqa: BLE001 -- say why, keep the simulation
            QtWidgets.QMessageBox.warning(self, "Reload Page", "The page could not be reloaded. %s" % exc)
        self._show_edit_note()

    def _show_edit_note(self) -> None:
        cur = self.current()
        # with the automatic reload on, the page reloads by itself after the last edit and
        # says so over the page (show_reloading): no line over the page for it
        edited = bool(cur is not None and cur.doc_changed())
        saved = bool(cur is not None and getattr(cur, "file_changed", False))
        # (the editor's own update of the page, not reloaded by itself: _live_tick)
        behind = bool(cur is not None and getattr(cur, "auto_behind", False))
        on = ((edited or saved) and not options.get("reload_page")) or behind
        if on:
            self.edit_note.setText(
                "The page changed in the editor."
                if edited
                else "The page was saved by another program."
                if saved
                else "The editor updated this page from its libraries."
            )
        self.edit_row.setVisible(on)

    # -- live reload (live_reload.py) ------------------------------------------------------
    @staticmethod
    def _library_generation() -> int:
        try:
            from ... import editing

            return int(editing._LIBRARY_GENERATION[0])
        except Exception:  # noqa: BLE001
            return 0

    def _sessions(self) -> list:
        out = {}
        for p in self.pages():
            s = getattr(p.sim, "session", None)
            if s is not None and getattr(s, "alive", False):
                out[id(s)] = s
        return list(out.values())

    def _log_target(self, session):
        """The simulation whose Outputs show a session's reload lines: the current tab's when it
        is of that session, else the first page of it."""
        cur = self.current()
        order = ([cur] if cur is not None else []) + self.pages()
        for p in order:
            if getattr(p.sim, "session", None) is session:
                return p.sim
        return None

    def _pick_page(self, title: str) -> Optional[str]:
        cur = self.current()
        from ..browser import PageBrowserDialog

        return PageBrowserDialog.get_open_path(self, start_path=cur.page_path if cur is not None else None)

    def open_other_page(self, path: Optional[str] = None) -> Optional["SimulatedPage"]:
        """Open Page: another page in a new tab, in the current tab's simulation (the same
        project, simulator and server: its tags, alarms, clock and startup code), in a window
        of its own on the server. A page open here already is shown. ``path`` = the page
        (else the page browser asks)."""
        path = path or self._pick_page("Open Page")
        if not path:
            return None
        cur = self.current()
        osim = cur.sim if cur is not None else None
        sim = None
        if osim is not None and getattr(osim, "session", None) is not None:
            from .ctsim_source import EngineSimulation

            name = os.path.splitext(os.path.basename(path))[0]
            sim = EngineSimulation(
                engine_path=getattr(osim, "engine_path", None),
                hub=getattr(osim, "_hub", None),
                location=getattr(osim, "location", None),
                folder=getattr(osim, "folder", None),
                page_name=name,
            )
        return self.open_page(path, sim=sim)

    def reload_cicode(self) -> None:
        """Reload Cicode: the changed .ci files of the current tab's simulation, compiled again."""
        cur = self.current()
        s = getattr(cur.sim, "session", None) if cur is not None else None
        if s is None or not getattr(s, "alive", False):
            self.status_message(
                "Reload Cicode: the page is not running in the Cicode simulator.", at_least_ms=5000
            )
            return
        self.live.sync(self._sessions())
        n = self.live.reload_now(s)
        if n:
            self.status_message(
                "Reload Cicode: %d file%s sent to the simulator." % (n, "" if n == 1 else "s"),
                at_least_ms=5000,
            )

    def _libraries_changed(self, paths) -> None:
        """Library files changed on disk: drop caches and reload pages that use them."""
        from .live_reload import _key, editor_wrote, library_run_going

        if library_run_going():
            for p in paths or ():
                self._lib_held[_key(p)] = p
            return
        paths = [p for p in paths or () if not editor_wrote(p)]
        if not paths:
            return
        before = self._library_generation()
        try:
            from ... import editing

            editing.libraries_changed()
        except Exception:  # noqa: BLE001
            from ...infra import logsetup

            logsetup.note_exception("sim.window.libraries_changed")
        if self._lib_generation == before:
            # (a change of the editor's not followed yet is left to the next tick)
            self._lib_generation = self._library_generation()
            self._doc_lib_generation = self._lib_generation
        _MODEL_CACHE.clear()
        if not options.get("reload_page"):
            return
        for p in self.pages():
            if p.libraries_changed(paths):
                self._library_reload(p, paths)

    def _pages_changed(self, paths) -> None:
        """Page files changed on disk: reload the tabs that show them."""
        from .live_reload import _key, editor_documents, file_stamp

        keys = {_key(p) for p in paths}
        auto = bool(options.get("reload_page"))
        for p in self.pages():
            if getattr(p, "left", False) or not p.page_path or _key(p.page_path) not in keys:
                continue
            if p.doc is not None and any(d is p.doc for d in editor_documents(p.page_path)):
                continue
            if file_stamp(p.page_path) == getattr(p, "file_stamp", None):
                continue
            # (a document closed in the editor: the tab follows the file from now on)
            p.doc = None
            p.doc_key = None
            p.file_changed = True
            if auto:
                self._schedule_reload(p, "its file was saved by another program")
        self._show_edit_note()

    def _follow_editor_documents(self) -> None:
        """A tab that follows an editor document whose page the editor reloaded from disk
        (Changed on Disk, Revert to Saved: a new document in the same tab) follows the new
        document, and reloads (or says so)."""
        from .live_reload import editor_documents

        for p in self.pages():
            if p.doc is None or getattr(p, "left", False):
                continue
            docs = editor_documents(p.page_path)
            if not docs or any(d is p.doc for d in docs):
                continue
            p.doc = docs[0]
            p.doc_key = None  # (what it shows is not the new document)
            p.pending_why = "the editor reloaded it from disk"

    def document_renamed(self, doc, path: str) -> None:
        """File > Save As in the editor: the tabs that follow ``doc`` show the file at
        ``path`` from now on (their title, what a change on disk is matched against, and
        the file they fall back to when the document is closed)."""
        for p in self.pages():
            if p.doc is not doc or getattr(p, "left", False):
                continue
            p.page_path = path
            if getattr(p, "page", None) is not None:
                with contextlib.suppress(Exception):
                    p.page.name = os.path.splitext(os.path.basename(path))[0]
            p._note_file()
            p.titleChanged.emit()
        self._show_edit_note()

    def _library_reload(self, sp: "SimulatedPage", paths=None) -> None:
        """Reload after a library change, once the editor's genie update has landed."""
        from .live_reload import editor_genie_update_pending, editor_library_watches

        why = "a library it uses changed"
        wins = editor_library_watches(sp.doc, paths) if paths and sp.doc is not None else []
        if wins:
            for w in wins:
                with contextlib.suppress(RuntimeError):
                    w._on_library_files_changed(list(paths))
            sp.lib_wait = why
        elif editor_genie_update_pending(sp.doc):
            sp.lib_wait = why
        else:
            self._schedule_reload(sp, why, library=True)

    def _schedule_reload(self, sp: "SimulatedPage", why: str, library: bool = False) -> None:
        """``sp`` reloads after the reload pause; ``library``: for a library change, which
        waits while the editor runs something that writes libraries (_live_tick)."""
        from .live_reload import page_delay_s

        sp.reload_due = time.monotonic() + page_delay_s()
        sp.reload_why = why
        sp.reload_lib = library

    def _adopt_documents(self) -> None:
        """A window the page's Cicode opened (a popup, a pane) whose page is open in the editor
        follows that document (its edits reload it)."""
        from .live_reload import editor_document

        for p in self.pages():
            if p.doc is not None or p.attach_window is None or getattr(p, "left", False):
                continue
            d = editor_document(p.page_path)
            if d is None:
                continue
            p.doc = d
            # (it shows the file: the editor's unsaved edits are new to it)
            p.doc_key = None if getattr(d, "dirty", False) else _doc_key(d)

    def _live_tick(self) -> None:
        """Every tick: the live reload follows the simulations shown; a page edited in the
        editor (or whose library changed) reloads about a second after the last change."""
        sessions = self._sessions()
        self.live.sync(sessions)
        cur = self.current()
        s = getattr(cur.sim, "session", None) if cur is not None else None
        self.clock.act_reload_cicode.setEnabled(s is not None and s in sessions)
        now = time.monotonic()
        from .live_reload import library_run_going, run_holds_documents

        running = library_run_going()
        # (the tabs that follow an editor document wait for the run too: a run in this
        # process, or with Settings 'Open tabs wait for the update of what uses a saved
        # genie'; else they follow the editor's tabs, which take each library write at once)
        docs_held = running and run_holds_documents()
        if self._lib_held and not running:
            # the editor's run that wrote libraries has ended: what changed meanwhile (its
            # own writes are left to its generation, below)
            held, self._lib_held = list(self._lib_held.values()), {}
            self._libraries_changed(held)
        gen = self._library_generation()
        auto = bool(options.get("reload_page"))
        if gen != self._lib_generation and not running:
            # (while such a run goes on, its saves move the generation one by one: the pages
            # reload once, at its end)
            # ctgedit wrote a library (an element saved): the pages whose libraries changed
            self._lib_generation = gen
            self._doc_lib_generation = gen
            _MODEL_CACHE.clear()
            if auto:
                for p in self.pages():
                    if p.libraries_changed():
                        self._library_reload(p)
        elif running and not docs_held and gen != self._doc_lib_generation:
            # (the chain after a library save runs in its helper: the editor's tabs took a
            # library it wrote, or the user's save; the pages that follow an editor document
            # follow it now, once the editor's update of the document has landed. The others
            # wait for the run's end, when the generation above moves for them)
            self._doc_lib_generation = gen
            if auto:
                for p in self.pages():
                    if p.doc is not None and p.libraries_changed():
                        self._library_reload(p)
        if now >= self._adopt_at:
            self._adopt_at = now + 0.5
            try:
                self._follow_editor_documents()
                if auto:
                    self._adopt_documents()
            except Exception:  # noqa: BLE001 -- must not stop the polling
                from ...infra import logsetup

                logsetup.note_exception("sim.window.adopt_documents")
            if not auto:
                self._show_edit_note()
        if not auto:
            return
        from .live_reload import editor_genie_update_pending, editor_library_check_pending, page_delay_s

        for p in list(self.pages()):
            if getattr(p, "left", False):
                continue
            held = docs_held if p.doc is not None else running
            if held and (
                getattr(p, "lib_wait", "") or (p.reload_due is not None and getattr(p, "reload_lib", False))
            ):
                # a library change waits while the editor's run writes libraries (an update
                # of the document that lands meanwhile is part of it): the page reloads once,
                # at the run's end. A page that follows an editor document during the chain in
                # its helper does not wait (docs_held)
                continue
            if getattr(p, "lib_wait", ""):
                if editor_genie_update_pending(p.doc) or editor_library_check_pending(p.doc):
                    continue
                # the editor's update has landed: the reload for the library change takes
                # it too (not a separate automatic change for the note)
                why, p.lib_wait = p.lib_wait, ""
                if p.doc is not None and _auto_only(p):
                    p.doc_key = _doc_key(p.doc)
                    p.doc_steps = _doc_steps(p.doc)
                self._schedule_reload(p, why, library=True)
            if p.doc_changed() and not options.get("reload_auto_updates") and _auto_only(p):
                # the editor updated the page by itself (its genie update came after the
                # simulation started, the genies' libraries had changed): the simulation goes
                # on with the page the user saw, and the note offers Reload Page (a reload
                # here would throw away the values that had just come)
                p.doc_key = _doc_key(p.doc)
                p.doc_steps = _doc_steps(p.doc)
                p.auto_behind = True
                if p is self.current():
                    self._show_edit_note()
            elif p.doc_changed():
                key = _doc_key(p.doc)
                if key != p.reload_key:
                    # (each new edit puts the reload off: it comes after the last one)
                    p.reload_key = key
                    p.reload_due = now + page_delay_s()
                    p.reload_why = p.pending_why or "it changed in the editor"
                    p.reload_lib = False
                    p.pending_why = ""
                    if p is self.current():
                        self._show_edit_note()
            if p.reload_due is not None and now >= p.reload_due:
                self._auto_reload(p)

    def _auto_reload(self, sp: "SimulatedPage") -> None:
        why = sp.reload_why or "it changed"
        sp.reload_due = None
        sp.reload_lib = False
        try:
            sp.reload_page()
        except Exception as exc:  # noqa: BLE001 -- say why, keep the simulation
            from ...infra import logsetup

            logsetup.note_exception("sim.window.auto_reload")
            text = "The page %s could not be reloaded: %s" % (sp.title(), exc)
            kind = "error"
        else:
            text = "Page %s reloaded: %s." % (sp.title(), why)
            kind = "log"
        add = getattr(sp.sim, "_add_log", None)
        if add is not None:
            add(
                S.LogEntry(
                    getattr(sp.sim, "_now", lambda: 0.0)(),
                    kind,
                    text,
                    level="error" if kind == "error" else "",
                    category="reload",
                )
            )
        self._show_edit_note()

    def moveEvent(self, ev):  # noqa: N802 -- Qt naming
        super().moveEvent(ev)
        # (the popups stay over the page they are measured from)
        self._layout_views()

    def resizeEvent(self, ev):  # noqa: N802 -- Qt naming
        super().resizeEvent(ev)
        self._layout_views()
        self._lay_out_toolbars()

    def _lay_out_toolbars(self) -> None:
        """The page bar beside the clock bar when both fit, on a row of its own when not
        (a docked window, a laptop)."""
        clock = getattr(self, "clock", None)
        bar = getattr(clock, "page_bar", None)
        if bar is None:
            return
        need = clock.sizeHint().width() + bar.sizeHint().width() + 24
        wrap = need > self.width()
        try:
            if wrap and not self.toolBarBreak(bar):
                self.insertToolBarBreak(bar)
            elif not wrap and self.toolBarBreak(bar):
                self.removeToolBarBreak(bar)
        except RuntimeError:  # swallow-ok: a toolbar being deleted with the window
            pass

    def _show_empty_note(self) -> None:
        """The note in the middle of the tab area while no page is simulated."""
        lab = getattr(self, "empty_note", None)
        if lab is None:
            return
        on = self.tabs.count() == 0
        if on:
            w = max(1, min(self.tabs.width() - 32, theme.px(420)))
            lab.setFixedWidth(w)
            lab.adjustSize()
            lab.move((self.tabs.width() - lab.width()) // 2, max(0, (self.tabs.height() - lab.height()) // 3))
            lab.raise_()
        if on != lab.isVisibleTo(self.tabs):
            lab.setVisible(on)

    def changeEvent(self, ev):  # noqa: N802 -- Qt naming
        super().changeEvent(ev)
        if ev.type() == QtCore.QEvent.ActivationChange and self.isActiveWindow():
            self._show_edit_note()

    def _tab_changed(self, _k: int) -> None:
        self._show_empty_note()
        self._show_edit_note()
        cur = self.current()
        self._set_panel_sources()
        self.outputs.clear()
        if cur is not None:
            # the tab's log and the logs of the windows shown in it or opened from it
            fam = self.family(cur)
            if len(fam) == 1:
                self.outputs.add_entries(cur.log)
            else:
                self.outputs.add_entries(
                    sorted((e for v in fam for e in v.log), key=lambda e: getattr(e, "time", 0.0))
                )
            self.setWindowTitle("Simulate %s - ctgedit" % cur.title())
        else:
            self.setWindowTitle("Simulate - ctgedit")
        self._show_status()
        self._show_clock()
        self._show_popups()
        self._sync_record()

    def _sync_record(self) -> None:
        """Scenario > Record shows the current tab's recording (each tab records on its own),
        and every tab that records says so in its name."""
        t = self._sim_tools(say=False)
        on = bool(t is not None and t.recording)
        a = self.clock.act_record
        if a.isChecked() != on:
            a.blockSignals(True)
            a.setChecked(on)
            a.blockSignals(False)
        for sp in self.pages():
            k = self.tabs.indexOf(sp)
            if k >= 0:
                self.tabs.setTabText(k, self._tab_text(sp))

    def _tab_text(self, sp) -> str:
        from .sim_tools import tools_of

        try:
            t = tools_of(sp.sim)
        except Exception:  # noqa: BLE001  # swallow-ok: a tab without tools does not record
            t = None
        return sp.title() + (" (recording)" if t is not None and t.recording else "")

    # -- the status line -----------------------------------------------------------------
    def _status_of(self, cur) -> Optional[S.SimStatus]:
        if cur is None:
            return None
        fn = getattr(cur.sim, "status", None)
        if fn is not None:
            try:
                return fn()
            except Exception:  # noqa: BLE001 -- a source problem must not break the view
                return S.SimStatus("The simulation does not answer.", level="danger")
        if getattr(cur, "demo", False):
            return S.SimStatus(DEMO_NOTE, demo=True, level="warn")
        return None

    def _show_status(self) -> None:
        cur = self.current()
        st = self._status_of(cur)
        key = None if st is None else (st.text, st.level, st.can_retry, st.engine_missing, st.busy, st.detail)
        if key == getattr(self, "_status_key", ()):
            return
        self._status_key = key
        # while the simulator starts the page says so over itself: the saved page with a
        # 'stopped' clock would look like a simulation that failed
        starting = bool(
            st is not None
            and st.busy
            and cur is not None
            and not getattr(cur.sim, "page_active", lambda: True)()
        )
        if starting:
            cur.show_reloading(True, "Starting the Cicode simulator…")
        elif (
            cur is not None
            and getattr(cur, "_reload_overlay", None) is not None
            and cur._reload_overlay.text().startswith("Starting the Cicode simulator")
        ):
            cur.show_reloading(False)
        if st is None or not st.text:
            self.status_row.setVisible(False)
            self.sim_status.setText("")
            return
        # all is well (connecting, or the page runs on the simulator): the status bar says so
        # and the page keeps the room; the line over the page is for what needs the user
        # (demo values, a stopped or missing simulator, a page the simulator refused)
        self.sim_status.setText(st.text)
        self.sim_status.setToolTip(st.detail or "")
        engine = hasattr(cur.sim, "reconnect")
        problem = (
            st.level in ("warn", "danger") or st.engine_missing or (engine and st.can_retry and not st.busy)
        )
        if not problem:
            self.status_row.setVisible(False)
            return
        self.note.setText(st.text)
        self.note.setToolTip(st.detail or "")
        # (the line over the page says it in full: the status bar keeps its first words, the
        # whole text in its tool tip, not the same sentence twice)
        head = st.text.split(":", 1)[0] if ":" in st.text[:40] else ""
        self.sim_status.setText(head)
        self.sim_status.setToolTip(st.text + ("\n" + st.detail if st.detail else ""))
        role = st.level if st.level in ("warn", "danger") else "text_dim"
        theme.set_note_role(self.note, role)
        set_banner_role(self._status_banner, role)
        self.reconnect_button.setVisible(engine and st.can_retry and not st.busy)
        self.engine_button.setVisible(engine and (st.engine_missing or bool(engine_setting())))
        self.status_row.setVisible(True)
        # no simulator and no demo values: a panel over the page says so (the page shows as
        # it is saved), with the way to choose one
        show = engine and st.engine_missing and not getattr(cur.sim, "demo_values", True)
        cur.show_missing_panel(show, st.text, self.choose_engine, self.reconnect_current)
        if show:
            self.status_row.setVisible(False)  # (the panel says it, once)

    def reconnect_current(self) -> None:
        """Reconnect the current tab's simulation."""
        cur = self.current()
        fn = getattr(cur.sim, "reconnect", None) if cur is not None else None
        if fn is not None:
            fn()
            self._show_status()

    def choose_engine(self) -> None:
        """Choose ctsim.exe (the setting 'sim/ctsim_path'), then reconnect every tab."""
        start = engine_setting() or ""
        path, _f = QtWidgets.QFileDialog.getOpenFileName(
            self, "Choose the Cicode Simulator", start, "ctsim.exe (ctsim.exe);;Programs (*.exe)"
        )
        if not path:
            return
        self.set_engine_path(os.path.normpath(path))

    def set_engine_path(self, path: str) -> None:
        """Store the ctsim.exe to use ('' = find it) and reconnect the tabs."""
        from ...sim import client as C

        try:
            from ..prefs.settings import app_settings

            st = app_settings()
            if path:
                st.setValue(C.SETTING, path)
            else:
                st.remove(C.SETTING)
        except Exception:  # noqa: BLE001
            pass
        # (the tabs only: the windows shown in them or opened from them close with their
        # windows, a tab's Reconnect closes its page first; their own Reconnect would send a
        # closePage for each and attach again to a window that is gone)
        for p in self.tab_pages():
            fn = getattr(p.sim, "reconnect", None)
            if fn is not None:
                fn(engine_path=path or "")
        self._show_status()

    # -- clock + polling ---------------------------------------------------------------
    def _clock(self, mode: str, value=None) -> None:
        cur = self.current()
        if cur is None:
            return
        if mode == "step" and value is None:
            # Simulate Settings > Step runs (one scan: the simulator's own step)
            value = options.step_seconds()
        try:
            cur.sim.clock(mode, value)
        except Exception:  # noqa: BLE001
            pass
        if mode == "step":
            self.tick()
        self._show_clock()

    def _debug_toggled(self, _on=None) -> None:
        errs, nums = self.clock.act_errors.isChecked(), self.clock.act_numbers.isChecked()
        try:
            options.put("show_errors", errs)
            options.put("show_numbers", nums)
        except Exception:  # noqa: BLE001 -- a setting is a convenience
            pass
        for p in self.pages():
            p.canvas.set_show_errors(errs)
            p.canvas.show_numbers = nums

    # -- scenarios and snapshots (sim_tools) ---------------------------------------------------
    def _sim_tools(self, say: bool = True):
        """The tools of the current tab's simulation, None (and a status line) when the Cicode
        simulator does not run it."""
        from .sim_tools import tools_of

        v = self.panel_view()
        t = tools_of(v.sim) if v is not None else None
        if t is None or not t.available():
            if say:
                self.status_message("The Cicode simulator does not run this page.", at_least_ms=5000)
            return None
        return t

    @staticmethod
    def _scenario_dir() -> str:
        from ...infra import paths

        d = paths.data_dir("scenarios")
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:  # swallow-ok: the file dialog then opens where it can
            pass
        return d

    def _record_toggled(self, on: bool) -> None:
        t = self._sim_tools()
        if t is None:
            self.clock.act_record.blockSignals(True)
            self.clock.act_record.setChecked(False)
            self.clock.act_record.blockSignals(False)
            return
        if on:
            t.start_recording()
            self._sync_record()
            self.status_message(
                "Recording. Choose Scenario > Record again to stop and save.", at_least_ms=6000
            )
            return
        sc = t.stop_recording()
        self._sync_record()
        if not sc.get("steps"):
            self.status_message("Nothing was recorded.", at_least_ms=5000)
            return
        # (the recording is gone once stopped: a cancelled or failed save asks again, and
        # only Discard drops it)
        while self.save_scenario(sc) is None:
            n = len(sc.get("steps") or ())
            box = QtWidgets.QMessageBox(
                QtWidgets.QMessageBox.Warning,
                "Save Recording",
                "The recording (%d step%s) is not saved. It is lost if you discard it."
                % (n, "" if n == 1 else "s"),
                QtWidgets.QMessageBox.NoButton,
                self,
            )
            again = box.addButton("Save…", QtWidgets.QMessageBox.AcceptRole)
            box.addButton("Discard", QtWidgets.QMessageBox.DestructiveRole)
            box.setDefaultButton(again)
            box.exec()
            if box.clickedButton() is not again:
                self.status_message("The recording was discarded.", at_least_ms=5000)
                return

    def save_scenario(self, sc: dict, path: Optional[str] = None) -> Optional[str]:
        """Save a scenario as a JSON file (the recording); the path, None when cancelled."""
        import json

        if path is None:
            cur = self.current()
            name = ((cur.title() if cur is not None else "") or "page") + " recording.json"
            path, _f = QtWidgets.QFileDialog.getSaveFileName(
                self, "Save Recording", os.path.join(self._scenario_dir(), name), "Scenarios (*.json)"
            )
            if not path:
                return None
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(sc, fh, indent=1)
        except OSError as e:
            QtWidgets.QMessageBox.warning(self, "Save Recording", "The recording was not saved: %s" % e)
            return None
        self.status_message("Saved %d steps to %s." % (len(sc.get("steps") or ()), path), at_least_ms=6000)
        return path

    def play_scenario(self, path: Optional[str] = None) -> bool:
        import json

        t = self._sim_tools()
        if t is None:
            return False
        if path is None:
            path, _f = QtWidgets.QFileDialog.getOpenFileName(
                self, "Play Scenario", self._scenario_dir(), "Scenarios (*.json)"
            )
            if not path:
                return False
        try:
            with open(path, encoding="utf-8") as fh:
                sc = json.load(fh)
        except (OSError, ValueError) as e:
            QtWidgets.QMessageBox.warning(self, "Play Scenario", "The scenario was not read: %s" % e)
            return False
        if not isinstance(sc, dict) or not str(sc.get("format", "")).startswith("ctsim-scenario"):
            QtWidgets.QMessageBox.warning(
                self, "Play Scenario", "This is not a scenario file (ctsim-scenario/0)."
            )
            return False
        sc.setdefault("description", os.path.basename(path))
        return t.play(sc)

    def stop_scenario(self) -> None:
        t = self._sim_tools()
        if t is not None:
            t.stop_scenario()
            self.status_message("The scenario was stopped.", at_least_ms=4000)

    def save_snapshot(self, path: Optional[str] = None) -> bool:
        t = self._sim_tools()
        if t is None:
            return False
        if path is None:
            cur = self.current()
            name = ((cur.title() if cur is not None else "") or "page") + " snapshot.json"
            path, _f = QtWidgets.QFileDialog.getSaveFileName(
                self, "Save Snapshot", os.path.join(self._scenario_dir(), name), "Snapshots (*.json)"
            )
            if not path:
                return False
        if not getattr(self, "_snapshot_wired", False):
            self._snapshot_wired = True
            self._snapshot_saved.connect(self._snapshot_done, QtCore.Qt.QueuedConnection)
        return t.save_snapshot(path, lambda err, p=path: self._snapshot_saved.emit(p, err or ""))

    def _snapshot_done(self, path: str, error: str) -> None:
        """Save Snapshot's end, on the window's thread: a failed write is said, with Save
        Snapshot again to another place."""
        if not error:
            self.status_message("Saved %s." % path, at_least_ms=5000)
            return
        box = QtWidgets.QMessageBox(
            QtWidgets.QMessageBox.Warning,
            "Save Snapshot",
            "The snapshot was not saved to %s: %s" % (path, error),
            QtWidgets.QMessageBox.Close,
            self,
        )
        again = box.addButton("Save Elsewhere…", QtWidgets.QMessageBox.AcceptRole)
        box.exec()
        if box.clickedButton() is again:
            self.save_snapshot()

    def load_snapshot(self, path: Optional[str] = None) -> bool:
        t = self._sim_tools()
        if t is None:
            return False
        if path is None:
            path, _f = QtWidgets.QFileDialog.getOpenFileName(
                self, "Load Snapshot", self._scenario_dir(), "Snapshots (*.json)"
            )
            if not path:
                return False
        why = t.load_snapshot(path)
        if why:
            QtWidgets.QMessageBox.warning(self, "Load Snapshot", "The snapshot was not loaded: %s" % why)
            return False
        self.status_message("Loaded %s." % path, at_least_ms=5000)
        return True

    def _page_keys_toggled(self, on: bool) -> None:
        """Page Keys: the keys typed on a page go to the simulation (on) or stay ctgedit's."""
        from .canvas import SimCanvas

        SimCanvas.keys_to_page = bool(on)
        try:
            options.put("keys_to_page", bool(on))
        except Exception:  # noqa: BLE001 -- a setting is a convenience
            from ...infra import logsetup

            logsetup.note_exception("sim.window._page_keys_toggled")
        self.status_message(
            "Keys go to the page's keyboard commands."
            if on
            else "Keys stay with ctgedit: the page gets no keys (the mouse still works).",
            at_least_ms=4000,
        )

    def open_inspector(self, an: Optional[int] = None):
        """The Inspector window (one, made on first use): the current tab's page, object
        ``an`` selected when given."""
        if self._inspector is None:
            from .inspector import PageInspector

            self._inspector = PageInspector(self, self)
        self._inspector.inspect(an if an and an > 0 else None)
        return self._inspector

    def show_log_object(self, e) -> bool:
        """Show on Page: the tab of the line's window / page, the object centred and marked."""
        an = getattr(e, "an", -1) or -1
        if an <= 0:
            return False
        tabs = self.pages()
        cur = self.current()
        order = ([cur] if cur is not None else []) + [p for p in tabs if p is not cur]
        for p in order:
            if p.owns(e) and p.canvas.show_object(an):
                self.tabs.setCurrentWidget(p)
                return True
        self.status_message("Object AN %d is not on an open page." % an, at_least_ms=5000)
        return False

    def _set_user(self, choice) -> None:
        """The User choice: log the current tab's simulation in, and remember it."""
        if not choice:
            return
        from ..prefs.settings import app_settings

        try:
            app_settings().setValue("sim/user", str(choice))
        except Exception:  # noqa: BLE001 -- a setting is a convenience
            pass
        cur = self.current()
        fn = getattr(cur.sim, "set_user", None) if cur is not None else None
        if fn is not None:
            fn(str(choice))

    def _show_user(self) -> None:
        cur = self.current()
        fn = getattr(cur.sim, "user_state", None) if cur is not None else None
        self.clock.user.setEnabled(fn is not None)
        if fn is None:
            return
        _serial, user, users = fn()
        if not user:
            user = {
                "mode": getattr(cur.sim, "want_user", "full").partition(":")[0],
                "name": getattr(cur.sim, "want_user", "").partition(":")[2],
            }
        self.clock.set_users(users, user)

    def _show_clock(self) -> None:
        cur = self.current()
        cs = None
        if cur is not None:
            try:
                cs = cur.sim.clock_state()
            except Exception:  # noqa: BLE001
                cs = None
        self.clock.show_state(cs)

    def tick(self) -> None:
        """Poll every tab's simulation; the panels follow the current tab."""
        cur = self.current()
        try:
            # before the tabs follow the engine's windows: a window that asked for its
            # drawing gets it now, and no tab sends it a second time
            self._serve_viewers()
        except Exception:  # noqa: BLE001 -- must not stop the polling
            from ...infra import logsetup

            logsetup.note_exception("sim.window.serve_viewers")
        pending = self.__dict__.setdefault("_pending_outputs", [])
        for p in self.pages():
            new = p.tick()
            if new and cur is not None and (p is cur or self.root_of(p) is cur):
                pending.extend(new)
        # the panels follow at 5 Hz, not every tick: each refresh re-sorts the log and walks
        # every line and tag
        now = time.monotonic()
        panels = now - getattr(self, "_panels_at", 0.0) >= PANEL_PERIOD_S
        if panels and pending:
            self.outputs.add_entries(pending)
            self._pending_outputs = []
        try:
            self._follow_windows()
        except Exception:  # noqa: BLE001 -- a window list problem must not stop the polling
            from ...infra import logsetup

            logsetup.note_exception("sim.window.follow_windows")
        try:
            self._live_tick()
        except Exception:  # noqa: BLE001 -- a reload problem must not stop the polling
            from ...infra import logsetup

            logsetup.note_exception("sim.window.live_tick")
        self._show_clock()  # (cheap: every tick)
        self._show_status()  # (cheap: nothing done unless it changed)
        if not panels:
            return
        self._panels_at = now
        self.inputs.refresh()
        self._refresh_alarm_panels()
        self._show_user()

    # -- the viewer's part: drawings Cicode-opened windows ask for, dialogs, alarms -------
    def _services(self):
        """(services, a tab of that simulation) per simulation shown."""
        out = {}
        for p in self.pages():
            fn = getattr(p.sim, "viewer", None)
            svc = fn() if fn is not None else None
            if svc is not None and id(svc) not in out:
                out[id(svc)] = (svc, p)
        return list(out.values())

    def _page_of_window(self, svc, window) -> Optional["SimulatedPage"]:
        """The tab showing engine ``window`` of ``svc``'s simulation (else one of its tabs,
        else the current tab)."""
        any_tab = None
        for p in self.pages():
            fn = getattr(p.sim, "viewer", None)
            if fn is None or fn() is not svc:
                continue
            if window is not None and p.engine_window() == window:
                return p
            any_tab = any_tab or p
        return any_tab or self.current()

    def _serve_viewers(self) -> None:
        # the drawings asked for whose model was still being made (a helper process): again
        later, self._wanted_later = getattr(self, "_wanted_later", []), []
        for svc, want, p in later:
            if not svc.closed:
                self._send_wanted_model(svc, want, p)
        for svc, p in self._services():
            for want in svc.take_models():
                self._send_wanted_model(svc, want, p)
            for kind, body in svc.take_dialogs():
                self.dialogs.handle(svc, kind, body)
            for want in svc.take_images():
                self._send_window_image(svc, want)
            for data in svc.take_clipboard():
                self._copy_window_image(data)
        self.dialogs.forget_closed()

    def _send_window_image(self, svc, want: dict) -> None:
        """ctsim/imageWanted (WinFile, WinPrint, WinCopy): the picture of the engine window,
        drawn as the tab or window showing it draws its page, at the scale asked
        (snapshot.tab_image); ``none`` when no tab shows that window."""
        from .snapshot import bmp_bytes, tab_image

        data = None
        try:
            window = int(want.get("window"))
            sx = float(want.get("xScale") or 1.0)
            sy = float(want.get("yScale") or 1.0)
        except TypeError, ValueError:
            window, sx, sy = None, 0.0, 0.0
        view = None
        if window is not None:
            for p in self.pages():
                fn = getattr(p.sim, "viewer", None)
                if fn is not None and fn() is svc and p.engine_window() == window:
                    view = p
                    break
        if view is not None and sx > 0 and sy > 0:
            try:
                img = tab_image(self, view, sx, sy)
                data = bmp_bytes(img) if img is not None else None
            except Exception:  # noqa: BLE001 -- the simulator gets 'none' and goes on
                logsetup.note_exception("sim.window.send_window_image")
                data = None
        svc.send_image(want.get("id"), data, clipboard=bool(want.get("clipboard")))

    def _copy_window_image(self, data: bytes) -> None:
        """WinCopy's picture, recoloured by the simulator, to the clipboard."""
        img = QtGui.QImage.fromData(data, "BMP")
        if img.isNull():
            return
        QtGui.QGuiApplication.clipboard().setImage(img)
        self.status_message(
            "Cicode copied a picture of the window (%d x %d)." % (img.width(), img.height()),
            at_least_ms=4000,
        )

    def _send_wanted_model(self, svc, want: dict, owner: "SimulatedPage") -> None:
        """ctsim/modelWanted: the drawing of the page Cicode opened in a window (the editor's
        document when a tab simulates that page from one, unsaved edits included)."""
        from .viewer import find_page_file

        page = str(want.get("page") or "")
        path = find_page_file(page, str(want.get("path") or ""), svc.search_dirs())
        model = None
        if path and str(want.get("window")) == "0" and not getattr(svc, "first_drawing_noted", False):
            # the first drawing a simulator asks for in window 0: the startup page (made early
            # next time: prefetch_startup_drawing)
            svc.first_drawing_noted = True
            if owner.page_path:
                note_startup_drawing(owner.page_path, path)
        if path:
            key = os.path.normcase(os.path.abspath(path))
            doc = next(
                (
                    t.doc
                    for t in self.pages()
                    if t.doc is not None
                    and t.page_path
                    and os.path.normcase(os.path.abspath(t.page_path)) == key
                ),
                None,
            )
            if doc is not None:
                # the document's model, made in a helper process and kept (page_model_job):
                # never made again here on the GUI thread (a big page would outlast the
                # simulator's wait for it, and the page would run without its drawing)
                got = page_model_job(path, doc)
            else:
                # (a file's: made in a helper process too, and kept for its version)
                got = page_model_file_job(path)
            if hasattr(got, "result"):
                if not got.done():
                    self._wanted_later = getattr(self, "_wanted_later", [])
                    self._wanted_later.append((svc, want, owner))
                    return
                got = got.result(0)
            model = got
        add_log = getattr(owner.sim, "_add_log", None)
        now = getattr(owner.sim, "_now", lambda: 0.0)
        if model is None:
            if add_log is not None:
                add_log(
                    S.LogEntry(
                        now(),
                        "log",
                        "The drawing of page %s (window %s) could "
                        "not be read: it runs without it." % (page or "?", want.get("window")),
                    )
                )
            # (the simulator starts it at once, not after its wait for a drawing)
            svc.send_no_model(int(want["window"]), page)
            return

        def done(body, err):
            from ...sim import client as C

            if err is not None and add_log is not None and not isinstance(err, C.ConnectionLost):
                add_log(
                    S.LogEntry(
                        now(), "error", "The drawing of page %s did not go to the simulator: %s" % (page, err)
                    )
                )

        svc.send_model(int(want["window"]), page, model, done)

    #: the Alarms / Equipment tabs follow their lists at most this often (their answers come
    #: at most 4 a second; a refresh copies up to 2000 rows)
    ALARM_PANEL_EVERY = 0.25

    def _refresh_alarm_panels(self, force: bool = False) -> None:
        if not force and not self.dock_inputs.isVisible():
            return
        now = time.monotonic()
        if not force and now - getattr(self, "_alarm_panels_at", 0.0) < self.ALARM_PANEL_EVERY:
            return
        self._alarm_panels_at = now
        w = self.input_tabs.currentWidget()
        if w is self.alarms or w is self.equipment:
            w.refresh(force=force)

    def confirm_close(self) -> bool:
        """Closing stops every simulation in the window: with the Simulate setting on, a
        question first when a scenario is being recorded or tags were set by hand (the
        dock's X sits next to its float button)."""
        if not options.get("confirm_close"):
            return True
        recording = bool(self.clock.act_record.isChecked())
        writes = sum(int(getattr(p.sim, "user_writes", 0) or 0) for p in self.pages())
        if not recording and not writes:
            return True
        what = (
            "the scenario being recorded"
            if recording and not writes
            else "the tag values you set"
            if writes and not recording
            else "the scenario being recorded and the tag values you set"
        )
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("Stop the Simulation?")
        box.setIcon(QtWidgets.QMessageBox.Question)
        box.setText("Closing stops every simulation in this window; %s go with it. Close?" % what)
        box.setStandardButtons(QtWidgets.QMessageBox.Close | QtWidgets.QMessageBox.Cancel)
        box.setDefaultButton(QtWidgets.QMessageBox.Cancel)
        return box.exec() == QtWidgets.QMessageBox.Close

    def closeEvent(self, ev):  # noqa: N802 -- Qt naming
        if not self.confirm_close():
            ev.ignore()
            return
        self._save_layout()
        self.shut_down()
        # the window stays in _WINDOWS, hidden: Tools > Simulate Page shows it again (removing
        # the last Python reference here would delete the window while Qt is closing it, and
        # its signal lambdas keep it alive anyway)
        super().closeEvent(ev)
        if self._dock is not None and _alive(self._dock):
            self._dock.hide()  # (docked: its dock goes with it)

    def shut_down(self) -> None:
        """What closing the window does: every simulation in it stops, its tabs and the
        windows its pages opened close."""
        self._timer.stop()
        self.dialogs.cancel_all()
        for p in self.pages():
            p.stop()
        for v in list(self._views):
            if v.frame is not None:
                v.frame.hide()
                v.frame.deleteLater()
                v.frame = None
            else:
                v.hide()
                v.deleteLater()
        self._views = []
        self._panel_view = None
        self._closed_here = {}
        self.live.close()
        while self.tabs.count():
            w = self.tabs.widget(0)
            self.tabs.removeTab(0)
            w.deleteLater()
        self._tab_changed(-1)


def _alive(obj) -> bool:
    """``obj`` is a Qt object whose C++ side still exists."""
    try:
        import shiboken6

        return obj is not None and shiboken6.isValid(obj)
    except Exception:  # noqa: BLE001
        return obj is not None


#: the Simulate window (a Python reference keeps the parentless window alive; it is hidden,
#: not deleted, when closed)
_WINDOWS: List[SimulateWindow] = []


def simulate_window(create: bool = True) -> Optional[SimulateWindow]:
    """The Simulate window (one for the application, hidden while closed), made when
    ``create``."""
    for w in _WINDOWS:
        try:
            w.isVisible()
            return w
        except RuntimeError:
            continue
    if not create:
        return None
    w = SimulateWindow()
    _WINDOWS.append(w)
    return w


def user_setting() -> str:
    """The Simulate window's User choice ('full', 'none' or 'user:<name>'; default 'full':
    every privilege, so every button of the page works)."""
    from ..prefs.settings import app_settings

    try:
        v = str(app_settings().value("sim/user", "full") or "full")
    except Exception:  # noqa: BLE001
        v = "full"
    return v if v in ("full", "none") or v.startswith("user:") else "full"


def open_simulation(
    page_path: str, page=None, sim=None, show: bool = True, parsed=None, doc=None, host=None
) -> SimulatedPage:
    """Simulate a page in the Simulate window (Tools > Simulate Page); ``doc`` = the editor's
    document (the engine simulates it as it is, unsaved edits included); ``host`` = the editor
    window that asks (the window docks there when it was docked last, setting 'sim/docked')."""
    if sim is None and options.get("run_startup"):
        # the startup page's drawing, made while the window and the simulator start (the
        # simulator asks for it once its startup runs)
        prefetch_startup_drawing(page_path)
    w = simulate_window()
    sp = w.open_page(page_path, page=page, sim=sim, parsed=parsed, doc=doc)
    if show:
        w.present(host)
    elif host is not None:
        w._opener = host
    return sp


# ---------------------------------------------------------------- the startup page's drawing
# The simulator shows the project's startup page in window 0 before the page simulated (its
# startup code runs once that page shows) and asks for its drawing then: a helper process
# makes it, all of it on the way to the page going live. The
# page the simulator asked for first last time is remembered per project, and its drawing
# made as the Simulate window opens (a wrong guess costs one helper process).


def _startup_drawings_file() -> str:
    return data_dir("simulate", "startup_drawings.json")


def _startup_drawings() -> dict:
    try:
        with open(_startup_drawings_file(), encoding="utf-8") as fh:
            got = json.load(fh)
        return got if isinstance(got, dict) else {}
    except OSError, ValueError:  # swallow-ok: none remembered yet (or unreadable: start again)
        return {}


def _project_key(page_path: str) -> str:
    return os.path.normcase(os.path.dirname(os.path.abspath(page_path)))


def note_startup_drawing(page_path: str, drawing_path: str) -> None:
    """The simulator of ``page_path``'s project asked for ``drawing_path`` first (window 0)."""
    key = _project_key(page_path)
    known = _startup_drawings()
    if known.get(key) == drawing_path:
        return
    known[key] = drawing_path
    try:
        os.makedirs(os.path.dirname(_startup_drawings_file()), exist_ok=True)
        tmp = _startup_drawings_file() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(known, fh)
        os.replace(tmp, _startup_drawings_file())
    except OSError:
        logsetup.note_exception("simulate.window.note_startup_drawing")


def prefetch_startup_drawing(page_path: str) -> None:
    """Start the drawing of the page the simulator of ``page_path``'s project showed first last
    time (page_model_file_job keeps it for the simulator's ask)."""
    if not page_path:
        return
    path = _startup_drawings().get(_project_key(page_path))
    if not isinstance(path, str) or not path or not os.path.isfile(path):
        return
    if os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(page_path)):
        return  # (the page itself: its own model goes with the open)
    page_model_file_job(path)


# the process caches of this module (infra/caches.py: when each is emptied)
from ...infra import caches as _caches  # noqa: E402

_caches.register("simulate.models", _MODEL_CACHE, on=("refresh", "tests"))
