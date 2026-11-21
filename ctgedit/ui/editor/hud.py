"""The selection HUD: a row of buttons under the selection."""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets


class SelectionHUD(QtWidgets.QFrame):
    #: the buttons' icons (px at 100 %)
    ICON = 14
    #: the surface's alpha at Overlay opacity 100 %
    ALPHA = 225
    #: the surface, in the dark design's colours (the overlay and border roles)
    SHEET = (
        "#SelectionHUD { background: rgba(34, 38, 44, %d); border: 1px solid #3a3f46; border-radius: 5px; }"
    )

    def __init__(self, view, window):
        super().__init__(view.viewport())
        self.setObjectName("SelectionHUD")
        self._view = view
        lay = QtWidgets.QHBoxLayout(self)
        lay.setSpacing(1)
        self._buttons = []
        #: the button of Properties (Genie Parameters for a genie: set_primary)
        self._primary = None
        self._window = window
        from ..prefs.window_options import hud_actions

        self.set_actions(hud_actions(window))
        self.hide()
        view.viewport().installEventFilter(self)

    def set_actions(self, actions) -> None:
        """The bar's commands, in order (Settings > Ribbon > Selection toolbar)."""
        lay = self.layout()
        for b in self._buttons:
            lay.removeWidget(b)
            b.hide()
            b.deleteLater()
        self._buttons = []
        self._primary = None
        props = getattr(self._window, "act_obj_props", None)
        for action in actions:
            b = QtWidgets.QToolButton(self)
            b.setDefaultAction(action)
            b.setAutoRaise(True)
            lay.addWidget(b)
            self._buttons.append(b)
            if self._primary is None and action is props:
                self._primary = b
        self._theme_changed()
        self.adjustSize()

    def actions_shown(self) -> list:
        """The commands of the bar's buttons now, in order."""
        return [b.defaultAction() for b in self._buttons]

    def _theme_changed(self) -> None:
        """The surface's alpha from Overlay opacity, the icons and margins at the text size
        (theme._retheme_widgets; canvas_options.apply_canvas_nav after Settings). A visible
        bar is hidden and the window shows it again where it fits at its new size."""
        from .navbar import overlay_opacity
        from ..style.theme import px

        self.setStyleSheet(self.SHEET % round(self.ALPHA * overlay_opacity() / 100))
        self.layout().setContentsMargins(px(4), px(2), px(4), px(2))
        size = QtCore.QSize(px(self.ICON), px(self.ICON))
        for b in self._buttons:
            b.setIconSize(size)
        if self.isVisible():
            replace = getattr(self._window, "_schedule_hud", None)
            if callable(replace):
                self.hide()
                replace()  # (placed again once the view settles)
            else:
                self.adjustSize()

    def eventFilter(self, obj, event) -> bool:
        # canvas interaction hides the HUD; window re-shows it 250 ms after
        # selectionChanged
        if obj is self._view.viewport() and event.type() in (
            QtCore.QEvent.MouseButtonPress,
            QtCore.QEvent.Wheel,
        ):
            self.hide()
        return False

    def set_primary(self, action) -> None:
        """The Properties button (the first one by default) = what a double-click does
        (Genie Parameters for a genie); a bar without Properties keeps its buttons."""
        b = self._primary
        if b is not None and b.defaultAction() is not action:
            b.setDefaultAction(action)

    def _covered(self, rect: QtCore.QRect, skip) -> int:
        """How many drawn page objects (other than the selection's) the bar would cover."""
        from ...render.scene import PAGE_INDEX_ROLE

        poly = self._view.mapToScene(rect)
        seen = set()
        try:
            for it in self._view.scene().items(poly, QtCore.Qt.IntersectsItemShape):
                idx = it.data(PAGE_INDEX_ROLE)
                if isinstance(idx, int) and idx not in skip:
                    seen.add(idx)
        except Exception:  # noqa: BLE001 -- placement only
            return 0
        return len(seen)

    def show_for_rect(self, scene_rect: QtCore.QRectF, skip=()) -> None:
        """Show the bar next to the selection, on the side (below, above, right, left)
        where it covers the fewest other objects (below always, over
        the objects under the selection); ``skip`` = the selection's page indices."""
        vp = self._view.viewport()
        if not self._buttons:
            self.hide()  # (Settings > Ribbon: no commands, no bar)
            return
        self.adjustSize()
        w, h, gap = self.width(), self.height(), 8
        tl = self._view.mapFromScene(scene_rect.topLeft())
        br = self._view.mapFromScene(scene_rect.bottomRight())
        cx = (tl.x() + br.x()) // 2
        cands = [
            (cx - w // 2, br.y() + gap),
            (cx - w // 2, tl.y() - h - gap),
            (br.x() + gap, tl.y()),
            (tl.x() - w - gap, tl.y()),
        ]
        skip = set(skip)
        # the other overlays on the canvas (the navigation bar): the bar never sits on them
        # (clamped into the viewport it could cover the zoom controls)
        over = [
            c.geometry()
            for c in vp.findChildren(QtWidgets.QWidget, options=QtCore.Qt.FindDirectChildrenOnly)
            if c is not self and c.isVisible() and c.isWindow() is False
        ]
        best = None
        for order, (x, y) in enumerate(cands):
            r = QtCore.QRect(int(x), int(y), w, h)
            inside = vp.rect().contains(r)
            x = max(2, min(r.x(), vp.width() - w - 2))
            y = max(2, min(r.y(), vp.height() - h - 2))
            r = QtCore.QRect(x, y, w, h)
            hits = sum(1 for g in over if g.intersects(r))
            score = (0 if inside else 1, hits, self._covered(r, skip), order)
            if best is None or score < best[0]:
                best = (score, r)
        self.move(best[1].topLeft())
        self.show()
        self.raise_()
