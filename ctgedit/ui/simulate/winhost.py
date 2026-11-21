"""Show windows the page's Cicode opens, the way the runtime does."""

from __future__ import annotations
from ..style.theme import sp as _sp

from typing import Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

MODE_PAGE_CHILD = 0x1
MODE_WINDOW_CHILD = 0x2
MODE_NO_CAPTION = 0x10
MODE_KEEP_SIZE = 0x400
MODE_FREE_ASPECT = 0x1000
MODE_PINNED = 0x80000

TAB, EMBEDDED, POPUP = "tab", "embedded", "popup"


def _int(v, default: int = 0) -> int:
    try:
        return int(v)
    except TypeError, ValueError:
        return default


def placement(w: dict, tab_per_window: bool = False) -> str:
    """How window ``w`` (a windowsChanged entry) is shown: TAB (Open each window in a tab of
    its own), EMBEDDED (a pinned child: inside its parent) or POPUP."""
    if tab_per_window:
        return TAB
    mode = _int(w.get("mode"))
    if mode & MODE_PINNED and _int(w.get("parent"), -1) >= 0:
        return EMBEDDED
    return POPUP


def is_child(w: dict) -> bool:
    """A page child or window child (WinNewAt mode 1 / 2): its (x, y) is in its parent
    window's page."""
    return (
        bool(_int(w.get("mode")) & (MODE_PAGE_CHILD | MODE_WINDOW_CHILD)) and _int(w.get("parent"), -1) >= 0
    )


def window_rect(w: dict) -> QtCore.QRectF:
    """(x, y, w, h) of window ``w``; a zero size stays zero (the page's size then counts)."""
    return QtCore.QRectF(
        _int(w.get("x")), _int(w.get("y")), max(0, _int(w.get("w"))), max(0, _int(w.get("h")))
    )


def content_scale(w: dict, page_w: float, page_h: float) -> Tuple[float, float]:
    """The scale of a window's page in its window (the runtime fits the page to the window
    unless mode 1024 keeps its size; mode 4096 lets the two axes differ)."""
    mode = _int(w.get("mode"))
    ww, wh = _int(w.get("w")), _int(w.get("h"))
    if mode & MODE_KEEP_SIZE or ww <= 0 or wh <= 0 or page_w <= 0 or page_h <= 0:
        return 1.0, 1.0
    sx, sy = ww / float(page_w), wh / float(page_h)
    if not mode & MODE_FREE_ASPECT:
        sx = sy = min(sx, sy)
    return sx, sy


def view_size(w: dict, page_w: float, page_h: float) -> Tuple[float, float]:
    """The window's size in page pixels: its own (w, h), else its page's."""
    ww, wh = _int(w.get("w")), _int(w.get("h"))
    return (float(ww) if ww > 0 else float(page_w), float(wh) if wh > 0 else float(page_h))


class PopupFrame(QtWidgets.QFrame):
    """A window the page's Cicode opened that is not pinned to its parent: a frameless window
    over the Simulate window with a thin caption (its title, drag to move it, Close = the
    window closes in the simulator) around the simulated page."""

    closeRequested = QtCore.Signal()
    #: the user dragged the size grip: the page's new size on the screen (the view's)
    resizedByUser = QtCore.Signal(QtCore.QSize)
    #: a double-click on the caption: back to the zoom of the page it opened over
    resetZoom = QtCore.Signal()

    CAPTION = 22

    def __init__(self, view: QtWidgets.QWidget, title: str, parent=None):
        super().__init__(parent, QtCore.Qt.Tool | QtCore.Qt.FramelessWindowHint)
        self.setObjectName("SimulatePopup")
        self.setFrameShape(QtWidgets.QFrame.Box)
        self.setLineWidth(1)
        self.setAccessibleName("Simulated window")
        self.view = view
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)
        self.caption = QtWidgets.QWidget(self)
        # (the look of a panel's title bar in ctgedit, ui/panels/docking.DockTitleBar)
        self.caption.setObjectName("DockTitleBar")
        self.caption.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.caption.setFixedHeight(self.CAPTION)
        self.caption.setCursor(QtCore.Qt.SizeAllCursor)
        row = QtWidgets.QHBoxLayout(self.caption)
        row.setContentsMargins(_sp(1), 0, _sp(0), 0)
        row.setSpacing(_sp(1))
        self.title = QtWidgets.QLabel(title, self.caption)
        self.title.setAccessibleName("Window title")
        # (a narrow popup keeps its width: the title is cut, not the window widened)
        self.title.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        self.title.setMinimumWidth(0)
        row.addWidget(self.title, 1)
        self.close_button = QtWidgets.QToolButton(self.caption)
        self.close_button.setAutoRaise(True)
        self.close_button.setFocusPolicy(QtCore.Qt.NoFocus)
        self.close_button.setIconSize(QtCore.QSize(16, 16))
        self._close_glyph()
        self.close_button.setToolTip("Close this window in the simulation")
        self.close_button.setAccessibleName("Close window")
        self.close_button.clicked.connect(self.closeRequested.emit)
        row.addWidget(self.close_button)
        lay.addWidget(self.caption)
        view.setParent(self)
        lay.addWidget(view, 1)
        # the corner grip: drag it to scale the window (its page fits the new size)
        self.grip = QtWidgets.QSizeGrip(self)
        self.grip.setToolTip("Drag to scale this window")
        self.grip.resize(12, 12)
        self.grip.installEventFilter(self)
        self._gripping = False
        self.caption.setToolTip(
            "Drag to move. Double-click to follow the page's zoom again. "
            "The mouse wheel over the window scales it."
        )
        self.caption.installEventFilter(self)
        self.title.installEventFilter(self)
        self._drag: Optional[QtCore.QPoint] = None
        #: the user moved the window: a later position from the simulation does not move it
        self.moved_by_user = False
        self.placed: Optional[Tuple[int, int, int, int]] = None

    def set_title(self, text: str) -> None:
        self.title.setText(text)

    def _close_glyph(self) -> None:
        """Close as a panel's title bar draws it, in the theme's text colour."""
        from ..panels.docking import glyph

        colour = self.palette().color(QtGui.QPalette.WindowText)
        dpr = self.devicePixelRatioF() or 1.0
        self.close_button.setIcon(QtGui.QIcon(glyph("close", colour, dpr=dpr)))

    def changeEvent(self, ev):  # noqa: N802 -- Qt naming
        if (
            ev.type() in (QtCore.QEvent.PaletteChange, QtCore.QEvent.StyleChange)
            and getattr(self, "close_button", None) is not None
        ):
            self._close_glyph()  # (a theme switch: the glyph in the new colour)
        super().changeEvent(ev)

    def place(self, top_left: QtCore.QPoint, size: QtCore.QSize) -> None:
        """The window's top-left corner on the screen (WinNewAt's x, y: the corner of the
        window, its caption included) and its page's size on the screen; kept on the
        screen."""
        m = self.contentsMargins()
        w = size.width() + m.left() + m.right() + self.lineWidth() * 2
        h = size.height() + self.CAPTION + m.top() + m.bottom() + self.lineWidth() * 2
        x, y = top_left.x(), top_left.y()
        scr = QtGui.QGuiApplication.screenAt(top_left) or QtGui.QGuiApplication.primaryScreen()
        if scr is not None:
            a = scr.availableGeometry()
            x = max(a.left(), min(x, a.right() + 1 - w))
            y = max(a.top(), min(y, a.bottom() + 1 - h))
        key = (x, y, w, h)
        if key == self.placed:
            return
        self.placed = key
        if self.moved_by_user:
            self.resize(w, h)
        else:
            self.setGeometry(x, y, w, h)

    def resizeEvent(self, ev):  # noqa: N802 -- Qt naming
        super().resizeEvent(ev)
        g = getattr(self, "grip", None)
        if g is not None:
            g.move(self.width() - g.width() - 1, self.height() - g.height() - 1)
            g.raise_()
        if getattr(self, "_gripping", False):
            self.resizedByUser.emit(self.view.size())

    def eventFilter(self, obj, ev):  # noqa: N802 -- Qt naming
        t = ev.type()
        if obj is getattr(self, "grip", None):
            if t == QtCore.QEvent.MouseButtonPress:
                self._gripping = True
            elif t == QtCore.QEvent.MouseButtonRelease:
                self._gripping = False
            return False
        if t == QtCore.QEvent.MouseButtonDblClick and ev.button() == QtCore.Qt.LeftButton:
            self._drag = None
            self.resetZoom.emit()
            return True
        if t == QtCore.QEvent.MouseButtonPress and ev.button() == QtCore.Qt.LeftButton:
            self._drag = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()
            return True
        if t == QtCore.QEvent.MouseMove and self._drag is not None:
            self.move(ev.globalPosition().toPoint() - self._drag)
            self.moved_by_user = True
            return True
        if t == QtCore.QEvent.MouseButtonRelease and self._drag is not None:
            self._drag = None
            return True
        return super().eventFilter(obj, ev)
