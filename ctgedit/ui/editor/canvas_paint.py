"""The canvas's own drawing over the page: selection, handles, hover, marks, guidelines and outlines."""

from __future__ import annotations

import math
from ...format import geometry as _geom
from .canvas_look import (
    NODE_BOX_PX,
    guide_colour,
    guide_width,
    handle_size,
    mark_width,
    outline_width,
    pointer_reach,
)
from PySide6 import QtCore, QtGui


class CanvasPainting:
    """The canvas's own drawing over the page (mixed into CanvasView)."""

    def _outline_hit(self, sp: QtCore.QPointF):
        """(index the new node takes, the node) for Insert at scene point
        ``sp`` on the reshape target's outline, or None."""
        idx, info = self.reshape_target()
        if idx is None or not info.can_insert:
            return None
        page = getattr(self.scene(), "_page", None)
        width = page.objects[idx].style.line_width if page is not None else 1
        tol = max((4.0 + pointer_reach()) / max(self.zoom(), 0.01), (width or 1) / 2.0)
        return _geom.node_insert_point(info, (sp.x(), sp.y()), tol)

    # -- overlay painting ----------------------------------------------------
    def _draw_nodes(self, painter: QtGui.QPainter, z: float, accent: QtGui.QColor) -> None:
        """The reshape target's nodes (help 1172188): white circles with an accent edge, a
        selected node filled with the accent; during a node drag the reshaped outline
        follows the pointer."""
        idx, info = self.reshape_target()
        if idx is None:
            return
        nodes = self._dragged_nodes(info)
        off = self._live_shift(idx)
        if off is not None:
            nodes = [(x + off.x(), y + off.y()) for x, y in nodes]
        sel = self._node_sel if idx == self._node_owner else set()
        if self._node_drag is not None and self._node_drag["moved"]:
            pen = QtGui.QPen(accent)
            pen.setWidthF(mark_width(1.5) / z)
            pen.setStyle(QtCore.Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(QtCore.Qt.NoBrush)
            poly = QtGui.QPolygonF([QtCore.QPointF(x, y) for x, y in nodes])
            if info.closed:
                painter.drawPolygon(poly)
            else:
                painter.drawPolyline(poly)
        from ..style.theme import fixed

        white = fixed("handle_fill")
        r = max(NODE_BOX_PX, handle_size()) / 2.0 / z
        pen = QtGui.QPen(accent)
        pen.setWidthF(1.3 / z)
        painter.setPen(pen)
        for k, (x, y) in enumerate(nodes):
            painter.setBrush(QtGui.QBrush(accent if k in sel else white))
            painter.drawEllipse(QtCore.QPointF(x, y), r, r)

    def _outline_parts(self, unit: list[int]):
        """:meth:`_unit_outline` as (box, None) for a unit outlined by its box (a group, most
        objects) and (None, shape) for a single node / curve object; (None, None) for none."""
        sc = self.scene()
        if len(unit) == 1 and sc is not None and hasattr(sc, "_items"):
            item = sc._items.get(unit[0])
            if isinstance(item, self._SHAPE_ITEM_TYPES):
                path = self._shape_outline(sc, unit[0], item)
                if not path.isEmpty():
                    return None, path
        return self._unit_rect(unit), None

    def _outline_candidates(self, rect: QtCore.QRectF, m: float) -> list:
        """The selected units whose outline may reach ``rect``: all of them for a small
        selection; for a large one those whose bounds (kept per selection and scene build,
        moved by a drag's live offset) touch it."""
        units = self._units
        if len(units) < self.OUTLINE_CULL_FROM:
            return units
        sc = self.scene()
        build = (sc, getattr(sc, "build_serial", None))
        memo = self.__dict__.get("_outline_cull")
        if memo is None or memo[0] is not units or memo[1][0] is not build[0] or memo[1][1] != build[1]:
            kept = []
            for unit in units:
                r = self._unit_rect(unit)
                if r is None:
                    continue
                off = self._live_shift(unit[0])
                kept.append((r.translated(-off) if off is not None else r, unit))
            memo = (units, build, kept)
            self.__dict__["_outline_cull"] = memo
        out = []
        for base, unit in memo[2]:
            off = self._live_shift(unit[0])
            r = base.translated(off) if off is not None else base
            if rect.intersects(r.adjusted(-m, -m, m, m)):
                out.append(unit)
        return out

    def _draw_selection_outlines(
        self, painter: QtGui.QPainter, rect: QtCore.QRectF, z: float, accent: QtGui.QColor
    ) -> None:
        """Draw the selected units' dashed outlines."""
        ow = outline_width()
        m = (3.0 + ow) / z
        boxes: list = []
        shapes: list = []
        for unit in self._outline_candidates(rect, m):
            box, shape = self._outline_parts(unit)
            reach = box if box is not None else (shape.boundingRect() if shape is not None else None)
            if reach is None or not rect.intersects(reach.adjusted(-m, -m, m, m)):
                continue
            (boxes if box is not None else shapes).append((len(unit) > 1, box or shape))
        if not boxes and not shapes:
            return
        painter.setBrush(QtCore.Qt.NoBrush)
        if boxes:
            aa = painter.testRenderHint(QtGui.QPainter.Antialiasing)
            painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
            for group in (False, True):
                pen = QtGui.QPen(accent)
                pen.setCosmetic(True)
                pen.setWidthF(ow)
                # (Qt.DashLine is 4 on, 2 off in pen widths: the scaled pens' widths kept)
                w = 1.4 if group else 1.2
                pen.setDashPattern([4 * w, 2 * w])
                painter.setPen(pen)
                for g, b in boxes:
                    if g == group:
                        painter.drawRect(b)
            painter.setRenderHint(QtGui.QPainter.Antialiasing, aa)
        if shapes:
            pen = QtGui.QPen(accent)
            pen.setCosmetic(True)
            pen.setWidthF(ow)
            pen.setDashPattern([4 * 1.2, 2 * 1.2])
            painter.setPen(pen)
            for _group, path in shapes:
                painter.drawPath(path)

    def _paint_guidelines(self, painter, rect, z, accent) -> None:
        gd = self._guide_drag if self._guide_drag and self._guide_drag["moved"] else None
        if not (self._guides_visible and (self._vguides or self._hguides or gd)):
            return
        from ..style.theme import color

        line = guide_colour(170)  # (Settings > Appearance > Guideline colour)
        pen = QtGui.QPen(line)
        pen.setWidthF(guide_width() / z)
        pen.setStyle(QtCore.Qt.DashLine)
        painter.setPen(pen)
        # (a guideline being moved is drawn where it is dragged, below; a
        # Ctrl+drag copy leaves the original in place)
        moving = gd is not None and not gd["copy"]
        skip_v = gd["orig"] if moving and gd["axis"] == "v" else None
        skip_h = gd["orig"] if moving and gd["axis"] == "h" else None
        for gx in self._vguides:
            if gx != skip_v:
                painter.drawLine(QtCore.QPointF(gx, rect.top()), QtCore.QPointF(gx, rect.bottom()))
        for gy in self._hguides:
            if gy != skip_h:
                painter.drawLine(QtCore.QPointF(rect.left(), gy), QtCore.QPointF(rect.right(), gy))
        if gd is not None:
            # solid accent; red where the drop deletes it (the page edge)
            pen = QtGui.QPen(color("danger") if self._guide_deletes(gd["axis"], gd["pos"]) else accent)
            pen.setWidthF((guide_width() + 0.5) / z)
            painter.setPen(pen)
            p = gd["pos"]
            if gd["axis"] == "v":
                painter.drawLine(QtCore.QPointF(p, rect.top()), QtCore.QPointF(p, rect.bottom()))
            else:
                painter.drawLine(QtCore.QPointF(rect.left(), p), QtCore.QPointF(rect.right(), p))

    def _paint_marks(self, painter, rect, z, accent) -> None:
        from ..style.theme import fixed

        # genies a background update is composing again (set_pending)
        if self._pending:
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(fixed("pending_wash"))
            for i in self._pending:
                outline = self._unit_outline([i])
                if outline is not None:
                    painter.drawRect(outline.boundingRect())
        # the objects changed since the save (View > Mark Changes Since Save)
        changed = self._changed
        if changed:
            pen = QtGui.QPen(fixed("changed_edge"))
            pen.setWidthF(mark_width(1.5) / z)
            painter.setPen(pen)
            painter.setBrush(fixed("changed_wash"))
            m = 4.0 / z
            for i in changed:
                outline = self._unit_outline([i])
                if outline is not None:
                    box = outline.boundingRect()
                    if rect.intersects(box.adjusted(-m, -m, m, m)):
                        painter.drawRect(box)

    def _paint_hover(self, painter, rect, z, accent) -> None:
        """Hover pre-selection + tree-hover highlight outlines (shape-true); the units a
        selection box being dragged would take (its live preview)."""
        from ..style.theme import canvas_colour

        painter.setBrush(QtCore.Qt.NoBrush)
        # (Settings > Canvas > "Outline the object under the pointer" off: only the Objects
        # panel's highlight)
        hover = self._hover_unit if self.HOVER_OUTLINE else []
        for unit, alpha in ((hover, 110), (self._highlight, 160)):
            if not unit or self._moving or self._marquee is not None:
                continue
            outline = self._unit_outline(unit)
            if outline is None:
                continue
            pen = QtGui.QPen(canvas_colour("hover", alpha))
            pen.setWidthF(outline_width() / z)
            painter.setPen(pen)
            painter.drawPath(outline)
        if self._marquee is not None and self._box_preview:
            pen = QtGui.QPen(canvas_colour("hover", 200))
            pen.setCosmetic(True)
            pen.setWidthF(outline_width() + 0.5)
            painter.setPen(pen)
            m = (3.5 + outline_width()) / z
            for unit in self._box_preview:
                outline = self._unit_outline(unit)
                if outline is not None and rect.intersects(outline.boundingRect().adjusted(-m, -m, m, m)):
                    painter.drawPath(outline)

    def _paint_selection(self, painter, rect, z, accent) -> None:
        """One outline per selected unit (the actual SHAPE for single pipes / polygons /
        ellipses / lines, the bbox for groups), the sizing handles, the nodes."""
        from ..style.theme import canvas_colour, fixed

        painter.save()
        self._draw_selection_outlines(painter, rect, z, accent)
        painter.restore()
        multi = self._multi_box()
        if multi is not None and self._resizing is None and self._rotating is None:
            # several units: one solid box round them all (the dashed outlines inside)
            painter.save()
            pen = QtGui.QPen(accent)
            pen.setCosmetic(True)
            pen.setWidthF(outline_width())
            painter.setPen(pen)
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
            painter.drawRect(multi)
            painter.restore()
        idx, box = self._handle_box()
        if (
            idx is not None
            and self._resizing is None
            and self._node_drag is None
            and self._rotating is None
            and not self._moving
        ):
            painter.save()
            self._paint_handles(painter, box, z)
            self._paint_grip(painter, box, z, accent)
            painter.restore()
        if self._resizing is None:
            painter.save()
            self._draw_nodes(painter, z, accent)
            painter.restore()
        if self._resizing is not None:
            pen = QtGui.QPen(accent)
            pen.setWidthF(mark_width(1.5) / z)
            painter.setPen(pen)
            painter.setBrush(QtGui.QBrush(canvas_colour("marquee")))
            painter.drawRect(self._resize_rect.normalized())
        if self._rotating is not None:
            self._paint_rotate_ghost(painter, z, accent)
        if self._flash_unit:
            # an Alt+click reached this unit: over everything, lightly filled
            outline = self._unit_outline(self._flash_unit)
            if outline is not None:
                pen = QtGui.QPen(accent)
                pen.setCosmetic(True)
                pen.setWidthF(outline_width() + 1.0)
                painter.setPen(pen)
                painter.setBrush(canvas_colour("selection", 77))
                painter.drawPath(outline)
        if self._marquee is not None:
            # ENCLOSE (fully contained): a solid edge in the selection colour; TOUCH
            # (crossing): a dashed edge in green (shape and colour both tell them apart)
            mc = fixed("marquee_touch") if self._marquee_touch else QtGui.QColor(accent)
            pen = QtGui.QPen(mc)
            pen.setCosmetic(True)
            pen.setWidthF(mark_width(1.0))
            if self._marquee_touch:
                pen.setDashPattern([4.0, 3.0])
            painter.setPen(pen)
            fill = QtGui.QColor(mc)
            fill.setAlpha(30)
            painter.setBrush(QtGui.QBrush(fill))
            painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
            painter.drawRect(self._marquee)

    def _paint_handles(self, painter, box: QtCore.QRectF, z: float) -> None:
        """The sizing handles: white squares with a 1 px edge in the selection colour, the
        edge handles 2 px smaller, the one under the pointer 2 px larger."""
        from ..style.theme import canvas_colour, fixed

        pen = QtGui.QPen(canvas_colour("handle_edge"))
        pen.setCosmetic(True)
        pen.setWidthF(1.0)
        painter.setPen(pen)
        painter.setBrush(QtGui.QBrush(fixed("handle_fill")))
        painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
        for hid, hx, hy, size in self._handle_specs(box):
            if hid == self._hover_handle:
                size += 2.0
            s = size / z
            painter.drawRect(QtCore.QRectF(hx - s / 2, hy - s / 2, s, s))

    def _paint_grip(self, painter, box: QtCore.QRectF, z: float, accent) -> None:
        """The rotation grip: a small round handle above the top-middle handle, on a stem."""
        from ..style.theme import fixed

        p = self._grip_point()
        if p is None:
            return
        pen = QtGui.QPen(accent)
        pen.setCosmetic(True)
        pen.setWidthF(1.0)
        painter.setPen(pen)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        r = 4.5 / z
        stem = QtCore.QPointF(p.x(), p.y() + r)
        top = QtCore.QPointF(p.x(), box.top() - (handle_size() / 2.0) / z)
        if top.y() > stem.y():
            painter.drawLine(stem, top)
        painter.setBrush(QtGui.QBrush(fixed("handle_fill")))
        painter.drawEllipse(p, r, r)

    def _paint_rotate_ghost(self, painter, z: float, accent) -> None:
        """The box turned by the quarter turns the grip is dragged to, about its centre."""
        from ..style.theme import canvas_colour

        d = self._rotating
        b, c = d["box"], d["centre"]
        if d["steps"] % 2:
            ghost = QtCore.QRectF(c.x() - b.height() / 2.0, c.y() - b.width() / 2.0, b.height(), b.width())
        else:
            ghost = QtCore.QRectF(b)
        pen = QtGui.QPen(accent)
        pen.setCosmetic(True)
        pen.setWidthF(mark_width(1.5))
        pen.setDashPattern([4.0, 3.0])
        painter.setPen(pen)
        painter.setBrush(QtGui.QBrush(canvas_colour("marquee")))
        painter.drawRect(ghost)
        if d["steps"]:
            # which way the box's top went: a mark on the side the top edge turns to
            top = {
                1: (ghost.right(), ghost.center().y()),
                2: (ghost.center().x(), ghost.bottom()),
                3: (ghost.left(), ghost.center().y()),
            }[d["steps"]]
            painter.setBrush(QtGui.QBrush(accent))
            painter.drawEllipse(QtCore.QPointF(*top), 3.0 / z, 3.0 / z)

    def _paint_selection_chips(self, painter, accent) -> None:
        """Viewport marks of the selection: a multi-selection's count at the top-left of its
        box, and the '+' or '+/-' of a selection box that adds or toggles."""
        from ..style.theme import color

        multi = self._multi_box()
        if multi is not None and self._resizing is None and self._rotating is None and not self._moving:
            r = self.mapFromScene(multi).boundingRect()
            self._paint_badge(
                painter,
                QtCore.QPoint(r.left(), r.top() - 3),
                str(len(self._units)),
                accent,
                color("on_accent"),
                above=True,
            )
        if self._marquee is not None and self._marquee_mods:
            text = "+/-" if self._marquee_mods & QtCore.Qt.ControlModifier else "+"
            at = QtCore.QPoint(self._last_vp)
            self._paint_badge(
                painter, QtCore.QPoint(at.x() + 8, at.y() - 8), text, accent, color("on_accent"), above=True
            )

    def _paint_badge(self, painter, at: QtCore.QPoint, text: str, bg, fg, above: bool = False) -> None:
        """A small rounded chip with ``text`` whose bottom-left (``above``) or top-left is
        at ``at``, kept on the canvas."""
        f = self._pill_font()
        fm = QtGui.QFontMetricsF(f)
        w, h = fm.horizontalAdvance(text) + 10, fm.height() + 2
        box = QtCore.QRectF(at.x(), at.y() - h if above else at.y(), w, h)
        vp = QtCore.QRectF(self.viewport().rect()).adjusted(2, 2, -2, -2)
        if box.left() < vp.left():
            box.moveLeft(vp.left())
        if box.top() < vp.top():
            box.moveTop(vp.top())
        if box.right() > vp.right():
            box.moveRight(vp.right())
        painter.setFont(f)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(bg))
        painter.drawRoundedRect(box, h / 2.0, h / 2.0)
        painter.setPen(QtGui.QPen(QtGui.QColor(fg)))
        painter.drawText(box, QtCore.Qt.AlignCenter, text)

    def _paint_draw_preview(self, painter, rect, z, accent) -> None:
        """What a drawing tool will make: the box / line / measure being dragged, and the
        points of a polygon, polyline or pipe. Only a polygon is filled (lightly, in its
        colour to be); its closing edge back to the first point is dashed. A pipe and a
        polyline are never filled."""
        if self._draw_start is not None and self._draw_cur is not None:
            pen = QtGui.QPen(accent)
            pen.setWidthF(mark_width(1.5) / z)
            painter.setPen(pen)
            painter.setBrush(QtCore.Qt.NoBrush)
            # the preview is the CONSTRAINED result (Ctrl / Shift), snapped
            end = self._snap_pt(self._draw_cur)
            if self._tool == "line":
                painter.drawLine(self._draw_start, self._line_end(self._draw_start, end, self._draw_mods))
            elif self._tool == "measure":
                # the distance line, its end marks and the dashed box of its extents
                a = self._draw_start
                painter.drawLine(a, end)
                m = 4.0 / z
                for pt in (a, end):
                    painter.drawLine(
                        QtCore.QPointF(pt.x() - m, pt.y() - m), QtCore.QPointF(pt.x() + m, pt.y() + m)
                    )
                    painter.drawLine(
                        QtCore.QPointF(pt.x() - m, pt.y() + m), QtCore.QPointF(pt.x() + m, pt.y() - m)
                    )
                box = QtCore.QRectF(a, end).normalized()
                if box.width() >= 1 and box.height() >= 1:
                    dpen = QtGui.QPen(accent)
                    dpen.setWidthF(mark_width(1.0) / z)
                    dpen.setStyle(QtCore.Qt.DashLine)
                    painter.setPen(dpen)
                    painter.drawRect(box)
            else:
                from ..style.theme import canvas_colour

                painter.setBrush(QtGui.QBrush(canvas_colour("selection", 30)))
                r = self._box_rect(self._draw_start, end, self._draw_mods)
                if self._tool.startswith("ellipse"):
                    painter.drawEllipse(r)
                else:
                    painter.drawRect(r)
        if not self._nodes:
            return
        pts = list(self._nodes)
        if self._draw_cur is not None:
            pts.append(self._draw_cur)
        closed = self._tool == "polygon" and len(pts) >= 3
        if closed:
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(self._preview_fill(accent))
            painter.drawPolygon(QtGui.QPolygonF(pts))
        pen = QtGui.QPen(accent)
        pen.setWidthF(mark_width(1.5) / z)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.NoBrush)
        path = QtGui.QPainterPath(pts[0])
        for q in pts[1:]:
            path.lineTo(q)
        painter.drawPath(path)
        if closed:
            dpen = QtGui.QPen(accent)
            dpen.setWidthF(mark_width(1.0) / z)
            dpen.setStyle(QtCore.Qt.DashLine)
            painter.setPen(dpen)
            painter.drawLine(pts[-1], pts[0])
        if len(self._nodes) <= 64:  # skip node dots on dense freehand paths
            r = 3.0 / z
            painter.setPen(pen)
            painter.setBrush(QtGui.QBrush(accent))
            for q in self._nodes:
                painter.drawEllipse(q, r, r)

    def _paint_crosshair(self, painter) -> None:
        """View > Cross Hair Cursor: lines through the point a click lands on (snapped to
        the grid with Snap to Grid on, where a small circle marks it), across the view or
        short (Settings > Canvas); the theme's accent with a 1 px contrasting halo, so they
        read on any page colour; the position next to the pointer."""
        if not (self._crosshair and self._cross_pos is not None):
            return
        sp = self._cross_point(self._cross_pos)
        c = self.mapFromScene(sp)
        from ..style.theme import color

        acc = color("accent")
        halo = self._halo_for(acc)
        vp = self.viewport().rect()
        if self.CROSSHAIR_STYLE == "short":
            r = 40
            segs = [(c.x() - r, c.y(), c.x() + r, c.y()), (c.x(), c.y() - r, c.x(), c.y() + r)]
        else:
            segs = [(vp.left(), c.y(), vp.right(), c.y()), (c.x(), vp.top(), c.x(), vp.bottom())]
        painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
        for colour, width in ((halo, 3), (acc, 1)):
            pen = QtGui.QPen(colour)
            pen.setWidth(width)
            painter.setPen(pen)
            for x0, y0, x1, y1 in segs:
                painter.drawLine(x0, y0, x1, y1)
        if self._snap:
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            painter.setBrush(QtCore.Qt.NoBrush)
            for colour, width in ((halo, 3), (acc, 1.4)):
                pen = QtGui.QPen(colour)
                pen.setWidthF(width)
                painter.setPen(pen)
                painter.drawEllipse(QtCore.QPointF(c), 5.0, 5.0)
        if self.CROSSHAIR_READOUT and not self._pill:
            self._paint_pill(
                painter, self.mapFromScene(self._cross_pos), self._crosshair_readout_text(self._cross_pos)
            )

    def _paint_pill(self, painter, at: QtCore.QPoint, text: str) -> None:
        """A small chip next to the pointer (the drag readout, the cross hair position)."""
        from ..style.theme import color

        painter.setFont(self._pill_font())
        box = self._pill_box(at, text)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setPen(QtGui.QPen(color("border")))
        bg = color("popup")
        bg.setAlpha(235)
        painter.setBrush(bg)
        painter.drawRoundedRect(box, 4, 4)
        painter.setPen(QtGui.QPen(color("text")))
        painter.drawText(box, QtCore.Qt.AlignCenter, text)

    def _paint_zoom_box(self, painter) -> None:
        box = self.zoom_box_rect()
        if box is None or (box.width() < 2 and box.height() < 2):
            return
        from ..style.theme import color

        acc = color("accent")
        painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
        fill = QtGui.QColor(acc)
        fill.setAlpha(28)
        painter.setBrush(fill)
        pen = QtGui.QPen(acc)
        pen.setWidthF(mark_width(1.0))
        pen.setStyle(QtCore.Qt.DashLine)
        painter.setPen(pen)
        painter.drawRect(box)

    def _paint_page_chip(self, painter) -> None:
        """The page is out of view: a chip at the canvas edge nearest to it, with an arrow
        toward it and 'Page' (a click is Fit Page)."""
        box = self._page_chip = self.page_chip_rect()
        if box is None:
            return
        from ..style.theme import color

        sc = self.scene()
        d = QtCore.QPointF(self.mapFromScene(sc.page_rect().center())) - box.center()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setPen(QtGui.QPen(color("border")))
        painter.setBrush(color("popup"))
        painter.drawRoundedRect(box, box.height() / 2.0, box.height() / 2.0)
        # the arrow, pointing at the page
        ang = math.atan2(d.y(), d.x())
        ac = QtCore.QPointF(box.left() + 14, box.center().y())
        # (a shaft and a narrow head: a plain triangle reads as pointing down at some angles)
        tr = QtGui.QTransform().translate(ac.x(), ac.y()).rotateRadians(ang)
        pen = QtGui.QPen(color("accent"), 2)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        painter.setPen(pen)
        painter.drawLine(tr.map(QtCore.QLineF(-6, 0, 3, 0)))
        head = QtGui.QPolygonF([QtCore.QPointF(7, 0), QtCore.QPointF(1, -4), QtCore.QPointF(1, 4)])
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(color("accent"))
        painter.drawPolygon(tr.map(head))
        painter.setFont(self._chip_font())
        painter.setPen(QtGui.QPen(color("text")))
        painter.drawText(box.adjusted(24, 0, -8, 0), QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, "Page")

    def _paint_focus_frame(self, painter) -> None:
        """A 2 px accent frame inside the canvas while the keyboard works on it."""
        from ..style.theme import color

        pen = QtGui.QPen(color("accent"))
        pen.setWidth(2)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.NoBrush)
        r = self.viewport().rect()
        painter.drawRect(r.adjusted(1, 1, -1, -1))
