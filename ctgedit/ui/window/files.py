"""Open, new, save, delete and rename pages and library elements."""

from __future__ import annotations

from typing import Optional

from ..main_window_base import (  # noqa: F401
    EditableDocument,
    ElementDocument,
    Page,
    QtCore,
    QtGui,
    QtWidgets,
    app_settings,
    contextlib,
    editing_mod,
    os,
    ui_state,
)
from ..main_window_base import (  # noqa: F401
    _PendingPage,
)

#: a new template's page with no page open and no named size in the Page Defaults (never
#: set, 'Default' or 'User')
NEW_TEMPLATE_SIZE = (1920, 1080)

#: CtDraw's cache notice (PageFiles._library_cache_notice), the editor's, for every window:
#: the runs of several steps going on now ('holds'), the notice waiting for their end, and
#: whether its box was shown this session
CACHE_NOTICE_RUN = {"holds": 0, "waiting": "", "shown": False}


def new_template_size(lib: str) -> tuple:
    """The page size of a new template made with no page open."""
    from ...format.template_place import RESOLUTIONS
    from ..dialog.dialogs import page_defaults

    d = page_defaults(os.path.dirname(os.path.abspath(lib)), resolve_default=False)
    if d.get("source") == "settings" and not app_settings().contains("pagedefaults/resolution"):
        return NEW_TEMPLATE_SIZE
    res = str(d.get("resolution") or "").lower()
    for name, w, h in RESOLUTIONS:
        if name.lower() == res:
            return (w, h)
    return NEW_TEMPLATE_SIZE


#: the key of an element tab's nested genies in its genie_sources_done (_update_stale_nested)
NESTED_DONE = ("", "<nested>")


def redo_wait_s() -> int:
    """How long an automatic update of an open tab waits for the user's steps to redo, which
    it would clear (s, counted from its first wait; 0: it does not wait): Settings > Pages and
    libraries > When a page opens, read at each look."""
    from ..prefs.settings import WHOLE_SPANS, whole

    return whole("opt/auto_update_redo_wait_s", WHOLE_SPANS["opt/auto_update_redo_wait_s"])


class PageFiles:
    """MainWindow's open / new / save of pages and library elements, deleting and renaming them,
    and passing a saved element on to the pages and genies that use it."""

    # ------------------------------------------------ diagnostics
    def _open_log_folder(self) -> None:
        from ... import crashlog

        d = crashlog.log_dir()
        with contextlib.suppress(OSError):
            os.makedirs(d, exist_ok=True)
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(d))

    def _copy_diagnostics(self) -> str:
        from ...infra import logsetup

        docs = [e["doc"] for w in _mw().MainWindow._instances for e in getattr(w, "_docs", [])]
        text = logsetup.diagnostics_text(docs)
        QtWidgets.QApplication.clipboard().setText(text)
        self.statusBar().showMessage(
            "Diagnostics copied to the clipboard. Paste them into your problem report.", 6000
        )
        return text

    def bring_to_front(self) -> None:
        """Show this window above the others (a second launch handed it a
        page, so the user expects to see it)."""
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def set_page(self, page: Page) -> None:
        """Render a standalone page (demo mode, no document editing)."""
        self._page = page
        self.scene.set_page(page, grid_size=self._grid_spec() if self._show_grid else 0)
        self.scene.set_element_mode(False)
        self.objects_dock.set_page(page, [])
        self._update_title()

    def quick_open(self, prefill: str = "") -> None:
        """File > Quick Open (Ctrl+Shift+O): a search over the pages, symbols, genies, tags
        and Cicode functions of the page's project; Enter opens, arms or shows the row.
        Without a page the project of the most recent page is searched."""
        if not isinstance(prefill, str):
            prefill = ""  # (the menu's and the key's QAction passes its 'checked')
        pdir = ""
        doc = getattr(self, "doc", None)
        if doc is not None and getattr(doc, "path", ""):
            pdir = os.path.dirname(os.path.abspath(doc.path))
        else:
            recent = self.recent_pages()
            if recent:
                pdir = os.path.dirname(recent[0])
        if getattr(self, "quick_open_popup", None) is None:
            from ..dialog.quick_open import QuickOpen

            self.quick_open_popup = QuickOpen(self)
        self.quick_open_popup.open_at(pdir, prefill=prefill)

    def open_dialog(self) -> None:
        """File > Open: the Citect project browser (projects of the active
        install, pages / templates / symbols / genies with thumbnails,
        search and a live preview) instead of the Windows file dialog."""
        from ..browser import PageBrowserDialog

        start = self.doc.path if self.doc is not None else None
        element = self.doc.element_name if isinstance(self.doc, ElementDocument) else None
        r = PageBrowserDialog.get_open_target(
            self, start, before_delete=self._before_delete_or_rename, start_element=element
        )
        if r is None:
            return
        if r.kind == "page":
            self._remember_dir("open", r.path)
            self.open_page(r.path)
            return
        # a template / symbol / genie opens for EDITING as its own element
        if r.element:
            self._open_element(r.path, r.element)

    #: ms a step on the GUI thread runs before its busy note shows over the canvas
    #: (Settings > Pages and libraries > Long jobs, ui/prefs/job_options)
    BUSY_NOTE_MS = 400

    @contextlib.contextmanager
    def _busy(self, message: str = ""):
        """Show a wait cursor while a slow step runs on the GUI thread."""
        if message:
            self.statusBar().showMessage(message)
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.ExcludeUserInputEvents)
        watch = None
        if message:
            from ..toast import BusyWatch

            watch = BusyWatch(self, message, self.BUSY_NOTE_MS)
        try:
            yield
        finally:
            if watch is not None:
                self._busy_note_shown = watch.finish()
            QtWidgets.QApplication.restoreOverrideCursor()
            if message and self.statusBar().currentMessage() == message:
                self.statusBar().clearMessage()

    @staticmethod
    def _error_text(e: BaseException) -> str:
        """What a failure says to the user: its own message (the Python type goes to the
        log, and to the error box's details), else that something went wrong."""
        from ...infra import logsetup

        logsetup.note_exception("ui.error_text", e)
        text = str(e).strip()
        if isinstance(e, KeyError) and len(e.args) == 1:
            text = "%s is missing." % e.args[0]
        return text or "Something went wrong (%s)." % type(e).__name__

    def _open_element(self, lib_path: str, element: str, kind: str = "") -> None:
        """Open a library element (symbol / genie / super genie / template)
        for editing in its own tab, like CtDraw's element window; Save writes
        it back into its library."""
        from ...format.composites import in_composition_cache

        if in_composition_cache(lib_path):
            # a composite genie's composition cache: only Plant SCADA's
            # composer writes it (help 1172220)
            QtWidgets.QMessageBox.information(
                self,
                "Open",
                "%s is part of a composition cache. Change the composite "
                "genie's template or its options instead." % lib_path,
            )
            return
        name = "%s.%s" % (os.path.splitext(os.path.basename(lib_path))[0], element)
        # already open: its tab is shown (a restored one not shown yet opens now); in another
        # editor window, that window comes to the front (two tabs of one element: the later
        # save would drop the other's edits)
        wins = [self] + [w for w in list(type(self)._instances) if w is not self]
        for win in wins:
            try:
                k = next(
                    (i for i, e in enumerate(win._docs) if win._is_element_tab(e, lib_path, element)), None
                )
            except RuntimeError:  # swallow-ok: a window being destroyed
                continue
            if k is None:
                continue
            if win is not self:
                if win.isMinimized():
                    win.showNormal()
                win.raise_()
                win.activateWindow()
                win.statusBar().showMessage("%s is open in this window." % name, 6000)
            win.show_tab(k)
            return
        try:
            with self._busy("Opening %s…" % name):
                doc = ElementDocument.open_element(lib_path, element)
                # (its hidden objects: the tab's activation; the tab shown keeps its own)
                self.set_document(doc)
        except Exception as e:  # noqa: BLE001
            from ..messages import error_box

            error_box(
                self,
                "Open Failed",
                "%s could not be opened: %s" % (name, self._error_text(e)),
                exc=e,
                next_steps=("Check that its library opens in the Library panel, then try again.",),
                actions=(("Show in Explorer", lambda p=lib_path: self._reveal_in_explorer(p)),),
                icon=QtWidgets.QMessageBox.Critical,
            )
            return
        self._locked.clear()
        QtCore.QTimer.singleShot(0, self, self._frame_content)
        editing = "Editing %s %s. Save writes it back into %s." % (
            doc.kind,
            doc.display_name,
            os.path.basename(lib_path),
        )
        self.statusBar().showMessage(editing, 6000)
        # before editing: which pages place it (CtDraw's library view 'Used
        # By'); a library supplied with Plant SCADA is included by every
        # project -- its save warns instead
        from ..dialog.library_dialogs import shipped_library

        if not shipped_library(lib_path):
            QtCore.QTimer.singleShot(150, self, lambda d=doc, t=editing: self._usage_notice(d, t))
            # (what uses its library is read now, on a worker thread, so the search after
            # its save finds it read)
            from .library import warm_element_users

            warm_element_users(lib_path)

    def _usage_notice(self, doc, after: str) -> None:
        """Status-bar note of how many pages place the element being edited, in place of the
        note ``after`` (the opening one): never over a newer one, such as what its save said."""
        bar = self.statusBar()
        if self.doc is not doc or bar.currentMessage() != after:
            return
        from .. import lifecycle

        usage = lifecycle.element_usage(doc.path, doc.element_name)
        if not usage or self.doc is not doc or bar.currentMessage() != after:
            return
        names = ", ".join(e.name for e in usage[:4]) + (" …" if len(usage) > 4 else "")
        bar.showMessage(
            "%s is placed directly on %d page%s (%s). Saving it updates the open pages and "
            "asks about the others; Edit > Library > Used By lists every user, also through "
            "other genies." % (doc.display_name, len(usage), "" if len(usage) == 1 else "s", names),
            10000,
        )

    @staticmethod
    def _tab_element(d) -> str:
        """The library element a tab's document is ('' for a page): an opened element's
        name, or a restored element tab's that is not shown yet."""
        if isinstance(d, ElementDocument):
            return d.element_name or ""
        return getattr(d, "element", "") or ""

    def _version_refused(self, doc, errors=None) -> bool:
        """``doc`` is an earlier version shown read-only: it is never written over its page
        or library element (Save goes to Save As; Save All and the close list say so)."""
        from ..dialog.history_versions import VERSION_SAVE_TEXT, is_version

        if not is_version(doc):
            return False
        text = VERSION_SAVE_TEXT % self._doc_name(doc)
        if errors is not None:
            errors.append(text)
        else:
            self.statusBar().showMessage(text, 10000)
        return True

    @staticmethod
    def _doc_name(doc) -> str:
        return doc.display_name if isinstance(doc, ElementDocument) else os.path.basename(doc.path)

    def _is_element_tab(self, e: dict, lib_path: str, element: str) -> bool:
        """Tab entry ``e`` is library element ``lib_path``/``element`` (opened or not); an
        earlier version of it shown read-only is not."""
        from ..dialog.history_versions import is_version

        if is_version(e["doc"]):
            return False
        el = self._tab_element(e["doc"])
        return (
            bool(el)
            and el.lower() == (element or "").lower()
            and os.path.normcase(os.path.abspath(e["doc"].path))
            == os.path.normcase(os.path.abspath(lib_path))
        )

    def _tabs_of(self, kind: str, path: str, element: str = "") -> list:
        """Indices of the open tabs showing page ``path`` (kind 'page') or
        library element ``path``/``element`` (a restored tab not shown yet too)."""
        from ..dialog.history_versions import is_version

        key = os.path.normcase(os.path.abspath(path))
        out = []
        for k, e in enumerate(self._docs):
            d = e["doc"]
            if os.path.normcase(os.path.abspath(d.path)) != key or is_version(d):
                continue
            el = self._tab_element(d)
            if kind == "page" and not el:
                out.append(k)
            elif kind != "page" and el and el.lower() == element.lower():
                out.append(k)
        return out

    def _before_delete_or_rename(self, kind: str, path: str, element: str = ""):
        """A page / element is about to be deleted or renamed: its tabs with unsaved edits
        are asked about first (False cancels). Returns the step that closes its tabs (their
        Save would write it back), for after the delete or rename succeeded: one that fails
        leaves the tabs and their edits as they are."""
        open_in = [(self, self._tabs_of(kind, path, element))]
        for w in type(self)._instances:
            if w is not self:
                with contextlib.suppress(RuntimeError):  # a window being destroyed
                    open_in.append((w, w._tabs_of(kind, path, element)))
        if any(w._docs[k]["doc"].dirty for w, tabs in open_in for k in tabs):
            what = element if kind != "page" else os.path.basename(path)
            r = QtWidgets.QMessageBox.warning(
                self,
                "Unsaved Changes",
                "%s is open here with unsaved changes. Continuing closes it and discards them." % what,
                QtWidgets.QMessageBox.Discard | QtWidgets.QMessageBox.Cancel,
                QtWidgets.QMessageBox.Cancel,
            )
            if r != QtWidgets.QMessageBox.Discard:
                return False

        def close() -> None:
            for w in [self] + [x for x in type(self)._instances if x is not self]:
                with contextlib.suppress(RuntimeError):  # a window being destroyed
                    for k in sorted(w._tabs_of(kind, path, element), reverse=True):
                        w._docs[k]["doc"].dirty = False
                        w._close_doc_tab(k)
                    # the library / page lists refresh once the change is on disk
                    QtCore.QTimer.singleShot(0, w, w._after_lifecycle_change)

        return close

    def _after_lifecycle_change(self) -> None:
        with contextlib.suppress(Exception):
            self.library_dock.refresh()
        for e in self._docs:
            if e["doc"] is not self.doc:
                e["stale"] = True
        self._update_title()

    def _delete_library_element(self, lib_path: str, element: str) -> bool:
        """Library panel > Delete element: warn about the pages using it,
        confirm, delete (marked deleted until Pack Libraries)."""
        from .. import lifecycle

        ok = lifecycle.delete_element(self, lib_path, element, before=self._before_delete_or_rename)
        if ok:
            self.statusBar().showMessage(
                "Deleted %s.%s. Tools > Pack Libraries frees its space."
                % (os.path.splitext(os.path.basename(lib_path))[0], element),
                8000,
            )
        return ok

    def _rename_library_element(self, lib_path: str, element: str):
        """Library panel > Rename element."""
        from .. import lifecycle

        new = lifecycle.rename_element(self, lib_path, element, before=self._before_delete_or_rename)
        if new:
            self.statusBar().showMessage(
                "Renamed %s.%s to %s." % (os.path.splitext(os.path.basename(lib_path))[0], element, new), 8000
            )
        return new

    def _frame_content(self) -> None:
        """Zoom to the element's objects (they keep their element
        coordinates, often far from the canvas origin), at most at Settings > Canvas
        'Largest zoom when an element opens'; not when it opened where it was left."""
        if self.doc is None or not self.doc.page.objects:
            return
        entry = next((e for e in self._docs if e["doc"] is self.doc), None)
        if entry is not None and entry.get("view_restored"):
            return
        from ..panels.doc_tabs import open_zoom_cap

        cap = open_zoom_cap(element=True) or self.view.ZOOM_MAX
        box = self.doc.selection_extent([i for i, o in enumerate(self.doc.page.objects) if o.parent is None])
        if box is None:
            return
        l, t, r, b = box
        # one small margin
        with contextlib.suppress(Exception):
            self.view.frame_rect(QtCore.QRectF(l, t, max(1, r - l), max(1, b - t)), margin=0.04, max_zoom=cap)

    def new_blank_dialog(self, kind: str = "") -> None:
        """File > New...: the page browser's New mode (what to create, its project, library
        and name; browser/new_mode.py). ``kind`` = the tab to start on (default: the last)."""
        from ..browser import PageBrowserDialog

        start = (
            os.path.abspath(self.doc.path)
            if self.doc is not None
            else os.path.join(self._dialog_dir("open"), "NewPage.ctg")
        )
        target = PageBrowserDialog.get_new(self, start_path=start, kind=kind or None)
        if target is None:
            return
        if target.kind == "edit_template":
            self._open_element(target.path, target.element)
        elif target.kind == "page":
            self._create_page(target.template_ref, target.linked, target.path)
        else:
            self._create_library_element(target.kind, target.path, target.element)

    # ------------------------------------------------ a new page from the last template
    def last_template(self):
        """(style, element) of the template File > New used last for a page, or None."""
        raw = str(app_settings().value("pagebrowser/new/template", "") or "")
        parts = raw.split("|")
        if len(parts) != 4 or not parts[0] or not parts[1]:
            return None
        return parts[0], parts[1]

    def new_page_from_last_template(self) -> bool:
        """File > New Page from the Last Template (the '+' button's menu): a new page in this
        project ('NewPage' or Settings > Pages and libraries 'Name new pages:', or the first
        free 'NewPage_2' ...) from the template File > New
        used last, linked as it was then, without the New window."""
        lt = self.last_template()
        if lt is None:
            self.statusBar().showMessage(
                "Create a page with File > New first: its template is used next time.", 6000
            )
            return False
        from ..browser.naming import unique_page_name

        pdir = self._current_project_dir()
        try:
            existing = [os.path.splitext(f)[0] for f in os.listdir(pdir) if f.lower().endswith(".ctg")]
        except OSError:
            existing = []
        # a new page is on disk only after its first Save: the open tabs' names are taken too
        existing += self.open_page_names(pdir)
        from ..prefs.window_options import new_page_name

        name = unique_page_name(new_page_name(), existing)
        linked = bool(app_settings().value("newpage/linked", True, type=bool))
        before = len(self._docs)
        self._create_page("%s.%s.<ctt" % lt, linked, os.path.join(pdir, name + ".ctg"))
        return len(self._docs) > before

    def open_page_names(self, pdir: str) -> list:
        """The names of the pages of folder ``pdir`` open in a tab of any editor window (saved
        or not)."""
        key = os.path.normcase(os.path.abspath(pdir))
        out = []
        for w in [self] + [w for w in list(type(self)._instances) if w is not self]:
            try:
                docs = [e["doc"] for e in w._docs]
            except RuntimeError:  # swallow-ok: a window being destroyed
                continue
            for d in docs:
                p = getattr(d, "path", "") or ""
                if self._tab_element(d) or not p.lower().endswith(".ctg"):
                    continue
                if os.path.normcase(os.path.dirname(os.path.abspath(p))) == key:
                    out.append(os.path.splitext(os.path.basename(p))[0])
        return out

    def _page_open_in_tab(self, path: str) -> bool:
        """Page ``path`` is open in a tab of an editor window (a new page not saved yet too)."""
        name = os.path.splitext(os.path.basename(path))[0].lower()
        return name in {n.lower() for n in self.open_page_names(os.path.dirname(os.path.abspath(path)))}

    # ------------------------------------------------ pages dropped from Explorer
    def _install_page_drop(self) -> None:
        """A page (.ctg) or a library dropped on the canvas opens; on the rest of the window
        too (dragEnterEvent / dropEvent below). The canvas takes it through its drop hooks
        (canvas_hooks) when they are there, else through an event filter on it."""
        try:
            from ..editor import canvas_hooks
        except ImportError:  # swallow-ok: w1-canvasnav's hooks not in this build: the filter
            canvas_hooks = None
        full = canvas_hooks is None or not hasattr(canvas_hooks, "register_drop_handler")
        if not full:
            canvas_hooks.register_drop_handler("pages", _hook_accept, _hook_drop, priority=100)
        self._page_drop_filter = _PageDropFilter(self, full)
        self.view.viewport().installEventFilter(self._page_drop_filter)

    def _show_drop_frame(self, pages, libs) -> None:
        f = self.__dict__.get("_drop_frame")
        if f is None:
            f = self._drop_frame = _DropFrame(self.view.viewport())
        text = self.drop_hint(pages, libs)
        f.show_over(text)
        if self.statusBar().currentMessage() != text:
            self.statusBar().showMessage(text, 3000)

    def _hide_drop_frame(self) -> None:
        f = self.__dict__.get("_drop_frame")
        if f is not None and f.isVisible():
            f.hide()

    @staticmethod
    def drop_hint(pages, libs) -> str:
        """'Drop to open Mixer' (the status line and the frame over the canvas)."""
        names = [os.path.splitext(os.path.basename(p))[0] for p in pages + libs]
        if len(names) == 1:
            return "Drop to open %s" % names[0]
        return "Drop to open %d files" % len(names)

    def open_dropped(self, pages, libs) -> None:
        """Pages dropped from Explorer open in tabs (a page that does not open is reported);
        a library opens the page browser on its elements."""
        self._hide_drop_frame()
        if pages:
            failed = self.open_files(pages)
            self._note_other_install([p for p in pages if p not in {f for f, _r in failed}])
        if libs:
            from ..browser import PageBrowserDialog

            r = PageBrowserDialog.get_open_target(self, libs[0], before_delete=self._before_delete_or_rename)
            if r is not None:
                if r.kind == "page":
                    self.open_page(r.path)
                elif r.element:
                    self._open_element(r.path, r.element)

    def _note_other_install(self, paths) -> None:
        """Opened pages outside the install Plant SCADA uses: said in the status bar (the
        project label says so too)."""
        try:
            from ...io.citect_config import live_user_dir

            live = live_user_dir()
        except Exception:  # noqa: BLE001  # swallow-ok: no install known, nothing to say
            return
        if not live:
            return
        key = os.path.normcase(os.path.abspath(live))
        other = [
            p for p in paths if os.path.normcase(os.path.dirname(os.path.dirname(os.path.abspath(p)))) != key
        ]
        if other:
            self.statusBar().showMessage(
                "%s is not in the active install. The Graphics Builder and the runtime use %s "
                "and do not see edits to it." % (os.path.basename(other[0]), live),
                10000,
            )

    def _tab_drag(self, e) -> bool:
        """A tab of an editor window's tab bar is being dragged."""
        from ..panels.doc_tabs import TAB_MIME, DocTabBar

        return (
            e.mimeData() is not None
            and e.mimeData().hasFormat(TAB_MIME)
            and isinstance(e.source(), DocTabBar)
        )

    def dragEnterEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        pages, libs = dropped_documents(e.mimeData())
        tab = self._tab_drag(e)
        if pages or libs or tab:
            e.setDropAction(QtCore.Qt.MoveAction if tab else QtCore.Qt.CopyAction)
            e.accept()
            if pages or libs:
                self.statusBar().showMessage(self.drop_hint(pages, libs), 3000)
            return
        super().dragEnterEvent(e)

    def dragMoveEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        pages, libs = dropped_documents(e.mimeData())
        if pages or libs or self._tab_drag(e):
            e.accept()
            return
        super().dragMoveEvent(e)

    def dropEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        if self._tab_drag(e):
            # another window's tab moves here (at the end); this window's own: nothing
            from ..panels.doc_tabs import dragged_tab_index

            k = dragged_tab_index(e.mimeData(), e.source())
            e.setDropAction(QtCore.Qt.MoveAction)
            e.accept()
            if k >= 0 and e.source().window() is not self:
                self._tab_dropped(e.source(), k, len(self._docs))
            return
        pages, libs = dropped_documents(e.mimeData())
        if pages or libs:
            e.setDropAction(QtCore.Qt.CopyAction)
            e.accept()
            QtCore.QTimer.singleShot(0, self, lambda p=pages, lb=libs: self.open_dropped(p, lb))
            return
        super().dropEvent(e)

    def _current_project_dir(self) -> str:
        """The project new pages / templates are listed for: the open page's
        (or the open library element's), else the last used folder."""
        if self.doc is not None:
            return os.path.dirname(os.path.abspath(self.doc.path))
        return self._dialog_dir("open")

    def _new_page(self) -> None:
        """File > New > Page: the New window on its Page tab."""
        self.new_blank_dialog("page")

    def _create_page(self, ref: str, linked: bool, new_path: str) -> None:
        """A new page from template ``ref`` at ``new_path`` (linked to it or not). It opens in
        a new tab; nothing is written until its first Save."""
        from ..dialog.dialogs import page_defaults

        if not ref or not new_path:
            return
        if os.path.exists(new_path):
            QtWidgets.QMessageBox.warning(
                self, "New Page", "%s already exists. Choose another page name." % os.path.basename(new_path)
            )
            return
        if self._page_open_in_tab(new_path):
            # (a new page not saved yet: two tabs of one file, the second save would
            # overwrite the first)
            QtWidgets.QMessageBox.warning(
                self,
                "New Page",
                "A page named %s is already open in a tab. Choose another "
                "page name." % os.path.splitext(os.path.basename(new_path))[0],
            )
            return
        # a template without its own background: the Page Defaults colour of
        # the project the page is created in (CtDraw: every new page)
        target_dir = os.path.dirname(os.path.abspath(new_path))
        bg = page_defaults(target_dir)["background"]
        try:
            doc = EditableDocument.new_from_template(
                ref, new_path, linked=linked, background=(bg, bg), register=False, write=False
            )
        except KeyError:
            from ..dialog.genie_dialogs import _friendly_ref

            QtWidgets.QMessageBox.warning(
                self,
                "New Page",
                "Project %s cannot use template %s: the template is not in that project "
                "or the projects it includes."
                % (os.path.basename(os.path.dirname(os.path.abspath(new_path))), _friendly_ref(ref)),
            )
            return
        except Exception as e:  # noqa: BLE001
            from ..messages import error_box

            error_box(
                self,
                "New Page Failed",
                "The page could not be made: %s" % self._error_text(e),
                exc=e,
                next_steps=(
                    "Choose another template, or check that the template opens in the Library panel.",
                ),
                icon=QtWidgets.QMessageBox.Critical,
            )
            return
        self.set_document(doc)
        self._locked.clear()
        self._remember_dir("save", new_path)
        # nothing is written until the first Save (CtDraw's new page too); that save writes
        # the page, its runtime files and its project rows
        self._update_title()
        self._update_actions()
        from ..dialog.genie_dialogs import _friendly_ref

        name = os.path.splitext(os.path.basename(new_path))[0]
        self.statusBar().showMessage(
            "New page %s from template %s%s. Save it to add it to its project."
            % (name, _friendly_ref(ref), "" if linked else " (unlinked)"),
            10000,
        )

    def _create_library_element(self, kind: str, lib: str, elem: str) -> None:
        """An EMPTY element ``elem`` of ``lib`` opens in its own tab; draw it, then Save writes
        it into the library (an element needs at least one object)."""
        if not lib or not elem:
            return
        page_size = None
        if kind in ("template", "supergenie"):
            pb = self.doc.parsed.page if self.doc is not None else None
            page_size = (
                (pb.page_width, pb.page_height)
                if (pb and kind == "template")
                else new_template_size(lib)
                if kind == "template"
                else (400, 300)
            )
        try:
            doc = ElementDocument.new_element(lib, elem, kind, page_size=page_size)
        except Exception as e:  # noqa: BLE001
            from ..messages import error_box

            error_box(
                self,
                "New",
                "%s.%s could not be made: %s"
                % (os.path.splitext(os.path.basename(lib))[0], elem, self._error_text(e)),
                exc=e,
                next_steps=("Choose another name, or check that the library can be written to.",),
                actions=(("Show in Explorer", lambda p=lib: self._reveal_in_explorer(p)),),
            )
            return
        # (a new element hides nothing: its tab's activation; the tab shown keeps its own)
        self.set_document(doc)
        self._locked.clear()
        self.statusBar().showMessage(
            "New %s %s. Draw it, then save it into %s."
            % ({"supergenie": "super genie"}.get(kind, kind), doc.display_name, os.path.basename(lib)),
            8000,
        )

    def _render_region(self, src: tuple, size: tuple, indices=None):
        """Render scene region ``src`` (x, y, w, h) into a ``size`` TH
        thumbnail blob (format/thumbnail.encode)."""
        from ...format import thumbnail as _thumb

        tw, th = size
        img = QtGui.QImage(tw, th, QtGui.QImage.Format_RGB32)
        img.fill(QtCore.Qt.white)
        painter = QtGui.QPainter(img)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        try:
            with self.scene.export_only(indices):
                self.scene.render(painter, QtCore.QRectF(0, 0, tw, th), QtCore.QRectF(*src))
        finally:
            painter.end()
        bits = bytes(memoryview(img.constBits()))[: tw * th * 4]
        return _thumb.encode(bits, tw, th)

    def _element_thumbnail(self):
        """The TH thumbnail of the open ELEMENT (its content, longest side
        200 -- elements are not page-scaled). An element whose tab is not shown (Save All
        saves it without showing it) is drawn in a scratch scene."""
        geo = self.doc.thumbnail_geometry()
        if geo is None:
            return None
        if self.doc.page is not self._page:
            return self._scratch_region(self.doc, geo[0], geo[1])
        return self._render_region(geo[0], geo[1])

    @staticmethod
    def _scratch_region(doc, src: tuple, size: tuple):
        """:meth:`_render_region` of a document that is not the one shown: its page in a
        scratch scene (as _page_thumbnail_for draws another page's thumbnail)."""
        from ...format import thumbnail as _thumb
        from ...render.scene import PageScene

        if doc.page is None:
            return None
        tw, th = size
        scene = PageScene()
        try:
            scene.set_page(doc.page)
            img = QtGui.QImage(tw, th, QtGui.QImage.Format_RGB32)
            img.fill(QtCore.Qt.white)
            painter = QtGui.QPainter(img)
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
            try:
                with scene.export_only(None):
                    scene.render(painter, QtCore.QRectF(0, 0, tw, th), QtCore.QRectF(*src))
            finally:
                painter.end()
            bits = bytes(memoryview(img.constBits()))[: tw * th * 4]
            return _thumb.encode(bits, tw, th)
        finally:
            scene.clear()
            scene.deleteLater()

    def _save_document(self, doc, errors) -> bool:
        """Save ``doc`` (a tab of this window) in place as :meth:`_save_to` does, without
        showing its tab (File > Save All: the tab shown stays as it is; the thumbnail of a
        page or element not shown is drawn in a scratch scene). ``errors`` collects what
        could not be done. Never raises."""
        name = doc.display_name if isinstance(doc, ElementDocument) else os.path.basename(doc.path)
        shown, self.doc = self.doc, doc
        swapped = doc is not shown
        # (the save's own refreshes (an element's save refreshes the window) would draw the
        # swapped document on the canvas: held while the swap lasts, and the tab shown is
        # drawn once afterwards, since its genies may have been composed again)
        own_hold = swapped and self.__dict__.get("_refresh_hold") is None
        if own_hold:
            self._refresh_hold = []
        try:
            if self._version_refused(doc, errors):
                return False
            if doc.read_only and EditableDocument.file_read_only(doc.path):
                errors.append("%s is read-only. Use Save As on its tab to save a copy." % name)
                return False
            return self._save_to(errors=errors)
        except Exception as ex:  # noqa: BLE001 -- keep saving the others
            from ...infra import logsetup

            logsetup.note_exception("ui.window.files.save_document", page=doc.path)
            errors.append("%s could not be saved: %s" % (name, self._error_text(ex)))
            return False
        finally:
            self.doc = shown
            if own_hold:
                self.__dict__.pop("_refresh_hold", None)
            if swapped and shown is not None:
                with contextlib.suppress(RuntimeError):
                    self._refresh()
                    self._update_actions()

    def _render_thumbnail(self) -> None:
        """Regenerate the page's embedded thumbnail from the current content
        (CtDraw does on save; a stale one mis-previews the page in every
        page browser)."""
        from ...format import thumbnail as _thumb

        if isinstance(self.doc, ElementDocument):
            return
        pb = self.doc.parsed.page
        pw = self.doc.page.appearance.width or getattr(pb, "page_width", 0)
        ph = self.doc.page.appearance.height or getattr(pb, "page_height", 0)
        tw, th = _thumb.thumb_size(pw, ph)
        if not tw:
            return
        if self.doc.page is not self._page:
            # (a page whose tab is not shown, Save All: drawn in a scratch scene)
            thumb = self._page_thumbnail_for(self.doc)
            if thumb is not None:
                pb.thumbnail = thumb
            return
        img = QtGui.QImage(tw, th, QtGui.QImage.Format_RGB32)
        img.fill(QtCore.Qt.white)
        painter = QtGui.QPainter(img)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        try:
            with self.scene.export_only(None):
                self.scene.render(painter, QtCore.QRectF(0, 0, tw, th), QtCore.QRectF(0, 0, pw, ph))
        finally:
            painter.end()
        bits = bytes(memoryview(img.constBits()))[: tw * th * 4]
        pb.thumbnail = _thumb.encode(bits, tw, th)

    def _save_element_to(self, path=None, name=None, errors=None) -> bool:
        """Save the open library ELEMENT into its library (or, Save As, into
        ``path`` under ``name``). Never raises; ``errors`` as in _save_to."""
        doc = self.doc
        in_place = path is None and name is None
        if in_place and self._version_refused(doc, errors):
            return False
        if not self._confirm_shipped_template(doc, path or doc.path, in_place):
            return False
        # (a block the chain after a genie save stored from the block this tab holds is the
        # tab's own: no question, library._take_chain_write)
        if in_place and doc.changed_on_disk() and not self._take_chain_write(doc, look_again=False):
            r = QtWidgets.QMessageBox.warning(
                self,
                "Element Changed on Disk",
                "%s was changed in its library since it was opened here.\n\nOverwrite "
                "it with this version?" % doc.display_name,
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
                QtWidgets.QMessageBox.Cancel,
            )
            if r != QtWidgets.QMessageBox.Yes:
                if errors is not None:
                    errors.append(
                        "%s was not saved: it changed in its library since it "
                        "was opened, and you chose not to overwrite it." % doc.display_name
                    )
                return False
        thumb = None
        with contextlib.suppress(Exception):
            thumb = self._element_thumbnail()
        # (a copy the save brings up to date is drawn from the element as stored)
        from ...render.headless_thumb import element_thumbnail
        from ..dialog import element_save

        # (a large element is normalised in the window's helper while the window paints and
        # says what it does: ui/dialog/element_save; the automatic updates of this tab wait for the
        # save meanwhile, _genie_may_land)
        self._saving_doc = doc
        try:
            with self._busy("Saving %s…" % doc.display_name):
                doc.save(
                    path,
                    name,
                    thumbnail=thumb,
                    page_thumbnail=self._page_thumbnail_for,
                    thumbnail_for=element_thumbnail,
                    prepare=element_save.prepare_for(self),
                )
        except Exception as e:  # noqa: BLE001 -- a refused save must never escape
            return self._save_failed(path or doc.path, e, errors, offer_save_as=in_place)
        finally:
            self._saving_doc = None
        from ..recovery import AutosaveController

        AutosaveController.forget(doc)
        jobs = getattr(self, "_genie_jobs", None)
        if jobs is not None and in_place:
            # (its save composed the genies nested in it: an update that has not landed
            # would only compose them again, from the block the tab no longer holds)
            with contextlib.suppress(Exception):
                if jobs.cancel_automatic(doc, templates=False):
                    self._genies_updated(doc)
                    self._clear_auto_update_line(doc, templates=False)
        with contextlib.suppress(Exception):
            # (its save composed the genies nested in it: the tab does not compose them
            # again until a file they are made of changes, _update_stale_nested)
            ui_state(doc).genie_sources_done[NESTED_DONE] = doc.nested_genie_sources()
        self._watch(doc.path)  # a replace drops the watch
        self._after_library_write(doc.path)
        self._library_cache_notice(getattr(doc, "save_warning", ""))
        self._propagate_element(doc)
        self._refresh()
        # a genie save creates the missing pages of its super genies (CtDraw)
        self._report_super_pages(getattr(doc, "super_pages", None) or [])
        if (doc.path or "").lower().endswith(".ctt"):
            self._template_saved_prompt(doc)
        elif getattr(doc, "kind", "") == "supergenie":
            self._supergenie_saved_prompt(doc)
        elif getattr(doc, "kind", "") in ("symbol", "genie") and getattr(doc, "save_files", None):
            # the pages and genies that use it, directly or through other genies
            self._users_saved_prompt(doc)
        return True

    def _confirm_shipped_template(self, doc, target: str, in_place: bool) -> bool:
        """Confirm before saving into a library that ships with Plant SCADA."""
        from ...format.template_place import shipped_project

        if not (target or "").lower().endswith((".ctt", ".ctl", ".ctm")):
            return True
        proj = shipped_project(target)
        if (
            proj is None
            or ui_state(doc).shipped_ok
            or not app_settings().value("opt/warn_shipped_template", True, type=bool)
        ):
            return True
        what = {".ctt": "template", ".ctl": "symbol", ".ctm": "genie"}[os.path.splitext(target)[1].lower()]
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setWindowTitle("Standard %s Library" % what.title())
        box.setText(
            "%s is a %s library supplied with Plant SCADA (project %s)."
            % (os.path.basename(target), what, proj)
        )
        box.setInformativeText(
            "Saving here changes the pages of every project that includes %s, and a "
            "Plant SCADA update or reinstall replaces the file. Save a copy into your "
            "own project instead?" % proj
        )
        copy_btn = box.addButton("Save a Copy…", QtWidgets.QMessageBox.AcceptRole) if in_place else None
        anyway = box.addButton("Save Here Anyway", QtWidgets.QMessageBox.DestructiveRole)
        box.addButton(QtWidgets.QMessageBox.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is anyway:
            doc.ui.shipped_ok = True
            return True
        if copy_btn is not None and clicked is copy_btn:
            self._save_element_as()
        return False

    def _template_saved_prompt(self, doc) -> None:
        """After a template save, ask about updating what uses it."""
        from ..dialog.library_dialogs import shipped_library

        if getattr(doc, "save_files", None):
            # (the open pages take the saved template now, also when nothing closed needs
            # an update)
            self._follow_saved_template("template", doc.display_name)
        if not shipped_library(doc.path):
            if getattr(doc, "save_files", None):
                self._users_saved_prompt(doc)
            return
        st = app_settings()
        if not st.value("opt/save_template_warning", True, type=bool):
            return
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Information)
        box.setWindowTitle("Template Saved")
        box.setText("The pages that use template %s still show its old version." % doc.display_name)
        box.setInformativeText("Run Update Pages now to update them?")
        run = box.addButton("Update Pages…", QtWidgets.QMessageBox.AcceptRole)
        box.addButton("Later", QtWidgets.QMessageBox.RejectRole)
        again = QtWidgets.QCheckBox("Don't ask again", box)  # (owned by the box)
        box.setCheckBox(again)
        box.exec()
        if again.isChecked():
            st.setValue("opt/save_template_warning", False)
        if box.clickedButton() is run:
            # (the chain box ticked for this run, as the question of a template of the
            # user's own: the templates built on it are saved first)
            self._update_pages(chain=True)

    def _supergenie_saved_prompt(self, doc) -> None:
        """After a super genie save, ask about updating its pages."""
        from .library import _page_tab

        n_open = 0
        changed = []
        for w in type(self)._instances or [self]:
            with contextlib.suppress(RuntimeError):  # a window being destroyed
                w._open_pending_placing(doc.element_name)
            for e in list(getattr(w, "_docs", ())):
                d = e["doc"]
                # (a read-only tab, an earlier version or a page opened to view, is left: its
                # page is a closed one for Update Pages)
                if d is doc or not _page_tab(d):
                    continue
                with contextlib.suppress(Exception):
                    # a page created from THIS element: the same name in the
                    # library file the page resolves (another library may hold an
                    # element of the same name)
                    where = d.supergenie_element()
                    if (
                        where
                        and where[1].lower() == doc.element_name.lower()
                        and os.path.normcase(os.path.abspath(where[0]))
                        == os.path.normcase(os.path.abspath(doc.path))
                        and d.supergenie_status()[0] == "stale"
                        and d.update_supergenie_page()
                    ):
                        n_open += 1
                        changed.append((w, d))
                        w._mark_modeless_reason([d], "replace")
        # (a page shown in another window is drawn again now, with its title)
        self._follow_tabs_changed(changed)
        if n_open:
            self._update_title()
            from ..names import plural
            from ..notify import notify

            notify(
                self,
                "Updated %s from super genie %s. Save them to keep the change."
                % (plural(n_open, "open page"), doc.display_name),
                "success",
            )
        st = app_settings()
        if not st.value("opt/save_supergenie_warning", True, type=bool):
            return
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Information)
        box.setWindowTitle("Super Genie Saved")
        box.setText("The pages created from super genie %s still show its old version." % doc.display_name)
        box.setInformativeText("Run Update Pages now to update them?")
        run = box.addButton("Update Pages…", QtWidgets.QMessageBox.AcceptRole)
        box.addButton("Later", QtWidgets.QMessageBox.RejectRole)
        again = QtWidgets.QCheckBox("Don't ask again", box)  # (owned by the box)
        box.setCheckBox(again)
        self._exec_prompt(box)
        if again.isChecked():
            st.setValue("opt/save_supergenie_warning", False)
        if box.clickedButton() is run:
            self._update_pages()

    @staticmethod
    def _exec_prompt(box) -> None:
        """Show a modal question (a seam: tests answer it)."""
        box.exec()

    def _propagate_element(self, doc) -> None:
        """A genie was saved: update the open pages and elements that use it."""
        if getattr(doc, "kind", "") == "symbol":
            self._propagate_symbol(doc)
            # the copies of the symbol nested in placed genies and in open element tabs, in
            # every window, as after a genie save
            if app_settings().value("opt/auto_update_genies", True, type=bool):
                with self._updates_for("save"):
                    self._queue_open_genie_updates()
            return
        auto = app_settings().value("opt/auto_update_genies", True, type=bool)
        # (every editor window's pages, as a symbol save does)
        for w in type(self)._instances or [self]:
            with contextlib.suppress(RuntimeError):  # a window being destroyed
                w._open_pending_placing(doc.element_name)
                w._update_by_previous_version(doc, auto)
        # every open page and element tab whose genies are now out of date gets the changed
        # genies' copies composed again (CtDraw's page load), after this save returns: the
        # page shown first, then the other open pages, in every window
        if auto:
            # (the element tabs nearest the saved genie first: _update_stale_nested)
            with self._updates_for("save", [(doc.path, doc.element_name)]):
                self._queue_open_genie_updates()
        self._update_title()

    def _update_by_previous_version(self, doc, auto: bool) -> int:
        """Update this window's page tabs that place the saved genie."""
        from .library import _page_tab

        data = doc.previous_bytes() if callable(getattr(doc, "previous_bytes", None)) else None
        if data is None:
            return 0
        held = auto and self._runs_hold_tabs()
        if auto and not held:
            return 0  # (the update of every out of date genie takes them now)
        key = (doc.library_name, doc.element_name)
        low = (key[0].lower(), key[1].lower())
        n = 0
        for e in list(getattr(self, "_docs", ())):
            d = e["doc"]
            # (a read-only tab, an earlier version or a page opened to view, is left, as
            # its open composes nothing)
            if d is doc or not _page_tab(d):
                continue
            if ui_state(d).steps_after_template:
                # (its open's template step is in the worker: its genies are composed
                # after it, tabs._open_steps_after_template, as CtDraw's load orders them)
                continue
            try:
                path = d.library_path(doc.library_name, doc.element_name, "ctm")
                if not path or os.path.normcase(os.path.abspath(path)) != os.path.normcase(
                    os.path.abspath(doc.path)
                ):
                    continue
                roots = [i for i, (a, b) in d.genie_instances() if (a.lower(), b.lower()) == low]
            except Exception:  # noqa: BLE001 -- never let a page block the save
                from ...infra import logsetup

                logsetup.note_exception(
                    "ui.window.files.update_by_previous_version", page=getattr(d, "path", "")
                )
                continue
            if not roots:
                continue
            # (the instances are marked on the canvas until the update lands, as
            # _update_stale_genies marks the genies it composes again)
            d.ui.genies_updating = (len(d.page.objects), roots)
            if d is self.doc:
                self.view.set_pending(self._genies_updating(d))
            self._genie_job_runner().submit(
                d, {}, [doc.display_name], first=d is self.doc, why="save", previous={key: (doc.path, data)}
            )
            n += 1
        return n

    def _note_left_by_previous(self, doc, job) -> None:
        """A genie save's update of ``doc`` by the genie's previous version
        (:meth:`_update_by_previous_version`) landed and left instances it did not
        reproduce: with Settings' Update changed genies off nothing updates them later, so
        the window says so (with it on, the update of every out of date genie takes them)."""
        left = int(getattr(job, "left", 0) or 0)
        if (
            not left
            or not getattr(job, "previous", None)
            or app_settings().value("opt/auto_update_genies", True, type=bool)
        ):
            return
        from ..names import plural
        from ..notify import notify

        what = " and ".join(getattr(job, "names", None) or ()) or "the genie"
        page = os.path.splitext(os.path.basename(getattr(doc, "path", "") or ""))[0]
        notify(
            self,
            "%s on %s %s left out of date. Update Pages updates %s."
            % (
                plural(left, "placed " + what, "placed " + what),
                page,
                "is" if left == 1 else "are",
                "it" if left == 1 else "them",
            ),
            "info",
            detail=getattr(doc, "path", ""),
        )

    #: what the open tabs' genie updates queued now are for (:meth:`_updates_for`): their
    #: GenieJob.why and the library elements just saved [(library file, element)]
    _queue_for: tuple = ("", ())

    @staticmethod
    @contextlib.contextmanager
    def _updates_for(why: str, saved=()):
        """The open tabs' genie updates queued in the block are for ``why`` (GenieJob.why:
        'save' after a library save, 'follow' at the end of a run that saved library genies
        again); ``saved`` [(library file, element)]: the element tabs nearest them go first
        (_update_stale_nested). Every window's (:meth:`_queue_open_genie_updates`)."""
        was = PageFiles._queue_for
        PageFiles._queue_for = (why, tuple(saved or ()))
        try:
            yield
        finally:
            PageFiles._queue_for = was

    def _queue_open_genie_updates(self) -> None:
        """Queue genie updates for every open page, the shown one first."""
        gen = editing_mod._LIBRARY_GENERATION[0]
        wins = [self] + [w for w in (type(self)._instances or ()) if w is not self]
        for w in wins:
            try:
                docs, shown = list(w._docs), w.doc
                hold = getattr(w._genie_job_runner(), "hold", None)
            except RuntimeError:  # swallow-ok: a window being destroyed
                continue
            order = [e for e in docs if e["doc"] is shown] + [e for e in docs if e["doc"] is not shown]
            with contextlib.ExitStack() as held:
                if callable(hold):
                    held.enter_context(hold())
                for e in order:
                    if isinstance(e["doc"], _PendingPage):
                        continue
                    # (an element tab too: the genies nested in it, _update_stale_nested)
                    e["genie_gen"] = gen
                    with contextlib.suppress(Exception):
                        w._update_stale_genies(e["doc"], first=e["doc"] is shown)

    @staticmethod
    def _follow_tabs_changed(changed) -> None:
        """Redraw tabs that took a library save."""
        wins: list = []
        for w, _d in changed or ():
            if not any(w is x for x in wins):
                wins.append(w)
        for w in wins:
            with contextlib.suppress(RuntimeError):  # (a window being destroyed)
                if any(x is w and d is w.doc for x, d in changed):
                    w._sync_modeless("replace")
                    w._refresh()
                    w._update_actions()
                w._update_title()

    def _look_at_open_genies_again(self) -> None:
        """Check open pages and element tabs for genie updates again."""
        genies = app_settings().value("opt/auto_update_genies", True, type=bool)
        if genies:
            for w in [self] + [w for w in (type(self)._instances or ()) if w is not self]:
                try:
                    entries = list(w._docs)
                except RuntimeError:  # swallow-ok: a window being destroyed
                    continue
                for e in entries:
                    done = getattr(getattr(e["doc"], "ui", None), "genie_sources_done", None)
                    if done:
                        done.clear()  # (composed again from the files as they are)
        with contextlib.suppress(Exception):
            self._follow_symbols_in_tabs()
        if genies:
            with contextlib.suppress(Exception):
                self._queue_open_genie_updates()

    def _genie_job_runner(self):
        """This window's genie update jobs (ui/jobs/genie_jobs), made on first use."""
        jobs = getattr(self, "_genie_jobs", None)
        if jobs is None:
            from ..jobs.genie_jobs import GenieJobs

            jobs = self._genie_jobs = GenieJobs(self)
            jobs.started.connect(self._genie_job_started)
            jobs.finished.connect(self._genie_job_finished)
            jobs.gate = self._genie_may_land
        return jobs

    def _genie_may_land(self, doc, waited: float = 0.0) -> bool:
        """Should a finished genie update wait because the user is busy?"""
        if doc is not None and doc is self.__dict__.get("_saving_doc"):
            return False  # (its save is under way: ui/dialog/element_save)
        modal = QtWidgets.QApplication.activeModalWidget()
        prop = getattr(modal, "property", None)
        if modal is not None and not (callable(prop) and prop("ctg_lets_open_tabs_update")):
            # (a dialog the user works in; not the Genie Saved question or the search for
            # what uses a saved element, under which the open tabs take the save at once:
            # what is open comes first)
            return False
        if waited < redo_wait_s() and self._user_can_redo(doc):
            return False
        if doc is self.doc:
            view = getattr(self, "view", None)
            if view is not None and view.busy():
                return False
        return True

    @staticmethod
    def _user_can_redo(doc) -> bool:
        """``doc`` has a step of the user's to redo: an automatic update undone (the bar's
        Undo) is not one (a new version of the library replaces it anyway, and the update
        the user undid is not made again until then)."""
        if getattr(doc, "can_redo", None) is None or not doc.can_redo():
            return False
        redo = getattr(doc, "_redo", None)
        if redo is None:
            return True
        return any(not getattr(c, "automatic", False) for c in redo)

    def _note_auto_change(self, doc, what: str, command, changed: bool = True) -> None:
        """List an automatic change in the bar over the canvas."""
        if command is not None:
            with contextlib.suppress(AttributeError):
                command.automatic = True
        if not changed:
            return
        items = ui_state(doc).auto_changes
        if items is None:
            items = doc.ui.auto_changes = []
        doc.ui.auto_count = int(getattr(doc.ui, "auto_count", 0) or 0) + 1
        same = next((k for k, (w, _c) in enumerate(items) if w == what), None)
        if same is not None and command is None:
            pass  # (nothing new to undo: listed already)
        elif same is not None and items[same][1] is None:
            items[same] = (what, command)
        else:
            items.append((what, command))
        doc.ui.auto_bar_dismissed = False
        if doc is self.doc:
            self._auto_bar.show_for(doc)

    def _undo_auto_changes(self) -> None:
        """The bar's Undo: the automatic steps on top of the page's undo history go back;
        one the user made after them stays (it says so)."""
        doc = self.doc
        auto = {id(c) for _w, c in (ui_state(doc).auto_changes or []) if c is not None}
        n = 0
        while getattr(doc, "_undo", None) and id(doc._undo[-1]) in auto:
            self.undo()
            n += 1
        if n:
            doc.ui.auto_changes = [(w, c) for w, c in doc.ui.auto_changes if c is not None and c in doc._undo]
            if not doc.ui.auto_changes:
                doc.ui.auto_changes = []
            self._auto_bar.show_for(doc)
        elif auto:
            self.statusBar().showMessage(
                "You made changes after the automatic updates: undo them first (Edit > Undo).", 8000
            )

    @staticmethod
    def _only_auto_changes(doc) -> bool:
        """The page's unsaved changes are all automatic ones (no step of the user's)."""
        undo = getattr(doc, "_undo", None) or []
        auto = {id(c) for _w, c in (ui_state(doc).auto_changes or []) if c is not None}
        return bool(undo) and all(id(c) in auto for c in undo)

    def _ask_save_on_close(self, doc, pending, close_word: str = "closing it"):
        """Ask whether to save on close."""
        name = PageFiles._doc_name(doc)
        if not pending and self._only_auto_changes(doc):
            from ..editor.notices import auto_change_names

            whats = "; ".join(auto_change_names(doc.ui.auto_changes))
            box = QtWidgets.QMessageBox(
                QtWidgets.QMessageBox.Question,
                "Unsaved Changes",
                "%s was updated automatically: %s. Save these updates before %s?" % (name, whats, close_word),
                QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard | QtWidgets.QMessageBox.Cancel,
                self,
            )
            box.setDefaultButton(QtWidgets.QMessageBox.Discard)
            return box.exec()
        return QtWidgets.QMessageBox.question(
            self,
            "Unsaved Changes",
            "Save changes to %s before %s?%s" % (name, close_word, self._pending_text(pending)),
            QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard | QtWidgets.QMessageBox.Cancel,
        )

    def _update_stale_genies(self, doc, first: bool = False) -> list:
        """Bring the genies of ``doc`` up to date."""
        if ui_state(doc).steps_after_template:
            return []
        if self._runs_hold_tabs():
            self._after_library_run(doc)
            return []
        if self._waits_until_idle(doc):
            return []
        if isinstance(doc, ElementDocument):
            return self._update_stale_nested(doc, first)
        if getattr(doc, "read_only", False):
            return []
        changes, sources = {}, {}
        with contextlib.suppress(Exception):
            changes = doc.genie_changes()
            if changes:
                sources = doc.genie_sources(set(changes))
        # a genie whose nested genie changed stays 'out of date' until the genie that
        # nests it is saved again: once updated, it is not composed again until a file
        # it is made of changes
        done = ui_state(doc).genie_sources_done
        if done is None:
            done = doc.ui.genie_sources_done = {}
        changes = {k: v for k, v in changes.items() if sources.get(k) is None or done.get(k) != sources[k]}
        if not changes:
            return []
        names = set()
        for k, v in changes.items():
            names |= {"%s.%s" % c for c in v} if v else {"%s.%s" % k}
        names = sorted(names, key=str.lower)
        for k in changes:
            if sources.get(k) is not None:
                done[k] = sources[k]
        from ...infra import answer_memo, noop_memo

        memo_key = doc.genie_noop_key(changes)
        if noop_memo.known(memo_key):
            return []  # (composed before with these files: no change)
        why = self._queue_for[0]
        # (the end of a run on a page that took the save already: it shows the change, so no
        # marks and no line while it is composed again, and its step becomes part of the
        # save's, or is named as it: _genie_job_finished)
        quiet = why == "follow" and self._took_the_save(doc)
        # composed before with these files: the worker's answer then, at once
        ans = answer_memo.get(memo_key)
        if ans is not None:
            from ..jobs import genie_jobs

            n = None
            top = genie_jobs.undo_top(doc)
            with contextlib.suppress(Exception):
                n = genie_jobs.apply_answer(doc, ans)
            if n is not None and n >= 0:
                self._genie_job_finished(
                    doc,
                    names,
                    n,
                    stepped=genie_jobs.undo_top(doc) is not top,
                    why=why,
                    below=top,
                    quiet=quiet,
                )
                return names
        # the genies being composed again are marked on the canvas until the update lands
        if not quiet:
            with contextlib.suppress(Exception):
                doc.ui.genies_updating = (
                    len(doc.page.objects),
                    [i for i, (a, b) in doc.genie_instances() if (a.lower(), b.lower()) in changes],
                )
                if doc is self.doc:
                    self.view.set_pending(self._genies_updating(doc))
        self._genie_job_runner().submit(doc, changes, names, first=first, why=why, quiet=quiet)
        return names

    def _took_the_save(self, doc) -> bool:
        """Did ``doc``'s tab take the library save whose run just ended?"""
        jobs = self.__dict__.get("_genie_jobs")
        pending = getattr(jobs, "pending", None)
        if callable(pending) and any(getattr(j, "why", "") == "save" for j in pending(doc)):
            return True
        return self._save_step_entry(doc) is not None

    @staticmethod
    def _save_step_entry(doc) -> Optional[str]:
        """What the automatic changes bar says for the newest update of a library save that
        ``doc``'s undo history still holds (its step marked genie_save_step:
        :meth:`_genie_job_finished`); None when it holds none (saved since, or undone)."""
        held = {id(c) for c in getattr(doc, "_undo", None) or () if getattr(c, "genie_save_step", False)}
        for what, c in reversed(ui_state(doc).auto_changes or ()):
            if c is not None and id(c) in held:
                return what
        return None

    def _update_stale_nested(self, doc, first: bool = False) -> list:
        """Recompose an element tab's nested genies that changed since it was saved."""
        from ..dialog.history_versions import is_version

        if doc.is_new or getattr(doc, "read_only", False) or is_version(doc):
            return []
        keys, sources = frozenset(), frozenset()
        try:
            if doc.changed_on_disk() and not self._take_chain_write(doc, look_again=False):
                if self._libraries_being_saved():
                    self._after_library_run(doc)
                    # (the chain's helper wrote it and does not name it yet: looked at
                    # again as soon as its answer so far does, not at its end)
                    self._when_chain_names(doc)
                return []
            # (first the library symbols placed in it, as a page's open does them before
            # its genies: the worker then composes from the records with them)
            self._update_placed_symbols(doc)
            if self._waits_until_idle(doc):
                return []  # (both when the user has finished)
            keys = doc.nested_genie_changes()
            if keys:
                sources = doc.nested_genie_sources()
        except Exception:  # noqa: BLE001 -- the tab stays as it was read
            from ...infra import logsetup

            logsetup.note_exception("ui.window.files.update_stale_nested", page=doc.path)
            return []
        if not keys:
            return []
        done = ui_state(doc).genie_sources_done
        if done is None:
            done = doc.ui.genie_sources_done = {}
        if done.get(NESTED_DONE) == sources:
            return []
        done[NESTED_DONE] = sources
        names, label = self._nested_copy_names(doc, keys)
        why, saved = self._queue_for
        quiet = why == "follow" and self._took_the_save(doc)
        # the genies whose copies change are marked on the canvas until the update lands
        if not quiet:
            with contextlib.suppress(Exception):
                doc.ui.genies_updating = (len(doc.page.objects), [i for i, _k in doc.genie_instances()])
                if doc is self.doc:
                    self.view.set_pending(self._genies_updating(doc))
        self._genie_job_runner().submit(
            doc,
            {k: None for k in keys},
            names,
            first=first,
            why=why,
            near=self._nesting_depth(doc, saved),
            quiet=quiet,
            **({"label": label} if label else {}),
        )
        return names

    @staticmethod
    def _nested_copy_names(doc, keys) -> tuple:
        """(names, undo step label) for recomposed nested copies."""
        from ...io.genieusage import TEMPLATE_COPY, nested_copy_labels

        try:
            spelled = nested_copy_labels(doc, keys)
        except Exception:  # noqa: BLE001 -- named by their keys then
            from ...infra import logsetup

            logsetup.note_exception("ui.window.files.nested_copy_names", page=getattr(doc, "path", ""))
            spelled = sorted(("%s.%s" % k for k in keys), key=str.lower)
        lead = TEMPLATE_COPY[len("the ") :] if TEMPLATE_COPY.startswith("the ") else TEMPLATE_COPY
        names = [lead + x[len(TEMPLATE_COPY) :] if x.startswith(TEMPLATE_COPY) else x for x in spelled]
        templates = [x for x in spelled if x.startswith(TEMPLATE_COPY)]
        if templates and len(templates) == len(spelled):
            label = "%s updated" % ", ".join(names)
        elif templates:
            label = "update genies and template from library"
        else:
            label = ""  # (the job's own: 'update genies from library')
        return names, label

    @staticmethod
    def _nesting_depth(doc, saved) -> Optional[int]:
        """How deep the element tab nests the first of ``saved``."""
        if not saved:
            return None
        from ...format import genie_place as GP

        best = None
        try:
            res = doc._resolver()
            for path, element in saved:
                chain = GP.records_nesting_chain(doc.parsed.page.objects, path, element, res, shortest=True)
                if chain is not None and (best is None or len(chain) < best):
                    best = len(chain)
        except Exception:  # noqa: BLE001 -- its order only: after the tabs that nest it
            from ...infra import logsetup

            logsetup.note_exception("ui.window.files.nesting_depth", page=getattr(doc, "path", ""))
            return None
        return best

    @staticmethod
    def _tab_takes_symbols(doc) -> bool:
        """An open page or element tab whose placed library symbols may be re-derived in it:
        not a restored tab not opened yet, a new element, a read-only page or library, an
        earlier version shown read-only, or one changed on disk since the tab read it (the
        reload question comes first)."""
        from ..dialog.history_versions import is_version

        if isinstance(doc, _PendingPage) or getattr(doc, "read_only", False) or is_version(doc):
            return False
        if isinstance(doc, ElementDocument) and doc.is_new:
            return False
        return not doc.changed_on_disk()

    def _placed_symbol_step(self, doc, stale, remember: bool = True) -> int:
        """Re-derive stale placed library symbols in ``doc``."""
        top = (getattr(doc, "_undo", None) or [None])[-1]
        names = sorted(
            {
                "%s.%s" % key
                for _i, key, _k in doc.symbol_placements()
                if (key[0].lower(), key[1].lower()) in stale
            },
            key=str.lower,
        )
        n = doc.update_symbol_placements(stale) or 0
        now = (getattr(doc, "_undo", None) or [None])[-1]
        self._note_auto_change(
            doc,
            "symbols updated from their library (%s)" % ", ".join(names[:5]),
            now if now is not top else None,
            changed=now is not top,
        )
        if remember:
            self._remember_symbol_versions(doc, stale)
        return n

    @staticmethod
    def _remember_symbol_versions(doc, stale) -> None:
        """An update in its tab took the library symbols ``stale`` placed in ``doc`` to their
        current versions (an automatic one, the open's, a symbol save's, a run's): undone,
        it is not made again by an automatic update until a symbol is saved again
        (DocumentUiState.symbols_done; a failed or cancelled genie update leaves it)."""
        keys = {(a.lower(), b.lower()) for a, b in stale}
        st = ui_state(doc)
        if not keys or st is not getattr(doc, "ui", None):
            return
        st.symbols_done = frozenset(
            {x for x in st.symbols_done if x[0] not in keys} | PageFiles._symbol_versions(doc, keys)
        )

    @staticmethod
    def _symbol_versions(doc, keys) -> frozenset:
        """((library, element), its current link-table entry), lower case, of the library
        symbols ``keys`` placed in ``doc`` (genie_place.current_link_entry: the element's
        stamp, new at each save of it); a symbol whose element does not read is left out."""
        from ...format import genie_place as GP

        out = set()
        for lib, elem in sorted(keys):
            try:
                path = doc.library_path(lib, elem, "ctl")
                if path:
                    out.add(((lib, elem), GP.current_link_entry(path, lib, elem)[1].lower()))
            except Exception:  # noqa: BLE001  # swallow-ok: no version to remember for it
                continue
        return frozenset(out)

    def _update_placed_symbols(self, doc, gate: bool = True) -> list:
        """Update an open tab's placed symbols whose element changed."""
        if not app_settings().value("opt/auto_update_symbols", True, type=bool):
            return []
        if ui_state(doc).steps_after_template:
            return []
        if self._runs_hold_tabs():
            self._after_library_run(doc)
            return []
        element = isinstance(doc, ElementDocument)
        try:
            if not self._tab_takes_symbols(doc):
                return []
            placed = doc.symbol_placements()
            stale = doc.stale_symbol_elements() if placed else set()
            done = ui_state(doc).symbols_done
            if stale and done:
                # (a version it took before and the user undid since stays undone)
                stale -= {k for k, _v in self._symbol_versions(doc, stale) & done}
            if not stale:
                return []
            if gate and self._waits_first(doc):
                return []  # (the template step or reload that waits comes first)
            if self._redo_holds(doc) if gate == "redo" else bool(gate) and not self._may_change_now(doc):
                # (a step in the middle of a drag would move the records under the view's
                # selection, and its release would move other objects)
                self._wait_until_idle(doc)
                return []
            names = sorted(
                {"%s.%s" % key for _i, key, _k in placed if (key[0].lower(), key[1].lower()) in stale},
                key=str.lower,
            )
            top = (getattr(doc, "_undo", None) or [None])[-1]
            self._placed_symbol_step(doc, stale)
        except Exception:  # noqa: BLE001 -- the tab stays as it was read (its open does it)
            from ...infra import logsetup

            logsetup.note_exception("ui.window.files.update_placed_symbols", page=doc.path)
            return []
        if (getattr(doc, "_undo", None) or [None])[-1] is top:
            return []
        self._mark_modeless_reason([doc], "replace")
        if doc is self.doc:
            # (drawn now when its tab's scene is on the canvas; during the tab's activation
            # (its first show) the activation draws it right after)
            ent = next((e for e in self._docs if e["doc"] is doc), None)
            if ent is not None and ent.get("scene") is self.scene:
                self._sync_modeless("replace")
                self._refresh()
                self._update_actions()
            from ..notify import notify

            what = ", ".join(names[:3]) + (" …" if len(names) > 3 else "")
            if element:
                notify(
                    self,
                    "Updated %s in %s. Save it to keep the change." % (what, doc.display_name),
                    "success",
                    detail=doc.path,
                    key="symbols:%s:%s" % (doc.path, doc.element_name),
                )
            else:
                notify(
                    self,
                    "Updated %s on %s. Save the page to keep the change."
                    % (what, os.path.splitext(os.path.basename(doc.path))[0]),
                    "success",
                    detail=doc.path,
                    key="symbols:%s" % doc.path,
                )
        self._update_title()
        return names

    def _follow_symbols_in_tabs(self) -> int:
        """Update placed symbols in every editor window's tabs."""
        if not app_settings().value("opt/auto_update_symbols", True, type=bool):
            return 0
        n = 0
        for w in [self] + [w for w in (type(self)._instances or ()) if w is not self]:
            try:
                entries = list(w._docs)
            except RuntimeError:  # swallow-ok: a window being destroyed
                continue
            for e in entries:
                if isinstance(e["doc"], _PendingPage):
                    continue
                with contextlib.suppress(RuntimeError):
                    n += bool(w._update_placed_symbols(e["doc"]))
        return n

    def _bring_up_to_date(self, doc, first: bool = False, gate: bool = True) -> list:
        """Update ``doc``'s symbols and genies that changed since its tab took them."""
        took = list(self._update_placed_symbols(doc, gate=gate))
        if app_settings().value("opt/auto_update_genies", True, type=bool):
            self._update_stale_genies(doc, first=first)
        return took

    # -- automatic changes that wait for the user ------------------------------------------
    def _may_change_now(self, doc, redo: bool = True) -> bool:
        """Can an automatic change of ``doc`` be made now?"""
        import time

        if not redo:
            return self._genie_may_land(doc, float("inf"))
        since = next((item[1] for item in self.__dict__.get("_idle_docs") or () if item[0] is doc), None)
        return self._genie_may_land(doc, time.monotonic() - since if since is not None else 0.0)

    def _redo_holds(self, doc) -> bool:
        """Is ``doc`` holding an update to keep its redo steps?"""
        import time

        if not self._user_can_redo(doc):
            return False
        since = next((item[1] for item in self.__dict__.get("_idle_docs") or () if item[0] is doc), None)
        return (time.monotonic() - since if since is not None else 0.0) < redo_wait_s()

    def _wait_until_idle(self, doc, redraw: bool = False, templates=(), since=None, reload: str = "") -> None:
        """Wait until the user is done, then update ``doc``."""
        import time
        from ..prefs.job_options import poll_ms

        labels = {str(x).lower() for x in templates or () if x}
        waits = self.__dict__.setdefault("_idle_docs", [])
        for item in waits:
            if item[0] is doc:
                item[2] = item[2] or redraw
                item[3] |= labels
                item[4] = item[4] or reload
                break
        else:
            waits.append(
                [doc, time.monotonic() if since is None else since, bool(redraw), labels, reload or ""]
            )
        timer = self.__dict__.get("_idle_timer")
        if timer is None:
            timer = self._idle_timer = QtCore.QTimer(self)
            timer.timeout.connect(self._idle_tick)
        if not timer.isActive():
            timer.setInterval(poll_ms())
            timer.start()

    def _waits_until_idle(self, doc) -> bool:
        return any(item[0] is doc for item in self.__dict__.get("_idle_docs") or ())

    def _reload_waits(self, doc) -> bool:
        """``doc`` waits for the user with a reload of what the chain's helper saved
        (:meth:`_wait_until_idle` ``reload``): its file changed by ctgedit's own update, which
        the tab takes when the user has finished."""
        return any(item[0] is doc and item[4] for item in self.__dict__.get("_idle_docs") or ())

    def _waits_first(self, doc) -> bool:
        """``doc`` waits with a step that comes before its symbols and genies (the chain's
        template step, a reload of what the chain's helper saved): its symbol step waits
        for it too (:meth:`_idle_tick` makes them in that order)."""
        return any(item[0] is doc and (item[3] or item[4]) for item in self.__dict__.get("_idle_docs") or ())

    def _take_idle_wait(self, doc, show: bool = False):
        """Stop ``doc`` waiting for the user."""
        waits = self.__dict__.get("_idle_docs") or []
        item = next((x for x in waits if x[0] is doc), None)
        if item is None:
            return None
        if show and (item[4] or self._redo_holds(doc)):
            redraw, item[2] = bool(item[2]), False
            return redraw, frozenset(), item[1], ""
        waits.remove(item)
        return bool(item[2]), frozenset(item[3]), item[1], item[4]

    def _templates_that_waited(self, doc, labels, hold: bool = False) -> int:
        """The chain's template step of ``doc`` that waited for the user (``labels``:
        :meth:`_wait_until_idle`), made now (library._chain_templates_in_tabs, no second
        wait); ``hold``: the window does not draw it (a tab being shown: its show draws it).
        The pages changed."""
        if not labels:
            return 0
        own = hold and self.__dict__.get("_refresh_hold") is None
        if own:
            self._refresh_hold = []
        try:
            return self._chain_templates_in_tabs(labels, docs=(doc,), gate=False)
        except Exception:  # noqa: BLE001 -- the page is updated at its next open
            from ...infra import logsetup

            logsetup.note_exception("ui.window.files.templates_that_waited", page=getattr(doc, "path", ""))
            return 0
        finally:
            if own:
                self.__dict__.pop("_refresh_hold", None)

    def _idle_tick(self) -> list:
        """Timer for _wait_until_idle."""
        import time

        waits = list(self.__dict__.get("_idle_docs") or ())
        self._idle_docs = []
        timer = self.__dict__.get("_idle_timer")
        if self.__dict__.get("_window_closed"):
            # (the window was closed: nothing lands on its pages)
            if timer is not None:
                timer.stop()
            return []
        now = time.monotonic()
        ready = []
        for item in waits:
            doc, since, redraw, labels, reload = item
            e = next((x for x in self._docs if x["doc"] is doc), None)
            if e is None:
                continue  # (closed, or moved: _move_entry_to)
            if not self._may_change_now(doc, redo=False):
                self._idle_docs.append(item)
                continue
            if redraw and not reload:
                # (with a reload, after it: the reload draws it, a tab it keeps is drawn
                # then)
                item[2] = False
                self._redraw_waited(doc, e)
            if not self._genie_may_land(doc, now - since):
                self._idle_docs.append(item)  # (steps to redo: for up to a minute)
                continue
            if labels and self._runs_hold_tabs():
                # (the template step waits for a run that saves library elements, as the
                # symbol and genie steps do: it reads the template's library)
                self._idle_docs.append(item)
                continue
            ready.append(item)
        for item in ready:
            doc, _since, redraw, labels, reload = item
            if reload:
                if self._reload_that_waited(doc, reload):
                    continue  # (reloaded: its open checks follow)
                if redraw and (reload == "page" or self._stays_changed_on_disk(doc)):
                    # (the library watch's redraw that waited with the reload: the tab kept
                    # for a change of the user's is drawn from the files as they are; an
                    # element tab that read the store again was drawn by it)
                    item[2] = False
                    e = next((x for x in self._docs if x["doc"] is doc), None)
                    if e is not None:
                        self._redraw_waited(doc, e)
            # (the template first, as the chain's end and a page's open do it)
            self._templates_that_waited(doc, labels)
            try:
                # (the wait is over: no second look at it)
                self._bring_up_to_date(doc, first=doc is self.doc, gate=False)
            except Exception:  # noqa: BLE001 -- the tab stays as it is (its next show looks)
                from ...infra import logsetup

                logsetup.note_exception("ui.window.files.idle_tick", page=getattr(doc, "path", ""))
        if timer is not None and not self._idle_docs:
            timer.stop()
        return [item[0] for item in ready]

    def _redraw_waited(self, doc, e) -> None:
        """The library watch's redraw that waited for the user (:meth:`_wait_until_idle`
        ``redraw``): the page shown is drawn again from the library files as they are now,
        a hidden tab when it is shown (its 'stale' mark)."""
        try:
            if doc is self.doc:
                from .libwatch import redraw_from_libraries

                redraw_from_libraries(doc)
                self._refresh()
                self._update_diag_chips()
                self.view.viewport().update()
            else:
                e["stale"] = True  # (drawn again when its tab is shown)
        except Exception:  # noqa: BLE001 -- keep showing what we have
            from ...infra import logsetup

            logsetup.note_exception("ui.window.files.idle_tick/redraw", page=getattr(doc, "path", ""))

    @staticmethod
    def _stays_changed_on_disk(doc) -> bool:
        """``doc``'s file (an element tab: its library's block) differs from what the tab
        read: it did not take what was saved meanwhile."""
        try:
            return bool(doc.changed_on_disk())
        except Exception:  # noqa: BLE001 -- not known: taken as changed
            from ...infra import logsetup

            logsetup.note_exception("ui.window.files.stays_changed_on_disk", page=getattr(doc, "path", ""))
            return True

    def _reload_that_waited(self, doc, kind: str) -> bool:
        """Run a reload that waited for the user."""
        from ...infra import logsetup

        waiting = self._chain_writes_waiting().pop(id(doc), None)
        versions = waiting[1] if waiting is not None and waiting[0]() is doc else {}
        try:
            if kind == "element":
                self._settle_saved_tabs([[doc.path, doc.element_name]], reload=True, gate=False)
                return False
            if not doc.changed_on_disk():
                return False
            # (a window of the tab holding typed values keeps the tab, as a change of the
            # user's does: the reload would close it with what was typed)
            if (
                not getattr(doc, "has_unstepped_changes", False)
                and (not doc.dirty or self._only_automatic_since_saved(doc))
                and not self._pending_windows(doc)
            ):
                return bool(self.revert_document(doc, ask=False))
            self._take_chain_write(doc, versions)
        except Exception:  # noqa: BLE001 -- the tab is left (its notice says it)
            logsetup.note_exception("ui.window.files.reload_that_waited", page=getattr(doc, "path", ""))
        return False

    def _hold_tab_updates_for_run(self) -> int:
        """Hold the open tabs' automatic updates while a chain runs."""
        n = 0
        for w in [self] + [w for w in (type(self)._instances or ()) if w is not self]:
            try:
                jobs, entries = w.__dict__.get("_genie_jobs"), list(w._docs)
            except RuntimeError:  # swallow-ok: a window being destroyed
                continue
            if jobs is None:
                continue
            for e in entries:
                d = e["doc"]
                if isinstance(d, _PendingPage):
                    continue
                try:
                    if not jobs.cancel_automatic(d, templates=False):
                        continue
                    # (composed again at the run's end, from the files as they are then)
                    ui_state(d).genie_sources_done.clear()
                    w._genies_updated(d)
                    w._clear_auto_update_line(d, templates=False)
                    w._after_library_run(d)
                    n += 1
                except Exception:  # noqa: BLE001 -- the tab lands its update as before
                    from ...infra import logsetup

                    logsetup.note_exception(
                        "ui.window.files.hold_tab_updates_for_run", page=getattr(d, "path", "")
                    )
        return n

    def _after_library_run(self, doc) -> None:
        """Update ``doc`` after a run that saves library genies."""
        from ..prefs.job_options import poll_ms

        waiting = self.__dict__.setdefault("_after_run_docs", [])
        if not any(d is doc for d in waiting):
            waiting.append(doc)
        timer = self.__dict__.get("_after_run_timer")
        if timer is None:
            timer = self._after_run_timer = QtCore.QTimer(self)
            timer.timeout.connect(self._library_run_over)
        if not timer.isActive():
            timer.setInterval(poll_ms())
            timer.start()

    def _when_chain_names(self, doc) -> None:
        """Wait for the running chain's answer to name ``doc``'s element."""
        if not self._chain_progress:
            return
        from ..prefs.job_options import poll_ms

        waiting = self.__dict__.setdefault("_chain_named_docs", [])
        if not any(d is doc for d in waiting):
            waiting.append(doc)
        timer = self.__dict__.get("_chain_named_timer")
        if timer is None:
            timer = self._chain_named_timer = QtCore.QTimer(self)
            timer.timeout.connect(self._chain_named_tick)
        if not timer.isActive():
            timer.setInterval(poll_ms())
            timer.start()

    def _chain_named_tick(self) -> list:
        """Timer for _when_chain_names."""
        from ..jobs.update_run import UpdatePagesRun

        waiting = list(self.__dict__.get("_chain_named_docs") or ())
        self._chain_named_docs = []
        timer = self.__dict__.get("_chain_named_timer")
        if self.__dict__.get("_window_closed") or not UpdatePagesRun.users_busy or not self._chain_progress:
            if timer is not None:
                timer.stop()
            return []
        versions = self._chain_versions_so_far()
        done = []
        for doc in waiting:
            if not any(e["doc"] is doc for e in self._docs):
                continue  # (closed, or moved to another window)
            try:
                if doc.changed_on_disk() and not self._chain_wrote(doc, versions):
                    self._chain_named_docs.append(doc)
                    continue
                self._update_stale_genies(doc, first=doc is self.doc)
            except Exception:  # noqa: BLE001 -- the chain's end looks at it
                from ...infra import logsetup

                logsetup.note_exception("ui.window.files.chain_named_tick", page=getattr(doc, "path", ""))
                continue
            done.append(doc)
        if timer is not None and not self._chain_named_docs:
            timer.stop()
        return done

    def _nested_job_refused(self, doc) -> None:
        """The worker refused a nested genie job because the library changed."""
        with contextlib.suppress(Exception):
            ui_state(doc).genie_sources_done.pop(NESTED_DONE, None)
        if getattr(doc, "read_only", False) or not app_settings().value(
            "opt/auto_update_genies", True, type=bool
        ):
            return
        try:
            self._update_stale_genies(doc, first=doc is self.doc)
        except Exception:  # noqa: BLE001 -- the tab stays as it is (its next show looks)
            from ...infra import logsetup

            logsetup.note_exception("ui.window.files.nested_job_refused", page=getattr(doc, "path", ""))

    def _library_run_over(self) -> list:
        """The timer of :meth:`_after_library_run`: when the run has ended (and a run in this
        process has brought the open tabs up to date: tabs_following), the tabs that waited
        for it are brought up to date, those still open in this window. The documents."""
        from ..jobs.update_run import UpdatePagesRun

        if self._libraries_being_saved() or UpdatePagesRun.tabs_following:
            return []
        timer = self.__dict__.get("_after_run_timer")
        if timer is not None:
            timer.stop()
        waiting, self._after_run_docs = list(self.__dict__.get("_after_run_docs") or ()), []
        if not any(w is self for w in type(self)._instances or ()):
            return []  # (the window was closed)
        gen = editing_mod._LIBRARY_GENERATION[0]
        genies = app_settings().value("opt/auto_update_genies", True, type=bool)
        done = []
        for doc in waiting:
            e = next((e for e in self._docs if e["doc"] is doc), None)
            if e is None:
                continue  # (closed, or moved to another window)
            if genies:
                e["genie_gen"] = gen
            try:
                self._bring_up_to_date(doc, first=doc is self.doc)
            except Exception:  # noqa: BLE001 -- the tab stays as it is (its next show looks)
                from ...infra import logsetup

                logsetup.note_exception("ui.window.files.library_run_over", page=getattr(doc, "path", ""))
                continue
            done.append(doc)
        return done

    def _genie_answer_before_derive(self, doc) -> bool:
        """Apply a remembered genie answer before the page's derive."""
        if (
            isinstance(doc, ElementDocument)
            or doc.read_only
            or not app_settings().value("opt/auto_update_genies", True, type=bool)
            or self._runs_hold_tabs()
        ):
            return False
        from ..jobs import genie_jobs

        got = genie_jobs.preapply_answer(doc)
        if got is None:
            return False
        names, step, changes, sources, n = got
        done = doc.ui.genie_sources_done
        for k in changes:
            if sources.get(k) is not None:
                done[k] = sources[k]
        # (no range replaced: only the link table took the current stamps, said quietly)
        doc.ui.genie_preapplied = (names, step, not n)
        return True

    def _genie_answer_said(self, doc) -> None:
        """The open checks' line for a genie update :meth:`_genie_answer_before_derive`
        applied: as _genie_job_finished says a landed one (the page shows it already)."""
        pre = ui_state(doc).genie_preapplied
        if not pre:
            return
        doc.ui.genie_preapplied = None
        names, step = pre[0], pre[1]
        quiet = len(pre) > 2 and pre[2]
        self._update_title()
        if doc is self.doc:
            self._update_actions()
        if not quiet:
            self._genies_updated_message(doc, names)
        self._note_auto_change(
            doc,
            "%s updated from %s library"
            % (
                ", ".join(names[:3]) + (" …" if len(names) > 3 else ""),
                "its" if len(names) == 1 else "their",
            ),
            step,
        )

    def _genies_updated_message(self, doc, names) -> None:
        from ..notify import notify

        path = getattr(doc, "path", "")
        what = ", ".join(names[:3]) + (" …" if len(names) > 3 else "")
        if isinstance(doc, ElementDocument):
            notify(
                self,
                "Updated %s in %s. Save it to keep the change." % (what, doc.display_name),
                "success",
                detail=path,
                key="genies:%s:%s" % (path, doc.element_name),
            )
            return
        page = os.path.splitext(os.path.basename(path))[0]
        notify(self, "Updated %s on %s." % (what, page), "success", detail=path, key="genies:%s" % path)

    @staticmethod
    def _genies_updating(doc) -> list:
        """The root indices of the genies a background update of ``doc`` is composing
        again (none once the page's objects changed in number: the indices moved)."""
        st = getattr(ui_state(doc), "genies_updating", None)
        if not st or getattr(doc, "page", None) is None or st[0] != len(doc.page.objects):
            return []
        return list(st[1])

    def _genies_updated(self, doc) -> None:
        with contextlib.suppress(Exception):
            doc.ui.genies_updating = None
        if doc is self.doc:
            self.view.set_pending([])

    def _clear_auto_update_line(self, doc, genies: bool = True, templates: bool = True) -> None:
        """Clear the status line of an automatic update that won't land."""
        if doc is not self.doc:
            return
        msg = self.statusBar().currentMessage()
        if "you can go on working" not in msg:
            return
        if (genies and msg.startswith("Updating ")) or (
            templates and msg.startswith("Applying the template ")
        ):
            self.statusBar().clearMessage()

    def _genie_job_started(self, doc, names) -> None:
        if doc is not self.doc:
            return
        job = getattr(getattr(self, "_genie_jobs", None), "last_started", None)
        if job is not None and getattr(job, "quiet", False):
            # (the end of a run on a page that shows the save already: it only finishes the
            # save's step, nothing on the page is marked as changing)
            return
        if job is not None and getattr(job, "template", False):
            # (named as its library spells it)
            from ..names import template_name

            self.statusBar().showMessage(
                "Applying the template %s again (you can go on working)…"
                % ", ".join(template_name(x) for x in names[:1])
            )
            return
        if doc is self.doc:
            self.statusBar().showMessage(
                "Updating %s %s. The genies marked in amber change when it is "
                "done (you can go on working)…"
                % (
                    ", ".join(names[:3]),
                    "in this element" if isinstance(doc, ElementDocument) else "on this page",
                )
            )

    def _genie_job_finished(
        self, doc, names, n, job=None, stepped: bool = False, why: str = "", below=None, quiet: bool = False
    ) -> None:
        """A genie update of ``doc`` landed."""
        if not any(e["doc"] is doc for e in self._docs):
            return  # (the page was closed meanwhile)
        stepped = bool(stepped or getattr(job, "stepped", False))
        why = why or getattr(job, "why", "")
        quiet = bool(quiet or getattr(job, "quiet", False))
        below = below if below is not None else getattr(job, "below", None)
        self._note_left_by_previous(doc, job)
        if getattr(job, "unfinished", False):
            # a genie left as stored (its library unreadable then): the next open or tab
            # switch tries again, not only the next change of a file
            ui_state(doc).genie_sources_done.clear()
        from ..notify import notify, open_log_action

        if job is None or getattr(job, "bindings", None) is None:
            self._genies_updated(doc)
        if job is not None and getattr(job, "template", False):
            # the open's template step, computed in the worker: said as the inline step says
            # it, then the genie step that follows it (tabs._post_open_checks). A remembered
            # genie answer lands at once: the window shows both steps with ONE refresh
            own_hold = self.__dict__.get("_refresh_hold") is None
            if own_hold:
                self._refresh_hold = []
            try:
                link = names[0] if names else ""
                if n and n > 0:
                    self._mark_modeless_reason([doc], "replace")
                    if doc is self.doc:
                        self._refresh()
                        self._update_actions()
                    self._update_title()
                    self._template_message(doc, link, True)
                    top = (getattr(doc, "_undo", None) or [None])[-1]
                    from ..names import template_name

                    self._note_auto_change(doc, "template %s re-applied" % template_name(link), top)
                elif n is None or n < 0:
                    self._template_message(doc, link, False)
                if ui_state(doc).steps_after_template:
                    # (the open's symbol, placeholder genie and genie steps, which waited for
                    # it: CtDraw's load order, tabs._open_steps_after_template)
                    self._open_steps_after_template(doc)
                elif not doc.read_only and app_settings().value("opt/auto_update_genies", True, type=bool):
                    self._update_stale_genies(doc, first=doc is self.doc)
            finally:
                held = self.__dict__.pop("_refresh_hold", None) if own_hold else None
            if held and doc is self.doc:
                units = []
                for h in held:
                    units.extend(h[2])
                self._refresh(
                    keep_sel=all(h[0] for h in held), rebuild_tree=any(h[1] for h in held), units=units
                )
                self._update_actions()
            return
        if job is not None and getattr(job, "bindings", None) is not None:
            # a genie dialog's OK that ran in the worker
            if n and n > 0:
                self._mark_modeless_reason([doc], "replace")
                if doc is self.doc:
                    self._refresh()
                    self._update_actions()
                self._update_title()
                notify(self, "Updated the parameters of %s." % names[0], "success")
            elif n is None or n < 0:
                notify(
                    self,
                    "Could not apply the parameters of %s. The genie keeps its old values." % names[0],
                    "error",
                    actions=[open_log_action()],
                )
            return
        if n is None or n < 0:
            # the next open / tab switch tries again
            ui_state(doc).genie_sources_done.clear()
            if doc is self.doc:
                text = (
                    "Could not update %s in this element. Opening it again tries again."
                    if isinstance(doc, ElementDocument)
                    else "Could not update %s on this page. Opening the page again tries again."
                )
                notify(
                    self,
                    text % ", ".join(names[:3]),
                    "error",
                    actions=[open_log_action()],
                    key="genies:%s" % getattr(doc, "path", ""),
                )
            return
        if stepped and self._fold_into_save_step(doc, why, below):
            # (said with the save's step: no line, no second entry in the bar, one undo step)
            self._clear_auto_update_line(doc)
            if n:
                self._mark_modeless_reason([doc], "replace")
                if doc is self.doc:
                    self._refresh()
            if doc is self.doc:
                self._update_actions()
            self._update_title()
            return
        if stepped and why in ("save", "follow"):
            top = (getattr(doc, "_undo", None) or [None])[-1]
            with contextlib.suppress(AttributeError):
                top.genie_save_step = True
        if not n:
            # nothing changed: the 'Updating ... (you can go on working)' line goes
            self._clear_auto_update_line(doc)
            if getattr(job, "changed_on_disk", False):
                self._nested_job_refused(doc)
                return
            if stepped:
                # (no genie changed, but the page's link table took the genies' current
                # stamps: one undo step, the page unsaved; it is an automatic change, never
                # one of the user's, for the bar's Undo, the close question and the chain)
                self._update_title()
                if doc is self.doc:
                    self._update_actions()
                top = (getattr(doc, "_undo", None) or [None])[-1]
                # (the end of the run on a tab that took the save, a step of the user's in
                # between: only the stamps the run wrote, named as the save's update, not as
                # a genie the user never saved)
                what = self._save_step_entry(doc) if quiet and why == "follow" else None
                self._note_auto_change(
                    doc,
                    what
                    or "%s updated from %s library"
                    % (
                        ", ".join(names[:3]) + (" …" if len(names) > 3 else ""),
                        "its" if len(names) == 1 else "their",
                    ),
                    top,
                )
            return
        self._mark_modeless_reason([doc], "replace")
        if doc is self.doc:
            self._refresh()
            self._update_actions()
            self._genies_updated_message(doc, names)
        self._update_title()
        top = (getattr(doc, "_undo", None) or [None])[-1]
        self._note_auto_change(
            doc,
            "%s updated from %s library"
            % (
                ", ".join(names[:3]) + (" …" if len(names) > 3 else ""),
                "its" if len(names) == 1 else "their",
            ),
            top,
        )

    @staticmethod
    def _fold_into_save_step(doc, why: str, below) -> bool:
        """Fold an update into the save step that caused it."""
        if why != "follow" or below is None or not getattr(below, "genie_save_step", False):
            return False
        undo = getattr(doc, "_undo", None) or []
        if len(undo) < 2 or undo[-2] is not below:
            return False
        if not any(c is below for _w, c in ui_state(doc).auto_changes or ()):
            return False  # (saved since, or undone and made again)
        top = undo[-1]
        with contextlib.suppress(AttributeError):
            top.automatic = True
        if not doc.fold_last_step():
            return False
        doc.ui.auto_count = int(getattr(doc.ui, "auto_count", 0) or 0) + 1
        return True

    def _propagate_symbol(self, doc) -> None:
        """A symbol was saved: update open pages that place it."""
        from .library import _page_tab

        low = {(doc.library_name.lower(), doc.element_name.lower())}
        updated = pages = elems = 0
        changed = []
        for w in type(self)._instances or [self]:
            with contextlib.suppress(RuntimeError):  # a window being destroyed
                w._open_pending_placing(doc.element_name)
            for e in list(getattr(w, "_docs", ())):
                d = e["doc"]
                if d is doc or isinstance(d, _PendingPage):
                    continue
                elem = isinstance(d, ElementDocument)
                if not elem and not _page_tab(d):
                    # (a read-only page tab is left, as its open re-derives nothing:
                    # _tab_takes_symbols)
                    continue
                try:
                    if elem and not w._tab_takes_symbols(d):
                        continue
                    if ui_state(d).steps_after_template:
                        # (its open's template step is in the worker: its symbols follow it,
                        # as CtDraw's load meets the template first)
                        continue
                    path = d.library_path(doc.library_name, doc.element_name, "ctl")
                    if not path or os.path.normcase(os.path.abspath(path)) != os.path.normcase(
                        os.path.abspath(doc.path)
                    ):
                        continue
                    # (an element tab: the symbols it places directly; the copies inside the
                    # genies nested in it are composed by _queue_open_genie_updates. A page
                    # remembers the version below, when it took it)
                    n = w._placed_symbol_step(d, low, remember=elem)
                except Exception:  # noqa: BLE001 -- never let a page block the save
                    continue
                took = bool(n or getattr(d, "symbol_stamps_updated", False))
                if took and not elem:
                    # (the version it took: an update undone on the page is not made again
                    # by a later automatic one, _update_placed_symbols)
                    w._remember_symbol_versions(d, low)
                if took:
                    changed.append((w, d))
                if n:
                    with contextlib.suppress(RuntimeError):
                        w._mark_modeless_reason([d], "replace")
                updated += n
                if elem:
                    elems += took
                else:
                    pages += took
        # (a page or element shown in another window is drawn again now, with its title)
        self._follow_tabs_changed(changed)
        self._update_title()
        if pages or elems:
            from ..names import plural
            from ..notify import notify

            where = " and ".join(
                x
                for x in (
                    plural(pages, "open page") if pages else "",
                    plural(elems, "open library element") if elems else "",
                )
                if x
            )
            notify(
                self,
                "Updated %d placed %s on %s. Save %s to keep the change%s."
                % (
                    updated,
                    doc.display_name,
                    where,
                    "it" if pages + elems == 1 else "them",
                    "; Tools > Update Pages updates the others" if pages else "",
                ),
                "success",
            )

    def _library_cache_notice(self, warn: str) -> None:
        """CtDraw's in-memory element cache caveat (Studio always runs a
        Graphics Builder): once per session (this editor process, every window) as a
        NON-blocking notice, after that on the status bar only. While a run of several
        steps goes on in any window (_cache_notice_held) it waits for that run's end."""
        if not warn:
            return
        if CACHE_NOTICE_RUN["holds"]:
            # (a run of several steps is going on: said once when it has ended)
            if not CACHE_NOTICE_RUN["waiting"]:
                CACHE_NOTICE_RUN["waiting"] = warn
            return
        self.statusBar().showMessage("Saved. Restart the Graphics Builder to see this change there.", 8000)
        if CACHE_NOTICE_RUN["shown"]:
            return
        CACHE_NOTICE_RUN["shown"] = True
        self._show_cache_notice_box(warn)

    def _show_cache_notice_box(self, warn: str) -> None:
        """The non-blocking box of _library_cache_notice (a seam: tests count it)."""
        box = QtWidgets.QMessageBox(
            QtWidgets.QMessageBox.Information, "Graphics Builder", warn, QtWidgets.QMessageBox.Ok, self
        )
        box.setModal(False)
        box.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        box.show()

    @contextlib.contextmanager
    def _cache_notice_held(self):
        """Hold CtDraw's cache notice until a multi-step run is done."""
        CACHE_NOTICE_RUN["holds"] += 1
        try:
            yield
        finally:
            CACHE_NOTICE_RUN["holds"] -= 1
            if not CACHE_NOTICE_RUN["holds"]:
                warn = CACHE_NOTICE_RUN["waiting"]
                CACHE_NOTICE_RUN["waiting"] = ""
                if warn:
                    with contextlib.suppress(RuntimeError):  # (the window has gone)
                        self._library_cache_notice(warn)

    def _after_library_write(self, lib_path: str, *more: str) -> None:
        """Libraries changed (``lib_path`` and ``more``): the pages open in the other tabs
        of every editor window re-render on their next activation (their genies / symbols
        show the new art), the Library panels re-list (new elements, new thumbnails), the
        catalog of each library's folder is looked at again."""
        paths = [p for p in (lib_path,) + more if p]
        for w in type(self)._instances or [self]:
            try:
                shown, entries = w.doc, list(w._docs)
            except RuntimeError:  # swallow-ok: a window being destroyed
                continue
            for e in entries:
                if e["doc"] is not shown:
                    e["stale"] = True
            with contextlib.suppress(Exception):
                proj = (
                    os.path.dirname(os.path.abspath(shown.path))
                    if shown is not None and getattr(shown, "path", "")
                    else os.path.dirname(os.path.abspath(paths[0]))
                )
                w.library_dock.set_project_dir(proj)
                w.library_dock.refresh()
        dirs = []
        for p in paths:
            d = os.path.dirname(os.path.abspath(p))
            if os.path.normcase(d) not in {os.path.normcase(x) for x in dirs}:
                dirs.append(d)
        with contextlib.suppress(Exception):
            from ...io.catalog import catalog_for

            cat = catalog_for()
            for d in dirs:
                cat.touch(d)
        # (the open Used By windows read their users again)
        with contextlib.suppress(RuntimeError):
            self._follow_used_by_windows()

    def _confirm_replace_new(self, errors=None) -> str:
        """The first save of a new page whose file exists now: 'replace' | 'saved' (saved
        under another name) | 'cancel' (the default)."""
        name = os.path.basename(self.doc.path)
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setWindowTitle("Page Already Exists")
        box.setText("%s already exists. It was saved after this new page was started here." % name)
        box.setInformativeText(
            "Replacing it loses the page that is on disk now. Save this page under another name instead?"
        )
        copy_btn = box.addButton("Save As…", QtWidgets.QMessageBox.AcceptRole)
        over = box.addButton("Replace", QtWidgets.QMessageBox.DestructiveRole)
        cancel = box.addButton(QtWidgets.QMessageBox.Cancel)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is over:
            return "replace"
        if clicked is copy_btn:
            return "saved" if self.save_as() else "cancel"
        if errors is not None:
            errors.append(
                "%s was not saved: a page of that name was saved after it was "
                "started, and you chose not to replace it." % name
            )
        return "cancel"

    def _save_to(self, path=None, errors=None) -> bool:
        """Save, and report anything the save couldn't do."""
        if isinstance(self.doc, ElementDocument):
            return self._save_element_to(path, errors=errors)
        in_place = path is None or os.path.normcase(os.path.abspath(path)) == os.path.normcase(
            os.path.abspath(self.doc.path)
        )
        if path is None and self._version_refused(self.doc, errors):
            return False
        # (a page file the chain after a genie save wrote from the version this tab holds is
        # the tab's own: no question, library._take_chain_write)
        if in_place and self.doc.changed_on_disk() and not self._take_chain_write(self.doc, look_again=False):
            box = QtWidgets.QMessageBox(
                QtWidgets.QMessageBox.Warning,
                "Page Changed on Disk",
                "%s was changed on disk since it was opened here.\n\nOverwrite it "
                "with this version?" % os.path.basename(self.doc.path),
                parent=self,
            )
            over = box.addButton("Overwrite", QtWidgets.QMessageBox.AcceptRole)
            box.addButton(QtWidgets.QMessageBox.Cancel)
            box.setDefaultButton(QtWidgets.QMessageBox.Cancel)
            box.exec()
            if box.clickedButton() is not over:
                if errors is not None:
                    errors.append(
                        "%s was not saved: it changed on disk since it was "
                        "opened, and you chose not to overwrite it." % os.path.basename(self.doc.path)
                    )
                return False
        if in_place and getattr(self.doc, "_disk_stamp", 0) is None and os.path.exists(self.doc.path):
            # a new page (not on disk when it was made here) whose file was made since, by
            # another tab or program: its first save does not replace that page unasked
            r = self._confirm_replace_new(errors)
            if r != "replace":
                return r == "saved"  # saved under another name / cancelled
        if in_place and self.doc.damage:
            r = self._confirm_damaged_save(errors)
            if r != "overwrite":
                return r == "saved"  # saved as a copy / cancelled
        target = path or self.doc.path
        if not self._confirm_builder_save(target, errors):
            return False
        with contextlib.suppress(Exception):
            self._render_thumbnail()
        try:
            self.doc.save(path)
        except Exception as e:  # noqa: BLE001 -- a refused save must never escape
            return self._save_failed(path or self.doc.path, e, errors, offer_save_as=in_place)
        from ..recovery import AutosaveController

        AutosaveController.forget(self.doc)
        self._watch(self.doc.path)  # a replace drops the watch
        warn = getattr(self.doc, "save_warning", "")
        if warn:
            if errors is not None:
                errors.append("%s: %s" % (os.path.basename(self.doc.path), warn))
            else:
                QtWidgets.QMessageBox.warning(self, "Save", warn)
        self._refresh_runtime_page()
        with contextlib.suppress(Exception):  # page browser listings
            from ...io.catalog import catalog_for

            catalog_for().touch(os.path.dirname(os.path.abspath(self.doc.path)))
        with contextlib.suppress(RuntimeError):  # (the Used By windows: the page's status)
            self._follow_used_by_windows()
        with contextlib.suppress(Exception):  # a report only: never fails a save
            self._after_save_checks(errors)
        return True


#: the files a drop from Explorer opens: pages, and libraries (their elements in the browser)
PAGE_SUFFIXES = (".ctg",)
LIBRARY_SUFFIXES = (".ctl", ".ctm", ".ctt", ".ctc")


def dropped_documents(mime) -> tuple:
    """(pages, libraries): the local .ctg files and library files of a drag."""
    pages, libs = [], []
    if mime is None or not mime.hasUrls():
        return pages, libs
    for url in mime.urls():
        if not url.isLocalFile():
            continue
        p = os.path.normpath(url.toLocalFile())
        ext = os.path.splitext(p)[1].lower()
        if ext in PAGE_SUFFIXES:
            pages.append(p)
        elif ext in LIBRARY_SUFFIXES and os.path.isfile(p):
            libs.append(p)
    return pages, libs


def _hook_window(view):
    w = view.window()
    return w if hasattr(w, "open_dropped") else None


def _hook_accept(view, event, scene_pos, unit):
    """canvas_hooks drop handler: a page or library dragged over the canvas (the hint)."""
    pages, libs = dropped_documents(event.mimeData())
    w = _hook_window(view) if pages or libs else None
    if w is None:
        return None
    w._show_drop_frame(pages, libs)
    return w.drop_hint(pages, libs)


def _hook_drop(view, event, scene_pos, unit) -> bool:
    """canvas_hooks drop handler: it opens (instead of the canvas's picture import)."""
    pages, libs = dropped_documents(event.mimeData())
    w = _hook_window(view) if pages or libs else None
    if w is None:
        return False
    w._hide_drop_frame()
    QtCore.QTimer.singleShot(0, w, lambda: w.open_dropped(pages, libs))
    return True


class _DropFrame(QtWidgets.QWidget):
    """The dashed accent frame over the canvas while a page dragged from Explorer is over it
    (with what a drop does)."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(QtCore.Qt.WA_NoSystemBackground, True)
        self.text = ""
        self.hide()

    def show_over(self, text: str) -> None:
        self.text = text
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self.update()

    def paintEvent(self, _e) -> None:
        from ..style.theme import COLORS

        p = QtGui.QPainter(self)
        accent = QtGui.QColor(COLORS["accent"])
        pen = QtGui.QPen(accent, 2, QtCore.Qt.DashLine)
        p.setPen(pen)
        fill = QtGui.QColor(accent)
        fill.setAlpha(28)
        p.setBrush(fill)
        p.drawRoundedRect(QtCore.QRectF(self.rect()).adjusted(4, 4, -4, -4), 8, 8)
        if self.text:
            from ..style.theme import font as _font

            f = _font("subtitle")  # (the type scale's, as the other headings)
            p.setFont(f)
            fm = QtGui.QFontMetrics(f)
            w = fm.horizontalAdvance(self.text) + 32
            h = fm.height() + 16
            r = QtCore.QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(COLORS["popup"]))
            p.drawRoundedRect(r, 6, 6)
            p.setPen(QtGui.QColor(COLORS["text"]))
            p.drawText(r, QtCore.Qt.AlignCenter, self.text)
        p.end()


class _PageDropFilter(QtCore.QObject):
    """On the canvas a page or library dragged from Explorer opens instead of being imported
    (pictures still import). ``full``: the filter takes the drag itself (no canvas hooks);
    else it only hides the frame when the drag leaves (the hook takes the drag)."""

    def __init__(self, win, full: bool):
        super().__init__(win)
        self._win = win
        self.full = full

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802 -- Qt naming
        t = ev.type()
        if t == QtCore.QEvent.DragLeave:
            self._win._hide_drop_frame()
            return False
        if t not in (QtCore.QEvent.DragEnter, QtCore.QEvent.DragMove, QtCore.QEvent.Drop):
            return False
        if self._win._tab_drag(ev):
            # a page tab dragged over the canvas: the window's own drop rule (another
            # window's tab moves here; its own stays)
            if t == QtCore.QEvent.Drop:
                self._win.dropEvent(ev)
            else:
                ev.setDropAction(QtCore.Qt.MoveAction)
                ev.accept()
            return True
        pages, libs = dropped_documents(ev.mimeData())
        if not (pages or libs):
            if t == QtCore.QEvent.Drop:
                self._win._hide_drop_frame()
            return False
        if t == QtCore.QEvent.Drop:
            self._win._hide_drop_frame()
            if not self.full:
                return False
            ev.setDropAction(QtCore.Qt.CopyAction)
            ev.accept()
            QtCore.QTimer.singleShot(
                0, self, self._win, lambda p=pages, lb=libs: self._win.open_dropped(p, lb)
            )
            return True
        if not self.full:
            return False
        ev.setDropAction(QtCore.Qt.CopyAction)
        ev.accept()
        self._win._show_drop_frame(pages, libs)
        return True


def _mw():
    """ctgedit.ui.main_window (MainWindow is defined there, after this module is imported)."""
    from .. import main_window

    return main_window
