"""The status bar."""

from __future__ import annotations
from ..style.theme import sp as _sp  # (the spacing scale)

import os
import time
import weakref
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ..prefs.settings import app_settings


@dataclass
class Item:
    name: str
    factory: Callable
    order: int
    default_visible: bool = True
    label: str = ""
    needs_doc: bool = False
    #: a narrow window drops the fields of the lowest priority first (the message area keeps
    #: its room)
    priority: int = 50


_ITEMS: Dict[str, Item] = {}
#: the windows whose status bar is built (a field registered later is added to them too)
_WINDOWS: "weakref.WeakSet" = weakref.WeakSet()


def register(
    name: str,
    factory: Callable,
    order: int,
    default_visible: bool = True,
    label: str = "",
    needs_doc: bool = False,
    priority: int = 50,
) -> None:
    """Add the status bar field ``name`` (see the module notes); registering a name again
    replaces it in windows built later. ``priority``: when the window is too narrow for every
    field, the lowest go first."""
    _ITEMS[name] = Item(
        name,
        factory,
        int(order),
        bool(default_visible),
        label or name.replace("_", " ").capitalize(),
        bool(needs_doc),
        int(priority),
    )
    for w in list(_WINDOWS):
        try:
            if name not in fields(w):
                _add_field(w, _ITEMS[name])
                refresh(w)
        except RuntimeError:  # swallow-ok: a window being destroyed
            continue


def items() -> List[Item]:
    """The registered fields, left to right."""
    return sorted(_ITEMS.values(), key=lambda i: (i.order, i.name))


# --------------------------------------------------------------------------- choices
def _choices() -> list:
    v = app_settings().value("ui/statusbar_items", [], type=list) or []
    return [str(x) for x in v]


def item_visible(name: str) -> bool:
    """Whether the user shows field ``name`` (its default when not chosen)."""
    ch = _choices()
    if "+" + name in ch:
        return True
    if "-" + name in ch:
        return False
    it = _ITEMS.get(name)
    return it.default_visible if it is not None else True


def set_item_visible(name: str, on: bool) -> None:
    """Show or hide field ``name`` in every editor window (kept for the next start)."""
    ch = [c for c in _choices() if c[1:] != name]
    it = _ITEMS.get(name)
    if it is None or bool(on) != it.default_visible:
        ch.append(("+" if on else "-") + name)
    app_settings().setValue("ui/statusbar_items", ch)
    for w in list(_WINDOWS):
        try:
            refresh(w)
        except RuntimeError:  # swallow-ok: a window being destroyed
            continue


# --------------------------------------------------------------------------- the bar
class MessageField(QtWidgets.QWidget):
    """The message area: the level icon, the text (cut to fit, the whole text in the tool
    tip) and the message's first two buttons."""

    def __init__(self, bar: "StatusBar"):
        super().__init__(bar)
        from ..style.theme import sp

        self._bar = bar
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(sp(1), 0, sp(2), 0)
        lay.setSpacing(sp(2))
        self.icon = QtWidgets.QLabel(self)
        self.icon.setFixedSize(16, 16)
        self.icon.hide()
        lay.addWidget(self.icon)
        from ..names import ElidedLabel

        self.label = ElidedLabel("", self)
        self.label.elide_mode = QtCore.Qt.ElideRight
        self.label.setObjectName("StatusMessage")
        lay.addWidget(self.label, 1)
        self.buttons: List[QtWidgets.QToolButton] = []
        self._btn_lay = QtWidgets.QHBoxLayout()
        self._btn_lay.setSpacing(sp(1))
        lay.addLayout(self._btn_lay)
        self.level: Optional[str] = None
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)

    def set_message(self, text: str, level: Optional[str], actions=(), detail: str = "") -> None:
        from ..notify import LEVEL_NAME, level_icon
        from ..style.theme import set_note_role

        self.level = level
        self.label.setText(text)
        tip = text if not detail else "%s\n%s" % (text, detail)
        self.label.setToolTip(tip if text else "")
        role = {"warning": "warn", "error": "danger"}.get(level or "", "text")
        set_note_role(self.label, role)
        if level:
            self.icon.setPixmap(level_icon(level, 16).pixmap(16, 16))
            self.icon.setToolTip(LEVEL_NAME.get(level, ""))
            self.icon.show()
        else:
            self.icon.hide()
        self.setAccessibleName(("%s: %s" % (LEVEL_NAME.get(level, ""), text)) if level and text else text)
        for b in self.buttons:
            b.hide()
            b.deleteLater()
        self.buttons = []
        for a in list(actions or ())[:2]:
            b = QtWidgets.QToolButton(self)
            b.setObjectName("StatusMessageAction")
            b.setText(a.label)
            b.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
            b.setStyleSheet(_ACTION_QSS)
            b.setEnabled(a.can_run())
            # (a lambda: an Action is a tuple, which a connection cannot keep a weak link to)
            b.clicked.connect(lambda _=False, a=a: a.run())
            self._btn_lay.addWidget(b)
            self.buttons.append(b)

    def sync_actions(self, actions) -> None:
        for b, a in zip(self.buttons, actions or ()):
            b.setEnabled(a.can_run())

    def enterEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        super().enterEvent(e)
        self._bar.hold(True)

    def leaveEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        super().leaveEvent(e)
        self._bar.hold(False)


def _qss_action() -> str:
    from ..style.theme import DARK

    return (
        "QToolButton#StatusMessageAction { color: %(a)s; border: 1px solid transparent; "
        "border-radius: 3px; padding: 0px 6px; }"
        "QToolButton#StatusMessageAction:hover { border-color: %(e)s; }"
        "QToolButton#StatusMessageAction:disabled { color: %(d)s; }"
        'QToolButton#StatusMessageAction[kbdFocus="true"] { border: 2px solid %(a)s; '
        "padding: 0px 5px; }" % {"a": DARK["accent"], "e": DARK["border"], "d": DARK["text_dim"]}
    )


_ACTION_QSS = _qss_action()


class StatusBar(QtWidgets.QStatusBar):
    """The editor window's status bar (see the module notes)."""

    #: ms a message stays at least after the pointer leaves it
    HOLD_MIN_MS = 1500

    def __init__(self, window):
        super().__init__(window)
        self.setObjectName("EditorStatusBar")
        self._win = window
        self.field = MessageField(self)
        self.addWidget(self.field, 1)
        self._text = ""
        self._actions = ()
        self._detail = ""
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.clearMessage)
        self._until = 0.0
        self._held = False

    # -- the message ----------------------------------------------------------------------
    def showMessage(self, text: str, timeout: int = 0) -> None:  # noqa: N802 -- Qt naming
        """Qt's call: a plain message (no level icon), as it is written, for the caller's
        time times Settings > Appearance > Messages stay; kept in the bell's list when the
        caller gives it 4 s or more (not a progress line ending in '…')."""
        from ..notify import scaled_ms

        text = str(text or "")
        self.show_message(text, scaled_ms(timeout))
        if text and int(timeout or 0) >= _record_ms() and not text.endswith("…"):
            from ..notify import Message, centre

            centre(self._win).add(Message(text, "info"))

    def show_hint(self, text: str, ms: int = 0) -> None:
        """A hint (the first run's keys hint): shown like a plain message
        (Messages stay too), never kept in the bell's list."""
        from ..notify import scaled_ms

        self.show_message(str(text or ""), scaled_ms(ms))

    def show_message(
        self, text: str, ms: int = 0, level: Optional[str] = None, actions=(), detail: str = ""
    ) -> None:
        self._text = str(text or "")
        self._actions = tuple(actions or ())
        self._detail = str(detail or "")
        self.field.set_message(self._text, level, self._actions, detail)
        self._timer.stop()
        self._until = time.monotonic() + ms / 1000.0 if ms > 0 else 0.0
        if ms > 0 and not self._held:
            self._timer.start(int(ms))
        self.messageChanged.emit(self._text)

    def currentMessage(self) -> str:  # noqa: N802 -- Qt naming
        return self._text

    def current_level(self) -> Optional[str]:
        return self.field.level if self._text else None

    def message_state(self) -> tuple:
        """What the message area shows now (its text, level, buttons, detail and the moment
        it goes, 0: it stays), for :meth:`restore_message`: a job's progress line told over
        it gives it back when the line goes (ui/jobs/job_progress)."""
        return (self._text, self.current_level(), self._actions, self._detail, self._until)

    def restore_message(self, state) -> None:
        """The message of :meth:`message_state` shown again, for the time it had left; one
        whose time ran out meanwhile goes."""
        text, level, actions, detail, until = state
        left = 0
        if until:
            left = int((until - time.monotonic()) * 1000)
            if left <= 0:
                text = ""
        if not text:
            self.clearMessage()
            return
        self.show_message(text, left, level=level, actions=actions, detail=detail)

    def clearMessage(self) -> None:  # noqa: N802 -- Qt naming
        self._timer.stop()
        self._until = 0.0
        if not self._text and not self.field.buttons:
            return
        self._text = ""
        self._actions = ()
        self._detail = ""
        self.field.set_message("", None)
        self.messageChanged.emit("")

    def hold(self, on: bool) -> None:
        """Keep the message while the pointer is over it (and a moment after)."""
        self._held = bool(on)
        if not self._until:
            return
        if on:
            self._timer.stop()
        else:
            left = max(self.HOLD_MIN_MS, int((self._until - time.monotonic()) * 1000))
            self._until = time.monotonic() + left / 1000.0
            self._timer.start(left)

    def sync_actions(self) -> None:
        """The message's buttons can still run? (an Undo of a step no longer the newest)"""
        self.field.sync_actions(self._actions)

    # -- room for the fields --------------------------------------------------------------
    #: px the message area keeps at least (at 100 %)
    MESSAGE_MIN = 300

    def fit_fields(self) -> None:
        """Show the fields that fit: when the window is too narrow for every field, the ones
        of the lowest priority go first (the message area keeps :data:`MESSAGE_MIN`)."""
        from ..style.theme import px

        fs = list(fields(self._win).values())
        if not fs:
            return
        asked = [f for f in fs if f.asked()]
        others = 0  # (widgets other code put in the bar)
        for w in self.findChildren(QtWidgets.QWidget, options=QtCore.Qt.FindDirectChildrenOnly):
            if (
                w is self.field
                or isinstance(w, Field)
                or not w.isVisible()
                or w.isWindow()
                or isinstance(w, QtWidgets.QSizeGrip)
            ):
                continue
            others += w.sizeHint().width()
        avail = self.width() - px(self.MESSAGE_MIN) - others - px(24)
        total = sum(f.sizeHint().width() for f in asked)
        drop = set()
        for f in sorted(asked, key=lambda f: (f.item.priority, -f.item.order)):
            if total <= avail:
                break
            drop.add(f.item.name)
            total -= f.sizeHint().width()
        for f in fs:
            f.squeezed = f.item.name in drop
            f.sync()

    def resizeEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        super().resizeEvent(e)
        self.fit_fields()

    # -- the right-click menu ---------------------------------------------------------------
    def fields_menu(self) -> QtWidgets.QMenu:
        m = QtWidgets.QMenu(self)
        m.setObjectName("StatusFieldsMenu")
        for it in items():
            if not it.label:
                continue
            a = m.addAction(it.label)
            a.setCheckable(True)
            a.setChecked(item_visible(it.name))
            a.setObjectName("status_field_" + it.name)
            a.toggled.connect(lambda on, n=it.name: set_item_visible(n, on))
        sb = getattr(self._win, "act_status_bar", None)
        if sb is not None:
            m.addSeparator()
            m.addAction(sb)
        return m

    def contextMenuEvent(self, e: QtGui.QContextMenuEvent) -> None:  # noqa: N802
        m = self.fields_menu()
        m.exec(e.globalPos())
        m.deleteLater()
        e.accept()


def _record_ms() -> int:
    from ..notify import RECORD_MIN_MS

    return RECORD_MIN_MS


class Field(QtWidgets.QWidget):
    """One status bar field: its widget with 8 px padding and a 1 px separator at its left.
    It shows while :meth:`wanted` (the user's choice, the page, the widget's own wish)."""

    def __init__(self, item: Item, inner: QtWidgets.QWidget, parent=None):
        super().__init__(parent)
        from ..style.theme import sp

        self.item = item
        self.inner = inner
        self.setObjectName("StatusField_" + item.name)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(sp(2), 0, sp(2), 0)
        lay.setSpacing(0)
        # (no minimum of its own: a narrow window drops whole fields, StatusBar.fit_fields,
        # instead of the window not getting narrower)
        lay.setSizeConstraint(QtWidgets.QLayout.SetNoConstraint)
        lay.addWidget(inner)
        self._watched = list(getattr(inner, "watched", lambda: [inner])())
        for w in self._watched:
            w.installEventFilter(self)
        self.user_on = True
        self.doc_ok = True
        #: dropped because the window is too narrow for every field
        self.squeezed = False

    def minimumSizeHint(self) -> QtCore.QSize:  # noqa: N802 -- Qt naming
        return QtCore.QSize(0, super().minimumSizeHint().height())

    def asked(self) -> bool:
        """Shown by the user's choice, the page and its widget's own wish (room aside)."""
        return self.user_on and self.doc_ok and any(not w.isHidden() for w in self._watched)

    def wanted(self) -> bool:
        return self.asked() and not self.squeezed

    def sync(self) -> None:
        want = self.wanted()
        if want != (not self.isHidden()):
            self.setVisible(want)

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802 -- Qt naming
        if ev.type() in (QtCore.QEvent.ShowToParent, QtCore.QEvent.HideToParent):
            self.sync()
            QtCore.QTimer.singleShot(0, self, self._refit)
        return False

    def _refit(self) -> None:
        bar = self.parentWidget()
        if isinstance(bar, StatusBar):
            bar.fit_fields()
        else:
            self.sync()

    def paintEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        from ..style import theme

        p = QtGui.QPainter(self)
        p.setPen(QtGui.QPen(theme.color("border"), 1))
        m = max(3, self.height() // 5)
        p.drawLine(0, m, 0, self.height() - m)
        p.end()


def fields(window) -> dict:
    return window.__dict__.setdefault("_status_fields", {})


def field(window, name: str) -> Optional[Field]:
    return fields(window).get(name)


def _add_field(window, it: Item) -> Optional[Field]:
    sb = window.statusBar()
    try:
        inner = it.factory(window)
    except Exception:  # noqa: BLE001 -- one field's error must not break the window
        from ...infra.logsetup import note_exception

        note_exception("status_items.factory:%s" % it.name)
        return None
    if inner is None:
        return None
    f = Field(it, inner, sb)
    have = fields(window)
    before = [x for x in have.values() if (x.item.order, x.item.name) < (it.order, it.name)]
    # (normal widgets come first in the bar's index: the message area)
    index = 1 + len(before)
    sb.insertPermanentWidget(index, f)
    have[it.name] = f
    return f


def build(window) -> StatusBar:
    """Give ``window`` its status bar and the registered fields."""
    sb = window.statusBar()
    if not isinstance(sb, StatusBar):
        sb = StatusBar(window)
        window.setStatusBar(sb)
    _WINDOWS.add(window)
    for it in items():
        if it.name not in fields(window):
            _add_field(window, it)
    refresh(window)
    return sb


def refresh(window) -> None:
    """Every field after a change: shown or hidden (the user's choices, a page open) and the
    fields that follow the page (the document state, the zoom, the snap chips)."""
    if not fields(window):
        return  # (not built yet: _build_statusbar does it)
    has_doc = getattr(window, "doc", None) is not None
    _compile_tips(window, has_doc)
    for name, f in list(fields(window).items()):
        f.user_on = item_visible(name)
        f.doc_ok = has_doc or not f.item.needs_doc
        upd = getattr(f.inner, "refresh", None)
        if callable(upd) and f.user_on and f.doc_ok:
            try:
                upd(window)
            except RuntimeError:  # swallow-ok: a widget being destroyed with the window
                continue
    sb = window.statusBar()
    if isinstance(sb, StatusBar):
        sb.fit_fields()
        sb.sync_actions()
    t = window.__dict__.get("_ctg_toast")
    if t is not None:
        t.sync_actions()


def _compile_tips(window, has_doc: bool) -> None:
    """With no page open, Compile and Run act on the project of the most recent page: their
    tool tips name it ('Compiles Example')."""
    for name, verb in (("act_compile", "Compiles"), ("act_run", "Compiles and runs")):
        a = getattr(window, name, None)
        if a is None:
            continue
        own = a.property("_ctg_own_tip")
        if own is None:
            own = a.toolTip() or ""
            a.setProperty("_ctg_own_tip", own)
        target = None
        if not has_doc:
            fn = getattr(window, "_compile_target", None)
            target = fn() if callable(fn) else None
        tip = "%s %s" % (verb, os.path.basename(target[0])) if target else own
        if a.toolTip() != tip:
            a.setToolTip(tip)


def refresh_doc_state(window) -> None:
    """Only the document state field (a save, an undo: the window's title changed)."""
    f = fields(window).get("doc_state")
    if f is not None and f.user_on and f.doc_ok:
        f.inner.refresh(window)


# --------------------------------------------------------------------------- the fields
class ClickButton(QtWidgets.QToolButton):
    """A flat field button with a click and a double-click of its own."""

    doubleClicked = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAutoRaise(True)
        self.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        if e.button() == QtCore.Qt.LeftButton:
            self.doubleClicked.emit()
            e.accept()
            return
        super().mouseDoubleClickEvent(e)


_NO_ARROW = " QToolButton::menu-indicator { image: none; width: 0px; }"


def _project_factory(window):
    b = ProjectButton(window)
    window.lbl_project = b
    return b


class ProjectButton(QtWidgets.QToolButton):
    """Where the page lives: 'Install · Project' (amber when it is not the active install).
    A click lists the project's pages, its included projects, Show in Explorer, Copy Path
    and the install's projects."""

    MAX_PAGES = 30

    def __init__(self, window):
        super().__init__(window)
        self._win = window
        self.setAutoRaise(True)
        self.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        self.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.setAccessibleName("Project")
        self.menu_ = QtWidgets.QMenu(self)
        self.menu_.setObjectName("ProjectMenu")
        self.menu_.aboutToShow.connect(self.fill)
        self.setMenu(self.menu_)
        self.setStyleSheet("")

    def setStyleSheet(self, sheet: str) -> None:  # noqa: N802 -- Qt naming
        """The window's note colour (``set_note_role``: 'color: …; padding: …') and no menu
        arrow (the whole field is the button)."""
        sheet = str(sheet or "").strip()
        if sheet and "{" not in sheet:
            sheet = "QToolButton { %s }" % sheet
        super().setStyleSheet(sheet + _NO_ARROW)

    #: the last line of the tool tip (what a click does)
    CLICK_TIP = "Click for the project's pages, Show in Explorer and Copy Path."

    def setToolTip(self, tip: str) -> None:  # noqa: N802 -- Qt naming
        """The window's tool tip (the page's path, or why it is amber) and what a click
        does."""
        tip = str(tip or "").strip()
        super().setToolTip("%s\n%s" % (tip, self.CLICK_TIP) if tip else "")

    def fill(self) -> None:
        m = self.menu_
        m.clear()
        w = self._win
        doc = getattr(w, "doc", None)
        if doc is None or not getattr(doc, "path", ""):
            a = m.addAction("No page is open")
            a.setEnabled(False)
            return
        pdir = os.path.dirname(os.path.abspath(doc.path))
        name = os.path.basename(pdir)
        head = m.addAction("Pages of %s" % name)
        head.setEnabled(False)
        pages = project_pages(pdir)
        here = os.path.normcase(os.path.abspath(doc.path))
        for p in pages[: self.MAX_PAGES]:
            a = m.addAction(os.path.splitext(os.path.basename(p))[0])
            a.setCheckable(True)
            a.setChecked(os.path.normcase(p) == here)
            a.triggered.connect(lambda _=False, p=p: self._open(p))
        more = m.addAction("More…" if len(pages) > self.MAX_PAGES else "Open Page Browser…")
        more.triggered.connect(lambda _=False: self._browse(pdir))
        cat, info = None, None
        try:
            from ...io.catalog import catalog_for

            cat = catalog_for()
            info = cat.project_for_dir(pdir)
        except Exception:  # noqa: BLE001
            from ...infra.logsetup import note_exception

            note_exception("status_items.project_menu")
        inc = [
            p for p in (cat.closure(info) if cat is not None and info is not None else []) if p is not info
        ]
        if inc:
            sub = m.addMenu("Included Projects")
            for p in inc:
                a = sub.addAction(p.name)
                a.triggered.connect(lambda _=False, d=p.dir: self._browse(d))
        m.addSeparator()
        m.addAction("Show in Explorer", lambda: w._reveal_in_explorer(os.path.abspath(doc.path)))
        m.addAction("Copy Path", lambda: self._copy(os.path.abspath(doc.path)))
        inst = getattr(info, "install", None)
        if cat is not None and inst is not None:
            m.addSeparator()
            sub = m.addMenu(inst.label)
            for p in cat.projects(inst):
                a = sub.addAction(p.name)
                a.setCheckable(True)
                a.setChecked(p is info)
                a.triggered.connect(lambda _=False, d=p.dir: self._browse(d))

    def _open(self, path: str) -> None:
        self._win.open_page(path)

    def _browse(self, project_dir: str) -> None:
        from ..browser import PageBrowserDialog

        w = self._win
        r = PageBrowserDialog.get_open_target(
            w, os.path.join(project_dir, "_"), before_delete=w._before_delete_or_rename
        )
        if r is None:
            return
        if r.kind == "page":
            w.open_page(r.path)
        elif r.element:
            w._open_element(r.path, r.element)

    def _copy(self, path: str) -> None:
        QtWidgets.QApplication.clipboard().setText(path)
        from ..notify import notify

        notify(self._win, "Copied the path of %s." % os.path.basename(path), detail=path)


def project_pages(pdir: str) -> list:
    """The pages (.ctg) of the folder ``pdir``, in natural order."""
    try:
        names = [n for n in os.listdir(pdir) if n.lower().endswith(".ctg")]
    except OSError:
        return []
    from ...io.catalog import natural_key

    return [os.path.join(pdir, n) for n in sorted(names, key=lambda n: natural_key(n.lower()))]


def _ans_factory(window):
    from ..style.theme import set_note_role

    lbl = QtWidgets.QLabel("")
    set_note_role(lbl, "text_dim", "padding:0 6px;")
    window.lbl_ans = lbl
    return lbl


def _hidden_factory(window):
    chip = QtWidgets.QToolButton(window)
    chip.setAutoRaise(True)
    chip.setVisible(False)
    chip.setToolTip("Objects you hid on this page. Click to show them all")
    chip.clicked.connect(window._show_all_roots)
    window._hidden_chip = chip
    return chip


def _changes_factory(window):
    """'5 changed' while View > Mark Changes Since Save is on (ui/panels/changes_dock: the chip
    shows itself while the marks show; a click goes to the next change)."""
    chip = window._make_changes_chip()
    window._changes_chip = chip
    return chip


class ProblemChips(QtWidgets.QWidget):
    """The page's problem chips: symbols not found, a display that may be out of date
    (ui/panels/diag_ui.py)."""

    def __init__(self, window):
        super().__init__(window)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(_sp(1))
        self.chips = window._build_diag_chips(lay)

    def watched(self) -> list:
        return list(self.chips)


class DocStateButton(QtWidgets.QToolButton):
    """'Saved', '4 unsaved changes' or 'Read-only'; the tool tip says when the last recovery
    copy was written. A click shows the History panel (read-only: Save As…)."""

    def __init__(self, window):
        super().__init__(window)
        self._win = window
        self.setAutoRaise(True)
        self.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        self.setAccessibleName("Document state")
        self.state = ""
        self.clicked.connect(self._clicked)
        # (a look twice a second while shown: steps land from many places, a nudge after
        # its pause, a background genie update, a recovery copy)
        self._poll = QtCore.QTimer(self)
        self._poll.setInterval(self.POLL_MS)
        self._poll.timeout.connect(self._tick)

    POLL_MS = 500

    def showEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        super().showEvent(e)
        self._poll.start()

    def hideEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        super().hideEvent(e)
        self._poll.stop()

    def _tick(self) -> None:
        try:
            self.refresh(self._win)
        except RuntimeError:  # swallow-ok: the window is being destroyed
            self._poll.stop()

    def _clicked(self) -> None:
        w = self._win
        if self.state == "read_only":
            w.act_save_as.trigger()
            return
        d = getattr(w, "dock_history", None)
        if d is not None:
            d.show()
            d.raise_()
            hd = getattr(w, "history_dock", None)
            if hd is not None:
                hd.show_document(w.doc)

    def refresh(self, window) -> None:
        from ..names import plural
        from ..style.theme import set_note_role

        doc = getattr(window, "doc", None)
        if doc is None:
            self.state = ""
            return
        n = unsaved_steps(doc)  # (also notes the saved point it sees)
        if getattr(doc, "read_only", False):
            state, text, role = "read_only", "Read-only", "warn"
            tip = "The page is read-only. Click to save a copy with Save As"
        elif getattr(doc, "dirty", False):
            state, role = "unsaved", ""
            text = plural(n, "unsaved change") if n else "Unsaved changes"
            tip = "Changes not saved yet. Click to see them in the History panel"
        else:
            state, text, role = "saved", "Saved", "text_dim"
            tip = "Every change is saved. Click to see the History panel"
        at = getattr(getattr(doc, "ui", None), "recovery_at", None)
        if at and state != "saved":
            tip += ".\nRecovery copy written at %s." % time.strftime("%H:%M", time.localtime(at))
        else:
            tip += "."
        if text != self.text():
            self.setText(text)
        if state != self.state:
            set_note_role(self, role, "padding:0 6px;") if role else set_note_role(self, "", "padding:0 6px;")
        self.state = state
        if tip != self.toolTip():
            self.setToolTip(tip)


def unsaved_steps(doc) -> int:
    """How many undo steps the page is away from its saved version (0 when not known):
    ``doc.saved_depth`` when the document keeps it, else the steps since the window last saw
    it saved."""
    depth = len(getattr(doc, "_undo", None) or []) + int(getattr(doc, "undo_trimmed", 0) or 0)
    if hasattr(doc, "saved_depth"):
        sd = doc.saved_depth
        return abs(depth - sd) if sd is not None else 0
    st = getattr(doc, "ui", None)
    if st is None:
        return 0
    if not getattr(doc, "dirty", False):
        st.status_saved_depth = depth
        return 0
    mark = getattr(st, "status_saved_depth", None)
    if mark is None:
        st.status_saved_depth = mark = 0 if not getattr(doc, "_undo", None) else None
    if mark is None or depth <= mark:
        return 0
    return depth - mark


def _doc_state_factory(window):
    b = DocStateButton(window)
    window.status_doc_state = b
    return b


class SelectionButton(ClickButton):
    """The selection: 'Text AN 104 · 12 × 13 px at 1173, 131' ('3 objects · 120 × 40 px at
    10, 20'). A click shows the Properties panel, a double-click opens Adjust Position and
    Size. The window's ``lbl_sel.setText(...)`` makes it read the selection again."""

    def __init__(self, window):
        super().__init__(window)
        self._win = window
        self.setAccessibleName("Selection")
        self.setToolTip(
            "The selection: its kind, size and position. Click to show the "
            "Properties panel, double-click to adjust its position and size"
        )
        self.clicked.connect(self._properties)
        self.doubleClicked.connect(self._adjust)

    def setText(self, _text: str) -> None:  # noqa: N802 -- Qt naming
        super().setText(selection_text(self._win))

    def _properties(self) -> None:
        d = getattr(self._win, "dock_props", None)
        if d is not None:
            d.show()
            d.raise_()

    def _adjust(self) -> None:
        a = getattr(self._win, "act_adjust", None)
        if a is not None and a.isEnabled():
            a.trigger()


def selection_text(window) -> str:
    from ..names import kind_name, plural

    doc = getattr(window, "doc", None)
    sel = list(getattr(window, "_sel", []) or [])
    if doc is None:
        return ""
    objs = doc.page.objects
    sel = [i for i in sel if 0 <= i < len(objs)]
    if not sel:
        return "No selection"
    boxes = []
    for i in sel:
        g = getattr(objs[i], "geometry", None)
        if g is not None:
            boxes.append((g.x, g.y, g.x + g.extent_x, g.y + g.extent_y))
    if len(sel) == 1:
        o = objs[sel[0]]
        head = kind_name(o) + ((" AN %d" % o.an) if getattr(o, "an", 0) > 0 else "")
    else:
        roots = {doc.root_of(i) for i in sel}
        head = plural(len(roots), "object")
    if not boxes:
        return head
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    return "%s · %d × %d px at %d, %d" % (head, x1 - x0, y1 - y0, x0, y0)


def _selection_factory(window):
    b = SelectionButton(window)
    window.lbl_sel = b
    return b


class PositionLabel(QtWidgets.QLabel):
    """The pointer on the page: 'x 412  y 224 px' (a fixed width: the field does not jump);
    blank while the pointer is not over the canvas."""

    def __init__(self, window):
        super().__init__(window)
        self.setAccessibleName("Pointer position")
        self.setToolTip("The pointer's position on the page, in page pixels")
        self.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        fm = QtGui.QFontMetrics(self.font())
        self.setMinimumWidth(fm.horizontalAdvance("x 0000  y 0000 px") + 4)

    def set_pos(self, x: int, y: int) -> None:
        self.setText("x %d  y %d px" % (x, y))

    def clear_pos(self) -> None:
        self.setText("")


def _position_factory(window):
    lbl = PositionLabel(window)
    window.lbl_pos = lbl
    window.view.hoverPos.connect(lbl.set_pos)
    from ..editor import canvas_hooks

    canvas_hooks.register_viewport_filter("status.position", _LeaveFilter)
    return lbl


class _LeaveFilter(QtCore.QObject):
    """The canvas's viewport: the pointer left it, so the position field goes blank."""

    def __init__(self, view):
        super().__init__(view)
        self._view = view

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802 -- Qt naming
        if ev.type() == QtCore.QEvent.Leave:
            try:
                lbl = getattr(self._view.window(), "lbl_pos", None)
            except RuntimeError:  # swallow-ok: the canvas is being destroyed
                return False
            if isinstance(lbl, PositionLabel):
                lbl.clear_pos()
        return False


class ZoomField(QtWidgets.QWidget):
    """The zoom: a click lists the zoom levels and fits (the navigation bar's menu), a
    double-click fits the page, Ctrl+click types a zoom."""

    def __init__(self, window):
        super().__init__(window)
        self._win = window
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.button = ZoomButton(window, self)
        lay.addWidget(self.button)
        self.spin = QtWidgets.QSpinBox(self)
        self.spin.setRange(1, 6400)
        self.spin.setSuffix(" %")
        self.spin.setAccessibleName("Zoom")
        self.spin.setToolTip("Type a zoom and press Enter. Esc goes back")
        self.spin.hide()
        self.spin.editingFinished.connect(self._spin_done)
        self.spin.installEventFilter(self)
        lay.addWidget(self.spin)

    def watched(self) -> list:
        return [self.button, self.spin]

    def setText(self, text: str) -> None:  # noqa: N802 -- (tabs.py blanks it on close)
        self.button.setText(text)

    def text(self) -> str:
        return self.button.text()

    def show_zoom(self, z: float) -> None:
        from ..editor.navbar import zoom_text

        self.button.setText(zoom_text(z))
        self.button.setAccessibleName("Zoom %d percent" % round(z * 100))

    def refresh(self, window) -> None:
        if not self.button.text() and getattr(window, "doc", None) is not None:
            self.show_zoom(window.view.zoom())

    def edit(self) -> None:
        """Ctrl+click: type a zoom in place."""
        v = self._win.view
        self.spin.setValue(int(round(v.zoom() * 100)))
        self.button.hide()
        self.spin.show()
        self.spin.setFocus(QtCore.Qt.OtherFocusReason)
        self.spin.selectAll()

    def _spin_done(self) -> None:
        if self.spin.isHidden():
            return
        if self.spin.hasFocus() or getattr(self, "_apply", False):
            self._win.view.set_zoom(self.spin.value() / 100.0)
        self._close_spin()

    def _close_spin(self) -> None:
        self._apply = False
        self.spin.hide()
        self.button.show()
        self._win.view.setFocus(QtCore.Qt.OtherFocusReason)

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802 -- Qt naming
        if obj is self.spin and ev.type() == QtCore.QEvent.KeyPress:
            if ev.key() == QtCore.Qt.Key_Escape:
                self._close_spin()
                return True
            if ev.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                self.spin.interpretText()
                self._apply = True
                self._spin_done()
                return True
        if obj is self.spin and ev.type() == QtCore.QEvent.FocusOut and not self.spin.isHidden():
            QtCore.QTimer.singleShot(0, self, self._close_spin)
        return False


class ZoomButton(QtWidgets.QToolButton):
    def __init__(self, window, holder: ZoomField):
        super().__init__(holder)
        self._win = window
        self._holder = holder
        self.setAutoRaise(True)
        self.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        self.setToolTip(
            "The zoom. Click for the zoom levels, double-click to fit the page, Ctrl+click to type a zoom"
        )
        self.setAccessibleName("Zoom")
        self._opened_at = 0.0
        self._menu = None

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        if e.button() != QtCore.Qt.LeftButton or not self.rect().contains(e.position().toPoint()):
            super().mouseReleaseEvent(e)
            return
        self.setDown(False)
        e.accept()
        if e.modifiers() & QtCore.Qt.ControlModifier:
            self._holder.edit()
            return
        self.open_menu()

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        if e.button() == QtCore.Qt.LeftButton:
            self.fit()
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        if e.key() in (QtCore.Qt.Key_Space, QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter, QtCore.Qt.Key_Down):
            self.open_menu()
            e.accept()
            return
        super().keyPressEvent(e)

    def fit(self) -> None:
        m = self._menu
        if m is not None and m.isVisible():
            m.hide()
        a = getattr(self._win, "act_zoom_fit", None)
        if a is not None:
            a.trigger()

    def open_menu(self) -> QtWidgets.QMenu:
        from ..editor.navbar import zoom_menu

        m = self._menu = zoom_menu(self._win)
        self._opened_at = time.monotonic()
        m.installEventFilter(self)
        m.aboutToHide.connect(self._menu_hidden)
        size = m.sizeHint()
        g = self.mapToGlobal(QtCore.QPoint(self.width() - size.width(), -size.height() - 2))
        m.popup(g)
        return m

    def _menu_hidden(self) -> None:
        m = self._menu
        if m is not None:
            m.removeEventFilter(self)
            try:
                m.aboutToHide.disconnect(self._menu_hidden)
            except RuntimeError, TypeError:  # swallow-ok: already disconnected
                pass

    def menu_press(self, global_pos: QtCore.QPoint) -> bool:
        """A press while the zoom menu is open: on this button soon after it opened the menu
        it is the second click of a double-click (Fit Page)."""
        quick = time.monotonic() - self._opened_at <= QtWidgets.QApplication.doubleClickInterval() / 1000.0
        on_me = self.rect().contains(self.mapFromGlobal(global_pos))
        if quick and on_me:
            self.fit()
            return True
        return False

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802 -- Qt naming
        if obj is self._menu and ev.type() == QtCore.QEvent.MouseButtonPress:
            return self.menu_press(ev.globalPosition().toPoint())
        return False


def _zoom_factory(window):
    f = ZoomField(window)
    window.lbl_zoom = f
    window.view.zoomChanged.connect(f.show_zoom)
    return f


class ChipButton(QtWidgets.QToolButton):
    """A toggle chip on the status bar for a menu command (its own QAction), with a short
    name; in the accent colour while it is on."""

    def __init__(self, action: QtGui.QAction, short: str, parent=None):
        super().__init__(parent)
        self._short = short
        self.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        self.setAutoRaise(True)
        self.setDefaultAction(action)
        self.setText(short)
        self.setObjectName("StatusChip")
        self.setStyleSheet(_chip_qss())

    def actionEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        super().actionEvent(e)
        if self.text() != self._short:
            self.setText(self._short)


def _chip_qss() -> str:
    from ..style.theme import DARK

    return (
        "QToolButton#StatusChip { border: 1px solid transparent; border-radius: 3px; "
        "padding: 0px 6px; color: %(dim)s; }"
        "QToolButton#StatusChip:hover { border-color: %(edge)s; }"
        "QToolButton#StatusChip:checked { color: %(a)s; border-color: transparent; "
        "background: rgba(77, 163, 255, 0.16); }"
        "QToolButton#StatusChip:checked:hover { border-color: %(edge)s; }"
        'QToolButton#StatusChip[kbdFocus="true"] { border: 2px solid %(a)s; '
        "padding: 0px 5px; }" % {"dim": DARK["text_dim"], "edge": DARK["border"], "a": DARK["accent"]}
    )


class SnapChips(QtWidgets.QWidget):
    """Snap, Guidelines and Smart Guides (View > Grid > Snap to Grid, View > Guidelines >
    Snap to Guidelines, View > Smart Guides)."""

    def __init__(self, window):
        super().__init__(window)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(_sp(0))
        self.chips = []
        for name, short in (
            ("act_snap", "Snap"),
            ("act_snap_guides", "Guidelines"),
            ("act_smart_guides", "Smart Guides"),
        ):
            a = getattr(window, name, None)
            if a is None:
                continue
            c = ChipButton(a, short, self)
            lay.addWidget(c)
            self.chips.append(c)


class LabelsButton(QtWidgets.QToolButton):
    """'Labels: Native Text' while the page's labels are not shown as stored (View >
    Labels); a click lists the choices."""

    def __init__(self, window):
        super().__init__(window)
        self._win = window
        self.setAutoRaise(True)
        self.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        self.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.setAccessibleName("Labels")
        self.setToolTip("How the page's @( ) labels are shown (View > Labels)")
        self.setStyleSheet(_NO_ARROW.strip())
        m = getattr(window, "labels_menu", None)
        if m is not None:
            self.setMenu(m)

    def refresh(self, window) -> None:
        mode = getattr(window, "_label_mode", "literal") or "literal"
        if mode == "literal":
            self.setVisible(False)
            return
        from ..main_window_base import LABEL_MODE_NAMES

        self.setText("Labels: %s" % LABEL_MODE_NAMES.get(mode, mode).split(":")[0])
        self.setVisible(True)


def _tasks_factory(window):
    from ..jobs.tasks import TaskButton

    b = TaskButton(window)
    window.status_tasks = b
    return b


def _bell_factory(window):
    from ..notify import BellButton

    b = BellButton(window)
    window.status_bell = b
    return b


register("tasks", _tasks_factory, 10, label="Background tasks", priority=95)
register("bell", _bell_factory, 20, label="Messages", priority=100)
register("project", _project_factory, 30, label="Project", needs_doc=True, priority=55)
register("ans", _ans_factory, 40, label="Animation points", needs_doc=True, priority=20)
register("hidden", _hidden_factory, 50, label="Hidden objects", needs_doc=True, priority=85)
register("changes", _changes_factory, 55, label="Changes since save", needs_doc=True, priority=60)
register("problems", lambda w: ProblemChips(w), 60, label="Problems", needs_doc=True, priority=90)
register("doc_state", _doc_state_factory, 70, label="Document state", needs_doc=True, priority=65)
register("selection", _selection_factory, 80, label="Selection", needs_doc=True, priority=70)
register("position", _position_factory, 90, label="Pointer position", needs_doc=True, priority=45)
register("zoom", _zoom_factory, 100, label="Zoom", needs_doc=True, priority=80)
register("snap", lambda w: SnapChips(w), 110, label="Snap", needs_doc=True, priority=30)
register("labels", lambda w: LabelsButton(w), 120, label="Labels", needs_doc=True, priority=40)
