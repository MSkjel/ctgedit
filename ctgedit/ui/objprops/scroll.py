"""Layout helpers for tabbed property windows."""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets


def _screen_size(widget: QtWidgets.QWidget) -> QtCore.QSize:
    screen = None
    try:
        screen = widget.screen()
    except RuntimeError:
        screen = None
    screen = screen or QtWidgets.QApplication.primaryScreen()
    if screen is None:
        return QtCore.QSize(16777215, 16777215)
    return screen.availableGeometry().size()


#: the available size of the screen a widget is on (public name)
screen_size = _screen_size


class PageScroll(QtWidgets.QScrollArea):
    """A property page in a frameless scroll area (widgetResizable)."""

    def __init__(self, page: QtWidgets.QWidget, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.setWidget(page)

    def page(self) -> QtWidgets.QWidget:
        return self.widget()

    def sizeHint(self) -> QtCore.QSize:  # noqa: N802 -- Qt API
        """The page's whole size (QScrollArea caps it at 36 x 24 lines)."""
        w = self.widget()
        if w is None:
            return super().sizeHint()
        s = w.sizeHint().expandedTo(w.minimumSizeHint())
        f = 2 * self.frameWidth()
        return (s + QtCore.QSize(f, f)).boundedTo(_screen_size(self))


def scroll_page(page: QtWidgets.QWidget) -> PageScroll:
    """``page`` in a :class:`PageScroll` (the widget a tab widget holds)."""
    return PageScroll(page)


def unwrap_page(w):
    """The page a tab holds: the scroll area's page, else ``w`` itself."""
    if isinstance(w, PageScroll):
        return w.widget()
    return w


def page_scroll_of(widget):
    """The nearest :class:`PageScroll` around ``widget`` (None when there is none)."""
    w = widget.parentWidget() if widget is not None else None
    while w is not None:
        if isinstance(w, PageScroll):
            return w
        w = w.parentWidget()
    return None


def ensure_visible(widget) -> None:
    """Scroll the page holding ``widget`` so that it shows."""
    scroll = page_scroll_of(widget)
    if scroll is not None:
        try:
            scroll.ensureWidgetVisible(widget)
        except RuntimeError:
            pass


class FitTabWidget(QtWidgets.QTabWidget):
    """A tab widget wide enough to show every tab title."""

    def _titles_width(self) -> int:
        bar = self.tabBar()
        if bar is None or not self.count():
            return 0
        return (
            bar.sizeHint().width()
            + 2 * self.style().pixelMetric(QtWidgets.QStyle.PM_DefaultFrameWidth, None, self)
            + 8
        )

    def sizeHint(self) -> QtCore.QSize:  # noqa: N802 -- Qt API
        s = super().sizeHint()
        need = self._titles_width()
        if not need:
            return s
        return QtCore.QSize(max(s.width(), need), s.height()).boundedTo(_screen_size(self))

    def minimumSizeHint(self) -> QtCore.QSize:  # noqa: N802 -- Qt API
        s = super().minimumSizeHint()
        need = self._titles_width()
        if not need:
            return s
        return QtCore.QSize(max(s.width(), min(need, _screen_size(self).width())), s.height())


class CurrentStack(QtWidgets.QStackedWidget):
    """A stacked widget as big as the page it shows (a QStackedWidget is as big as its
    biggest page)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.currentChanged.connect(lambda _i: self.updateGeometry())

    def sizeHint(self) -> QtCore.QSize:  # noqa: N802 -- Qt API
        w = self.currentWidget()
        if w is None:
            return super().sizeHint()
        return w.sizeHint().expandedTo(w.minimumSizeHint()).expandedTo(w.minimumSize())

    def minimumSizeHint(self) -> QtCore.QSize:  # noqa: N802 -- Qt API
        w = self.currentWidget()
        if w is None:
            return super().minimumSizeHint()
        return w.minimumSizeHint().expandedTo(w.minimumSize())


def note(label: QtWidgets.QLabel) -> QtWidgets.QLabel:
    """A page's note: word-wrapped, never wider than the page gives it."""
    label.setWordWrap(True)
    label.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
    return label


def lines_height(edit: QtWidgets.QPlainTextEdit, lines: int) -> int:
    """The height of ``lines`` text lines in ``edit`` (frame and margins in)."""
    fm = edit.fontMetrics()
    margin = int(edit.document().documentMargin() * 2) + edit.frameWidth() * 2
    return fm.lineSpacing() * max(1, int(lines)) + margin + 4


class LinesMixin:
    """A multi-line box that opens ``min_lines`` high, never gets lower and
    takes the extra height of its window (vertical Expanding)."""

    def _init_lines(self, min_lines: int) -> None:
        self._min_lines = max(1, int(min_lines))
        pol = self.sizePolicy()
        pol.setVerticalPolicy(QtWidgets.QSizePolicy.Expanding)
        self.setSizePolicy(pol)
        self._apply_lines()

    def _lines_px(self) -> int:
        return lines_height(self, self._min_lines)

    def _apply_lines(self) -> None:
        self.setMinimumHeight(self._lines_px())

    def min_lines(self) -> int:
        return self._min_lines

    def sizeHint(self) -> QtCore.QSize:  # noqa: N802 -- Qt API
        s = QtWidgets.QPlainTextEdit.sizeHint(self)
        return QtCore.QSize(s.width(), self._lines_px())

    def minimumSizeHint(self) -> QtCore.QSize:  # noqa: N802 -- Qt API
        s = QtWidgets.QPlainTextEdit.minimumSizeHint(self)
        return QtCore.QSize(s.width(), self._lines_px())

    def changeEvent(self, e) -> None:  # noqa: N802 -- Qt API
        QtWidgets.QPlainTextEdit.changeEvent(self, e)
        if e.type() in (QtCore.QEvent.FontChange, QtCore.QEvent.StyleChange):
            self._apply_lines()


class LinesEdit(LinesMixin, QtWidgets.QPlainTextEdit):
    """A plain multi-line text box of at least ``min_lines`` lines."""

    def __init__(self, min_lines: int = 2, parent=None):
        QtWidgets.QPlainTextEdit.__init__(self, parent)
        self._init_lines(min_lines)
