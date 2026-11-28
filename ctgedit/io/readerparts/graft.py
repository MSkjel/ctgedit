"""Reader: appearance taken from owner records, button faces and hotspots."""

from __future__ import annotations

import math
import re  # noqa: F401 (re-exported)
from ...format import geometry as _geom
from ...model.document import (
    ObjectKind,
    TextJustify,
)
from ...format.textcodec import CODEC
import re as _re  # noqa: F401 (re-exported)
import struct as _struct
from bisect import bisect_left, bisect_right
from collections import defaultdict
from ...format import colourstate as _cs
from ...format import legacy as _legacy
from ...format import placement as _pl
from ...format.ctf import own_caption
from ...format.symbols import (
    embedded_bitmaps_from_body,
    reference_from_object,
)
from ...infra.logsetup import note_exception as _note_exc
from ...format import codes as _codes
from . import common as _pc, style as _ps, symbols as _py, graft as _pg  # noqa: F401


class GraftCtx:
    """Shared state for the art grafts of one forest build."""

    def __init__(self, page, pobjs, resolver):
        self.page, self.pobjs, self.resolver = page, pobjs, resolver
        self._rule = None
        self._units: dict = {}
        self._memo: dict = {}

    def unit(self, r: int):
        """The owners of root unit ``r``'s records (aligned with its contiguous subtree), or
        None when they cannot be resolved."""
        if r in self._units:
            return self._units[r]
        own = None
        if self.resolver is not None and 0 <= r < len(self.pobjs):
            try:
                if self._rule is None:
                    from ...format.ctf import Owners

                    self._rule = Owners(self.resolver)
                n = len(self.page.subtree(r))
                own = self._rule.resolve(self.pobjs[r : r + n])
            except Exception:  # noqa: BLE001
                _note_exc("reader._GraftCtx.unit")
                own = None
        self._units[r] = own
        return own

    def twin(self, i: int):
        """The record owning record ``i``'s appearance; None when it is its own."""
        r = self.page.root_of(i)
        own = self.unit(r)
        if own is None or not 0 <= i - r < len(own):
            return None
        tw = own[i - r]
        return tw if tw is not None and tw is not self.pobjs[i] else None

    def owners(self, idx: int):
        """The owners of ``page.subtree(idx)`` (None: no owner), or None when the unit's owners
        cannot be resolved."""
        r = self.page.root_of(idx)
        own = self.unit(r)
        if own is None:
            return None
        out = []
        for j in self.page.subtree(idx):
            tw = own[j - r] if 0 <= j - r < len(own) else None
            out.append(tw if tw is not None and tw is not self.pobjs[j] else None)
        return out

    def _m(self, kind: str, po, fn):
        key = (kind, id(po))
        hit = self._memo.get(key)
        if hit is None or hit[0] is not po:
            try:
                v = fn(po)
            except Exception:  # noqa: BLE001
                _note_exc("reader._GraftCtx._m")
                v = None
            hit = self._memo[key] = (po, v)  # (po kept alive: its id stays unique)
        return hit[1]

    def cb(self, po):
        """The record's own style block (forward walk), None without one."""
        return self._m("cb", po, lambda p: _geom.find_colour_block(p) if p.subtype_size & 0x2 else None)

    def nodes(self, po):
        return self._m("nodes", po, _geom.get_object_nodes)

    def text_tail(self, po):
        return self._m("tail", po, _geom.parse_text_tail)

    def shape_tail(self, po):
        return self._m("shape", po, _geom.parse_shape_tail)

    def dibs(self, po):
        """(picture, flash picture) of the record's own DIB section: each (width, height,
        rgba, has_alpha) or None (a flashing bitmap has two)."""
        return self._m("dib", po, _dib_pictures) or (None, None)

    def dib(self, po):
        return self.dibs(po)[0]


def _dib_pictures(p):
    """(picture, flash picture) of a bitmap record's own DIB section, each (width, height,
    rgba, has_alpha) or None."""
    geo = _pc.read_geometry(p.body_raw, 0)
    eb, flash = embedded_bitmaps_from_body(p.body_raw, geo["end"] if geo else 0)

    def pic(b):
        return None if b is None else (b.width, b.height, b.rgba, b.has_alpha)

    return pic(eb), pic(flash)


def note_flash_slots(o, cb) -> None:
    """Keep the flashing colour pairs of the style block an object draws with."""
    if cb is None:
        return
    slots = tuple((int(a), int(b)) for a, b in cb.slots)
    if any(a != b for a, b in slots):
        o.raw["_flash_slots"] = slots
    else:
        o.raw.pop("_flash_slots", None)


def graft_dib(o, twin) -> bool:
    """Give an art-stripped member its owner's picture (raw '_embedded_bmp', and
    '_embedded_bmp_flash' when the owner is a FLASHING bitmap: the runtime alternates the two
    on the flash phase). False = the owner has no picture."""
    eb, flash = _dib_pictures(twin)
    if eb is None:
        return False
    o.raw["_embedded_bmp"] = eb
    if flash is not None:
        o.raw["_embedded_bmp_flash"] = flash
    o.raw["_bmp_grafted"] = True  # a twin's DIB (element frame)
    return True


def _twin_line_slot(mpo, cb):
    """The line slot an art-stripped member draws with."""
    if cb.flags[2]:
        return cb.slots[0]
    s0 = state0_pair(mpo)
    return s0 if s0 is not None else cb.slots[0]


def _map_slot_point(mode: int, M, x: float, y: float):
    """A point through a geometry-header slot the way CtDraw bakes one (by
    the FULL mode byte, placement.apply_rect's operation order, C truncation toward zero)."""
    A, B, C, D, H, K = M
    if mode == 0:
        px, py = x, y
    elif mode == 1:
        px, py = x + H, y + K
    elif mode == 2:
        px, py = A * x, D * y
    elif mode == 3:
        px, py = A * x + H, y * D + K
    else:
        px, py = y * C + x * A + H, x * B + y * D + K
    return float(int(px)), float(int(py))


def member_nodes(mpo, g, nodes):
    """An owner's vertices as the art-stripped member draws them."""
    try:
        mode, M = _pl.slot3(mpo)
    except Exception:  # noqa: BLE001
        _note_exc("reader.member_nodes")
        mode, M = 0, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    pts = [_map_slot_point(mode, M, float(x), float(y)) for x, y in nodes]
    l, t = float(g.x), float(g.y)
    r, b = l + float(g.extent_x or 0), t + float(g.extent_y or 0)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    if abs(min(xs) - l) > 2 or abs(max(xs) - r) > 2 or abs(min(ys) - t) > 2 or abs(max(ys) - b) > 2:
        ox, oy = [p[0] for p in nodes], [p[1] for p in nodes]
        lw, lh = max(ox) - min(ox), max(oy) - min(oy)
        sx = (r - l) / lw if lw else 1.0
        sy = (b - t) / lh if lh else 1.0
        pts = [(l + (x - min(ox)) * sx, t + (y - min(oy)) * sy) for x, y in nodes]
    return [(float(x), float(y)) for x, y in pts]


def own_text_caption(po):
    """The caption in a text record's own STRING SECTION ([u32 flag][lpstr TXT_OFF,
    else the caption]...) -- the caption CtDraw's load keeps for an art-stripped member
    (ctf.ctf_rec bakes it into the owner's appearance part); None when the record has none."""
    try:
        L = _cs.layout(po)
        if L is None:
            return None
        o = L["end"] + (54 if po.subtype_size & 0x2 else 0)
        cap = own_caption(po.body_raw[o:], po.subtype_size)
    except Exception:  # noqa: BLE001
        _note_exc("reader.own_text_caption")
        return None
    return cap.decode(CODEC) if cap is not None else None


def _txtmode(props) -> int:
    try:
        return int(str(props.get("TXTMODE") or "0").strip() or "0")
    except ValueError:
        return 0


def editor_display(po, caption):
    """The string CtDraw's editor draws for a text."""
    shown = _ps.literal_display(po, caption)
    if shown is None:
        return None
    # case 0 tests the caption's first byte (*caption == 0): only an EMPTY caption
    # falls back to TXT_ON -- a blank ' ' caption draws as itself
    if not caption and not shown.strip():
        props = po.raw_properties()
        if _txtmode(props) == 0:
            on = props.get("TXT_ON")
            if on:
                return str(on)
    return shown


def total_vscale(po) -> float:
    """The vertical scale of a record's Total transform (slot 3): what a text's point size
    is multiplied by where it is drawn (pt = MulDiv(px * sy, 72, LOGPIXELSY))."""
    try:
        slots, _end = _pl.read_slots_of(po)
        a, b, c, d = slots[2][1][:4]
    except Exception:  # noqa: BLE001 -- no transform: unscaled
        _note_exc("reader.total_vscale")
        return 1.0
    if not b and not c:
        return abs(d) or 1.0
    return abs(a * d - b * c) ** 0.5 or 1.0


def _scaled_text_size(size: int, mpo, twin) -> int:
    """An owner's font size as the member shows it: scaled by the member's Total relative to
    the owner's (a text in a genie placed at 2.46x is 2.46x the element's point size, as its
    stored box already is)."""
    if not size or twin is None or twin is mpo:
        return size
    ratio = total_vscale(mpo) / total_vscale(twin)
    if abs(ratio - 1.0) < 0.02:
        return size
    return max(1, int(size * ratio + 0.5))


def _member_matrix_scale(mpo, twin):
    """(sx, sy) a member text is drawn at when its twin keeps a scaled matrix."""
    if twin is None or twin is mpo or _geom.text_scale(twin) is None:
        return None
    try:
        a, b, c, d = _pl.read_slots_of(mpo)[0][2][1][:4]
    except Exception:  # noqa: BLE001
        _note_exc("reader._member_matrix_scale")
        return None
    sx, sy = math.hypot(a, b), math.hypot(c, d)
    if abs(sx - 1.0) < 1e-6 and abs(sy - 1.0) < 1e-6:
        return None
    return (sx, sy)


def text_needs_frame(po) -> bool:
    """Does CtDraw's editor draw a frame for this text?"""
    try:
        slots, _end = _pl.read_slots_of(po)
        a, b, c, d = slots[2][1][:4]
        gh = _geom.parse_geom_header(po.body_raw)
    except Exception:  # swallow-ok: a text without full transform slots (a short record)
        return False
    if gh is None:
        return False
    if a * d - b * c < 0:
        return True  # mirrored (bit 8)
    det = a * d - b * c
    if abs(det) < 1e-12:
        return False
    # bit 1: the box's corners through the matrix; 'uniform' when the box's
    # height / width ratio before and after differ by less than 0.05 (both
    # widths under 0.05 count as uniform too). The box before = rect1 through the inverse.
    l, t, r, bb = gh.rect1
    W, H = abs(r - l), abs(bb - t)
    ia, ib, ic, id_ = d / det, -b / det, -c / det, a / det  # inverse 2 x 2
    w0 = abs(ia * W) + abs(ic * H) if abs(b) + abs(c) > 1e-9 else abs(ia * W)
    h0 = abs(ib * W) + abs(id_ * H) if abs(b) + abs(c) > 1e-9 else abs(id_ * H)
    tw = ((a * w0) ** 2 + (b * w0) ** 2) ** 0.5  # |p1 - p0|
    th = ((c * h0) ** 2 + (d * h0) ** 2) ** 0.5  # |p3 - p0|
    if w0 < 0.05 or tw <= 0.05:
        return not (w0 < 0.05 and tw < 0.05)
    return abs(h0 / w0 - th / tw) >= 0.05


def graft_stripped_text(o, mpo, twin, ctx, objs, j) -> bool:
    """An art-stripped text's display."""
    st = o.style
    if st is None:
        return False
    if _pc.get_element_window():
        # an element window draws the binding chain as composed ('%Name% 3 L1'), which
        # style._element_window_props set from the record's own value bag: no twin caption,
        # no token resolution (CtDraw's element editor)
        return True
    mprops = mpo.raw_properties()
    tt = ctx.text_tail(twin) if (twin is not None and twin is not mpo and twin.subtype_size & 0x2) else None
    if tt is None and _txtmode(mprops) not in (1, 2):
        return False
    own = own_text_caption(mpo)
    if own:
        cap = own
    elif "TXT_OFF" in mprops:
        cap = str(mprops.get("TXT_OFF") or "")
    elif tt is not None and tt.get("text"):
        cap = _py.substitute_tokens(tt["text"], mprops, objs, j) or ""
    else:
        cap = ""
    # a token left in the caption is a parameter nothing binds (the stored copy kept
    # '%head%'): CtDraw's composition gives it its declared default, else blank
    for _ in range(2):
        if "%" not in cap or not _py.TOKEN_RE.search(cap):
            break
        cap = _py.substitute_tokens(cap, mprops, objs, j) or ""
    shown = editor_display(mpo, cap)
    if shown is not None:
        st.text = "" if not shown.strip() else _ps.norm_nl(shown).rstrip("\n")
    elif st.text and _py.TOKEN_RE.search(st.text):
        # the native / translated label modes (the Simulate window) keep the reader's caption,
        # which is the raw '%Line3%' template: show the substituted caption instead
        st.text = "" if not cap.strip() else _ps.clean_display_text(_ps.norm_nl(cap)).rstrip("\n")
    return True


def graft_twin_extras(o, mpo, twin, ctx) -> None:
    """The parts of an art-stripped member's look taken from its owner."""
    if (
        twin is not None
        and twin is not mpo
        and mpo.type_code == 0x1003
        and not (o.raw.get("_trend") or {}).get("pens")
    ):
        # an art-stripped TREND draws its owner's pens, not the default rainbow
        try:
            info = _legacy.trend_info(twin)
        except Exception:  # noqa: BLE001
            _note_exc("reader.graft_twin_extras/trend")
            info = None
        if info and info.get("pens"):
            o.raw["_trend"] = info
    if twin is None or twin is mpo or o.raw.get("_twin_grafted") or not (twin.subtype_size & 0x2):
        return
    o.raw["_twin_grafted"] = True
    tc = mpo.type_code
    cb = ctx.cb(twin)
    if cb is not None:
        o.raw["_twin_style"] = True
        if twin.subtype_size & 0x10:
            note_flash_slots(o, cb)
    g = o.geometry
    if g is not None and tc in (0x0A, 0x0B, 0x10, 0x01) and (tc == 0x01 or not g.nodes):
        nodes = ctx.nodes(twin)
        if nodes and len(nodes) >= 2 and (tc != 0x01 or len(nodes) == 2):
            g.nodes = member_nodes(mpo, g, nodes)
    st = o.style
    if cb is None or st is None:
        return
    if tc == 0x0B:
        st.line_width = max(1, cb.flags[4] or 1)
        hi, lo = _ps.design_colour(cb.slots[3]), _ps.design_colour(cb.slots[4])
        if hi is not None:
            st.hilight_colour = hi
        if lo is not None:
            st.lolight_colour = lo
    elif tc == 0x01:
        col = _ps.design_colour(_twin_line_slot(mpo, cb))
        st.line_colour = col
        st.line_width = max(1, cb.flags[4] or 1)
        st.line_style = (cb.flags[1] & 0x0F) if col is not None else 5
    elif tc == 0x05:
        col = _ps.design_colour(_twin_line_slot(mpo, cb))
        if col is not None:
            st.text_colour = col
        else:
            o.raw["_text_transparent"] = True


def state0_pair(po):
    """The state-0 colour pair of a record, or None."""
    L = _cs.layout(po)
    if L is None or "state0_at" not in L:
        return None
    try:
        return _struct.unpack_from("<II", po.body_raw, L["state0_at"])
    except _struct.error:
        return None


def _member_twins(pobjs, idx: int, sub, resolved, ref, resolver, ctx):
    """The record that owns each member's appearance (its twin) and the record whose
    reference it borrows: (twins, references, whether each member has an owner, whether
    the subtree aligns with the element's records); None when neither pairs them."""
    tobjs = resolved.objects
    # The record that OWNS each member's appearance = CtDraw's load linkage, whether or not the
    # instance's records line up with its element's: a linked record's is its element
    # tree and every instance record is paired with the element
    # record whose handle its AN string names, of the same class (CtDraw matches
    # atoi(an_str) and the class,
    # recursively into nested links -- never by position). ctf._Owners is that rule (the .ctF
    # writer's); a member whose owner is of another class (or has none) keeps its own. The
    # index-aligned pairing below is the fallback, per member, where no owner resolves (a
    # container whose page copy lost its link and is grafted with the aligned TEMPLATE
    # record's ``ref``).
    own = ctx.owners(idx)
    # An EXACT index-for-index type_code alignment over the whole subtree: then the element
    # record at position k is the twin of inline member sub[k] -- the record that OWNS its
    # appearance is that element record, or, for a member of a genie NESTED in the element
    # (itself art-stripped there), the aligned record of THAT genie's element
    # (genie_place.art_twins).
    aligned = len(sub) <= len(tobjs) and all(
        pobjs[j].type_code == tobjs[k].type_code for k, j in enumerate(sub)
    )
    if own is None and not aligned:
        return None
    owned = [
        own is not None and own[k] is not None and own[k].type_code == pobjs[j].type_code
        for k, j in enumerate(sub)
    ]
    atw = _py.art_twins_for(resolved, ref, resolver, len(sub)) if aligned and not all(owned) else None
    twl, refl = [], []
    for k, j in enumerate(sub):
        if owned[k]:
            twl.append(own[k])
            refl.append(own[k])
        elif aligned:
            twl.append(atw[k] if atw is not None else tobjs[k])
            refl.append(tobjs[k])
        else:
            twl.append(pobjs[j])  # no owner, no alignment: its own
            refl.append(pobjs[j])
    return twl, refl, owned, aligned


def _graft_text_font(objs, j: int, mpo, twin, ctx, genie_sizes: dict, genie_face: str) -> str:
    """An art-stripped TEXT member's caption and its twin's font (bold, size, face);
    the sizes grafted counted in ``genie_sizes``. The genie's face (``genie_face``, the
    first one grafted)."""
    st = objs[j].style
    if (
        not (mpo.subtype_size & 0x2)
        and not objs[j].raw.get("_twin_text")
        and graft_stripped_text(objs[j], mpo, twin, ctx, objs, j)
    ):
        objs[j].raw["_twin_text"] = True
    if (mpo.subtype_size & 0x2) or st.bold or st.font_size or st.font:
        return genie_face  # inline kept its own style block / font
    tpo = twin
    if not (tpo.subtype_size & 0x2):
        return genie_face
    cb, tt = ctx.cb(tpo), ctx.text_tail(tpo)
    if cb is None:
        return genie_face
    attribs = cb.flags[3]
    st.bold = bool(attribs & 0x10)
    st.italic = bool(attribs & 0x20)
    st.underline = bool(attribs & 0x40)
    st.strikeout = bool(attribs & 0x80)
    # justify = the attribs LOW nibble (0 L / 1 R / 2 C)
    st.justify = (
        TextJustify.RIGHT
        if (attribs & 0x0F) == 1
        else TextJustify.CENTRE
        if (attribs & 0x0F) == 2
        else TextJustify.LEFT
    )
    if tt is not None:
        if tt.get("size"):
            ms = _member_matrix_scale(mpo, tpo)
            if ms is not None:
                # the twin keeps a scale in its matrix (a mirrored text resized): the
                # member is drawn through its Total, the point size as the twin's
                objs[j].raw["_text_scale"] = ms
                st.font_size = tt["size"]
            else:
                st.font_size = _scaled_text_size(tt["size"], mpo, tpo)
            genie_sizes[st.font_size] = genie_sizes.get(st.font_size, 0) + 1
        if tt.get("face"):
            st.font = tt["face"]
            if not genie_face:
                genie_face = tt["face"]
    objs[j].raw["_template_font"] = True
    return genie_face


def _graft_shape_style(st, oj, mpo, twin, ctx) -> None:
    """An art-stripped closed shape's polygon flag, corner radius, ellipse / rectangle
    style, 3D effect and gradient from its twin."""
    # an art-stripped POLYGON's open/closed flag is its twin's style-block attribs bit 0 (the
    # block the member lacks). CtDraw's polygon draw FILLS an open polygon (NULL pen, when
    # fill_style != 0) and strokes only the polyline.
    if mpo.type_code in (0x10, 0x0A) and not (mpo.subtype_size & 0x2) and twin.subtype_size & 0x2:
        ocb = ctx.cb(twin)
        if ocb is not None:
            st.polygon_open = bool(ocb.flags[3] & 0x01)
    # graft the template's CORNER RADIUS (rounded rects): the inline copy lost its shape tail
    # (radius 0). This runs even when the inline kept a style block: only the rounding is
    # grafted, not the fill.
    if mpo.type_code == 0x0F and not getattr(st, "corner_radius", 0):
        tst = ctx.shape_tail(twin)
        if tst and tst.get("kind") == "rect" and 0 < (tst.get("radius") or 0) <= 32:
            st.corner_radius = tst["radius"]
            oj.raw["_template_radius"] = True
    # ...its ellipse style + pie / arc angles, rectangle style and 3D effect: the style
    # block's effects / attribs bytes and the ellipse tail the stripped copy lost
    if (
        mpo.type_code in (0x0F, 0x11)
        and not (mpo.subtype_size & 0x2)
        and twin.subtype_size & 0x2
        and not oj.raw.get("_twin_shape")
    ):
        scb = ctx.cb(twin)
        if scb is not None:
            oj.raw["_twin_shape"] = True
            f6 = scb.flags
            if not st.three_d:
                st.three_d = f6[0] & 0x0F
                st.three_d_depth = (f6[5] - 256) if f6[5] >= 0x80 else f6[5]
            if st.three_d and st.hilight_colour is None and st.lolight_colour is None:
                # (the bevel colours: the owner's hilight / lolight / shadow slots)
                st.hilight_colour = _ps.design_colour(scb.slots[3])
                st.lolight_colour = _ps.design_colour(scb.slots[4])
                st.shadow_colour = _ps.design_colour(scb.slots[5])
            if st.three_d and not st.style_flags:
                st.style_flags = bytes(f6)  # (the scene's effect depth reads them)
            if mpo.type_code == 0x0F:
                st.rectangle_style = (f6[3] >> 4) & 0x3
            else:
                st.ellipse_style = f6[3] & 0x0F if (f6[3] & 0x0F) <= 2 else 0
                est = ctx.shape_tail(twin)
                if est and est.get("kind") == "ellipse":
                    st.start_angle = float(est["start"])
                    st.end_angle = float(est["end"])
    # ...and its GRADIENT: the shape tail is part of the appearance the stripped copy lost
    if (
        mpo.type_code in (0x0F, 0x10, 0x11)
        and not (mpo.subtype_size & 0x2)
        and not st.gradient_mode
        and twin.subtype_size & 0x2
    ):
        gst = ctx.shape_tail(twin)
        if gst and 0 < gst["grad_mode"] <= 8:
            g = _ps.design_colour((gst["grad_on"], gst["grad_off"]))
            if g is not None:
                st.gradient_mode = gst["grad_mode"]
                st.gradient_colour = g


def _graft_member_line(st, oj, mpo, twin, ctx) -> None:
    """An art-stripped closed shape's line from its twin (none when the twin's line is
    transparent)."""
    # graft the template's LINE treatment onto an ART-STRIPPED member (no own style block) that
    # the decode left with NO line. CtDraw composes the genie from the .ctm TEMPLATE: where the
    # template twin has NO visible line (no style block, OR a no-colour sentinel 0xFF line slot)
    # it draws NOTHING -> suppress our outline; where it has a real line colour, graft that.
    # (an UNALIGNED instance's member without an owner has twin = itself: nothing says its
    # frame is invisible, so it keeps the reader's default pen)
    if (
        mpo.type_code in (0x0F, 0x10, 0x0A, 0x11)
        and not (mpo.subtype_size & 0x2)
        and st.line_colour is None
        and not st.filled
        and twin is not mpo
    ):
        tpo_l = twin
        tline = None
        if tpo_l.subtype_size & 0x2:
            lcb = ctx.cb(tpo_l)
            if lcb is not None:
                tline = _ps.design_colour(lcb.slots[0])
                s0 = state0_pair(mpo) if lcb.flags[2] == 0 else None
                if s0 is not None:
                    # a hollow owner: the member's line slot is its own state 0
                    tline = _ps.design_colour(s0)
        if tline is not None:
            st.line_colour = tline
            st.line_width = max(1, lcb.flags[4] or 1)
            st.line_style = lcb.flags[1] & 0x0F
            oj.raw["_template_line"] = True
        else:
            # template frame is transparent (no style block / sentinel line) -> no outline;
            # PS_NULL reaches every kind's pen, _no_outline the RECTANGLE path
            st.line_colour = None
            st.line_style = 5
            if mpo.type_code == 0x0F:
                oj.raw["_no_outline"] = True
    elif (
        mpo.type_code in _py.GRAFT_TYPE_CODES
        and not (mpo.subtype_size & 0x2)
        and st.filled
        and twin.subtype_size & 0x2
    ):
        # a FILLED art-stripped member: its fill is its own state-0, but its LINE (colour /
        # width / style) is the library twin's: the member has no line data at all.
        # a record with a library owner saves no style block
        # and loads none (CtDraw runs only on bit 0x2), so its
        # pen (slot, width) is the twin's.
        lcb = ctx.cb(twin)
        if lcb is not None:
            tline = _ps.design_colour(lcb.slots[0])
            if tline is not None:
                st.line_colour = tline
                st.line_width = max(1, lcb.flags[4] or 1)
                st.line_style = lcb.flags[1] & 0x0F
                oj.raw["_template_line"] = True
            elif lcb.flags[2]:
                # the twin's LINE slot is the no-colour sentinel: CtDraw draws the face with
                # a NULL pen (PS_NULL)
                st.line_colour = None
                st.line_style = 5
                oj.raw["_template_line"] = True


def _graft_member_fill(st, oj, mpo, twin, ctx) -> None:
    """An art-stripped closed shape's fill: cleared under a hollow twin, else the twin's
    static fill when the decode decided none."""
    # template HOLLOW clears a spurious inline FILL: an art-stripped member the decode wrongly
    # FILLED (e.g. a (0,0) trailing pair read as solid black) whose aligned template twin is
    # HOLLOW (style block, fill_style byte 0) must render UNFILLED (outline only). Keeps the
    # line colour -> the outline draws.
    if not (mpo.subtype_size & 0x2) and st.filled:
        tpo_h = twin
        if tpo_h.subtype_size & 0x2:
            hcb = ctx.cb(tpo_h)
            if hcb is not None and hcb.flags[2] == 0:  # template fill_style 0 = hollow
                st.filled = False
                oj.raw["_template_hollow"] = True
    if mpo.subtype_size & 0x2:
        return  # kept its own style block -> don't graft the FILL
    if st.filled:
        return  # the decode already DECIDED a fill (COL_A=TRUE / a
        #                                resting state-0): never override it
    rp = oj.raw.get("_props") or {}
    if str(rp.get("COL_A", "")).strip().upper() in ("TRUE", "FALSE"):
        return  # a LITERAL COL_A gate decides this statically (TRUE
        #                                paints its own pair, FALSE stays blank): the editor
        #                                does NOT show the template fill
    tpo = twin
    if not (tpo.subtype_size & 0x2):
        return  # template twin has no static face either
    cb = ctx.cb(tpo)
    if cb is None or cb.flags[2] == 0:
        return  # no template, or HOLLOW template face (no fill to graft;
        #                                grafting its border adds an outline the editor hides)
    fill = _ps.design_colour(cb.slots[1])
    s0 = state0_pair(mpo)
    if s0 is not None:
        # the member's fill slot is its own state 0, not the owner's (state0_pair)
        fill = _ps.design_colour(s0)
    if fill is None:
        return
    # The genie INSTANCE's flat COL_MAXON is its RUNTIME colour; when the template's static
    # fill EQUALS it, this is a runtime-coloured cell the editor leaves blank. Graft only a
    # DISTINCT static fill.
    cm_on = _ps.pair_on(rp.get("COL_MAXON")) or _ps.pair_on(rp.get("!COL_STATIC"))
    if cm_on is not None and (cm_on & 0xFFFFFF) == fill:
        return
    st.fill_colour = fill
    st.filled = True
    oj.raw["_template_colour"] = True


def _graft_shape_member(oj, mpo, twin, ctx) -> None:
    """An art-stripped closed shape member's appearance from its twin."""
    st = oj.style
    _graft_shape_style(st, oj, mpo, twin, ctx)
    _graft_member_line(st, oj, mpo, twin, ctx)
    _graft_member_fill(st, oj, mpo, twin, ctx)


def _graft_group_refs(page, objs, pobjs, sub, owned, refl, resolver) -> None:
    """An aligned art-stripped group member without an owner borrows its template twin's
    reference, and its stripped leaves are repaired from that element."""
    # REF-GRAFT: an art-stripped GROUP container member (tc=0x08, has members, no resolved children
    # AND no own library ref) whose ALIGNED template twin carries a library REF: the real GLYPH
    # art sits behind that nested group ref, which the inline page copy lost, so its stripped
    # tc=0x07 leaves resolve to nothing. Borrow the template twin's ref and REPAIR the
    # container's stripped leaves from the resolved element (mirrors the page-level template-ref
    # borrow + _repair_stripped_nodes). The library element's tc=0x07 records
    # carry the embedded DIB / nested ref for each state; _repair_stripped_nodes grafts them
    # IN PLACE (no double-draw), then we state-select to the resting/default glyph.
    # (only on the index-aligned fallback: a member with a resolved owner already took it above
    # -- the leaf graft below expands a linked leaf owner's own reference -- and a count-matched
    # repair of a nested container could pair the wrong records)
    for k, j in enumerate(sub):
        if owned[k]:
            continue
        pj = pobjs[j]
        oj = objs[j]
        if pj.type_code != _codes.TC.GROUP or not oj.members or oj.children:
            continue
        try:
            if reference_from_object(pj) is not None:
                continue  # already carries its own ref (expanded via _expand_symbol)
            tref = reference_from_object(refl[k])
        except Exception:  # noqa: BLE001
            _note_exc("reader._graft_group_refs/template ref")
            tref = None
        if tref is None or tref.ext not in _py.EXPANDABLE_EXTS:
            continue
        csub = [m for m in sorted(page.subtree(j)) if m != j and m < len(pobjs)]
        pairs = [(objs[m], pobjs[m]) for m in csub]
        try:
            if _py.repair_stripped_nodes(pj, pairs, resolver, 1, ref=tref):
                for m in csub:
                    if pobjs[m].type_code == _py.SYMBOL_SET_TC and objs[m].members:
                        _py.reselect_set_state(objs[m], objs, pobjs)
        except Exception:  # noqa: BLE001
            _note_exc("reader._graft_group_refs/state select")
            pass


def _graft_leaf_pictures(objs, pobjs, sub, twl, refl, resolver, ctx) -> None:
    """A stripped symbol leaf takes its twin's embedded picture, or expands the library
    reference its twin carries."""
    # LEAF-DIB graft: a stripped tc=0x07 SYMBOL leaf (no children, no DIB, no own ref) whose ALIGNED
    # template twin carries an embedded DIB directly. A page can place its template chrome MULTIPLE
    # times; the first instance gets its bitmaps via the page-template tpl_map graft, but the
    # DUPLICATE instances are art-stripped, so their background occludes the first instance's
    # bitmaps. Graft the template twin's DIB so every instance draws the bitmap (they overlap
    # identically). Mirrors the page-template tpl_map leaf-graft, for genie templates.
    for k, j in enumerate(sub):
        if pobjs[j].type_code != 0x07:
            continue
        oj = objs[j]
        if oj.children or oj.raw.get("_embedded_bmp") is not None or oj.raw.get("_non_drawable"):
            continue
        try:
            if reference_from_object(pobjs[j]) is not None:
                continue  # has its own ref -> resolved via _expand_symbol
            tpo = twl[k]
            if tpo is pobjs[j]:
                continue  # no owner record: nothing to borrow
            eb, flash = ctx.dibs(tpo)
            if eb is not None:
                oj.raw["_embedded_bmp"] = eb
                if flash is not None:
                    oj.raw["_embedded_bmp_flash"] = flash
                oj.raw["_bmp_grafted"] = True  # a twin's DIB (element frame)
                continue
            # ...or a library REF: expand it like the page-template leaf graft does
            tref = reference_from_object(refl[k])
            if tref is not None and tref.ext in _py.EXPANDABLE_EXTS:
                rs = resolver.resolve(tref)
                if rs is not None:
                    _py.expand_element(oj, rs, resolver, 1)
        except Exception:  # noqa: BLE001
            _note_exc("reader._graft_leaf_pictures/leaf expand")
            pass


def _genie_text_fonts(objs, pobjs, sub, genie_sizes: dict, genie_face: str) -> None:
    """The genie's un-grafted texts take its most common grafted size and its face, and
    the justify their anchor shows."""
    # un-grafted genie TEXT (no font in the inline copy OR its template twin, e.g. menu button
    # captions) inherits the genie's font SIZE/face: CtDraw draws all the menu text at one size.
    # Each caption keeps its OWN weight.
    # the MOST COMMON grafted size = the body font, not a lone header size. May be 0 when the
    # template grafted no sizes onto this genie: then only the justify is corrected.
    genie_size = max(genie_sizes, key=genie_sizes.get) if genie_sizes else 0
    for k, j in enumerate(sub):
        mpo = pobjs[j]
        if mpo.type_code != 0x05 or (mpo.subtype_size & 0x2):
            continue
        st = objs[j].style
        if st.font_size:
            continue
        is_mask = "#" in (st.text or "")
        # A numeric VALUE MASK ('####.#' / value display) FILLS its field at a big font, RIGHT-
        # aligned. So leave its size 0 -> the scene's rect-height fallback fills the box, and
        # RIGHT-justify it so it anchors to the field's right edge instead of overflowing left.
        # Menu BUTTON CAPTIONS keep the label size + centre.
        if genie_size and not is_mask:
            st.font_size = genie_size
            if not st.font:
                st.font = genie_face
        # the justify IS in the data: the engine lays a text out from its baseline ANCHOR per
        # justify (left: x = l, centre: x = (l+r)/2, right: x = r -- textmetrics), so where the
        # anchor sits in rect1 says how CtDraw draws it
        anc, r1 = objs[j].raw.get("_anchor"), objs[j].raw.get("_rect1")
        inferred = None
        if anc and r1:
            l, r = min(r1[0], r1[2]), max(r1[0], r1[2])
            if abs(anc[0] - l) <= 1:
                inferred = TextJustify.LEFT
            elif abs(anc[0] - r) <= 1:
                inferred = TextJustify.RIGHT
            elif abs(anc[0] - (l + r) / 2.0) <= 1.5:
                inferred = TextJustify.CENTRE
        if inferred is not None:
            st.justify = inferred
        elif is_mask:
            st.justify = TextJustify.RIGHT
        elif st.justify == TextJustify.LEFT:
            st.justify = TextJustify.CENTRE
        objs[j].raw["_template_font"] = True


def _value_field_boxes(objs, pobjs, sub) -> None:
    """The art-stripped rectangles that back a value display draw as its white field."""
    # VALUE-FIELD boxes: an art-stripped rect the line-graft just suppressed (_no_outline) that
    # TIGHTLY BACKS a value-display (a '#'-mask TEXT or a NUMBER -- the field where a runtime value
    # appears) is the field BACKGROUND. CtDraw draws it WHITE with a black border, NOT transparent.
    # Distinguish from a generic suppressed FRAME by SIZE: a field box HUGS the value text's
    # height; a frame is much taller than its caption. White-fill + black-border the fields; frames
    # stay suppressed. (Boxes that kept a style block / template fill are untouched.)
    vfields = [
        objs[j].geometry
        for j in sub
        if (
            objs[j].kind is ObjectKind.NUMBER
            or (objs[j].kind is ObjectKind.TEXT and "#" in (objs[j].style.text or ""))
        )
    ]
    # centres of the genie's ALREADY-FILLED closed shapes (e.g. the grafted blue border + white
    # field of a value box): a suppressed rect that SITS OVER one of these is the runtime
    # SETPOINT HOTSPOT overlay, not a field background. Only the TRUE field, which has no filled box
    # under it, gets the white background.
    _CL = (ObjectKind.RECTANGLE, ObjectKind.POLYGON, ObjectKind.ELLIPSE)
    if vfields:
        # (sorted by x: a rect looks only at the centres within its x range)
        filled_centres = sorted(
            (
                objs[s].geometry.x + objs[s].geometry.extent_x / 2.0,
                objs[s].geometry.y + objs[s].geometry.extent_y / 2.0,
            )
            for s in sub
            if objs[s].style.filled and objs[s].kind in _CL
        )
        centre_xs = [cx for cx, _cy in filled_centres]
        for j in sub:
            oj = objs[j]
            if not oj.raw.get("_no_outline") or oj.style.filled or pobjs[j].type_code != 0x0F:
                continue
            g = oj.geometry
            x1, y0, y1 = g.x + g.extent_x, g.y, g.y + g.extent_y
            lo, hi = bisect_left(centre_xs, g.x), bisect_right(centre_xs, x1)
            if any(y0 <= cy <= y1 for _cx, cy in filled_centres[lo:hi]):
                continue  # covers an already-filled value box -> hotspot overlay
            s0 = state0_pair(pobjs[j])
            field = _ps.design_colour(s0) if s0 is not None else 0xFFFFFF
            if field is None:
                continue  # its own state 0 is no colour: CtDraw shows the page
                #                            through it
            for vg in vfields:
                tcx, tcy = vg.x + vg.extent_x / 2.0, vg.y + vg.extent_y / 2.0
                # the field must HUG the value text on BOTH axes (small padding) -- a box that
                # contains the value text but is much WIDER/taller is the genie's OUTER FRAME
                # (label + value), which CtDraw leaves transparent.
                if (
                    g.x <= tcx <= g.x + g.extent_x
                    and g.y <= tcy <= g.y + g.extent_y
                    and g.extent_x <= vg.extent_x + 40
                    and g.extent_y <= vg.extent_y + 14
                ):
                    oj.raw.pop("_no_outline", None)
                    oj.style.filled = True
                    oj.style.fill_colour = field
                    oj.style.line_colour = 0x000000
                    oj.raw["_value_field"] = True
                    break


def graft_template_member_colours(page, objs, pobjs, idx, resolver, ref=None, ctx=None) -> None:
    """Graft the template's static fills onto art-stripped member rects."""
    po = pobjs[idx] if idx < len(pobjs) else None
    if po is None:
        return
    try:
        if ref is None:
            ref = reference_from_object(po)
        # accept .ctt PAGE-TEMPLATE genies too (not just .ctm/.ctl symbol templates): pages
        # embed extra instances of their .ctt chrome template as genies,
        # art-stripped -- they must graft their logo/icon DIBs + fills from the .ctt element or their
        # white header bg occludes the real logo.
        if ref is None or ref.ext not in (_py.EXPANDABLE_EXTS + ("ctt",)):
            return
        resolved = resolver.resolve(ref)
    except Exception:  # noqa: BLE001
        _note_exc("reader.graft_template_member_colours/resolve")
        return
    if resolved is None:
        return
    sub = list(page.subtree(idx))  # depth-first, document order, includes idx
    if len(sub) < 2:
        return
    if ctx is None:
        ctx = GraftCtx(page, pobjs, resolver)
    got = _member_twins(pobjs, idx, sub, resolved, ref, resolver, ctx)
    if got is None:
        return
    twl, refl, owned, aligned = got

    genie_sizes, genie_face = {}, ""  # the genie's grafted text fonts (for un-grafted siblings)
    for k, j in enumerate(sub):
        mpo = pobjs[j]
        if not (mpo.subtype_size & 0x2):
            graft_twin_extras(objs[j], mpo, twl[k], ctx)
        # ---- TEXT members: graft the template's FONT (bold/size/face) ----------------------
        if mpo.type_code == 0x05:
            genie_face = _graft_text_font(objs, j, mpo, twl[k], ctx, genie_sizes, genie_face)
            continue
        if mpo.type_code not in _py.GRAFT_TYPE_CODES:
            continue  # not a closed shape
        _graft_shape_member(objs[j], mpo, twl[k], ctx)

    if aligned:
        _graft_group_refs(page, objs, pobjs, sub, owned, refl, resolver)

    _graft_leaf_pictures(objs, pobjs, sub, twl, refl, resolver, ctx)

    _genie_text_fonts(objs, pobjs, sub, genie_sizes, genie_face)

    _value_field_boxes(objs, pobjs, sub)


def _is_target_btn(o) -> bool:
    return (
        o.raw.get("_type_code") == 0x1001
        and str((o.raw.get("_props") or {}).get("VIS_MODE", "")).upper() == "TARGET"
    )


def sibling_button_flags(siblings) -> tuple:
    """(has a Touch record, has a TARGET button) among ``siblings`` -- what
    :func:`fill_button_faces` decides a sibling group's faces by."""
    return (
        any(o.raw.get("_type_code") == _codes.TC.TOUCH for o in siblings),
        any(_is_target_btn(o) for o in siblings),
    )


def fill_button_faces(objs, only=None) -> None:
    """Give an unfilled rect grouped with a touch command CtDraw's grey button face."""
    BTN_FACE = 0x00D9D6CF  # XP control-button gray (Touch-grouped)
    MENU_FACE = 0x00F5F5F5  # near-white menu/nav button face (TARGET-button-grouped)

    def walk(siblings, keep=None):
        has_touch, has_target_btn = sibling_button_flags(siblings)
        for o in siblings:
            if keep is not None and id(o) not in keep:
                continue
            # An ART-STRIPPED rect (no style block, dwFlags&2 clear) is a genie member whose face
            # CtDraw composes from the TEMPLATE / system button colour, NOT its inline colour-state
            # data -- so override any inline-derived fill (fill rule otherwise paints it
            # from its state-0). A hollow styled rect (unfilled) is also a candidate.
            art_stripped = not ((o.raw.get("_dwflags") or 0) & 0x2)
            if o.raw.get("_type_code") == 0x0F:
                touch = has_touch or any(c.raw.get("_type_code") == _codes.TC.TOUCH for c in o.children)
                # only a genie / library member (dwFlags 0x100: those faces are 0x11A) --
                # a free hollow rect the user grouped with a touch command stays hollow
                if touch and not o.style.filled and (o.raw.get("_dwflags") or 0) & 0x100:
                    # a Touch-grouped rect with NO resting fill -> system control gray. (One
                    # that DOES have a state-0 fill keeps it: it is not a bare button face.)
                    o.style.fill_colour = BTN_FACE
                    o.style.filled = True
                    o.raw["_button_face"] = True
                elif (
                    has_target_btn
                    and art_stripped
                    and not o.style.filled
                    and not any(
                        str(k).startswith(("COL_STA", "COL_ARR", "COL_THR", "COL_MAXON"))
                        for k in (o.raw.get("_props") or {})
                    )
                ):
                    # (only a face with NO resting fill of its own: a filled one shows its
                    # state-0)
                    # the art-stripped face of a TARGET (hotspot) menu button with NO genuine inline
                    # colour bag is composed ~white from the template (override any spurious
                    # fill from a trailing scan). A rect that DOES carry a real COL_ARR/COL_STA state
                    # keeps its own colour.
                    o.style.fill_colour = MENU_FACE
                    o.style.filled = True
                    o.raw["_button_face"] = True
            if o.children:
                walk(o.children)

    by_parent = defaultdict(list)
    for i, o in enumerate(objs):
        by_parent[o.parent].append(i)
    for idxs in by_parent.values():
        if only is None:
            walk([objs[i] for i in idxs])
        elif any(i in only for i in idxs):
            walk([objs[i] for i in idxs], {id(objs[i]) for i in idxs if i in only})


def suppress_redundant_hotspots(objs, only=None) -> None:
    """Hide unfilled clickable hotspot rects that sit on a filled member."""
    n = len(objs)
    order = range(n) if only is None else sorted(i for i in only if i < n)
    root = {}
    looped = set()  # (a walk that met the step limit: a parent cycle)
    for i in order:
        p = objs[i].parent
        if p is not None and 0 <= p < n and p in root and p not in looped:
            root[i] = root[p]  # the walk from i goes on as the one from its parent
            continue
        j, seen = i, 0
        while p is not None and 0 <= p < n and seen < n:
            j, p, seen = p, objs[p].parent, seen + 1
        root[i] = j
        if p is not None and 0 <= p < n:
            looped.add(i)
    _CLOSED = (ObjectKind.RECTANGLE, ObjectKind.POLYGON, ObjectKind.ELLIPSE)
    filled_by_root = defaultdict(list)
    for i in order:
        o = objs[i]
        if o.style.filled and o.kind in _CLOSED:
            g = o.geometry
            filled_by_root[root[i]].append((g.x, g.y, g.x + g.extent_x, g.y + g.extent_y))
    # per root: the filled boxes sorted by their left edge and their widest width. A box
    # covers 40 % of a hotspot (area > 0) only when the two overlap, so only the boxes whose
    # left edge lies within (hotspot left - widest width, hotspot right) can
    sorted_by_root = {}
    for r, boxes in filled_by_root.items():
        boxes.sort()
        widest = max(bx1 - bx0 for bx0, _by0, bx1, _by1 in boxes)
        sorted_by_root[r] = ([b[0] for b in boxes], boxes, widest)
    for i in order:
        o = objs[i]
        if not (
            o.kind == ObjectKind.RECTANGLE
            and o.members
            and not o.style.filled
            and o.style.line_colour is None
            and (o.raw.get("_dwflags") or 0) & 0x10
        ):
            continue
        g = o.geometry
        ax0, ay0, ax1, ay1 = g.x, g.y, g.x + g.extent_x, g.y + g.extent_y
        area = (ax1 - ax0) * (ay1 - ay0)
        if area <= 0:
            continue
        got = sorted_by_root.get(root[i])
        if got is None:
            continue
        lefts, boxes, widest = got
        lo = bisect_left(lefts, ax0 - max(0, widest) - 1)
        hi = bisect_right(lefts, ax1)
        for bx0, by0, bx1, by1 in boxes[lo:hi]:
            ix = max(0, min(ax1, bx1) - max(ax0, bx0))
            iy = max(0, min(ay1, by1) - max(ay0, by0))
            if ix * iy >= 0.4 * area:
                o.raw["_no_outline"] = True
                break


def expand_nested_containers(children: list, pobjs, resolver, depth: int) -> None:
    """Expand genie containers inside an element from their library refs."""
    n = min(len(children), len(pobjs))
    for i in range(n):
        po = pobjs[i]
        if po.type_code != _codes.TC.GROUP or not children[i].members:
            continue
        if children[i].children:
            continue
        if any(_py.member_has_art(children[j]) for j in _py.local_subtree(children, i)):
            continue
        try:
            ref = reference_from_object(po)
            if ref is None or ref.ext not in _py.EXPANDABLE_EXTS:
                continue
            resolved = resolver.resolve(ref)
        except Exception:  # noqa: BLE001
            _note_exc("reader.expand_nested_containers")
            continue
        if resolved is None:
            continue
        _py.expand_element(children[i], resolved, resolver, depth + 1)
        if children[i].children:
            children[i].raw["_resolved_ref"] = str(resolved.reference)
            for j in _py.local_subtree(children, i):
                children[j].raw["_state_hidden"] = True


def content_bbox_min(children) -> tuple[int, int]:
    """Min (x, y) corner of the drawable children's bounding boxes (element origin).

    Degenerate anchor objects (1x1) and non-drawables are ignored so the origin tracks the real
    artwork. Returns (0, 0) when nothing usable is found."""
    xs = []
    ys = []
    for c in children:
        if c.raw.get("_non_drawable"):
            continue
        g = c.geometry
        if g.extent_x <= 1 and g.extent_y <= 1:
            continue
        xs.append(g.x)
        ys.append(g.y)
    if not xs:
        return (0, 0)
    return (min(xs), min(ys))
