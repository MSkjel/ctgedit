"""Page templates: compose, re-apply after a change, switch to another."""

from __future__ import annotations

from ..infra.paths import path_key

import collections
import copy
import hashlib
import os
import struct
import threading
from typing import Dict, List, Optional, Sequence, Tuple

from . import genie_place as GP
from . import geometry as G
from . import links
from . import placement as P
from .page import PageObject
from .symbols import SymbolReference, reference_from_object
from . import forest as _forest
from ..infra.logsetup import note_exception as _note_exc

GROUP_TC, SYMSET_TC, TEXT_TC, ACTIVEX_TC, DUMMY_TC = 0x08, 0x0C, 0x05, 0x0D, 0xFFFF
INPUT_TCS = GP.INPUT_TCS
POINT_CLASSES = GP.POINT_CLASSES
NONE_DEFAULT = chr(10)  # a declaration '\n' = no default


# ----------------------------------------------------------------- element
class Template:
    """A template element as CtDraw composes it."""

    def __init__(self, el: GP.Element, width: int, height: int, trailer=None):
        self.el = el
        self.width, self.height = width, height
        self.trailer = trailer
        #: the library files (path, symbols.library_version) of the templates whose copies the load
        #: composed again: the cached template is read again when one of them changes
        self.deps: Tuple[Tuple[str, tuple], ...] = ()
        #: {entry name without stamp, lower case: the current entry} of the templates whose
        #: copies the load composed again (:func:`template_entries`)
        self.entry_overrides: Dict[str, Tuple[bytes, str]] = {}
        #: the same of the genies whose PLACEHOLDERS the load placed again
        #: (:func:`_place_placeholders`)
        self.placeholder_overrides: Dict[str, Tuple[bytes, str]] = {}
        #: :func:`shows_composition`'s answers for this version of the template, by the
        #: page's template records (their digest) and page name (it lives with the template)
        self.shown: Dict[tuple, bool] = {}

    @property
    def records(self):
        return self.el.records

    @property
    def lib_path(self) -> str:
        return self.el.lib_path


_TPL_CACHE: Dict[Tuple[str, str, tuple], Template] = {}


def _deps_current(t: Template) -> bool:
    from .symbols import library_version

    for path, ver in t.deps:
        try:
            if library_version(path) != ver:
                return False
        except OSError:
            return False
    return True


#: the templates being loaded on this thread: a template that holds a copy of itself (or
#: two that hold each other's) loads the inner one as stored
_LOADING = threading.local()


def load_template(lib_path: str, element: str) -> Template:
    """Load a template element as CtDraw's load holds it."""
    from .symbols import library_version

    key = (os.path.normcase(lib_path), element.lower(), library_version(lib_path))
    t = _TPL_CACHE.get(key)
    if t is not None and _deps_current(t):
        return t
    el = GP.load_element(lib_path, element, refresh=False)
    blk = GP.open_library(lib_path)._find_block(element)
    w, h = struct.unpack_from("<2i", blk.data, 0x14)
    trailer = None
    try:
        from . import element as E

        trailer = E.open_element(lib_path, element).trailer
    except Exception:  # noqa: BLE001 -- the trailer is optional page data
        _note_exc("template_place.load_template")
        trailer = None
    t = Template(el, w, h, trailer)
    busy = getattr(_LOADING, "keys", None)
    if busy is None:
        busy = _LOADING.keys = set()
    me = (path_key(lib_path), element.lower())
    if me not in busy:
        busy.add(me)
        try:
            _compose_stale_copies(t)
        finally:
            busy.discard(me)
    _place_placeholders(t)
    have = _TPL_CACHE.get(key)
    if have is not None and _deps_current(have):
        return have  # (loaded meanwhile on another thread: the first one)
    _TPL_CACHE[key] = t
    return t


def _current_spelled(base: str, cur: Tuple[bytes, str]) -> Tuple[bytes, str]:
    """The current entry ``cur`` with the names as the link ``base`` spells them."""
    return cur[0], base + cur[1][len(links.strip_stamp(cur[1])) :]


def _compose_stale_copies(t: Template) -> None:
    """:func:`load_template`'s composition of the template copies ``t`` holds whose import
    entry quotes another version than the inner template's current one (resolved on the
    template library's own search path, as its nested genies are)."""
    from .symbols import library_version

    spans = nested_template_spans(t.el.records)
    if not spans:
        return
    try:
        from .symbols import SymbolResolver

        res = SymbolResolver.for_page(t.lib_path)
    except Exception:  # noqa: BLE001 -- the stored copies are used
        _note_exc("template_place._compose_stale_copies")
        return
    stored = {
        links.strip_stamp(n).lower(): (g, n) for g, n in GP.element_table_entries(t.lib_path, t.el.element)
    }
    stale, current, deps = set(), {}, []
    for a, b, ref in spans:
        if template_nesting_chain(t.el.records[a:b], t.lib_path, t.el.element, res) is not None:
            # (a copy of this template, or of one that holds its copy: it stays as stored.
            # Composed, each save of it would nest the stored version one level deeper, as
            # its own copy is out of date after every save of it)
            continue
        base = links.strip_stamp(GP.decode(t.el.records[a]).link or ref.raw)
        try:
            rs = res.resolve(ref)
            cur = GP.current_link_entry(rs.library_path, ref.library, ref.element) if rs is not None else None
        except Exception:  # noqa: BLE001  # swallow-ok: it does not resolve: the copy as stored
            cur = None
        if cur is None:
            continue
        try:
            deps.append((rs.library_path, library_version(rs.library_path)))
        except OSError:  # swallow-ok: the outer library's own version still keys the cache
            pass
        want = _current_spelled(base, cur)
        have = stored.get(base.lower())
        if have is not None and have[1].lower() == want[1].lower():
            continue  # (the copy holds the current version)
        stale.add((ref.library.lower(), ref.element.lower()))
        current[base.lower()] = want
    if stale:
        rep: dict = {}
        recs = compose_nested_templates(t.el.records, res, only=stale, report=rep, markers=False, memory=True)
        done = set(rep.get("composed", ()))
        el = copy.copy(t.el)
        el.records = recs
        t.el = el
        for base, ent in current.items():
            lib, elem, _x = parse_ref_text(base)
            if (lib.lower(), elem.lower()) in done:
                t.entry_overrides[base] = ent
        for _a, _b, ref in nested_template_spans(recs):
            try:
                rs = res.resolve(ref)
                sub = load_template(rs.library_path, ref.element) if rs is not None else None
            except Exception:  # noqa: BLE001  # swallow-ok: its own libraries are not followed
                sub = None
            if sub is not None:
                deps.extend(sub.deps)
    t.deps = tuple(deps)


def _place_placeholders(t: Template) -> None:
    """Place the placeholder genies a template holds, where their library opens."""
    recs = t.el.records
    if len(recs) < 2 or not any(po.type_code == DUMMY_TC and not po.class_flag for po in recs):
        return
    from .symbols import library_version
    from .symbols import SymbolResolver

    try:
        res = SymbolResolver.for_page(t.lib_path)
    except Exception:  # noqa: BLE001 -- the markers stay
        _note_exc("template_place._place_placeholders")
        return
    parents = P.forest_parents(recs)
    hits, deps = [], []
    for k, po in enumerate(recs):
        got = GP.placeholder_genie(po)
        if got is None:
            continue
        a, inside = parents[k], False
        while a is not None:
            if recs[a].subtype_size & 0x1:
                inside = True
                break
            a = parents[a]
        if inside:
            continue
        ref = got[0]
        try:
            rs = res.resolve(ref)
            if rs is None:
                continue
            el = GP.load_element(rs.library_path, ref.element, compose=True)
            el.library, el.element = ref.library, ref.element  # (the marker's spelling)
            deps.append((rs.library_path, library_version(rs.library_path)))
        except Exception:  # noqa: BLE001 -- that marker stays
            _note_exc("template_place._place_placeholders/load")
            continue
        hits.append((k, el))
    if not hits:
        return
    mine = {k for k, _el in hits}
    outside = {G.get_handle(po) for j, po in enumerate(recs) if j not in mine} - {None}
    order, entries, done = GP.place_page_placeholders(
        recs, hits, outside=outside, resolver=res, where="template_place._place_placeholders"
    )
    if not done:
        return
    # (the groups above a genie whose box is not its marker's take the union again)
    moved, off = [], 0
    for j in done:
        s = j + off
        e = subtree_end(order, s)
        off += (e - s) - 1
        if GP.decode(order[s]).rect1 != GP.decode(recs[j]).rect1:
            moved.append(s)
    if moved:
        dec = [GP.decode(po) for po in order]
        before = [(r.anchor, r.rect1, r.rect2) for r in dec]
        _refit_containers(dec, moved)
        order = [
            GP.to_po(GP.encode(r)) if (r.anchor, r.rect1, r.rect2) != before[k] else order[k]
            for k, r in enumerate(dec)
        ]
    el = copy.copy(t.el)
    el.records = order
    t.el = el
    for g, n in entries:
        if not n.startswith("\\") and links.entry_stamp(n) is not None:
            t.placeholder_overrides[links.strip_stamp(n).lower()] = (g, n)
    t.deps = tuple(t.deps) + tuple(deps)


def template_entries(tpl: Template) -> List[Tuple[bytes, str]]:
    """The template's own link table entries."""
    out = GP.element_table_entries(tpl.lib_path, tpl.el.element)
    over = dict(getattr(tpl, "placeholder_overrides", None) or ())
    over.update(tpl.entry_overrides)
    if not over:
        return out
    return [over.get(links.strip_stamp(n).lower(), (g, n)) for g, n in out]


def resolve_template(resolver, ref) -> Optional[Template]:
    rs = resolver.resolve(ref) if resolver is not None else None
    if rs is None:
        return None
    return load_template(rs.library_path, ref.element)


def parse_ref_text(text: str) -> Tuple[str, str, str]:
    """'<lib>.<elem>.<ctt' -> (lib, elem, 'ctt'). The library is all before the element's
    dot, the LAST one before the extension (CtDraw's, symbols._split_lib_element):
    a library file named 'plant.v2' is the library 'plant.v2'."""
    core, _, ext = text.rpartition(".")
    if not core or not ext.startswith("<"):
        core, ext = text, ""  # (no '.<ct?' suffix)
    lib, _, elem = core.rpartition(".")
    if not lib:
        lib, elem = core, ""
    return lib, elem, ext.lstrip("<").lower()


def reference_for(text: str) -> SymbolReference:
    lib, elem, ext = parse_ref_text(text)
    return SymbolReference(lib, elem, ext or "ctt", "symbol")


# --------------------------------------------------------------- page scan
subtree_end = _forest.subtree_end


def template_roots(objs) -> List[Tuple[int, int, object]]:
    """(start, end, SymbolReference) of every ROOT record placing a .ctt
    element (a page may hold 1..4 consecutive copies)."""
    out = []
    i = 0
    while i < len(objs):
        end = subtree_end(objs, i)
        po = objs[i]
        if po.subtype_size & 1 and po.raw_payload is None:
            try:
                ref = reference_from_object(po)
            except Exception:  # noqa: BLE001
                _note_exc("template_place.template_roots")
                ref = None
            if ref is not None and ref.ext == "ctt":
                out.append((i, end, ref))
        i = end
    return out


# ----------------------------------------------------------- nested levels
def _subst_level_nested(ent, outer, specials=None) -> None:
    """Re-derive a nested genie level's value bag from its templates and the outer level."""
    vals = ent[1]
    new = [(key, GP.substitute(tmpl, outer[1], outer[0], key, specials)) for key, tmpl in ent[2].pairs]
    newd = {k.lower(): v for k, v in new}

    def own(name):
        d = ent[0].get(name)
        return "" if (d is None or d == NONE_DEFAULT) else d

    for key, v in new:
        if vals.get(key) is not None:
            vals.set(key, v or own(key))
    have = {k.lower() for k, _v in vals.pairs}
    for name, d in ent[0].pairs:
        if name.lower() in have:
            continue
        v = newd.get(name.lower(), "") or ("" if d == NONE_DEFAULT else d)
        if v:
            vals.pairs.append((name, v))
            have.add(name.lower())
    for key, v in new:
        if key.lower() not in have and v:
            vals.pairs.append((key, v))
            have.add(key.lower())


def _rederive_levels(entries, untrimmed: Optional[dict] = None):
    """Re-derive levels from their outer levels when there's no placed level."""
    ents = [[b.copy() for b in e] for e in entries]
    N = len(ents) - 1
    for lvl in range(N - 1, 0, -1):
        _subst_level_nested(ents[lvl], ents[lvl + 1])
    e0, lv1 = ents[0], ents[1]
    for key, tmpl in e0[2].pairs:
        v = GP.substitute(tmpl, lv1[1], lv1[0], key)
        e0[1].set(key, v)
        if v[:1].isspace():
            low = key.lower()
            if untrimmed is not None:
                untrimmed[low] = v
            e0[1].pairs = [(kk, vv.lstrip() if kk.lower() == low else vv) for kk, vv in e0[1].pairs]
    return ents


# -------------------------------------------------------------- composition
def _mapper(total):
    a, b, c, d, h, k = total

    def mp(x, y):
        return (int(a * x + c * y + h), int(b * x + d * y + k))

    return mp


def _map_rect(mp, rect):
    (x1, y1), (x2, y2) = mp(*rect[:2]), mp(*rect[2:])
    return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))


def _group_rects(recs: List[GP.Rec], kids: Sequence[int]):
    """(rect1, rect2) of a 0x08 group over its direct children ``kids`` (geometry
    group_union: a DummyMarker counts in the Extent, an empty one not in the Bounds)."""
    return G.group_union([(recs[c].tc, recs[c].rect1, recs[c].rect2) for c in kids])


def _levels(r: GP.Rec) -> int:
    return len(r.entries) - 1 if r.entries else 0


def _marker_box(slots, fresh: bool = False):
    """(anchor, rect1, rect2) of a marker in a template copy, from its slots."""
    m2, s2 = slots[1]
    m3, s3 = slots[2]
    sH, sK = (s2[4], s2[5]) if m2 else (0.0, 0.0)
    T3 = s3 if m3 else P.IDENT
    x0, y0 = _mapper(T3)(sH, sK)
    x1, y1 = _mapper(T3)(sH + 1, sK + 1)
    if not m2:
        x1, y1 = x0, y0
    if m2 or not fresh:
        return (x0, y0), (x0, y0, x1, y1), (x0, y0, x1, y1)
    return None


def _prefix_handles(
    E, recs: List[GP.Rec], parents, used: set, root_handle: Optional[int], old_prefix
) -> List[int]:
    """The handles of the prefix records: the root's, a kept one by identity, then the
    twin's when free, else a fresh one; an input record shares its owner's."""
    n = len(E)
    old_by_an = {}
    if old_prefix:
        op = P.forest_parents(old_prefix)
        for j, po in enumerate(old_prefix):
            if j == 0 or (po.type_code in INPUT_TCS and op[j] is not None):
                continue
            old_by_an.setdefault((po.an_str, po.type_code), G.get_handle(po))
    h_root = root_handle
    next_free = GP.handle_allocator(used)
    if h_root is None:
        tw = G.get_handle(E[0])
        h_root = tw if tw not in used else next_free()
    used.add(h_root)
    new_h: List[Optional[int]] = [None] * n
    new_h[0] = h_root
    for k in range(1, n):  # kept handles (identity match) first
        if recs[k].tc in INPUT_TCS and parents[k] is not None:
            continue
        hk = old_by_an.get((str(G.get_handle(E[k])), recs[k].tc))
        if hk is not None:
            new_h[k] = hk
            used.add(hk)
    for k in range(1, n):  # then the twin's when free, else fresh
        if new_h[k] is not None or (recs[k].tc in INPUT_TCS and parents[k] is not None):
            continue
        tw = G.get_handle(E[k])
        new_h[k] = tw if (tw is not None and tw not in used) else next_free()
        used.add(new_h[k])
    for k in range(1, n):  # input records share their owner's
        if new_h[k] is None:
            new_h[k] = new_h[parents[k]]

    return new_h


def _prefix_bags(
    el, E, recs: List[GP.Rec], parents, new_h, link_text: str, local, root_values, page_name: str, fresh: bool
) -> tuple:
    """The property bags of the prefix records: (the root's levels, each record's
    entries (None: as stored), the e0 values before their left trim)."""
    n = len(E)
    mL, L = local
    N0 = _levels(recs[0])
    sub_ents: List[Optional[list]] = [None] * n
    untrimmed: Dict[int, Dict[str, str]] = {}  # {k: {key: e0 value before its left trim}}
    if N0 >= 1:
        # a NEW copy (New Page, a template switch that carries no values) binds nothing,
        # not even the declared defaults (LIVE)
        bind_none = fresh and root_values is None
        P0 = GP.Bag(list(root_values), 0) if root_values is not None else None
        if P0 is None:
            P0 = GP.Bag([])
            for name, dflt in GP.declared_params(el).items():
                if name.lower() != "an" and dflt and not bind_none:
                    P0.pairs.append((name, dflt))
        hs = [new_h[k] for k in range(n) if not (recs[k].tc in INPUT_TCS and parents[k] is not None)]
        el2 = copy.copy(el)
        el2.library, el2.element, _x = parse_ref_text(link_text)
        el2.ext = _x or "ctt"  # a super genie page links its '.<ctm'
        built = GP.build_instance_records(
            el2, (mL, tuple(L)), None, handles=hs, page_name=page_name, root_bag=P0, resolver=None
        )
        for k, po in enumerate(built):
            sub_ents[k] = GP.decode(po).entries
        if bind_none:
            _bind_nothing(sub_ents)
        if fresh and not P0.pairs:
            _unbound_as_typed(E, parents, sub_ents)
    else:
        for k, r in enumerate(recs):
            if r.entries is not None and len(r.entries) >= 2:
                sub_ents[k] = _rederive_levels(r.entries, untrimmed.setdefault(k, {}))

    return N0, sub_ents, untrimmed


def _prefix_records(
    E, recs: List[GP.Rec], new_h, sub_ents, untrimmed: dict, N0: int, link_text: str, root_an, root_values_ov
) -> None:
    """Each prefix record's handle, flags, link, AN, entries and class section."""
    for k, r in enumerate(recs):
        e_po = E[k]
        r.handle = new_h[k]
        if k == 0:
            r.dw = (r.dw & ~0x2) | 0x1 | 0x4
            r.link = link_text
            r.name = ""
            r.an = root_an if root_an is not None else str(G.get_handle(e_po))
        else:
            keep2 = r.tc == ACTIVEX_TC
            r.dw &= ~(0x1 | (0 if keep2 else 0x2))
            r.link = None
            r.an = str(G.get_handle(e_po))
        changed = False
        if sub_ents[k] is not None and r.entries is not None:
            ents = [[b.copy() for b in e] for e in sub_ents[k]]
            for li in range(min(len(ents), len(r.entries))):  # size words = history
                for bi in range(3):
                    ents[li][bi].ov = r.entries[li][bi].ov
            if k == 0 and N0 >= 1:
                ents[N0][1].ov = root_values_ov if root_values_ov is not None else 11
            changed = ents[0][1].pairs != r.entries[0][1].pairs
            r.entries = ents
        if r.tc != ACTIVEX_TC:  # ActiveX keeps its style + OLE section
            vals = cap = None
            if (
                r.entries is not None
                and (N0 >= 1 or changed)
                and ((r.tc == TEXT_TC and r.entries[0][2].pairs) or e_po.subtype_size & 0x100)
            ):
                # the metadata cache and a templated text's string section
                # follow the SUBSTITUTED value bag
                vals = r.entries[0][1]
                t = G.parse_text_tail(e_po) if e_po.subtype_size & 2 else None
                cap = t["text"] if t else None
                if r.entries[0][2].get("TXT_OFF") is not None:
                    cap = untrimmed.get(k, {}).get("txt_off", vals.get("TXT_OFF") or "")
            r.cls = GP.strip_class_section(r, vals, cap, e_po.subtype_size)
            if (
                (N0 >= 1 or changed)
                and r.dw & 0x80
                and r.entries is not None
                and r.entries[0][2].pairs
                and r.entries[0][1].get("TEXT") is not None
            ):
                r.s80 = GP.enc_lp(r.entries[0][1].get("TEXT"))
            r.style = None


def _prefix_slots(recs: List[GP.Rec], parents, mL: int, L, el) -> None:
    """The prefix records' transform slots under the copy's Local (``mL``, ``L``) of
    element ``el``."""
    n = len(recs)
    s2r, m2r = P.slot2_for_linked(tuple(L), mL, el.e)
    recs[0].slots = [(mL, tuple(L)), (m2r, s2r), (m2r, s2r)]
    for k in range(1, n):
        m2, s2 = recs[k].slots[1]
        m3, s3 = recs[k].slots[2]
        recs[k].slots = [(0, P.IDENT), (m2, s2), (m3, s3)]
    if not (mL == 0 and el.e == (0, 0)):
        for k in range(1, n):
            chain = []
            a = parents[k]
            while a is not None:
                if a == 0:
                    chain.append((tuple(L), mL, el.e))
                else:
                    ma, sa = recs[a].slots[1]
                    chain.append((sa, ma, (0, 0)))
                a = parents[a]
            m2, s2 = recs[k].slots[1]
            s3, m3 = P.apply_parent(s2, m2, chain)
            recs[k].slots[2] = (m3, s3)


def _prefix_geometry(E, recs: List[GP.Rec], parents, fresh: bool) -> None:
    """The prefix records' anchors and boxes, bottom-up."""
    n = len(recs)
    rootT = recs[0].slots[2][1] if recs[0].slots[2][0] else P.IDENT
    mp = _mapper(rootT)
    children = [[] for _ in range(n)]
    for k in range(n):
        if parents[k] is not None:
            children[parents[k]].append(k)
    for k in range(n - 1, -1, -1):
        r, e_po = recs[k], E[k]
        if r.cf and children[k] and r.tc in (GROUP_TC, SYMSET_TC):
            if r.tc == SYMSET_TC:
                kids = [c for c in children[k] if recs[c].tc not in GP.UNION_SKIP] or children[k]
                r1, r2 = recs[kids[0]].rect1, recs[kids[0]].rect2
            else:
                r1, r2 = _group_rects(recs, children[k])
            r.rect1, r.rect2 = r1, r2
            r.anchor = (r1[0], r1[3] - 1) if r.tc == GROUP_TC else mp(*r.anchor)
            continue
        anchor = mp(*r.anchor)
        r1 = _map_rect(mp, r.rect1)
        r2 = tuple(r1[i] + (r.rect2[i] - r.rect1[i]) for i in range(4))
        if r.tc in POINT_CLASSES:
            ax, ay = anchor
            ox, oy = r.anchor
            r1 = (r.rect1[0] + ax - ox, r.rect1[1] + ay - oy, r.rect1[2] + ax - ox, r.rect1[3] + ay - oy)
            r2 = tuple(r1[i] + (r.rect2[i] - r.rect1[i]) for i in range(4))
        if r.tc == DUMMY_TC:
            box = _marker_box(r.slots, fresh)
            if box is not None:
                anchor, r1, r2 = box
        if r.tc == TEXT_TC and r.entries is not None:
            ext = _text_extent_if_changed(r, e_po, anchor)
            if ext is not None and tuple(ext[0]) != tuple(r1):
                r1, r2 = ext
        r.anchor, r.rect1, r.rect2 = anchor, r1, r2


def compose_prefix(
    tpl: Template,
    *,
    link_text: str,
    local=(0, P.IDENT),
    root_values: Optional[List[Tuple[str, str]]] = None,
    root_values_ov: Optional[int] = None,
    root_an: Optional[str] = None,
    root_handle: Optional[int] = None,
    old_prefix: Optional[Sequence[PageObject]] = None,
    used_outside: Optional[set] = None,
    page_name: str = "",
    fresh: bool = False,
    measure_texts: bool = False,
) -> List[PageObject]:
    """The template prefix records for ``tpl``."""
    el = tpl.el
    E = el.records
    parents = P.forest_parents(E)
    recs = [GP.decode(po) for po in E]
    used = set(used_outside or ())
    mL, L = local
    new_h = _prefix_handles(E, recs, parents, used, root_handle, old_prefix)
    N0, sub_ents, untrimmed = _prefix_bags(
        el, E, recs, parents, new_h, link_text, local, root_values, page_name, fresh
    )
    _prefix_records(E, recs, new_h, sub_ents, untrimmed, N0, link_text, root_an, root_values_ov)
    _prefix_slots(recs, parents, mL, L, el)
    _prefix_geometry(E, recs, parents, fresh)
    return [GP.to_po(GP.encode(r)) for r in recs]


def _bind_nothing(sub_ents) -> None:
    """Clear the placed levels' values of a new template copy."""
    for ents in sub_ents:
        if not ents or len(ents) < 2:
            continue
        top = ents[-1]
        if top[1].pairs and not top[2].pairs:
            top[1] = GP.Bag([], top[1].ov)


def _unbound_as_typed(E: Sequence[PageObject], parents, sub_ents) -> None:
    """A fresh template copy with no parameter values shows its texts as typed."""
    tpl = {k for k, po in enumerate(E) if k and is_template_copy_root(po)}
    for k, ents in enumerate(sub_ents):
        if not k or E[k].type_code != TEXT_TC or not ents or len(ents) < 2:
            continue
        typed = ents[0][2].get("TXT_OFF")
        if not typed or not GP.template_refs(GP.Bag([("TXT_OFF", typed)])):
            continue
        a, direct = parents[k], True
        while a is not None and a != 0:
            if (E[a].class_flag and E[a].subtype_size & 0x200) or a in tpl:
                direct = False
                break
            a = parents[a]
        if direct:
            ents[0][1].set("TXT_OFF", typed)


def _text_extent_if_changed(r, e_po, anchor):
    """(rect1, rect2) of a copy's text member if it changed, else None."""
    try:
        if not e_po.subtype_size & 2:
            return None
        return GP.member_text_box(r, e_po, anchor)
    except Exception:  # noqa: BLE001 -- keep the mapped stored extent
        _note_exc("template_place._text_extent_if_changed")
        return None


# ------------------------------------------------------------- update merge
def _text_key(r: GP.Rec):
    if r.tc != TEXT_TC or len(r.cls) < 4:
        return None
    try:
        sl = GP.text_strings_len(r.cls)
    except Exception:  # noqa: BLE001
        _note_exc("template_place._text_key")
        return None
    v = r.entries[0][1] if r.entries else GP.Bag([])
    return (r.cls[:sl], r.anchor, v.get("TXTMODE"), v.get("TXT_STR"), v.get("TXT_OFF"))


def _refit_containers(recs: List[GP.Rec], moved: Optional[Sequence[int]] = None) -> None:
    """The groups and symbol sets of ``recs`` refit around their children, bottom-up; with
    ``moved`` (record indexes) only the containers above those records."""
    n = len(recs)
    stack, parents = [], []
    for i, r in enumerate(recs):
        while stack and stack[-1][1] == 0:
            stack.pop()
        if stack:
            parents.append(stack[-1][0])
            stack[-1][1] -= 1
        else:
            parents.append(None)
        if r.cf:
            stack.append([i, r.cf])
    children = [[] for _ in range(n)]
    for k in range(n):
        if parents[k] is not None:
            children[parents[k]].append(k)
    above = None
    if moved is not None:
        above = set()
        for k in moved:
            a = parents[k]
            while a is not None and a not in above:
                above.add(a)
                a = parents[a]
    for k in range(n - 1, -1, -1):
        r = recs[k]
        if not (r.cf and children[k] and r.tc in (GROUP_TC, SYMSET_TC)):
            continue
        if above is not None and k not in above:
            continue
        if r.tc == SYMSET_TC:
            kids = [c for c in children[k] if recs[c].tc not in GP.UNION_SKIP] or children[k]
            r.rect1, r.rect2 = recs[kids[0]].rect1, recs[kids[0]].rect2
        else:
            r.rect1, r.rect2 = _group_rects(recs, children[k])
            r.anchor = (r.rect1[0], r.rect1[3] - 1)


def merge_onto(
    old: Sequence[PageObject], new: List[PageObject], keep_text_extent: bool = True
) -> List[PageObject]:
    """Merge onto surviving records, keeping their bag key order and size words."""
    idx = {}
    seen = collections.Counter()
    for j, po in enumerate(old):
        key = (po.an_str, po.type_code)
        idx[key + (seen[key],)] = j
        seen[key] += 1
    seen = collections.Counter()
    recs = []
    kept_text = False
    for k, n_po in enumerate(new):
        key = (n_po.an_str, n_po.type_code)
        j = 0 if k == 0 else idx.get(key + (seen[key],))
        seen[key] += 1
        rn = GP.decode(n_po)
        if j is None or old[j].type_code != n_po.type_code:
            recs.append(rn)
            continue
        ro = GP.decode(old[j])
        if ro.entries and rn.entries and len(ro.entries) == len(rn.entries):
            rn.entries = [
                [GP.merge_bag(bo, bn) for bo, bn in zip(eo, en)] for eo, en in zip(ro.entries, rn.entries)
            ]
        if keep_text_extent and rn.tc == TEXT_TC and (rn.rect1, rn.rect2) != (ro.rect1, ro.rect2):
            ko, kn = _text_key(ro), _text_key(rn)
            if ko is not None and ko == kn:
                rn.rect1, rn.rect2 = ro.rect1, ro.rect2
                kept_text = True
        recs.append(rn)
    if kept_text:
        _refit_containers(recs)
    return [GP.to_po(GP.encode(r)) for r in recs]


# ----------------------------------------------------------------- page ops
def link_entry(tpl: Template, link_text: str):
    el = copy.copy(tpl.el)
    el.library, el.element, _x = parse_ref_text(link_text)
    el.ext = _x or "ctt"  # '.<ctm' for a super genie page
    return GP.link_table_entry(el)


def root_local(po: PageObject):
    (m1, L, _o, _n), _s2, _s3 = P.read_slots(po.body_raw)[0]
    return (m1, tuple(L))


def root_values(po: PageObject):
    r = GP.decode(po)
    N = _levels(r)
    if N < 1:
        return None, None
    b = r.entries[N][1]
    return list(b.pairs), b.ov


def is_stale(page_block, tpl: Template, link_text: str, page_name: str = "") -> bool:
    """Is the page's template entry stamp different from the element's current one?"""
    if not page_block.link_count:
        return True
    t = links.parse_link_table(page_block.link_table_raw)
    want_guid, want_name = link_entry(tpl, link_text)
    base = links.strip_stamp(want_name).lower()
    for guid, name in t[1] if t else []:
        if links.strip_stamp(name).lower() == base:
            if (guid, name.lower()) != (want_guid, want_name.lower()):
                return True
            if (
                getattr(tpl, "entry_overrides", None)
                or getattr(tpl, "placeholder_overrides", None)
                or copy_placeholders_open(page_block.objects, tpl, link_text)
            ):
                return not shows_composition(page_block.objects, tpl, link_text, page_name)
            return False
    return True


def copy_placeholders(objs: Sequence[PageObject], link_text: str) -> List[Tuple[int, SymbolReference]]:
    """(index, the genie's reference) of the PLACEHOLDER genies (genie_place.placeholder_genie)
    inside the page's placements of the template ``link_text`` names."""
    lib, elem, _x = parse_ref_text(links.strip_stamp(link_text))
    want = (lib.lower(), elem.lower())
    out = []
    for a, b, ref in template_roots(objs):
        if (ref.library.lower(), ref.element.lower()) != want:
            continue
        for k in range(a + 1, b):
            got = GP.placeholder_genie(objs[k])
            if got is not None:
                out.append((k, got[0]))
    return out


def copy_placeholders_open(objs: Sequence[PageObject], tpl: Template, link_text: str) -> bool:
    """Does the page's template copy hold a placeholder genie whose library opens now?"""
    hits = copy_placeholders(objs, link_text)
    if not hits:
        return False
    from .symbols import SymbolResolver

    try:
        res = SymbolResolver.for_page(tpl.lib_path)
    except Exception:  # noqa: BLE001 -- read as current: the markers stay
        _note_exc("template_place.copy_placeholders_open")
        return False
    for _k, ref in hits:
        try:
            if res.resolve(ref) is not None:
                return True
        except Exception:  # noqa: BLE001  # swallow-ok: a library that does not open places nothing
            continue
    return False


def shows_composition(objs: Sequence[PageObject], tpl: Template, link_text: str, page_name: str = "") -> bool:
    """The page's placements of the template ``link_text`` names (:func:`template_roots`)
    hold the records :func:`recompose` gives them from ``tpl`` (the template as its load
    holds it): an update of the page would change none of them. Remembered with the
    template (``tpl.shown``), by the records and the page name."""
    from . import writer

    lib, elem, _x = parse_ref_text(links.strip_stamp(link_text))
    want = (lib.lower(), elem.lower())
    spans = [
        (a, b) for a, b, ref in template_roots(objs) if (ref.library.lower(), ref.element.lower()) == want
    ]
    if not spans:
        return True
    h = hashlib.sha1()
    for a, b in spans:
        h.update(b"%d:%d;" % (a, b))
        for po in objs[a:b]:
            pl = writer.object_payload(po)
            h.update(b"%d;" % len(pl))
            h.update(pl)
    key = (page_name, len(objs), h.digest())
    memo = getattr(tpl, "shown", None)
    if memo is None:
        memo = tpl.shown = {}
    hit = memo.get(key)
    if hit is not None:
        return hit
    same = True
    for a, b in spans:
        new = _recompose_span(objs, a, b, tpl, page_name)
        if [writer.object_payload(po) for po in new] != [writer.object_payload(po) for po in objs[a:b]]:
            same = False
            break
    if len(memo) > 64:
        memo.clear()
    memo[key] = same
    return same


def _recompose_span(
    objs: Sequence[PageObject],
    a: int,
    b: int,
    t: Template,
    page_name: str = "",
    merge: bool = True,
    values: Optional[dict] = None,
) -> List[PageObject]:
    """The template placement ``objs[a:b]`` composed again from ``t`` (:func:`recompose`'s
    rule for one placement)."""
    old = list(objs[a:b])
    outside = {G.get_handle(po) for j, po in enumerate(objs) if not (a <= j < b)}
    vals, vov = root_values(old[0])
    if values:
        vals = list(vals or [])
        for k, v in values.items():
            hit = next((n for n, (kk, _vv) in enumerate(vals) if kk.lower() == str(k).lower()), None)
            if hit is None:
                vals.append((str(k), str(v)))
            else:
                vals[hit] = (vals[hit][0], str(v))
    link_text = GP.decode(old[0]).link
    new = compose_prefix(
        t,
        link_text=link_text,
        local=root_local(old[0]),
        root_values=vals,
        root_values_ov=vov,
        root_an=old[0].an_str,
        root_handle=G.get_handle(old[0]),
        old_prefix=old,
        used_outside=outside,
        page_name=page_name,
    )
    if merge:
        new = merge_onto(old, new)
    return new


def recompose(
    objs: Sequence[PageObject],
    resolver,
    *,
    page_name: str = "",
    merge: bool = True,
    only_stale_of=None,
    values: Optional[dict] = None,
) -> dict:
    """Recompose every template placement of a page from its current element."""
    objs = list(objs)
    roots = template_roots(objs)
    out, pos, spans = [], 0, []
    entry = tpl0 = link0 = None
    for a, b, ref in roots:
        t = resolve_template(resolver, ref)
        if t is None:
            continue
        out.extend(objs[pos:a])
        link_text = GP.decode(objs[a]).link
        new = _recompose_span(objs, a, b, t, page_name, merge=merge, values=values)
        spans.append((len(out), b - a, len(new)))
        out.extend(new)
        pos = b
        if entry is None:
            entry, tpl0, link0 = link_entry(t, link_text), t, link_text
    if tpl0 is None:
        return dict(objects=None, spans=[], entry=None, template=None, link=None)
    out.extend(objs[pos:])
    return dict(objects=out, spans=spans, entry=entry, template=tpl0, link=link0)


# ------------------------------------------------- a template's copy in a library element
def is_template_copy_root(po: PageObject) -> bool:
    """Is ``po`` the root of a template copy held by a library element?"""
    if (
        not po.class_flag
        or not po.subtype_size & 0x1
        or po.subtype_size & 0x200
        or po.raw_payload is not None
    ):
        return False
    try:
        ref = reference_from_object(po)
    except Exception:  # noqa: BLE001  # swallow-ok: an unreadable link names no template
        return False
    return ref is not None and ref.ext == "ctt"


def nested_template_spans(recs: Sequence[PageObject]) -> List[Tuple[int, int, SymbolReference]]:
    """(start, end, reference) of each template copy in a library element's records (its
    record tree, or its session page): :func:`is_template_copy_root` and its subtree."""
    out = []
    k, n = 0, len(recs)
    while k < n:
        po = recs[k]
        if is_template_copy_root(po):
            end = subtree_end(recs, k)
            out.append((k, end, reference_from_object(po)))
            k = end
        else:
            k += 1
    return out


def nested_template_keys(recs: Sequence[PageObject]) -> frozenset:
    """(library, element), lower case, of the templates whose copies ``recs`` hold."""
    return frozenset(
        (ref.library.lower(), ref.element.lower()) for _a, _b, ref in nested_template_spans(recs)
    )


def _key_of(path: str, element: str) -> tuple:
    return path_key(path), (element or "").lower()


def _label_of(path: str, element: str) -> str:
    return "%s.%s" % (os.path.splitext(os.path.basename(path))[0], element)


def template_nesting_chain(
    recs: Sequence[PageObject], target_path: str, target: str, resolver
) -> Optional[List[str]]:
    """The templates by which ``recs`` hold a copy of the target template."""
    from .symbols import SymbolResolver

    want = _key_of(target_path, target)
    seen = set()
    queue = [(list(recs), resolver, [])]
    while queue:
        records, res, chain = queue.pop(0)
        for _a, _b, ref in nested_template_spans(records):
            try:
                got = res.resolve(ref) if res is not None and ref is not None else None
            except Exception:  # noqa: BLE001  # swallow-ok: an unresolved copy holds nothing
                got = None
            if got is None:
                continue
            key = _key_of(got.library_path, ref.element)
            step = chain + [_label_of(got.library_path, ref.element)]
            if key == want:
                return step
            if key in seen:
                continue
            seen.add(key)
            try:
                inner = GP.load_element(got.library_path, ref.element, refresh=False).records
                sub = SymbolResolver.for_page(got.library_path)
            except Exception:  # noqa: BLE001  # swallow-ok: an unreadable template holds nothing
                continue
            queue.append((inner, sub, step))
    return None


def _rederive_markers(span: Sequence[PageObject]) -> List[PageObject]:
    """Re-derive the boxes of a stored template copy's free markers."""
    out = list(span)
    parents = P.forest_parents(out)
    marks = [
        k
        for k in range(1, len(out))
        if out[k].type_code == DUMMY_TC and (parents[k] is None or out[parents[k]].type_code != SYMSET_TC)
    ]
    boxes = {}
    for k in marks:
        r = GP.decode(out[k])
        if r.slots[1][0]:
            continue  # (a point in slot 2: kept)
        box = _marker_box(r.slots)
        if box is not None and box != (r.anchor, r.rect1, r.rect2):
            boxes[k] = box
    if not boxes:
        return out
    recs = [GP.decode(po) for po in out]
    before = [(r.anchor, r.rect1, r.rect2) for r in recs]
    for k, box in boxes.items():
        recs[k].anchor, recs[k].rect1, recs[k].rect2 = box
    _refit_containers(recs, list(boxes))
    return [
        GP.to_po(GP.encode(r)) if (r.anchor, r.rect1, r.rect2) != before[k] else out[k]
        for k, r in enumerate(recs)
    ]


def _compose_copy(
    old: Sequence[PageObject],
    tpl: Template,
    link_text: str,
    outside: set,
    page_name: str = "",
    merge: bool = True,
) -> List[PageObject]:
    """Recompose one template copy of a library element."""
    old = list(old)
    vals, vov = root_values(old[0])
    new = compose_prefix(
        tpl,
        link_text=link_text,
        local=root_local(old[0]),
        root_values=vals,
        root_values_ov=vov,
        root_an=old[0].an_str,
        root_handle=G.get_handle(old[0]),
        old_prefix=old,
        used_outside=outside,
        page_name=page_name,
        measure_texts=True,
    )
    if merge:
        new = merge_onto(old, new, keep_text_extent=False)
    lock = old[0].subtype_size & 0x4
    if new[0].subtype_size & 0x4 != lock:
        r0 = GP.decode(new[0])
        r0.dw = (r0.dw & ~0x4) | lock
        new[0] = GP.to_po(GP.encode(r0))
    return new


def copies_in_element_form(recs: Sequence[PageObject]) -> Optional[bool]:
    """Are the template copies this element holds in the element form?"""
    if not recs:
        return False
    try:
        return len(GP.decode(recs[0]).entries or ()) >= 2
    except Exception:  # noqa: BLE001 -- the copies are kept as stored
        _note_exc("template_place.copies_in_element_form")
        return None


#: the levels a template with parameters of its own adds to every record of a copy it holds
#: (its element level: one)
ELEMENT_LEVELS = 1


def element_form_entries(entries, tc: int = 0, nested: bool = False):
    """A template copy record's levels in the element form, from its page form."""
    if entries is None:
        return None
    from .genie_norm import class_ov

    ents = [[b.copy() for b in lv] for lv in entries]
    for i, lv in enumerate(ents):
        if not lv[2].pairs and lv[1].pairs:
            lv[2] = GP.Bag(list(lv[1].pairs), lv[1].ov if i else lv[2].ov)
    if ents and not nested:
        ents[0][2].ov = class_ov(tc)
    for _ in range(ELEMENT_LEVELS):
        ents.append([GP.Bag([], 0), GP.Bag([], 0), GP.Bag([], 0)])
    return ents


def to_element_form(span: Sequence[PageObject]) -> List[PageObject]:
    """A template copy's records (its root first) in the ELEMENT form
    (:func:`element_form_entries` on each record that has bags; a record is nested when it
    or a record above it in the copy is a genie root)."""
    span = list(span)
    out = []
    for po, nested in zip(span, _nested_in_copy(span)):
        if not po.subtype_size & 0x8:
            out.append(po)
            continue
        r = GP.decode(po)
        if r.entries is None:
            out.append(po)
            continue
        r.entries = element_form_entries(r.entries, r.tc, nested)
        out.append(GP.to_po(GP.encode(r)))
    return out


def _page_form(span: Sequence[PageObject]) -> List[PageObject]:
    """An ELEMENT-form copy's records with the outer template's own level taken off each
    record: the page form the composition reads (its root values, the members' handles)."""
    out = []
    for po in span:
        r = GP.decode(po) if po.subtype_size & 0x8 else None
        if r is None or r.entries is None or len(r.entries) <= ELEMENT_LEVELS:
            out.append(po)
            continue
        r.entries = [[b.copy() for b in lv] for lv in r.entries[:-ELEMENT_LEVELS]]
        out.append(GP.to_po(GP.encode(r)))
    return out


def _nested_in_copy(span: Sequence[PageObject]) -> List[bool]:
    """Per record of a template copy (its root first): it is a genie root or below one (a
    genie copy the inner template holds)."""
    span = list(span)
    parents = P.forest_parents(span)
    out = []
    for k, po in enumerate(span):
        a = k
        hit = False
        while a is not None and a != 0:
            if span[a].class_flag and span[a].subtype_size & 0x200:
                hit = True
                break
            a = parents[a]
        out.append(hit)
    return out


def _values_in_template_order(span: Sequence[PageObject]) -> List[PageObject]:
    """Each copy record's value bag in its template bag's key order."""
    out = []
    for po, nested in zip(span, _nested_in_copy(span)):
        r = GP.decode(po) if (po.subtype_size & 0x8 and not nested) else None
        if r is None or not r.entries:
            out.append(po)
            continue
        lv = r.entries[0]
        if lv[2].pairs and lv[1].pairs:
            b1 = _in_order(lv[1], lv[2])
            if b1.pairs != lv[1].pairs:
                lv[1] = b1
                out.append(GP.to_po(GP.encode(r)))
                continue
        out.append(po)
    return out


def _matched(old: Sequence[PageObject], new: Sequence[PageObject]) -> List[bool]:
    """Per record of ``new``: a record of ``old`` matches it (the root, else link number +
    class + the occurrence, :func:`merge_onto`'s match)."""
    have = collections.Counter((po.an_str, po.type_code) for po in old)
    seen: collections.Counter = collections.Counter()
    out = []
    for k, po in enumerate(new):
        key = (po.an_str, po.type_code)
        out.append(k == 0 or seen[key] < have[key])
        seen[key] += 1
    return out


def _compose_copy_element_form(
    old: Sequence[PageObject],
    tpl: Template,
    link_text: str,
    outside: set,
    page_name: str = "",
    memory: bool = False,
) -> List[PageObject]:
    """Recompose an element-form template copy, as CtDraw's load + save does."""
    old = list(old)
    new = _compose_copy(_page_form(old), tpl, link_text, outside, page_name, merge=False)
    ef = to_element_form(new)
    if memory:
        ef = [e if hit else n for e, n, hit in zip(ef, new, _matched(old, new))]
    new = merge_onto(old, ef, keep_text_extent=False)
    # (the save writes the value bags in the template bags' order; the load holds them as
    # stored)
    return new if memory else _values_in_template_order(new)


def compose_nested_templates(
    recs: Sequence[PageObject],
    resolver,
    *,
    only=None,
    report: Optional[dict] = None,
    markers: bool = True,
    page_name: str = "",
    memory: bool = False,
) -> List[PageObject]:
    """Recompose the template copies a library element holds from their current template."""
    out = list(recs)
    composed, stale = set(), set()
    spans = nested_template_spans(out)
    element_form = bool(spans) and copies_in_element_form(out)
    for a, b, ref in reversed(spans):
        key = (ref.library.lower(), ref.element.lower())
        new = None
        if only is None or key in only:
            try:
                tpl = resolve_template(resolver, ref) if element_form is not None else None
                if tpl is not None:
                    r0 = GP.decode(out[a])
                    outside = {G.get_handle(po) for j, po in enumerate(out) if not a <= j < b}
                    outside.discard(None)
                    if element_form:
                        new = _compose_copy_element_form(
                            out[a:b], tpl, r0.link or ref.raw, outside, page_name, memory=memory
                        )
                    else:
                        new = _compose_copy(out[a:b], tpl, r0.link or ref.raw, outside, page_name)
            except Exception:  # noqa: BLE001 -- the copy keeps its records (and old stamp)
                _note_exc("template_place.compose_nested_templates")
                new = None
            (composed if new is not None else stale).add(key)
        if new is None and markers:
            new = _rederive_markers(out[a:b])
        if new is not None:
            out[a:b] = new
    if report is not None:
        report["composed"] = frozenset(composed - stale)
        report["stale"] = frozenset(stale)
    return out


def replace_link_entry(
    link_raw: bytes, link_count: int, area: str, old_ref_base: Optional[str], entry, records=None
) -> Tuple[bytes, int]:
    """The link table with the template's entry replaced in place or inserted at the front."""
    t = links.parse_link_table(link_raw) if link_count else None
    area0, ents = t if t is not None else (area or "    ", [])
    ents = list(ents)
    hit = None
    for i, (_g, nm) in enumerate(ents):
        base = links.strip_stamp(nm).lower()
        if (old_ref_base and base == old_ref_base.lower()) or base.endswith(".<ctt"):
            hit = i
            break
    if hit is None:
        ents.insert(0, entry)
    else:
        ents[hit] = entry
    if records is not None:
        roots = template_roots(records)
        if roots:
            a, b, _r = roots[0]
            ents = GP.add_record_labels(ents, list(records[a:b]), after=entry[1])
    return links.serialize_link_table(area0, ents)


def switch(
    objs: Sequence[PageObject],
    resolver,
    new_link_text: str,
    *,
    page_name: str = "",
    keep_local: bool = False,
    carry_params: bool = True,
) -> dict:
    """Switch a page to another template."""
    objs = list(objs)
    roots = template_roots(objs)
    tpl = resolve_template(resolver, reference_for(new_link_text))
    if tpl is None:
        raise KeyError("template %s is not on this project's include path" % new_link_text)
    at = roots[0][0] if roots else 0
    old_root = objs[roots[0][0]] if roots else None
    drop = set()
    for a, b, _r in roots:
        drop.update(range(a, b))
    keep = [po for j, po in enumerate(objs) if j not in drop]
    at_keep = sum(1 for j in range(at) if j not in drop)
    outside = {G.get_handle(po) for po in keep}
    vals = None
    if carry_params and old_root is not None:
        v, _ov = root_values(old_root)
        if v:
            decl = GP.declared_params(tpl.el)
            low = {k.lower() for k in decl}
            bag = GP.Bag([(k, d) for k, d in decl.items() if d and k.lower() != "an"])
            for k, val in v:
                if k.lower() in low:
                    bag.set(k, val)
            vals = bag.pairs
    local = root_local(old_root) if (keep_local and old_root is not None) else (0, P.IDENT)
    # (a fresh copy, as New Page makes: the template change of CtDraw's Page Properties
    # is not captured)
    new = compose_prefix(
        tpl,
        link_text=new_link_text,
        local=local,
        root_values=vals,
        used_outside=outside,
        page_name=page_name,
        fresh=True,
    )
    old_link = GP.decode(old_root).link if old_root is not None else None
    return dict(
        objects=keep[:at_keep] + new + keep[at_keep:],
        template=tpl,
        old_link=old_link,
        entry=link_entry(tpl, new_link_text),
        removed=len(drop),
        added=len(new),
    )


def template_page_properties(tpl: Template):
    """(background (on, off) or None, [(key, value)] non-'!' page properties)
    CtDraw's template load puts on the page."""
    if tpl.trailer is None:
        return None, []
    try:
        bg = tpl.trailer.background()
    except Exception:  # noqa: BLE001
        _note_exc("template_place.template_page_properties")
        bg = None
    try:
        props = [(k, v) for k, v in tpl.trailer.page_properties() if not k.startswith("!")]
    except Exception:  # noqa: BLE001
        _note_exc("template_place.template_page_properties")
        props = []
    return bg, props


def template_associations(tpl: Template) -> List[Tuple[str, str]]:
    """The template trailer's associations."""
    from .element import three_field

    try:
        pairs = list(tpl.trailer.associations or []) if tpl.trailer is not None else []
    except Exception:  # noqa: BLE001
        _note_exc("template_place.template_associations")
        return []
    return three_field(pairs)


# ------------------------------------------------------------ new pages
#: projects a Plant SCADA install ships: their libraries are AVEVA's (a change
#: reaches every project that includes them and is lost on a reinstall)
SHIPPED_PROJECTS = (
    "Include",
    "System",
    "SxW_Style_Include",
    "Tab_Style_Include",
    "SA_Include",
    "SA_Library",
    "SA_Controls",
    "Library_Controls",
    "Library_Equipment",
    "IndustrialGraphics_Include",
    "CSV_Include",
)

#: the resolutions a template element name carries ('normal@HD1080',
#: 'blank@-XGA'; the SA templates spell UHD4K '4K'), with their page size
RESOLUTIONS = (
    ("VGA", 640, 480),
    ("SVGA", 800, 600),
    ("XGA", 1024, 768),
    ("SXGA", 1280, 1024),
    ("HD768", 1366, 768),
    ("HD720", 1280, 720),
    ("HD1080", 1920, 1080),
    ("WUXGA", 1920, 1200),
    ("UHD4K", 3840, 2160),
    ("4K", 3840, 2160),
)


def shipped_project(path: str) -> Optional[str]:
    """The shipped project (SHIPPED_PROJECTS) ``path`` lives in, else None."""
    d = os.path.basename(os.path.dirname(os.path.abspath(path)))
    for name in SHIPPED_PROJECTS:
        if d.lower() == name.lower():
            return name
    return None


def split_element(element: str) -> Tuple[str, str, bool]:
    """'pagecontent@-HD1080' -> ('pagecontent', 'HD1080', title_bar=False);
    an undecorated element -> (element, '', True)."""
    name, sep, deco = element.partition("@")
    if not sep:
        return element, "", True
    return name, deco.lstrip("-"), not deco.startswith("-")


def element_for(name: str, resolution: str, title_bar: bool) -> str:
    """The element name of template ``name`` in ``resolution`` with / without
    the title bar ('normal', 'HD1080', True -> 'normal@HD1080')."""
    if not resolution:
        return name
    return "%s@%s%s" % (name, "" if title_bar else "-", resolution)


def new_page_bytes(
    width: int,
    height: int,
    *,
    background=None,
    page_properties=(),
    associations=(),
    stamp: Optional[int] = None,
    objects: Sequence[PageObject] = (),
    link_entries=(),
) -> bytes:
    """A new page .ctg, as CtDraw's New Page starts one."""
    from ..infra import identity
    from . import writer
    from .attributes import NO_COLOUR
    from .container import Block, Container
    from .element import ctdraw_timestamp
    from .element_session import attributes_block
    from .page import PAGE_HEAD_SIZE, PageBlock

    w, h = max(1, int(width)), max(1, int(height))
    stamp = ctdraw_timestamp() if stamp is None else int(stamp) & 0xFFFFFFFF
    ident = struct.pack("<I", stamp) + identity.guid_bytes()
    raw, count = links.serialize_link_table("    ", list(link_entries))
    objs = list(objects)
    pb = PageBlock(
        0, len(objs), 0, w, h, count, ident, "    ", PAGE_HEAD_SIZE, PAGE_HEAD_SIZE, raw, objs, b""
    )
    page_data = writer.serialize_page_block(pb, bytes(PAGE_HEAD_SIZE))
    bg = tuple(background) if background is not None else (NO_COLOUR, NO_COLOUR)
    attr_data = attributes_block(ident, w, h, bg, list(page_properties), list(associations))
    cont = Container(
        9,
        0x0C,
        stamp,
        stamp,
        8,
        [Block(0x0001, "page", 0, 0, page_data), Block(0x0101, "attributes", 0, 0, attr_data)],
    )
    cont.relayout()
    return cont.to_bytes()


def add_entries(ents: List[Tuple[bytes, str]], new, at: Optional[int] = None, current=None) -> None:
    """Insert link entries not listed yet."""
    have_ref = {links.strip_stamp(n).lower() for _g, n in ents if not n.startswith("\\")}
    have_lab = {n for _g, n in ents if n.startswith("\\")}
    pos = len(ents) if at is None else at
    for g, n in new:
        if n.startswith("\\"):
            if n in have_lab:
                continue
            have_lab.add(n)
        else:
            key = links.strip_stamp(n).lower()
            if key in have_ref:
                if current is not None:
                    k = next(
                        i
                        for i, (_g, m) in enumerate(ents)
                        if not m.startswith("\\") and links.strip_stamp(m).lower() == key
                    )
                    if GP.carried_entry_wins(ents[k], (g, n), current(n)):
                        base = links.strip_stamp(ents[k][1])
                        ents[k] = (g, base + n[len(links.strip_stamp(n)) :])
                continue
            have_ref.add(key)
        ents.insert(pos, (g, n))
        pos += 1


def new_page(
    tpl: Template,
    link_text: str,
    *,
    page_name: str = "",
    linked: bool = True,
    background=None,
    resolver=None,
    stamp: Optional[int] = None,
) -> bytes:
    """File > New > Page from a template."""
    bg_t, props = template_page_properties(tpl)
    recs = compose_prefix(tpl, link_text=link_text, page_name=page_name, fresh=True)
    if linked:
        entries = [link_entry(tpl, link_text)]
        add_entries(entries, GP.label_entries(recs))
    else:
        # (the root's children are complete subtrees: they become the page's roots)
        recs = unlinked_copy(recs, tpl, resolver)[1:]
        entries = []
        # (the copies are the template's stored records: what they nest is at the version
        # the template's own import table quotes)
        add_entries(
            entries,
            GP.page_link_entries(recs, resolver, carried=template_entries(tpl))
            if resolver is not None
            else GP.label_entries(recs),
        )
    return new_page_bytes(
        tpl.width,
        tpl.height,
        background=bg_t or background,
        page_properties=props,
        associations=template_associations(tpl),
        stamp=stamp,
        objects=recs,
        link_entries=entries,
    )


# ---------------------------------------------------------------- cut link
def unlinked_copy(prefix: Sequence[PageObject], tpl: Template, resolver=None) -> List[PageObject]:
    """The template prefix as ordinary page objects."""
    from . import writer

    E = tpl.records
    prefix = list(prefix)
    if len(prefix) != len(E) or any(
        p.type_code != e.type_code or p.class_flag != e.class_flag for p, e in zip(prefix, E)
    ):
        raise ValueError("the page's copy of its template does not match the template record for record")
    rp0 = GP.decode(prefix[0])
    m3, T = rp0.slots[2]
    T = tuple(T) if m3 else P.IDENT
    if any(abs(a - b) > 1e-9 for a, b in zip(T[:4], P.IDENT[:4])):
        raise ValueError(
            "the template is scaled or rotated on this page; only an unscaled template can be unlinked"
        )
    dx, dy = int(round(T[4])), int(round(T[5]))
    copies = [GP.to_po(writer.object_payload(po)) for po in E]
    if dx or dy:
        cache: Dict[tuple, Optional[dict]] = {}

        def link_info(po):
            try:
                ref = reference_from_object(po)
            except Exception:  # noqa: BLE001
                _note_exc("template_place.link_info")
                ref = None
            if ref is None or resolver is None:
                return None
            key = (ref.library.lower(), ref.element.lower(), ref.ext)
            if key not in cache:
                cache[key] = P.element_info(resolver, ref.library, ref.element, ref.ext)
            return cache[key]

        P.translate_subtrees(copies, dx, dy, link_info)
    out = []
    eparents = P.forest_parents(E)
    for k, (c, p) in enumerate(zip(copies, prefix)):
        rc, rp = GP.decode(c), GP.decode(p)
        el_dw = E[k].subtype_size
        rc.handle = rp.handle
        # the page copy's geometry, a DummyMarker's too (LIVE)
        rc.anchor, rc.rect1, rc.rect2 = rp.anchor, rp.rect1, rp.rect2
        if rc.entries is not None and rp.entries is not None and len(rc.entries) == len(rp.entries):
            if _in_linked_genie(E, eparents, k):
                # a member of a genie the copy still links: CtDraw re-composes
                # it on load, in the element record's key order (LIVE)
                rc.entries = [
                    [_in_order(bp, bc) for bp, bc in zip(ep, ec)] for ep, ec in zip(rp.entries, rc.entries)
                ]
            else:
                rc.entries = rp.entries
        ml = GP.metadata_len(rc.cls) if (el_dw & 0x100 and len(rc.cls) >= 2) else 0
        if rc.tc == TEXT_TC and len(rc.cls) - ml >= 4:
            # the page copy's (substituted) string section, then the element's
            # font tail
            sl = GP.text_strings_len(rc.cls[ml:])
            rc.cls = rp.cls + rc.cls[ml + sl :]
        elif ml and len(rp.cls) >= 2:
            rc.cls = rp.cls[: GP.metadata_len(rp.cls)] + rc.cls[ml:]
        elif rc.tc == DUMMY_TC and len(rc.cls) >= 12:
            # a marker's class part is its box (rect + point, 6.18): the page copy's, as
            # its geometry (LIVE)
            rc.cls = struct.pack("<6h", *rp.rect1, *rp.anchor) + rc.cls[12:]
        if rc.dw & 0x80 and rp.s80 is not None:
            rc.s80 = rp.s80
        if k == 0:
            rc.dw &= ~(0x1 | 0x4)
            rc.link = None
            rc.an = "-1"
        out.append(GP.to_po(GP.encode(rc)))
    return out


def _in_linked_genie(E, parents, k: int) -> bool:
    """Element record ``k`` sits below a nested linked genie instance (an
    ancestor other than the template root with LINK + genie, dw 0x201)."""
    j = parents[k]
    while j is not None and parents[j] is not None:
        if E[j].subtype_size & 0x201 == 0x201:
            return True
        j = parents[j]
    return False


def _in_order(page_bag: "GP.Bag", el_bag: "GP.Bag") -> "GP.Bag":
    """The page bag's pairs in the element bag's key order (keys the element
    bag lacks follow in the page's order), keeping the page bag's size word."""
    rank = {k.lower(): i for i, (k, _v) in enumerate(el_bag.pairs)}
    pairs = sorted(page_bag.pairs, key=lambda kv: rank.get(kv[0].lower(), len(rank)))
    return GP.Bag(pairs, page_bag.ov)


def _cut_levels(
    copy_: List[PageObject],
    prefix: Sequence[PageObject],
    resolver,
    outside: set,
    page_name: str = "",
    composed: Optional[Dict[Tuple[str, str], bool]] = None,
    skip=frozenset(),
) -> List[PageObject]:
    """The bags of a template copy after Cut Link."""
    out = list(copy_)
    if not out:
        return out
    parents = P.forest_parents(out)
    r0 = GP.decode(out[0])
    tpl_level = bool(r0.entries) and len(r0.entries) >= 2

    def is_root(j):
        return bool(out[j].class_flag and out[j].subtype_size & 0x200)

    def between(j):
        n, a = 0, parents[j]
        while a is not None and parents[a] is not None:
            n += is_root(a)
            a = parents[a]
        return n

    redo = []
    tops = []  # (composed=) the template's own genies (linked roots)
    for j in range(1, len(out)):
        po = out[j]
        if not po.subtype_size & 0x8 or j in skip:
            continue  # (``skip``: an inner template's copy, as it is)
        r = GP.decode(po)
        if not r.entries:
            continue
        root, n = is_root(j), between(j)
        ents = [[bg.copy() for bg in lv] for lv in r.entries]
        L = 1 + n + (1 if root else 0)
        outer = None
        if tpl_level and len(ents) > L:
            outer = ents.pop(L)
            if L == 1 and len(ents) == 1:
                ents[0][2] = GP.Bag([], ents[0][2].ov)  # a plain object: values only
        if root and n == 0:
            if composed is not None and po.subtype_size & 0x1:
                tops.append(j)
            r.an = prefix[j].an_str
            ents[0][2] = GP.Bag([], ents[0][2].ov)
            if len(ents) >= 2:
                lvl = ents[1]
                ov, od = (outer[1], outer[0]) if outer else (GP.Bag([]), GP.Bag([]))
                vals = GP.Bag([], lvl[1].ov)
                for key, t in lvl[2].pairs:
                    v = GP.substitute(t, ov, od, key)
                    if v:
                        vals.pairs.append((key, v))
                if {k.lower(): v for k, v in vals.pairs} != {k.lower(): v for k, v in lvl[1].pairs}:
                    lvl[1] = vals
                    redo.append(j)
                lvl[2] = GP.Bag([], lvl[2].ov)
        r.entries = ents
        out[j] = GP.to_po(GP.encode(r))
    done = set()
    keys = {}
    for j in tops:
        try:
            ref = reference_from_object(out[j])
        except Exception:  # noqa: BLE001  # swallow-ok: an unreadable link names nothing
            ref = None
        if ref is not None:
            keys[j] = (ref.library.lower(), ref.element.lower())
    for j in reversed(redo):
        end = GP.subtree_end(out, j)
        inst = out[j:end]
        others = {G.get_handle(o) for i, o in enumerate(out) if not j <= i < end}
        new = GP.refresh_instance(inst, resolver, page_name, outside_handles=(outside | others) - {None})
        if not new:
            continue
        new = list(new)
        r, rn = GP.decode(inst[0]), GP.decode(new[0])
        if (rn.name, rn.an) != (r.name, r.an):
            # the instance's object name is the placement's
            rn.name, rn.an = r.name, r.an
            new[0] = GP.to_po(GP.encode(rn))
        out[j:end] = new
        done.add(j)
    if composed is not None:
        # a genie the template placed is its current version only where every copy of it
        # was composed again here (the others are the template's stored copies)
        for j, key in keys.items():
            composed[key] = composed.get(key, True) and j in done
    return out


def _inner_copies(copy_: List[PageObject], prefix: Sequence[PageObject]):
    """Keep the inner templates' copies when cutting an outer template."""
    out = list(copy_)
    keep = set()
    tpl_level = bool(prefix) and len(GP.decode(prefix[0]).entries or ()) >= 2
    for a, b, _ref in nested_template_spans(out):
        for k in range(a, b):
            if k >= len(prefix):
                continue
            r = GP.decode(prefix[k])
            if tpl_level and r.entries is not None and len(r.entries) >= 2:
                r.entries = [[bg.copy() for bg in lv] for lv in r.entries[:-1]]
            if k == a:
                rc = GP.decode(out[k])
                r.dw = (r.dw & ~0x2) | 0x1 | 0x4
                r.link = rc.link
            out[k] = GP.to_po(GP.encode(r))
            keep.add(k)
    return out, frozenset(keep)


def _without_outer_root(copy_: List[PageObject]) -> List[PageObject]:
    """Drop an outer template root that holds nothing but the inner template's copy."""
    if (
        not copy_
        or copy_[0].class_flag != 1
        or len(copy_) < 2
        or subtree_end(copy_, 1) != len(copy_)
        or not is_template_copy_root(copy_[1])
    ):
        return copy_
    return copy_[1:]


def cut_link(
    objs: Sequence[PageObject], link_raw: bytes, link_count: int, area: str, resolver, page_name: str = ""
) -> dict:
    """Edit > Cut Link on the page's template."""
    objs = list(objs)
    roots = template_roots(objs)
    if not roots:
        raise ValueError("this page is not linked to a template")
    old_link = GP.decode(objs[roots[0][0]]).link or roots[0][2].raw
    added: List[PageObject] = []
    count = 0
    carried: Dict[str, Tuple[bytes, str]] = {}
    composed: Dict[Tuple[str, str], bool] = {}
    for a, b, ref in reversed(roots):
        tpl = resolve_template(resolver, ref)
        if tpl is None:
            raise ValueError(
                "the template %s is not on this project's include path, so "
                "its objects cannot be copied into the page" % ref.raw
            )
        # the copies are the template's stored records: what they nest is at the version
        # the template's own import table quotes (the older one of two templates')
        for g, n in template_entries(tpl):
            k = links.strip_stamp(n).lower()
            have = carried.get(k)
            if have is None or GP.carried_entry_wins(have, (g, n)):
                carried[k] = (g, n)
        copy_ = unlinked_copy(objs[a:b], tpl, resolver)
        copy_, inner = _inner_copies(copy_, objs[a:b])
        if resolver is not None:
            outside = {G.get_handle(o) for j, o in enumerate(objs) if not a <= j < b}
            copy_ = _cut_levels(copy_, objs[a:b], resolver, outside, page_name, composed=composed, skip=inner)
        copy_ = _without_outer_root(copy_)
        objs[a:b] = copy_
        added = copy_ + added
        count += len(copy_)
    t = links.parse_link_table(link_raw) if link_count else None
    area0, ents = t if t is not None else (area or "    ", [])
    # (a genie the cut composed again in every copy is at its current version)
    new = (
        GP.page_link_entries(
            added,
            resolver,
            carried=list(carried.values()),
            fresh=frozenset(k for k, v in composed.items() if v),
        )
        if resolver is not None
        else []
    )
    relisted = {n for _g, n in new if n.startswith("\\")}
    held = {n for _g, n in GP.label_entries(objs)}
    keep, pos, block = [], None, False
    for g, n in ents:
        # a page's only .ctt references are its template placements
        if not n.startswith("\\") and links.strip_stamp(n).lower().endswith(".<ctt"):
            if pos is None:
                pos = len(keep)
            block = True
            continue
        if block and n.startswith("\\"):
            # the template's label block: CtDraw lists each label after the
            # element that now owns it, and a label no text holds any more goes (LIVE)
            if n in relisted or n not in held:
                continue
        else:
            block = False
        keep.append((g, n))
    # (an element the page lists already, placed on it at its current version, keeps the
    # page out of date when the cut copies hold an older one)
    add_entries(
        keep,
        new,
        pos if pos is not None else 0,
        current=GP.current_entry_lookup(resolver) if resolver is not None else None,
    )
    raw, cnt = links.serialize_link_table(area0, keep)
    return dict(objects=objs, link_raw=raw, link_count=cnt, link=old_link, records=count)


# the process caches of this module (infra/caches.py: when each is emptied)
from ..infra import caches as _caches  # noqa: E402

_caches.register("template_place.templates", _TPL_CACHE, on=("libraries", "refresh", "tests"))
