"""Write library elements in the form CtDraw rebuilds on load and save, so a CtDraw save
leaves them unchanged."""

from __future__ import annotations

import re
import struct
from typing import Dict, List, Optional, Sequence, Tuple

from . import genie_place as GP
from .genie_place import Bag, decode, encode, to_po, substitute, template_refs
from . import geometry as G, placement as P, colourstate as CS, writer
from .page import PageObject
from ..infra.logsetup import note_exception as _note_exc
from . import codes as _codes
from . import records as _records

OV11_TCS = frozenset({0x1007, 0x1009, _codes.TC.KEYBOARD, 0x100D, _codes.TC.TOUCH})
GROUP_TC, SYMSET_TC, TEXT_TC = 0x08, 0x0C, 0x05
#: the style block CtDraw writes for a synthesised element root group
DEFAULT_GROUP_STYLE = _records.DEFAULT_STYLE.pack()
DEFAULT_GROUP_CS = _records.colour_state()
_NATIVE = re.compile(r"@\(([^)]*)\)")


def class_ov(tc: int) -> int:
    """The in-memory internal-pair overshoot of e0.b1 (engine output:
    0x1000 0, 0x100B / 0x1010 11, every other class 10)."""
    if tc == 0x1000:
        return 0
    return 11 if tc in OV11_TCS else 10


def parents_of(recs) -> List[Optional[int]]:
    return P.forest_parents(recs)


def is_genie_element(recs: Sequence[PageObject]) -> bool:
    """Does the element have its own genie level?"""
    parents = P.forest_parents(list(recs))
    depth = nested_roots(list(recs), parents)
    for k, po in enumerate(recs):
        if po.subtype_size & 0x8 and depth[k] == 0:
            r = decode(po)
            own = 3 if po.type_code == 0xFFFF and GP.placeholder_genie(po) else 2
            if r.entries and len(r.entries) >= own:
                return True
    return False


def template_declares(recs: Sequence[PageObject]) -> bool:
    """A TEMPLATE's records name a %parameter% of its own: a direct record (not the root,
    not in a genie or template copy nested in it: :func:`nested_roots`) whose values name a
    %token% (its first own parameter, just drawn: a text '%P%'). A copy of another template
    it holds does not count, whatever its records carry (they are the inner template's)."""
    recs = list(recs)
    parents = P.forest_parents(recs)
    depth = nested_roots(recs, parents)
    for k, po in enumerate(recs):
        if k == 0 or depth[k] or not po.subtype_size & 0x8:
            continue
        try:
            r = decode(po)
        except Exception:  # noqa: BLE001  # swallow-ok: an undecodable record names nothing
            continue
        if r.entries and template_refs(r.entries[0][1]):
            return True
    return False


def convert_template_records(recs: List[PageObject], parents=None) -> List[PageObject]:
    """A template's records when it gets its first parameter."""
    from . import template_place as TP

    out = list(recs)
    spans = TP.nested_template_spans(out)
    in_copy = set()
    for a, b, _ref in spans:
        out[a:b] = TP.to_element_form(out[a:b])
        in_copy.update(range(a, b))
    parents = P.forest_parents(out) if parents is None else parents
    depth = nested_roots(out, parents)
    for k, po in enumerate(out):
        if k == 0 or k in in_copy or not depth[k] or not po.subtype_size & 0x8:
            continue
        r = decode(po)
        if r.entries is None:
            continue
        r.entries = TP.element_form_entries(r.entries, r.tc, nested=True)
        out[k] = to_po(encode(r))
    return out


def nested_roots(recs, parents) -> Dict[int, int]:
    """Record index -> number of nested genie roots at or above it."""
    from . import template_place as TP

    tpl = {k for k, po in enumerate(recs) if k and TP.is_template_copy_root(po)}
    depth = {}
    for k in range(len(recs)):
        d = 0
        a = k
        while a is not None and a != 0:
            po = recs[a]
            if po.class_flag and ((po.subtype_size & 0x201) == 0x201 or a in tpl):
                d += 1
            a = parents[a]
        depth[k] = d
    return depth


def _bound_top(ents, recs, parents, k: int, root_index: int) -> bool:
    """Is the top level of nested member ``k`` a placed page-form level?"""
    top = ents[-1]
    if top[2].pairs:
        return False
    if top[1].pairs:
        return True
    roots = 0
    a = k
    while a is not None and a != root_index:
        po = recs[a]
        if po.class_flag and po.subtype_size & 0x200:
            roots += 1
        a = parents[a]
    return len(ents) - 1 <= roots


def normalise_records(
    recs: List[PageObject],
    *,
    root_index: int = 0,
    ext: str = "ctm",
    convert: bool = False,
    resolver=None,
    lib_path: str = "",
    edited: Optional[set] = None,
    e=(0, 0),
    page_like: bool = False,
    nested_only=None,
    stored_payloads=None,
    report: Optional[dict] = None,
    fonts_path: Optional[str] = None,
) -> List[PageObject]:
    """The CtDraw-normal form of an element's records."""
    recs = list(recs)
    base = list(recs)
    n = len(recs)
    parents = parents_of(recs)
    genie = convert or is_genie_element(recs)
    # 1. in a genie element, instances still in their PAGE form (pasted from a page) take
    # the element form first (GP.convert_page_form): the re-composition below rebuilds a
    # nested copy from its binding level, which a page-form copy placed without bindings
    # does not have (its slots and geometry then stayed as stored)
    keeps_page_form = not (genie and not page_like and ext != "ctt")
    if not keeps_page_form:
        recs = GP.convert_page_form(recs, genie=True)
    edited_objs = [recs[k] for k in (edited or ()) if 0 <= k < len(recs)]
    # 1a, first: a TEMPLATE's copy the element holds (a template made from a page linked to
    # another) composed again from the current template when its import entry changed
    # (``nested_only`` names it; None = every copy), as CtDraw's element load does (LIVE,
    # oracle chain/outer_template Q2); every other copy as stored, its free DummyMarkers'
    # boxes derived again (Q3). Its records are the inner template's, placed: nothing below
    # derives them again (template_place.compose_nested_templates)
    tpl_stale = frozenset()
    if resolver is not None:
        from . import template_place as TP

        tpl_rep: dict = {}
        recs = TP.compose_nested_templates(recs, resolver, only=nested_only, report=tpl_rep)
        tpl_stale = frozenset(tpl_rep.get("stale", ()))
    if convert and ext == "ctt":
        # a TEMPLATE's first own parameter: the records this normal form leaves as they are
        # (its template copies, its nested genie copies) take its level too
        # (:func:`convert_template_records`, LIVE oracle chain4/outer_param)
        recs = convert_template_records(recs)
    # 1a. nested genies re-composed from their current definition
    if resolver is not None:
        el = GP.Element(lib_path, "", "", ext, tuple(e), recs, (0, 0, 0, 0))
        # (a genie element keeps its nested copies in the element form: step 1)
        el.element_form_copies = not keeps_page_form
        GP.refresh_nested(el, resolver, merge_only=nested_only)
        recs = el.records
        twins = getattr(el, "nested_twins", {})
        if report is not None:
            report["stale_nested"] = frozenset(getattr(el, "stale_nested", ()) or ()) | tpl_stale
            if getattr(el, "cycles", None):
                # (a nested genie that holds a copy of itself: its copy there stays as
                # stored; the chains for the caller to name)
                report["cycles"] = frozenset(el.cycles)
    else:
        twins = {}
    # a nested copy composed again with more or fewer records (an object added to or
    # removed from its genie), or with as many in another order (Bring to Front of a
    # genie's text over its nested genie): the forest and the edited records follow. The
    # forest is the records' child counts in order
    grown = len(recs) != n or any(a.class_flag != b.class_flag for a, b in zip(recs, base))
    if grown:
        n = len(recs)
        parents = parents_of(recs)
        at = {id(po): k for k, po in enumerate(recs)}
        edited = {at[id(po)] for po in edited_objs if id(po) in at}
    depth = nested_roots(recs, parents)
    # a record of a nested genie copy that nothing above changed (not converted from the
    # page form, not re-composed, not edited) is kept as stored: CtDraw's load + save
    # re-derives no level of such a copy
    if stored_payloads is None:
        touched = set(range(len(recs)))
    elif grown:
        # (a record kept as it was, wherever it moved to, stays as stored)
        kept = {id(po) for po in base}
        touched = {
            k
            for k in range(n)
            if id(recs[k]) not in kept or writer.object_payload(recs[k]) not in stored_payloads
        }
    else:
        touched = {
            k
            for k in range(n)
            if recs[k] is not base[k] or writer.object_payload(base[k]) not in stored_payloads
        }
    touched |= set(edited or ())
    # (a template's copy: the records step 1a gave it, as they are)
    in_tpl = _template_copy_records(recs, root_index)
    out: List[Optional[PageObject]] = [None] * n
    dec = [
        decode(po) if (depth[k] == 0 or k in touched or k == root_index) and k not in in_tpl else None
        for k, po in enumerate(recs)
    ]
    for k, r in enumerate(dec):
        po = recs[k]
        if r is None:
            out[k] = po
            continue
        root = k == root_index
        caption = ""  # a page-form member's TXT_OFF before its left trim
        if r.entries is not None:
            ents = [[b.copy() for b in ent] for ent in r.entries]
            if genie and len(ents) == 1 and depth[k] == 0:
                # the genie form: the user-entered values are the templates
                ents[0][2] = Bag(list(ents[0][1].pairs), 0)
                ents.append([Bag([], 0), Bag([], 0), Bag([], 0)])
            N = len(ents) - 1
            if N >= 1:
                # (a symbol set declares its own template's tokens too, a member or
                # the element's root; only a GROUP's come from its members)
                # (built on EVERY save, edited or not, as CtDraw's save does)
                if (
                    genie
                    and depth[k] == 0
                    and N == 1
                    and not (root and r.tc == GROUP_TC)
                    and not (po.class_flag and r.tc == GROUP_TC)
                ):
                    # declarations = the template's distinct %tokens%, first-
                    # appearance order: a token's own '%name=default%' is the
                    # default (the last one written wins), else
                    # a known default is kept, else '\n'
                    # (a default the previous declaration held is NOT kept: the level is
                    # built anew: '%X=5%' edited to
                    # '%X%' saves X = '\n' and drops the substituted value)
                    ents[1][0] = Bag(
                        [(t, d if d is not None else "\n") for t, d in GP.template_decls(ents[0][2]).items()],
                        0,
                    )
                # the binding levels of a nested genie: each level's values
                # re-derived from its templates with the level above
                # (element level unbound -> defaults)
                # (any genie root inside the element: one nested deeper has no link bit,
                # 0x208 / 0x218)
                nested_root = bool(po.class_flag and (po.subtype_size & 0x200)) and not root
                # a nested MEMBER: the binding level under the element's own level is
                # effective as its templates, each lower level as its re-derived values
                # (GP.top_level_templates); a nested root takes the values only
                # (LIVE n0 .. n4)
                member_rule = depth[k] >= 1 and not nested_root and N >= 2
                # a member of a copy kept in the PAGE form (step 1 converts none in a
                # template, a super genie page or an element without parameters): its top
                # level is the placed level, BOUND, so every level under it takes the
                # values above, as a page derives them (template_place._rederive_levels,
                # the page composition)
                page_form = member_rule and keeps_page_form and _bound_top(ents, recs, parents, k, root_index)
                eff = None
                for lvl in range(N - 1, 0, -1):
                    # (a nested ROOT's own level takes the outer level's VALUES only, no
                    # declared default: LIVE, P bound to '%Q=5%' under an
                    # unbound Q gives the root b1 {} and its member b1 P = 5)
                    GP.subst_level(
                        ents[lvl], ents[lvl + 1], outer_vals=eff, decls=None if member_rule else GP.Bag([], 0)
                    )
                    if member_rule:
                        eff = (
                            GP.top_level_templates(ents[lvl])
                            if lvl == N - 1 and not page_form
                            else ents[lvl][1]
                        )
                if depth[k] == 0:
                    # a direct member: substituted with the element's own
                    # (unbound) level = the declared defaults
                    vals, decls = ents[1][1], ents[1][0]
                else:
                    # a member of a genie NESTED in the element: its level-1
                    # parameter values are the (unresolved) BINDING templates
                    # e1.b2 -- '%DPSStateType%' -> '%DPS_StateType%' (LIVE
                    # dpsalarm); e1.b1 itself resolves to the (empty) defaults
                    # per parameter: its binding TEMPLATE (e1.b2) when it has
                    # one, else its bound constant (e1.b1: list_cluster's
                    # index = 18)
                    vals = eff if eff is not None else Bag(list(ents[1][1].pairs), 0)
                    decls = ents[1][0]
                for key, tmpl in ents[0][2].pairs:
                    # (no trim of a DIRECT member: the element keeps ' @(..)', LIVE
                    # example.face1, and ' ' for n0's own ' %Label%', LIVE chain7/label_blank)
                    v = substitute(tmpl, vals, decls, key)
                    ents[0][1].set(key, v)
                    if depth[k] >= 1 and v[:1].isspace():
                        # a record of a nested copy: the left trim after the value is set,
                        # so one of only blanks stays as '' (GP.build_instance_records,
                        # LIVE), in the page form, in the element form (LIVE, the fixture
                        # oracle's chain7/label_blank) and a record a host gained while the
                        # genie between was out of date (chain5/stale_host); the string
                        # section keeps the caption as substituted
                        low = key.lower()
                        if low == "txt_off":
                            caption = v
                        ents[0][1].pairs = [
                            (kk, vv.lstrip() if kk.lower() == low else vv) for kk, vv in ents[0][1].pairs
                        ]
                ents[0][1].ov = class_ov(r.tc)
                if depth[k] == 0:
                    ents[0][2].ov = class_ov(r.tc)
                else:
                    # a nested member: 0, except a LINKED symbol (tc 7 whose
                    # current twin has dwFlags 0x1) keeps 10 (LIVE list_cluster. A
                    # Graphics Builder that composed a nested
                    # copy from an element OPENED IN A WINDOW writes the class pair
                    # instead: its in-memory form, not the file's)
                    tw = twins.get(k)
                    linked = r.tc == 0x07 and (
                        (tw is not None and tw.subtype_size & 0x1) or po.subtype_size & 0x1
                    )
                    ents[0][2].ov = 10 if linked else 0
                for lvl in range(1, N + 1):
                    for b in ents[lvl]:
                        b.ov = 0
                if root:
                    ents[N][1].ov = 11
                elif po.class_flag and (po.subtype_size & 0x200):
                    # a nested genie root's OWN level is always level 1 (a copy nested
                    # deeper, without the linked bit, too: LIVE n0 .. n4)
                    ents[1][1].ov = 11
                    ents[1][2].ov = 11 if ents[1][2].pairs else 0
            else:
                ents[0][1].ov = class_ov(r.tc)
                ents[0][2].ov = 0
            r.entries = ents
        # text: string section from the substituted values, tail caption =
        # the template, extent re-measured from the template caption
        if r.tc == TEXT_TC and (r.dw & 2) and r.entries is not None:
            _text(r, po)
        elif r.tc == TEXT_TC and r.entries is not None and depth[k] >= 1:
            # an art-stripped member of a nested genie: its string
            # section follows the re-derived values (LIVE block_element
            # '%Element%')
            try:
                r.cls = GP.strip_class_section(r, r.entries[0][1], caption, 0)
            except Exception:  # noqa: BLE001
                _note_exc("genie_norm.normalise_records")
                pass
        if r.tc == GP.BUTTON_TC and r.entries is not None and depth[k] == 0:
            _button(r)
        if r.entries is not None and r.cs and CS.layout(po) is not None:
            cs = GP.rebuild_colour_state(po, r.entries[0][1], twins.get(k))
            if cs is not None and not root:
                r.cs = cs
        if root and r.tc == GROUP_TC and r.cf:
            r.cs = DEFAULT_GROUP_CS
            if r.dw & 2:
                r.style = DEFAULT_GROUP_STYLE
        out[k] = to_po(encode(r))
    # 1b. a CONTAINER's outermost declarations are built anew from its subtree: every
    # default a member declares in its outermost level (not a '\n' one) is declared on
    # every container above it, the first in DFS order winning a name, and a name no
    # member declares any more goes (a later record's
    # default fills a name that is absent, empty or '\n'). LIVE: texts
    # '%X%' + '%X=5%' give the root group X = 5; a nested genie bound
    # with '%Q=5%' carries Q = 5 on its element level, its members too; a member edited
    # to '%X=7%' moves the root to 7; the member removed, X goes. The container's own
    # tokens (a symbol set, a button owning a command) stay, their '\n' filled.
    if genie:
        _container_declarations(out, parents, depth, root_index, skip=in_tpl)
    # 2. geometry: texts re-measured above; every legacy Number and String with the Bounds
    # CtDraw's load derives in its project's named font and every legacy Trend in the form
    # it writes, inside a nested copy or a template copy too (legacy.save_extents);
    # containers refit bottom-up
    from . import legacy as _legacy

    fonts = None
    fpath = lib_path if fonts_path is None else fonts_path
    if fpath and any(po.type_code in (_legacy.NUMBER_TC, _legacy.STRING_TC) for po in out):
        fonts = _legacy.named_fonts(fpath)
    out, _moved = _legacy.save_extents(out, fonts or None)
    out = list(out)
    out = _refit(out, parents, root_index, grow=_alarm_grow(lib_path, resolver), skip=in_tpl)
    # 3. root handle = lowest handle unused by the members
    root = out[root_index]
    if not (root.type_code == GROUP_TC and root.class_flag):
        _single_root(root, e)
    if root.type_code == GROUP_TC and root.class_flag:
        used = {G.get_handle(o) for i, o in enumerate(out) if i != root_index}
        h0 = G.get_handle(root)
        if stored_payloads is not None and h0 is not None and h0 not in used:
            # a stored element's root keeps its handle (a root whose handle a member
            # took moves to the lowest free)
            return out
        lf = next(h for h in range(0, 0x8000) if h not in used)
        G.set_handle(root, lf)
    return out


def _template_copy_records(recs, root_index: int = 0) -> set:
    """The indexes of the records of every template copy in ``recs`` (its root included,
    template_place.nested_template_spans), the element's root left out."""
    from . import template_place as TP

    out = set()
    for a, b, _ref in TP.nested_template_spans(recs):
        out.update(k for k in range(a, b) if k != root_index)
    return out


def _container_declarations(out, parents, depth, root_index, skip=frozenset()) -> None:
    """Step 1b of normalise_records."""
    n = len(out)
    children = {}
    for k in range(n):
        p = parents[k]
        if p is not None:
            children.setdefault(p, []).append(k)
    decoded = {}

    def dec(k):
        if k not in decoded:
            try:
                decoded[k] = decode(out[k]) if out[k] is not None else None
            except Exception:  # noqa: BLE001  # swallow-ok: an undecodable record declares nothing
                decoded[k] = None
        return decoded[k]

    def outermost_defaults(k):
        r = dec(k)
        if r is None or not r.entries or len(r.entries) < 2:
            return []
        return [(nm, dv) for nm, dv in r.entries[-1][0].pairs if dv and dv != "\n"]

    def visit(k):
        """Post-order: the subtree's defaults (name -> default, first in DFS) after the
        container ``k`` itself was rebuilt."""
        if k in skip:
            return []
        gathered = []
        for c in children.get(k, ()):
            gathered += visit(c)
        po = out[k]
        if po is None or not po.class_flag:
            return outermost_defaults(k) + gathered
        r = dec(k)
        if r is None or not r.entries or len(r.entries) < 2:
            return gathered
        bag = r.entries[-1][0]
        own = [(nm, dv) for nm, dv in bag.pairs]
        if depth[k] >= 1:
            # a nested genie's root or a container inside one keeps its element level's
            # declarations as stored (as CtDraw's load + save does); its defaults still
            # count for the containers above
            return [(nm, dv) for nm, dv in own if dv and dv != "\n"] + gathered
        if depth[k] == 0:
            # a direct container (a group with an animated property, a set, an owner):
            # its OWN tokens first (a group's VISIBLE '%Equipment%.CtrlMode'
            # declares Equipment)
            keep = [
                (nm, d if d is not None else "\n") for nm, d in GP.template_decls(r.entries[0][2]).items()
            ]
        else:
            # a nested genie's ROOT, or a container that is a MEMBER of a nested genie (a
            # symbol set, a shape owning a command, a group): the outer tokens its own
            # bindings name, as the member rule declares them (as CtDraw's save does)
            keep = (
                [(nm, d if d is not None else "\n") for nm, d in GP.template_decls(r.entries[-2][2]).items()]
                if len(r.entries) >= 3
                else []
            )
        names = {nm.lower(): i for i, (nm, _d) in enumerate(keep)}
        new = list(keep)
        for nm, dv in gathered:
            i = names.get(nm.lower())
            if i is None:
                names[nm.lower()] = len(new)
                new.append((nm, dv))
            elif not new[i][1] or new[i][1] == "\n":
                new[i] = (new[i][0], dv)
        # the names the container declares already keep their stored order, new ones
        # follow
        pos = {nm.lower(): i for i, (nm, _d) in enumerate(own)}
        new.sort(key=lambda t: (0, pos[t[0].lower()]) if t[0].lower() in pos else (1, 0))
        if new != own:
            r.entries[-1][0] = Bag(new, bag.ov)
            out[k] = to_po(encode(r))
            decoded.pop(k, None)
        return [(nm, dv) for nm, dv in new if dv and dv != "\n"]

    visit(root_index)


def _single_root(po, e) -> None:
    """A single-object element root (a .ctl bitmap / bar / trend): CtDraw
    stores slots (I, T(-e), I) and keeps an embedded bitmap's class-native
    rect = rect1."""
    (m1, L, _o, _n), (m2, S2, _o2, _n2), (m3, S3, _o3, _n3) = P.read_slots(po.body_raw)[0]
    want2 = (1, (1.0, 0.0, 0.0, 1.0, float(-e[0]), float(-e[1]))) if (e[0] or e[1]) else (0, P.IDENT)
    if (m1, m3) == (0, 0) and (m2, tuple(S2)) == (want2[0], tuple(want2[1])):
        return
    if m1 not in (0, 1) or abs(L[0] - 1) > 1e-9 or abs(L[3] - 1) > 1e-9 or L[1] or L[2]:
        return  # a genuinely transformed root: keep
    P.write_slots(po, [(0, P.IDENT), want2, (0, P.IDENT)])
    from ctgedit.format import writer

    if P.has_native_rect(po):
        gh = G.parse_geom_header(po.body_raw)
        o = P.class_section_offset(po)
        body = bytearray(po.body_raw)
        struct.pack_into("<4h", body, o, *G.norm_rect(gh.rect1))
        po.body_raw = bytes(body)
    writer.refresh_object_bags(po)


def _button(r) -> None:
    """A genie element button: caption from the template text."""
    from ctgedit.format import textmetrics as TM

    b1, b2 = r.entries[0][1], r.entries[0][2] if len(r.entries[0]) > 2 else Bag([])
    cap = b2.get("TEXT") if b2.get("TEXT") is not None else (b1.get("TEXT") or "")
    if r.dw & 0x80 and r.s80 is not None:
        r.s80 = GP._enc_lp(cap)
    if b1.get("WIDTH") is None:
        ex = TM.extent(cap or " ", "Arial", 11, 0, 1, 0, (0, 0))
        w = max(21, (ex[2] - ex[0]) + 11)
        l, t, rr, b = r.rect1
        d2 = r.rect2[2] - rr
        r.rect1 = (l, t, l + w, b)
        r.rect2 = (r.rect2[0], r.rect2[1], l + w + d2, r.rect2[3])
    if (r.dw & 2) and len(r.cls) >= 8:
        r.cls = r.cls[:-8] + struct.pack("<4h", *r.rect1)


def _text(r, po) -> None:
    """Rebuild a genie-element text's class section (string section + tail)."""
    from ctgedit.format import textmetrics as TM

    tail = G.parse_text_tail(po)
    if tail is None or tail.get("k_off") is None:
        return
    b1, b2 = r.entries[0][1], r.entries[0][2]
    body = po.body_raw
    L = CS.layout(po)
    cls_at = len(body) - len(r.cls)
    k_off, echo_off = tail["k_off"] - cls_at, tail["echo_off"] - cls_at
    s_text, s_face = tail["s_text"] - cls_at, tail["s_face"] - cls_at
    if k_off < 0:
        return
    caption_tmpl = b2.get("TXT_OFF")
    tail_text = tail["text"] if caption_tmpl is None else caption_tmpl
    # an unset substituted caption falls back to the tail (template) text
    # (LIVE DPSHeader '%Alarm Name%')
    sec = GP.text_strings(b1, tail_text)
    cls = bytearray(r.cls)
    # the page code page, losslessly (Python's cp1252 wrote '?' for the 5
    # bytes it leaves undefined, and for every other ANSI code page's text)
    enc = tail_text.encode(GP.CODEC, "replace")
    new = (
        bytes(cls[:k_off])
        + sec
        + bytes(cls[echo_off:s_text])
        + struct.pack("<H", len(enc))
        + enc
        + bytes(cls[s_face:])
    )
    r.cls = new
    # extent: props = the SUBSTITUTED bag, caption = the tail (template)
    # text (GP.engine_root_extent rule; LIVE mm_monitor TXTMODE 4)
    s = TM.measured_string(GP._Props(b1), tail_text)
    if s is not None:
        attribs = body[L["style_at"] + 3] if L and "style_at" in L else 0
        flags = body[L["style_at"] : L["style_at"] + 6] if L and "style_at" in L else None
        M = GP.flip_total(po)
        if M is not None:
            # a mirrored text: measured in its local frame, at its echo, and its box mapped
            # through its Total (textmetrics.text_extent's rule; LIVE, the fixture oracle's
            # chain/mirror_back)
            loc = TM.extent(
                s, tail["face"], tail["size"], attribs, tail["orient"], tail["charset"], tuple(tail["echo"])
            )
            new1 = GP.cut_box(M, loc)
            if r.rect1 is not None and new1 == tuple(r.rect1) and r.rect2 is not None:
                return
            r.rect1 = new1
            r.rect2 = GP.cut_box(M, tuple(G.predict_bounds(TEXT_TC, loc, flags, tail["size"]) or loc))
            return
        ext = TM.extent(
            s, tail["face"], tail["size"], attribs, tail["orient"], tail["charset"], tuple(r.anchor)
        )
        new1 = tuple(int(v) for v in ext)
        if r.rect1 is not None and new1 == tuple(r.rect1) and r.rect2 is not None:
            # the extent did not change: the stored bounds stay (as CtDraw's load +
            # save keeps them)
            return
        r.rect1 = new1
        r.rect2 = tuple(int(v) for v in (G.predict_bounds(TEXT_TC, ext, flags, tail["size"]) or ext))


def _alarm_grow(lib_path: str = "", resolver=None):
    """A group's bounds grown by its alarm indicator."""
    from . import alarmind as AI

    sizes: list = []

    def grow(po, bounds):
        if b"AI_ON" not in po.body_raw.upper():
            return bounds
        s = AI.settings(AI.props_of(po))
        if not s["on"]:
            return bounds
        if not sizes:
            try:
                sizes.append(AI.flag_sizes(lib_path, resolver) if lib_path else [AI.DEFAULT_FLAG])
            except Exception:  # noqa: BLE001 -- the default flag box still applies
                _note_exc("genie_norm._alarm_grow.grow")
                sizes.append([AI.DEFAULT_FLAG])
        return tuple(int(v) for v in AI.grown_bounds(tuple(bounds), s, sizes[0]))

    return grow


def _refit(recs, parents, root_index, grow=None, skip=frozenset()):
    """Containers = union of their children (symbol set: FIRST state); a
    group's Bounds also take its alarm indicator (``grow``, :func:`_alarm_grow`). ``skip``:
    records kept as they are (a template copy's: template_place composed it)."""
    recs = list(recs)
    n = len(recs)
    kids = [[] for _ in range(n)]
    for k in range(n):
        if parents[k] is not None:
            kids[parents[k]].append(k)
    for k in range(n - 1, -1, -1):
        po = recs[k]
        if not (po.class_flag and kids[k] and po.type_code in (GROUP_TC, SYMSET_TC)):
            continue
        if po.subtype_size & 0x1 or k in skip:
            continue  # a linked instance: keep (engine-derived)
        if po.type_code == SYMSET_TC:
            ks = [c for c in kids[k] if recs[c].type_code not in GP.UNION_SKIP] or kids[k]
            gh = G.parse_geom_header(recs[ks[0]].body_raw)
            r1, r2 = tuple(gh.rect1), tuple(gh.rect2)
        else:
            # (a DummyMarker counts in the Extent and, unless empty, the Bounds: LIVE,
            # CtDraw's save of fxparts.fx_dummy, geometry.group_union)
            ghs = [(recs[c].type_code, G.parse_geom_header(recs[c].body_raw)) for c in kids[k]]
            r1, r2 = G.group_union(
                [(tc, G.norm_rect(g.rect1), tuple(g.rect2)) for tc, g in ghs if g is not None]
            )
            if grow is not None:
                r2 = grow(po, r2)
        r = decode(po)
        if r.tc == GROUP_TC:
            r.anchor = (r1[0], r1[3] - 1)
        r.rect1, r.rect2 = r1, r2
        recs[k] = to_po(encode(r))
    return recs


# ------------------------------------------------------------ template edits
def set_template_property(po: PageObject, key: str, value: str) -> PageObject:
    """Set a genie member's property in its template bag."""
    r = decode(po)
    if r.entries is None:
        raise ValueError("record has no property bags")
    if len(r.entries) >= 2:
        r.entries[0][2].set(key, value)
    else:
        r.entries[0][1].set(key, value)
    return to_po(encode(r))


def needs_conversion(recs, new_value: str) -> bool:
    return (not is_genie_element(recs)) and bool(template_refs(Bag([("x", new_value)])))


# ------------------------------------------------------------ link table
def _current_entry(resolver, lib: str, elem: str, ext: str, cache: dict):
    """(guid, name) of a referenced element with its current stamp."""
    key = (lib.lower(), elem.lower(), ext.lower())
    if key in cache:
        return cache[key]
    ent = None
    if resolver is not None:
        try:
            libs = resolver.libraries(lib, ext)
            for L in libs:
                if L.element(elem) is not None:
                    ent = GP.current_link_entry(L.path, lib, elem)
                    break
        except Exception:  # noqa: BLE001
            _note_exc("genie_norm._current_entry")
            ent = None
    cache[key] = ent
    return ent


def rebuild_link_table(
    recs, old_entries, resolver=None, instantiated=(), keep_stale=(), page_like: bool = False
) -> List[Tuple[bytes, str]]:
    """Rebuild the element's link table the way CtDraw writes it."""
    from ctgedit.format import links
    from ctgedit.format.symbols import split_lib_element

    old: dict = {}
    for g, nm in old_entries:
        if nm.startswith("\\"):
            continue
        key = links.strip_stamp(nm).lower()
        # (a placeholder's bare entry beside a stamped one of the same genie: the stamped
        # one is the version a copy holds)
        if key not in old or (links.entry_stamp(old[key][1]) is None and links.entry_stamp(nm) is not None):
            old[key] = (g, nm)
    out: List[Tuple[bytes, str]] = []
    # (a placeholder's bare entry, a linked copy's entry and a label each have their own
    # seen set)
    seen, seen_bare, written = set(), set(), set()
    cache: dict = {}
    recs = list(recs)
    depth = nested_roots(recs, parents_of(recs)) if page_like else {}

    def put(ent):
        k = (ent[0], ent[1].lower())
        if k not in written:
            written.add(k)
            out.append(ent)

    for k, po in enumerate(recs):
        if po.type_code == 0xFFFF and not po.class_flag:
            # a PLACEHOLDER genie (CtDraw's save while its library did not open): the bare
            # name with a zero GUID, in record order (LIVE, oracle chain5/element_placeholder);
            # kept until the genie is placed again (it is the only trace of it)
            lib = str(po.raw_properties().get("_Library") or "").strip()
            if lib:
                bare = links.strip_stamp(lib)
                if bare.lower() not in seen_bare:
                    seen_bare.add(bare.lower())
                    put((bytes(16), bare))
            continue
        r = decode(po)
        if r.dw & 1 and r.link:
            key = r.link.lower()
            if key not in seen:
                seen.add(key)
                # the LAST dot before the extension (symbols._split_lib_element):
                # a library file named plant.v2.ctm keeps its entry
                stem, _, ext = r.link.rpartition(".")
                lib, elem = split_lib_element(stem)
                ent = None
                if (lib.lower(), elem.lower()) in keep_stale:
                    ent = old.get(key)
                if ent is None:
                    ent = _current_entry(resolver, lib, elem, ext.lstrip("<"), cache) or old.get(key)
                if ent is not None:
                    put(ent)
        for g, lab in GP.label_entries([po], template=not depth.get(k)):
            # (one label per native, case-insensitively: GP.label_key)
            if GP.label_key(lab) not in seen:
                seen.add(GP.label_key(lab))
                out.append((g, lab))
    for g, nm in old_entries:
        if "@super" in nm.lower() and links.strip_stamp(nm).lower() not in seen:
            base = links.strip_stamp(nm)
            lib, elem = split_lib_element(base)
            ent = _current_entry(resolver, lib, elem, "ctm", cache)
            made = base.lower() in instantiated
            g = ent[0] if (made and ent) else bytes(16)
            # the target's CURRENT stamp (stored one when unresolvable)
            out.append((g, "%s#%s" % (base, ent[1].rsplit("#", 1)[1])) if ent else (g, nm))
    return out


def link_table_bytes(area: str, entries) -> Tuple[bytes, int]:
    from ctgedit.format import links

    if not entries:
        return b"", 0
    return links.serialize_link_table(area, entries)
