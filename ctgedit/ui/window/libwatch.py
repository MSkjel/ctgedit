"""Notice when another program saves a library the open pages use."""

from __future__ import annotations

from ...infra.paths import path_key

from ..prefs.settings import option

from ..names import name_list

from ..main_window_base import (
    QtCore,
    app_settings,
    contextlib,
    editing_mod,
    os,
)
from ..main_window_base import PendingPage
from ...infra import caches as _caches
from ...infra import logsetup

#: the library files the windows acted on: normcase path -> (mtime_ns, size) then (two
#: windows watch the same folders: the second one's notice of a change is nothing new)
_SEEN_CACHE: dict = {}
_caches.register("libwatch.seen", _SEEN_CACHE, on=("refresh", "tests"))

#: normcase path -> path of the library files that changed while the chain after a genie
#: save ran in its helper process (UpdatePagesRun.users_busy): looked at when it has ended
_HELD: dict = {}
_caches.register("libwatch.held", _HELD, on=("tests",))


def _chain_running() -> bool:
    from ..jobs.update_run import UpdatePagesRun

    return bool(UpdatePagesRun.users_busy)


def _chain_holds(wait: bool) -> bool:
    """Hold library watch changes until the running chain ends."""
    from ..jobs.update_run import UpdatePagesRun

    if not UpdatePagesRun.users_busy:
        return False
    return bool(wait or UpdatePagesRun.tabs_following)


def redraw_from_libraries(doc) -> None:
    """``doc`` drawn again from the library files as they are now, its records as they are
    (EditableDocument.rerender_for_libraries: no edit, so a Simulate tab that follows it and
    took the library change itself does not reload it again)."""
    fn = getattr(doc, "rerender_for_libraries", None)
    if fn is None:
        fn = doc._rerender_page
    fn()


def chain_saves_settled(libraries, first=None) -> list:
    """The chain after a genie save ended; its writes are the editor's own."""
    from ...format.fileio import note_written, written_stamp

    own = set()
    for p in libraries or ():
        if not p:
            continue
        k = path_key(p)
        own.add(k)
        st = _stamp(p)
        if st is not None and written_stamp(p) == st:
            # (the open tabs and Simulate have this version already)
            _SEEN_CACHE[k] = st
            continue
        note_written(p)
        _SEEN_CACHE[k] = _stamp(p)
    held = [p for k, p in sorted(_HELD.items()) if k not in own]
    _HELD.clear()
    changed = []
    for p in held:
        k = path_key(p)
        st = _stamp(p)
        if st is not None and _SEEN_CACHE.get(k) == st:
            continue
        _SEEN_CACHE[k] = st
        if st is not None and written_stamp(p) == st:
            continue  # (written by this process: propagated)
        changed.append(p)
    if not changed:
        return []
    return libraries_changed_on_disk(changed, first=first)


def _stamp(path: str):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _opened_stamps(doc) -> dict | None:
    """{normcase library path: its version (fileio.file_version)} the document's drawing was
    derived from (its derivation's resolver), None when not known."""
    der = getattr(doc, "_derivation", None)
    res = getattr(der, "resolver", None) if der is not None else None
    opened = getattr(res, "opened", None) if res is not None else None
    return dict(opened) if isinstance(opened, dict) else None


def _affected(doc, keys: set) -> bool:
    """The document read one of the changed files (``keys``), and not as it is now; or a
    changed file is a library of the same name as one it asked for (a copy put in a
    nearer project shadows the one read: the derive compares what each name finds)."""
    from ...format.fileio import file_version

    opened = _opened_stamps(doc)
    if opened is None:
        return True
    for k in keys:
        st = opened.get(k)
        if st is None:
            continue
        try:
            now = file_version(k)
        except OSError:
            now = None
        if now != st:
            return True
    der = getattr(doc, "_derivation", None)
    found = getattr(getattr(der, "resolver", None), "found", None)
    asked = {n.lower() for n in found} if isinstance(found, dict) else set()
    asked |= {os.path.basename(p).lower() for p in opened}
    return any(os.path.basename(k).lower() in asked for k in keys)


def libraries_changed_on_disk(paths, first=None, during: str = "") -> list:
    """Another program changed library files: redraw and update what uses them."""
    from ...format.fileio import note_changed
    from ..main_window import MainWindow

    keys = {path_key(p) for p in paths}
    for p in paths:
        # (a version of the file the caches have not seen, even when its (mtime, size) is
        # the one they read)
        note_changed(p)
    editing_mod.libraries_changed()
    gen = editing_mod.LIBRARY_GENERATION[0]
    auto = option("auto_update_genies")
    done, took, waiting = [], [], []
    wins = [w for w in MainWindow.editor_windows() if w is first] + [
        w for w in MainWindow.editor_windows() if w is not first
    ]
    for w in wins:
        try:
            entries = list(w._docs)
            shown = w.doc
        except RuntimeError:  # swallow-ok: a window being destroyed
            continue
        order = [e for e in entries if e["doc"] is shown] + [e for e in entries if e["doc"] is not shown]
        for e in order:
            doc = e["doc"]
            if isinstance(doc, PendingPage):
                continue
            if auto:
                e["genie_gen"] = gen
            if _affected(doc, keys):
                # its drawing read a changed file: drawn again from the files as they are
                if doc is shown and not w._may_change_now(doc, redo=False):
                    # (the user is in the middle of something: drawn again and brought up
                    # to date when they have finished, and not said to be drawn again now)
                    w._wait_until_idle(doc, redraw=True)
                    waiting.append(doc)
                    continue
                done.append(doc)
                if doc is shown:
                    try:
                        redraw_from_libraries(doc)
                        w._refresh()
                        w._update_diag_chips()
                        w.view.viewport().update()
                    except Exception:  # noqa: BLE001 -- keep showing what we have
                        logsetup.note_exception("ui.window.libwatch.rerender", page=doc.path)
                else:
                    e["stale"] = True  # drawn again when its tab is shown
            # the library symbols placed in it whose element changed are re-derived (Update
            # changed symbols: its drawing holds their copies), then its genies (and the genies
            # nested in them) that changed are composed again (Update changed genies): the link table
            # says which (genie_changes), a worker composes them; a file the running update
            # wrote follows the save, as the run's end does (files._updates_for 'follow': a
            # tab that took the save gets no amber marks, its step becomes part of the save's)
            with (
                contextlib.suppress(Exception),
                w._updates_for("follow") if during else contextlib.nullcontext(),
            ):
                if w._bring_up_to_date(doc, first=doc is shown):
                    took.append(doc)
        with contextlib.suppress(Exception):
            w.library_dock.set_project_dir(w.library_dock.project_dir())
    # (the open Used By windows read their users again, as after a save here)
    for w in wins[:1]:
        with contextlib.suppress(RuntimeError):
            w._follow_used_by_windows()
    if done or took or waiting:
        names = sorted({os.path.basename(p) for p in paths})
        files = name_list(names, 3)
        if during:
            text = "%s changed during the update of what uses %s: %s." % (
                files,
                during,
                _watch_result(done, took, waiting),
            )
        else:
            text = "%s %s changed by another program: %s." % (
                files,
                "was" if len(names) == 1 else "were",
                _watch_result(done, took, waiting),
            )
        logsetup.get("open").info("library watch: %s", text)
        for w in wins:
            with contextlib.suppress(RuntimeError):
                w.statusBar().showMessage(text, 6000)
            break
    return done


def _watch_result(drawn, took, waiting=()) -> str:
    """What the library watch did, for its status line."""
    d, t = len(drawn), len(took)
    if waiting:
        later = (
            "the page shown is updated" if len(waiting) == 1 else "the pages shown are updated"
        ) + " when you have finished"
        if not d and not t:
            return later
        other = "1 other page was" if d == 1 else "%d other pages were" % d
        if not t:
            return "%s drawn again, and %s" % (other, later)
        syms = "the symbols on %s updated" % ("1 other page were" if t == 1 else "%d other pages were" % t)
        first = "%s drawn again and %s" % (other, syms) if d else syms
        return "%s, and %s" % (first, later)
    pages = "the page was" if d == 1 else "%d pages were" % d
    if not t:
        return "%s drawn again" % pages
    if d == 1 and t == 1 and drawn[0] is took[0]:
        return "the page was drawn again and its symbols were updated"
    syms = "the symbols on %s updated" % ("the page were" if t == 1 else "%d pages were" % t)
    return "%s drawn again and %s" % (pages, syms) if d else syms


class LibraryWatch:
    """MainWindow mixin: the library folder watch and the window-activation re-checks."""

    def _install_library_watch(self) -> None:
        self._lib_watch = None
        self._lib_pending: set = set()
        self._lib_timer = QtCore.QTimer(self)
        self._lib_timer.setSingleShot(True)
        self._lib_timer.timeout.connect(self._check_library_changes)
        self._apply_library_watch()

    # -- settings ------------------------------------------------------------------------
    @staticmethod
    def _library_watch_on() -> bool:
        return bool(option("library_watch"))

    def _apply_library_watch(self) -> None:
        """Settings > Pages and libraries > Files and folders changed (the apply hook): the
        watch starts, stops or waits as long as set."""
        timer = getattr(self, "_lib_timer", None)
        if timer is None:
            return
        try:
            delay = int(app_settings().value("opt/library_watch_delay_ms", 1000, type=int))
        except TypeError, ValueError:  # swallow-ok: a garbled setting reads as its default
            delay = 1000
        timer.setInterval(max(100, delay))
        if not self._library_watch_on():
            self._close_library_watch()
            return
        self._sync_library_watch()

    def _close_library_watch(self) -> None:
        w = getattr(self, "_lib_watch", None)
        self._lib_watch = None
        if w is not None:
            with contextlib.suppress(RuntimeError):
                w.close()

    # -- what is watched -----------------------------------------------------------------
    def _library_folders(self) -> list:
        """The library folders of every open document: the search paths of its drawing's
        resolver (the page's project, the extra library folders, its include chain)."""
        out, seen = [], set()
        for e in list(getattr(self, "_docs", ())):
            doc = e["doc"]
            if isinstance(doc, PendingPage) or not getattr(doc, "path", ""):
                continue
            der = getattr(doc, "_derivation", None)
            res = getattr(der, "resolver", None) if der is not None else None
            paths = list(getattr(res, "search_paths", None) or ()) if res is not None else []
            if not paths:
                try:
                    from ...format.symbols import SymbolResolver

                    paths = list(SymbolResolver.for_page(doc.path).search_paths)
                except Exception:  # noqa: BLE001
                    logsetup.note_exception("ui.window.libwatch.folders", page=doc.path)
                    paths = [os.path.dirname(os.path.abspath(doc.path))]
            for p in paths:
                k = path_key(p)
                if k not in seen and os.path.isdir(p):
                    seen.add(k)
                    out.append(os.path.abspath(p))
        return out

    def _sync_library_watch(self) -> None:
        """Watch the library folders of the documents open now (a new watch when they
        changed: a page opened or closed)."""
        if getattr(self, "_lib_timer", None) is None or not self._library_watch_on():
            return
        want = self._library_folders()
        have = getattr(self._lib_watch, "folders", None) if self._lib_watch is not None else None
        if have is not None and {path_key(p) for p in have} == {path_key(p) for p in want}:
            return
        self._close_library_watch()
        if not want:
            return
        from ..simulate.live_reload import LIBRARY_EXTS, FolderWatch

        w = FolderWatch(want, self, exts=LIBRARY_EXTS)
        w.changed.connect(self._on_library_files_changed)
        self._lib_watch = w

    def _library_watch_rescan(self) -> None:
        """Compare the watched folders with what they held (a tab shown, the window
        activated: a change notice can be missed while the user is in another program)."""
        w = getattr(self, "_lib_watch", None)
        if w is not None:
            with contextlib.suppress(RuntimeError):
                w.rescan()

    # -- changes -------------------------------------------------------------------------
    def _on_library_files_changed(self, paths) -> None:
        self._lib_pending.update(paths)
        self._lib_timer.start()

    def _check_library_changes(self) -> list:
        """Handle the library files that changed, once changes settle."""
        from ...format.fileio import note_written, written_stamp

        paths, self._lib_pending = self._lib_pending, set()
        if _chain_holds(self._tabs_wait_for_runs()):
            for p in paths:
                _HELD[path_key(p)] = p
            return []
        during = ""
        if _chain_running():
            from ..jobs.update_run import UpdatePagesRun

            during = UpdatePagesRun.users_busy
        changed = []
        for p in sorted(paths):
            k = path_key(p)
            st = _stamp(p)
            if st is not None and _SEEN_CACHE.get(k) == st:
                continue
            _SEEN_CACHE[k] = st
            if st is not None and written_stamp(p) == st:
                continue  # this process wrote it: its save propagated
            changed.append(p)
            if during and st is not None:
                # (taken now: the editor's own from here on, so the Simulate window leaves
                # it to the editor's generation and the chain's end does not count it a
                # second time, chain_saves_settled)
                note_written(p)
        if not changed:
            return []
        return libraries_changed_on_disk(changed, first=self, during=during)

    # -- the window is activated ---------------------------------------------------------
    def changeEvent(self, ev):  # noqa: N802 -- Qt naming
        super().changeEvent(ev)
        if ev.type() == QtCore.QEvent.ActivationChange and self.isActiveWindow():
            with contextlib.suppress(Exception):
                self._window_activated()

    def _window_activated(self) -> None:
        """Back from another program: the libraries, the tag tables and the include chain
        may have changed meanwhile. The watch compares its folders, and the Tags and Library
        panels re-check their sources (no-ops while nothing changed)."""
        self._sync_library_watch()
        self._library_watch_rescan()
        doc = getattr(self, "doc", None)
        path = getattr(doc, "path", "") if doc is not None else ""
        if not path:
            return
        proj_dir = os.path.dirname(os.path.abspath(path))
        with contextlib.suppress(Exception):
            self.library_dock.set_project_dir(proj_dir)
        with contextlib.suppress(Exception):
            self.tags_dock.set_project_dir(proj_dir)
