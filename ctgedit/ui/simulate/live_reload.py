"""Live reload while simulating."""

from __future__ import annotations

from ...infra.paths import path_key
from ...infra.logsetup import note_exception as _note_exc

from ..prefs.settings import option

import contextlib
import os
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from PySide6 import QtCore

from ...format.fileio import LIBRARY_EXTS
from . import options
from . import sources as S

CICODE_EXTS = (".ci",)
PAGE_EXTS = (".ctg",)
WATCHED_EXTS = CICODE_EXTS + LIBRARY_EXTS + PAGE_EXTS
#: a .ci change is sent this long after the last change of the file (ms): the default of
#: Settings > Simulate > Send the change after (options.cicode_delay_ms)
CICODE_DEBOUNCE_MS = 500
#: a page reloads this long after the last edit (or library change) (s): the default of
#: Settings > Simulate > Reload after a pause of (options.reload_delay_s)
PAGE_DEBOUNCE_S = 1.0


def page_delay_s() -> float:
    """The pause after the last edit before the page reloads (s; the setting)."""
    return options.reload_delay_s()


def file_stamp(path: str) -> Optional[Tuple[int, int]]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def library_stamp(path: str) -> Optional[tuple]:
    """The version of a library file a page's drawing was derived from (fileio.file_version:
    ctgedit's own second write of one size within one clock tick counts), None when it
    cannot be read."""
    from ...format.fileio import file_version

    try:
        return file_version(path)
    except OSError:
        return None


def editor_genie_update_pending(doc) -> bool:
    """Is an editor genie update of ``doc`` still pending?"""
    import sys

    if doc is None:
        return False
    mw = sys.modules.get("ctgedit.ui.main_window")
    cls = getattr(mw, "MainWindow", None) if mw is not None else None
    for w in list(getattr(cls, "instances", None) or ()):
        waiting = getattr(w, "__dict__", {}).get("_after_run_docs") or ()
        if any(d is doc for d in waiting):
            return True
        # (or an update that waits for the user to finish: files._wait_until_idle)
        if any(item[0] is doc for item in getattr(w, "__dict__", {}).get("_idle_docs") or ()):
            return True
        jobs = getattr(w, "__dict__", {}).get("_genie_jobs")
        if jobs is None:
            continue
        try:
            if jobs.busy(doc):
                return True
        except RuntimeError:  # swallow-ok: a window being destroyed
            continue
    return False


def library_run_going() -> bool:
    """Is the editor running something that writes libraries one after another?"""
    from ..jobs.update_run import UpdatePagesRun as R

    return bool(R.users_busy or R.chain_busy or R.tabs_following)


def tabs_wait_for_runs() -> bool:
    """Settings > Pages and libraries > When a page opens: 'Open tabs wait for the update of
    what uses a saved genie' (opt/tabs_wait_for_runs, off by default: ui/window/tabs)."""
    return option("tabs_wait_for_runs")


def run_holds_documents() -> bool:
    """Should tabs following an editor document wait for the run to end?"""
    from ..jobs.update_run import UpdatePagesRun as R

    if R.chain_busy or R.tabs_following:
        return True
    return bool(R.users_busy) and tabs_wait_for_runs()


def editor_wrote(path: str) -> bool:
    """This process wrote ``path`` as it is on disk now (fileio.written_stamp: an editor save,
    or a write of the chain's helper the editor took as its own when the chain ended): the
    editor's library generation says it (SimulateWindow._live_tick follows it)."""
    from ...format.fileio import written_stamp

    st = file_stamp(path)
    return st is not None and written_stamp(path) == st


def _editor_windows_of(doc) -> list:
    """The editor windows that hold ``doc`` in a tab."""
    import sys

    if doc is None:
        return []
    mw = sys.modules.get("ctgedit.ui.main_window")
    cls = getattr(mw, "MainWindow", None) if mw is not None else None
    out = []
    for w in list(getattr(cls, "instances", None) or ()):
        try:
            if any(e["doc"] is doc for e in list(getattr(w, "_docs", ()))):
                out.append(w)
        except RuntimeError:  # swallow-ok: a window being destroyed
            continue
    return out


def editor_library_watches(doc, paths) -> list:
    """The editor windows holding ``doc`` whose library watch looks at the folder of one of
    ``paths`` (ui/window/libwatch, Settings > Pages and libraries: on): they compose the
    document's genies again for a library another program wrote, after their own pause."""
    folders = {path_key(os.path.dirname(os.path.abspath(p))) for p in paths or () if p}
    out = []
    for w in _editor_windows_of(doc):
        watch = getattr(w, "__dict__", {}).get("_lib_watch")
        have = getattr(watch, "folders", None) if watch is not None else None
        if have and folders & {path_key(f) for f in have}:
            out.append(w)
    return out


def editor_library_check_pending(doc) -> bool:
    """An editor window holding ``doc`` has library changes its watch has not looked at yet
    (the pause after the last change: libwatch._check_library_changes)."""
    for w in _editor_windows_of(doc):
        d = getattr(w, "__dict__", {})
        timer = d.get("_lib_timer")
        try:
            if d.get("_lib_pending") or (timer is not None and timer.isActive()):
                return True
        except RuntimeError:  # swallow-ok: a window being destroyed
            continue
    return False


def folder_snapshot(
    folder: str, exts: Tuple[str, ...] = WATCHED_EXTS, key: Callable[[str], str] = path_key
) -> Dict[str, Tuple[str, int, int]]:
    """{normcased path: (path, mtime_ns, size)} of the files of ``exts`` in ``folder`` (not its
    subfolders: the compiler reads a project's top-level .ci files); ``key`` normcases a
    path (:func:`path_key`)."""
    out: Dict[str, Tuple[str, int, int]] = {}
    try:
        with os.scandir(folder) as it:
            for e in it:
                if not e.name.lower().endswith(exts):
                    continue
                try:
                    if not e.is_file():
                        continue
                    st = e.stat()
                except OSError:
                    continue
                out[key(e.path)] = (e.path, st.st_mtime_ns, st.st_size)
    except OSError:
        pass
    return out


def is_cicode(path: str) -> bool:
    return path.lower().endswith(CICODE_EXTS)


def is_library(path: str) -> bool:
    return path.lower().endswith(LIBRARY_EXTS)


def is_page(path: str) -> bool:
    return path.lower().endswith(PAGE_EXTS)


class FolderWatch(QtCore.QObject):
    """The files of the watched kinds in some folders: ``changed`` = the paths that changed,
    were added or removed (a folder's change rescans it)."""

    changed = QtCore.Signal(list)

    def __init__(self, folders: Iterable[str], parent=None, exts: Tuple[str, ...] = WATCHED_EXTS):
        super().__init__(parent)
        self.exts = exts
        self.folders: List[str] = []
        seen: Set[str] = set()
        for f in folders:
            if f and os.path.isdir(f) and path_key(f) not in seen:
                seen.add(path_key(f))
                self.folders.append(os.path.abspath(f))
        self._snap: Dict[str, Dict[str, Tuple[str, int, int]]] = {}
        #: path -> path_key of the paths seen (a rescan compares every watched file: the
        #: tab switches and window activations rescan every folder)
        self._keys: Dict[str, str] = {}
        self._watcher = QtCore.QFileSystemWatcher(self)
        for f in self.folders:
            snap = folder_snapshot(f, self.exts, self._key)
            self._snap[path_key(f)] = snap
            self._watch(f, snap)
        self._watcher.directoryChanged.connect(self._rescan)
        self._watcher.fileChanged.connect(lambda p: self._rescan(os.path.dirname(p)))

    def _key(self, path: str) -> str:
        k = self._keys.get(path)
        if k is None:
            k = self._keys[path] = path_key(path)
        return k

    def _watch(self, folder: str, snap) -> None:
        key = self._key
        want = [folder] + [v[0] for v in snap.values()]
        have = {key(p) for p in self._watcher.files() + self._watcher.directories()}
        add = [p for p in want if key(p) not in have]
        if add:
            self._watcher.addPaths(add)

    def rescan(self, folder: Optional[str] = None) -> List[str]:
        """Compare ``folder`` (every folder when None) with what it held: the changed paths
        (also emitted)."""
        out: List[str] = []
        for f in [folder] if folder else list(self.folders):
            out += self._diff(f)
        if out:
            self.changed.emit(out)
        return out

    def _rescan(self, folder: str) -> None:
        if path_key(folder) in self._snap:
            self.rescan(folder)

    def _diff(self, folder: str) -> List[str]:
        k = self._key(folder)
        old = self._snap.get(k)
        if old is None:
            return []
        new = folder_snapshot(folder, self.exts, self._key)
        self._snap[k] = new
        out = [v[0] for p, v in new.items() if old.get(p) is None or old[p][1:] != v[1:]]
        out += [v[0] for p, v in old.items() if p not in new]
        self._watch(folder, new)
        return out

    def close(self) -> None:
        try:
            self._watcher.removePaths(self._watcher.files() + self._watcher.directories())
        except Exception:  # noqa: BLE001  # swallow-ok: closing anyway
            pass
        self.deleteLater()


class _SessionWatch:
    """What one simulation's live reload holds."""

    def __init__(self, session, watch: FolderWatch, timer: QtCore.QTimer):
        self.session = session
        self.watch = watch
        self.timer = timer
        self.pending: Set[str] = set()  # .ci files to send when the timer fires
        self.since_start: Dict[str, str] = {}  # every .ci changed since the watch began
        self.told: Set[str] = set()  # attached: the files already noted


class LiveReload(QtCore.QObject):
    """The Simulate window's live reload of Cicode (and its library-change notice).
    ``log_to(session)`` = the simulation (a tab's EngineSimulation) that shows the lines."""

    #: library files (.ctl / .ctm / .ctt) changed on disk
    libraryChanged = QtCore.Signal(list)
    #: page files (.ctg) changed on disk
    pageChanged = QtCore.Signal(list)

    def __init__(self, log_to: Callable[[object], Optional[object]], parent=None):
        super().__init__(parent)
        self._log_to = log_to
        self._watches: Dict[int, _SessionWatch] = {}
        # (a change notice can be missed while the watcher re-arms after a burst: the folders
        # are compared again now and then too; a few folder listings)
        self._sweep = QtCore.QTimer(self)
        self._sweep.setInterval(options.folder_check_ms())
        self._sweep.timeout.connect(self._sweep_all)

    def apply_settings(self) -> None:
        """Settings > Simulate changed: the folder check and each simulation's Cicode pause
        take the new times (a pause already running keeps its old one)."""
        ms = options.folder_check_ms()
        if ms != self._sweep.interval():
            self._sweep.setInterval(ms)
        ms = options.cicode_delay_ms()
        for sw in self._watches.values():
            if sw.timer.interval() != ms:
                sw.timer.setInterval(ms)

    # -- which simulations ---------------------------------------------------------------
    def sync(self, sessions: Iterable[object]) -> None:
        """Watch the unit folders of these sessions (the alive ones), stop watching the rest."""
        alive = {}
        for s in sessions:
            if s is not None and getattr(s, "alive", False):
                alive[id(s)] = s
        for k in [k for k in self._watches if k not in alive or self._watches[k].session is not alive[k]]:
            self._drop(k)
        for k, s in alive.items():
            if k not in self._watches:
                self._add(s)
        if self._watches and not self._sweep.isActive():
            self._sweep.start()
        elif not self._watches and self._sweep.isActive():
            self._sweep.stop()

    def _sweep_all(self) -> None:
        for sw in list(self._watches.values()):
            sw.watch.rescan()

    def _add(self, s) -> None:
        dirs = [d for _n, d in (getattr(s, "unit", None) or ()) if d]
        if not dirs and getattr(s, "project", ""):
            dirs = [s.project]
        watch = FolderWatch(dirs, self)
        timer = QtCore.QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(options.cicode_delay_ms())
        sw = _SessionWatch(s, watch, timer)
        watch.changed.connect(lambda paths, sw=sw: self._changed(sw, paths))
        timer.timeout.connect(lambda sw=sw: self._fire(sw))
        self._watches[id(s)] = sw

    def _drop(self, k: int) -> None:
        sw = self._watches.pop(k, None)
        if sw is None:
            return
        sw.timer.stop()
        # (the handlers hold this object: left connected, the deferred deletion of the watch
        # or the timer would delete the watch twice, an abort)
        for sig in (sw.watch.changed, sw.timer.timeout):
            with contextlib.suppress(RuntimeError, TypeError):
                sig.disconnect()
        sw.timer.deleteLater()
        sw.watch.close()

    def watch_of(self, session) -> Optional[_SessionWatch]:
        return self._watches.get(id(session)) if session is not None else None

    def close(self) -> None:
        self._sweep.stop()
        for k in list(self._watches):
            self._drop(k)

    # -- changes -------------------------------------------------------------------------
    def _changed(self, sw: _SessionWatch, paths: List[str]) -> None:
        libs = [p for p in paths if is_library(p)]
        pages = [p for p in paths if is_page(p)]
        for p in paths:
            if not is_cicode(p):
                continue
            sw.since_start[path_key(p)] = p
            if not options.get("reload_cicode"):
                continue
            if getattr(sw.session, "attached", False):
                if path_key(p) not in sw.told:
                    sw.told.add(path_key(p))
                    self._log(
                        sw.session,
                        S.LogEntry(
                            self._now(sw.session),
                            "log",
                            "%s changed. This simulation was started by another program, which "
                            "reloads its code: Reload Cicode reloads it from here." % os.path.basename(p),
                            file=p,
                        ),
                    )
                continue
            sw.pending.add(path_key(p))
            sw.timer.start()
        if libs:
            self.libraryChanged.emit(libs)
        if pages:
            self.pageChanged.emit(pages)

    def _fire(self, sw: _SessionWatch) -> None:
        files = sorted(sw.since_start.get(k, k) for k in sw.pending)
        sw.pending.clear()
        for f in files:
            self.send(sw.session, f)

    def reload_now(self, session) -> int:
        """Reload Cicode: every .ci file of the unit that changed since the simulation started
        goes to the simulator (also on a simulation another program started). The number of
        files sent."""
        sw = self.watch_of(session)
        if sw is None:
            return 0
        sw.watch.rescan()  # (a change the watcher has not reported yet)
        sw.timer.stop()
        sw.pending.clear()
        files = sorted(sw.since_start.values(), key=lambda p: p.lower())
        if not files:
            self._log(
                session,
                S.LogEntry(
                    self._now(session),
                    "log",
                    "Reload Cicode: no .ci file of the project changed since the simulation started.",
                ),
            )
            return 0
        for f in files:
            self.send(session, f)
        return len(files)

    def send(self, session, path: str) -> None:
        """ctsim/reload of one file; its compile errors go to Outputs with file and line."""
        base = os.path.basename(path)
        if not os.path.isfile(path):
            self._log(
                session,
                S.LogEntry(
                    self._now(session),
                    "log",
                    "%s was removed: its functions stay until the simulation starts again." % base,
                    file=path,
                ),
            )
            return
        from ...sim import client as C

        # the answer comes on the connection's reader thread: the simulation that shows its
        # lines is found here, on the GUI thread (the window's tabs are widgets)
        sim = self._log_to(session)

        def done(body, err):
            if err is not None:
                if not isinstance(err, C.ConnectionLost):
                    self._log(
                        session,
                        S.LogEntry(
                            self._now(session, sim),
                            "error",
                            "%s was not reloaded: %s" % (base, err),
                            level="error",
                            file=path,
                        ),
                        sim,
                    )
                return
            self._answer(session, path, body if isinstance(body, dict) else {}, sim)

        try:
            session.reload_file(path, callback=done)
        except C.CtsimError as e:
            self._log(
                session,
                S.LogEntry(
                    self._now(session),
                    "error",
                    "%s was not reloaded: %s" % (base, e),
                    level="error",
                    file=path,
                ),
            )

    def _answer(self, session, path: str, body: dict, sim=None) -> None:
        """The reload's answer (the reader thread): a compile error per line; a refusal
        without errors as one line (the simulator logs the compiled ones itself)."""
        base = os.path.basename(path)
        if body.get("compiled"):
            return
        errs = [e for e in body.get("errors") or () if isinstance(e, dict)]
        now = self._now(session, sim)
        for e in errs:
            try:
                line = int(e.get("line") or 0)
            except TypeError, ValueError:
                line = 0
            code = str(e.get("code") or "")
            msg = str(e.get("message") or "")
            self._log(
                session,
                S.LogEntry(
                    now,
                    "error",
                    ("%s %s" % (code, msg)).strip() or "compile error",
                    level="error",
                    file=path,
                    line=line,
                ),
                sim,
            )
        reason = str(body.get("reason") or "").rstrip(".")
        self._log(
            session,
            S.LogEntry(
                now,
                "error",
                "%s was not reloaded: %s." % (base, reason or "the simulation keeps its old code"),
                level="error",
                file=path,
            ),
            sim,
        )

    # -- where the lines go ------------------------------------------------------------------
    def _log(self, session, entry, sim=None) -> None:
        if not getattr(entry, "category", ""):
            entry.category = "reload"
        if sim is None:
            sim = self._log_to(session)
        add = getattr(sim, "_add_log", None) if sim is not None else None
        if add is not None:
            add(entry)

    def _now(self, session, sim=None) -> float:
        if sim is None:
            sim = self._log_to(session)
        fn = getattr(sim, "_now", None) if sim is not None else None
        try:
            return float(fn()) if fn is not None else 0.0
        except Exception:  # noqa: BLE001
            _note_exc("sim.live_reload._now")
            return 0.0


def editor_document(path: str):
    """The editor's open document of the page at ``path`` (an editor window's tab), else None."""
    docs = editor_documents(path)
    return docs[0] if docs else None


def editor_documents(path: str) -> list:
    """Every open editor document of the page at ``path`` (editor windows' tabs), in window
    and tab order."""
    import sys

    mw = sys.modules.get("ctgedit.ui.main_window")
    cls = getattr(mw, "MainWindow", None) if mw is not None else None
    if cls is None or not path:
        return []
    want = path_key(path)
    out = []
    for w in list(getattr(cls, "instances", None) or ()):
        try:
            entries = list(getattr(w, "_docs", ()) or ())
        except RuntimeError:  # a window being destroyed
            continue
        for e in entries:
            d = e.get("doc") if isinstance(e, dict) else None
            p = getattr(d, "path", "") if d is not None else ""
            if not p or not hasattr(d, "export_page_model") or not hasattr(d, "parsed"):
                continue  # (a restored tab not opened yet, an element)
            if path_key(p) == want:
                out.append(d)
    return out


def page_libraries(page, doc=None) -> Optional[Dict[str, Optional[tuple]]]:
    """{library file: its version (:func:`library_stamp`)} of the libraries the page's drawing was derived from (the
    derive's resolver: reader page attribute ``_sim_libraries``, else the document's own
    derivation); None when not known."""
    paths = getattr(page, "_sim_libraries", None) if page is not None else None
    if paths is None and doc is not None:
        der = getattr(doc, "_derivation", None)
        res = getattr(der, "resolver", None) if der is not None else None
        paths = resolver_libraries(res)
    if paths is None:
        return None
    return {p: library_stamp(p) for p in paths}


def resolver_libraries(resolver) -> Optional[List[str]]:
    """The library files a SymbolResolver opened (its library cache), None without one."""
    if resolver is None:
        return None
    cache = getattr(resolver, "_libs_cache", None)
    if not isinstance(cache, dict):
        return None
    out: List[str] = []
    seen: Set[str] = set()
    for libs in list(cache.values()):
        for lib in libs or ():
            p = getattr(lib, "path", "")
            if p and path_key(p) not in seen:
                seen.add(path_key(p))
                out.append(p)
    return out
