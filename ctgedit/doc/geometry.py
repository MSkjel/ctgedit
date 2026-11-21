"""Geometry, placement and node edits."""

from __future__ import annotations

from ..editing_base import (  # noqa: F401
    COORD_REFUSED,
    List,
    Optional,
    PageObject,
    Sequence,
    TransformError,
    Tuple,
    check_coords,
    factory,
    geom,
    hashlib,
    math,
    struct,
    writer,
)
from ..editing_base import (  # noqa: F401
    _Command,
)
from ..format import colourstate as _colourstate
from ..format import placement as _placement


class _BlockDiff:
    """The blocks of a byte field an edit changed, as they were before it: undo gives the field
    back from its value after the edit (the undo stack is LIFO, so that is the value then)."""

    BLOCK = 32
    __slots__ = ("kind", "size", "runs")

    def __init__(self, kind, size, runs):
        self.kind, self.size, self.runs = kind, size, runs

    @classmethod
    def of(cls, before, after):
        if (
            type(before) is not type(after)
            or not isinstance(before, (bytes, bytearray))
            or len(before) != len(after)
            or len(before) < 4 * cls.BLOCK
        ):
            return None
        n, B = len(before), cls.BLOCK
        runs = [
            (i, bytes(before[i : i + B])) for i in range(0, n, B) if before[i : i + B] != after[i : i + B]
        ]
        if sum(len(r) for _i, r in runs) * 2 > n:
            return None  # most of it changed: keep the whole value
        return cls(type(before), n, runs)

    def undo(self, after):
        if after is None or len(after) != self.size:
            raise ValueError("the field changed size since the edit")
        out = bytearray(after)
        for i, old in self.runs:
            out[i : i + len(old)] = old
        return self.kind(out)

    def __len__(self):
        return sum(len(r) for _i, r in self.runs) + 16 * len(self.runs)


class _TurnPoints:
    """The in-memory positions CtDraw keeps between turns and mirrors."""

    __slots__ = ("prev", "new", "at", "frac")

    def __init__(self, prev, frac=None):
        self.prev, self.new, self.at = prev, {}, None
        self.frac = frac or {}

    def true(self, p):
        at = self.at
        m = self.prev.get(at) if at is not None else None
        f = self.frac.get(at) if at is not None else None
        if m is None and f is None:
            return p
        tx = m[0].get(p[0]) if m is not None else None
        ty = m[1].get(p[1]) if m is not None else None
        if f is not None:
            if tx is None and (m is None or p[0] not in m[0]):
                tx = _with_fraction(p[0], f[0])
            if ty is None and (m is None or p[1] not in m[1]):
                ty = _with_fraction(p[1], f[1])
        return (p[0] if tx is None else tx, p[1] if ty is None else ty)

    def local(self, stored):
        """The record's Local in memory: the remembered one when it is the ``stored``
        (mode, L) but for the signs of its zeros, else ``stored``."""
        m = self.prev.get(self.at) if self.at is not None else None
        mem = m[2] if m is not None and len(m) > 2 else None
        if (
            mem is not None
            and mem[0] == stored[0]
            and all(abs(x - y) < 1e-9 for x, y in zip(mem[1], stored[1]))
        ):
            return mem
        return stored

    def set_local(self, mode: int, L) -> None:
        if self.at is not None:
            self.new.setdefault(self.at, _new_entry())[2] = (mode, tuple(L))

    def group(self, stored):
        """The (base mode, base Local, group mode, group Local) of the carrier being turned
        under a plain group when they still compose to its ``stored`` (mode, L), else None."""
        from ..format import placement

        m = self.prev.get(self.at) if self.at is not None else None
        g = m[4] if m is not None and len(m) > 4 else None
        if g is None:
            return None
        L, mode = placement.compose_after(g[1], g[0], g[3], g[2])
        if mode == stored[0] and all(abs(x - y) < 1e-9 for x, y in zip(L, stored[1])):
            return g
        return None

    def set_group(self, state) -> None:
        if self.at is not None:
            self.new.setdefault(self.at, _new_entry())[4] = tuple(state)

    def turn(self):
        """The quarter turns a text with no Local holds in memory since its load or save,
        as the Local (mode, L) CtDraw composed them onto (the page stores them as the
        orient), or None."""
        m = self.prev.get(self.at) if self.at is not None else None
        mem = m[2] if m is not None and len(m) > 2 else None
        return mem if mem is not None and _turn_only(mem[1]) else None

    def group_turn(self):
        """The (base mode, base Local, group mode, group Local) of a text with no Local in a
        plain group when they compose to quarter turns alone (the page stores them as the
        orient), or None."""
        from ..format import placement

        m = self.prev.get(self.at) if self.at is not None else None
        g = m[4] if m is not None and len(m) > 4 else None
        if g is None:
            return None
        L, _mode = placement.compose_after(g[1], g[0], g[3], g[2])
        return g if _turn_only(L) else None

    def box(self, at=None):
        """The text's box in memory its last turn left (None: its stored extent)."""
        at = self.at if at is None else at
        m = self.prev.get(at) if at is not None else None
        return m[3] if m is not None and len(m) > 3 else None

    def set_box(self, box) -> None:
        if self.at is not None:
            self.new.setdefault(self.at, _new_entry())[3] = tuple(box)

    def note(self, true, out) -> None:
        if self.at is None:
            return
        maps = self.new.setdefault(self.at, _new_entry())
        for mp, o, t in ((maps[0], int(out[0]), true[0]), (maps[1], int(out[1]), true[1])):
            if o not in mp:
                mp[o] = t
            elif mp[o] is not None and mp[o] != t:
                mp[o] = None


def _new_entry():
    """A record's empty :class:`_TurnPoints` entry: the x and y maps, its Local in memory,
    its box in memory and its state under a plain group."""
    return [{}, {}, None, None, None]


def _turn_only(L) -> bool:
    """The Local's linear part is one, two or three quarter turns: no flip, not the
    identity."""
    a, b, c, d = L[:4]
    return a * d - b * c > 0 and (abs(b) > 0.5 or a < 0)


def _with_fraction(w, f):
    """The true value CtDraw's int cast wrote as ``w`` when its fraction is ``f`` (0 <= f <
    1): ``w + f`` above 0, ``w - 1 + f`` below; None for no fraction, and for 0, which two
    true values (one each side of 0) truncate to."""
    if not f or not w:
        return None
    return w + f if w > 0 else w - 1.0 + f


#: per record, how many turned states keep their true positions (undo / redo come back to
#: one of them)
_TURN_STATES = 8


class GeometryEdits:
    """EditableDocument's geometry, placement and node edits."""

    # -- placed-symbol placement (Local transform; format.placement) -----------
    def _link_info(self, po: PageObject):
        """The resolved library element (origin e + native rect N + root handle
        + state-0) of a placed symbol, cached per link; None if unresolvable."""
        from ..format import placement
        from ..format.symbols import reference_from_object

        try:
            ref = reference_from_object(po)
        except Exception:  # noqa: BLE001
            ref = None
        if ref is None:
            return None
        key = (ref.library.lower(), ref.element.lower(), ref.ext)
        # the resolver first: a library written since drops the cached infos
        res = self._resolver()
        cache = self.__dict__.setdefault("_link_cache", {})
        if key not in cache:
            cache[key] = placement.element_info(res, ref.library, ref.element, ref.ext) if res else None
        return cache[key]

    def _translate_records(self, idx: Sequence[int], dx: int, dy: int) -> None:
        """Move a set of page records (closed under subtrees) the way CtDraw
        re-derives them: see :func:`placement.translate_records`."""
        from ..format import placement

        objs = self.parsed.page.objects
        sem = self.page.objects if self.page is not None else []
        order = sorted(set(idx))
        pos = [objs[i] for i in order]
        where = {i: k for k, i in enumerate(order)}
        parents = []
        for i in order:
            a = sem[i].parent if i < len(sem) else None
            parents.append(where.get(a) if a is not None else None)

        def info(po):
            return self._link_info(po)

        placement.translate_records(pos, parents, dx, dy, info, lambda k: self._ancestor_chain(order[k]))

    def _ancestor_chain(self, index: int) -> list:
        """(Local, mode, e) of every ancestor of page record ``index``,
        nearest first -- what its Total is composed from."""
        from ..format import placement

        out = []
        sem = self.page.objects if self.page is not None else []
        a = sem[index].parent if index < len(sem) else None
        while a is not None:
            out.append(placement.link_of(self.parsed.page.objects[a]))
            a = sem[a].parent if a < len(sem) else None
        return out

    def _translate_detached(self, records: List[PageObject], dx: int, dy: int) -> None:
        """Move detached record subtrees (clipboard / duplicate / library copy,
        DFS order) as root-level units -- the same transform rules as a page
        move, so a nested genie / symbol set does not get d applied twice."""
        from ..format import placement

        if not (dx or dy):
            return
        try:
            placement.translate_subtrees(records, dx, dy, self._link_info)
        except (struct.error, OverflowError) as e:
            # (copies past what a page stores: refused, not left on the originals)
            raise TransformError(COORD_REFUSED) from e
        except Exception:  # noqa: BLE001 -- a malformed record: per-record move
            for po in records:
                try:
                    geom.translate_object(po, dx, dy)
                except (struct.error, OverflowError) as e:
                    raise TransformError(COORD_REFUSED) from e
                except Exception:  # noqa: BLE001
                    pass

    def _used_handles(self) -> set:
        """Every object handle in use on the page."""
        used = set()
        for po in self.parsed.page.objects:
            h = geom.get_handle(po)
            if h is not None:
                used.add(h)
        return used

    @staticmethod
    def _alloc_handle(used: set) -> int:
        """A fresh page-unique handle, as CtDraw gives a new object: the lowest free slot in
        0..0x7FFE (genie_place.alloc_handles); recorded in ``used``."""
        from ..format import genie_place as GP

        return GP.alloc_handles(used, 1)[0]

    def next_handle(self) -> int:
        """A fresh object handle for a new record (live AN = handle + 1)."""
        return self._alloc_handle(self._used_handles())

    def _renumber(
        self, records: Sequence[PageObject], used: Optional[set] = None, keep_free: bool = False
    ) -> None:
        """Give a cloned block fresh page-unique handles."""
        if used is None:
            used = self._used_handles()
        from ..format import genie_place as GP

        owner_of: List[Optional[int]] = []  # an input record: its owner's index
        stack: list = []  # [owner index, children left]
        for i, po in enumerate(records):
            while stack and stack[-1][1] <= 0:
                stack.pop()
            owner = None
            if stack:
                owner = stack[-1][0]
                stack[-1][1] -= 1
            owner_of.append(owner if po.type_code in geom.INPUT_TYPE_CODES else None)
            if po.class_flag > 0:
                stack.append([i, po.class_flag])
        new: List[Optional[int]] = [None] * len(records)
        if keep_free:
            for i, po in enumerate(records):
                h0 = geom.get_handle(po) if owner_of[i] is None else None
                if h0 is not None and 0 <= h0 < 31999 and h0 not in used:
                    new[i] = h0
                    used.add(h0)
        next_free = GP.handle_allocator(used)
        for i in range(len(records)):
            if owner_of[i] is None and new[i] is None:
                new[i] = next_free()
        for i, po in enumerate(records):
            geom.set_handle(po, new[i] if owner_of[i] is None else new[owner_of[i]])

    # -- geometry edits ----------------------------------------------------------
    def move_object(self, index: int, dx: int, dy: int) -> None:
        self.move_objects([index], dx, dy)

    def move_objects(self, indices: Sequence[int], dx: int, dy: int) -> None:
        """Translate objects (anchor + rects + vertex arrays together)."""
        if not indices or (dx == 0 and dy == 0):
            return
        # a container never moves without its members
        idx = self.expand_to_subtrees(i for i in indices if 0 <= i < len(self.objects))

        model_moved = []

        def shift(ddx, ddy):
            fit = self._fitting_ancestors(idx)
            keep = self._held_text_bounds(idx)
            self._translate_records(idx, ddx, ddy)
            self._rebound_moved_texts(idx, keep)
            self._shift_model(idx, ddx, ddy)
            model_moved.append(True)
            for a in fit:
                self._refit_container(a)

        fit0 = self._fitting_ancestors(idx)
        before = self._record_states(list(idx) + fit0)
        carried = self._turned_points(idx)
        try:
            shift(dx, dy)
            self._carry_turned(carried, dx, dy)
        except (struct.error, OverflowError) as e:
            # (a position the page cannot store: every record and the model back as they
            # were, no undo step)
            self._put_record_states(before)
            pending = self.__dict__.get("_pending_states")
            if pending and pending[-1] is before:
                pending.pop()
            if model_moved:
                self._shift_model(idx, -dx, -dy)
            for a in fit0:
                self._container_model_from_record(a)
            raise TransformError(COORD_REFUSED) from e

        def undo():
            # the records' own bytes back, not the inverse move: a move RE-DERIVES the moved
            # records' transform slots 2 / 3 and linked symbols from their Locals, so moving
            # back gives the re-derived slots instead of what the file stored; the model
            # follows in place, as the move's did (and the next derive re-derives these units)
            self._put_record_states(before)
            self._shift_model(idx, -dx, -dy)
            for a in fit0:
                self._container_model_from_record(a)

        # redo puts the records' states after the move back (moving again re-derives every
        # moved record, slow on a large genie); the states are compacted first, so the
        # step's size counts them too
        self._compact_states()
        after = self._after_states(before)

        def redo():
            if after is None:
                shift(dx, dy)
                return
            self._put_record_states(after)
            self._shift_model(idx, dx, dy)
            for a in fit0:
                self._container_model_from_record(a)

        self._push_step(_Command("move", redo, undo))
        # (a move does not derive: what a page window and the Simulate window watch for an edit)
        self._note_edit()

    def _held_text_bounds(self, idx: Sequence[int]) -> set:
        """The texts of ``idx`` in a plain group turned since their load or save: they keep
        their Bounds across a move (:meth:`_rebound_moved_texts`)."""
        objs = self.parsed.page.objects
        held = self._turned_points([i for i in idx if objs[i].type_code == 0x05])
        return {i for i, m in held.items() if len(m) > 3 and m[3] is not None and self._in_plain_group(i)}

    def _rebound_moved_texts(self, idx: Sequence[int], keep=frozenset()) -> None:
        """Derive a moved text's bounds again from its extent."""
        objs = self.parsed.page.objects
        for i in idx:
            po = objs[i]
            if po.type_code != 0x05 or po.raw_payload is not None or not po.subtype_size & 0x2 or i in keep:
                continue
            gh = geom.parse_geom_header(po.body_raw)
            flags = geom.style_flags(po)
            if gh is None or flags is None:
                continue
            r1 = geom.norm_rect(gh.rect1)
            pb = geom.predict_bounds(0x05, r1, flags) or r1
            pb = tuple(int(v) for v in pb)
            if pb != geom.norm_rect(gh.rect2):
                geom._set_rect2(po, pb)

    def _shift_model(self, idx: Sequence[int], ddx: int, ddy: int) -> None:
        """Move the derived objects of records ``idx`` in place (a move does not derive; their
        units are derived again on the next derive)."""
        self._touch_model(idx, move=(ddx, ddy))
        for i in idx:
            if i < len(self.page.objects):
                o = self.page.objects[i]
                g = o.geometry
                g.x += ddx
                g.y += ddy
                g.nodes = [(x + ddx, y + ddy) for x, y in g.nodes]
                # resolved symbol/genie art is positioned by the cached placement
                # transform; translate it too or the art snaps back on rebuild.
                xf = o.raw.get("_symbol_xform")
                if xf:
                    o.raw["_symbol_xform"] = (xf[0] + ddx, xf[1] + ddy, xf[2], xf[3])
                for c in o.children:
                    cxf = c.raw.get("_symbol_xform")
                    if cxf:
                        c.raw["_symbol_xform"] = (cxf[0] + ddx, cxf[1] + ddy, cxf[2], cxf[3])
                self._model_header_from_record(i)
        # a label moved off a stack of identical gated labels, or from over a hidden copy:
        # the copies' flags as a derive gives them (the views build those again)
        from ..io.reader import restack_labels

        changed = restack_labels(self.page, idx)
        if changed:
            self._touch_model(changed)

    def _model_header_from_record(self, index: int) -> None:
        """Copy the record's current header values into its derived object."""
        if index >= len(self.page.objects):
            return
        o = self.page.objects[index]
        g = getattr(o, "geometry", None)
        if g is None:
            return
        gh = geom.parse_geom_header(self.parsed.page.objects[index].body_raw)
        if gh is None:
            return
        raw = o.raw
        for key, val in (("_anchor", gh.anchor), ("_rect1", gh.rect1), ("_rect2", gh.rect2)):
            if key in raw:
                raw[key] = tuple(val)
        lt = geom.get_local_transform(self.parsed.page.objects[index])
        t = g.transform
        vals = lt[1] if lt is not None else (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        if (t.a, t.b, t.c, t.d, t.h, t.k) != tuple(vals):
            g.transform = type(t)(*vals)

    def _container_model_from_record(self, index: int) -> None:
        """A container's derived extent from its record's rect1 (what _refit_container sets
        after rewriting the record), and its header extras (:meth:`_model_header_from_record`)."""
        po = self.parsed.page.objects[index]
        gh = geom.parse_geom_header(po.body_raw)
        self._touch_model([index])
        if gh is None or index >= len(self.page.objects):
            return
        l, t, r, b = geom.norm_rect(gh.rect1)
        g = self.page.objects[index].geometry
        g.x, g.y = int(l), int(t)
        g.extent_x, g.extent_y = int(r - l), int(b - t)
        self._model_header_from_record(index)

    def _record_states(self, indices) -> list:
        """(record, a copy of its state) of page records ``indices`` -- every field, bytes
        copied, like _edit_snapshot keeps them: an undo that must give the stored bytes back
        exactly puts these back (:meth:`_put_record_states`) instead of editing back."""
        objs = self.parsed.page.objects
        out = [
            (objs[i], self._state_copy(objs[i].__dict__)) for i in sorted(set(indices)) if 0 <= i < len(objs)
        ]
        # once the edit is made, only the fields it changed are kept (_compact_states)
        self.__dict__.setdefault("_pending_states", []).append(out)
        return out

    @staticmethod
    def _state_copy(state: dict) -> dict:
        return {
            k: (bytearray(v) if isinstance(v, bytearray) else list(v) if isinstance(v, list) else v)
            for k, v in state.items()
        }

    def _put_record_states(self, states: list) -> None:
        # every changed-block field is checked first: an undo that cannot be made leaves
        # every record as it is
        for item in states:
            if len(item) == 3:
                o, st, _added = item
                for k, v in st.items():
                    if isinstance(v, _BlockDiff):
                        cur = o.__dict__.get(k)
                        if cur is None or len(cur) != v.size:
                            raise ValueError(
                                (
                                    "This step cannot be undone: a later save of the genie or template "
                                    "rewrote the objects it changed."
                                )
                            )
        for item in states:
            if len(item) == 3:  # compacted: the changed fields only
                o, st, added = item
                for k in added + self._CACHE_FIELDS:
                    o.__dict__.pop(k, None)
                st = {
                    k: (v.undo(o.__dict__.get(k)) if isinstance(v, _BlockDiff) else v) for k, v in st.items()
                }
                o.__dict__.update(self._state_copy(st))
                continue
            o, st = item
            o.__dict__.clear()
            o.__dict__.update(self._state_copy(st))

    def _after_states(self, states: list) -> Optional[list]:
        """The states the records of compacted ``states`` (:meth:`_compact_states`) have now,
        in the same form, for a redo that puts them back (:meth:`_put_record_states`): a
        byte field the edit changed in a few blocks as the blocks it has now, another field
        as a copy, and the keys the edit removed to drop. None when one is not compacted."""
        out = []
        for item in states:
            if len(item) != 3:
                return None
            o, changed, added = item
            cur = o.__dict__
            st, gone = {}, []
            for k, v in changed.items():
                if k not in cur:
                    gone.append(k)
                    continue
                now = cur[k]
                if isinstance(v, _BlockDiff):
                    try:
                        d = _BlockDiff.of(now, v.undo(now))
                    except ValueError:
                        return None
                    st[k] = d if d is not None else self._state_copy({k: now})[k]
                else:
                    st[k] = self._state_copy({k: now})[k]
            for k in added:
                st[k] = self._state_copy({k: cur[k]})[k]
            out.append((o, st, tuple(gone)))
        return out

    #: keep only an edit's changed blocks of a byte field for its undo (False: the whole
    #: value; ElementDocument, whose save rewrites the records the undo steps hold in place)
    BLOCK_DIFFS = True

    _MISSING = object()
    #: per-record caches an undo does not keep (they are rebuilt from the bytes when needed)
    _CACHE_FIELDS = (
        writer.FRESH_PARSE_ATTR,
        writer.NODE_ARRAY_MEMO_ATTR,
        _colourstate.LAYOUT_MEMO_ATTR,
        _placement.SLOTS_MEMO_ATTR,
        writer.VALUE_BAG_MEMO_ATTR,
    )

    def _compact_states(self) -> None:
        """The saved record states of the edit just made keep only the fields the edit
        changed. Equality is checked identity first, so an unchanged
        bag list (the move kept its parse) costs nothing to compare."""
        pending = self.__dict__.pop("_pending_states", None)
        if not pending:
            return
        miss = self._MISSING
        for states in pending:
            for k, item in enumerate(states):
                if len(item) != 2:
                    continue
                o, st = item
                cur = o.__dict__
                changed = {}
                for key, v in st.items():
                    if key in self._CACHE_FIELDS:
                        continue
                    now = cur.get(key, miss)
                    if now is v:
                        continue
                    if now is miss or type(now) is not type(v) or now != v:
                        # a byte field of the same length keeps only the blocks the edit
                        # changed (a move changes a few bytes of each record), not in a
                        # document whose save rewrites its records in place
                        # (BLOCK_DIFFS: a library element)
                        d = _BlockDiff.of(v, now) if now is not miss and self.BLOCK_DIFFS else None
                        changed[key] = d if d is not None else v
                added = tuple(key for key in cur if key not in st and key not in self._CACHE_FIELDS)
                states[k] = (o, changed, added)

    # -- rotate / mirror (CtDraw Arrange > Rotate... / Mirror...) -------------
    # gradient direction after a quarter turn / mirror (AttributeGradientMode:
    # 1 L>R, 2 R>L, 3 T>B, 4 B>T, 5/6 edge<->middle across the height, 7/8
    # across the width): a CW turn sends the left edge to the top. CtDraw's
    # own bake of a rectangle or ellipse leaves the gradient mode alone,
    # so its gradient keeps pointing the old way; ctgedit turns it with the
    # shape unless TURN_GRADIENTS is off (Settings > Editing). A line keeps
    # its true direction in both
    #: turn a gradient with its shape (opt/turn_gradients, ui.settings.apply_drawing_options)
    TURN_GRADIENTS = True
    #: a quarter turn about a half-pixel centre shifts onto whole pixels so that four turns
    #: give the start back, instead of CtDraw's truncation (opt/reversible_turns)
    REVERSIBLE_TURNS = False
    #: a text resized unevenly keeps the stretch as a matrix, as CtDraw does; off: its point
    #: size follows the new height (opt/stretch_texts, ui.settings.apply_drawing_options)
    STRETCH_TEXTS = True
    _GRADIENT_TURN = {
        "cw": {1: 3, 2: 4, 3: 2, 4: 1, 5: 7, 6: 8, 7: 5, 8: 6},
        "acw": {1: 4, 2: 3, 3: 1, 4: 2, 5: 7, 6: 8, 7: 5, 8: 6},
        "mh": {1: 2, 2: 1},
        "mv": {3: 4, 4: 3},
    }

    def _turn_shape_tail(self, po: PageObject, op: Optional[str]) -> None:
        """Turn the parts a box rotate leaves alone: ellipse angles and gradient direction."""
        if not op:
            return
        tail = geom.parse_shape_tail(po)
        if tail is None:
            return
        if tail["kind"] == "ellipse":
            s, e = tail["start"], tail["end"]
            if op == "cw":
                s, e = s + 90, e + 90
            elif op == "acw":
                s, e = s - 90, e - 90
            elif op == "mh":
                s, e = -e, -s
            else:
                s, e = 180 - e, 180 - s
            if max(s, e) >= 360 and min(s, e) > 0:
                s, e = s - 360, e - 360
            elif min(s, e) <= -360 and max(s, e) < 0:
                s, e = s + 360, e + 360
            # a sweep too wide to shift as a pair: fold each angle like CtDraw
            s, e = int(math.fmod(s, 360)), int(math.fmod(e, 360))
            if (s, e) != (tail["start"], tail["end"]):
                geom.set_ellipse_angles(po, s, e)
                tail = geom.parse_shape_tail(po) or tail
        mode = tail.get("grad_mode") or 0
        if not type(self).TURN_GRADIENTS:
            return
        new = self._GRADIENT_TURN.get(op, {}).get(mode, mode)
        if new != mode:
            geom.set_gradient(po, new)

    def _transform_one(
        self, po: PageObject, fn, op: Optional[str] = None, Q=None, inherited: bool = False
    ) -> None:
        """Apply a point transform to one record's shape."""
        gh = geom.parse_geom_header(po.body_raw)
        if gh is None:
            return
        from ..format import legacy

        r1 = geom.norm_rect(gh.rect1)
        # a 1x1 RECTANGLE is still a box: CtDraw turns its corners and keeps
        # its (l, b) hotspot
        if (
            po.type_code in geom.INPUT_TYPE_CODES
            or po.type_code in legacy.POINT_TCS
            or po.type_code == 0x1004
            or (r1[2] - r1[0] <= 1 and r1[3] - r1[1] <= 1 and po.type_code != 0x0F)
        ):
            # an input record's icon box / a point class's natural extent (a
            # point Extent: V3/V4 legacy symbols too): the object is placed by
            # its point, never turned (like a resize)
            ax, ay = gh.anchor
            nx, ny = fn((ax, ay))
            geom.translate_object(
                po,
                int(round(nx)) - ax,
                int(round(ny)) - ay,
                slot_shifts=(None, None, None) if inherited else None,
            )
            return
        if po.type_code == 0x01 and not (po.subtype_size & 0x2):
            # an art-stripped (library-linked) line: its endpoints live in the
            # twin; the record keeps the normalised extent + its start point
            # as the anchor (any corner) -- turn both, keep the Bounds offsets
            na = geom.find_node_array(po)
            d = geom._rect2_deltas(gh)
            l, t, r, b = gh.rect1
            (x1, y1), (x2, y2) = fn((l, t)), fn((r, b))
            r1 = (
                int(round(min(x1, x2))),
                int(round(min(y1, y2))),
                int(round(max(x1, x2))),
                int(round(max(y1, y2))),
            )
            ax, ay = fn(tuple(gh.anchor))
            body = bytearray(po.body_raw)
            struct.pack_into("<2h", body, gh.anchor_off, int(round(ax)), int(round(ay)))
            struct.pack_into("<4h", body, gh.rect1_off, *r1)
            struct.pack_into("<4h", body, gh.rect2_off, *(r1[i] + d[i] for i in range(4)))
            if na is not None:
                for n, p in enumerate(geom.read_nodes(bytes(body), na)):
                    px, py = fn(p)
                    struct.pack_into("<2h", body, na.data_off + n * 4, int(round(px)), int(round(py)))
            po.body_raw = bytes(body)
            geom.refresh_bags(po)
            return
        if po.type_code == 0x01:
            # the true draw-order endpoints (a free line's rect1 is normalised)
            pts = geom.get_object_nodes(po) or [gh.rect1[:2], gh.rect1[2:]]
            (x1, y1), (x2, y2) = fn(tuple(pts[0])), fn(tuple(pts[1]))
            geom._set_line_endpoints(po, int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2)))
            return
        na = geom.find_node_array(po)
        if na is not None:
            pts = [fn(p) for p in geom.read_nodes(po.body_raw, na)]
            geom.set_object_nodes(po, [(int(round(x)), int(round(y))) for x, y in pts])
            self._turn_shape_tail(po, op)
            return
        if (
            po.type_code == 0x05
            and not inherited
            and getattr(fn, "pivot", None) is not None
            and geom.parse_text_tail(po) is not None
            and (op in ("mh", "mv") or (op in ("cw", "acw") and self._text_flipped(po)))
        ):
            self._turn_flipped_text(po, fn, op, inherited)
            return
        if op in ("cw", "acw") and po.type_code == 0x05 and self._text_flipped(po):
            self._turn_flipped_text(po, fn, op, inherited)
            return
        if op in ("cw", "acw") and po.type_code == 0x05 and geom.parse_text_tail(po) is not None:
            # CtDraw's text rotation: the baseline ANCHOR is
            # transformed, the orient turns, and the box is re-measured
            self._hold_text_turn(po, fn, op, inherited)
            self._orient_content(po, op, gh)
            tail = geom.parse_text_tail(po)
            ax, ay = fn(tuple(tail["echo"]))
            anchor = (int(round(ax)), int(round(ay)))
            body = bytearray(po.body_raw)
            struct.pack_into("<2h", body, gh.anchor_off, *anchor)
            struct.pack_into("<2h", body, tail["echo_off"], *anchor)
            po.body_raw = bytes(body)
            if not geom.refit_text(po):
                l, t, r, b = gh.rect1
                p1, p2 = fn((l, t)), fn((r, b))
                geom._retarget_header(
                    po,
                    geom.norm_rect(gh.rect1),
                    (
                        int(round(min(p1[0], p2[0]))),
                        int(round(min(p1[1], p2[1]))),
                        int(round(max(p1[0], p2[0]))),
                        int(round(max(p1[1], p2[1]))),
                    ),
                )
            else:
                # the box is measured again, and the bounds the page stores are the measured
                # box's (the fixture capture, turns T03 / T10: rect2 = rect1). The box CtDraw
                # keeps in memory for the next turn's centre is the old box turned (T26: the
                # second turn centres on it): the extent the text was loaded or saved with,
                # or that box after a turn since (_TurnPoints.box)
                pts = getattr(fn, "points", None)
                if pts is not None:
                    old = pts.box() or geom.norm_rect(gh.rect1)
                    p1, p2 = fn(old[:2]), fn(old[2:])
                    box = (
                        int(min(p1[0], p2[0])),
                        int(min(p1[1], p2[1])),
                        int(max(p1[0], p2[0])),
                        int(max(p1[1], p2[1])),
                    )
                    pts.set_box(box)
                    if not inherited and self._in_plain_group(pts.at):
                        # a text in a plain group turns through the group's Local, and
                        # the save composes that onto it: the extent is measured again,
                        # the Bounds are the box in memory (the fixture capture, turns
                        # T11)
                        self._put_text_bounds(po, box)
            return
        if op:
            if self._orient_content(po, op, gh, Q, inherited, fn=fn):
                return  # fully placed (a linked symbol)
        l, t, r, b = gh.rect1
        p1, p2 = fn((l, t)), fn((r, b))
        box = (
            int(round(min(p1[0], p2[0]))),
            int(round(min(p1[1], p2[1]))),
            int(round(max(p1[0], p2[0]))),
            int(round(max(p1[1], p2[1]))),
        )
        geom.resize_object(po, box)
        if po.type_code != 0x11:
            self._keep_anchor_rule(po, geom.norm_rect(gh.rect1), tuple(gh.anchor), box, fn)
        self._turn_shape_tail(po, op)
        if po.type_code == 0x05 and geom.sync_text_echo(po):
            # a mirrored text keeps a LOCAL echo; its box = Total(measured)
            geom.refit_text(po)
            geom.refresh_bags(po)

    def _in_plain_group(self, index) -> bool:
        """Record ``index`` is a member of a group and sits under no transform carrier
        (:meth:`_under_carrier`)."""
        sem = self.page.objects if self.page is not None else []
        if index is None or index >= len(sem) or sem[index].parent is None:
            return False
        return not self._under_carrier()(index)

    @staticmethod
    def _put_text_bounds(po: PageObject, box) -> None:
        """Write a text's Bounds (rect2) for the extent ``box``, its style's Bounds rule
        applied (:func:`geom.predict_bounds`)."""
        gh = geom.parse_geom_header(po.body_raw)
        flags = geom.style_flags(po)
        if gh is None or flags is None:
            return
        r2 = geom.predict_bounds(0x05, box, flags) or box
        body = bytearray(po.body_raw)
        struct.pack_into("<4h", body, gh.rect2_off, *(int(v) for v in r2))
        po.body_raw = bytes(body)

    def _hold_text_turn(self, po: PageObject, fn, op: str, inherited: bool) -> None:
        """Remember a quarter turn of a text the way CtDraw holds it in memory."""
        from ..format import placement

        pts = getattr(fn, "points", None)
        if pts is None or inherited or pts.at is None or getattr(fn, "pivot", None) is None:
            return
        lt = geom.get_local_transform(po)
        if lt is not None and (lt[0] or any(abs(x - y) > 1e-12 for x, y in zip(lt[1], placement.IDENT))):
            return
        q = 1 if op == "cw" else 3
        if self._in_plain_group(pts.at):
            m0, L0, mg, G = pts.group_turn() or (0, placement.IDENT, 0, placement.IDENT)
            G, mg = placement.turn_local(G, mg, q, fn.pivot, fn.shift)
            pts.set_group((m0, L0, mg, G))
        else:
            mode, L = pts.turn() or (0, placement.IDENT)
            L, mode = placement.turn_local(L, mode, q, fn.pivot, fn.shift)
            pts.set_local(mode, L)

    def _unturn_text(self, po: PageObject, L, pts) -> None:
        """Give a text the orient and the echo it had before the quarter turns ``L`` it
        holds in memory (:meth:`_hold_text_turn`): the orient less the turns, the echo the
        true one turned back. A turned echo's true value has the fraction of L's offset (the
        echo before was whole), which the written one truncates toward zero."""
        from ..format import placement

        tail = geom.parse_text_tail(po)
        if tail is None:
            return
        a, b = L[0], L[1]
        q = 1 if b > 0.5 else 3 if b < -0.5 else 2 if a < 0 else 0
        turns = (geom.ORIENT_TO_TURNS.get(tail["orient"], 0) - q) % 4
        geom.set_text_orient(po, geom.TURNS_TO_ORIENT[turns])
        tail = geom.parse_text_tail(po)
        echo = tail["echo"]
        known = pts.true(echo)
        true = []
        for axis, off in ((0, L[4]), (1, L[5])):
            f = off - math.floor(off)
            w = echo[axis]
            v = _with_fraction(w, f) if 1e-9 < f < 1.0 - 1e-9 else float(w)
            true.append(known[axis] if v is None else v)
        inv = placement._affine_inv(L)
        bx = inv[0] * true[0] + inv[2] * true[1] + inv[4]
        by = inv[1] * true[0] + inv[3] * true[1] + inv[5]
        body = bytearray(po.body_raw)
        struct.pack_into("<2h", body, tail["echo_off"], int(round(bx)), int(round(by)))
        po.body_raw = bytes(body)

    @staticmethod
    def _text_flipped(po: PageObject) -> bool:
        """A text that keeps a flip or a turn as a matrix in its Local (a mirror)."""
        lt = geom.get_local_transform(po)
        return lt is not None and bool(lt[0] & 0x06) and geom.parse_text_tail(po) is not None

    def _turn_flipped_text(self, po: PageObject, fn, op: str, inherited: bool) -> None:
        """Mirror or turn a text that carries a matrix."""
        from ..format import placement

        pts = getattr(fn, "points", None)
        lt = geom.get_local_transform(po)
        mode, L = lt if lt else (0, placement.IDENT)
        group = None
        held = None
        grouped = pts is not None and not inherited and self._in_plain_group(pts.at)
        if pts is not None and not inherited and op in ("mh", "mv") and not mode:
            # a mirror of a text turned since its load or save: CtDraw holds the turns in
            # the text's Local, or its plain group's (_hold_text_turn), and the mirror
            # composes onto them
            held = pts.group_turn() if grouped else pts.turn()
            if held is not None:
                self._unturn_text(
                    po,
                    placement.compose_after(held[1], held[0], held[3], held[2])[0] if grouped else held[1],
                    pts,
                )
        if grouped:
            # a text in a plain group turns through the group's own Local, as a genie
            # there does (nested_op of _transform_objects): its Local in memory is the
            # group's composed onto its base (the mode bytes ORed), which the
            # save composes in for good (the fixture capture, turns T32: turning the
            # text's own Local gives a zero the wrong sign)
            group = held or pts.group((mode, L))
            if group is None:
                group = pts.local((mode, L)) + (0, placement.IDENT)
        elif held is not None:
            mode, L = held
        elif pts is not None:
            mode, L = pts.local((mode, L))
        if inherited:
            L2, m2 = L, mode  # (its Local is its genie chain's)
        elif group is not None:
            m0, L0, mg, G = group
            if op in ("mh", "mv"):
                G, mg = placement.mirror_local(G, mg, op == "mh", fn.pivot)
            else:
                G, mg = placement.turn_local(G, mg, 1 if op == "cw" else 3, fn.pivot, fn.shift)
            group = (m0, L0, mg, G)
            L2, m2 = placement.compose_after(L0, m0, G, mg)
        elif op in ("mh", "mv"):
            L2, m2 = placement.mirror_local(L, mode, op == "mh", fn.pivot)
        else:
            L2, m2 = placement.turn_local(L, mode, 1 if op == "cw" else 3, fn.pivot, fn.shift)
        tail = geom.parse_text_tail(po)
        gh = geom.parse_geom_header(po.body_raw)
        if tail is None or gh is None:
            return
        old_box = geom.norm_rect(gh.rect1)
        ex, ey = tail["echo"]
        a, b, c, d, h, k = L2
        anchor = (int(a * ex + c * ey + h), int(b * ex + d * ey + k))
        # a mirrored text resized keeps the scale in its matrix (scale_text_matrix); the
        # mirror undone leaves a uniform scale with no flip, which CtDraw bakes into the
        # font as it bakes any uniform, unmirrored transform of a text
        # (uniform = the text's own box keeps its aspect within 0.05). A scale over quarter
        # turns stays a matrix (the turns are held in memory,
        # not baked, until the save)
        scale = None if inherited else geom.uniform_text_scale(L2, geom.local_box(L, old_box))
        if scale is not None and scale[1] == (1.0, 0.0, 0.0, 1.0):
            geom.bake_text_scale(po, scale[0])
            a, b, c, d = scale[1]
        plain = (a, b, c, d) == (1.0, 0.0, 0.0, 1.0)
        # quarter turns alone (a mirror undone): the page stores them as the orient, as
        # for a text turned with no flip, and memory keeps them as the Local
        # (_hold_text_turn)
        turns = (
            not plain
            and not inherited
            and pts is not None
            and _turn_only(L2)
            and abs(math.hypot(c, d) - 1.0) < 1e-9
        )
        if turns:
            q = 1 if b > 0.5 else 3 if b < -0.5 else 2
            geom.set_text_orient(
                po, geom.TURNS_TO_ORIENT[(geom.ORIENT_TO_TURNS.get(tail["orient"], 0) + q) % 4]
            )
        if plain or turns:
            geom.set_local_transforms(po, 0, placement.IDENT)
        else:
            geom.set_local_transforms(po, m2, L2)
        gh = geom.parse_geom_header(po.body_raw)
        tail = geom.parse_text_tail(po)
        body = bytearray(po.body_raw)
        struct.pack_into("<2h", body, gh.anchor_off, *anchor)
        if plain or turns:
            struct.pack_into("<2h", body, tail["echo_off"], *anchor)
        po.body_raw = bytes(body)
        if pts is not None:
            if plain:
                pts.set_local(0, placement.IDENT)
            else:
                pts.set_local(m2, L2)
                if group is not None:
                    pts.set_group(group)
        measured = geom.refit_text(po)
        (x1, y1), (x2, y2) = fn(old_box[:2]), fn(old_box[2:])
        box = (int(min(x1, x2)), int(min(y1, y2)), int(max(x1, x2)), int(max(y1, y2)))
        if not measured:
            # (no metrics here: the old box goes where the operation puts it)
            geom._retarget_header(po, old_box, box)
        elif turns:
            # (the box in memory is the old box turned, as for a turned text)
            pts.set_box(box)
            if group is not None:
                self._put_text_bounds(po, box)
        geom.refresh_bags(po)

    @staticmethod
    def _keep_anchor_rule(po: PageObject, old, anchor, new, fn) -> None:
        """Keep a box's hotspot where its class puts it."""
        gh = geom.parse_geom_header(po.body_raw)
        if gh is None:
            return
        if po.type_code in (0x10, 0x0A, 0x0B):
            # a polygon / pipe without its own vertices (art-stripped): the
            # hotspot is its first vertex, a point of the content, even when
            # it lies on a corner of the box (the turned point, not the new
            # box's top-left)
            fx, fy = fn(anchor)
            if (int(round(fx)), int(round(fy))) != tuple(gh.anchor):
                body = bytearray(po.body_raw)
                struct.pack_into("<2h", body, gh.anchor_off, int(round(fx)), int(round(fy)))
                po.body_raw = bytes(body)
            return

        def axis(v, lo, hi, nlo, nhi):
            for off, base, sign in ((0, lo, 1), (0, hi, -1), (1, lo, 1), (1, hi, -1)):
                if v == base + sign * off:
                    return (nlo if sign > 0 else nhi) + sign * off
            return None

        ax = axis(anchor[0], old[0], old[2], new[0], new[2])
        ay = axis(anchor[1], old[1], old[3], new[1], new[3])
        if ax is None or ay is None:
            fx, fy = fn(anchor)
            ax, ay = int(round(fx)), int(round(fy))
        if (ax, ay) != tuple(gh.anchor):
            body = bytearray(po.body_raw)
            struct.pack_into("<2h", body, gh.anchor_off, ax, ay)
            po.body_raw = bytes(body)

    def _orient_content(self, po: PageObject, op: str, gh, Q=None, inherited: bool = False, fn=None) -> bool:
        """Orient a box record's content the way CtDraw stores it for its kind."""
        l, t, r, b = gh.rect1
        cx, cy = (l + r) / 2.0, (t + b) / 2.0
        if Q is None:
            if op == "cw":
                Q = (0.0, 1.0, -1.0, 0.0, cx + cy, cy - cx)
            elif op == "acw":
                Q = (0.0, -1.0, 1.0, 0.0, cx - cy, cx + cy)
            elif op == "mh":
                Q = (-1.0, 0.0, 0.0, 1.0, 2.0 * cx, 0.0)
            else:
                Q = (1.0, 0.0, 0.0, -1.0, 0.0, 2.0 * cy)
        if po.type_code == 0x05:
            tail = geom.parse_text_tail(po)
            if op in ("cw", "acw"):
                if tail is not None:
                    turns = geom.ORIENT_TO_TURNS.get(tail["orient"], 0)
                    turns = (turns + (1 if op == "cw" else 3)) % 4
                    geom.set_text_orient(po, geom.TURNS_TO_ORIENT[turns])
            elif not inherited:
                lt = geom.get_local_transform(po)
                pivot = getattr(fn, "pivot", None) if fn is not None else None
                if pivot is None:
                    m = geom.compose_transform(lt[1] if lt else (1, 0, 0, 1, 0, 0), Q)
                    geom.set_local_transforms(po, self._mode_for_matrix(m), m)
                    return
                from ..format import placement

                pts = getattr(fn, "points", None)
                mode, L = lt if lt else (0, placement.IDENT)
                if pts is not None:
                    mode, L = pts.local((mode, L))
                L2, m2 = placement.mirror_local(L, mode, op == "mh", pivot)
                geom.set_local_transforms(po, m2, L2)
                if pts is not None:
                    pts.set_local(m2, L2)
            return
        if po.type_code == 0x07:
            if self._bitmap_pixels_op(po, op, gh):
                return
            if inherited:
                return
            from ..format.symbols import reference_from_object

            try:
                has_ref = reference_from_object(po) is not None
            except Exception:  # noqa: BLE001
                has_ref = False
            if not has_ref:
                return
            lt = geom.get_local_transform(po)
            m = geom.compose_transform(lt[1] if lt else (1, 0, 0, 1, 0, 0), Q)
            from ..format import placement

            info = self._link_info(po) if placement.is_linked_symbol(po) else None
            if info is not None:
                # a placed symbol: Local is the ONLY stored placement; slots
                # 2/3 + rect1 + anchor are re-derived (no rect resize after)
                placement.rederive_linked(po, m, self._mode_for_matrix(m), info["e"], info["N"])
                geom.refresh_bags(po)
                return True
            geom.set_local_transforms(po, self._mode_for_matrix(m), m)
        return False

    @staticmethod
    def _mode_for_matrix(m) -> int:
        """Derive the transform mode byte from a full 2x3 matrix (bitfield:
        0x01 translate, 0x02 scale/flip, 0x04 rotate + quadrant<<4)."""
        a, b, c, d, h, k = m
        mode = 0
        if abs(h) > 1e-9 or abs(k) > 1e-9:
            mode |= 0x01
        if abs(b) > 1e-9 or abs(c) > 1e-9:
            mode |= 0x04
            quad = 1 if b > 0 else 3
            mode |= quad << 4
        elif a < 0 and d < 0:
            mode |= 0x04 | (2 << 4)
        elif abs(a - 1) > 1e-9 or abs(d - 1) > 1e-9:
            mode |= 0x02
        if mode == 0:
            return 0
        # force a serialization nibble carrying every non-default double
        low = mode & 0x0F
        if (mode & 0x04) and low < 4:
            mode = (mode & 0xF0) | (low | 0x04)
        return mode

    def _bitmap_pixels_op(self, po: PageObject, op: str, gh) -> bool:
        """Rotate/flip an EMBEDDED bitmap's DIB pixels (CtDraw stores no matrix for
        bitmaps). Returns False for non-bitmap tc=7."""
        from ..format.symbols import embedded_bitmaps_from_body, _find_stream

        body = po.body_raw
        start = gh.end if gh else 0
        primary, flash = embedded_bitmaps_from_body(body, start)
        if primary is None:
            return False
        hit = _find_stream(body, start)
        if hit is None or hit[0] < 12:
            return False
        off = hit[0]
        sec_start = off - 12
        flags, _uncomp, comp = struct.unpack_from("<III", body, sec_start)
        end = off + comp
        if (flags & 0xF00) == 0x100:
            if end + 4 > len(body):
                return False
            (comp2,) = struct.unpack_from("<I", body, end)
            end += 4 + comp2
        if end != len(body):
            return False  # unknown tail follows; do not touch

        def xform(eb):
            w, h, px = eb.width, eb.height, eb.rgba
            if op in ("cw", "acw"):
                out = bytearray(w * h * 4)
                for y in range(h):
                    row = px[y * w * 4 : (y + 1) * w * 4]
                    for x in range(w):
                        nx, ny = (h - 1 - y, x) if op == "cw" else (y, w - 1 - x)
                        d = (ny * h + nx) * 4
                        out[d : d + 4] = row[x * 4 : x * 4 + 4]
                return bytes(out), h, w
            if op == "mh":
                out = bytearray()
                for y in range(h):
                    row = px[y * w * 4 : (y + 1) * w * 4]
                    out += b"".join(row[x * 4 : x * 4 + 4] for x in range(w - 1, -1, -1))
                return bytes(out), w, h
            rows = [px[y * w * 4 : (y + 1) * w * 4] for y in range(h)]
            return b"".join(reversed(rows)), w, h

        def key(w, h, px, fpx):
            return hashlib.sha1(
                struct.pack("<II", w, h) + px + b"|" + (fpx if fpx is not None else b"")
            ).digest()

        # the DIB section this record had, by its pixels: turning a bitmap back
        # to pixels it had before reuses those exact bytes (our zlib stream is
        # not CtDraw's), so four quarter turns / two flips are byte-identical
        sections = self.__dict__.setdefault("_dib_sections", {})
        while len(sections) > 64:  # bounded: drop the oldest
            sections.pop(next(iter(sections)))
        sections.setdefault(
            key(primary.width, primary.height, primary.rgba, flash.rgba if flash is not None else None),
            body[sec_start:],
        )
        rgba1, w1, h1 = xform(primary)
        frgba = xform(flash)[0] if flash is not None else None
        known = sections.get(key(w1, h1, rgba1, frgba))
        if known is not None:
            po.body_raw = body[:sec_start] + known
            return True
        new = factory.make_bitmap_record(0, (0, 0, w1, h1), rgba1, w1, h1, flash_rgba=frgba)
        # reuse only the freshly-encoded DIB SECTION from the helper record
        ngh = geom.parse_geom_header(new.body_raw)
        nhit = _find_stream(new.body_raw, ngh.end if ngh else 0)
        po.body_raw = body[:sec_start] + new.body_raw[nhit[0] - 12 :]
        return True

    def _quarter_map(self, roots: Sequence[int], op: str, points: Optional[_TurnPoints] = None):
        """The point map and matrix of a quarter turn or mirror about the selection's centre."""
        from ..format import placement

        objs = self.parsed.page.objects
        if type(self).REVERSIBLE_TURNS:
            c = self._kept_turn_centre(roots)
            if c is not None:
                return self._turn_about((c[0], c[1], c[0], c[1]), op, points=points)
        boxes, exact = [], []
        for u in self._units_from(roots):
            ubox = self._unit_bbox(u)
            gh = geom.parse_geom_header(objs[u[0]].body_raw)
            r1 = geom.norm_rect(gh.rect1) if gh is not None else ubox
            if r1[2] - r1[0] <= 1 and r1[3] - r1[1] <= 1 and all(self._is_point_box(i) for i in u):
                # a point unit (V3/V4 animation point / legacy symbol): its
                # bounds are an editor icon, not geometry -- it turns about
                # the centre of its 1x1 extent (CtDraw's own PositionRotate:
                # the hotspot moves +1 px in x per quarter turn)
                ubox = r1
            elif points is not None and objs[u[0]].type_code == 0x05 and points.box(u[0]) is not None:
                # a text turned before keeps its turned box in memory, its old box turned
                # (the page stores the box measured again); a move takes it along (the
                # fixture capture, turns T06)
                ubox = points.box(u[0])
            elif points is not None and any(
                objs[i].type_code == 0x05 and points.box(i) is not None and self._in_plain_group(i)
                for i in u[1:]
            ):
                # a plain group's box in memory is the union of its members' boxes in
                # memory, a turned text's old box turned among them, where the page
                # stores the text's measured box (the fixture capture, turns T37)
                ubox = self._held_box(u[0], points)
            boxes.append(ubox)
            info = (
                self._link_info(objs[u[0]])
                if len(u) == 1 and placement.is_linked_symbol(objs[u[0]])
                else None
            )
            exact.append(placement.linked_extent(objs[u[0]], info) if info is not None else None)
        if not boxes:
            return None
        if all(x is not None for x in exact):
            # only placed symbols: everything derives from their Locals, so
            # turn about the exact (untruncated) extent -- a rounded pivot
            # would walk the symbol a pixel per turn
            boxes = exact
        l, t = min(x[0] for x in boxes), min(x[1] for x in boxes)
        r, b = max(x[2] for x in boxes), max(x[3] for x in boxes)
        return self._turn_about((l, t, r, b), op, whole_pixels=boxes is not exact, points=points)

    def _held_box(self, index: int, points: _TurnPoints, member: bool = False):
        """Record ``index``'s box as CtDraw holds it in memory: a turned text's old box
        turned (:meth:`_TurnPoints.box`), a plain group that follows the union rule the
        union of its members' boxes in memory, anything else its stored box (a
        ``member``'s Extent, as the union rule takes it)."""
        objs = self.parsed.page.objects
        if objs[index].type_code == 0x05 and points.box(index) is not None:
            return points.box(index)
        kids = [
            k
            for k in self._direct_children(index)
            if objs[k].type_code not in geom.INPUT_TYPE_CODES
            and geom.parse_geom_header(objs[k].body_raw) is not None
        ]
        if not kids or not self._container_fits(index):
            if member:
                return geom.norm_rect(geom.parse_geom_header(objs[index].body_raw).rect1)
            return self._obj_bbox(index)
        boxes = [self._held_box(k, points, True) for k in kids]
        return (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )

    def _turn_about(self, box, op: str, whole_pixels: bool = True, points: Optional[_TurnPoints] = None):
        """``(fn, Q, local_op)`` of a quarter turn or mirror about the centre of ``box``."""
        from ..format import placement

        l, t, r, b = box
        s = (0.0, 0.0)
        cx, cy = (l + r) / 2.0, (t + b) / 2.0
        if op == "cw":  # screen (y down): (dx, dy) -> (-dy, dx)
            Q = (0.0, 1.0, -1.0, 0.0, cx + cy + s[0], cy - cx + s[1])
        elif op == "acw":  # (dx, dy) -> (dy, -dx)
            Q = (0.0, -1.0, 1.0, 0.0, cx - cy + s[0], cx + cy + s[1])
        elif op == "mh":
            Q = (-1.0, 0.0, 0.0, 1.0, 2.0 * cx, 0.0)
        else:
            Q = (1.0, 0.0, 0.0, -1.0, 0.0, 2.0 * cy)

        def fn(p):
            x, y = p
            return (Q[0] * x + Q[2] * y + Q[4], Q[1] * x + Q[3] * y + Q[5])

        if whole_pixels:
            # a point of a shape, a node or an anchor turned about a half-pixel centre
            # lands on .5: CtDraw truncates it toward zero (an int cast) and keeps the
            # half for the next turn
            # (``points``); a Local takes the exact centre (local_op)
            Q0 = Q

            def fn(p):  # noqa: F811 -- the truncating map
                x, y = points.true(p) if points is not None else p
                tx = Q0[0] * x + Q0[2] * y + Q0[4]
                ty = Q0[1] * x + Q0[3] * y + Q0[5]
                out = (float(int(tx)), float(int(ty)))
                if points is not None:
                    points.note((tx, ty), out)
                return out

        # a Local (a placed symbol or genie, a mirrored text) takes the turn about the exact
        # centre, half pixel and all, as CtDraw composes it
        s_local = s

        def local_op(L, mode, parent):
            # the pivot in the carrier's parent space (the top carrier of a
            # chain sits under non-carriers: a translation at most)
            px, py = cx, cy
            if parent is not None:
                _pm, P = parent
                if abs(P[1]) > 1e-12 or abs(P[2]) > 1e-12 or P[0] != 1.0 or P[3] != 1.0:
                    Pi = placement._affine_inv(P)
                    M = placement.affine_mul(Pi, placement.affine_mul(Q, P))
                    L2 = geom.compose_transform(L if mode else placement.IDENT, M)
                    return L2, self._mode_for_matrix(L2)
                px, py = cx - P[4], cy - P[5]
            if op in ("cw", "acw"):
                return placement.turn_local(L, mode, 1 if op == "cw" else 3, (px, py), s_local)
            return placement.mirror_local(L, mode, op == "mh", (px, py))

        fn.points, fn.pivot, fn.shift = points, (cx, cy), s_local
        return fn, Q, local_op

    def _is_point_box(self, index: int) -> bool:
        """The record's Extent (rect1) is a point (1x1 or less)."""
        gh = geom.parse_geom_header(self.parsed.page.objects[index].body_raw)
        if gh is None:
            return True
        l, t, r, b = geom.norm_rect(gh.rect1)
        return r - l <= 1 and b - t <= 1

    def _follow_owner_hotspots(self, idx, parents, conts, before) -> None:
        """Keep input records on their owner's hotspot after a turn."""
        objs = self.parsed.page.objects
        for k, i in enumerate(idx):
            po = objs[i]
            p = parents[k]
            if po.type_code not in geom.INPUT_TYPE_CODES or p is None or p in conts:
                continue
            was = geom.parse_geom_header(before[i][0])
            owner_was = geom.parse_geom_header(before[idx[p]][0])
            if was is None or owner_was is None or tuple(was.anchor) != tuple(owner_was.anchor):
                continue
            now = geom.parse_geom_header(po.body_raw)
            owner = geom.parse_geom_header(objs[idx[p]].body_raw)
            if now is None or owner is None:
                continue
            dx = owner.anchor[0] - now.anchor[0]
            dy = owner.anchor[1] - now.anchor[1]
            if dx or dy:
                geom.translate_object(po, dx, dy, slot_shifts=(None, None, None))

    def _follow_owner_frames(self, idx, parents, conts, before) -> None:
        """Keep a mirrored text's input records in its frame."""
        from ..format import legacy, placement

        objs = self.parsed.page.objects
        for k, i in enumerate(idx):
            po = objs[i]
            p = parents[k]
            if po.type_code not in geom.INPUT_TYPE_CODES or p is None or p in conts:
                continue
            owner = objs[idx[p]]
            if owner.type_code != 0x05:
                continue
            (_a, _b, (m_old, t_old, _o, _n)), _e = placement.read_slots(before[idx[p]][0])
            (_a, _b, (m_new, t_new, _o, _n)), _e = placement.read_slots(owner.body_raw)
            if (m_old, tuple(t_old)) == (m_new, tuple(t_new)):
                continue
            ct = legacy.class_tail(po)  # the point section, == anchor here
            (s1, s2, _s3), _e = placement.read_slots(po.body_raw)
            if s1[1] == placement.IDENT:
                mode, T = m_new, t_new
            else:
                T = geom.compose_transform(s1[1], t_new)
                mode = self._mode_for_matrix(T)
            placement.write_slots(po, [(s1[0], s1[1]), (s2[0], s2[1]), (mode, T)])
            a, b, c, d, h, kk = T
            det = a * d - b * c
            if ct is not None and ct[0] == "point" and abs(det) > 1e-12:
                gh = geom.parse_geom_header(po.body_raw)
                x, y = gh.anchor[0] - h, gh.anchor[1] - kk
                px, py = (d * x - c * y) / det, (-b * x + a * y) / det
                body = bytearray(po.body_raw)
                struct.pack_into("<2h", body, len(body) - 4, int(round(px)), int(round(py)))
                po.body_raw = bytes(body)
            geom.refresh_bags(po)

    def _refit_turned(self, index: int, fitted: bool, fn) -> None:
        """Refit a turned container's header."""
        from ..format import placement

        po = self.parsed.page.objects[index]
        gh = geom.parse_geom_header(po.body_raw)
        if gh is None:
            return
        old = geom.norm_rect(gh.rect1)
        corner = po.type_code == 0x08 and tuple(gh.anchor) == (old[0], old[3] - 1)
        if not fitted:
            placement.map_header_box(po, fn, box_anchor=corner)
            return
        self._refit_container(index, anchor=corner)
        if not corner:
            gh2 = geom.parse_geom_header(po.body_raw)
            ax, ay = fn(tuple(gh.anchor))
            body = bytearray(po.body_raw)
            struct.pack_into("<2h", body, gh2.anchor_off, int(round(ax)), int(round(ay)))
            po.body_raw = bytes(body)

    def _transform_objects(self, label: str, indices: Sequence[int], op: str) -> None:
        """Rotate or mirror whole units as one undo step."""
        from ..format import placement

        objs = self.parsed.page.objects
        roots = sorted({self.root_of(i) for i in indices if 0 <= i < len(objs)})
        idx = self.expand_to_subtrees(roots)
        if not idx:
            return
        # a composite genie turns like any genie (CtDraw offers Rotate / Mirror for one)
        points = _TurnPoints(self._turned_points(idx), self._member_fractions(idx))
        mapped = self._quarter_map(roots, op, points)
        if mapped is None:
            return
        fn, Q, local_op = mapped
        turned = {}
        sem = self.page.objects
        fit = self._fitting_ancestors(idx)
        before = {i: (bytes(objs[i].body_raw), objs[i].subtype_size) for i in list(idx) + list(fit)}
        where = {i: k for k, i in enumerate(idx)}
        parents = [where.get(sem[i].parent) if sem[i].parent is not None else None for i in idx]
        conts = {k for k, i in enumerate(idx) if sem[i].members and objs[i].type_code in (0x08, 0x0C)}
        fitted = {k: self._container_fits(idx[k], any_container=True) for k in conts}

        def content(k, po, inherited):
            if points is None:
                self._transform_one(po, fn, op, Q, inherited)
                return
            points.at = idx[k]
            try:
                self._transform_one(po, fn, op, Q, inherited)
            finally:
                points.at = None
            turned[idx[k]] = (bytes(po.body_raw), po.subtype_size)

        # a carrier's Local in memory: a mode without B and C (a mirror's mode 3) stores
        # no zero signs, which CtDraw keeps for the next turns (the fixture capture, turns
        # T08)
        carriers = set()

        def held_local(k, stored):
            points.at = idx[k]
            try:
                return points.local(stored)
            finally:
                points.at = None

        def kept_local(k, mode, L):
            if mode & 0x04:
                return
            points.at = idx[k]
            try:
                points.set_local(mode, L)
            finally:
                points.at = None
            carriers.add(idx[k])

        # a carrier under a plain group: CtDraw turns the selected group's own Local
        # (on each selected object), the carrier's Local in
        # memory is the group's composed onto its base (the general product, the
        # mode bytes ORed, so two turns keep the group's quarter nibble 2), and the save
        # composes it in for good (_turn_memo_after_save; the fixture capture, turns T30:
        # turning the carrier's own Local gives a zero the wrong sign)
        def nested_op(k, L, mode, parent):
            if parent is not None:
                _pm, P = parent
                if any(abs(x - y) > 1e-12 for x, y in zip(P, placement.IDENT)):
                    return local_op(L, mode, parent)
            points.at = idx[k]
            try:
                held = points.group((mode, L))
                m0, L0, mg, G = held if held is not None else (mode, L, 0, placement.IDENT)
                G, mg = local_op(G, mg, None)
                points.set_group((m0, L0, mg, G))
            finally:
                points.at = None
            carriers.add(idx[k])
            return placement.compose_after(L0, m0, G, mg)

        # each state keeps the records' parses: an undo / redo puts them back with the bytes
        parses_before = {i: writer.parse_state(objs[i]) for i in before}

        def restore(state, parses=None):
            for i, (body, fl) in state.items():
                po = objs[i]
                po.body_raw = body
                po.subtype_size = fl
                if parses is None or not writer.restore_parse(po, parses.get(i)):
                    writer.refresh_object_bags(po)

        self._use_tag_formats()
        try:
            placement.turn_records(
                [objs[i] for i in idx],
                parents,
                local_op,
                fn,
                self._link_info,
                lambda k: self._ancestor_chain(idx[k]),
                conts,
                content,
                held_local,
                kept_local,
                nested_op,
            )
            self._follow_owner_hotspots(idx, parents, conts, before)
            self._follow_owner_frames(idx, parents, conts, before)
            for k in sorted(conts, reverse=True):
                self._refit_turned(idx[k], fitted[k], fn)
            for a in fit:
                self._refit_container(a)
        except Exception as exc:  # noqa: BLE001 -- reported, never half-applied
            restore(before)
            subject = "The selection"
            k = getattr(exc, "index", None)
            if k is not None and 0 <= k < len(idx):
                kind = getattr(sem[idx[k]].kind, "value", sem[idx[k]].kind)
                subject = "Object %d (%s)" % (idx[k], kind)
                exc = getattr(exc, "error", exc)
            verb = "rotated" if op in ("cw", "acw") else "mirrored"
            raise TransformError(
                "%s could not be %s (%s). Nothing was changed; leave that "
                "object out of the selection and try again." % (subject, verb, exc)
            ) from exc
        after = {i: (bytes(objs[i].body_raw), objs[i].subtype_size) for i in before}
        if points is not None:
            # a record whose bytes changed after its own turn (an input record onto its
            # owner's new hotspot, a refit container) keeps no true positions; a carrier
            # keeps its Local and its state under a plain group (its refit changes its box,
            # not its Local)
            kept = {i: m for i, m in points.new.items() if turned.get(i) == after[i]}
            for i in carriers:
                if i in points.new and i in after:
                    m = kept.get(i)
                    new_m = points.new[i]
                    kept[i] = (
                        [{}, {}, new_m[2], None, new_m[4]]
                        if m is None
                        else [m[0], m[1], new_m[2], m[3], new_m[4]]
                    )
            self._remember_turned(kept, after)
        self._turn_centre = (tuple(roots), {i: self._turn_key(*after[i]) for i in after}, fn.pivot)

        parses_after = {i: writer.parse_state(objs[i]) for i in after}

        def apply(state, parses):
            restore(state, parses)
            self._rerender_page()

        self._apply(label, lambda: apply(after, parses_after), lambda: apply(before, parses_before))

    def _kept_turn_centre(self, roots):
        """The centre of the last turn or mirror when it was of the same selection and none
        of its records changed since (REVERSIBLE_TURNS)."""
        kept = getattr(self, "_turn_centre", None)
        if not kept or kept[0] != tuple(roots):
            return None
        objs = self.parsed.page.objects
        for i, key in kept[1].items():
            if i >= len(objs) or self._turn_key(objs[i].body_raw, objs[i].subtype_size) != key:
                return None
        return kept[2]

    @staticmethod
    def _turn_key(body: bytes, flags: int):
        return (len(body), hash(body), flags)

    def _turned_points(self, idx) -> dict:
        """Per record of ``idx`` turned before and unchanged since: the true positions its
        turn left it with (:class:`_TurnPoints`; undo and redo come back to a remembered
        state)."""
        memo = getattr(self, "_turn_memo", None)
        if not memo:
            return {}
        objs = self.parsed.page.objects
        out = {}
        for i in idx:
            states = memo.get(i)
            if states:
                got = states.get(self._turn_key(objs[i].body_raw, objs[i].subtype_size))
                if got is not None:
                    out[i] = got
        return out

    def _carry_turned(self, carried: dict, dx: int, dy: int) -> None:
        """A move keeps the half pixels of turned records (CtDraw composes the move onto the
        same Local). A carrier under a plain group keeps its state there: the move goes onto
        the group's Local."""
        if not carried:
            return
        objs = self.parsed.page.objects
        maps, after = {}, {}
        for i, m in carried.items():
            xm, ym = m[0], m[1]
            loc = m[2] if len(m) > 2 else None
            if loc is not None:
                a, b, c, d, h, k = loc[1]
                loc = (loc[0] | 0x01, (a, b, c, d, h + dx, k + dy))
            box = m[3] if len(m) > 3 else None
            if box is not None:
                box = (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy)
            grp = m[4] if len(m) > 4 else None
            if grp is not None and (dx or dy):
                a, b, c, d, h, k = grp[3]
                grp = (grp[0], grp[1], grp[2] | 0x01, (a, b, c, d, h + dx, k + dy))
            maps[i] = (
                {k + dx: (None if v is None else v + dx) for k, v in xm.items()},
                {k + dy: (None if v is None else v + dy) for k, v in ym.items()},
                loc,
                box,
                grp,
            )
            after[i] = (bytes(objs[i].body_raw), objs[i].subtype_size)
        self._remember_turned(maps, after)

    def _under_carrier(self):
        """A test ``f(i)``: record ``i`` sits under a transform carrier (a genie, a group with
        a Local, a placed symbol: :func:`placement.carries`), so its true positions follow the
        carrier's Local of doubles; each ancestor is asked once."""
        from ..format import placement

        objs = self.parsed.page.objects
        sem = self.page.objects if self.page is not None else []
        carrying = {}

        def under_carrier(i):
            a = sem[i].parent if i < len(sem) else None
            while a is not None and a < len(objs):
                c = carrying.get(a)
                if c is None:
                    c = carrying[a] = placement.carries(objs[a])
                if c:
                    return True
                a = sem[a].parent if a < len(sem) else None
            return False

        return under_carrier

    def _member_fractions(self, idx) -> dict:
        """Fractional page positions of records under a transform carrier."""
        from ..format import placement

        objs = self.parsed.page.objects
        under_carrier = self._under_carrier()
        out = {}
        for i in idx:
            po = objs[i]
            if po.type_code == 0x05 or placement.is_linked_symbol(po) or not under_carrier(i):
                continue
            try:
                (_s1, _s2, (m3, T, _o, _n)), _e = placement.read_slots_of(po)
            except Exception:  # noqa: BLE001  # swallow-ok: no slots, so no Total to read
                continue
            if not m3 & 0x01:
                continue
            a, b, c, d, h, k = T
            if (
                any(abs(v) > 1e-12 and abs(abs(v) - 1.0) > 1e-12 for v in (a, b, c, d))
                or (abs(a) > 0.5) == (abs(c) > 0.5)
                or (abs(b) > 0.5) == (abs(d) > 0.5)
            ):
                continue  # a scale or a shear: no fixed fraction
            fr = []
            for v in (h, k):
                f = v - math.floor(v)
                fr.append(0.0 if f < 1e-9 or f > 1.0 - 1e-9 else f)
            if fr[0] or fr[1]:
                out[i] = (fr[0], fr[1])
        return out

    def _turn_memo_after_save(self) -> None:
        """What CtDraw's save leaves of the turn memory."""
        from ..format import placement

        memo = getattr(self, "_turn_memo", None)
        self._turn_centre = None
        if not memo:
            self._turn_memo = None
            return
        objs = self.parsed.page.objects
        under_carrier = self._under_carrier()
        kept = {}
        for i, states in memo.items():
            if i >= len(objs):
                continue
            text = objs[i].type_code == 0x05
            whole = not text and under_carrier(i)
            out = {}
            for key, m in states.items():
                loc = m[2] if len(m) > 2 else None
                grp = m[4] if len(m) > 4 else None
                if grp is not None:
                    L, mode = placement.compose_after(grp[1], grp[0], grp[3], grp[2])
                    loc = (mode, tuple(L))
                if loc is not None and text and _turn_only(loc[1]):
                    loc = None  # (a text's turns: the save made them its orient)
                if whole:
                    out[key] = (m[0], m[1], loc, m[3] if len(m) > 3 else None, None)
                elif loc is not None:
                    out[key] = ({}, {}, loc, None, None)
            if out:
                kept[i] = out
        self._turn_memo = kept or None

    def _remember_turned(self, maps: dict, after: dict) -> None:
        memo = getattr(self, "_turn_memo", None)
        if memo is None:
            memo = self._turn_memo = {}
        for i, m in maps.items():
            states = memo.setdefault(i, {})
            states[self._turn_key(*after[i])] = tuple(m)
            while len(states) > _TURN_STATES:
                states.pop(next(iter(states)))

    def selection_extent(self, indices: Sequence[int], bounds: bool = False, held: bool = False):
        """(l, t, r, b) of the selected units."""
        boxes = []
        units = list(self._units_from(indices))
        turned = self._turned_points([u[0] for u in units]) if bounds and held else {}
        for u in units:
            box = self._unit_bbox(u)
            if bounds:
                po = self.parsed.page.objects[u[0]]
                gh = geom.parse_geom_header(po.body_raw)
                kept = turned.get(u[0])
                if po.type_code == 0x05 and kept is not None and len(kept) > 3 and kept[3] is not None:
                    box = kept[3]
                elif gh is not None and (box[2] - box[0] > 1 or box[3] - box[1] > 1):
                    box = geom.norm_rect(gh.rect2)
            boxes.append(box)
        if not boxes:
            return None
        return (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )

    def rotate_objects(self, indices: Sequence[int], clockwise: bool = True) -> None:
        """Rotate the selection 90 degrees about its centre."""
        self._transform_objects("rotate", indices, "cw" if clockwise else "acw")

    def mirror_objects(self, indices: Sequence[int], horizontal: bool = True) -> None:
        """Mirror the selection about its bounding-box centre axis (box contents flip
        like CtDraw: text/symbols via slot-1 flip matrices, bitmaps by flipping DIB
        pixels; a pie / arc reverses its sweep)."""
        self._transform_objects("mirror", indices, "mh" if horizontal else "mv")

    def set_object_rect(self, index: int, rect: Tuple[int, int, int, int]) -> None:
        """Resize/reposition one object to rect=(l, t, r, b). A container
        (group / genie / symbol set) scales its whole subtree."""
        check_coords(rect)
        if self.page is not None and index < len(self.page.objects) and self.page.objects[index].members:
            gh = geom.parse_geom_header(self.parsed.page.objects[index].body_raw)
            if gh is not None:
                l, t, r, b = geom.norm_rect(gh.rect1)
                nl, nt, nr, nb = geom.norm_rect(rect)
                if (nr - nl, nb - nt) == (r - l, b - t):
                    self.move_objects([index], nl - l, nt - t)
                    return
            self._scale_unit(index, rect)
            return
        held, points = self._text_resize_local(index, rect)

        def mutate(po):
            from ..format import placement, textmetrics

            if points is not None:
                # quarter turns held in memory composed into the matrix the resize keeps:
                # the text takes back its orient and echo from before them (as a mirror
                # does, _unturn_text), and the scale maps its box in memory (the box
                # measured at its echo, turned: CtDraw's old box turned, _TurnPoints.box)
                # onto the one asked for. The matrix is kept as _text_resize_local decided
                # (a second decision on the measured box could bake the text near the
                # uniform rule's edge, after its turn was taken back: it would lose it)
                box0 = points.box() or geom.norm_rect(geom.parse_geom_header(po.body_raw).rect1)
                self._unturn_text(po, held[1], points)
                local = textmetrics.text_extent(po)
                if local is not None:
                    box0 = geom.exact_box(held[1], local)
                ok = geom.scale_text_matrix(
                    po, geom.box_map(box0, geom.norm_rect(rect)), held=held, per_entry=True
                )
                geom.refresh_bags(po)
                return ok
            info = self._link_info(po) if placement.is_linked_symbol(po) else None
            geom.resize_object(po, rect, link_info=info, held=held, stretch=self.STRETCH_TEXTS)

        self._body_edit("resize", index, mutate)

    def _text_resize_local(self, index: int, rect):
        """The held Local and points for resizing a text."""
        from ..format import placement

        objs = self.parsed.page.objects
        po = objs[index] if 0 <= index < len(objs) else None
        if (
            po is None
            or po.type_code != 0x05
            or not po.subtype_size & 0x2
            or geom.parse_text_tail(po) is None
        ):
            return None, None
        points = _TurnPoints(self._turned_points([index]), self._member_fractions([index]))
        points.at = index
        lt = geom.get_local_transform(po)
        stored = lt if lt else (0, placement.IDENT)
        if self._in_plain_group(index):
            # (a member of a plain group: its turns and mirrors live in the group's Local)
            g = points.group(stored) if stored[0] else None
            if g is None:
                return None, None
            L, mode = placement.compose_after(g[1], g[0], g[3], g[2])
            return (mode, tuple(L)), None
        if not stored[0]:
            turn = points.turn()
            if turn is None:
                return None, None
            if geom.text_resize_bakes(po, rect, held=turn, from_box=points.box(), stretch=self.STRETCH_TEXTS):
                return turn, None
            return turn, points
        held = points.local(stored)
        return (held if held is not stored else None), None

    def _scale_unit(self, index: int, rect: Tuple[int, int, int, int], matrix=None) -> None:
        """Resize a container's whole subtree onto ``rect``. One undo step."""
        from ..format import placement

        objs = self.parsed.page.objects
        gh = geom.parse_geom_header(objs[index].body_raw)
        if gh is None:
            return
        l, t, r, b = geom.norm_rect(gh.rect1)
        nl, nt, nr, nb = geom.norm_rect(rect)
        if (l, t, r, b) == (nl, nt, nr, nb):
            return
        if matrix is not None:
            M6 = tuple(float(v) for v in matrix)
        else:
            # a point-sized axis cannot scale (a V3/V4 genie is a 1x1 hotspot)
            sx = (nr - nl) / (r - l) if r - l > 1 else 1.0
            sy = (nb - nt) / (b - t) if b - t > 1 else 1.0
            M6 = (sx, 0.0, 0.0, sy, nl - sx * l, nt - sy * t)
        order = self.subtree(index)
        before = {i: (bytes(objs[i].body_raw), objs[i].subtype_size) for i in order}
        parses_before = {i: writer.parse_state(objs[i]) for i in order}  # (as a turn's)
        fit = self._fitting_ancestors(order)
        anc_old = {a: bytes(objs[a].body_raw) for a in fit}
        sem = self.page.objects
        where = {i: k for k, i in enumerate(order)}
        parents = [where.get(sem[i].parent) if sem[i].parent is not None else None for i in order]
        # only a group / genie / symbol-set record takes the union of its
        # children; a leaf owning behaviour records keeps its own geometry
        conts = {k for k, i in enumerate(order) if sem[i].members and objs[i].type_code in (0x08, 0x0C)}

        self._use_tag_formats()
        placement.scale_records(
            [objs[i] for i in order],
            parents,
            M6,
            self._link_info,
            lambda k: self._ancestor_chain(order[k]),
            conts,
            stretch_texts=self.STRETCH_TEXTS,
        )
        for i in sorted((i for k, i in enumerate(order) if k in conts), reverse=True):
            # a group / genie root re-derives its hotspot (l, b-1); a symbol
            # set's was mapped with the unit
            self._refit_container(i, anchor=objs[i].type_code == 0x08)
        for a in fit:
            self._refit_container(a)
        after = {i: (bytes(objs[i].body_raw), objs[i].subtype_size) for i in order}
        parses_after = {i: writer.parse_state(objs[i]) for i in order}
        anc_new = {a: bytes(objs[a].body_raw) for a in fit}

        def apply(state, anc, parses):
            for i, (body, fl) in state.items():
                po = objs[i]
                po.body_raw = body
                po.subtype_size = fl
                if not writer.restore_parse(po, parses.get(i)):
                    writer.refresh_object_bags(po)
            for a, body in anc.items():
                objs[a].body_raw = body
            self._rerender_page()

        self._apply(
            "resize",
            lambda: apply(after, anc_new, parses_after),
            lambda: apply(before, anc_old, parses_before),
        )

    def set_object_nodes(self, index: int, nodes: List[Tuple[int, int]]) -> bool:
        """Rewrite a line/polygon/pipe vertex list."""
        return bool(self._body_edit("nodes", index, lambda po: geom.set_object_nodes(po, nodes)))

    # -- reshape: node editing (help 1172187 / 1172188) -------------------------------
    def reshape_info(self, index: int) -> Optional["geom.ReshapeInfo"]:
        """The object's nodes when it can be reshaped node by node (a free
        line, a pipe, a polygon / polyline: geometry.reshape_info), else
        None."""
        objs = self.parsed.page.objects if self.parsed and self.parsed.page else []
        if not 0 <= index < len(objs):
            return None
        return geom.reshape_info(objs[index])

    @staticmethod
    def _coords_ok(nodes) -> bool:
        return all(geom.COORD_MIN <= int(v) <= geom.COORD_MAX for p in nodes for v in p)

    def reshape_nodes(
        self, index: int, nodes: List[Tuple[int, int]], label: str = "reshape", mapping=None
    ) -> bool:
        """Replace an object's nodes. One undo step."""
        info = self.reshape_info(index)
        nodes = [(int(round(x)), int(round(y))) for x, y in nodes]
        if info is None or len(nodes) < geom.MIN_NODES or not self._coords_ok(nodes):
            return False
        if info.kind == "line" and len(nodes) != 2:
            return False
        from ..format import vertexanim

        renames = []
        if mapping:
            renames = vertexanim.renumber_edits(self.object_properties(index), mapping, len(nodes))
        if not renames:
            return bool(self._body_edit(label, index, lambda po: geom.set_object_nodes(po, nodes)))
        with self.batch(label):
            if not self._body_edit(label, index, lambda po: geom.set_object_nodes(po, nodes)):
                return False
            for k, v in renames:
                # the element-aware writers (a genie element's member keeps its
                # keys in the TEMPLATE bag)
                if v is None:
                    self.remove_property(index, k)
                else:
                    self.ensure_property(index, k, v)
        return True

    def move_object_nodes(self, index: int, which: Sequence[int], dx: int, dy: int) -> bool:
        """Move the nodes ``which`` by (dx, dy) -- CtDraw's node drag
        (every selected node takes the same delta, so their layout is
        kept). One undo step."""
        info = self.reshape_info(index)
        if info is None or not (dx or dy):
            return False
        sel = {k for k in which if 0 <= k < len(info.nodes)}
        if not sel:
            return False
        nodes = [(x + dx, y + dy) if k in sel else (x, y) for k, (x, y) in enumerate(info.nodes)]
        return self.reshape_nodes(index, nodes, "move node" if len(sel) == 1 else "move nodes")

    def insert_object_node(self, index: int, at: int, point: Tuple[int, int]) -> bool:
        """Insert a node so it becomes node ``at`` (CtDraw's Insert / '+').
        Not for a line (help 1172188: draw a polyline instead).
        The vertex animations after it move up by one. One undo step."""
        from ..format import vertexanim

        info = self.reshape_info(index)
        if info is None or not info.can_insert or not 0 < at <= len(info.nodes):
            return False
        nodes = list(info.nodes)
        nodes.insert(at, (int(point[0]), int(point[1])))
        return self.reshape_nodes(index, nodes, "add node", vertexanim.insert_mapping(len(info.nodes), at))

    def delete_object_nodes(self, index: int, which: Sequence[int]) -> bool:
        """Delete nodes. Refused when fewer than two would be left."""
        from ..format import vertexanim

        info = self.reshape_info(index)
        if info is None or info.kind == "line":
            return False
        gone = {k for k in which if 0 <= k < len(info.nodes)}
        if not gone or len(info.nodes) - len(gone) < geom.MIN_NODES:
            return False
        nodes = [p for k, p in enumerate(info.nodes) if k not in gone]
        return self.reshape_nodes(
            index,
            nodes,
            "delete node" if len(gone) == 1 else "delete nodes",
            vertexanim.delete_mapping(len(info.nodes), gone),
        )

    def vertex_animation(self, index: int):
        """The polygon's per-vertex animation settings (format.vertexanim)."""
        from ..format import vertexanim

        info = self.reshape_info(index)
        count = len(info.nodes) if info is not None else 0
        return vertexanim.read(self.object_properties(index), count)
