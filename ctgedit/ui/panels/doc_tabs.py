"""The document tab bar."""

from __future__ import annotations

from ..style.theme import set_note_role
from ..style.theme import sp as _sp  # (the spacing scale)

from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ..prefs.settings import app_settings, choice

#: a tab is never narrower than this many characters of its name (plus its icon and button)
MIN_CHARS = 12
#: nor wider than this (px); a longer name ends in '…'
MAX_TAB_WIDTH = 240
#: the characters a compact pinned tab shows
PINNED_CHARS = 6
#: a drag of a tab this far above or below the bar takes it out of the bar (px)
DRAG_OUT_PX = 40
#: the mime type of a dragged tab (within this process)
TAB_MIME = "application/x-ctgedit-tab"

#: Settings > Appearance: when the tab close buttons show
TAB_CLOSE_BUTTONS = (
    ("hover", "On the current tab and under the pointer"),
    ("always", "On every tab"),
    ("never", "Never (Ctrl+W or the middle button closes)"),
)
#: Settings > Pages and libraries: the order Ctrl+Tab goes through the tabs
CTRL_TAB_ORDER = (("mru", "Most recently used"), ("position", "Tab order"))
#: Settings > Pages and libraries: what opens when ctgedit starts
STARTUP = (("session", "Reopen the last pages"), ("start", "Show the start page"), ("none", "Open nothing"))
#: Settings > Canvas: the largest zoom a page (an element) opens at
OPEN_ZOOM_MAX = (("100", "100 %"), ("200", "200 %"), ("400", "400 %"), ("0", "No limit"))

KINDS = ("page", "template", "symbol", "genie", "supergenie")
KIND_NAMES = {
    "page": "Page",
    "template": "Template",
    "symbol": "Symbol",
    "genie": "Genie",
    "supergenie": "Super genie",
}


def close_button_mode() -> str:
    return choice("ui/tab_close_button", TAB_CLOSE_BUTTONS, "hover")


def pinned_compact() -> bool:
    return bool(app_settings().value("ui/pinned_tab_compact", True, type=bool))


def ctrl_tab_order() -> str:
    return choice("ui/ctrl_tab_order", CTRL_TAB_ORDER, "mru")


def startup_mode() -> str:
    return choice("opt/startup", STARTUP, "session")


def open_zoom_cap(element: bool = False) -> float:
    """The largest zoom a page (``element``: a library element) opens at, as a factor;
    0 = no limit."""
    if element:
        v = choice("view/open_zoom_max_element", OPEN_ZOOM_MAX, "400")
    else:
        v = choice("view/open_zoom_max", OPEN_ZOOM_MAX, "200")
    return int(v) / 100.0


def _colour(role: str) -> QtGui.QColor:
    from ..style.theme import COLORS

    return QtGui.QColor(COLORS.get(role, COLORS["text"]))


# ------------------------------------------------------------------------ kind icons
def _draw_kind(p: QtGui.QPainter, kind: str, fg: QtGui.QColor) -> None:
    """A tab's kind glyph in a 20 x 20 box."""

    def poly(pts, closed=True):
        path = QtGui.QPainterPath(QtCore.QPointF(*pts[0]))
        for x, y in pts[1:]:
            path.lineTo(x, y)
        if closed:
            path.closeSubpath()
        p.drawPath(path)

    if kind == "template":
        p.drawRect(QtCore.QRectF(3.5, 3.5, 13, 13))
        p.fillRect(QtCore.QRectF(3.5, 3.5, 13, 3.5), fg)
        p.drawLine(QtCore.QLineF(6, 10.5, 14, 10.5))
        p.drawLine(QtCore.QLineF(6, 13.5, 11, 13.5))
    elif kind == "symbol":
        poly([(10, 3), (17, 10), (10, 17), (3, 10)])
        p.setBrush(fg)
        p.drawEllipse(QtCore.QPointF(10, 10), 1.8, 1.8)
    elif kind == "genie":
        p.drawRect(QtCore.QRectF(5.5, 5.5, 9, 9))
        for y in (8.0, 12.0):
            p.drawLine(QtCore.QLineF(2.5, y, 5.5, y))
            p.drawLine(QtCore.QLineF(14.5, y, 17.5, y))
        p.setBrush(fg)
        p.drawEllipse(QtCore.QPointF(10, 10), 1.6, 1.6)
    elif kind == "supergenie":
        p.drawRect(QtCore.QRectF(3.5, 4.5, 13, 11))
        p.fillRect(QtCore.QRectF(3.5, 4.5, 13, 3), fg)
        p.drawRect(QtCore.QRectF(7.5, 10.0, 5, 3.5))
    elif kind == "lock":
        p.drawRect(QtCore.QRectF(5, 9, 10, 8))
        path = QtGui.QPainterPath(QtCore.QPointF(7, 9))
        path.lineTo(7, 6.5)
        path.arcTo(QtCore.QRectF(7, 3.5, 6, 6), 180, -180)
        path.lineTo(13, 9)
        p.drawPath(path)
        p.setBrush(fg)
        p.drawEllipse(QtCore.QPointF(10, 13), 1.2, 1.2)
    else:  # a page
        poly([(4.5, 2.5), (12.5, 2.5), (15.5, 5.5), (15.5, 17.5), (4.5, 17.5)])
        poly([(12.5, 2.5), (12.5, 5.5), (15.5, 5.5)], closed=False)
        p.drawLine(QtCore.QLineF(7, 10, 13, 10))
        p.drawLine(QtCore.QLineF(7, 13, 13, 13))


def kind_pixmap(kind: str, size: int = 16, dpr: float = 1.0, role: str = "text_dim") -> QtGui.QPixmap:
    side = max(1, round(size * dpr))
    pm = QtGui.QPixmap(side, side)
    pm.fill(QtCore.Qt.transparent)
    fg = _colour(role)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    p.scale(side / 20.0, side / 20.0)
    pen = QtGui.QPen(fg, 1.4)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(QtCore.Qt.NoBrush)
    try:
        _draw_kind(p, kind, fg)
    finally:
        p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def kind_icon(kind: str, dpr: float = 1.0) -> QtGui.QIcon:
    """The kind glyph in the theme's colours of now (make it again after a theme change)."""
    ic = QtGui.QIcon()
    for s in (16, 20):
        ic.addPixmap(kind_pixmap(kind, s, dpr), QtGui.QIcon.Normal)
        ic.addPixmap(kind_pixmap(kind, s, dpr, "text"), QtGui.QIcon.Selected)
        ic.addPixmap(kind_pixmap(kind, s, dpr, "text"), QtGui.QIcon.Active)
    return ic


def _strip_pixmap(kind: str, size: int, dpr: float, role: str) -> QtGui.QPixmap:
    side = max(1, round(size * dpr))
    pm = QtGui.QPixmap(side, side)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    p.scale(side / 16.0, side / 16.0)
    pen = QtGui.QPen(_colour(role), 1.4)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    p.setPen(pen)
    if kind == "plus":
        p.drawLine(QtCore.QPointF(8, 3.5), QtCore.QPointF(8, 12.5))
        p.drawLine(QtCore.QPointF(3.5, 8), QtCore.QPointF(12.5, 8))
    elif kind == "tick":  # the current page in the list
        pen.setWidthF(1.8)
        p.setPen(pen)
        path = QtGui.QPainterPath(QtCore.QPointF(3.0, 8.5))
        path.lineTo(6.5, 12.0)
        path.lineTo(13.0, 4.5)
        p.drawPath(path)
    else:  # 'chevron': the list of open pages
        path = QtGui.QPainterPath(QtCore.QPointF(4.5, 6.5))
        path.lineTo(8, 10)
        path.lineTo(11.5, 6.5)
        p.drawPath(path)
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


class _GlyphButton(QtWidgets.QToolButton):
    """A flat button at the end of the tab bar with a drawn glyph ('plus', 'chevron') in the
    theme's colours (made again when the theme changes)."""

    def __init__(self, glyph: str, parent=None):
        super().__init__(parent)
        self._glyph = glyph
        self.setAutoRaise(True)
        self.setIconSize(QtCore.QSize(16, 16))
        self._make_icon()

    def _make_icon(self) -> None:
        dpr = self.devicePixelRatioF() or 1.0
        ic = QtGui.QIcon()
        ic.addPixmap(_strip_pixmap(self._glyph, 16, dpr, "text_dim"), QtGui.QIcon.Normal)
        ic.addPixmap(_strip_pixmap(self._glyph, 16, dpr, "text"), QtGui.QIcon.Active)
        self.setIcon(ic)

    def changeEvent(self, e) -> None:
        if e.type() in (
            QtCore.QEvent.PaletteChange,
            QtCore.QEvent.StyleChange,
            QtCore.QEvent.ApplicationPaletteChange,
        ):
            self._make_icon()
        super().changeEvent(e)


def tick_icon(dpr: float = 1.0) -> QtGui.QIcon:
    """A tick in the text colour (the highlighted row's text colour when highlighted): the
    current page's row in the list of open pages (a checked row with a kind icon shows no
    check mark in this style)."""
    ic = QtGui.QIcon()
    for s in (16, 20):
        ic.addPixmap(_strip_pixmap("tick", s, dpr, "text"), QtGui.QIcon.Normal)
        ic.addPixmap(_strip_pixmap("tick", s, dpr, "sel_text"), QtGui.QIcon.Active)
        ic.addPixmap(_strip_pixmap("tick", s, dpr, "sel_text"), QtGui.QIcon.Selected)
    return ic


# ------------------------------------------------------------------------ the close button
class TabCloseButton(QtWidgets.QAbstractButton):
    """A tab's close button: a thin cross in the dim text colour (the full text colour on a
    hover square when the pointer is over it), or a filled dot while the page has unsaved
    changes and the tab is not under the pointer. ``mode`` 'none' paints nothing and takes no
    click, but keeps its place (the tab does not change width on hover)."""

    SIZE = 16

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(QtCore.Qt.ArrowCursor)
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self.setToolTip("Close")
        self.setAccessibleName("Close")
        self._mode = "cross"
        self._current = False

    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str, current: bool = False) -> None:
        """'cross' | 'dot' (a dot that turns into the cross under the pointer) | 'mark' (a
        dot that takes no click) | 'pin' (a pinned tab: a click unpins it) | 'none'."""
        if mode != self._mode or current != self._current:
            self._mode = mode
            self._current = current
            tip = {
                "dot": "Unsaved changes. Close",
                "mark": "Unsaved changes",
                "pin": "Pinned. Click to unpin",
            }.get(mode, "Close")
            self.setToolTip(tip)
            self.setAccessibleName({"pin": "Unpin", "mark": "Unsaved changes"}.get(mode, "Close"))
            self.update()

    def shows(self) -> str:
        """What it paints now: 'cross', 'dot', 'pin' or ''."""
        m = self._mode
        hover = self.underMouse()
        if m == "cross":
            return "cross"
        if m == "dot":
            return "cross" if hover else "dot"
        if m == "mark":
            return "dot"
        if m == "pin":
            return "pin"
        return ""

    def mousePressEvent(self, e) -> None:
        if self._mode in ("none", "mark"):
            e.ignore()  # the tab under it takes the click
            return
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        if self._mode in ("none", "mark"):
            e.ignore()
            return
        super().mouseReleaseEvent(e)

    def enterEvent(self, e) -> None:
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e) -> None:
        what = self.shows()
        if not what:
            return
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        r = QtCore.QRectF(self.rect())
        clickable = self._mode not in ("mark",)
        hover = self.underMouse() and clickable
        if hover:
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(_colour("raised" if self.isDown() else "hover"))
            p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
        fg = _colour("text" if hover or self._current else "text_dim")
        if what == "dot":
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(fg)
            p.drawEllipse(r.center(), 4.0, 4.0)
        elif what == "pin":
            # a push pin: the head, the collar and the needle, leaning right
            p.translate(r.center())
            p.rotate(40)
            pen = QtGui.QPen(fg, 1.2)
            pen.setCapStyle(QtCore.Qt.RoundCap)
            p.setPen(pen)
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawRoundedRect(QtCore.QRectF(-2.2, -5.5, 4.4, 5.0), 1.0, 1.0)
            p.drawLine(QtCore.QPointF(-3.8, -0.5), QtCore.QPointF(3.8, -0.5))
            p.drawLine(QtCore.QPointF(0, -0.5), QtCore.QPointF(0, 5.5))
        else:
            pen = QtGui.QPen(fg, 1.3)
            pen.setCapStyle(QtCore.Qt.RoundCap)
            p.setPen(pen)
            c = r.center()
            d = 3.5
            p.drawLine(QtCore.QPointF(c.x() - d, c.y() - d), QtCore.QPointF(c.x() + d, c.y() + d))
            p.drawLine(QtCore.QPointF(c.x() + d, c.y() - d), QtCore.QPointF(c.x() - d, c.y() + d))
        p.end()


class _TabTail(QtWidgets.QWidget):
    """What a tab shows after its name: the project (when another open tab has the same
    name) and the close button."""

    def __init__(self, bar: "DocTabBar"):
        super().__init__(bar)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(_sp(0), 0, 0, 0)
        lay.setSpacing(_sp(1))
        self.project = QtWidgets.QLabel(self)
        self.project.setObjectName("TabProject")
        self.project.hide()
        self.button = TabCloseButton(self)
        lay.addWidget(self.project)
        lay.addWidget(self.button)
        self.button.clicked.connect(lambda: bar.close_button_clicked(self))
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)

    def set_project(self, text: str) -> None:
        if text:
            self.project.setText("· " + text)
            set_note_role(self.project, "text_dim", "background: transparent;")
            self.project.show()
        else:
            self.project.clear()
            self.project.hide()
        self.adjustSize()


# ------------------------------------------------------------------------ the tab bar
class DocTabBar(QtWidgets.QTabBar):
    """The open pages and library elements (see the module docstring). The window keeps each
    tab's state with :meth:`set_tab_state`; the bar tells it what the user asked for through
    its signals (``tabCloseRequested`` too, as a QTabBar does)."""

    #: right-click on a tab (index, global position)
    tabMenuRequested = QtCore.Signal(int, QtCore.QPoint)
    #: a tab was dragged out of the bar and dropped where no editor window took it
    #: (index, global position)
    tabDraggedOut = QtCore.Signal(int, QtCore.QPoint)
    #: another window's tab was dropped here (the source bar, its index, the drop index)
    tabDropped = QtCore.Signal(object, int, int)
    #: a drag (a move) of the tabs ended
    moveFinished = QtCore.Signal()
    #: the pin of a pinned tab was clicked
    tabUnpinRequested = QtCore.Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAccessibleName("Open pages")
        self.setDocumentMode(True)
        self.setTabsClosable(False)  # (the bar's own close buttons: _TabTail)
        self.setMovable(True)
        self.setExpanding(False)
        self.setElideMode(QtCore.Qt.ElideRight)
        self.setUsesScrollButtons(True)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setIconSize(QtCore.QSize(16, 16))
        self._hover = -1
        self._press = None  # (index, pos) of a left press on a tab
        self._dragging_out = False
        self._strip = None
        self._icons = {}
        self._icons_dpr = 0.0
        self._uids = 0
        self.drag_index = -1
        self.currentChanged.connect(lambda _i: self._sync_buttons())
        # (the pressed tab moves along the bar while it is dragged: its index follows)
        self.tabMoved.connect(self._follow_press)

    # -- the strip ---------------------------------------------------------------------
    def setVisible(self, on: bool) -> None:  # noqa: N802 -- Qt naming
        super().setVisible(on)
        if self._strip is not None:
            self._strip.setVisible(on)

    # -- per-tab state -------------------------------------------------------------------
    def _state(self, i: int) -> dict:
        d = self.tabData(i)
        return d if isinstance(d, dict) else {}

    def _icon(self, kind: str) -> QtGui.QIcon:
        dpr = self.devicePixelRatioF() or 1.0
        if dpr != self._icons_dpr:
            self._icons = {}
            self._icons_dpr = dpr
        ic = self._icons.get(kind)
        if ic is None:
            ic = self._icons[kind] = kind_icon(kind, dpr)
        return ic

    def _tail(self, i: int) -> _TabTail:
        t = self.tabButton(i, QtWidgets.QTabBar.RightSide)
        if not isinstance(t, _TabTail):
            t = _TabTail(self)
            self.setTabButton(i, QtWidgets.QTabBar.RightSide, t)
        return t

    def tab_uid(self, i: int) -> int:
        """Tab ``i``'s own number: it stays with the tab when tabs move (a dragged tab is
        named by it, not by its index). 0: no such tab."""
        if not 0 <= i < self.count():
            return 0
        st = self._state(i)
        if not st.get("uid"):
            self._uids += 1
            st = dict(st, uid=self._uids)
            self.setTabData(i, st)
        return st["uid"]

    def index_of_uid(self, uid: int) -> int:
        """The index of the tab numbered ``uid`` now (-1: none)."""
        if uid:
            for i in range(self.count()):
                if self._state(i).get("uid") == uid:
                    return i
        return -1

    def set_tab_state(
        self,
        i: int,
        name: str,
        kind: str = "page",
        dirty: bool = False,
        pinned: bool = False,
        read_only: bool = False,
        project: str = "",
        show_project: bool = False,
        tip: str = "",
        sync: bool = True,
    ) -> None:
        """Tab ``i`` shows ``name`` (its first characters when pinned and compact), the kind
        icon (a lock when read-only), ``project`` after the name when ``show_project`` (a
        pinned tab never does) and the unsaved dot when ``dirty``. ``sync`` False: the
        caller sets several tabs and calls :meth:`sync_buttons` once at the end."""
        if not 0 <= i < self.count():
            return
        compact = pinned and pinned_compact()
        old = self._state(i)
        st = {
            "name": name,
            "kind": kind,
            "dirty": bool(dirty),
            "pinned": bool(pinned),
            "read_only": bool(read_only),
            "project": project,
            "show_project": bool(show_project and not pinned),
            "compact": compact,
            "uid": old.get("uid") or self.tab_uid(i),
        }
        text = name[:PINNED_CHARS] if compact else name
        if old == st and self.tabText(i) == text and (not tip or self.tabToolTip(i) == tip):
            if sync:  # (nothing changed: every refresh asks)
                self._sync_buttons()
            return
        if old != st or self.tabText(i) != text:
            self.setTabData(i, st)
            if self.tabText(i) != text:
                self.setTabText(i, text)
            self.setTabIcon(i, self._icon("lock" if read_only else kind))
            tail = self._tail(i)
            tail.set_project(project if st["show_project"] else "")
            what = []
            if read_only:
                what.append("read-only")
            if dirty:
                what.append("unsaved")
            self.setAccessibleTabName(i, name + (" (%s)" % ", ".join(what) if what else ""))
            self._relayout()
        if tip and self.tabToolTip(i) != tip:
            self.setTabToolTip(i, tip)
        if sync:
            self._sync_buttons()

    def sync_buttons(self) -> None:
        """Every close button's look (after :meth:`set_tab_state` calls with sync False)."""
        self._sync_buttons()

    def tab_state(self, i: int) -> dict:
        """What :meth:`set_tab_state` gave tab ``i`` (tests, the window's menus)."""
        return dict(self._state(i))

    def full_name(self, i: int) -> str:
        return self._state(i).get("name") or self.tabText(i)

    def _relayout(self) -> None:
        # (a tab's size depends on its button and text: Qt lays the bar out again when an
        # icon size changes)
        self.setIconSize(self.iconSize())
        self.updateGeometry()

    def relayout(self) -> None:
        """Every tab sized again (Settings > Appearance > Tab width changed)."""
        self._relayout()
        self.update()

    def close_button(self, i: int) -> Optional[TabCloseButton]:
        t = self.tabButton(i, QtWidgets.QTabBar.RightSide)
        return t.button if isinstance(t, _TabTail) else None

    def _sync_buttons(self) -> None:
        """Each close button's look: the setting, the current and the hovered tab, unsaved,
        pinned."""
        mode = close_button_mode()
        cur = self.currentIndex()
        for i in range(self.count()):
            b = self.close_button(i)
            if b is None:
                continue
            st = self._state(i)
            dirty, pinned = st.get("dirty", False), st.get("pinned", False)
            if pinned:
                m = "mark" if dirty else "pin"
            elif mode == "never":
                m = "mark" if dirty else "none"
            elif dirty:
                m = "cross" if i == self._hover else "dot"
            elif mode == "always" or i in (cur, self._hover):
                m = "cross"
            else:
                m = "none"
            b.set_mode(m, current=i == cur)

    def close_button_clicked(self, tail: _TabTail) -> None:
        for i in range(self.count()):
            if self.tabButton(i, QtWidgets.QTabBar.RightSide) is tail:
                if tail.button.mode() == "pin":
                    self.tabUnpinRequested.emit(i)
                else:
                    self.tabCloseRequested.emit(i)
                return

    def changeEvent(self, e) -> None:
        if e.type() in (
            QtCore.QEvent.PaletteChange,
            QtCore.QEvent.StyleChange,
            QtCore.QEvent.ApplicationPaletteChange,
        ):
            # the theme changed: the icons and the project labels in its colours
            self._icons = {}
            for i in range(self.count()):
                st = self._state(i)
                if st:
                    self.setTabIcon(i, self._icon("lock" if st.get("read_only") else st.get("kind", "page")))
                    t = self.tabButton(i, QtWidgets.QTabBar.RightSide)
                    if isinstance(t, _TabTail):
                        t.set_project(st.get("project", "") if st.get("show_project") else "")
        super().changeEvent(e)

    # -- sizes ---------------------------------------------------------------------------
    def _min_width(self, i: int) -> int:
        st = self._state(i)
        fm = self.fontMetrics()
        text = self.tabText(i)
        chars = len(text) if st.get("compact") else min(len(text), MIN_CHARS)
        w = fm.horizontalAdvance(text[:chars])
        if not st.get("compact") and len(text) > MIN_CHARS:
            w += fm.horizontalAdvance("…")
        w += self.iconSize().width() + 4 if not self.tabIcon(i).isNull() else 0
        tail = self.tabButton(i, QtWidgets.QTabBar.RightSide)
        if tail is not None:
            w += tail.sizeHint().width() + 4
        return w + 26  # the tab's padding (theme: 12 px a side)

    def tabSizeHint(self, i: int) -> QtCore.QSize:  # noqa: N802 -- Qt naming
        s = super().tabSizeHint(i)
        lo = self._min_width(i)
        if self._state(i).get("compact"):
            return QtCore.QSize(lo, s.height())
        tail = self.tabButton(i, QtWidgets.QTabBar.RightSide)
        extra = tail.sizeHint().width() if tail is not None and self._state(i).get("show_project") else 0
        hi = max(lo, MAX_TAB_WIDTH + extra)
        return QtCore.QSize(max(lo, min(s.width(), hi)), s.height())

    def minimumTabSizeHint(self, i: int) -> QtCore.QSize:  # noqa: N802 -- Qt naming
        s = super().minimumTabSizeHint(i)
        return QtCore.QSize(self.tabSizeHint(i).width(), s.height())

    def shown_text(self, i: int) -> str:
        """The text tab ``i`` shows now (elided as Qt draws it)."""
        opt = QtWidgets.QStyleOptionTab()
        self.initStyleOption(opt, i)
        return opt.text

    # -- the mouse ---------------------------------------------------------------------------
    def mouseMoveEvent(self, e) -> None:
        pos = e.position().toPoint()
        k = self.tabAt(pos)
        if k != self._hover:
            self._hover = k
            self._sync_buttons()
        if self._press is not None and e.buttons() & QtCore.Qt.LeftButton and not self._dragging_out:
            if pos.y() < -DRAG_OUT_PX or pos.y() > self.height() + DRAG_OUT_PX:
                # (the pressed tab's index NOW: a drag along the bar first moved it)
                self._start_drag_out(self._press[0], e)
                return
        super().mouseMoveEvent(e)

    def _follow_press(self, frm: int, to: int) -> None:
        """A tab moved from ``frm`` to ``to``: the pressed tab's index follows."""
        if self._press is None:
            return
        k = self._press[0]
        if k == frm:
            k = to
        elif frm < k <= to:
            k -= 1
        elif to <= k < frm:
            k += 1
        self._press = (k, self._press[1])

    def leaveEvent(self, e) -> None:
        if self._hover != -1:
            self._hover = -1
            self._sync_buttons()
        super().leaveEvent(e)

    def mousePressEvent(self, e) -> None:
        pos = e.position().toPoint()
        k = self.tabAt(pos)
        if e.button() == QtCore.Qt.LeftButton and k >= 0:
            self._press = (k, pos)
        if e.button() == QtCore.Qt.MiddleButton and k >= 0:
            e.accept()  # (closed on release, under the same tab)
            self._press = None
            self._middle = k
            return
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        pos = e.position().toPoint()
        if e.button() == QtCore.Qt.MiddleButton:
            k = self.tabAt(pos)
            if k >= 0 and k == getattr(self, "_middle", -1):
                self.tabCloseRequested.emit(k)
            self._middle = -1
            e.accept()
            return
        was = self._press
        self._press = None
        super().mouseReleaseEvent(e)
        if was is not None and e.button() == QtCore.Qt.LeftButton:
            self.moveFinished.emit()

    def contextMenuEvent(self, e) -> None:
        k = self.tabAt(e.pos())
        if k >= 0:
            self.tabMenuRequested.emit(k, e.globalPos())
            e.accept()
        else:
            e.ignore()

    # -- dragging a tab out and in ----------------------------------------------------------
    def _start_drag_out(self, index: int, e) -> None:
        """The pressed tab left the bar: Qt's own move ends; a drag of the tab starts (another
        window's tab bar takes it; dropped where none does, it opens in a new window)."""
        self._dragging_out = True
        self._press = None
        try:
            # end Qt's own move of the tab first
            up = QtGui.QMouseEvent(
                QtCore.QEvent.MouseButtonRelease,
                e.position(),
                e.globalPosition(),
                QtCore.Qt.LeftButton,
                QtCore.Qt.NoButton,
                QtCore.Qt.NoModifier,
            )
            super().mouseReleaseEvent(up)
            if not 0 <= index < self.count():
                return
            # the tab goes by its own number (its index may change before it is dropped)
            uid = self.tab_uid(index)
            drag = QtGui.QDrag(self)
            mime = QtCore.QMimeData()
            mime.setData(TAB_MIME, QtCore.QByteArray(str(uid).encode("ascii")))
            drag.setMimeData(mime)
            with_pm = self.tab_pixmap(index)
            if not with_pm.isNull():
                drag.setPixmap(with_pm)
                drag.setHotSpot(QtCore.QPoint(with_pm.width() // 2, with_pm.height() // 2))
            self.drag_index = index
            result = self.exec_drag(drag)
            if result == QtCore.Qt.IgnoreAction:
                k = self.index_of_uid(uid)
                if k >= 0:
                    self.tabDraggedOut.emit(k, QtGui.QCursor.pos())
        finally:
            self._dragging_out = False
            self.drag_index = -1

    @staticmethod
    def exec_drag(drag: QtGui.QDrag):
        """Run the drag (a seam: tests answer it)."""
        return drag.exec(QtCore.Qt.MoveAction)

    def tab_pixmap(self, index: int) -> QtGui.QPixmap:
        r = self.tabRect(index)
        if r.isEmpty():
            return QtGui.QPixmap()
        return self.grab(r)

    def _tab_drag(self, e) -> bool:
        return (
            e.mimeData() is not None
            and e.mimeData().hasFormat(TAB_MIME)
            and isinstance(e.source(), DocTabBar)
        )

    def dragEnterEvent(self, e) -> None:
        if self._tab_drag(e):
            # (another window's tab moves here; this bar's own tab, dragged out and back,
            # goes where it is dropped)
            e.setDropAction(QtCore.Qt.MoveAction)
            e.accept()
            return
        e.ignore()  # (pages from Explorer: the window opens them)

    def dragMoveEvent(self, e) -> None:
        if self._tab_drag(e):
            e.setDropAction(QtCore.Qt.MoveAction)
            e.accept()
            return
        e.ignore()

    def dropEvent(self, e) -> None:
        if not self._tab_drag(e):
            e.ignore()
            return
        src_index = dragged_tab_index(e.mimeData(), e.source())
        if src_index < 0:
            e.ignore()
            return
        at = self.tabAt(e.position().toPoint())
        e.setDropAction(QtCore.Qt.MoveAction)
        e.accept()
        self.tabDropped.emit(e.source(), src_index, at if at >= 0 else self.count())


def dragged_tab_index(mime, source) -> int:
    """The index in ``source`` (the tab bar a tab drag started from) of the tab the drag
    carries (its number, :meth:`DocTabBar.tab_uid`); -1 when it is not there any more."""
    if mime is None or not mime.hasFormat(TAB_MIME) or not isinstance(source, DocTabBar):
        return -1
    try:
        uid = int(bytes(mime.data(TAB_MIME)).decode("ascii"))
    except ValueError:  # swallow-ok: a foreign payload is no tab
        return -1
    return source.index_of_uid(uid)


# ------------------------------------------------------------------------ the Ctrl+Tab list
class TabSwitcher(QtWidgets.QFrame):
    """The open pages in the order they were last used, centred over the window while Ctrl is
    held: Tab / Shift+Tab (or the arrows) move the choice, releasing Ctrl shows it, Esc
    cancels, a click shows the one clicked."""

    chosen = QtCore.Signal(object)
    cancelled = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget):
        super().__init__(parent)
        self.setObjectName("TabSwitcher")
        self.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self.setAutoFillBackground(True)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(_sp(1), _sp(1), _sp(1), _sp(1))
        lay.setSpacing(_sp(1))
        self.title = QtWidgets.QLabel("Open pages", self)
        lay.addWidget(self.title)
        self.list = QtWidgets.QTreeWidget(self)
        self.list.setObjectName("TabSwitcherList")
        self.list.setColumnCount(2)
        self.list.setHeaderHidden(True)
        self.list.setRootIsDecorated(False)
        self.list.setIndentation(0)
        self.list.setFocusPolicy(QtCore.Qt.NoFocus)
        self.list.setUniformRowHeights(True)
        self.list.setIconSize(QtCore.QSize(16, 16))
        self.list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.list.setAccessibleName("Open pages, the last used first")
        self.list.itemClicked.connect(lambda it, _c=0: self._choose(it))
        lay.addWidget(self.list)
        hint = self.hint = QtWidgets.QLabel("Release Ctrl to show the page. Esc cancels.", self)
        hint.setObjectName("TabSwitcherHint")
        lay.addWidget(hint)
        self._entries = []
        self.hide()
        self._restyle()

    def _restyle(self) -> None:
        from ..style.theme import COLORS

        self.setStyleSheet(
            "#TabSwitcher { background: %(popup)s; border: 1px solid %(border)s; "
            "border-radius: 8px; } #TabSwitcher QLabel { color: %(text_dim)s; "
            "background: transparent; } "
            "#TabSwitcherList { background: transparent; border: none; } "
            "#TabSwitcherList::item { padding: 4px 6px; } "
            "#TabSwitcherList::item:selected { background: %(selection)s; "
            "color: %(sel_text)s; }" % COLORS
        )

    def open(self, rows, start: int = 1) -> None:
        """Show ``rows`` = [(entry, name, project, kind, dirty)] with row ``start`` chosen."""
        from ..style.theme import COLORS

        self._restyle()
        self.list.clear()
        self._entries = []
        dim = QtGui.QColor(COLORS["text_dim"])
        dpr = self.devicePixelRatioF() or 1.0
        for entry, name, project, kind, dirty in rows:
            it = QtWidgets.QTreeWidgetItem([name + ("  •" if dirty else ""), project or ""])
            it.setIcon(0, kind_icon(kind, dpr))
            it.setForeground(1, dim)
            it.setToolTip(0, "%s (%s)%s" % (name, project, ", unsaved" if dirty else ""))
            self.list.addTopLevelItem(it)
            self._entries.append(entry)
        if not self._entries:
            return
        self.list.setCurrentItem(self.list.topLevelItem(max(0, min(start, len(self._entries) - 1))))
        for c in (0, 1):
            self.list.resizeColumnToContents(c)
        self.list.setColumnWidth(0, self.list.columnWidth(0) + 24)
        row_h = max(self.list.sizeHintForRow(0), self.list.fontMetrics().height() + 8)
        par = self.parentWidget()
        w = max(self.list.columnWidth(0) + self.list.columnWidth(1) + 40, self.hint.sizeHint().width() + 30)
        w = min(max(w, 300), max(300, par.width() - 80))
        room = max(3, (par.height() - 160) // max(1, row_h))
        rows_shown = min(14, len(self._entries), room)
        self.list.setFixedHeight(rows_shown * row_h + 2 * self.list.frameWidth() + 2)
        self.layout().activate()
        self.resize(w, self.sizeHint().height())
        self.move((par.width() - self.width()) // 2, max(40, (par.height() - self.height()) // 3))
        self.show()
        self.raise_()
        self.grabKeyboard()
        self.grabMouse()  # (a click elsewhere closes it)

    def mousePressEvent(self, e) -> None:
        """A click on a row shows that page; a click anywhere else closes the list."""
        gp = e.globalPosition().toPoint()
        vp = self.list.viewport()
        lp = vp.mapFromGlobal(gp)
        if vp.rect().contains(lp):
            it = self.list.itemAt(lp)
            if it is not None:
                self._choose(it)
            return
        if not self.rect().contains(self.mapFromGlobal(gp)):
            self.cancel()

    def rows_text(self) -> list:
        """The rows' texts (name, project), in order (tests)."""
        return [
            (self.list.topLevelItem(k).text(0), self.list.topLevelItem(k).text(1))
            for k in range(self.list.topLevelItemCount())
        ]

    def current_row(self) -> int:
        it = self.list.currentItem()
        return self.list.indexOfTopLevelItem(it) if it is not None else -1

    def step(self, d: int) -> None:
        n = self.list.topLevelItemCount()
        if n:
            self.list.setCurrentItem(self.list.topLevelItem((self.current_row() + d) % n))

    def current_entry(self):
        k = self.current_row()
        return self._entries[k] if 0 <= k < len(self._entries) else None

    def _choose(self, item=None) -> None:
        if item is not None:
            self.list.setCurrentItem(item)
        entry = self.current_entry()
        self.close_switcher()
        if entry is not None:
            self.chosen.emit(entry)

    def accept(self) -> None:
        self._choose()

    def cancel(self) -> None:
        self.close_switcher()
        self.cancelled.emit()

    def close_switcher(self) -> None:
        self.releaseKeyboard()
        self.releaseMouse()
        self.hide()

    def keyPressEvent(self, e) -> None:
        k = e.key()
        if k == QtCore.Qt.Key_Escape:
            self.cancel()
        elif k in (QtCore.Qt.Key_Tab, QtCore.Qt.Key_Down):
            self.step(1)
        elif k in (QtCore.Qt.Key_Backtab, QtCore.Qt.Key_Up):
            self.step(-1)
        elif k in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            self.accept()
        e.accept()

    def keyReleaseEvent(self, e) -> None:
        if e.key() == QtCore.Qt.Key_Control:
            self.accept()
        e.accept()

    def hideEvent(self, e) -> None:
        self.releaseKeyboard()
        self.releaseMouse()
        super().hideEvent(e)


# ------------------------------------------------------------------------ the list button
class OpenPagesMenu(QtWidgets.QMenu):
    """Every open tab with a search field over them; typing filters, Enter shows the first
    match. ``rows()`` -> [(name, project, kind, dirty, current, show)] is asked each time the
    menu opens; ``show(i)`` shows tab i."""

    def __init__(self, rows, show, parent=None):
        super().__init__(parent)
        self.setObjectName("OpenPagesMenu")
        self._rows, self._show = rows, show
        from ..style.theme import SearchField

        self.search = SearchField("Search open pages", self, delay_ms=0)
        box = QtWidgets.QWidget(self)
        bl = QtWidgets.QHBoxLayout(box)
        bl.setContentsMargins(_sp(1), _sp(1), _sp(1), _sp(1))
        bl.addWidget(self.search)
        wa = QtWidgets.QWidgetAction(self)
        wa.setDefaultWidget(box)
        self.addAction(wa)
        self.addSeparator()
        self._page_actions = []
        self.search.textChanged.connect(self.filter)
        self.search.returnPressed.connect(self._enter)
        self.aboutToShow.connect(self.fill)

    def fill(self) -> None:
        for a in self._page_actions:
            self.removeAction(a)
            a.deleteLater()
        self._page_actions = []
        dpr = self.devicePixelRatioF() or 1.0
        for i, (name, project, kind, dirty, current) in enumerate(self._rows()):
            # the current page: a tick in place of its kind icon, and its name in bold
            a = QtGui.QAction(
                tick_icon(dpr) if current else kind_icon(kind, dpr),
                name + ("  •" if dirty else "") + ("\t" + project if project else ""),
                self,
            )
            a.setCheckable(True)
            a.setChecked(current)
            if current:
                f = QtGui.QFont(self.font())
                f.setBold(True)
                a.setFont(f)
            a.setIconVisibleInMenu(True)
            a.setData(i)
            a.setToolTip("%s%s" % (name, (" (" + project + ")") if project else ""))
            a.triggered.connect(lambda _=False, k=i: self._show(k))
            self.addAction(a)
            self._page_actions.append(a)
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.filter("")
        QtCore.QTimer.singleShot(0, self, self.search.setFocus)

    def filter(self, text: str) -> None:
        t = (text or "").strip().lower()
        for a in self._page_actions:
            a.setVisible(not t or t in a.text().lower())

    def visible_rows(self) -> list:
        return [a for a in self._page_actions if a.isVisible()]

    def _enter(self) -> None:
        rows = self.visible_rows()
        if rows:
            k = rows[0].data()
            self.close()
            self._show(k)


def build_tab_strip(win, bar: DocTabBar) -> QtWidgets.QWidget:
    """The tab bar with, after it, the 'Open pages' list button and the '+' button (its
    default action is File > New…; its menu: a new page from the last template, Open…, Quick
    Open…)."""
    strip = QtWidgets.QWidget(win)
    strip.setObjectName("DocTabStrip")
    lay = QtWidgets.QHBoxLayout(strip)
    lay.setContentsMargins(0, 0, _sp(1), 0)
    lay.setSpacing(0)
    lay.addWidget(bar, 1)
    lst = _GlyphButton("chevron", strip)
    lst.setObjectName("OpenPagesButton")
    lst.setToolTip("Open pages")
    lst.setAccessibleName("Open pages")
    lst.setPopupMode(QtWidgets.QToolButton.InstantPopup)
    menu = OpenPagesMenu(win.tab_rows, win.show_tab, lst)
    lst.setMenu(menu)
    lst.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0px; }")
    plus = _GlyphButton("plus", strip)
    plus.setObjectName("NewTabButton")
    # (File > New…: the button runs that command; its menu holds the other ways to start)
    plus.clicked.connect(lambda: win.act_new.trigger())
    plus.setToolTip(
        "New page or library element (%s)" % win.act_new.shortcut().toString(QtGui.QKeySequence.NativeText)
    )
    plus.setAccessibleName("New")
    plus.setPopupMode(QtWidgets.QToolButton.MenuButtonPopup)
    plus.setMinimumWidth(36)  # (the arrow of its menu next to the '+')
    pm = QtWidgets.QMenu(plus)
    pm.addAction(win.act_new_from_last)
    pm.addSeparator()
    pm.addAction(win.act_open)
    pm.addAction(win.act_quick_open)
    plus.setMenu(pm)
    lay.addWidget(lst)
    lay.addWidget(plus)
    bar._strip = strip
    win.tab_list_button, win.tab_list_menu, win.tab_new_button = lst, menu, plus
    strip.setVisible(bar.isVisibleTo(win) if bar.parent() is not None else False)
    return strip


# ------------------------------------------------------------------------ the Settings rows
class TabSettingsRows:
    """The Settings window's rows for this module's settings: the largest opening zoom
    (Canvas), the tab close buttons and compact pinned tabs (Appearance), what opens on start
    and the Ctrl+Tab order (Pages and libraries)."""

    KEYS = {
        "view/open_zoom_max": "200",
        "view/open_zoom_max_element": "400",
        "ui/tab_close_button": "hover",
        "ui/ctrl_tab_order": "mru",
        "opt/startup": "session",
    }

    def __init__(self):
        def combo(items, key, tip=""):
            cb = QtWidgets.QComboBox()
            for v, label in items:
                cb.addItem(label, v)
            if tip:
                cb.setToolTip(tip)
            return cb

        self.zoom_max = combo(
            OPEN_ZOOM_MAX,
            "view/open_zoom_max",
            "A small page or popup opens at this zoom at most. Fit Page still fits it",
        )
        self.zoom_max_element = combo(
            OPEN_ZOOM_MAX, "view/open_zoom_max_element", "A symbol or genie opens at this zoom at most"
        )
        self.close_buttons = combo(
            TAB_CLOSE_BUTTONS, "ui/tab_close_button", "A tab with unsaved changes shows a dot"
        )
        self.compact = QtWidgets.QCheckBox("Show pinned tabs with a short name")
        self.compact.setToolTip("The full name is in the tab's tool tip")
        self.startup = combo(STARTUP, "opt/startup", "A page named on the command line always opens")
        self.ctrl_tab = combo(
            CTRL_TAB_ORDER,
            "ui/ctrl_tab_order",
            "Most recently used: hold Ctrl and press Tab to pick from a list",
        )
        self._combos = {
            "view/open_zoom_max": self.zoom_max,
            "view/open_zoom_max_element": self.zoom_max_element,
            "ui/tab_close_button": self.close_buttons,
            "ui/ctrl_tab_order": self.ctrl_tab,
            "opt/startup": self.startup,
        }
        self.load()

    def canvas_rows(self, section) -> None:
        section.row("Largest zoom when a page opens:", self.zoom_max)
        section.row("Largest zoom when an element opens:", self.zoom_max_element)

    def appearance_rows(self, page):
        """The Document tabs section (returned: the window's other tab rows join it)."""
        s = page.section("Document tabs")
        s.row("Tab close buttons:", self.close_buttons)
        s.check(self.compact)
        return s

    def library_rows(self, page):
        """The Starting and switching pages section (returned, as appearance_rows)."""
        s = page.section("Starting and switching pages")
        s.row("On start:", self.startup)
        s.row("Ctrl+Tab order:", self.ctrl_tab)
        return s

    def load(self, get=None) -> None:
        st = app_settings()
        get = get or (lambda key, default: st.value(key, default))
        for key, cb in self._combos.items():
            v = str(get(key, self.KEYS[key]) or self.KEYS[key])
            cb.setCurrentIndex(max(0, cb.findData(v)))
        v = get("ui/pinned_tab_compact", True)
        self.compact.setChecked(v not in (False, "false", "0", 0))

    def defaults(self) -> None:
        self.load(lambda key, default: default)

    def choices(self) -> dict:
        out = {key: cb.currentData() for key, cb in self._combos.items()}
        out["ui/pinned_tab_compact"] = self.compact.isChecked()
        return out
