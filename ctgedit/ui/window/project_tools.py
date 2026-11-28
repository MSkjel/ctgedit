"""Rename Pages, Check Project, and deleting or renaming pages and library elements."""

from __future__ import annotations

from ...infra.paths import folder_key, path_key

from .open_tabs import SharedState


from ..main_window_base import (
    QtCore,
    contextlib,
    os,
)


class ProjectTools:
    """The project-wide tools of the window and the deletes and renames that close tabs first."""

    def _before_delete_or_rename(self, kind: str, path: str, element: str = ""):
        """A page / element is about to be deleted or renamed: its tabs with unsaved edits
        are asked about first (False cancels). Returns the step that closes its tabs (their
        Save would write it back), for after the delete or rename succeeded: one that fails
        leaves the tabs and their edits as they are."""
        open_in = [(self, self._tabs_of(kind, path, element))]
        for w in type(self).instances:
            if w is not self:
                with contextlib.suppress(RuntimeError):  # a window being destroyed
                    open_in.append((w, w._tabs_of(kind, path, element)))
        if any(w._docs[k]["doc"].dirty for w, tabs in open_in for k in tabs):
            what = element if kind != "page" else os.path.basename(path)
            from ..messages import confirm

            if not confirm(
                self,
                "Unsaved Changes",
                "%s is open here with unsaved changes. Continuing closes it and discards them." % what,
                "Discard",
            ):
                return False

        def close() -> None:
            for w in [self] + [x for x in type(self).instances if x is not self]:
                with contextlib.suppress(RuntimeError):  # a window being destroyed
                    for k in sorted(w._tabs_of(kind, path, element), reverse=True):
                        w._docs[k]["doc"].dirty = False
                        w._close_doc_tab(k)
                    # the library / page lists refresh once the change is on disk
                    QtCore.QTimer.singleShot(0, w, w._after_lifecycle_change)

        return close

    def _open_tool_window(self, attr: str):
        """The modeless tool window kept in ``attr``, while it is open."""
        from ..modeless import alive

        dlg = getattr(self, attr, None)
        return dlg if alive(dlg) and dlg.isVisible() else None

    @staticmethod
    def _raise_tool_window(dlg) -> None:
        dlg.showNormal() if dlg.isMinimized() else dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _rename_pages_dialog(self, path: str = "") -> None:
        """Tools > Rename Pages, for the project of ``path`` (or the page shown), that page ticked."""
        from ..dialog.rename_pages import RenamePagesDialog

        if not path and self.doc is not None and str(getattr(self.doc, "path", "")).lower().endswith(".ctg"):
            path = self.doc.path
        dlg = self._open_tool_window("_rename_pages_dlg")
        if dlg is not None:
            # (the open window stays: its ticks, a run and Undo Rename are kept)
            key = folder_key(path) if path else ""
            if not key or dlg.renaming() or key in (os.path.normcase(d) for d in dlg.project_dirs()):
                if key and not dlg.renaming():
                    dlg.tick([os.path.splitext(os.path.basename(path))[0]])
                self._raise_tool_window(dlg)
                return
            dlg.close()
        pdir = os.path.dirname(os.path.abspath(path)) if path else ""
        if not pdir:
            from ..prefs.settings import app_settings

            from ..pick_fields import project_dirs

            last = str(app_settings().value("updatepages/root", "") or "")
            pdir = next(iter(project_dirs(last, False)), "") if last else ""
        pages = [os.path.splitext(os.path.basename(path))[0]] if path else []
        dlg = RenamePagesDialog(
            self,
            pdir,
            pages,
            hold=self.hold_pages,
            moved=self.pages_moved,
            check_references=self._check_project_dialog,
        )
        dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        dlg.setModal(False)
        self._rename_pages_dlg = dlg
        dlg.destroyed.connect(
            lambda *_a, d=dlg: (
                setattr(self, "_rename_pages_dlg", None)
                if getattr(self, "_rename_pages_dlg", None) is d
                else None
            )
        )
        dlg.show()

    def _windows(self) -> list:
        return [self] + [w for w in type(self).instances if w is not self]

    def hold_pages(self, paths) -> None:
        """Rename Pages is moving these pages: no window saves them or watches their files
        until :meth:`pages_moved`."""
        keys = {path_key(p) for p in paths}
        SharedState.renaming.update(keys)
        for w in self._windows():
            with contextlib.suppress(RuntimeError):  # a window being destroyed
                for e in w._docs:
                    path = e["doc"].path
                    if path and path_key(path) in keys:
                        with contextlib.suppress(Exception):
                            w._watcher.removePath(path)

    def pages_moved(self, moves, held) -> None:
        """Rename Pages ended: the tabs of the pages ``moves`` [(old path, new path)] show
        their new files, as after Save As, unsaved changes kept; the ``held`` pages are saved
        and watched again."""
        from ..prefs.settings import app_settings

        new_of = {path_key(o): n for o, n in moves}
        sw = None
        with contextlib.suppress(Exception):
            from ..simulate.window import simulate_window

            sw = simulate_window(create=False)
        for w in self._windows():
            with contextlib.suppress(RuntimeError):  # a window being destroyed
                for e in w._docs:
                    doc = e["doc"]
                    new = new_of.get(path_key(doc.path)) if doc.path else None
                    if new is not None:
                        doc.path = new
                        doc._project_cache = None
                        doc._tag_formats = None
                        if sw is not None:
                            with contextlib.suppress(Exception):
                                sw.document_renamed(doc, new)
                    if doc.path and (new is not None or path_key(doc.path) in SharedState.renaming):
                        w._watch(doc.path)
                w.library_dock.refresh()
                w._update_title()
        SharedState.renaming.difference_update(path_key(p) for p in held)
        if new_of:
            st = app_settings()
            mru = [str(p) for p in st.value("mru", [], type=list)]
            st.setValue("mru", [new_of.get(path_key(p), p) for p in mru])

    def _check_project_dialog(self, project_dir: str = "", includes: bool = False, renames=None) -> None:
        """Tools > Check Project; with ``renames`` (Rename Pages) it checks at once."""
        from ..dialog.check_project import CheckProjectDialog

        if (
            not project_dir
            and self.doc is not None
            and str(getattr(self.doc, "path", "")).lower().endswith(".ctg")
        ):
            project_dir = os.path.dirname(os.path.abspath(self.doc.path))
        dlg = self._open_tool_window("_check_project_dlg")
        if dlg is not None:
            if not renames or dlg.searching():
                # (the open window stays, with its findings and a check that runs)
                self._raise_tool_window(dlg)
                return
            dlg.close()
        dlg = CheckProjectDialog(
            self,
            project_dir,
            renames=renames,
            includes=includes,
            documents=self.open_documents,
            open_page=self.open_shown,
            goto=self._find_goto,
            replaced=self._find_replaced,
        )
        dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        dlg.setModal(False)
        self._check_project_dlg = dlg
        dlg.show()
        if renames:
            dlg.check()

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
