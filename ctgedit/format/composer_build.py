"""Build the composition cache element for a composite genie insert."""

from __future__ import annotations

import copy
import os
from typing import Dict, List, Optional, Sequence, Tuple

from . import composer as CP
from . import genie_place as GP
from . import geometry as G
from . import alarmind as AI
from .genie_place import Bag, decode, encode, to_po
from .page import PageObject


class ComposeContext:
    """What the composer asks CtDraw (ICtLibraryInfo) answered natively for
    the page ``page_path`` (its project's include chain)."""

    def __init__(self, page_path: str, resolver=None):
        from .symbols import SymbolResolver

        self.page_path = page_path
        self.project_dir = os.path.dirname(os.path.abspath(page_path))
        # a page copied out of its project: CTG_LIB_PATH names the real one
        self.extra = [d for d in os.environ.get("CTG_LIB_PATH", "").split(os.pathsep) if d]
        self.resolver = resolver if resolver is not None else SymbolResolver.for_page(page_path, self.extra)
        self._elements: Dict[tuple, Optional[GP.Element]] = {}
        self._flags: Optional[List[Tuple[int, int]]] = None

    # -- content items ------------------------------------------------------
    def element(self, item: CP.ContentItemDef) -> Optional[GP.Element]:
        """The content item's library element (its Project's folder first, then
        the page's include chain), loaded once."""
        key = (item.project.lower(), item.library.lower(), item.item.lower(), item.ext)
        if key in self._elements:
            el = self._elements[key]
            return copy.copy(el) if el is not None else None
        el = None
        path = self._find(item)
        if path is not None:
            try:
                el = GP.load_element(path, item.item, self.resolver)
            except Exception:  # noqa: BLE001 -- an unreadable element: no size
                el = None
        if el is not None:
            el.library = item.library  # CtDraw writes the template's spelling
            el.records = [_with_an_name(po) for po in _loaded(el, self.resolver)]
        self._elements[key] = el
        return copy.copy(el) if el is not None else None

    def _find(self, item: CP.ContentItemDef) -> Optional[str]:
        from .symbols import SymbolReference
        from .projects import resolve_project_dirs, sibling_install_user_dirs

        users: List[str] = []
        for base in [self.project_dir] + self.extra:
            user = os.path.dirname(os.path.abspath(base))
            for u in [user] + sibling_install_user_dirs(user):
                if u not in users:
                    users.append(u)
        dirs = resolve_project_dirs(item.project, users) if item.project else []
        for d in dirs:
            try:
                names = {n.lower(): n for n in os.listdir(d)}
            except OSError:
                continue
            fn = names.get(("%s.%s" % (item.library, item.ext)).lower())
            if fn is None:
                continue
            path = os.path.join(d, fn)
            try:
                from .symbols import open_library

                if open_library(path).element(item.item) is not None:
                    return path
            except Exception:  # noqa: BLE001
                continue
        res = self.resolver.resolve(
            SymbolReference(item.library, item.item, item.ext, "genie" if item.ext == "ctm" else "symbol")
        )
        return res.library_path if res is not None else None

    def instance(
        self,
        item: CP.ContentItemDef,
        params: Sequence[Tuple[str, str]],
        xy: Tuple[int, int],
        handles: Sequence[int],
    ) -> Optional[List[PageObject]]:
        """The content genie pasted with its extent's top-left at ``xy``,
        its non-empty bound parameters set (CtDraw is called only for
        a value with a length)."""
        el = self.element(item)
        if el is None:
            return None
        prm = {k: v for k, v in params if v != ""}
        local = GP.local_for_topleft(el, xy[0], xy[1])
        recs = GP.build_instance_records(el, local, prm, handles=list(handles), resolver=self.resolver)
        # the parameters can change the extent (a right-aligned label's text):
        # GenieLayout then moves the genie so the top-left of its extent is at
        # the point again
        gh = G.parse_geom_header(recs[0].body_raw) if recs else None
        if gh is not None:
            l, t = G.norm_rect(gh.rect1)[:2]
            if (l, t) != (xy[0], xy[1]):
                local = (local[0] + xy[0] - l, local[1] + xy[1] - t)
                recs = GP.build_instance_records(
                    el, local, prm, handles=list(handles), resolver=self.resolver
                )
        return recs

    def size_of(
        self, item: CP.ContentItemDef, params: Sequence[Tuple[str, str]]
    ) -> Optional[Tuple[float, float]]:
        """TryGetContentSizes: the Bounds size
        of the content pasted with its parameters. Memoised per content
        item and non-empty parameter set."""
        key = (item.key, item.ext, tuple((k, v) for k, v in params if v != ""))
        sizes = self.__dict__.setdefault("_sizes", {})
        if key in sizes:
            return sizes[key]
        el = self.element(item)
        out = None
        if el is not None:
            n = GP.handle_count(el)
            recs = self.instance(item, params, (0, 0), range(1, n + 1))
            gh = G.parse_geom_header(recs[0].body_raw) if recs else None
            if gh is not None:
                l, t, r, b = gh.rect2
                out = float(r - l), float(b - t)
        sizes[key] = out
        return out

    def version(self, item: CP.ContentItemDef) -> str:
        el = self.element(item)
        return CP.element_version(el.identity if el is not None else b"")

    # -- alarm indicators ---------------------------------------------------
    def flag_sizes(self) -> List[Tuple[int, int]]:
        """The alarm indicator flag sizes."""
        if self._flags is None:
            out = [AI.DEFAULT_FLAG]
            try:
                out = AI.flag_sizes(self.page_path, self.resolver)
            except Exception:  # noqa: BLE001 -- the default flag box still applies
                pass
            self._flags = out
        return list(self._flags)

    def expansion(self, size, al: CP.AlarmLayout):
        """TryGetAlarmIndicatorExtentExpansions : how far the indicator frame + flag reach past a box of
        ``size`` on each side."""
        w, h = int(size[0]), int(size[1])
        s = {
            "on": True,
            "width": al.width,
            "padding": al.padding,
            "inside": al.inside,
            "flag_on": True,
            "flag_loc": CP.FLAG_LOCATION.get(al.flag_style, 0),
        }
        g = AI.grown_bounds((0, 0, w, h), s, self.flag_sizes())
        return (float(-g[0]), float(-g[1])), (float(g[2] - w), float(g[3] - h))

    # -- a layout -------------------------------------------------------------
    def compose(self, model: CP.TemplateModel, inputs, final: bool = True) -> CP.Layout:
        return CP.compose_layout(model, inputs, self.size_of, self.expansion, final=final)


# ============================================================ building the element
def _set_own(po: PageObject, pairs: Sequence[Tuple[str, str]], template: bool) -> PageObject:
    """Set properties on a record's level-0 value bag (and, for a pasted
    genie root, its template bag too -- on an element member)."""
    r = decode(po)
    if r.entries is None:
        r.entries = [[Bag([]), Bag([]), Bag([])]]
        r.dw |= 0x8
    e0 = r.entries[0]
    # the template bag only on a genie with a level of its own (the stub
    # genie, a plain group: the value bag alone)
    template = template and len(r.entries) >= 2
    for k, v in pairs:
        e0[1].set(k, v)
        if template:
            e0[2].set(k, v)
    return to_po(encode(r))


def _loaded(el: GP.Element, resolver) -> List[PageObject]:
    """The element's records as CtDraw holds them after LOADING the library
    element (genie_norm: each member's value bag re-derived from its
    template with the declared defaults -- a template that substitutes to
    nothing REMOVES its key, the label's stale 'TXT_OFF %Label%')."""
    from . import genie_norm as GN

    if el.ext != "ctm":
        return list(el.records)
    try:
        out = GN.normalise_records(
            list(el.records), ext="ctm", resolver=resolver, lib_path=el.lib_path, e=tuple(el.e)
        )
    except Exception:  # noqa: BLE001 -- the stored records then
        return list(el.records)
    # the element ROOT is not re-made by a load (its colour state stays the
    # stored one); the paste derives its geometry
    return [el.records[0]] + list(out[1:])


def _with_an_name(po: PageObject) -> PageObject:
    """CtDraw 8.50's element load: a record with an object name that has no
    ``__AN_NAME__`` property gets one (appended to its value bag, and to its
    template bag when it has one) -- the paste then substitutes on top, so a
    template-only key such as TXT_OFF lands after it."""
    if po.raw_payload is not None or not (po.subtype_size & 0x8):
        return po
    r = decode(po)
    if not r.name or not r.entries:
        return po
    e0 = r.entries[0]
    if e0[1].get("__AN_NAME__") is not None:
        return po
    e0[1].pairs.append(("__AN_NAME__", r.name))
    if len(r.entries) >= 2 or e0[2].pairs:
        if e0[2].get("__AN_NAME__") is None:
            e0[2].pairs.append(("__AN_NAME__", r.name))
    return to_po(encode(r))


def _name(po: PageObject, name: str, template: bool) -> PageObject:
    r = decode(po)
    r.name = name
    po = to_po(encode(r))
    return _set_own(po, [("__AN_NAME__", name)], template)


def _hotspot(contents: Sequence[CP.Item]) -> Tuple[int, int]:
    x = y = 0.0
    for it in contents:
        a = it.item.anchor if it.item is not None else 0
        if not a:
            continue
        x, y = it.position
        w, h = it.size
        if a in (9, 8, 7):  # BottomLeft / Bottom / BottomRight
            y += h
        if a in (7, 5, 4):  # BottomRight / Right / TopRight
            x += w
        if a in (1, 5, 6):  # Center / Right / Left
            y += h * 0.5
        if a in (1, 3, 8):  # Center / Top / Bottom
            x += w * 0.5
        break
    return int(x + 0.5), int(y + 0.5)


class BuildError(ValueError):
    pass


def build_records(layout: CP.Layout, ctx: ComposeContext) -> Tuple[List[PageObject], Tuple[int, int]]:
    """The element's member records (every top-level object, DFS, element
    coordinates) and its hot spot, as CtDraw's GenieLayout makes them."""
    used: set = set()
    # (the lowest free handle, kept between the items: GP.handle_allocator)
    next_free = GP.handle_allocator(used)
    items = CP.marshal(layout.items)
    order: List[CP.Item] = []

    def walk(its):
        for it in its:
            order.append(it)
            if it.kind == "group":
                walk(it.children)

    walk(items)
    contents = [it for it in order if it.kind == "content"]

    def place(it: CP.Item) -> List[PageObject]:
        el = ctx.element(it.item)
        if el is None:
            raise BuildError(
                "The content item %s.%s.%s is not installed in this project or "
                "the projects it includes." % (it.item.project, it.item.library, it.item.item)
            )
        # the paste keeps every twin handle that is free, then gives the
        # taken ones the lowest handles neither the element nor the kept
        # twins use
        parents = GP.P.forest_parents(el.records)
        twins = [
            G.get_handle(po)
            for k, po in enumerate(el.records)
            if not (po.type_code in GP.INPUT_TCS and parents[k] is not None)
        ]
        hs: List[Optional[int]] = []
        for tw in twins:
            if tw is not None and 0 <= tw <= G.MAX_HANDLE and tw not in used:
                used.add(tw)
                hs.append(tw)
            else:
                hs.append(None)
        for j, h in enumerate(hs):
            if h is None:
                hs[j] = next_free()
        xy = (CP.placement(it.position[0]), CP.placement(it.position[1]))
        recs = ctx.instance(it.item, it.params, xy, hs)
        if not recs:
            raise BuildError("The content item %s.%s could not be placed." % (it.item.library, it.item.item))
        root = _zero_local(recs[0])
        if it.name:
            root = _name(root, it.name, template=True)
        if it.alarm is not None:
            root = _set_own(root, it.alarm.keys(), template=False)
        recs[0] = root
        return recs

    def group(it: CP.Item, kids: List[PageObject]) -> List[PageObject]:
        from . import factory

        tops, i = [], 0
        while i < len(kids):
            tops.append(i)
            i = GP.subtree_end(kids, i)
        ghs = [
            G.parse_geom_header(kids[k].body_raw) for k in tops if kids[k].type_code not in G.INPUT_TYPE_CODES
        ]
        u1 = GP.union_rect([G.norm_rect(g.rect1) for g in ghs])
        u2 = GP.union_rect([tuple(g.rect2) for g in ghs])
        h = next_free()
        grp = factory.make_group_record(h, u1, len(tops), bounds=u2)
        if it.name:
            grp = _name(grp, it.name, template=False)
        if it.alarm is not None:
            grp = _set_own(grp, it.alarm.keys(), template=False)
            s = AI.settings({k.upper(): v for k, v in it.alarm.keys()})
            r = decode(grp)
            r.rect2 = tuple(int(v) for v in AI.grown_bounds(tuple(u2), s, ctx.flag_sizes()))
            grp = to_po(encode(r))
        return [grp] + kids

    def run(its: Sequence[CP.Item]) -> List[PageObject]:
        out: List[PageObject] = []
        for it in reversed(its):
            if it.kind == "group":
                out += group(it, run(it.children))
            else:
                out += place(it)
        return out

    recs = [_button_size(po) for po in run(items)]
    derive_dynamic(recs)
    return recs, _hotspot(contents)


def _button_size(po: PageObject) -> PageObject:
    """A button's WIDTH / HEIGHT properties follow its box, as when CtDraw
    scales it (geometry.set_object_rect): the buttons of a SCALED nested
    genie store their scaled size."""
    if po.type_code != GP.BUTTON_TC or not (po.subtype_size & 0x8):
        return po
    r = decode(po)
    if not r.entries:
        return po
    l, t, rr, b = G.norm_rect(r.rect1)
    want = {"WIDTH": str(rr - l), "HEIGHT": str(b - t)}
    changed = False
    for bag in r.entries[0][1:3]:
        for key, v in want.items():
            cur = bag.get(key)
            if cur is not None and cur != v and cur.strip().lstrip("-").isdigit():
                bag.set(key, v)
                changed = True
    return to_po(encode(r)) if changed else po


def _zero_local(po: PageObject) -> PageObject:
    """A paste at the element origin stores its Local as identity (mode 0),
    not a zero translation."""
    from . import placement as P
    from . import writer

    slots = P.read_slots(po.body_raw)[0]
    (m1, L, _o, _n) = slots[0]
    if m1 == 1 and not L[4] and not L[5]:
        P.write_slots(po, [(0, P.IDENT)] + [(m, tuple(v)) for (m, v, _o2, _n2) in slots[1:]])
        writer.refresh_object_bags(po)
    return po


def regrow_bounds(recs: List[PageObject], flag_sizes) -> List[PageObject]:
    """Regrow the bounds of every group in a placed instance, bottom-up."""
    import struct
    from . import placement as P
    from . import writer

    parents = P.forest_parents(recs)
    kids: Dict[int, List[int]] = {}
    for k, p in enumerate(parents):
        if p is not None and recs[k].type_code not in G.INPUT_TYPE_CODES:
            kids.setdefault(p, []).append(k)
    for k in range(len(recs) - 1, -1, -1):
        po = recs[k]
        if po.type_code != 0x08 or not po.class_flag or k not in kids:
            continue
        r2s = []
        for c in kids[k]:
            gh = G.parse_geom_header(recs[c].body_raw)
            if gh is not None:
                r2s.append(tuple(gh.rect2))
        gh = G.parse_geom_header(po.body_raw)
        if not r2s or gh is None:
            continue
        want = GP.union_rect(r2s)
        if b"AI_ON" in bytes(po.body_raw).upper():
            s = AI.settings(AI.props_of(po))
            if s["on"]:
                want = AI.grown_bounds(tuple(want), s, flag_sizes)
        want = tuple(int(v) for v in want)
        if tuple(gh.rect2) != want:
            body = bytearray(po.body_raw)
            struct.pack_into("<4h", body, gh.rect2_off, *want)
            po.body_raw = bytes(body)
            writer.refresh_object_bags(po)
    return recs


def derive_dynamic(recs: List[PageObject]) -> None:
    """dwFlags 0x10 of every member, as the element save derives it
    (colourstate.is_dynamic; a member of a dynamic group / symbol set is
    dynamic too -- the named content genies and their members)."""
    from . import colourstate as CS
    from . import placement as P

    parents = P.forest_parents(recs)
    owners = {
        parents[k] for k, o in enumerate(recs) if o.type_code in CS.INPUT_TCS and parents[k] is not None
    }
    for k, po in enumerate(recs):
        p = parents[k]
        pdyn = p is not None and recs[p].type_code in CS.INHERIT_TCS and bool(recs[p].subtype_size & 0x10)
        CS.derive_dynamic_flag(po, parent_dynamic=pdyn, input_child=k in owners)
