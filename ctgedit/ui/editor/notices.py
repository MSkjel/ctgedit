"""The notice bar over the canvas (read-only, damaged, changed on disk and so on)."""

from __future__ import annotations

import os
from typing import Callable, List, NamedTuple, Optional

from PySide6 import QtCore, QtWidgets

from ...editing import ui_state

LEVEL_EDGE = {"info": "accent", "success": "tip", "warning": "warn", "error": "danger"}
#: Settings > Pages and libraries 'Tell me about a changed page:' (ui/disk_change_prompt)
DISK_CHANGE_PROMPTS = (("bar", "In a bar over the page"), ("dialog", "With a question box"))


def disk_change_prompt() -> str:
    """'bar' (a notice over the page, the default) or 'dialog' (the Changed on Disk box)."""
    from ..prefs.settings import app_settings

    v = str(app_settings().value("ui/disk_change_prompt", "bar") or "bar")
    return v if v in ("bar", "dialog") else "bar"


class Notice(NamedTuple):
    key: str
    level: str
    text: str
    actions: tuple = ()


def auto_change_names(items) -> List[str]:
    """What the automatic changes ``items`` ([(what, undo step)]: DocumentUiState.
    auto_changes) say, each once, in the order they first came."""
    return list(dict.fromkeys(w for w, _c in items or () if w))


def _store(doc) -> list:
    st = getattr(doc, "ui", None)
    if st is None:
        return []
    lst = getattr(st, "notices", None)
    if lst is None:
        lst = []
        st.notices = lst
    return lst


def of(doc) -> List[Notice]:
    """The notices added to ``doc`` (not the states worked out from the page)."""
    return list(_store(doc)) if doc is not None else []


def get(doc, key: str) -> Optional[Notice]:
    return next((n for n in of(doc) if n.key == key), None)


def add(doc, key: str, level: str, text: str, actions=()) -> Notice:
    """Show a notice on ``doc``'s page (see the module notes)."""
    n = Notice(
        key,
        level if level in LEVEL_EDGE else "info",
        str(text),
        tuple((a[0], a[1] if len(a) > 1 else None) for a in actions or ()),
    )
    lst = _store(doc)
    lst[:] = [x for x in lst if x.key != key] + [n]
    _dismissed(doc).discard(key)
    _show_again(doc)
    return n


def remove(doc, key: str) -> bool:
    lst = _store(doc)
    before = len(lst)
    lst[:] = [x for x in lst if x.key != key]
    if len(lst) != before:
        _show_again(doc)
        return True
    return False


def _dismissed(doc) -> set:
    st = getattr(doc, "ui", None)
    if st is None:
        return set()
    d = getattr(st, "notices_dismissed", None)
    if d is None:
        d = set()
        st.notices_dismissed = d
    return d


def window_of(doc, fallback=None):
    """The editor window that has ``doc`` in a tab (the tab may have moved windows)."""
    try:
        from ..main_window import MainWindow

        for w in MainWindow.editor_windows():
            if any(e["doc"] is doc for e in getattr(w, "_docs", [])):
                return w
    except Exception:  # noqa: BLE001  # swallow-ok: no editor windows (a tool)
        pass
    return fallback


def _show_again(doc) -> None:
    try:
        from ..main_window import MainWindow

        wins = MainWindow.editor_windows()
    except Exception:  # noqa: BLE001  # swallow-ok: no editor windows (a tool)
        return
    for w in wins:
        try:
            if getattr(w, "doc", None) is doc:
                bar = getattr(w, "_auto_bar", None)
                if bar is not None:
                    bar.show_for(doc)
        except RuntimeError:  # swallow-ok: a window being destroyed
            continue


def _keep(fn: Callable) -> Callable:
    """The action of a notice worked out from the page (read-only, damaged, super genie):
    it never closes the notice (a cancelled Save As leaves the page as it was); the notice
    goes by itself when the state ends, or with its close button."""

    def run():
        fn()
        return False

    return run


class NoticeRow(QtWidgets.QFrame):
    """One notice: the level icon, the text, the buttons, the close button."""

    closed = QtCore.Signal(str)

    def __init__(self, notice: Notice, run: Callable, parent=None):
        super().__init__(parent)
        from ..notify import LEVEL_NAME, level_icon
        from ..style.theme import DARK, icon, sp

        self.notice = notice
        self.setObjectName("NoticeRow")
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        edge = DARK[LEVEL_EDGE.get(notice.level, "accent")]
        self.setStyleSheet(
            "QFrame#NoticeRow { background: %(bg)s; border: none; border-bottom: 1px solid "
            "%(b)s; border-left: 3px solid %(edge)s; }"
            "QFrame#NoticeRow QLabel { background: transparent; }"
            "QFrame#NoticeRow QPushButton { padding: 2px 10px; }"
            % {"bg": DARK["panel"], "b": DARK["border"], "edge": edge}
        )
        self.setAccessibleName("%s: %s" % (LEVEL_NAME.get(notice.level, ""), notice.text))
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(sp(2), sp(1), sp(1), sp(1))
        lay.setSpacing(sp(2))
        ic = QtWidgets.QLabel(self)
        ic.setPixmap(level_icon(notice.level, 16).pixmap(16, 16))
        ic.setToolTip(LEVEL_NAME.get(notice.level, ""))
        lay.addWidget(ic, 0, QtCore.Qt.AlignVCenter)
        self.label = QtWidgets.QLabel(notice.text, self)
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        lay.addWidget(self.label, 1)
        self.buttons = []
        for k, (label, _fn) in enumerate(notice.actions):
            b = QtWidgets.QPushButton(label, self)
            b.setAutoDefault(False)
            # (the action is looked up at the click: a row reused for another page's equal
            # notice runs that page's action, :meth:`PageNoticeBar.show_for`)
            b.clicked.connect(lambda _=False, k=k: self._clicked(k))
            lay.addWidget(b, 0, QtCore.Qt.AlignVCenter)
            self.buttons.append(b)
        self._run = run
        close = QtWidgets.QToolButton(self)
        close.setIcon(icon("close"))
        close.setAutoRaise(True)
        close.setAccessibleName("Close")
        close.setToolTip("Hide this note")
        close.clicked.connect(lambda: self.closed.emit(self.notice.key))
        lay.addWidget(close, 0, QtCore.Qt.AlignVCenter)
        self.close_btn = close

    def _clicked(self, k: int) -> None:
        acts = self.notice.actions
        if 0 <= k < len(acts):
            self._run(self.notice, acts[k][1])

    def button(self, label: str) -> Optional[QtWidgets.QPushButton]:
        return next((b for b in self.buttons if b.text() == label), None)


class PageNoticeBar(QtWidgets.QWidget):
    """The notices of the page shown, one row each (see the module notes). ``undoRequested``
    = the automatic updates' Undo."""

    undoRequested = QtCore.Signal()

    def __init__(self, window):
        super().__init__(window)
        self._win = window
        self.setObjectName("PageNoticeBar")
        self._lay = QtWidgets.QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(0)
        self.rows: List[NoticeRow] = []
        self._doc = None
        self._empty_label = QtWidgets.QLabel("")
        self.hide()

    @property
    def label(self) -> QtWidgets.QLabel:
        """The automatic updates' text (the row of key 'auto'), else an empty label."""
        r = self.row("auto")
        return r.label if r is not None else self._empty_label

    def row(self, key: str) -> Optional[NoticeRow]:
        return next((r for r in self.rows if r.notice.key == key), None)

    def keys(self) -> list:
        return [r.notice.key for r in self.rows]

    # -- the notices of a page ---------------------------------------------------------------
    def notices_for(self, doc) -> List[Notice]:
        if doc is None:
            return []
        st = ui_state(doc)
        out: List[Notice] = []
        w = self._win
        if st.supergenie_view:
            target = None
            try:
                target = doc.supergenie_element()
            except Exception:  # noqa: BLE001  # swallow-ok: no definition to open then
                target = None
            out.append(
                Notice(
                    "supergenie",
                    "info",
                    "This page is made from a super genie, so it is open read-only. "
                    "Edit the super genie instead.",
                    (("Edit Super Genie", _keep(lambda t=target: w._open_element(*t))),)
                    if target is not None
                    else (),
                )
            )
        elif getattr(doc, "read_only", False):
            out.append(
                Notice(
                    "readonly",
                    "warning",
                    "This page is read-only. Save As saves a copy with your changes.",
                    (("Save As…", _keep(lambda: w.act_save_as.trigger())),),
                )
            )
        damage = getattr(doc, "damage", None)
        if damage:
            out.append(
                Notice(
                    "damaged",
                    "error",
                    "This page is damaged: %s. ctgedit opened what it could read. "
                    "Save As keeps the damaged original." % "; ".join(damage),
                    (
                        ("Save As…", _keep(lambda: w.act_save_as.trigger())),
                        ("Details", _keep(lambda d=doc: w._damage_details(d))),
                    ),
                )
            )
        from ..prefs.settings import option

        items = list(st.auto_changes or ())
        if items and not st.auto_bar_dismissed and option("show_auto_changes"):
            undo = (
                (("Undo", lambda: (self.undoRequested.emit(), False)[1]),)
                if any(c is not None for _w, c in items)
                else ()
            )
            out.append(
                Notice(
                    "auto", "info", "Updated automatically: %s." % "; ".join(auto_change_names(items)), undo
                )
            )
        out.extend(of(doc))
        gone = _dismissed(doc)
        return [n for n in out if n.key not in gone or n.key == "auto"]

    def show_for(self, doc) -> None:
        self._doc = doc
        if doc is not None and not getattr(doc, "dirty", False):
            doc.ui.auto_changes = []  # (saved, or undone: nothing left to list)
        want = self.notices_for(doc)
        if [(n.key, n.level, n.text, tuple(a[0] for a in n.actions)) for n in want] == [
            (r.notice.key, r.notice.level, r.notice.text, tuple(a[0] for a in r.notice.actions))
            for r in self.rows
        ]:
            for r, n in zip(self.rows, want):
                r.notice = n  # (the same rows: their actions follow the page)
            self.setVisible(bool(want))
            return
        for r in self.rows:
            r.hide()
            r.deleteLater()
        self.rows = []
        for n in want:
            r = NoticeRow(n, self._run, self)
            r.closed.connect(self._dismiss)
            self._lay.addWidget(r)
            r.show()  # (a child added to a shown bar waits for the loop)
            self.rows.append(r)
        self.setVisible(bool(want))

    def _run(self, notice: Notice, fn) -> None:
        doc = self._doc
        keep = False
        if fn is not None:
            try:
                keep = fn() is False
            except Exception:  # noqa: BLE001 -- a notice's button must not break the window
                from ...infra.logsetup import note_exception

                note_exception("notices.run:%s" % notice.key)
                keep = True
        if not keep and doc is not None:
            self._close(doc, notice.key)
        if doc is not None and doc is getattr(self._win, "doc", None):
            self.show_for(doc)

    def _dismiss(self, key: str) -> None:
        doc = self._doc
        if doc is None:
            return
        self._close(doc, key)
        self.show_for(doc)

    @staticmethod
    def _close(doc, key: str) -> None:
        if key == "auto":
            doc.ui.auto_bar_dismissed = True
            return
        lst = _store(doc)
        if any(n.key == key for n in lst):
            lst[:] = [n for n in lst if n.key != key]
        else:
            _dismissed(doc).add(key)


def page_name(doc) -> str:
    name = getattr(doc, "display_name", None)
    if name:
        return name
    return os.path.splitext(os.path.basename(getattr(doc, "path", "") or ""))[0]
