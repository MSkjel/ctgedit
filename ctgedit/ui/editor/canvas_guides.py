"""Smart guides: the edges and centres a dragged selection snaps to, found fast."""

from __future__ import annotations

import bisect
from . import canvas_hooks
from .canvas_look import mark_width
from PySide6 import QtCore, QtGui


def _target_index(view, targets: list):
    """``targets`` as sorted distinct values, each with its first place in the list (equal
    values are equally near, and the first among equals wins: the rest never can), made
    once per list. A drag keeps two lists (x and y)."""
    memo = view.__dict__.setdefault("_sg_index_memo", [])
    for lst, n, idx in memo:
        if lst is targets and n == len(targets):
            return idx
    first = {}
    for k, t in enumerate(targets):
        if t not in first:
            first[t] = k
    values = sorted(first)
    order = [first[t] for t in values]
    idx = (values, order, targets)
    memo.append((targets, len(targets), idx))
    del memo[:-4]  # (x and y of this drag, and of the last)
    return idx


def _nearest_target(idx, edges, tol):
    """(delta, target) of the target nearest one of ``edges`` within ``tol``: the edges in
    their order, a later one only when strictly nearer; for one edge the nearest target, the
    first in the list among equals. (None, 0.0) when none is near."""
    values, order, targets = idx
    best_c = None
    best_t = 0.0
    for edge in edges:
        # (a hair wider: the distance test below decides)
        lo = bisect.bisect_left(values, edge - tol - 1e-9)
        hi = bisect.bisect_right(values, edge + tol + 1e-9)
        pick = None
        for j in range(lo, hi):
            k = order[j]
            t = targets[k]
            c = t - edge
            if abs(c) > tol:
                continue
            if pick is None or abs(c) < abs(pick[0]) or (abs(c) == abs(pick[0]) and k < pick[1]):
                pick = (c, k, t)
        if pick is not None and (best_c is None or abs(pick[0]) < abs(best_c)):
            best_c, best_t = pick[0], pick[2]
    return best_c, best_t


class SmartGuides:
    """Smart guides (mixed into CanvasView)."""

    # ---- smart guides ----------------------------------
    def _sg_begin_drag(self) -> None:
        """Cache the selection box + alignment targets ONCE per drag (no per-move
        scans). On a page the boxes are the top-level objects' Extents (rect1,
        the coordinates the Properties panel shows), so an alignment lands on
        whole page pixels; without a page model the item bounds are used."""
        self._sg_targets_x = []
        self._sg_targets_y = []
        self._sg_extra_x = []
        self._sg_extra_y = []
        self._sg_rects: list[QtCore.QRectF] = []  # the objects (not the page)
        self._sg_bbox = QtCore.QRectF()
        sc = self.scene()
        if sc is None or not hasattr(sc, "_items"):
            return
        # the boxes other code adds (canvas_hooks.register_snap_targets: the underlay), without
        # the moving objects' own start boxes (an earlier version underlaid has them: they
        # would hold every small drag back where it began)
        sets = canvas_hooks.snap_sets(self)
        own = self.selection_start_boxes() if sets else []
        for s in sets:
            s = s.without(own)
            self._sg_rects += [QtCore.QRectF(r) for r in s.rects]
            self._sg_extra_x += s.xs
            self._sg_extra_y += s.ys
        self._sg_targets_x += self._sg_extra_x
        self._sg_targets_y += self._sg_extra_y
        sel = set(self._selection)
        page = getattr(sc, "_page", None)
        if page is not None and getattr(page, "objects", None):
            box = self._model_box(self._selection)
            if box is not None:
                self._sg_bbox = QtCore.QRectF(box)
            moving = {min(u) for u in self._units if u} | sel
            for i, r in self._sg_page_boxes(sc, page):
                if i in moving:
                    continue
                self._sg_rects.append(QtCore.QRectF(r))
                self._sg_targets_x += [r.left(), r.center().x(), r.right()]
                self._sg_targets_y += [r.top(), r.center().y(), r.bottom()]
        else:
            for i in sel:
                item = sc._items.get(i)
                if item is not None:
                    r = item.sceneBoundingRect()
                    self._sg_bbox = r if self._sg_bbox.isNull() else self._sg_bbox.united(r)
            for i, item in sc._items.items():
                if i in sel:
                    continue
                r = item.sceneBoundingRect()
                if r.isNull():
                    continue
                self._sg_rects.append(r)
                self._sg_targets_x += [r.left(), r.center().x(), r.right()]
                self._sg_targets_y += [r.top(), r.center().y(), r.bottom()]
        pw = getattr(sc, "_page_w", 0)
        ph = getattr(sc, "_page_h", 0)
        if pw and ph:
            self._sg_targets_x += [0.0, pw / 2.0, float(pw)]
            self._sg_targets_y += [0.0, ph / 2.0, float(ph)]

    def _sg_page_boxes(self, sc, page) -> tuple:
        """(root, Extents box) of every drawn, shown top-level object a drag can line up
        with, kept between drags while nothing they depend on changed (the same key as the
        selectable units)."""
        items = sc._items
        hidden = getattr(sc, "_hidden_roots", set()) or set()
        outer = self._units_key()
        refs = key = None
        if outer is not None:
            refs = (sc, page, items) + tuple(outer[0])
            key = (outer[1:], len(page.objects), len(items), frozenset(hidden))
            memo = self.__dict__.get("_sg_boxes_memo")
            if (
                memo is not None
                and memo[1] == key
                and len(memo[0]) == len(refs)
                and all(a is b for a, b in zip(memo[0], refs))
            ):
                return memo[2]
        out = []
        for i in page.roots():
            if i in hidden or i not in items:
                continue
            if self._is_template_anchor(i, items[i]):
                continue  # the page lines cover it
            g = page.objects[i].geometry
            if g is None or (g.extent_x <= 0 and g.extent_y <= 0):
                continue
            out.append((i, QtCore.QRectF(g.x, g.y, max(0, g.extent_x), max(0, g.extent_y))))
        out = tuple(out)
        if key is not None:
            self.__dict__["_sg_boxes_memo"] = (refs, key, out)
        return out

    def _sg_snap_move(self, raw: QtCore.QPointF, mods=None):
        """Snap a drag delta against the cached targets. Returns the snapped delta +
        active guide lines. Alt = all snapping off (``mods``: the keys held, else the
        keyboard's now). With smart guides off, only the extra targets (the underlay's
        objects, canvas_hooks.register_snap_targets) catch."""
        if self._move_free(mods) or self._sg_bbox.isNull():
            return raw, []
        if self._smart_guides:
            targets_x, targets_y = self._sg_targets_x, self._sg_targets_y
        elif self._sg_extra_x or self._sg_extra_y:
            targets_x, targets_y = self._sg_extra_x, self._sg_extra_y
        else:
            return raw, []
        tol = self.SMART_GUIDE_TOL / max(self.zoom(), 0.01)
        bb = self._sg_bbox.translated(raw)
        dx, gx = _nearest_target(
            _target_index(self, targets_x), (bb.left(), bb.center().x(), bb.right()), tol
        )
        dy, gy = _nearest_target(
            _target_index(self, targets_y), (bb.top(), bb.center().y(), bb.bottom()), tol
        )
        out = QtCore.QPointF(raw.x() + (dx or 0.0), raw.y() + (dy or 0.0))
        guides = []
        if dx is not None:
            guides.append(("v", gx))
        if dy is not None:
            guides.append(("h", gy))
        return out, guides

    def _paint_smart_guides(self, painter, rect, z, accent) -> None:
        """Live alignment lines while dragging; a move held on one axis
        draws a thin dashed line through its start along that axis."""
        if self._constrain is not None and self._moving:
            axis, p = self._constrain
            pen = QtGui.QPen(accent)
            pen.setCosmetic(True)
            pen.setWidthF(mark_width(1.0))
            pen.setDashPattern([3.0, 3.0])
            painter.setPen(pen)
            if axis == "x":
                painter.drawLine(QtCore.QPointF(rect.left(), p.y()), QtCore.QPointF(rect.right(), p.y()))
            else:
                painter.drawLine(QtCore.QPointF(p.x(), rect.top()), QtCore.QPointF(p.x(), rect.bottom()))
        if not self._sg_active:
            return
        pen = QtGui.QPen(accent)
        pen.setWidthF(mark_width(1.0) / z)
        pen.setStyle(QtCore.Qt.DashLine)
        painter.setPen(pen)
        for axis, coord in self._sg_active:
            if axis == "v":
                painter.drawLine(QtCore.QPointF(coord, rect.top()), QtCore.QPointF(coord, rect.bottom()))
            else:
                painter.drawLine(QtCore.QPointF(rect.left(), coord), QtCore.QPointF(rect.right(), coord))
        if self.SMART_GUIDE_OUTLINE:
            pen = QtGui.QPen(accent)
            pen.setWidthF(mark_width(1.5) / z)
            painter.setPen(pen)
            painter.setBrush(QtCore.Qt.NoBrush)
            m = 2.0 / z
            for r in self.smart_guide_partners():
                painter.drawRect(r.adjusted(-m, -m, m, m))
