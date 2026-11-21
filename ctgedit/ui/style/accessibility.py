"""Accessible names for screen readers."""

from __future__ import annotations

import re
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

#: dynamic property: the accessible name made here (and updated here while
#: it is still the widget's name -- a name set since by the code wins)
AUTO_PROP = "ctgAutoName"

#: a key hint at the end of a tool tip: '(Ctrl+S)', '(Ctrl+Shift+A)', '(F7)'
_SHORTCUT_TAIL = re.compile(r"\s*\((?:(?:(?:Ctrl|Shift|Alt|Meta)\+)+[^()\s]{1,14}|F\d{1,2})\)\s*$")
#: a drop-down arrow / triangle glyph at the end of a caption
_ARROW_TAIL = re.compile(r"[\s←-⇿▲-◅]+$")
_MAX_NAME = 80


def spoken(text: Optional[str]) -> str:
    """``text`` as a name: mnemonic '&' removed ('&&' stays one '&'), a
    trailing '…' / '...' and a trailing '(Ctrl+S)' shortcut hint dropped,
    the first line only, at most ~80 characters (cut at a word)."""
    s = str(text or "").replace("&&", "\0").replace("&", "").replace("\0", "&")
    s = s.strip().splitlines()[0].strip() if s.strip() else ""
    s = re.sub(r"(…|\.\.\.)$", "", s).strip()
    s = _SHORTCUT_TAIL.sub("", s).strip()
    s = _ARROW_TAIL.sub("", s).strip()  # 'More ▾' -> 'More'
    if len(s) > _MAX_NAME:
        cut = s[:_MAX_NAME].rsplit(" ", 1)[0]
        s = (cut or s[:_MAX_NAME]).rstrip(" ,;:-")
    return s


def meaningful(text: Optional[str]) -> bool:
    """Whether a screen reader would say something useful for ``text`` (at
    least one letter or digit, not only symbols like '▴' or '×')."""
    return any(ch.isalnum() for ch in str(text or ""))


def _qt_name(w: QtWidgets.QWidget) -> str:
    """The name Qt itself gives the widget (its text / buddy label)."""
    try:
        iface = QtGui.QAccessible.queryAccessibleInterface(w)
        return iface.text(QtGui.QAccessible.Name) if iface is not None else ""
    except Exception:  # noqa: BLE001
        return ""


def _label_text(w: QtWidgets.QWidget) -> str:
    """The label Qt names a field by when it has no name of its own
    (qaccessiblewidget.cpp buddyString): a sibling QLabel whose buddy it is,
    else the title of the group box it sits in. Read directly, so a name made
    here never has to be cleared (a NameChanged event each time) to learn it."""
    try:
        parent = w.parentWidget()
        if parent is None:
            return ""
        for lab in parent.findChildren(
            QtWidgets.QLabel, "", QtCore.Qt.FindChildOption.FindDirectChildrenOnly
        ):
            if lab.buddy() is w:
                return lab.text()
        if isinstance(parent, QtWidgets.QGroupBox):
            return parent.title()
    except RuntimeError:
        pass
    return ""


def suggested_name(w: QtWidgets.QWidget) -> str:
    """A name for ``w`` when Qt would give it none (else '')."""
    if isinstance(w, QtWidgets.QAbstractButton):
        text = w.text()
        if meaningful(text):
            return ""  # Qt says the text
        # (never w.defaultAction(): a QLineEdit's icon button is polished INSIDE
        # QLineEdit.addAction, and wrapping its action there orphans the
        # Python object addAction returns; setDefaultAction copied the
        # action's text and tip onto the button anyway)
        for cand in (w.toolTip(), w.statusTip(), w.whatsThis()):
            if meaningful(cand):
                return spoken(cand)
        return ""
    if isinstance(
        w,
        (
            QtWidgets.QLineEdit,
            QtWidgets.QComboBox,
            QtWidgets.QAbstractSpinBox,
            QtWidgets.QPlainTextEdit,
            QtWidgets.QTextEdit,
            QtWidgets.QSlider,
        ),
    ):
        if meaningful(_label_text(w)):
            return ""  # a buddy label names it
        cands = [w.toolTip(), w.statusTip()]
        if isinstance(w, QtWidgets.QLineEdit):
            cands.insert(0, w.placeholderText())
        elif isinstance(w, QtWidgets.QPlainTextEdit):
            cands.insert(0, w.placeholderText())
        elif isinstance(w, QtWidgets.QComboBox) and w.isEditable() and w.lineEdit() is not None:
            cands.insert(0, w.lineEdit().placeholderText())
        for cand in cands:
            if meaningful(cand):
                return spoken(cand)
    if isinstance(w, QtWidgets.QAbstractItemView):
        # a list / tree / table: its tip, else the panel (dock) it fills
        for cand in (w.toolTip(), w.statusTip()):
            if meaningful(cand):
                return spoken(cand)
        p = w.parentWidget()
        while p is not None and not p.isWindow():
            if isinstance(p, QtWidgets.QDockWidget):
                return spoken(p.windowTitle()) if meaningful(p.windowTitle()) else ""
            p = p.parentWidget()
    return ""


def name_widget(w: QtWidgets.QWidget) -> bool:
    """Give ``w`` a name if it needs one; refresh a name made here earlier.
    True when the name changed."""
    try:
        own = w.accessibleName()
        if own and own != (w.property(AUTO_PROP) or ""):
            return False  # named on purpose
        name = suggested_name(w)
        if name == own:
            return False  # unchanged: no NameChanged event for a screen reader
        w.setAccessibleName(name)
        w.setProperty(AUTO_PROP, name)
        return True
    except RuntimeError:  # deleted meanwhile
        return False


def accessible_name(w: QtWidgets.QWidget) -> str:
    """What a screen reader announces as ``w``'s name."""
    return _qt_name(w)


#: polished (before its first show), shown (a placeholder / text set late),
#: its tool tip or (a dock's) window title changed
_WATCHED = (
    QtCore.QEvent.Type.Polish,
    QtCore.QEvent.Type.Show,
    QtCore.QEvent.Type.ToolTipChange,
    QtCore.QEvent.Type.WindowTitleChange,
)


class _Namer(QtCore.QObject):
    #: the event types it handles (what the editor's combined application
    #: filter hands over, main_window._AppEventFilter)
    TYPES = frozenset(_WATCHED)

    def eventFilter(self, obj, ev) -> bool:
        return self.handle(obj, ev, ev.type())

    def handle(self, obj, ev, t) -> bool:
        if t in self.TYPES and isinstance(obj, QtWidgets.QWidget):
            if t == QtCore.QEvent.Type.WindowTitleChange and isinstance(obj, QtWidgets.QDockWidget):
                # the lists / trees named after this panel follow its new title
                for v in obj.findChildren(QtWidgets.QAbstractItemView):
                    name_widget(v)
            else:
                name_widget(obj)
        return False


def install(app: Optional[QtWidgets.QApplication] = None, dispatcher=None) -> QtCore.QObject:
    """Set accessible names on widgets, now and as they appear."""
    app = app or QtWidgets.QApplication.instance()
    install_focus_ring(app, dispatcher)
    f = getattr(app, "_ctg_namer", None)
    if f is not None:
        return f
    f = _Namer()
    if dispatcher is not None:
        dispatcher.add(f, f.TYPES)
    else:
        app.installEventFilter(f)
    app._ctg_namer = f  # keep it alive
    for w in app.allWidgets():
        name_widget(w)
    return f


# --------------------------------------------------------------------------- focus ring
_FR = QtCore.Qt.FocusReason
#: focus reasons that are keyboard use: Tab, Shift+Tab, a shortcut (Alt+letter, Ctrl+F),
#: the Alt key moving into the menu bar
_KEYBOARD_REASONS = frozenset(
    (_FR.TabFocusReason, _FR.BacktabFocusReason, _FR.ShortcutFocusReason, _FR.MenuBarFocusReason)
)
_K = QtCore.Qt.Key
#: keys that move within a widget or between them: they show the ring on the widget with
#: the focus (a tree clicked and then walked with the arrow keys)
_NAV_KEYS = frozenset(
    (
        _K.Key_Tab,
        _K.Key_Backtab,
        _K.Key_Up,
        _K.Key_Down,
        _K.Key_Left,
        _K.Key_Right,
        _K.Key_Home,
        _K.Key_End,
        _K.Key_PageUp,
        _K.Key_PageDown,
        _K.Key_F6,
        _K.Key_Space,
    )
)
_T = QtCore.QEvent.Type


def _repolish(w: QtWidgets.QWidget) -> None:
    st = w.style()
    st.unpolish(w)
    st.polish(w)
    w.update()


class FocusRing(QtCore.QObject):
    """Show where keyboard focus is."""

    TYPES = frozenset((_T.FocusIn, _T.FocusOut, _T.MouseButtonPress, _T.KeyPress))

    def __init__(self, parent=None):
        super().__init__(parent)
        self._keyboard = False  # the last input was a key press
        # the widgets that have the property now, by their C++ object (a widget's own
        # FocusOut, sent while it is destroyed, comes with a new plain QWidget wrapper)
        self._marked: dict = {}
        self._watched: set = set()  # the C++ objects whose destroyed drops them

    @staticmethod
    def _always() -> bool:
        from . import theme

        return theme.focus_ring() == "always"

    def eventFilter(self, obj, ev) -> bool:
        return self.handle(obj, ev, ev.type())

    def handle(self, obj, ev, t) -> bool:
        if t == _T.KeyPress:
            self._keyboard = True
            if ev.key() in _NAV_KEYS:
                fw = QtWidgets.QApplication.focusWidget()
                if fw is not None and fw is obj:
                    self.mark(fw)
        elif t == _T.MouseButtonPress:
            self._keyboard = False
            if not self._always():
                self.clear_all()
        elif t == _T.FocusIn and isinstance(obj, QtWidgets.QWidget):
            reason = ev.reason()
            if (
                self._always()
                or reason in _KEYBOARD_REASONS
                or (
                    self._keyboard
                    and reason != _FR.MouseFocusReason
                    and reason != _FR.PopupFocusReason
                    and reason != _FR.ActiveWindowFocusReason
                )
            ):
                self.mark(obj)
        elif t == _T.FocusOut and isinstance(obj, QtWidgets.QWidget):
            self.unmark(obj)
        return False

    @staticmethod
    def _key(w: QtWidgets.QWidget) -> int:
        """The C++ object of ``w`` (the same for every Python wrapper of it)."""
        import shiboken6

        return shiboken6.getCppPointer(w)[0]

    def mark(self, w: QtWidgets.QWidget) -> None:
        from .theme import KBD_FOCUS_PROP

        try:
            if w.property(KBD_FOCUS_PROP):
                return
            key = self._key(w)
            w.setProperty(KBD_FOCUS_PROP, True)
            _repolish(w)
            if key not in self._watched:
                # (once per widget: a deleted widget leaves nothing behind here)
                w.destroyed.connect(lambda _obj=None, k=key: self._forget(k))
                self._watched.add(key)
        except RuntimeError:  # swallow-ok: deleted while it took the focus
            return
        self._marked[key] = w

    def _forget(self, key: int) -> None:
        self._marked.pop(key, None)
        self._watched.discard(key)

    def unmark(self, w: QtWidgets.QWidget) -> None:
        from .theme import KBD_FOCUS_PROP
        import shiboken6

        try:
            key = self._key(w)
        except RuntimeError:  # swallow-ok: already deleted
            return
        known = self._marked.pop(key, None)
        if known is not None and known is not w and not shiboken6.isValid(known):
            # the FocusOut its destructor sends: the widget is half destroyed (only its
            # QWidget part is left), so its property and style are left alone
            return
        try:
            if w.property(KBD_FOCUS_PROP):
                w.setProperty(KBD_FOCUS_PROP, False)
                _repolish(w)
        except RuntimeError:  # swallow-ok: deleted meanwhile
            pass

    def clear_all(self) -> None:
        for w in list(self._marked.values()):
            self.unmark(w)
        self._marked = {}


class HotScrollBars(QtCore.QObject):
    """A scroll bar under the mouse widens its handle from 6 to 10 px (the theme's
    ``QScrollBar[hot="true"]`` rule): the ``hot`` property while the mouse is over it."""

    TYPES = frozenset((_T.Enter, _T.Leave))

    def eventFilter(self, obj, ev) -> bool:
        return self.handle(obj, ev, ev.type())

    def handle(self, obj, ev, t) -> bool:
        if t in self.TYPES and isinstance(obj, QtWidgets.QScrollBar):
            hot = t == _T.Enter
            try:
                if bool(obj.property("hot")) != hot:
                    obj.setProperty("hot", hot)
                    _repolish(obj)
            except RuntimeError:
                pass
        return False


def install_focus_ring(app: Optional[QtWidgets.QApplication] = None, dispatcher=None) -> FocusRing:
    """The keyboard focus ring and the hot scroll bars (once per application);
    ``dispatcher`` as for :func:`install`."""
    app = app or QtWidgets.QApplication.instance()
    f = getattr(app, "_ctg_focus_ring", None)
    if f is not None:
        return f
    f = FocusRing()
    hot = HotScrollBars()
    for h in (f, hot):
        if dispatcher is not None:
            dispatcher.add(h, h.TYPES)
        else:
            app.installEventFilter(h)
    app._ctg_focus_ring = f  # keep them alive
    app._ctg_hot_scrollbars = hot
    return f
