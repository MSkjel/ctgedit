"""View > Navigator: a small map of the work area."""

from __future__ import annotations
from ..style.theme import sp as _sp  # (the spacing scale)

from PySide6 import QtCore, QtGui, QtWidgets

#: the map's size (logical px at 100 %): the View > Panels > Navigator dock's always; the
#: one over the canvas follows Settings > Canvas > Navigation > Navigator size (map_size)
MAP_W, MAP_H = 200, 120

# Settings > Canvas > Navigation: Navigator size (view/navigator_size) and Navigator corner
# (view/navigator_corner), set by ui/editor/canvas_options.apply_canvas_settings; medium and
# the bottom right are the defaults
MAP_SIZES = {"small": (150, 90), "medium": (MAP_W, MAP_H), "large": (280, 168)}
CORNERS = ("bottom_right", "bottom_left", "top_right", "top_left")
_PANEL = {"size": (MAP_W, MAP_H), "corner": "bottom_right"}


def map_size() -> tuple:
    """(w, h) of the map over the canvas at 100 % text size (the dock's: MAP_W x MAP_H)."""
    return _PANEL["size"]


def panel_corner() -> str:
    return _PANEL["corner"]


def set_panel_options(size=None, corner=None) -> None:
    """The navigator's size by name ('small' / 'medium' / 'large') and its corner (one of
    CORNERS); anything else: medium, the bottom right."""
    _PANEL["size"] = MAP_SIZES.get(str(size or ""), MAP_SIZES["medium"])
    _PANEL["corner"] = corner if corner in CORNERS else "bottom_right"


class MapSource(QtCore.QObject):
    """One cached picture of a canvas's work area, shared by its maps."""

    changed = QtCore.Signal()

    def __init__(self, view):
        super().__init__(view)
        self._view = view
        self._pixmap: QtGui.QPixmap | None = None
        self._work = QtCore.QRectF()
        self._box = (0, 0)  # the map size (100 %) the picture was made for
        self._dirty = True
        self._maps: list = []
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self.rebuild)
        self._scene = None
        self._watch_scene()
        swapped = getattr(view, "sceneSwapped", None)
        if swapped is not None:
            swapped.connect(self._scene_swapped)

    def _scene_swapped(self) -> None:
        """Another page shows (a tab switch): the picture of the one before goes at once
        and the new page's is made 300 ms later."""
        self._watch_scene()
        self._pixmap = None
        self._work = QtCore.QRectF()
        self.schedule()
        self.changed.emit()

    def add_map(self, m) -> None:
        self._maps.append(m)

    def _watch_scene(self) -> None:
        sc = self._view.scene()
        if sc is self._scene:
            return
        if self._scene is not None:
            try:
                self._scene.changed.disconnect(self.schedule)
            except RuntimeError, TypeError:  # swallow-ok: the old scene is gone
                pass
        self._scene = sc
        if sc is not None:
            sc.changed.connect(self.schedule)

    def schedule(self, regions=None) -> None:
        """The scene changed (``regions``: scene.changed's list; None = a tab switch): render
        again 300 ms after the last change. Not for a change only inside the in-place text
        editor, nor for an empty list (QGraphicsScene sends one after each real list: its
        queued emit with nothing left in it)."""
        if regions is not None and (not regions or self._inside_text_editor(regions)):
            return
        self._dirty = True
        self._timer.start()

    def _inside_text_editor(self, regions) -> bool:
        get = getattr(self._view, "text_edit_item", None)
        item = get() if get is not None else None
        if item is None:
            return False
        try:
            box = item.sceneBoundingRect().adjusted(-2, -2, 2, 2)
        except RuntimeError:  # swallow-ok: the editor item is being removed
            return False
        return all(box.contains(QtCore.QRectF(r)) for r in regions)

    def ensure(self) -> None:
        """A map shows: a picture made while none was shown, or made for smaller maps
        than the ones shown now, is made now (on the next turn of the event loop)."""
        need = self._picture_box()
        if self._dirty or self._pixmap is None or need[0] > self._box[0] or need[1] > self._box[1]:
            QtCore.QTimer.singleShot(0, self, self.rebuild)

    def _wanted(self) -> bool:
        return any(m.isVisible() for m in self._maps)

    def _picture_box(self) -> tuple:
        """(w, h) at 100 % the picture is made for: the largest map shown, at least the
        dock's MAP_W x MAP_H (a Large navigator over the canvas only while it shows)."""
        w, h = MAP_W, MAP_H
        for m in self._maps:
            if m.isVisible():
                mw, mh = m.picture_size()
                w, h = max(w, mw), max(h, mh)
        return w, h

    def work_rect(self) -> QtCore.QRectF:
        sc = self._view.scene()
        work = sc.work_rect() if sc is not None and hasattr(sc, "work_rect") else QtCore.QRectF()
        if work.isNull():
            return QtCore.QRectF()
        return work

    def pixmap(self, size: QtCore.QSize) -> QtGui.QPixmap | None:
        if self._view.scene() is not self._scene:
            self._scene_swapped()  # (a scene set without the signal)
        if self._dirty and not self._timer.isActive():
            self.rebuild()
        return self._pixmap

    def rebuild(self) -> None:
        self._watch_scene()
        if not self._wanted():
            return  # (rendered when a map shows again)
        sc = self._view.scene()
        work = self.work_rect()
        self._dirty = False
        if sc is None or work.isNull():
            self._pixmap = None
            self.changed.emit()
            return
        from ..style.theme import px

        dpr = 1.0
        try:
            dpr = float(self._view.devicePixelRatioF() or 1.0)
        except RuntimeError:  # swallow-ok: the canvas is being destroyed
            return
        # the work area's own shape, twice the largest map's size (sharp in the larger dock
        # too; never smaller than the dock's: Settings > Canvas > Navigator size is the map
        # over the canvas only)
        mw, mh = self._box = self._picture_box()
        fit = _fit(QtCore.QRectF(0, 0, px(mw) * 2, px(mh) * 2), work)
        w, h = max(1, int(fit.width())), max(1, int(fit.height()))
        pm = QtGui.QPixmap(int(w * dpr), int(h * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        target = QtCore.QRectF(0, 0, w, h)
        try:
            chrome = getattr(sc, "editor_chrome", None)
            if chrome is not None:
                with chrome():
                    sc.render(p, target, work, QtCore.Qt.KeepAspectRatio)
            else:
                sc.render(p, target, work, QtCore.Qt.KeepAspectRatio)
        finally:
            p.end()
        self._pixmap = pm
        self._work = QtCore.QRectF(work)
        self.changed.emit()


def _fit(box: QtCore.QRectF, work: QtCore.QRectF) -> QtCore.QRectF:
    """``work``'s shape scaled into ``box``, centred."""
    if work.width() <= 0 or work.height() <= 0:
        return QtCore.QRectF(box)
    s = min(box.width() / work.width(), box.height() / work.height())
    w, h = work.width() * s, work.height() * s
    return QtCore.QRectF(box.left() + (box.width() - w) / 2.0, box.top() + (box.height() - h) / 2.0, w, h)


class NavigatorMap(QtWidgets.QWidget):
    """The map of one canvas (see the module notes). ``panel``: the map of the navigator over
    the canvas (its size follows Settings > Canvas > Navigator size); else the dock's."""

    def __init__(self, view, source: MapSource, parent=None, *, panel: bool = False):
        super().__init__(parent)
        self._view = view
        self._src = source
        self._panel = bool(panel)
        self._drag: QtCore.QPointF | None = None  # the grab point's offset from the centre
        self.setObjectName("NavigatorMap")
        self.setAccessibleName("Navigator")
        self.setToolTip("Click or drag to move around the page. Wheel: zoom. Double-click: Fit Page")
        self.setMouseTracking(True)
        self.setMinimumSize(80, 50)
        source.add_map(self)
        source.changed.connect(self.update)
        view.zoomChanged.connect(self.update)
        view.horizontalScrollBar().valueChanged.connect(self.update)
        view.verticalScrollBar().valueChanged.connect(self.update)

    def picture_size(self) -> tuple:
        """(w, h) of the map at 100 % text size: the setting's over the canvas, MAP_W x
        MAP_H in the dock."""
        return map_size() if self._panel else (MAP_W, MAP_H)

    def sizeHint(self) -> QtCore.QSize:
        from ..style.theme import px

        mw, mh = self.picture_size()
        return QtCore.QSize(px(mw), px(mh))

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._src.ensure()

    # ---- mapping -------------------------------------------------------------------
    def _target(self) -> QtCore.QRectF:
        return _fit(QtCore.QRectF(self.rect()).adjusted(3, 3, -3, -3), self._src.work_rect())

    def to_scene(self, p: QtCore.QPointF) -> QtCore.QPointF:
        t, work = self._target(), self._src.work_rect()
        if t.width() <= 0 or work.isNull():
            return QtCore.QPointF()
        s = work.width() / t.width()
        return QtCore.QPointF(work.left() + (p.x() - t.left()) * s, work.top() + (p.y() - t.top()) * s)

    def to_map(self, r: QtCore.QRectF) -> QtCore.QRectF:
        t, work = self._target(), self._src.work_rect()
        if work.isNull() or work.width() <= 0:
            return QtCore.QRectF()
        s = t.width() / work.width()
        return QtCore.QRectF(
            t.left() + (r.left() - work.left()) * s,
            t.top() + (r.top() - work.top()) * s,
            r.width() * s,
            r.height() * s,
        )

    def visible_frame(self) -> QtCore.QRectF:
        """The part of the work area the canvas shows, in map coordinates."""
        v = self._view
        seen = v.mapToScene(v.viewport().rect()).boundingRect()
        return self.to_map(seen)

    # ---- painting ------------------------------------------------------------------
    def paintEvent(self, _e) -> None:
        from ..style.theme import color

        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        t = self._target()
        pm = self._src.pixmap(self.size())
        if pm is not None and not t.isNull():
            p.drawPixmap(t, pm, QtCore.QRectF(pm.rect()))
        frame = self.visible_frame().intersected(t.adjusted(-2, -2, 2, 2))
        if not frame.isNull():
            acc = QtGui.QColor(color("accent"))
            fill = QtGui.QColor(acc)
            fill.setAlpha(40)
            p.setBrush(fill)
            p.setPen(QtGui.QPen(acc, 1.6))
            p.drawRect(frame)
        p.end()

    # ---- the mouse --------------------------------------------------------------------
    def _centre_on(self, scene_pt: QtCore.QPointF) -> None:
        v = self._view
        v.finish_view_animation()
        v._auto_fit = False
        v.centerOn(scene_pt)

    def mousePressEvent(self, e: QtGui.QMouseEvent) -> None:
        if e.button() != QtCore.Qt.LeftButton:
            super().mousePressEvent(e)
            return
        pos = e.position()
        frame = self.visible_frame()
        self._view._note_burst()
        if frame.contains(pos):
            self._drag = pos - frame.center()  # drag the frame from where it was taken
        else:
            self._drag = QtCore.QPointF()
            self._centre_on(self.to_scene(pos))  # a click centres there
        e.accept()

    def mouseMoveEvent(self, e: QtGui.QMouseEvent) -> None:
        if self._drag is None:
            frame = self.visible_frame()
            self.setCursor(
                QtCore.Qt.SizeAllCursor if frame.contains(e.position()) else QtCore.Qt.PointingHandCursor
            )
            return
        self._centre_on(self.to_scene(e.position() - self._drag))
        e.accept()

    def mouseReleaseEvent(self, e: QtGui.QMouseEvent) -> None:
        self._drag = None
        e.accept()

    def mouseDoubleClickEvent(self, e: QtGui.QMouseEvent) -> None:
        self._drag = None
        from . import viewnav

        viewnav.corner_double_click(self._view)
        e.accept()

    def wheelEvent(self, e: QtGui.QWheelEvent) -> None:
        # the canvas's wheel rules
        from . import viewnav as VN

        v = self._view
        e.accept()
        prefs = v.wheel_prefs()
        intent = VN.wheel_intent(v, e, prefs)
        kind = intent[0]
        if kind == "none":
            return
        v.finish_view_animation()
        v._note_burst()
        if kind == "zoom":
            z = v.zoom()
            target = VN.wheel_zoom_target(v, z, intent[1], prefs, intent[2])
            if target is not None:
                v.zoom_by(target / z, under_mouse=False)
            return
        if kind == "native":
            # (Qt's own scroll of the canvas: down the page, Shift or a sideways turn across)
            ad = e.angleDelta()
            dx, dy = ad.x(), ad.y()
            across = bool(e.modifiers() & QtCore.Qt.ShiftModifier) or (dx and not dy)
            units = dx if dx and not dy else dy
            x, y = (units, 0) if across else (0, units)
            v._scroll_by(*VN.wheel_scroll_px(v, x, y, "notch"))
            return
        v._scroll_by(*VN.wheel_scroll_px(v, *intent[1:]))


class NavigatorPanel(QtWidgets.QFrame):
    """The navigator over the canvas, bottom right unless Settings > Canvas > Navigator
    corner picks another (see the module notes)."""

    def __init__(self, view, source: MapSource, window=None):
        super().__init__(view.viewport())
        self._view = view
        self._window = window
        self.setObjectName("Navigator")
        self.setAccessibleName("Navigator")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(_sp(1), _sp(1), _sp(1), _sp(1))
        lay.setSpacing(0)
        self.map = NavigatorMap(view, source, self, panel=True)
        lay.addWidget(self.map)
        self._chevron = QtWidgets.QToolButton(self)
        self._chevron.setAutoRaise(True)
        self._chevron.setCursor(QtCore.Qt.ArrowCursor)
        self._chevron.clicked.connect(self.toggle_collapsed)
        self._wanted = True
        self._force_open = False  # opened on a small canvas (until it grows)
        self._applied = None  # the collapsed state last laid out
        from ..prefs.settings import app_settings

        st = app_settings()
        self._collapsed = bool(st.value("view/navigator_collapsed", False, type=bool))
        self.autohide = bool(st.value("view/navigator_autohide", True, type=bool))
        self._style()
        self._apply_collapsed()
        view.viewport().installEventFilter(self)
        source.changed.connect(self.refresh)
        view.zoomChanged.connect(self.refresh)
        view.horizontalScrollBar().valueChanged.connect(self.refresh)
        view.verticalScrollBar().valueChanged.connect(self.refresh)
        self.hide()

    def _style(self) -> None:
        from .navbar import surface_qss

        self.setStyleSheet(surface_qss("Navigator"))

    def _theme_changed(self) -> None:
        self._style()
        self._apply_collapsed()

    # ---- showing ----------------------------------------------------------------------
    def set_wanted(self, on: bool) -> None:
        """View > Navigator on / off."""
        self._wanted = bool(on)
        self.refresh()

    def page_fully_visible(self) -> bool:
        v = self._view
        sc = v.scene()
        page = sc.page_rect() if sc is not None and hasattr(sc, "page_rect") else None
        if page is None or page.isNull():
            return True
        seen = v.mapToScene(v.viewport().rect()).boundingRect()
        return seen.contains(page)

    def _has_page(self) -> bool:
        w = self._window
        if w is not None and getattr(w, "doc", None) is None:
            return False
        sc = self._view.scene()
        return sc is not None and hasattr(sc, "work_rect") and not sc.work_rect().isNull()

    #: a canvas less than this many open panels wide or high shows the button only
    SQUEEZE = 3

    def squeezed(self) -> bool:
        """The canvas is too small for the open map (it would cover much of it)."""
        vp = self._view.viewport()
        w, h = self._open_size()
        return vp.width() < self.SQUEEZE * w or vp.height() < self.SQUEEZE * h

    def shown_collapsed(self) -> bool:
        """The panel shows as its button only: collapsed, or on a small canvas."""
        return self._collapsed or (self.squeezed() and not self._force_open)

    def should_show(self) -> bool:
        if not self._wanted or not self._has_page():
            return False
        if self.shown_collapsed():
            return True
        return not (self.autohide and self.page_fully_visible())

    def refresh(self, *_args) -> None:
        if self._force_open and not self.squeezed():
            self._force_open = False  # (the canvas grew: the setting rules again)
        if self.shown_collapsed() != self._applied:
            self._apply_collapsed()
        show = self.should_show()
        if show and not self.shown_collapsed() and self.size() != QtCore.QSize(*self._open_size()):
            self.setFixedSize(*self._open_size())  # (another page: another shape)
            self._place_chevron()
        if show != self.isVisible():
            self.setVisible(show)
        if show:
            self.reposition()

    def toggle_collapsed(self) -> None:
        from ..prefs.settings import app_settings

        if self.squeezed():
            # a small canvas: the button opens the map until the canvas grows (an open map
            # collapses back to the button); the setting changes only to open a collapsed one
            opening = self.shown_collapsed()
            self._force_open = opening
            if opening and self._collapsed:
                self._collapsed = False
                app_settings().setValue("view/navigator_collapsed", False)
        else:
            self._collapsed = not self._collapsed
            self._force_open = False
            app_settings().setValue("view/navigator_collapsed", self._collapsed)
        self._apply_collapsed()
        self.refresh()

    def collapsed(self) -> bool:
        return self._collapsed

    def _apply_collapsed(self) -> None:
        from ..style.theme import px

        shut = self._applied = self.shown_collapsed()
        self.map.setVisible(not shut)
        # (the arrow points where the map goes: it folds into its corner, down in a bottom
        # corner and up in a top one, and opens the other way)
        top = panel_corner().startswith("top")
        fold, open_ = (
            (QtCore.Qt.UpArrow, QtCore.Qt.DownArrow) if top else (QtCore.Qt.DownArrow, QtCore.Qt.UpArrow)
        )
        self._chevron.setArrowType(open_ if shut else fold)
        self._chevron.setToolTip("Show the navigator" if shut else "Collapse the navigator")
        self._chevron.setAccessibleName(self._chevron.toolTip())
        self._chevron.setFixedSize(px(18), px(18))
        if shut:
            self.setFixedSize(px(18) + 8, px(18) + 8)
        else:
            self.setFixedSize(*self._open_size())
        self._place_chevron()
        self.reposition()

    def _open_size(self) -> tuple:
        """The panel's size: as wide as the map, as high as the work area's shape needs
        (at most the map's height, at least half of it)."""
        from ..style.theme import px

        mw, mh = map_size()
        w, h = px(mw), px(mh)
        work = self.map._src.work_rect()
        if not work.isNull() and work.width() > 0:
            h = int(max(h * 0.5, min(h, (w - 6) * work.height() / work.width() + 6)))
        return w + 8, h + 8

    def _place_chevron(self) -> None:
        c = self._chevron
        c.move(self.width() - c.width() - 4, 4)
        c.raise_()

    def settings_changed(self) -> None:
        """Settings > Canvas > Navigator size or corner changed: the panel takes its new size
        and place, and the map is drawn again at the new size."""
        self._apply_collapsed()
        self.refresh()
        self.map.updateGeometry()
        self.map._src.schedule()

    def reposition(self) -> None:
        """In its corner of the canvas (Settings > Canvas > "Navigator corner": the bottom
        right unless another is chosen), clear of the scroll bars and the navigation bar."""
        vp = self._view.viewport()
        corner = panel_corner()
        x = 14 if corner.endswith("left") else vp.width() - self.width() - 14
        y = 14 if corner.startswith("top") else vp.height() - self.height() - 14
        nb = getattr(self._window, "navbar", None) if self._window is not None else None
        if nb is not None and nb.isVisible():
            g = nb.geometry()
            if QtCore.QRect(x, y, self.width(), self.height()).intersects(g.adjusted(-6, -6, 6, 6)):
                if g.top() > vp.height() // 2:
                    y = g.top() - self.height() - 8  # above a bottom bar
                else:
                    y = g.bottom() + 8
        self.move(max(2, x), max(2, y))
        self._place_chevron()
        self.raise_()

    def eventFilter(self, obj, ev) -> bool:
        if obj is self._view.viewport() and ev.type() == QtCore.QEvent.Resize:
            self.refresh()
        return False
