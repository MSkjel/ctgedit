"""The start page shown when no page is open."""

from __future__ import annotations
from ..style.theme import sp as _sp  # (the spacing scale)

import os
from typing import List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ..browser.thumbs import CARD_SIZE
from ..prefs.settings import MRU_RANGE, app_settings, whole
from ..style.theme import COLORS


def max_cards() -> int:
    """The recent pages shown: as many as File > Recent Pages keeps (Settings > Pages and
    libraries > Recent pages kept)."""
    return whole("opt/mru_max", MRU_RANGE)


def _qss() -> str:
    return (
        """
QWidget#StartPage { background: %(bg)s; }
QLabel#title { color: %(head)s; font: 600 20px 'Segoe UI'; }
QLabel#dim { color: %(text_dim)s; font: 12px 'Segoe UI'; }
QPushButton#big {
    background: %(selection)s; color: %(on_selection)s; border: none; border-radius: 4px;
    padding: 8px 18px; font: 13px 'Segoe UI';
}
QPushButton#big:hover { background: %(selection_hover)s; }
QPushButton#big[kbdFocus="true"] { border: 2px solid %(accent)s; padding: 6px 16px; }
QToolButton#card {
    background: %(card)s; color: %(text)s; border: 1px solid %(border)s;
    border-radius: 6px; padding: 6px; font: 12px 'Segoe UI';
}
QToolButton#card:hover { border: 1px solid %(accent)s; background: %(hover)s; }
QToolButton#card[kbdFocus="true"] { border: 2px solid %(accent)s; padding: 5px; }
"""
        % COLORS
    )


def recent_pages(limit: Optional[int] = None) -> List[str]:
    """The recent pages that are still there (``.ctg`` files), newest first; at most
    ``limit`` (None: :func:`max_cards`, the Recent pages kept setting)."""
    try:
        mru = [str(p) for p in (app_settings().value("mru", [], type=list) or [])]
    except Exception:  # noqa: BLE001 -- a garbled setting must not break the start page
        mru = []
    out = []
    for p in mru:
        if p.lower().endswith(".ctg") and os.path.isfile(p) and p not in out:
            out.append(p)
    return out[: max_cards() if limit is None else limit]


def remove_recent(path: str) -> None:
    """Take ``path`` off the recent pages list (File > Recent Pages too)."""
    st = app_settings()
    try:
        mru = [str(p) for p in (st.value("mru", [], type=list) or [])]
    except Exception:  # noqa: BLE001
        mru = []
    key = os.path.normcase(os.path.abspath(path))
    st.setValue("mru", [p for p in mru if os.path.normcase(os.path.abspath(p)) != key])
    st.sync()


def _thumb_for(path: str) -> QtGui.QPixmap:
    try:
        from ...format.pagesummary import read_page_summary

        png = read_page_summary(path).thumbnail_png()
    except Exception:  # noqa: BLE001
        png = b""
    w, h = CARD_SIZE.width(), CARD_SIZE.height()
    img = QtGui.QImage.fromData(png, "PNG") if png else QtGui.QImage()
    box = QtGui.QPixmap(w, h)
    box.fill(QtGui.QColor(COLORS["deep"]))
    if not img.isNull():
        # every card the same size: the page fitted into the card
        pm = QtGui.QPixmap.fromImage(img).scaled(
            w, h, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
        )
        pt = QtGui.QPainter(box)
        pt.drawPixmap((w - pm.width()) // 2, (h - pm.height()) // 2, pm)
        pt.end()
    return box


class StartPage(QtWidgets.QWidget):
    """Overlay on the canvas viewport; hidden while a page is open."""

    openRequested = QtCore.Signal(str)  # a recent page's path
    browseRequested = QtCore.Signal()
    newRequested = QtCore.Signal()

    def __init__(self, viewport: QtWidgets.QWidget):
        super().__init__(viewport)
        self.setObjectName("StartPage")
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.setStyleSheet(_qss())
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(_sp(5), _sp(5), _sp(5), _sp(5))
        outer.setSpacing(_sp(3))
        title = QtWidgets.QLabel("ctgedit")
        title.setObjectName("title")
        outer.addWidget(title)
        sub = QtWidgets.QLabel("Open a page from a project, or start a new one.")
        sub.setObjectName("dim")
        outer.addWidget(sub)
        row = QtWidgets.QHBoxLayout()
        b_open = QtWidgets.QPushButton("Open Page…  (Ctrl+O)")
        b_open.setObjectName("big")
        b_open.clicked.connect(self.browseRequested)
        b_new = QtWidgets.QPushButton("New…  (Ctrl+N)")
        b_new.setObjectName("big")
        b_new.clicked.connect(self.newRequested)
        row.addWidget(b_open)
        row.addWidget(b_new)
        row.addStretch(1)
        outer.addLayout(row)
        self._recent_label = QtWidgets.QLabel("Recent pages")
        self._recent_label.setObjectName("dim")
        outer.addSpacing(10)
        outer.addWidget(self._recent_label)
        self._grid_host = QtWidgets.QWidget(self)
        self._grid = QtWidgets.QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(_sp(3))
        scroll = QtWidgets.QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        scroll.setWidget(self._grid_host)
        outer.addWidget(scroll, 1)
        self._cards: List[QtWidgets.QToolButton] = []
        viewport.installEventFilter(self)
        self.setGeometry(viewport.rect())

    def _theme_changed(self) -> None:
        # ui/style/theme calls this on a theme change: the colours are COLORS tokens
        self.setStyleSheet(_qss())

    def eventFilter(self, obj, ev):
        if obj is self.parentWidget() and ev.type() == QtCore.QEvent.Resize:
            self.setGeometry(obj.rect())
            if self.isVisible():
                self._layout_cards()
        return False

    def refresh(self) -> None:
        while self._grid.count():
            it = self._grid.takeAt(0)
            if it.widget() is not None:
                it.widget().deleteLater()
        paths = recent_pages()
        self._recent_label.setVisible(bool(paths))
        self._cards = []
        for path in paths:
            b = QtWidgets.QToolButton(self._grid_host)
            b.setObjectName("card")
            b.setToolButtonStyle(QtCore.Qt.ToolButtonTextUnderIcon)
            pm = _thumb_for(path)
            b.setIcon(QtGui.QIcon(pm))
            b.setIconSize(pm.size())
            b.setText(
                "%s\n%s"
                % (os.path.splitext(os.path.basename(path))[0], os.path.basename(os.path.dirname(path)))
            )
            b.setToolTip(path)
            b.setAccessibleName(os.path.splitext(os.path.basename(path))[0])
            b.setProperty("page_path", path)
            b.clicked.connect(lambda _=False, p=path: self.openRequested.emit(p))
            b.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
            b.customContextMenuRequested.connect(lambda pos, btn=b, p=path: self._card_menu(btn, p, pos))
            self._cards.append(b)
        self._layout_cards()

    def card_menu(self, path: str) -> QtWidgets.QMenu:
        """The right-click menu of a recent page's card."""
        m = QtWidgets.QMenu(self)
        m.addAction("Remove from Recent", lambda: self._remove(path))

        def reveal():
            from ..browser.dialog import reveal_in_explorer

            reveal_in_explorer(path)

        m.addAction("Show in Explorer", reveal)
        return m

    def _card_menu(self, btn: QtWidgets.QToolButton, path: str, pos: QtCore.QPoint) -> None:
        self.card_menu(path).exec(btn.mapToGlobal(pos))

    def _remove(self, path: str) -> None:
        remove_recent(path)
        self.refresh()

    def _layout_cards(self) -> None:
        cards = self._cards
        for b in cards:
            self._grid.removeWidget(b)
        w = max(self.width(), self.parentWidget().width() if self.parentWidget() else 0)
        cols = max(1, (w - 110) // (CARD_SIZE.width() + 32))
        for k, b in enumerate(cards):
            self._grid.addWidget(b, k // cols, k % cols)
        for c in range(max(cols, self._grid.columnCount())):
            self._grid.setColumnStretch(c, 0)
        self._grid.setColumnStretch(cols, 1)
        self._grid.setRowStretch(len(cards) // cols + 1, 1)
