"""How modeless windows follow the current page."""

from __future__ import annotations

from ..infra.logsetup import note_exception as _note_exc

from ..infra.paths import path_key

import contextlib
from typing import Callable, List, Optional

from PySide6 import QtCore, QtWidgets

from ..format.geometry import successor_index

#: the object windows (key kind -> the name a close message uses)
OBJECT_WINDOWS = {
    "objprops": "Properties",
    "genie": "Genie Parameters",
    "symset": "Symbol Set",
    "bitmap": "Bitmap Editor",
}
#: the vertical step between windows of the same kind (px)
STEP = 24
#: the delay of a debounced reload / tree rebuild (ms)
DEBOUNCE_MS = 150


def doc_key(doc) -> str:
    """The key part naming a document (its identity)."""
    return "%x" % id(doc)


def _records(doc) -> list:
    try:
        return doc.parsed.page.objects
    except AttributeError:
        return []


def alive(obj) -> bool:
    """``obj`` is a Qt object whose C++ side still exists (False for None)."""
    if obj is None:
        return False
    try:
        import shiboken6

        return shiboken6.isValid(obj)
    except ImportError:  # swallow-ok: no shiboken (a stand-in object in a test)
        return True


def has_pending(dlg) -> bool:
    """``dlg`` shows typed values not applied yet (its ``has_pending_edits``)."""
    fn = getattr(dlg, "has_pending_edits", None)
    try:
        return bool(fn()) if fn is not None else False
    except Exception:  # noqa: BLE001  # swallow-ok: a question or a note only
        return False


def _rebind(fn, old, new):
    """``fn`` (a callback a window was opened with) acting on editor window ``new`` instead
    of ``old``: a bound method of ``old`` becomes ``new``'s; a closure over ``old`` (the
    ``self`` of the method that opened the window) has that cell pointed at ``new`` -- the
    other callbacks made in the same call share the cell and follow too."""
    import types

    if isinstance(fn, types.MethodType):
        return types.MethodType(fn.__func__, new) if fn.__self__ is old else fn
    for cell in getattr(fn, "__closure__", None) or ():
        try:
            if cell.cell_contents is old:
                cell.cell_contents = new
        except ValueError:  # swallow-ok: an empty cell (not bound yet)
            continue
    return fn


class ModelessWindows(dict):
    """The open modeless windows of an editor window by key (see the module
    docstring). ``current()`` = the document shown: the short key
    ``(kind, index)`` of an object window is looked up on it."""

    def __init__(self, current: Callable):
        super().__init__()
        self._current = current

    def full_key(self, key):
        """The stored key for ``key`` (None: no object at that index)."""
        if (
            isinstance(key, tuple)
            and len(key) == 2
            and key[0] in OBJECT_WINDOWS
            and isinstance(key[1], int)
            and not isinstance(key[1], bool)
        ):
            doc = self._current()
            objs = _records(doc)
            if not 0 <= key[1] < len(objs):
                return None
            return (key[0], doc_key(doc), id(objs[key[1]]))
        return key

    def __getitem__(self, key):
        k = self.full_key(key)
        if k is None:
            raise KeyError(key)
        return super().__getitem__(k)

    def get(self, key, default=None):
        k = self.full_key(key)
        return default if k is None else super().get(k, default)

    def __contains__(self, key) -> bool:
        k = self.full_key(key)
        return k is not None and super().__contains__(k)

    def pop(self, key, *default):
        k = self.full_key(key)
        if k is None:
            if default:
                return default[0]
            raise KeyError(key)
        return super().pop(k, *default)

    def __delitem__(self, key) -> None:
        k = self.full_key(key)
        if k is None:
            raise KeyError(key)
        super().__delitem__(k)


class ModelessMixin:
    """MainWindow's modeless windows (see the module docstring)."""

    # -- opening -----------------------------------------------------------------
    def _show_modeless(
        self,
        dlg,
        key,
        on_accept=None,
        scope: str = "object",
        target=None,
        on_applied: Optional[Callable] = None,
        reload: Optional[Callable] = None,
        title: str = "",
        activate: bool = True,
    ):
        """Show ``dlg`` modeless, one window per ``key``."""
        doc = getattr(self, "doc", None)
        idx = None
        if scope == "object":
            objs = _records(doc)
            if isinstance(target, int) and not isinstance(target, bool):
                idx, target = target, None
            elif target is None and isinstance(key, tuple) and len(key) == 2 and isinstance(key[1], int):
                idx = key[1]
            if target is None and idx is not None and 0 <= idx < len(objs):
                target = objs[idx]
            if target is not None and (idx is None or not (0 <= idx < len(objs) and objs[idx] is target)):
                idx = next((i for i, po in enumerate(objs) if po is target), None)
            if target is None or idx is None:
                scope = "page"  # no record to follow: its page's
            else:
                kind = key[0] if isinstance(key, tuple) else str(key)
                key = (kind, doc_key(doc), id(target))
        existing = self._dialogs.get(key)
        if existing is not None and alive(existing):
            if existing.isHidden():
                existing._ctg_tab_hidden = False
                existing.show()
            existing.raise_()
            existing.activateWindow()
            return existing
        dlg.setModal(False)
        dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose, False)
        kind = key[0] if isinstance(key, tuple) else str(key)
        # (its key in the list; not '_ctg_key': the Adjust Position window
        # keeps its selection key under that name, _sync_adjust_dialog)
        dlg._ctg_wkey = key
        dlg._ctg_kind = kind
        dlg._ctg_scope = scope
        dlg._ctg_title = title or OBJECT_WINDOWS.get(kind, "")
        dlg._ctg_own_apply = False
        dlg._ctg_tab_hidden = False
        dlg._ctg_reload = reload
        if scope in ("object", "page") and doc is not None:
            dlg._ctg_doc = doc
            dlg._ctg_doc_key = doc_key(doc)
            dlg._ctg_serial = getattr(doc, "derive_serial", 0)
            dlg._ctg_edits = getattr(doc, "edit_serial", 0)
        else:
            dlg._ctg_doc = None
            dlg._ctg_doc_key = None
        if scope == "object":
            dlg._ctg_target = target
            dlg._ctg_index = idx
            dlg._ctg_sig = self._modeless_sig(doc, idx)
        # the editor window the window belongs to now (a page moved to another editor window
        # takes its windows along: _adopt_modeless)
        dlg._ctg_host = self
        dlg._ctg_on_applied = on_applied
        applied = getattr(dlg, "applied", None)
        if scope in ("object", "page") and applied is not None and hasattr(applied, "connect"):
            applied.connect(
                lambda d=dlg: d._ctg_host._modeless_applied(d, getattr(d, "_ctg_on_applied", None))
            )

        # (a window whose OK is its apply: the page's save question applies
        # its typed values through it)
        dlg._ctg_on_accept = on_accept

        def _finished(result, d=dlg, k=key):
            # out of the list FIRST: an OK that re-derives the page must not
            # find its own (closing) window stale (the key it has NOW: a
            # replaced record re-keys its window)
            host = getattr(d, "_ctg_host", self)
            k = getattr(d, "_ctg_wkey", k)
            if dict.get(host._dialogs, k) is d:
                dict.pop(host._dialogs, k, None)
            fn = getattr(d, "_ctg_on_accept", None)
            try:
                if (
                    result == QtWidgets.QDialog.Accepted
                    and fn is not None
                    and not getattr(d, "_ctg_dropped", False)
                ):
                    fn(d)
            finally:
                d.deleteLater()

        dlg.finished.connect(_finished)
        dict.__setitem__(self._dialogs, key, dlg)
        if not dlg.testAttribute(QtCore.Qt.WA_Moved):
            self._place_modeless(dlg)
        if not activate:
            # (the sheet a new object opens: the canvas keeps the keyboard, so the next
            # tool's key or the next drawing goes on)
            dlg.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)
        dlg.show()
        dlg.raise_()
        if activate:
            dlg.activateWindow()
        return dlg

    def _place_modeless(self, dlg) -> None:
        """The first window of a kind opens at the right of the canvas, the
        next ones of the same kind 24 px lower (kept on the screen)."""
        view = getattr(self, "view", None)
        if view is None or not self.isVisible():
            return
        try:
            vp = view.viewport()
            top_right = vp.mapToGlobal(QtCore.QPoint(vp.width(), 0))
        except RuntimeError:
            return
        size = dlg.size() if dlg.testAttribute(QtCore.Qt.WA_Resized) else dlg.sizeHint()
        n = sum(
            1
            for d in self._dialogs.values()
            if d is not dlg and type(d) is type(dlg) and alive(d) and d.isVisible()
        )
        x = top_right.x() - size.width() - 16
        y = top_right.y() + 16 + STEP * n
        screen = self.screen() or QtWidgets.QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            x = max(area.left(), min(x, area.right() - size.width()))
            y = max(area.top(), min(y, area.bottom() - size.height()))
        dlg.move(x, y)

    def _modeless_for(self, kind: str, index: Optional[int] = None):
        """The open window of ``kind`` for the object at ``index`` (an object
        window) or for the page shown (a page window: 'pageprops'); None."""
        if kind in OBJECT_WINDOWS:
            return self._dialogs.get((kind, index)) if index is not None else None
        doc = getattr(self, "doc", None)
        if doc is None:
            return None
        return self._dialogs.get((kind, doc_key(doc)))

    # -- following the page --------------------------------------------------------
    @staticmethod
    def _modeless_sig(doc, index) -> tuple:
        """What an object window shows: the payloads of its record and the
        record's subtree (a genie's members, an object's touch / keyboard
        records)."""
        from ..io.reader import record_sig

        objs = _records(doc)
        if index is None or not 0 <= index < len(objs):
            return ()
        try:
            span = doc._subtree_span(index)
        except Exception:  # noqa: BLE001
            _note_exc("modeless._modeless_sig")
            span = [index]
        return tuple(record_sig(objs[j]) for j in span if j < len(objs))

    def _modeless_applied(self, dlg, extra: Optional[Callable] = None) -> None:
        """The window's own Apply changed the page: redraw it without
        re-reading the window."""
        dlg._ctg_own_apply = True
        try:
            (extra or self._refresh)()
        finally:
            dlg._ctg_own_apply = False
            self._rebase_modeless(dlg)

    def _rebase_modeless(self, dlg) -> None:
        """What the window shows is the page now (its own Apply)."""
        doc = getattr(dlg, "_ctg_doc", None)
        if doc is None or doc is not getattr(self, "doc", None):
            return
        dlg._ctg_serial = getattr(doc, "derive_serial", 0)
        dlg._ctg_edits = getattr(doc, "edit_serial", 0)
        if getattr(dlg, "_ctg_scope", "") == "object":
            self._rekey_modeless(dlg)
            objs = _records(doc)
            i = getattr(dlg, "_ctg_index", None)
            if i is None or not (0 <= i < len(objs) and objs[i] is dlg._ctg_target):
                i = next((k for k, po in enumerate(objs) if po is dlg._ctg_target), None)
            if i is not None:
                dlg._ctg_index = i
                dlg._ctg_sig = self._modeless_sig(doc, i)

    def _rekey_modeless(self, dlg) -> None:
        """An object window whose record was replaced (its own Apply on a
        genie element's member writes a new record and the window takes it:
        ``_ctg_target``) is filed under the new record, so asking for that
        object's window again finds this one."""
        key = getattr(dlg, "_ctg_wkey", None)
        target = getattr(dlg, "_ctg_target", None)
        if target is None or not (isinstance(key, tuple) and len(key) == 3) or key[2] == id(target):
            return
        new = (key[0], key[1], id(target))
        other = dict.get(self._dialogs, new)
        if other is not None and other is not dlg and alive(other):
            return  # (another window already has that record)
        if dict.get(self._dialogs, key) is dlg:
            dict.pop(self._dialogs, key, None)
        dict.__setitem__(self._dialogs, new, dlg)
        dlg._ctg_wkey = new

    def _reload_object_window(self, dlg, index: int) -> bool:
        fn = getattr(dlg, "_ctg_reload", None)
        if fn is not None:
            return fn(dlg, index) is not False
        fn = getattr(dlg, "reload", None)
        if fn is None:
            return False
        fn()
        return True

    def _sync_modeless(self, reason: str = "refresh") -> None:
        """Follow the page shown with its object and page windows after a
        change. ``reason`` = 'refresh' (an edit), 'history' (Undo / Redo) or
        'replace' (Replace, Update Pages): a window that cannot re-read
        itself closes only for the last two."""
        doc = getattr(self, "doc", None)
        if doc is None or getattr(doc, "parsed", None) is None or getattr(doc.parsed, "page", None) is None:
            return
        dk = doc_key(doc)
        objs = _records(doc)
        serial = getattr(doc, "derive_serial", 0)
        idmap = None
        closed: List[str] = []
        lost = False

        def close(dlg, text: str) -> None:
            nonlocal lost
            lost = lost or has_pending(dlg)
            closed.append(text)
            self._drop_modeless(dlg)

        for key, dlg in list(self._dialogs.items()):
            if getattr(dlg, "_ctg_doc_key", None) != dk or getattr(dlg, "_ctg_dropped", False):
                continue
            if not alive(dlg):
                dict.pop(self._dialogs, key, None)
                continue
            scope = getattr(dlg, "_ctg_scope", "")
            if scope == "object":
                if idmap is None:
                    idmap = {id(po): i for i, po in enumerate(objs)}
                i = idmap.get(id(dlg._ctg_target))
                replaced = False
                if i is None:
                    # its record was replaced, not its object (a genie composed again from
                    # its library, an element tab that read its library again, an Undo that
                    # put the stored record back): the window follows the record that took
                    # its place and keeps what was typed
                    i = successor_index(objs, dlg._ctg_target, dlg._ctg_index)
                    if i is not None:
                        dlg._ctg_target = objs[i]
                        replaced = True
                if i is None:
                    if hasattr(dlg, "target_gone"):
                        dlg.target_gone()  # keeps its edits (Bitmap Editor)
                        continue
                    close(dlg, "The object was removed, so its %s closed." % (dlg._ctg_title or "window"))
                    continue
                self._rekey_modeless(dlg)
                moved = i != dlg._ctg_index
                if moved or replaced:
                    dlg._ctg_index = i
                    fn = getattr(dlg, "retarget", None)
                    if fn is not None:
                        fn(i)
                    elif moved:
                        close(
                            dlg,
                            "The object moved in the page, so its %s closed." % (dlg._ctg_title or "window"),
                        )
                        continue
                if not moved and not replaced:
                    # (the derives since it last looked, and the in-place
                    # changes not derived yet: a move shifts the model in place)
                    try:
                        changes = doc.model_changes_since(dlg._ctg_serial)
                    except Exception:  # noqa: BLE001
                        _note_exc("modeless._sync_modeless")
                        changes = None
                    if changes is not None and doc.root_of(i) not in changes:
                        dlg._ctg_serial = serial  # nothing it shows changed
                        continue
                dlg._ctg_serial = serial
                sig = self._modeless_sig(doc, i)
                if sig == dlg._ctg_sig:
                    continue
                dlg._ctg_sig = sig
                if dlg._ctg_own_apply or getattr(dlg, "_ctg_no_reload", False):
                    continue
                if not self._reload_object_window(dlg, i) and reason != "refresh":
                    close(dlg, "The object changed, so its %s closed." % (dlg._ctg_title or "window"))
            elif scope == "page":
                # (a page edit that changes no record -- page colour, size,
                # page properties -- derives nothing: the edit count shows it)
                edits = getattr(doc, "edit_serial", 0)
                changed = (
                    serial != dlg._ctg_serial
                    or reason != "refresh"
                    or edits != getattr(dlg, "_ctg_edits", edits)
                )
                dlg._ctg_serial = serial
                dlg._ctg_edits = edits
                if not changed or dlg._ctg_own_apply:
                    continue
                if hasattr(dlg, "reload"):
                    self._schedule_page_reload(dlg)
                elif reason != "refresh":
                    close(
                        dlg,
                        "The page changed, so %s closed."
                        % (dlg._ctg_title or dlg.windowTitle() or "its window"),
                    )
        if closed:
            more = len(closed) - 1
            text = (
                closed[0]
                if not more
                else "%s %d other window%s closed too." % (closed[0], more, "" if more == 1 else "s")
            )
            if lost:
                text += " What you typed there was not applied."
            self._say_windows_closed(text, "warning" if lost else "info")

    def _say_windows_closed(self, text: str, level: str) -> None:
        """Why windows closed: in the bell's list and the status bar, and said again with the
        message the same change shows after it (that message would hide the note)."""
        from .notify import centre, notify, reading_ms

        notify(self, text, level)

        def again(t=text, lv=level) -> None:
            try:
                sb = self.statusBar()
                cur = sb.currentMessage()
            except RuntimeError:  # swallow-ok: the window is being destroyed
                return
            if not cur or t in cur:
                return
            # (the later message keeps its level, buttons and detail; a lost typed text is a
            # warning either way)
            later = next((m for m in reversed(centre(self).entries) if m.text == cur), None)
            lvl = lv if lv == "warning" or later is None else later.level
            both = "%s %s" % (cur, t)
            show = getattr(sb, "show_message", None)
            if callable(show):
                show(
                    both,
                    reading_ms(both, lvl),
                    level=lvl,
                    actions=later.actions if later is not None else (),
                    detail=later.detail if later is not None else "",
                )
            else:
                sb.showMessage(both, reading_ms(both, lvl))

        QtCore.QTimer.singleShot(0, self, again)

    def _schedule_page_reload(self, dlg) -> None:
        dlg._ctg_reload_pending = True
        if not dlg.isVisible():
            return  # when its tab is shown again
        t = getattr(dlg, "_ctg_reload_timer", None)
        if t is None:
            t = QtCore.QTimer(dlg)
            t.setSingleShot(True)
            t.setInterval(DEBOUNCE_MS)
            t.timeout.connect(lambda d=dlg: self._run_page_reload(d))
            dlg._ctg_reload_timer = t
        t.start()

    def _run_page_reload(self, dlg) -> None:
        if not getattr(dlg, "_ctg_reload_pending", False) or not alive(dlg):
            return
        if dict.get(self._dialogs, getattr(dlg, "_ctg_wkey", None)) is not dlg or not dlg.isVisible():
            return
        dlg._ctg_reload_pending = False
        dlg.reload()

    def _drop_modeless(self, dlg) -> None:
        """Close a window without asking or applying anything."""
        dict.pop(self._dialogs, getattr(dlg, "_ctg_wkey", None), None)
        dlg._ctg_dropped = True
        with contextlib.suppress(RuntimeError):
            dlg.reject()

    def _drop_stale_dialogs(self, all_objects: bool = False) -> None:
        """Follow the page with its windows (:meth:`_sync_modeless`), or close
        every object and page window of the page shown (``all_objects``)."""
        if not all_objects:
            self._sync_modeless()
            return
        doc = getattr(self, "doc", None)
        if doc is None:
            return
        for dlg in self._page_windows(doc):
            self._drop_modeless(dlg)

    # -- page tabs ------------------------------------------------------------------
    def _page_windows(self, doc) -> list:
        """The object and page windows of ``doc``."""
        dk = doc_key(doc)
        return [
            d for d in list(self._dialogs.values()) if getattr(d, "_ctg_doc_key", None) == dk and alive(d)
        ]

    def _hide_page_windows(self, doc) -> None:
        """Another page is shown: hide the windows of ``doc``."""
        if doc is None:
            return
        for dlg in self._page_windows(doc):
            if dlg.isVisible():
                dlg._ctg_tab_hidden = True
                dlg.hide()

    def _show_page_windows(self, doc) -> None:
        """``doc`` is shown again: its windows come back."""
        if doc is None:
            return
        for dlg in self._page_windows(doc):
            if getattr(dlg, "_ctg_tab_hidden", False):
                dlg._ctg_tab_hidden = False
                dlg.show()
                if getattr(dlg, "_ctg_reload_pending", False):
                    self._run_page_reload(dlg)

    def _mark_modeless_reason(self, docs, reason: str) -> None:
        """``docs`` (pages not shown) were changed by Replace / Update Pages:
        their windows are followed with ``reason`` when they are shown."""
        cur = getattr(self, "doc", None)
        reasons = self.__dict__.setdefault("_modeless_reasons", {})
        for d in docs or ():
            if d is not cur:
                reasons[doc_key(d)] = reason

    def _pop_modeless_reason(self, doc) -> str:
        return self.__dict__.setdefault("_modeless_reasons", {}).pop(doc_key(doc), "refresh")

    def _close_page_windows(self, doc) -> None:
        """``doc`` was closed (or replaced): its windows go too."""
        for dlg in self._page_windows(doc):
            self._drop_modeless(dlg)
        self.__dict__.setdefault("_modeless_reasons", {}).pop(doc_key(doc), None)
        u = getattr(self, "_unresolved_dlg", None)
        if u is not None and alive(u):
            rep = getattr(u, "report", None)
            path = getattr(rep, "path", None)
            if path and path_key(path) == path_key(getattr(doc, "path", "") or ""):
                with contextlib.suppress(RuntimeError):
                    u.close()
                self._unresolved_dlg = None

    def _release_modeless(self, dlg) -> None:
        """Take ``dlg`` out of this window's list (its page moves to another editor window:
        :meth:`_adopt_modeless` there)."""
        k = getattr(dlg, "_ctg_wkey", None)
        if dict.get(self._dialogs, k) is dlg:
            dict.pop(self._dialogs, k, None)
        with contextlib.suppress(RuntimeError):
            dlg._ctg_was_shown = dlg.isVisible() or getattr(dlg, "_ctg_tab_hidden", False)
            dlg.hide()

    def _adopt_modeless(self, dlg, old) -> None:
        """``dlg`` of editor window ``old`` belongs to this one now (its page moved here):
        listed here, a child of this window, and what it calls back on Apply / OK reaches
        this window (the callbacks given to _show_modeless were made in ``old``)."""
        k = getattr(dlg, "_ctg_wkey", None)
        dict.__setitem__(self._dialogs, k, dlg)
        dlg._ctg_host = self
        # (a callback that goes through d._ctg_host already follows; one that a command made
        # over its own window is pointed here)
        for attr in ("_ctg_on_accept", "_ctg_on_applied", "_ctg_reload", "_ctg_apply"):
            fn = getattr(dlg, attr, None)
            if fn is not None:
                setattr(dlg, attr, _rebind(fn, old, self))
        shown = getattr(dlg, "_ctg_was_shown", False)
        geo = dlg.geometry()
        dlg.setParent(self, dlg.windowFlags())
        dlg.setGeometry(geo)
        if shown:
            dlg._ctg_tab_hidden = True  # (it shows with its page: _show_page_windows)

    def _prune_orphan_windows(self) -> None:
        """Close the windows of documents that are no longer in a tab (a
        reverted or reloaded page is a new document)."""
        open_keys = {doc_key(e["doc"]) for e in getattr(self, "_docs", [])}
        for dlg in list(self._dialogs.values()):
            dk = getattr(dlg, "_ctg_doc_key", None)
            if dk is not None and dk not in open_keys:
                self._drop_modeless(dlg)

    def _pending_windows(self, doc) -> list:
        """(window, name) of the windows of ``doc`` with typed values not
        applied yet."""
        return [
            (dlg, dlg.windowTitle() or dlg._ctg_title or "a window")
            for dlg in self._page_windows(doc)
            if has_pending(dlg)
        ]

    @staticmethod
    def _apply_pending_windows(pending) -> bool:
        """Apply the typed values of ``pending`` windows (the page's Save).
        False when one could not be applied."""
        for dlg, _name in pending:
            if getattr(dlg, "_ctg_dropped", False):
                # (closed since the question listed it, its object gone when its tab was
                # shown: its index names another object now; the close note said so)
                continue
            fn = getattr(dlg, "_ctg_apply", None) or getattr(dlg, "apply", None)
            if fn is None:
                if getattr(dlg, "_ctg_on_accept", None) is None:
                    continue
                # a window whose OK is its apply (Genie Parameters, Presentation
                # Options): its OK, with the window's own checks
                try:
                    dlg.accept()
                except Exception:  # noqa: BLE001 -- the save question explains
                    _note_exc("modeless._apply_pending_windows/accept")
                    return False
                if dlg.isVisible() or dlg.result() != QtWidgets.QDialog.Accepted:
                    return False  # its checks kept it open
                continue
            try:
                if fn() is False:
                    return False
            except Exception:  # noqa: BLE001 -- the save question explains
                _note_exc("modeless._apply_pending_windows")
                return False
        return True

    @staticmethod
    def _pending_text(pending) -> str:
        if not pending:
            return ""
        return "\n\nNot applied yet: %s." % ", ".join(name for _d, name in pending)

    def _close_modeless(self, scopes=None) -> None:
        """Close the modeless windows (``scopes``: only those scopes)."""
        for dlg in list(self._dialogs.values()):
            if (
                scopes is None
                or getattr(dlg, "_ctg_scope", "") in scopes
                or getattr(dlg, "_ctg_reads_page", False)
            ):
                self._drop_modeless(dlg)
        if scopes is None:
            dict.clear(self._dialogs)

    # -- selection and window windows ----------------------------------------------------
    def _sync_selection_windows(self) -> None:
        """The selection windows show the current selection of the page shown."""
        doc = getattr(self, "doc", None)
        sel = list(getattr(self, "_sel", []) or [])
        for dlg in list(self._dialogs.values()):
            if getattr(dlg, "_ctg_scope", "") != "selection" or not alive(dlg):
                continue
            fn = getattr(dlg, "set_selection", None)
            if fn is not None:
                with contextlib.suppress(Exception):
                    fn(doc, sel)

    def _schedule_window_sync(self) -> None:
        """Re-read the window windows that show the page (debounced)."""
        if not any(getattr(d, "_ctg_reads_page", False) for d in self._dialogs.values()):
            return
        t = getattr(self, "_window_sync_timer", None)
        if t is None:
            t = self._window_sync_timer = QtCore.QTimer(self)
            t.setSingleShot(True)
            t.setInterval(DEBOUNCE_MS)
            t.timeout.connect(self._run_window_sync)
        t.start()

    def _run_window_sync(self) -> None:
        doc = getattr(self, "doc", None)
        for dlg in list(self._dialogs.values()):
            if not getattr(dlg, "_ctg_reads_page", False) or not alive(dlg):
                continue
            if doc is None:
                self._drop_modeless(dlg)
                continue
            if not dlg.isVisible():
                continue
            fn = getattr(dlg, "_ctg_follow", None) or getattr(dlg, "set_document", None)
            if fn is not None:
                with contextlib.suppress(Exception):
                    fn(doc)
