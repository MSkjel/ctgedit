"""Which objects the canvas can select, in what order, and box selection."""

from __future__ import annotations

from .canvas_look import modifier_roles
from PySide6 import QtCore, QtGui


class CanvasSelection:
    """Which objects the canvas can select, in what order, and box selection (mixed into CanvasView)."""

    def select_units(self, units: list, reveal: bool = True) -> None:
        """Select ``units`` as a click would (selectionChanged follows), scroll them into
        view and name them on the status bar: the keyboard's way to select."""
        units = [list(u) for u in units if u]
        self._set_units(units)
        if units:
            self._key_root = min(units[-1])
        if reveal:
            self.reveal_selection()
        if units:
            if len(units) == 1:
                self.statusMessage.emit(self.unit_label(min(units[0])))
            else:
                self.statusMessage.emit("%d objects" % len(units))

    def _box_units(self, rect: QtCore.QRectF, touch: bool) -> list:
        """The units a selection box ``rect`` takes: whole inside it (Enclose) or crossed
        by it (``touch``: the band must cross the SHAPE, a pipe along its line), as the
        Select filter and the locks allow; in the scene's order."""
        roots = []
        units = {}  # root -> its unit
        covered = set()  # every index of a unit already taken
        mpath = QtGui.QPainterPath()
        mpath.addRect(rect)
        sc = self.scene()
        if sc is None or not hasattr(sc, "_items"):
            return []
        # (the scene's index names the items whose bounds reach the band: the walk below,
        # in the items' order, tests only those)
        near = {id(it) for it in sc.items(rect.adjusted(-1, -1, 1, 1), QtCore.Qt.IntersectsItemBoundingRect)}
        for i, item in sc._items.items():
            if i in covered:
                continue  # its unit is in: nothing more to learn
            if id(item) not in near:
                continue
            if not item.isVisible():
                continue
            br = item.sceneBoundingRect()
            if br.isNull():
                continue
            if touch:
                if not rect.intersects(br):
                    continue
                if not mpath.intersects(self._item_scene_shape(item)):
                    continue
            elif not rect.contains(br):
                continue  # enclose: whole bbox inside
            unit = self._box_unit_for(i, item)
            if not unit:  # filtered out, locked or a template object
                continue
            root = min(unit)
            if root not in units:
                units[root] = unit
                roots.append(root)
                covered.update(unit)
        return [units[r] for r in roots]

    def _box_unit_for(self, i: int, item) -> list:
        """The unit item ``i`` adds to a selection box ([] = none: the template's anchor,
        the Select filter, a lock), kept for the box drag being made."""
        memo = self.__dict__.get("_box_eligible")
        if memo is not None:
            got = memo.get(i)
            if got is not None:
                return got
        if self._is_template_anchor(i, item) or (self._kind_filter is not None and not self._kind_filter(i)):
            unit = []
        else:
            unit = list(self._resolve_unit(i) or [])
        if memo is not None:
            memo[i] = unit
        return unit

    def box_preview(self) -> list:
        """The units the selection box being dragged would take (its live preview)."""
        return [list(u) for u in self._box_preview]

    def _selectable_units(self) -> list:
        """(root, unit, extent box) of every unit a click could select now, in drawing
        order: not locked, not hidden, not the template's anchor, as the Select filter
        allows. Kept while nothing it depends on changed."""
        sc = self.scene()
        page = getattr(sc, "_page", None)
        items = getattr(sc, "_items", None) or {}
        if page is None or not getattr(page, "objects", None):
            return []
        hidden = getattr(sc, "_hidden_roots", set()) or set()
        outer = self._units_key()
        key = refs = None
        if outer is not None:
            # (the objects themselves are kept and compared by identity: an id() can come
            # back for a new object once the old one is gone)
            refs = (sc, page, items, self._kind_filter) + tuple(outer[0])
            key = (outer[1:], len(page.objects), len(items), frozenset(hidden))
            memo = self._selectable_memo
            if (
                memo is not None
                and memo[1] == key
                and len(memo[0]) == len(refs)
                and all(a is b for a, b in zip(memo[0], refs))
            ):
                return list(memo[2])
        out = self._selectable_units_now(page, items, hidden)
        if key is not None:
            self._selectable_memo = (refs, key, tuple(out))
        return out

    def _selectable_units_now(self, page, items, hidden) -> list:
        out = []
        for i in page.roots():
            if i in hidden or i not in items:
                continue
            o = page.objects[i]
            if o.raw.get("_non_drawable") or self._is_template_anchor(i, items[i]):
                continue
            if self._kind_filter is not None and not self._kind_filter(i):
                continue
            unit = self._resolve_unit(i)
            if not unit:
                continue
            g = o.geometry
            if g is not None and (g.extent_x > 0 or g.extent_y > 0):
                box = QtCore.QRectF(g.x, g.y, max(0, g.extent_x), max(0, g.extent_y))
            else:
                box = self._unit_rect(unit)
            if box is None:
                continue
            out.append((min(unit), unit, box))
        return out

    @staticmethod
    def _reading_order(entries: list) -> list:
        """``entries`` (root, unit, box) in reading order: rows from the top, each row
        from the left (an object whose top is above the middle of a row's first object
        is in that row)."""
        # (one pass: sorted by top, a row is the run of entries from the cursor whose top
        # is above its first one's middle)
        keyed = sorted(
            ((e[2].top(), e[2].left(), e[0], e[2].height(), e) for e in entries),
            key=lambda k: (k[0], k[1], k[2]),
        )
        out = []
        n = len(keyed)
        i = 0
        while i < n:
            limit = keyed[i][0] + max(1.0, keyed[i][3]) / 2.0
            j = i + 1
            while j < n and keyed[j][0] < limit:
                j += 1
            out.extend(k[4] for k in sorted(keyed[i:j], key=lambda k: (k[1], k[0], k[2])))
            i = j
        return out

    def select_next(self, forward: bool = True) -> bool:
        """Tab / Shift+Tab: the next / previous selectable unit after the key unit, in
        reading (sel/tab_order) or drawing order; from nothing, the first / last. False
        when there is nothing to select, or past the last / before the first: the keyboard
        then goes on to the next panel with that object still selected."""
        entries = self._selectable_units()
        if not entries:
            return False
        if self.TAB_ORDER != "drawing":
            entries = self._reading_order(entries)
        roots = [e[0] for e in entries]
        cur = self.key_root()
        if cur in roots:
            k = roots.index(cur) + (1 if forward else -1)
            if not 0 <= k < len(roots):
                return False
        else:
            k = 0 if forward else len(roots) - 1
        e = entries[k]
        self.select_units([e[1]])
        return True

    def select_direction(self, dx: int, dy: int) -> bool:
        """Ctrl+Alt + an arrow key: the nearest selectable unit that way from the key unit
        (the distance between centres, the sideways offset weighing three times the
        forward one). False when there is none."""
        entries = self._selectable_units()
        cur = self.key_root()
        here = next((e for e in entries if e[0] == cur), None)
        if here is None:
            return self.select_next(dx > 0 or dy > 0)
        c = here[2].center()
        best = None
        for root, unit, box in entries:
            if root == cur:
                continue
            v = box.center() - c
            fwd = v.x() * dx + v.y() * dy
            side = abs(v.x() * dy - v.y() * dx)
            if fwd <= 0:
                continue
            score = fwd + 3.0 * side
            if best is None or score < best[0]:
                best = (score, unit)
        if best is None:
            self.statusMessage.emit("No object that way.")
            return False
        self.select_units([best[1]])
        return True

    def select_parent(self) -> bool:
        """Shift+Enter: the group or genie the selected object is in (selected whole)."""
        page = getattr(self.scene(), "_page", None)
        root = self.key_root()
        if page is None or root is None or not 0 <= root < len(page.objects):
            return False
        par = page.objects[root].parent
        if par is None:
            self.statusMessage.emit("The object is not inside a group or genie.")
            return False
        self.select_units([self._resolve_subtree(par)])
        return True

    def select_first_member(self) -> bool:
        """Ctrl+Enter: the first object inside the selected group or genie (a genie's
        member shows read-only)."""
        page = getattr(self.scene(), "_page", None)
        root = self.key_root()
        if page is None or root is None or not 0 <= root < len(page.objects):
            return False
        from ...format.codes import INPUT_TCS

        kids = [
            m
            for m in page.objects[root].members
            if 0 <= m < len(page.objects) and page.objects[m].raw.get("_type_code") not in INPUT_TCS
        ]
        if not kids:
            self.statusMessage.emit("The object has no objects inside it.")
            return False
        self.select_units([self._resolve_subtree(kids[0])])
        return True

    @staticmethod
    def _box_rect(start: QtCore.QPointF, end: QtCore.QPointF, mods) -> QtCore.QRectF:
        """The drawn box (modifier_roles): 'proportional' = square / circle (the larger
        side; Ctrl in the Graphics Builder's keys), 'centre' = ``start`` is the CENTRE
        (Shift; help 1172124 / 1172125)."""
        roles = modifier_roles(mods)
        dx, dy = end.x() - start.x(), end.y() - start.y()
        if "proportional" in roles:
            s = max(abs(dx), abs(dy))
            dx = s if dx >= 0 else -s
            dy = s if dy >= 0 else -s
        if "centre" in roles:
            return QtCore.QRectF(start.x() - abs(dx), start.y() - abs(dy), 2 * abs(dx), 2 * abs(dy))
        return QtCore.QRectF(start, QtCore.QPointF(start.x() + dx, start.y() + dy)).normalized()
