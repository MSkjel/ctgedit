"""Reader: pictures, metafiles, symbol and genie expansion, template alignment."""

from __future__ import annotations

import os
import re
import struct  # noqa: F401 (re-exported)
from ...format import geometry as _geom
from ...format.page import _XFORM_DOUBLES, _extract_bmp, _geom_name_len, _parse_object, parse_ctg  # noqa: F401 (re-exported)
from ...model.document import (
    GraphicObject,
    Geometry,  # noqa: F401 (re-exported)
    ObjectKind,
)
from ...format.textcodec import CODEC  # noqa: F401 (re-exported)
import re as _re  # noqa: F401 (re-exported)
import struct as _struct  # noqa: F401 (re-exported)
import threading
from ...format import page as _pagemod  # noqa: F401 (re-exported)
from ...format import legacy as _legacy  # noqa: F401 (re-exported)
from ...format import placement as _pl  # noqa: F401 (re-exported)
from ...format.bitmaps import metafile_bits, stored_size as _stored_size
from ...format.symbols import legacy_first_symbol as _legacy_first_symbol  # noqa: F401 (re-exported)
from ...format.symbols import (
    SymbolReference,  # noqa: F401 (re-exported)
    SymbolResolver,
    embedded_bitmap_from_body,  # noqa: F401 (re-exported)
    embedded_bitmaps_from_body,
    open_library,
    reference_from_object,
    render_metafile,
)
from .. import project as _project  # noqa: F401 (re-exported)
from ...infra.logsetup import note_exception as _note_exc
from ...format import codes as _codes
from ...format import forest as _forest  # noqa: F401 (re-exported)
from . import common as _pc, style as _ps, symbols as _py, graft as _pg  # noqa: F401


# --------------------------------------------------------------------------- symbols
# Kinds that PLACE a library element and expand into real children.
_PLACEABLE_KINDS = frozenset(
    {
        ObjectKind.SYMBOL,
        ObjectKind.SYMBOL_SET,
        ObjectKind.GENIE,
        ObjectKind.COMPOSITE_GENIE,
        ObjectKind.GROUP,
    }
)


# Guard against self-referential libraries (a symbol that places itself, directly or via a
# cycle). Elements nest a few levels at most.
_MAX_SYMBOL_DEPTH = 6


# tc=7 "Bitmap": either a library REFERENCE (resolved into children) or an inline PICTURE
# (zlib-compressed DIB in its body).
_BITMAP_TYPE_CODE = 0x07


def _attach_embedded_bitmap(obj: GraphicObject, po, geom_end) -> None:
    """Decode a bitmap object's inline DIB for the renderer."""
    eb, flash = embedded_bitmaps_from_body(po.body_raw, geom_end or 0)
    if eb is None:
        return
    obj.raw["_embedded_bmp"] = (eb.width, eb.height, eb.rgba, eb.has_alpha)
    # the bytes its own picture takes in the page (the Objects panel shows them)
    try:
        obj.raw["_pic_bytes"] = _stored_size(po)
    except Exception:  # noqa: BLE001
        _note_exc("reader._attach_embedded_bitmap")
    if flash is not None:  # two-DIB FLASHING bitmap (alternates at runtime)
        obj.raw["_embedded_bmp_flash"] = (flash.width, flash.height, flash.rgba, flash.has_alpha)


def _attach_metafile(obj: GraphicObject, po, geom_end) -> None:
    """Render a metafile record's picture for the renderer."""
    try:
        wmf = metafile_bits(po)  # CtDraw's position: class section + 8
        tw = obj.geometry.extent_x if obj.geometry else 0
        th = obj.geometry.extent_y if obj.geometry else 0
        eb = render_metafile(po.body_raw, geom_end or 0, tw, th, wmf=wmf)
    except Exception:  # noqa: BLE001
        _note_exc("reader._attach_metafile")
        eb = wmf = None
    if eb is None:
        return
    obj.raw["_embedded_bmp"] = (eb.width, eb.height, eb.rgba, eb.has_alpha)
    obj.raw["_wmf"] = wmf
    obj.raw["_pic_bytes"] = len(wmf or b"")
    obj.raw["_metafile"] = True  # never rotated / aspect-kept (CtDraw: no transform)
    obj.kind = ObjectKind.SYMBOL  # a placed picture -> drawable like a bitmap
    obj.raw.pop("_non_drawable", None)


def _build_resolver(page_path: str):
    """Build a SymbolResolver for ``page_path``."""
    if os.environ.get("CTG_NO_SYMBOLS") == "1":
        return None
    extra = [p for p in os.environ.get("CTG_LIB_PATH", "").split(os.pathsep) if p]
    try:
        return SymbolResolver.for_page(page_path, extra_paths=extra)
    except Exception:  # noqa: BLE001
        _note_exc("reader._build_resolver")
        return None


# multi-state symbol container ("SymbolSet"): its tc=7 children are mutually exclusive STATES,
# CtDraw draws only the active one (in the runtime object).
_SYMBOL_SET_TC = 0x0C


# library ref extensions a genie container may expand from (.ctt templates define page furniture
# and are NOT expanded into the placing object's box).
_EXPANDABLE_EXTS = ("ctm", "ctl")


def _expand_symbol(obj: GraphicObject, po, resolver, depth: int) -> None:
    """Resolve a placed symbol and fill ``obj.children``."""
    if resolver is None or depth >= _MAX_SYMBOL_DEPTH:
        return
    try:
        ref = reference_from_object(po)
        if ref is None:
            return
        resolved = resolver.resolve(ref)
    except Exception:  # noqa: BLE001
        _note_exc("reader._expand_symbol")
        return
    if resolved is None:
        return
    _expand_element(obj, resolved, resolver, depth)
    if obj.children:
        obj.raw["_resolved_ref"] = str(resolved.reference)
        obj.raw["_resolved_lib"] = os.path.basename(resolved.library_path)


def _expand_element(obj: GraphicObject, resolved, resolver, depth: int) -> None:
    """Attach a library element's objects as ``obj.children``."""
    # the reader's object builder (it calls back into this part: a call-time import, so the
    # parts import no reader at load)
    from ..reader import _classify_container, _object_from

    children: list[GraphicObject] = []
    pobjs = resolved.objects
    for child_po in pobjs:
        try:
            child = _object_from(child_po, resolver, depth + 1)
        except Exception:  # noqa: BLE001 -- keep indices aligned with pobjs
            _note_exc("reader._expand_element/element record")
            child = GraphicObject(kind=ObjectKind.UNKNOWN)
            child.raw["_non_drawable"] = True
        children.append(child)
    if not children:
        return

    _wire_local_forest(children, pobjs)
    for i, child in enumerate(children):
        if child.members:
            try:
                _classify_container(child, pobjs[i])
            except Exception:  # noqa: BLE001
                _note_exc("reader._expand_element/classify")
                pass
    if depth + 1 < _MAX_SYMBOL_DEPTH:
        _pg._expand_nested_containers(children, pobjs, resolver, depth)
    _select_local_states(children, pobjs)
    # un-expanded nested containers keep their inline members: restore any art the cache stripped
    # (polygon/pipe vertices, symbol-slot DIBs/refs)
    n = min(len(children), len(pobjs))
    for i in range(n):
        if not children[i].members or children[i].children:
            continue
        if pobjs[i].type_code != _codes.TC.GROUP:
            continue
        sub = sorted(_local_subtree(children, i))
        pairs = [(children[j], pobjs[j]) for j in sub if j < n]
        if _repair_stripped_nodes(pobjs[i], pairs, resolver, depth + 1):
            # repaired state slots are mutually exclusive: draw one
            for j in sub:
                if j < n and pobjs[j].type_code == _SYMBOL_SET_TC and children[j].members:
                    _reselect_set_state(children[j], children, pobjs)

    emin = _pg._content_bbox_min(children)
    ew = resolved.width or 0
    eh = resolved.height or 0
    bx, by = obj.geometry.x, obj.geometry.y
    bw, bh = obj.geometry.extent_x, obj.geometry.extent_y
    # A 90/270 slot-1 rotation (off-diagonal A,B,C,D) makes rect1 the POST-rotation bbox, so the
    # placement box dimensions are SWAPPED relative to the element (a 32x15 valve placed vertical
    # has rect1 15x32). The scene applies that rotation about rect1's centre AFTER this scale, so:
    #   - scale the element against the box dims it maps to BEFORE rotation (swap bw/bh), and
    #   - place the element CENTRE onto rect1's centre (rotation about that centre is then
    #     position-invariant -- otherwise the rotation flings the art off the placement box).
    # Without a swapping rotation (identity / mirror-only / scale) emin maps onto the origin.
    lx = obj.raw.get("_local_xform")
    swap_rot = bool(lx) and (abs(lx[1]) > 1e-6 or abs(lx[2]) > 1e-6)
    if swap_rot:
        sx = (bh / ew) if (ew > 0 and bh > 0) else 1.0
        sy = (bw / eh) if (eh > 0 and bw > 0) else 1.0
        # map element content centre -> placement-rect centre (rotation pivot)
        ecx, ecy = emin[0] + ew / 2.0, emin[1] + eh / 2.0
        ox = (bx + bw / 2.0) - ecx * sx
        oy = (by + bh / 2.0) - ecy * sy
    else:
        sx = (bw / ew) if (ew > 0 and bw > 0) else 1.0
        sy = (bh / eh) if (eh > 0 and bh > 0) else 1.0
        # CtDraw draws a resolved element TRANSLATE-ONLY (SetWindowOrgEx to the element anchor):
        # it never ANISOTROPICALLY scales to rect1. When the placement box's aspect differs
        # from the element's, keep the element's aspect with a single uniform scale (geometric
        # mean, so the drawn area roughly matches a box-fit), CENTRED in the placement box.
        ls = obj.raw.get("_slot_scale")
        genuine = (
            bool(ls)
            and abs(ls[0] - ls[1]) > 1e-6
            and sy > 0
            and abs((sx / sy) - (ls[0] / ls[1])) <= 0.06 * (ls[0] / ls[1])
        )
        if genuine:
            # the aspect change IS the record's own Local scale: CtDraw draws the art through
            # it, stretched: keep the anisotropic box-fit
            ox = bx - emin[0] * sx
            oy = by - emin[1] * sy
        elif ew > 0 and eh > 0 and abs(sx - sy) > 0.12 * max(sx, sy, 1e-6):
            s = (sx * sy) ** 0.5
            sx = sy = s
            ox = bx + (bw - ew * s) / 2.0 - emin[0] * s
            oy = by + (bh - eh * s) / 2.0 - emin[1] * s
        else:
            # offset so element_min maps onto the placement origin after scaling
            ox = bx - emin[0] * sx
            oy = by - emin[1] * sy
    for child in children:
        child.raw["_symbol_xform"] = (ox, oy, sx, sy)
    if any(not c.raw.get("_non_drawable") for c in children):
        obj.children = children
        obj.raw["_symbol_xform"] = (ox, oy, sx, sy)


def _wire_local_forest(children: list, pobjs) -> None:
    """Wire parent/members over a DETACHED element-object list (local indices).

    Same depth-first child-count walk as :func:`_build_object_forest`, but indices are local to
    ``children`` (these objects never join a Page)."""
    n = min(len(children), len(pobjs))
    counts = [max(0, int(getattr(po, "class_flag", 0) or 0)) for po in pobjs[:n]]
    stack: list[list[int]] = []
    for i in range(n):
        parent = stack[-1][0] if stack else None
        children[i].parent = parent
        children[i].container_count = counts[i]
        if parent is not None:
            children[parent].members.append(i)
            stack[-1][1] -= 1
        if counts[i] > 0:
            stack.append([i, counts[i]])
        else:
            while stack and stack[-1][1] <= 0:
                stack.pop()


def _local_subtree(children: list, idx: int) -> list[int]:
    """All descendant indices of ``children[idx]`` (exclusive), via members."""
    out: list[int] = []
    todo = list(children[idx].members)
    while todo:
        j = todo.pop()
        out.append(j)
        todo.extend(children[j].members)
    return out


def _member_has_art(o: GraphicObject) -> bool:
    """Does this member draw real artwork in a static render?"""
    if o.raw.get("_non_drawable") or o.members:
        return False
    if o.kind in (ObjectKind.SYMBOL, ObjectKind.SYMBOL_SET):
        return bool(o.children) or o.raw.get("_embedded_bmp") is not None
    if o.kind is ObjectKind.UNKNOWN:
        return bool(o.children)
    return True


def _state_members(state: GraphicObject, objs) -> list:
    """The records below a symbol-set state (a group-root symbol's instance forest: its
    root's members, at any depth), via ``members`` indices into ``objs``."""
    if objs is None:
        return []
    out, todo = [], list(state.members)
    while todo:
        j = todo.pop()
        if 0 <= j < len(objs):
            out.append(objs[j])
            todo.extend(objs[j].members)
    return out


def _state_has_art(state: GraphicObject, objs) -> bool:
    if state.members and objs is not None:
        return any(_member_has_art(o) for o in _state_members(state, objs))
    return _member_has_art(state)


#: the type codes of a symbol-set state the editor draws one of: a linked / embedded symbol
#: record and a group-root symbol's instance forest (its 0x08 root)
SET_STATE_TCS = (0x07, 0x08)


def _select_set_state(states: list[GraphicObject], objs=None) -> None:
    """Pick the one state a symbol set draws."""
    if len(states) <= 1:
        return
    keep = next((s for s in states if _state_has_art(s, objs)), states[0])
    for s in states:
        if s is not keep:
            s.raw["_state_hidden"] = True
            for o in _state_members(s, objs):
                o.raw["_state_hidden"] = True


def _reselect_set_state(set_obj: GraphicObject, objs: list, pobjs) -> None:
    """Pick a set's drawn state again after a repair."""
    if set_obj.raw.get("_state_hidden"):
        return
    n = min(len(objs), len(pobjs))
    states = [objs[m] for m in set_obj.members if m < n and pobjs[m].type_code in SET_STATE_TCS]
    if len(states) <= 1:
        return
    keep = next((s for s in states if _state_has_art(s, objs)), states[0])
    if keep.raw.pop("_state_hidden", None):
        todo = [] if keep.children else list(keep.members)
        while todo:
            j = todo.pop()
            if 0 <= j < len(objs):
                objs[j].raw.pop("_state_hidden", None)
                if not objs[j].children:
                    todo.extend(objs[j].members)
    _select_set_state(states, objs)


def _select_local_states(children: list, pobjs) -> None:
    """Apply symbol-set state selection over a detached element stream."""
    n = min(len(children), len(pobjs))
    for i in range(n):
        if pobjs[i].type_code != _SYMBOL_SET_TC or not children[i].members:
            continue
        states = [children[m] for m in children[i].members if m < n and pobjs[m].type_code in SET_STATE_TCS]
        _select_set_state(states, children)


# type codes whose shape is a counted vertex array (polygon variants + pipe).
_NODE_TYPE_CODES = frozenset({0x0A, 0x0B, 0x10})


def _template_alignment(pobjs, resolver):
    """Page record index -> the template record at the same index."""
    return _template_alignment_span(pobjs, resolver)[0]


def _template_alignment_span(pobjs, resolver):
    """(:func:`_template_alignment`, how many LEADING records it read -- the result depends on
    nothing after them; None when it scanned every record and found no template)."""
    tpl = None
    base = 0
    read = None
    for i, po in enumerate(pobjs):
        if po.type_code != 0x08:
            continue
        try:
            ref = reference_from_object(po)
        except Exception:  # noqa: BLE001
            _note_exc("reader._template_alignment_span/reference")
            continue
        if ref is not None and ref.ext == "ctt":
            read = i + 1
            try:
                resolved = resolver.resolve(ref)
            except Exception:  # noqa: BLE001
                _note_exc("reader._template_alignment_span/resolve")
                resolved = None
            if resolved is not None:
                tpl = resolved.objects
                # the page inlines the template's record stream starting at the
                # template-genie record, NOT necessarily page index 0.
                base = i
            break
    if not tpl:
        return {}, read
    out = {}
    n = min(len(pobjs) - base, len(tpl))
    for k in range(n):
        a, b = pobjs[base + k], tpl[k]
        if a.type_code != b.type_code or a.class_flag != b.class_flag:
            continue
        ga = _pc._read_geometry(a.body_raw, 0)
        gb = _pc._read_geometry(b.body_raw, 0)
        if ga is None or gb is None or ga["rect1"] != gb["rect1"]:
            continue
        out[base + k] = b
    return out, base + n


def _template_alignment_safe(pobjs, resolver) -> dict:
    """:func:`_template_alignment`, {} when it fails (never fail a page load)."""
    return _template_alignment_read(pobjs, resolver)[0]


def _template_alignment_read(pobjs, resolver, previous=None):
    """(:func:`_template_alignment_safe`, the leading records it read). ``previous`` = an earlier
    derive of the same page: when those leading records are the same parsed records as then,
    its result is reused."""
    if resolver is None:
        return {}, 0
    if (
        previous is not None
        and previous.resolver is resolver
        and previous.tpl_read is not None
        and previous.tpl_read <= len(pobjs)
    ):
        old = previous.shadow
        # (an alignment that ran to the page's end reads a longer page further)
        if (previous.tpl_read < len(old) or len(pobjs) == len(old)) and all(
            pobjs[j] is old[j] for j in range(previous.tpl_read)
        ):
            return previous.tpl_map, previous.tpl_read
    try:
        return _template_alignment_span(pobjs, resolver)
    except Exception:  # noqa: BLE001
        _note_exc("reader._template_alignment_read")
        return {}, None


def _repair_stripped_nodes(
    container_po, member_pairs, resolver, depth: int = 1, ref=None, twins=None
) -> bool:
    """Restore the art a genie's copy strips."""
    stripped_nodes = [
        (o, po) for o, po in member_pairs if po.type_code in _NODE_TYPE_CODES and not o.geometry.nodes
    ]
    stripped_syms = [
        (o, po)
        for o, po in member_pairs
        if po.type_code == 0x07 and not o.children and o.raw.get("_embedded_bmp") is None
    ]
    if not stripped_nodes and not stripped_syms:
        return False
    try:
        if ref is None:
            ref = reference_from_object(container_po)
        if ref is None or ref.ext not in _EXPANDABLE_EXTS:
            return False
        resolved = resolver.resolve(ref)
    except Exception:  # noqa: BLE001
        _note_exc("reader._repair_stripped_nodes/resolve")
        return False
    if resolved is None:
        return False
    _tws: list = []

    def owner_twins():
        """``twins`` (a list, or a callable giving one -- resolved only when needed)."""
        if not _tws:
            try:
                _tws.append(twins() if callable(twins) else twins)
            except Exception:  # noqa: BLE001
                _note_exc("reader._repair_stripped_nodes/owner twins")
                _tws.append(None)
        t = _tws[0]
        return t if t is not None and len(t) == len(member_pairs) else None

    # ---- vertex arrays (polygon / pipe) ----------------------------------
    if stripped_nodes:
        lib_nodes = []
        for lpo in resolved.objects:
            if lpo.type_code in _NODE_TYPE_CODES:
                try:
                    lib_nodes.append(_geom.get_object_nodes(lpo))
                except Exception:  # noqa: BLE001
                    _note_exc("reader._repair_stripped_nodes/library nodes")
                    lib_nodes.append(None)
        inline = [(o, po) for o, po in member_pairs if po.type_code in _NODE_TYPE_CODES]
        if len(inline) == len(lib_nodes):  # else structure mismatch
            for (o, _po), nodes in zip(inline, lib_nodes):
                if not nodes or o.geometry.nodes:
                    continue
                xs = [p[0] for p in nodes]
                ys = [p[1] for p in nodes]
                lw = max(xs) - min(xs)
                lh = max(ys) - min(ys)
                g = o.geometry
                sx = (g.extent_x / lw) if (lw and g.extent_x) else 1.0
                sy = (g.extent_y / lh) if (lh and g.extent_y) else 1.0
                g.nodes = [(g.x + (p[0] - min(xs)) * sx, g.y + (p[1] - min(ys)) * sy) for p in nodes]
        elif twins is not None and owner_twins() is not None:
            for (o, po), tw in zip(member_pairs, owner_twins()):
                if (
                    po.type_code not in _NODE_TYPE_CODES
                    or o.geometry.nodes
                    or tw is None
                    or tw is po
                    or tw.type_code != po.type_code
                ):
                    continue
                try:
                    nodes = _geom.get_object_nodes(tw)
                except Exception:  # noqa: BLE001
                    _note_exc("reader._repair_stripped_nodes/twin nodes")
                    nodes = None
                if nodes and len(nodes) >= 2:
                    o.geometry.nodes = _pg._member_nodes(po, o.geometry, nodes)

    # ---- symbol slots (embedded DIB / nested library ref) -----------------
    grafted = False
    if stripped_syms and depth < _MAX_SYMBOL_DEPTH:
        lib_syms = [lpo for lpo in resolved.objects if lpo.type_code == 0x07]
        inline_syms = [(o, po) for o, po in member_pairs if po.type_code == 0x07]
        pairs = None
        if len(inline_syms) == len(lib_syms):
            pairs = list(zip(inline_syms, lib_syms))
        elif twins is not None and owner_twins() is not None:
            pairs = [
                ((o, po), tw)
                for (o, po), tw in zip(member_pairs, owner_twins())
                if po.type_code == 0x07 and tw is not None and tw is not po and tw.type_code == 0x07
            ]
        if pairs is not None:
            for (o, _po), lpo in pairs:
                if o.children or o.raw.get("_embedded_bmp") is not None:
                    continue
                try:
                    got = _pg._graft_dib(o, lpo)
                except Exception:  # noqa: BLE001
                    _note_exc("reader._repair_stripped_nodes/twin bitmap")
                    got = False
                if got:
                    grafted = True
                    continue
                try:
                    ref2 = reference_from_object(lpo)
                    rs2 = resolver.resolve(ref2) if ref2 is not None else None
                except Exception:  # noqa: BLE001
                    _note_exc("reader._repair_stripped_nodes/nested resolve")
                    rs2 = None
                if rs2 is not None:
                    _expand_element(o, rs2, resolver, depth)
                    grafted = grafted or bool(o.children)
    return grafted


_GRAFT_TYPE_CODES = frozenset({0x0F, 0x11, 0x10, 0x0A})


#: (library file version, element, resolver search path) -> the element's art twins (None: the
#: element does not exist). FINAL results only, each a pure function of its key: a computation
#: in progress is never visible in it (another thread asking meanwhile computes its own copy --
#: the first one stored is kept and shared), and a result that depended on where it was computed
#: (an element met again inside its own nesting, the nesting depth limit) is returned but never
#: stored.
_ART_TWINS_CACHE: dict = {}


_ART_TWINS_LOCK = threading.Lock()


_ART_TWINS_TLS = threading.local()  # .stack: [key, stable] of this thread's computations


_ART_TWINS_MAX_DEPTH = 8


_MISSING = object()


def _element_root_records(path: str, element: str):
    """The element's ONE root subtree (the trailer records dropped), from the process-cached
    library (no re-composition: only the appearance-owning records are wanted)."""
    pb = open_library(path).element(element)
    if pb is None:
        return None
    recs = [o for o in pb.objects if o.raw_payload is None]
    end, pending = 0, 1
    while pending and end < len(recs):
        pending -= 1
        pending += recs[end].class_flag
        end += 1
    return recs[:end]


def _art_twins_light(path: str, element: str, resolver, depth: int = 0):
    """art_twins without nested recomposition."""
    return _art_twins_entry(path, element, resolver)[0]


def _art_twins_entry(path: str, element: str, resolver):
    """(twins, height): ``height`` = the levels of nesting the twins were followed through
    (1: the element nests no linked element that was found)."""
    # (inside format/symbols.held_libraries: the version the hold read)
    from ...format.symbols import library_version

    try:
        key = (
            os.path.normcase(os.path.abspath(path)),
            element.lower(),
            library_version(path),
            tuple(getattr(resolver, "search_paths", None) or ()),
        )
    except OSError:
        return None, 0
    stack = getattr(_ART_TWINS_TLS, "stack", None)
    if stack is None:
        stack = _ART_TWINS_TLS.stack = []
    hit = _ART_TWINS_CACHE.get(key, _MISSING)
    # a stored result was followed to its full depth: at this depth a computation would stop
    # following the nesting (the depth limit) when it is too deep -- then compute it instead
    if hit is not _MISSING and len(stack) + 1 + hit[1] <= _ART_TWINS_MAX_DEPTH + 2:
        return hit
    for p, frame in enumerate(stack):
        if frame[0] == key:
            # the element nests itself (further down): no twins here -- and that computation
            # and every one inside it depend on where the cycle was entered (entered at another
            # of its elements, this one would be the cut): none of them is stored
            for f in stack[p:]:
                f[1] = False
            return None, 0
    frame = [key, True]
    stack.append(frame)
    try:
        got = _art_twins_compute(path, element, resolver, stack)
    finally:
        stack.pop()
    if not frame[1]:
        return got
    with _ART_TWINS_LOCK:
        return _ART_TWINS_CACHE.setdefault(key, got)


def _art_twins_compute(path: str, element: str, resolver, stack: list):
    """The body of :func:`_art_twins_entry` (``stack`` = this thread's computations, this one
    last): (twins, height)."""
    recs = _element_root_records(path, element)
    if recs is None:
        return None, 0
    out = list(recs)
    height = 1
    if len(stack) > _ART_TWINS_MAX_DEPTH:
        # nested too deep: the nested elements are not followed, so this result -- and every
        # one this thread is computing around it -- depends on the depth it was asked at
        if any(po.class_flag and (po.subtype_size & 0x1) for po in recs[1:]):
            for f in stack:
                f[1] = False
    else:
        k = 1
        while k < len(recs):
            po = recs[k]
            # a nested LINKED container -- a genie instance (0x200) or a linked group-root
            # symbol (a page's 'i' button = an info symbol inside the chrome template): its
            # members are art-stripped too, their appearance lives in the referenced element
            if po.class_flag and (po.subtype_size & 0x1):
                end, pending = k, 1
                while pending and end < len(recs):
                    pending -= 1
                    pending += recs[end].class_flag
                    end += 1
                try:
                    ref = reference_from_object(po)
                    res = resolver.resolve(ref) if ref is not None else None
                except Exception:  # noqa: BLE001
                    _note_exc("reader._art_twins_light")
                    res = None
                sub, h = (
                    _art_twins_entry(res.library_path, ref.element, resolver)
                    if res is not None
                    else (None, 0)
                )
                height = max(height, h + 1)
                if sub is not None and len(sub) == end - k:
                    for j in range(1, end - k):
                        out[k + j] = sub[j]
                k = end
                continue
            k += 1
    return out, height


#: a %name% / %name=default% token, leading '='s skipped (genie_place._token_name)
_TOKEN_RE = re.compile(r"%=*([^%\s=]+)(?:=([^%]*))?%")


def _substitute_tokens(text, props: dict, objs, j: int):
    """A twin's %TEMPLATE% caption as the INSTANCE shows it: each ``%name%`` / ``%name=def%``
    token takes the member's own substituted value (its bags carry ``name``), else the nearest
    ancestor genie's binding, else the declared default, else blank (a ``%Description%``
    parameter left empty draws nothing)."""
    if not text or "%" not in text:
        return text

    def val(m):
        name, default = m.group(1), m.group(2)
        v = props.get(name)
        a = objs[j].parent if 0 <= j < len(objs) else None
        while v is None and a is not None:
            subs = objs[a].substitutions or {}
            v = subs.get(name, subs.get("%" + name + "%"))
            a = objs[a].parent
        if v is None:
            v = default or ""
        return str(v)

    return _TOKEN_RE.sub(val, text)


def _art_twins_for(resolved, ref, resolver, n: int):
    """The art twins of the element ``ref`` resolved to (cached per library file version),
    or None when unavailable / not aligned (``n`` records)."""
    try:
        tw = _art_twins_light(resolved.library_path, ref.element, resolver)
    except Exception:  # noqa: BLE001
        _note_exc("reader._art_twins_for")
        tw = None
    return tw if tw is not None and len(tw) >= n else None


def template_records(pobjs, resolver) -> set:
    """The page records the template put there (:func:`_template_alignment_safe`), for the
    helper processes (picture_worker)."""
    return set(_template_alignment_safe(pobjs, resolver) or ())
