"""Opening pages, elements and dialogs; the tab lookups; the diagnostics commands."""

from __future__ import annotations

from ...infra.paths import path_key

from ..main_window_base import editor_windows

from ..main_window_base import (
    ElementDocument,
    Page,
    QtCore,
    QtWidgets,
    contextlib,
    os,
)
from ..messages import inform


class OpenCommands:
    """Opening pages, library elements and the File dialogs, the lookups of the tabs that
    show them, and Help's Open Log Folder and Copy Diagnostics."""

    # ------------------------------------------------ diagnostics
    def _open_log_folder(self) -> None:
        from ... import crashlog

        from ..messages import open_folder

        open_folder(crashlog.log_dir())

    def _copy_diagnostics(self) -> str:
        from ...infra import logsetup

        docs = [e["doc"] for w in editor_windows() for e in getattr(w, "_docs", [])]
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
            inform(
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
        wins = [self] + [w for w in list(type(self).instances) if w is not self]
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
            from .element_users import warm_element_users

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
            and path_key(e["doc"].path) == path_key(lib_path)
        )

    def _tabs_of(self, kind: str, path: str, element: str = "") -> list:
        """Indices of the open tabs showing page ``path`` (kind 'page') or
        library element ``path``/``element`` (a restored tab not shown yet too)."""
        from ..dialog.history_versions import is_version

        key = path_key(path)
        out = []
        for k, e in enumerate(self._docs):
            d = e["doc"]
            if path_key(d.path) != key or is_version(d):
                continue
            el = self._tab_element(d)
            if kind == "page" and not el:
                out.append(k)
            elif kind != "page" and el and el.lower() == element.lower():
                out.append(k)
        return out

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
