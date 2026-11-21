"""A layout that wraps its widgets onto more rows when it runs out of width."""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets


class FlowLayout(QtWidgets.QLayout):
    def __init__(self, parent=None, spacing: int = 4):
        super().__init__(parent)
        self._items: list = []
        #: heightForWidth per width, until the layout is invalidated (Qt asks many times)
        self._hfw: dict = {}
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    # -- QLayout -------------------------------------------------------------------------
    def addItem(self, item) -> None:
        self._items.append(item)
        self._hfw.clear()

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i: int):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i: int):
        self._hfw.clear()
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return QtCore.Qt.Orientations(QtCore.Qt.Orientation(0))

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        h = self._hfw.get(width)
        if h is None:
            h = self._hfw[width] = self._lay_out(QtCore.QRect(0, 0, width, 0), apply=False)
        return h

    def invalidate(self) -> None:
        self._hfw.clear()
        super().invalidate()

    def setGeometry(self, rect) -> None:
        super().setGeometry(rect)
        self._lay_out(rect, apply=True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QtCore.QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return size + QtCore.QSize(m.left() + m.right(), m.top() + m.bottom())

    # -- the layout ----------------------------------------------------------------------
    def _lay_out(self, rect, apply: bool) -> int:
        m = self.contentsMargins()
        r = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line = r.x(), r.y(), 0
        sp = self.spacing()
        for it in self._items:
            w = it.widget()
            if w is not None and w.isHidden():
                continue
            hint = it.sizeHint()
            nx = x + hint.width() + sp
            if nx - sp > r.right() + 1 and line > 0:
                x, y = r.x(), y + line + sp
                nx = x + hint.width() + sp
                line = 0
            if apply:
                it.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), hint))
            x = nx
            line = max(line, hint.height())
        return y + line - rect.y() + m.bottom()
