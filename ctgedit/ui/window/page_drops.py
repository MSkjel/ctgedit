"""Pages and pictures dropped from Explorer."""

from __future__ import annotations

from ...infra.paths import path_key

from ..main_window_base import (
    QtCore,
    QtGui,
    QtWidgets,
    os,
)

from .files import (
    LIBRARY_SUFFIXES,
    PAGE_SUFFIXES,
)


class PageDrops:
    """Opening the pages and libraries dropped on the window."""

    # ------------------------------------------------ pages dropped from Explorer
    def _install_page_drop(self) -> None:
        """A page (.ctg) or a library dropped on the canvas opens; on the rest of the window
        too (dragEnterEvent / dropEvent below). The canvas takes it through its drop hooks
        (canvas_hooks) when they are there, else through an event filter on it."""
        try:
            from ..editor import canvas_hooks
        except ImportError:  # swallow-ok: w1-canvasnav's hooks not in this build: the filter
            canvas_hooks = None
        full = canvas_hooks is None or not hasattr(canvas_hooks, "register_drop_handler")
        if not full:
            canvas_hooks.register_drop_handler("pages", _hook_accept, _hook_drop, priority=100)
        self._page_drop_filter = _PageDropFilter(self, full)
        self.view.viewport().installEventFilter(self._page_drop_filter)

    def _show_drop_frame(self, pages, libs) -> None:
        f = self.__dict__.get("_drop_frame")
        if f is None:
            f = self._drop_frame = _DropFrame(self.view.viewport())
        text = self.drop_hint(pages, libs)
        f.show_over(text)
        if self.statusBar().currentMessage() != text:
            self.statusBar().showMessage(text, 3000)

    def _hide_drop_frame(self) -> None:
        f = self.__dict__.get("_drop_frame")
        if f is not None and f.isVisible():
            f.hide()

    @staticmethod
    def drop_hint(pages, libs) -> str:
        """'Drop to open Mixer' (the status line and the frame over the canvas)."""
        names = [os.path.splitext(os.path.basename(p))[0] for p in pages + libs]
        if len(names) == 1:
            return "Drop to open %s" % names[0]
        return "Drop to open %d files" % len(names)

    def open_dropped(self, pages, libs) -> None:
        """Pages dropped from Explorer open in tabs (a page that does not open is reported);
        a library opens the page browser on its elements."""
        self._hide_drop_frame()
        if pages:
            failed = self.open_files(pages)
            self._note_other_install([p for p in pages if p not in {f for f, _r in failed}])
        if libs:
            from ..browser import PageBrowserDialog

            r = PageBrowserDialog.get_open_target(self, libs[0], before_delete=self._before_delete_or_rename)
            if r is not None:
                if r.kind == "page":
                    self.open_page(r.path)
                elif r.element:
                    self._open_element(r.path, r.element)

    def _note_other_install(self, paths) -> None:
        """Opened pages outside the install Plant SCADA uses: said in the status bar (the
        project label says so too)."""
        try:
            from ...io.citect_config import live_user_dir

            live = live_user_dir()
        except Exception:  # noqa: BLE001  # swallow-ok: no install known, nothing to say
            return
        if not live:
            return
        key = path_key(live)
        other = [
            p for p in paths if os.path.normcase(os.path.dirname(os.path.dirname(os.path.abspath(p)))) != key
        ]
        if other:
            self.statusBar().showMessage(
                "%s is not in the active install. The Graphics Builder and the runtime use %s "
                "and do not see edits to it." % (os.path.basename(other[0]), live),
                10000,
            )

    def _tab_drag(self, e) -> bool:
        """A tab of an editor window's tab bar is being dragged."""
        from ..panels.doc_tabs import TAB_MIME, DocTabBar

        return (
            e.mimeData() is not None
            and e.mimeData().hasFormat(TAB_MIME)
            and isinstance(e.source(), DocTabBar)
        )

    def dragEnterEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        pages, libs = dropped_documents(e.mimeData())
        tab = self._tab_drag(e)
        if pages or libs or tab:
            e.setDropAction(QtCore.Qt.MoveAction if tab else QtCore.Qt.CopyAction)
            e.accept()
            if pages or libs:
                self.statusBar().showMessage(self.drop_hint(pages, libs), 3000)
            return
        super().dragEnterEvent(e)

    def dragMoveEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        pages, libs = dropped_documents(e.mimeData())
        if pages or libs or self._tab_drag(e):
            e.accept()
            return
        super().dragMoveEvent(e)

    def dropEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        if self._tab_drag(e):
            # another window's tab moves here (at the end); this window's own: nothing
            from ..panels.doc_tabs import dragged_tab_index

            k = dragged_tab_index(e.mimeData(), e.source())
            e.setDropAction(QtCore.Qt.MoveAction)
            e.accept()
            if k >= 0 and e.source().window() is not self:
                self._tab_dropped(e.source(), k, len(self._docs))
            return
        pages, libs = dropped_documents(e.mimeData())
        if pages or libs:
            e.setDropAction(QtCore.Qt.CopyAction)
            e.accept()
            QtCore.QTimer.singleShot(0, self, lambda p=pages, lb=libs: self.open_dropped(p, lb))
            return
        super().dropEvent(e)


def dropped_documents(mime) -> tuple:
    """(pages, libraries): the local .ctg files and library files of a drag."""
    pages, libs = [], []
    if mime is None or not mime.hasUrls():
        return pages, libs
    for url in mime.urls():
        if not url.isLocalFile():
            continue
        p = os.path.normpath(url.toLocalFile())
        ext = os.path.splitext(p)[1].lower()
        if ext in PAGE_SUFFIXES:
            pages.append(p)
        elif ext in LIBRARY_SUFFIXES and os.path.isfile(p):
            libs.append(p)
    return pages, libs


def _hook_window(view):
    w = view.window()
    return w if hasattr(w, "open_dropped") else None


def _hook_accept(view, event, scene_pos, unit):
    """canvas_hooks drop handler: a page or library dragged over the canvas (the hint)."""
    pages, libs = dropped_documents(event.mimeData())
    w = _hook_window(view) if pages or libs else None
    if w is None:
        return None
    w._show_drop_frame(pages, libs)
    return w.drop_hint(pages, libs)


def _hook_drop(view, event, scene_pos, unit) -> bool:
    """canvas_hooks drop handler: it opens (instead of the canvas's picture import)."""
    pages, libs = dropped_documents(event.mimeData())
    w = _hook_window(view) if pages or libs else None
    if w is None:
        return False
    w._hide_drop_frame()
    QtCore.QTimer.singleShot(0, w, lambda: w.open_dropped(pages, libs))
    return True


class _DropFrame(QtWidgets.QWidget):
    """The dashed accent frame over the canvas while a page dragged from Explorer is over it
    (with what a drop does)."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(QtCore.Qt.WA_NoSystemBackground, True)
        self.text = ""
        self.hide()

    def show_over(self, text: str) -> None:
        self.text = text
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self.update()

    def paintEvent(self, _e) -> None:
        from ..style.theme import COLORS

        p = QtGui.QPainter(self)
        accent = QtGui.QColor(COLORS["accent"])
        pen = QtGui.QPen(accent, 2, QtCore.Qt.DashLine)
        p.setPen(pen)
        fill = QtGui.QColor(accent)
        fill.setAlpha(28)
        p.setBrush(fill)
        p.drawRoundedRect(QtCore.QRectF(self.rect()).adjusted(4, 4, -4, -4), 8, 8)
        if self.text:
            from ..style.theme import font as _font

            f = _font("subtitle")  # (the type scale's, as the other headings)
            p.setFont(f)
            fm = QtGui.QFontMetrics(f)
            w = fm.horizontalAdvance(self.text) + 32
            h = fm.height() + 16
            r = QtCore.QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(COLORS["popup"]))
            p.drawRoundedRect(r, 6, 6)
            p.setPen(QtGui.QColor(COLORS["text"]))
            p.drawText(r, QtCore.Qt.AlignCenter, self.text)
        p.end()


class _PageDropFilter(QtCore.QObject):
    """On the canvas a page or library dragged from Explorer opens instead of being imported
    (pictures still import). ``full``: the filter takes the drag itself (no canvas hooks);
    else it only hides the frame when the drag leaves (the hook takes the drag)."""

    def __init__(self, win, full: bool):
        super().__init__(win)
        self._win = win
        self.full = full

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802 -- Qt naming
        t = ev.type()
        if t == QtCore.QEvent.DragLeave:
            self._win._hide_drop_frame()
            return False
        if t not in (QtCore.QEvent.DragEnter, QtCore.QEvent.DragMove, QtCore.QEvent.Drop):
            return False
        if self._win._tab_drag(ev):
            # a page tab dragged over the canvas: the window's own drop rule (another
            # window's tab moves here; its own stays)
            if t == QtCore.QEvent.Drop:
                self._win.dropEvent(ev)
            else:
                ev.setDropAction(QtCore.Qt.MoveAction)
                ev.accept()
            return True
        pages, libs = dropped_documents(ev.mimeData())
        if not (pages or libs):
            if t == QtCore.QEvent.Drop:
                self._win._hide_drop_frame()
            return False
        if t == QtCore.QEvent.Drop:
            self._win._hide_drop_frame()
            if not self.full:
                return False
            ev.setDropAction(QtCore.Qt.CopyAction)
            ev.accept()
            QtCore.QTimer.singleShot(
                0, self, self._win, lambda p=pages, lb=libs: self._win.open_dropped(p, lb)
            )
            return True
        if not self.full:
            return False
        ev.setDropAction(QtCore.Qt.CopyAction)
        ev.accept()
        self._win._show_drop_frame(pages, libs)
        return True
