"""Push our records through the Graphics Builder's PageOpen + PageSave and capture what it
writes back."""

from __future__ import annotations

import base64
import contextlib
import difflib
import json
import os
import shutil
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ctgedit.backend.graphics_builder import GraphicsBuilder  # noqa: E402
from ctgedit.format import factory, geometry as G, writer  # noqa: E402
from ctgedit.format.page import parse_ctg, parse_ctg_bytes, _parse_object  # noqa: E402
from ctgedit.io.citect_config import live_project_dir  # noqa: E402

CAP_DIR = os.path.join(os.path.dirname(__file__), "_ctdraw", "engine_caps")


# --------------------------------------------------------------------- engine
def roundtrip(
    mutate,
    element: str = "!PUMP_A_ACC",
    project: str = "Example",
    visible: bool = False,
    register_first: bool = False,
) -> dict:
    """Apply ``mutate`` and round-trip the page through the Graphics Builder."""
    pdir = live_project_dir(project)
    ctg = os.path.join(pdir, element + ".ctg")
    ctf = os.path.join(pdir, element + ".ctF")
    bak_ctg, bak_ctf = ctg + ".rtbak", ctf + ".rtbak"
    shutil.copy2(ctg, bak_ctg)
    had_ctf = os.path.exists(ctf)
    if had_ctf:
        shutil.copy2(ctf, bak_ctf)
    out = {
        "opened": False,
        "saved": False,
        "engine_rewrote": False,
        "records": {},
        "page": element,
        "project": project,
    }
    try:
        p = parse_ctg(bak_ctg)
        before = {G.get_handle(po): writer.object_payload(po) for po in p.page.objects}
        handles = mutate(p)
        p.page.object_count = len(p.page.objects)
        data = writer.serialize_ctg(p.container, p.page, p.attributes)
        ours = {G.get_handle(po): writer.object_payload(po) for po in parse_ctg_bytes(data).page.objects}
        if handles is None:
            handles = [h for h, pl in ours.items() if before.get(h) != pl]
        with open(ctg, "wb") as fh:
            fh.write(data)
        if had_ctf:
            os.remove(ctf)
        if register_first:
            from ctgedit.io.pgregister import register_page

            register_page(pdir, element, ctg, log_change=False, mark_uncompiled=False, require_page_row=False)
            out["registered_first"] = True
        with GraphicsBuilder(visible=visible) as gb:
            c = gb.com
            with contextlib.suppress(Exception):
                c.ProjectSelect(project)
            with contextlib.suppress(Exception):
                c.PageOpen(project, element)
            name = None
            with contextlib.suppress(Exception):
                name = str(c.PageName())
            out["opened"] = bool(name) and element.lower() in name.lower()
            try:
                c.PageSave()
                out["saved"] = True
            except Exception as e:  # noqa: BLE001
                out["save_error"] = str(e)
            with contextlib.suppress(Exception):
                c.PageClose()
        after = open(ctg, "rb").read()
        out["engine_rewrote"] = after != data
        eng = {G.get_handle(po): writer.object_payload(po) for po in parse_ctg_bytes(after).page.objects}
        for h in handles:
            out["records"][h] = (ours.get(h), eng.get(h))
        out["ours_bytes"], out["engine_bytes"] = data, after
    finally:
        shutil.copy2(bak_ctg, ctg)
        os.remove(bak_ctg)
        if had_ctf:
            shutil.copy2(bak_ctf, ctf)
            os.remove(bak_ctf)
        elif os.path.exists(ctf):
            with contextlib.suppress(OSError):
                os.remove(ctf)
        if out["saved"] or out.get("registered_first"):
            # CtDraw's PageSave (Studio running) also registered the page it
            # saved: the pg*.DBF rows now describe the mutated page. Put them
            # back in step with the restored .ctg (stale rows fail the compile).
            try:
                from ctgedit.io.pgregister import register_page

                register_page(
                    pdir, element, ctg, log_change=False, mark_uncompiled=False, require_page_row=False
                )
            except Exception as e:  # noqa: BLE001
                out["reregister_error"] = str(e)
                print("WARNING: could not re-register %s: %s" % (element, e))
        GraphicsBuilder.reap()  # stray builders only; Studio's own is kept
    return out


# --------------------------------------------------------------------- diffs
def _po(payload: bytes):
    return _parse_object(0, 0, len(payload), 0, payload)


def _hexdiff(a: bytes, b: bytes, limit: int = 12) -> list:
    """Opcode list of byte differences (offsets from start AND from end, since
    most tails are end-anchored)."""
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    ops = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        ops.append(
            {
                "op": tag,
                "ours_at": i1,
                "ours_from_end": len(a) - i1,
                "eng_at": j1,
                "ours": a[i1:i2].hex(),
                "eng": b[j1:j2].hex(),
            }
        )
        if len(ops) >= limit:
            break
    return ops


def field_diff(ours: bytes, eng: bytes | None) -> dict:
    """Structured description of how the engine rewrote one record."""
    if eng is None:
        return {"dropped": True}
    if ours == eng:
        return {}
    a, b = _po(ours), _po(eng)
    d = {}
    for f in ("class_flag", "type_code", "subtype_size", "an_str"):
        va, vb = getattr(a, f), getattr(b, f)
        if va != vb:
            d[f] = [hex(va) if isinstance(va, int) else va, hex(vb) if isinstance(vb, int) else vb]
    ga, gb = G.parse_geom_header(a.body_raw), G.parse_geom_header(b.body_raw)
    if ga and gb:
        for f in ("anchor", "rect1", "rect2", "name"):
            if getattr(ga, f) != getattr(gb, f):
                d["geom." + f] = [getattr(ga, f), getattr(gb, f)]
        if a.body_raw[: ga.end] != b.body_raw[: gb.end] and not any(k.startswith("geom.") for k in d):
            d["geom.header_bytes"] = _hexdiff(a.body_raw[: ga.end], b.body_raw[: gb.end])
        rest_a, rest_b = a.body_raw[ga.end :], b.body_raw[gb.end :]
    else:
        rest_a, rest_b = a.body_raw, b.body_raw
    pa, pb = a.raw_properties(), b.raw_properties()
    if pa != pb:
        d["bag"] = {
            "added": {k: pb[k] for k in pb if k not in pa},
            "removed": {k: pa[k] for k in pa if k not in pb},
            "changed": {k: [pa[k], pb[k]] for k in pa if k in pb and pa[k] != pb[k]},
        }
    ca = G.find_colour_block_exact(a) if (a.subtype_size & 2) else None
    cb = G.find_colour_block_exact(b) if (b.subtype_size & 2) else None
    if (ca is None) != (cb is None) or (ca and cb and (ca.flags != cb.flags or ca.slots != cb.slots)):
        d["style_block"] = [
            None
            if ca is None
            else {"at": ca.offset, "flags": ca.flags.hex(), "slots": [(hex(x), hex(y)) for x, y in ca.slots]},
            None
            if cb is None
            else {"at": cb.offset, "flags": cb.flags.hex(), "slots": [(hex(x), hex(y)) for x, y in cb.slots]},
        ]
    if rest_a != rest_b:
        d["body_after_geom"] = {
            "ours_len": len(rest_a),
            "eng_len": len(rest_b),
            "ops": _hexdiff(rest_a, rest_b),
        }
    return d


# --------------------------------------------------------------------- cases
FACTORY_CASES = [
    ("line", dict(nodes=[(10, 10), (90, 50)], colour=0xFF0000)),
    ("rectangle", dict(rect=(100, 10, 160, 50), colour=0x00FF00)),
    ("rectangle_outline", dict(rect=(170, 10, 230, 50), colour=0x4080FF)),
    ("ellipse", dict(rect=(10, 60, 70, 110), colour=0xFFA000)),
    ("ellipse_outline", dict(rect=(80, 60, 140, 110), colour=0x00FFFF)),
    ("polygon", dict(nodes=[(150, 60), (230, 70), (210, 110), (160, 105)], colour=0xA040FF)),
    ("polyline", dict(nodes=[(20, 140), (60, 120), (90, 150)], colour=0x202020)),
    ("pipe", dict(nodes=[(10, 130), (120, 130), (120, 180)], colour=0x808080)),
    ("text", dict(rect=(140, 125, 300, 145), text="Hello ctgedit", colour=0x000000)),
    ("number", dict(rect=(140, 150, 220, 170), text="PUMP_A_SPEED")),
    ("button", dict(rect=(150, 160, 250, 195), text="Press me")),
    ("symbol", dict(rect=(260, 60, 324, 124), symbol=("servermon", "no", "ctl"))),
    ("string", dict(rect=(10, 200, 110, 220), text="Two state")),
    ("bar_graph", dict(rect=(260, 130, 290, 220))),
    ("slider", dict(rect=(295, 130, 325, 220))),
    ("animated_symbol", dict(rect=(240, 10, 300, 50), symbol=("icons", "help1", "ctl"))),
    ("cicode_object", dict(rect=(10, 190, 50, 225))),
    ("animation_keyboard", dict(rect=(60, 190, 90, 220))),
    ("trend", dict(rect=(120, 150, 300, 225))),
    ("activex", dict(rect=(10, 10, 90, 90))),
    ("process_analyst", dict(rect=(100, 10, 320, 120))),
    ("scheduler", dict(rect=(10, 100, 150, 200))),
    ("web_content", dict(rect=(160, 130, 320, 220))),
    ("digital_text", dict(rect=(10, 230, 110, 250))),
    ("color_flood", dict(rect=(120, 230, 180, 290))),
    # on no page the install ships (factory.CONTROL_SOURCES): skipped
    ("db_exchange", dict(rect=(190, 230, 390, 330))),
    ("pelco", dict(rect=(10, 300, 170, 420))),
]


def _link_info(symbol, element="!PUMP_A_ACC", project="Example"):
    """The resolved library element a placed symbol's Local derives from."""
    from ctgedit.format import placement
    from ctgedit.format.symbols import SymbolResolver

    res = SymbolResolver.for_page(os.path.join(live_project_dir(project), element + ".ctg"))
    return placement.element_info(res, *symbol)


def factory_mutator(kinds=None, base_handle: int = 900):
    def mutate(p):
        from ctgedit.format import textmetrics
        from ctgedit.io.project import Project

        fmts = {t.name.lower(): t.format for t in Project(live_project_dir("Example")).variable_tags()}
        textmetrics.use_tag_formats(lambda n: fmts.get(n.strip().lower()))
        hs = []
        h = base_handle
        for kind, kw in FACTORY_CASES:
            if kinds and kind not in kinds:
                continue
            if kind == "symbol":
                kw = dict(kw, link_info=_link_info(kw["symbol"]))
            try:
                po = factory.create_object(kind, handle=h, **kw)
            except Exception as e:  # noqa: BLE001 -- kind without a seed
                print("  skip %s: %s" % (kind, e))
                continue
            p.page.objects.append(po)
            hs.append(h)
            h += 1
        po = factory.make_bitmap_record(h, (95, 95, 103, 103), bytes([255, 0, 0, 255]) * 64, 8, 8)
        p.page.objects.append(po)
        hs.append(h)
        po = factory.make_bitmap_record(
            h + 1,
            (105, 95, 113, 103),
            bytes([255, 0, 0, 255]) * 64,
            8,
            8,
            flash_rgba=bytes([0, 255, 0, 255]) * 64,
        )
        p.page.objects.append(po)
        hs.append(h + 1)
        return hs

    return mutate


def save_capture(name: str, cap: dict, labels: dict | None = None) -> str:
    os.makedirs(CAP_DIR, exist_ok=True)
    recs = {}
    for h, (ours, eng) in cap["records"].items():
        recs[str(h)] = {
            "label": (labels or {}).get(h, ""),
            "ours": base64.b64encode(ours).decode() if ours else None,
            "engine": base64.b64encode(eng).decode() if eng else None,
            "fixed_point": ours == eng,
            "diff": field_diff(ours, eng) if ours else {"missing_ours": True},
        }
    doc = {k: v for k, v in cap.items() if k not in ("records", "ours_bytes", "engine_bytes")}
    doc["records"] = recs
    path = os.path.join(CAP_DIR, name + ".json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, default=str)
    return path


def control_mutator(p):
    """No change: which records does the engine normalise on its own?"""
    return [G.get_handle(po) for po in p.page.objects]


# one edit per record so every engine diff is attributable to exactly one op
EDIT_OPS = [
    ("move", lambda doc, i: doc.move_objects([i], 7, 5)),
    ("resize", lambda doc, i: doc.set_object_rect(i, _grow(doc, i))),
    ("line_width", lambda doc, i: doc.set_line_width(i, 3)),
    ("line_style", lambda doc, i: doc.set_line_style(i, 1)),
    ("fill_colour", lambda doc, i: doc.set_colour(i, 0x3366CC)),
    ("line_colour", lambda doc, i: doc.set_colour(i, 0xCC3300, line=True)),
    ("text", lambda doc, i: doc.set_text(i, "Edited text")),
    ("font_size", lambda doc, i: doc.set_font(i, size=14)),
]


def _grow(doc, i):
    g = doc.page.objects[i].geometry
    return (g.x, g.y, g.x + g.extent_x + 10, g.y + g.extent_y + 6)


def _dv(doc, i, **change):
    """One Display Value page Apply (EditableDocument.apply_text_display)
    on text ``i``: ``change`` sets fields of the page as it shows them --
    mode, expression, on_text, static, fmt, conditions, states / array
    ({row: text})."""
    from ctgedit.format import dspval as D

    po = doc.parsed.page.objects[i]
    if po.type_code != 0x05 or doc.page.objects[i].raw.get("_from_template"):
        raise ValueError("not a free text")
    before = doc.text_display(i)
    after = before.copy()
    for k, v in change.items():
        if k in ("states", "array"):
            continue
        setattr(after, k, list(v) if isinstance(v, list) else v)
    if "conditions" in change:
        after.expression = after.conditions[0]
        after.states = D.resized_states(after.states, len(after.conditions))
    for row, text in change.get("states", {}).items():
        after.states[row] = text
    for row, text in change.get("array", {}).items():
        after.array[row] = text
    if doc.apply_text_display(i, before, after) is False:
        raise ValueError("not stored")


# Appearance > Display Value (``dspval`` mode): every type, CtDraw's apply
DSPVAL_OPS = [
    ("dv_onoff", lambda d, i: _dv(d, i, mode=0, expression="1", on_text="ctgedit ON", static="ctgedit OFF")),
    (
        "dv_multi3",
        lambda d, i: _dv(
            d, i, mode=1, conditions=["1", "0", "1"], states={1: "S1", 3: "S3\r\nline 2", 5: "S5"}
        ),
    ),
    ("dv_array", lambda d, i: _dv(d, i, mode=2, expression="2", array={1: "A1", 2: "A2"})),
    ("dv_numeric", lambda d, i: _dv(d, i, mode=3, expression="1+2", fmt="#.##")),
    ("dv_string", lambda d, i: _dv(d, i, mode=4, expression='"ctgedit"')),
    # a blank text + one condition: the string section '05000000 0000 0200 0000 0000',
    # then a 2nd edit
    (
        "dv_multi_blank",
        lambda d, i: (_dv(d, i, mode=1, conditions=["1"], static=""), _dv(d, i, states={1: "One"})),
    ),
]


# Arrange > Rotate / Mirror (``turns`` mode): single objects of every kind and
# whole units (groups / genies / symbol sets)
TURN_OPS = [
    ("rotate_cw", lambda doc, i: doc.rotate_objects([i], clockwise=True)),
    ("rotate_acw", lambda doc, i: doc.rotate_objects([i], clockwise=False)),
    ("mirror_h", lambda doc, i: doc.mirror_objects([i], horizontal=True)),
]
UNIT_OPS = [
    ("move_unit", lambda d, i: d.move_objects([i], 9, 4)),
    ("resize_unit", lambda d, i: d.set_object_rect(i, _grow(d, i))),
]
TURN_UNIT_OPS = [
    ("rotate_unit", lambda d, i: d.rotate_objects([i], clockwise=True)),
    (
        "rotate_unit_180",
        lambda d, i: (d.rotate_objects([i], clockwise=True), d.rotate_objects([i], clockwise=True)),
    ),
    ("mirror_unit", lambda d, i: d.mirror_objects([i], horizontal=True)),
]


def edit_mutator(page_path, per_kind: int = 2, log=print, ops=None, unit_ops=None):
    """Apply each EDIT_OP (or ``ops``) to ``per_kind`` root objects of every
    kind, then each unit op to whole groups / genies, through the real
    EditableDocument API; returns (mutate, labels)."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(page_path)
    labels = {}
    done = set()
    for name, op in EDIT_OPS if ops is None else ops:
        used = {}
        for i, o in enumerate(doc.page.objects):
            if o.parent is not None or i in done or o.members:
                continue
            k = o.kind.value
            if used.get(k, 0) >= per_kind:
                continue
            before = writer.object_payload(doc.parsed.page.objects[i])
            try:
                op(doc, i)
            except Exception as e:  # noqa: BLE001 -- op n/a for this kind
                log("  %s n/a on [%d] %s: %s" % (name, i, k, e))
                continue
            if writer.object_payload(doc.parsed.page.objects[i]) == before:
                continue
            used[k] = used.get(k, 0) + 1
            done.add(i)
            labels[G.get_handle(doc.parsed.page.objects[i])] = "%s:%s[%d]" % (name, k, i)
    # whole UNITS (groups / genies / symbol sets): the subtree moves with its
    # root -- every record of the subtree must stay an engine fixed point
    for name, op in UNIT_OPS if unit_ops is None else unit_ops:
        n = 0
        for i, o in enumerate(doc.page.objects):
            if o.parent is not None or not o.members or i in done or n >= per_kind * 3:
                continue
            if o.raw.get("_from_template"):
                continue  # template records are loaded from the template (locked)
            sub = doc.page.subtree(i) if hasattr(doc.page, "subtree") else [i]
            if any(j in done for j in sub):
                continue
            before = [writer.object_payload(doc.parsed.page.objects[j]) for j in sub]
            try:
                op(doc, i)
            except Exception as e:  # noqa: BLE001
                log("  %s n/a on [%d] %s: %s" % (name, i, o.kind.value, e))
                continue
            after = [writer.object_payload(doc.parsed.page.objects[j]) for j in sub]
            if after == before:
                continue
            n += 1
            for j in sub:
                done.add(j)
                h = G.get_handle(doc.parsed.page.objects[j])
                labels.setdefault(h, "%s:%s[%d]<%d" % (name, doc.page.objects[j].kind.value, j, i))
    edited = list(doc.parsed.page.objects)

    def mutate(p):
        p.page.objects[:] = edited
        return list(labels)

    return mutate, labels


# Appearance > General / 3D Effects fields, one NEW object per case so
# every engine diff is attributable to exactly one field edit
def _btn_face(**kw):
    return lambda d, i: d.set_button_face(i, **kw)


def _both(*ops):
    def run(d, i):
        for op in ops:
            op(d, i)

    return run


_LINE = dict(nodes=[(0, 0), (60, 30)])
_BOX = dict(rect=(0, 0, 80, 40), colour=0x3366CC)
_POLY = dict(nodes=[(0, 40), (40, 0), (80, 40)], colour=0xCC9933)
_PIPE = dict(nodes=[(0, 20), (60, 20), (60, 50)])
_TEXT = dict(rect=(0, 0, 60, 20), text="Appearance")
_BTN = dict(rect=(0, 0, 90, 30), text="Go")
APPEARANCE_CASES = [
    ("line_colour", "line", _LINE, lambda d, i: d.set_colour_slot(i, G.SLOT_LINE, 0xCC0000)),
    (
        "line_colour_flashing",
        "line",
        _LINE,
        lambda d, i: d.set_colour_slot(i, G.SLOT_LINE, 0xCC0000, 0x0000CC),
    ),
    ("line_dash_dot", "line", _LINE, lambda d, i: d.set_line_style(i, 3)),
    ("line_dash_dot_dot", "line", _LINE, lambda d, i: d.set_line_style(i, 4)),
    ("line_raised_2", "line", _LINE, lambda d, i: d.set_effect(i, 1, 2)),
    (
        "rect_line_and_fill",
        "rectangle",
        _BOX,
        _both(
            lambda d, i: d.set_colour_slot(i, G.SLOT_LINE, 0xFF0000),
            lambda d, i: d.set_colour_slot(i, G.SLOT_FILL, 0x00FF00),
        ),
    ),
    (
        "rect_fill_flashing",
        "rectangle",
        _BOX,
        lambda d, i: d.set_colour_slot(i, G.SLOT_FILL, 0x00FF00, 0x0000FF),
    ),
    ("rect_raised_2", "rectangle", _BOX, lambda d, i: d.set_effect(i, 1, 2)),
    ("rect_lowered_auto", "rectangle", _BOX, lambda d, i: d.set_effect(i, 2, -1)),
    (
        "rect_lowered_border_extra",
        "rectangle",
        _BOX,
        _both(lambda d, i: d.set_effect(i, 2, 1), lambda d, i: d.set_rectangle_style(i, 3)),
    ),
    (
        "rect_shadowed_colour",
        "rectangle",
        _BOX,
        _both(lambda d, i: d.set_effect(i, 3, 3), lambda d, i: d.set_colour_slot(i, G.SLOT_SHADOW, 0x404040)),
    ),
    (
        "rect_hilight_lolight",
        "rectangle",
        _BOX,
        _both(
            lambda d, i: d.set_effect(i, 1, 1),
            lambda d, i: d.set_colour_slot(i, G.SLOT_HILIGHT, 0xFFFFCC),
            lambda d, i: d.set_colour_slot(i, G.SLOT_LOLIGHT, 0x333300),
        ),
    ),
    ("rect_embossed_auto", "rectangle", _BOX, lambda d, i: d.set_effect(i, 4, -1)),
    (
        "rect_outline_raised_big",
        "rectangle_outline",
        dict(rect=(0, 0, 400, 200), colour=0x000000),
        lambda d, i: d.set_effect(i, 1, -1),
    ),
    (
        "rect_radius_border",
        "rectangle",
        _BOX,
        _both(lambda d, i: d.set_corner_radius(i, 6), lambda d, i: d.set_rectangle_style(i, 1)),
    ),
    ("rect_gradient_colour", "rectangle", _BOX, lambda d, i: d.set_gradient(i, 3, 0xFFFFFF)),
    ("rect_gradient_flashing", "rectangle", _BOX, lambda d, i: d.set_gradient(i, 1, 0xFF0000, 0x00FF00)),
    ("ellipse_lowered_2", "ellipse", _BOX, lambda d, i: d.set_effect(i, 2, 2)),
    ("ellipse_pie_50_150", "ellipse", _BOX, lambda d, i: d.set_ellipse(i, style=1, start=50, end=150)),
    ("ellipse_arc_270_150", "ellipse", _BOX, lambda d, i: d.set_ellipse(i, style=2, start=270, end=150)),
    ("ellipse_line_colour", "ellipse", _BOX, lambda d, i: d.set_colour_slot(i, G.SLOT_LINE, 0x00FFFF)),
    ("polygon_shadowed_2", "polygon", _POLY, lambda d, i: d.set_effect(i, 3, 2)),
    ("polygon_embossed", "polygon", _POLY, lambda d, i: d.set_effect(i, 4, -1)),
    (
        "pipe_hilight_lolight",
        "pipe",
        _PIPE,
        _both(
            lambda d, i: d.set_colour_slot(i, G.SLOT_HILIGHT, 0xFFFF00),
            lambda d, i: d.set_colour_slot(i, G.SLOT_LOLIGHT, 0x404000),
        ),
    ),
    ("text_foreground", "text", _TEXT, lambda d, i: d.set_foreground(i, 0xCC0000)),
    ("text_script_cyrillic", "text", _TEXT, lambda d, i: d.set_text_charset(i, 204)),
    (
        "text_font",
        "text",
        _TEXT,
        lambda d, i: d.set_font(i, face="Tahoma", size=14, bold=True, justify="centre"),
    ),
    ("text_raised", "text", _TEXT, lambda d, i: d.set_effect(i, 1, -1)),
    (
        "button_font",
        "button",
        _BTN,
        lambda d, i: d.set_font(i, face="Tahoma", size=12, italic=True, underline=True, justify="left"),
    ),
    ("button_foreground", "button", _BTN, lambda d, i: d.set_foreground(i, 0xCC0000)),
    ("button_script_cyrillic", "button", _BTN, lambda d, i: d.set_text_charset(i, 204)),
    (
        "button_custom_fill",
        "button",
        _BTN,
        _btn_face(
            type="text",
            caption="Go",
            xp=True,
            custom_fill=True,
            up=(0x3366CC, 0x3366CC),
            down=(0x112244, 0x112244),
        ),
    ),
    (
        "button_custom_fill_off",
        "button",
        _BTN,
        _both(
            _btn_face(
                type="text",
                caption="Go",
                xp=True,
                custom_fill=True,
                up=(0x3366CC, 0x3366CC),
                down=(0x112244, 0x112244),
            ),
            _btn_face(type="text", caption="Go", xp=True, custom_fill=False),
        ),
    ),
    ("button_normal", "button", _BTN, _btn_face(type="text", caption="Go", xp=False)),
    ("button_target_border_3d", "button", _BTN, _btn_face(type="target", mode="BORDER_3D")),
    ("button_target_border", "button", _BTN, _btn_face(type="target", mode="BORDER")),
    ("button_symbol", "button", _BTN, _btn_face(type="symbol", symbol="icons.note")),
    ("button_caret_n", "button", _BTN, lambda d, i: d.set_text(i, "Start^nMotor")),
    ("button_two_lines", "button", _BTN, lambda d, i: d.set_text(i, "Start\nMotor")),
    ("button_raised", "button", _BTN, lambda d, i: d.set_effect(i, 1, -1)),
    # removing WIDTH re-sizes the button to its caption and stores the new width
    # (CtDraw's page load keeps no auto-width state; its save writes WIDTH)
    (
        "button_auto_width",
        "button",
        _BTN,
        _both(
            lambda d, i: d.set_text(i, "An auto-width caption"), lambda d, i: d.remove_property(i, "WIDTH")
        ),
    ),
]


def appearance_mutator(page_path, only=None, log=print):
    """Create one new object per APPEARANCE_CASES entry (a column down the
    page) and apply its field edit through the real EditableDocument API;
    returns (mutate, {handle: label})."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(page_path)
    labels = {}
    for n, (label, kind, kw, op) in enumerate(APPEARANCE_CASES):
        if only and label not in only:
            continue
        dx, dy = 20 + (n % 4) * 180, 20 + (n // 4) * 60
        kw = dict(kw)
        if "rect" in kw:
            l, t, r, b = kw["rect"]
            kw["rect"] = (l + dx, t + dy, r + dx, b + dy)
        if "nodes" in kw:
            kw["nodes"] = [(x + dx, y + dy) for x, y in kw["nodes"]]
        try:
            i = doc.create_object(kind, **kw)
            op(doc, i)
        except Exception as e:  # noqa: BLE001
            log("  %s failed: %s" % (label, e))
            continue
        labels[G.get_handle(doc.parsed.page.objects[i])] = label
    edited = list(doc.parsed.page.objects)

    def mutate(p):
        p.page.objects[:] = edited
        return list(labels)

    return mutate, labels


# bitmaps and import: one NEW picture per case, edited through the real
# EditableDocument API (the Bitmap Editor's save, Swap / Adjust Colors, the
# import of a .wmf); every byte outside a changed DIB must survive the engine
def _solid(rgb, n, alpha=255):
    return bytes(tuple(rgb) + (alpha,)) * n


def _test_wmf() -> bytes:
    """A placeable 2 x 1 inch WMF: a filled rectangle + an ellipse."""
    recs = (
        struct.pack("<IH4h", 7, 0x041B, 1440, 2880, 0, 0)  # Rectangle
        + struct.pack("<IH4h", 7, 0x0418, 1200, 1800, 240, 360)  # Ellipse
        + struct.pack("<IH", 3, 0)
    )
    hdr = struct.pack("<HHHIHIH", 2, 9, 0x300, (18 + len(recs)) // 2, 0, 7, 0)
    ph = struct.pack("<IH4hHI", 0x9AC6CDD7, 0, 0, 0, 2880, 1440, 1440, 0)
    chk = 0
    for k in range(0, 20, 2):
        chk ^= struct.unpack_from("<H", ph, k)[0]
    return ph + struct.pack("<H", chk) + hdr + recs


def _bmp(flash=False, size=8):
    def make(d, at):
        return d.add_bitmap_object(
            (at[0], at[1], at[0] + size, at[1] + size),
            _solid((255, 0, 0), size * size),
            size,
            size,
            flash_rgba=_solid((0, 0, 255), size * size) if flash else None,
        )

    return make


def _wmf(d, at):
    from ctgedit.format.bitmaps import wmf_from_file

    wmf, w, h = wmf_from_file(_test_wmf())
    return d.add_metafile_object((at[0], at[1], at[0] + w, at[1] + h), wmf)


def _bmp_grow(d, i):
    d.ensure_property(i, "VISIBLE", "1")
    g = G.parse_geom_header(d.parsed.page.objects[i].body_raw).rect1
    d.replace_bitmap(i, (g[0], g[1], g[0] + 10, g[1] + 9), _solid((0, 128, 0), 90), 10, 9)


def _bmp_colour_state_grow(d, i):
    # a COL_MAXON colour-state section (FLAG 0x23) moves the class section:
    # the grown extent must land in the native rect, not the style block
    d.ensure_property(i, "COL_MAXON", "0x0000ff00,0x0000ff00")
    d.replace_bitmap(i, None, _solid((0, 128, 0), 90), 10, 9)


BITMAP_CASES = [
    (
        "bmp_edit_primary",
        _bmp(flash=True),
        lambda d, i: d.replace_bitmap(i, None, _solid((0, 255, 0), 64), 8, 8),
    ),
    (
        "bmp_edit_flash_only",
        _bmp(flash=True),
        lambda d, i: d.replace_bitmap(
            i, None, _solid((255, 0, 0), 64), 8, 8, flash_rgba=_solid((255, 255, 0), 64)
        ),
    ),
    (
        "bmp_make_flashing",
        _bmp(),
        lambda d, i: d.replace_bitmap(
            i, None, _solid((255, 0, 0), 64), 8, 8, flash_rgba=_solid((0, 0, 0), 64)
        ),
    ),
    (
        "bmp_unflash",
        _bmp(flash=True),
        lambda d, i: d.replace_bitmap(i, None, _solid((255, 0, 0), 64), 8, 8, flash_rgba=None),
    ),
    ("bmp_visible_grow", _bmp(), _bmp_grow),
    ("bmp_colour_state_grow", _bmp(), _bmp_colour_state_grow),
    (
        "bmp_transparent_pixels",
        _bmp(),
        lambda d, i: d.replace_bitmap(i, None, _solid((255, 0, 0), 32) + bytes(128), 8, 8),
    ),
    ("bmp_swap_colours", _bmp(flash=True), lambda d, i: d.swap_colours([i], 0xFF0000, 0x00FF00)),
    ("bmp_adjust_colours", _bmp(), lambda d, i: d.adjust_colours([i], lambda c: c >> 1 & 0x7F7F7F)),
    ("wmf_import", _wmf, lambda d, i: None),
]


# the colour tools: Swap / Adjust Colors with CtDraw's arithmetic, flashing
# and Transparent picks, each on a NEW object
def _c_rect(colour=0xFF0000, animated=False):
    def make(d, at):
        i = d.create_object("rectangle", (at[0], at[1], at[0] + 60, at[1] + 40), None, colour=colour)
        if animated:
            d.ensure_property(i, "COL_A", "Tank_Level > 50")
            d.ensure_property(i, "COL_MAXON", "0x0000ff00,0x0000ff00")
        return i

    return make


def _c_line(d, at):
    return d.create_object("line", None, [(at[0], at[1]), (at[0] + 80, at[1] + 30)], colour=0x0000FF)


def _c_pipe(d, at):
    return d.create_object("pipe", None, [(at[0], at[1] + 20), (at[0] + 90, at[1] + 20)], colour=0x00FF00)


def _c_text(d, at):
    return d.create_object(
        "text", (at[0], at[1], at[0] + 60, at[1] + 20), None, colour=0x0000FF, text="Colour"
    )


def _c_adjust(**kw):
    from ctgedit.format import colourtools as T

    return lambda d, i: d.adjust_colours([i], T.Adjustment.from_dialog(**kw))


COLOUR_CASES = [
    ("col_swap_flashing", _c_rect(), lambda d, i: d.swap_colours([i], 0xFF0000, (0x00FF00, 0x0000FF))),
    ("col_swap_any", _c_rect(0x123456), lambda d, i: d.swap_colours([i], None, 0x00AA55)),
    (
        "col_swap_range",
        _c_rect(0x800000),
        lambda d, i: d.swap_colours([i], 0xFF0000, 0x00FF00, swap_range=True),
    ),
    ("col_swap_animated", _c_rect(animated=True), lambda d, i: d.swap_colours([i], 0xFF0000, 0x0000FF)),
    ("col_fill_transparent", _c_rect(0x336699), lambda d, i: d.set_colour_slot(i, 1, 0xFFFFFFFF)),
    ("col_line_transparent", _c_line, lambda d, i: d.set_colour_slot(i, 0, 0xFFFFFFFF)),
    ("col_pipe_flashing", _c_pipe, lambda d, i: d.set_pipe_colour(i, 0xFF0000, 0x0000FF)),
    ("col_adjust_hue", _c_rect(), _c_adjust(hue_from=0, hue_to=359, to_start=120, lightness=0, saturation=0)),
    (
        "col_adjust_light_sat",
        _c_text,
        _c_adjust(hue_from=200, hue_to=280, to_start=200, lightness=30, saturation=-40),
    ),
    (
        "col_adjust_selected_hues",
        _c_rect(0x00C000),
        _c_adjust(hue_from=90, hue_to=150, to_start=180, lightness=-50, saturation=0, light_selected=True),
    ),
]


def colours_mutator(page_path, only=None, log=print):
    """One object per COLOUR_CASES entry, recoloured through the real
    EditableDocument API; returns (mutate, labels)."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(page_path)
    labels = {}
    for n, (label, make, op) in enumerate(COLOUR_CASES):
        if only and label not in only:
            continue
        at = (20 + (n % 4) * 110, 150 + (n // 4) * 70)
        try:
            i = make(doc, at)
            op(doc, i)
        except Exception as e:  # noqa: BLE001
            log("  %s failed: %s" % (label, e))
            continue
        labels[G.get_handle(doc.parsed.page.objects[i])] = label
    edited = list(doc.parsed.page.objects)

    def mutate(p):
        p.page.objects[:] = edited
        return list(labels)

    return mutate, labels


def bitmaps_mutator(page_path, only=None, log=print):
    """Create one picture per BITMAP_CASES entry (a column down the page) and
    edit it through the real EditableDocument API; returns (mutate, labels)."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(page_path)
    labels = {}
    for n, (label, make, op) in enumerate(BITMAP_CASES):
        if only and label not in only:
            continue
        at = (20 + (n % 4) * 220, 20 + (n // 4) * 120)
        try:
            i = make(doc, at)
            op(doc, i)
        except Exception as e:  # noqa: BLE001
            log("  %s failed: %s" % (label, e))
            continue
        labels[G.get_handle(doc.parsed.page.objects[i])] = label
    edited = list(doc.parsed.page.objects)

    def mutate(p):
        p.page.objects[:] = edited
        return list(labels)

    return mutate, labels


# the special objects: one NEW object per case, set through the real
# EditableDocument API (the Trend / Cicode / Web Content / Tag Association pages)
def _trend_tags(n=2):
    from ctgedit.io.project import trend_tags

    names = [t.name for t in trend_tags(live_project_dir("Example"))][:n]
    return names + [""] * (n - len(names))


def _var_tag():
    from ctgedit.io.project import Project

    tags = [t.name for t in Project(live_project_dir("Example")).variable_tags()]
    return tags[0] if tags else "TT001"


def _trend(d, at, w=200, h=100):
    return d.create_object("trend", (at[0], at[1], at[0] + w, at[1] + h))


def _trend_pens(d, i):
    a, b = _trend_tags(2)
    d.set_trend_properties(i, pens=[(a, (0x0000FF, 0x0000FF)), (b, None)], periodic=True)


def _cicode(d, at):
    return d.create_object("cicode_object", (at[0], at[1] - 16, at[0] + 31, at[1] + 1))


def _donor_control(d, at):
    from ctgedit.io.activex import project_controls

    donors = project_controls(live_project_dir("Example"))
    if not donors:
        raise RuntimeError("no ActiveX control on the Example pages")
    return d.create_control(
        donors[0].path, donors[0].index, (at[0], at[1], at[0] + 160, at[1] + 90), donors[0].clsid
    )


SPECIAL_CASES = [
    ("trend_two_pens", _trend, _trend_pens),
    ("trend_samples_spacing", _trend, lambda d, i: d.set_trend_properties(i, samples=40, spacing=3)),
    ("trend_colours_only", _trend, lambda d, i: d.set_trend_properties(i, pens=[("", (0xFF00FF, 0x00FF00))])),
    (
        "trend_periodic_false_cmd",
        _trend,
        lambda d, i: d.set_trend_properties(i, periodic=False, command="TrendRun();"),
    ),
    (
        "web_url_print",
        lambda d, at: d.create_object("web_content", (at[0], at[1], at[0] + 200, at[1] + 100)),
        lambda d, i: d.set_web_content(i, url="https://www.aveva.com/", enable_print=True),
    ),
    (
        "web_url_only",
        lambda d, at: d.create_object("web_content", (at[0], at[1], at[0] + 200, at[1] + 100)),
        lambda d, i: d.set_web_content(i, url="https://localhost:5000/"),
    ),
    ("cicode_command", _cicode, lambda d, i: d.set_cicode_object(i, command="Beep();\r\nSleep(1)")),
    ("cicode_symbol", _cicode, lambda d, i: d.set_cicode_object(i, symbol="product.logo")),
    (
        "cicode_keyboard",
        _cicode,
        lambda d, i: d.set_input_commands(i, keys=[{"KEY_SEQ": "F2", "CMD": "Beep()"}]),
    ),
    (
        "activex_tag_association",
        lambda d, at: d.create_object("activex", (at[0], at[1], at[0] + 160, at[1] + 160)),
        lambda d, i: d.set_activex_associations(i, [("Value", _var_tag(), "")]),
    ),
    ("activex_copied_control", _donor_control, lambda d, i: None),
]


def special_mutator(page_path, only=None, log=print):
    """Create one special object per SPECIAL_CASES entry (a grid down the
    page) and set it through the EditableDocument API; returns (mutate,
    {handle: label})."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(page_path)
    labels = {}
    for n, (label, make, op) in enumerate(SPECIAL_CASES):
        if only and label not in only:
            continue
        at = (20 + (n % 4) * 240, 40 + (n // 4) * 180)
        try:
            i = make(doc, at)
            op(doc, i)
        except Exception as e:  # noqa: BLE001
            log("  %s failed: %s" % (label, e))
            continue
        labels[G.get_handle(doc.parsed.page.objects[i])] = label
    edited = list(doc.parsed.page.objects)

    def mutate(p):
        p.page.objects[:] = edited
        return list(labels)

    return mutate, labels


# node editing: one NEW object per case, reshaped through the real
# EditableDocument API (the canvas drag / Insert / Delete / Vertices page)
_QUAD = dict(nodes=[(0, 0), (80, 0), (80, 50), (0, 50)], colour=0x3366CC)
_PLINE = dict(nodes=[(0, 40), (40, 0), (80, 40), (120, 0)])
_PIPE3 = dict(nodes=[(0, 20), (60, 20), (60, 60), (120, 60)])


def _vertex_anim(d, i):
    for k, v in (
        ("VERTEX_VERTFROMX1", "0"),
        ("VERTEX_VERTFROMY1", "0"),
        ("VERTEX_VERTTOX1", "0"),
        ("VERTEX_VERTTOY1", "-40"),
        ("VERTEX_VERTEXP1", "(50)^(0,100)"),
    ):  # (no tag: no missing-tag box)
        d.ensure_property(i, k, v)


def _add_mid(a, b, at):
    """Insert a node at the midpoint of nodes a-b as node ``at``."""

    def run(d, i):
        nodes = d.reshape_info(i).nodes
        (x0, y0), (x1, y1) = nodes[a], nodes[b]
        return d.insert_object_node(i, at, ((x0 + x1) // 2, (y0 + y1) // 2))

    return run


def _shift_node(k, dx, dy):
    """The Vertices page's X / Y: node k typed elsewhere."""

    def run(d, i):
        nodes = list(d.reshape_info(i).nodes)
        nodes[k] = (nodes[k][0] + dx, nodes[k][1] + dy)
        return d.reshape_nodes(i, nodes, "vertices")

    return run


RESHAPE_CASES = [
    ("polygon_move_vertex_2", "polygon", _QUAD, lambda d, i: d.move_object_nodes(i, [1], 17, -9)),
    ("polygon_move_two_nodes", "polygon", _QUAD, lambda d, i: d.move_object_nodes(i, [1, 2], 11, 6)),
    ("polygon_add_node", "polygon", _QUAD, _add_mid(0, 1, 1)),
    ("polygon_add_node_closing", "polygon", _QUAD, _add_mid(3, 0, 4)),
    ("polygon_delete_node", "polygon", _QUAD, lambda d, i: d.delete_object_nodes(i, [2])),
    ("polyline_add_node", "polyline", _PLINE, _add_mid(1, 2, 2)),
    ("polyline_delete_two", "polyline", _PLINE, lambda d, i: d.delete_object_nodes(i, [1, 2])),
    ("pipe_move_node", "pipe", _PIPE3, lambda d, i: d.move_object_nodes(i, [2], 10, 15)),
    ("pipe_add_node", "pipe", _PIPE3, _add_mid(0, 1, 1)),
    ("pipe_delete_node", "pipe", _PIPE3, lambda d, i: d.delete_object_nodes(i, [1])),
    (
        "line_tilt_flat",
        "line",
        dict(nodes=[(0, 0), (100, 0)]),
        lambda d, i: d.move_object_nodes(i, [1], -30, 45),
    ),
    ("line_move_first_end", "line", _LINE, lambda d, i: d.move_object_nodes(i, [0], 90, 50)),
    ("polygon_vertex_anim", "polygon", _QUAD, _vertex_anim),
    ("polygon_vertex_anim_then_add", "polygon", _QUAD, _both(_vertex_anim, _add_mid(0, 1, 1))),
    ("polygon_vertices_page_xy", "polygon", _QUAD, _shift_node(1, 10, -10)),
]


def reshape_mutator(page_path, only=None, log=print):
    """Create one new object per RESHAPE_CASES entry and reshape it, then
    reshape up to two of the page's OWN free lines / pipes / polygons (move
    the first node, add a node on the first segment); returns (mutate,
    {handle: label})."""
    from ctgedit.editing import EditableDocument

    doc = EditableDocument.open(page_path)
    labels = {}
    own = len(doc.parsed.page.objects)  # the page's own records come first
    for n, (label, kind, kw, op) in enumerate(RESHAPE_CASES):
        if only and label not in only:
            continue
        dx, dy = 20 + (n % 4) * 180, 20 + (n // 4) * 90
        kw = dict(kw)
        kw["nodes"] = [(x + dx, y + dy) for x, y in kw["nodes"]]
        try:
            i = doc.create_object(kind, **kw)
            if op(doc, i) is False:
                raise RuntimeError("refused")
        except Exception as e:  # noqa: BLE001
            log("  %s failed: %s" % (label, e))
            continue
        labels[G.get_handle(doc.parsed.page.objects[i])] = label
    if not only:
        used = {}
        for i in range(own):
            info = doc.reshape_info(i)
            if (
                info is None
                or doc.page.objects[i].parent is not None
                or doc.page.objects[i].raw.get("_from_template")
            ):
                continue
            if used.get(info.kind, 0) >= 2:
                continue
            (x0, y0), (x1, y1) = info.nodes[0], info.nodes[1]
            ok = doc.move_object_nodes(i, [0], 3, -2)
            if info.can_insert:
                mid = ((x0 + x1) // 2, (y0 + y1) // 2)
                hit = G.node_insert_point(doc.reshape_info(i), mid, 2.0)
                if hit:
                    ok = doc.insert_object_node(i, *hit) or ok
            if ok:
                used[info.kind] = used.get(info.kind, 0) + 1
                labels[G.get_handle(doc.parsed.page.objects[i])] = "page_%s[%d]" % (info.kind, i)
    edited = list(doc.parsed.page.objects)

    def mutate(p):
        p.page.objects[:] = edited
        return list(labels)

    return mutate, labels


def inputs_mutator(page_path, log=print):
    """Touch and keyboard child records through EditableDocument."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import inputs as IN

    doc = EditableDocument.open(page_path)
    owners = {}
    r = doc.create_object("rectangle", rect=(40, 40, 120, 90), colour=0x3366CC)
    doc.set_input_commands(
        r,
        touch={
            "CMD": 'Message("ctgedit", "touch up", 64)',
            "MSG_LOG": "up",
            "DOWN_CMD": "TaskHnd(0)",
            "MSG_LOGDN": "down",
            "REPEAT_CMD": "Beep(0)",
            "MSG_LOGRPT": "repeat",
            "RPT_RATE": "250",
        },
        keys=[
            {"KEY_SEQ": "ENTER", "CMD": 'Message("ctgedit", "enter", 64)'},
            {"KEY_SEQ": "### ENTER", "CMD": "Beep(Arg1)", "PRIV": "1", "MSG_LOG": "value entered"},
        ],
    )
    owners[G.get_handle(doc.parsed.page.objects[r])] = "new_rect_touch_2keys"
    objs = doc.parsed.page.objects
    for i, po in enumerate(objs):
        if doc.page.objects[i].parent is not None or i == r:
            continue
        if po.type_code in (0x05, 0x0F, 0x11) and doc.input_owner_status(i)[0] and not doc.input_children(i):
            doc.add_input(i, "touch", {"CMD": "Beep(1)"})
            owners[G.get_handle(doc.parsed.page.objects[i])] = "added_touch[%d]" % i
            break
    # a transform CARRIER owner (a placed linked symbol): the new record is a
    # carrier too (factory.make_input_record chain=)
    from ctgedit.format import placement as P

    for i, po in enumerate(doc.parsed.page.objects):
        if (
            doc.page.objects[i].parent is not None
            or not P.is_linked_symbol(po)
            or doc.input_children(i)
            or not doc.input_owner_status(i)[0]
        ):
            continue
        try:
            doc.add_input(i, "keyboard", {"KEY_SEQ": "F2", "CMD": "Beep(3)"})
        except ValueError:  # scaled / rotated: not supported
            continue
        owners[G.get_handle(doc.parsed.page.objects[i])] = "linked_symbol_key[%d]" % i
        break
    for i, po in enumerate(doc.parsed.page.objects):
        if po.type_code == IN.TOUCH_TC and IN.is_free(po):
            owner = doc.input_owner(i)
            if not doc.input_owner_status(owner)[0]:
                continue
            doc.set_input_property(i, "CMD", "Beep(2)")
            owners[G.get_handle(doc.parsed.page.objects[owner])] = "edited_touch<%d" % owner
            break
    edited = list(doc.parsed.page.objects)

    def mutate(p):
        p.page.objects[:] = edited
        return []

    return mutate, owners


def _subtrees(data: bytes, handles) -> dict:
    """{owner handle: [payloads of the owner's subtree]} of a serialized page."""
    from ctgedit.format import inputs as IN

    objs = parse_ctg_bytes(data).page.objects
    out = {}
    for i, po in enumerate(objs):
        h = G.get_handle(po)
        if h in handles and po.type_code not in IN.INPUT_TCS and h not in out:
            out[h] = [writer.object_payload(o) for o in objs[i : IN.subtree_end(objs, i)]]
    return out


def run_inputs(element: str) -> int:
    src = os.path.join(live_project_dir("Example"), element + ".ctg")
    mutate, owners = inputs_mutator(src)
    cap = roundtrip(mutate, element=element)
    ours = _subtrees(cap["ours_bytes"], owners)
    eng = _subtrees(cap["engine_bytes"], owners)
    print("engine_rewrote=%s opened=%s saved=%s" % (cap["engine_rewrote"], cap["opened"], cap["saved"]))
    ok = cap["engine_rewrote"]
    for h, label in owners.items():
        a, b = ours.get(h), eng.get(h)
        same = a is not None and a == b
        ok = ok and same
        print("%5d %-26s %s (%d records)" % (h, label, "FIXED" if same else "DIFFERS", len(a or ())))
        if not same:
            for k, (x, y) in enumerate(zip(a or [], b or [])):
                if x != y:
                    print("      record %d: %s" % (k, json.dumps(field_diff(x, y), default=str)[:400]))
            if len(a or ()) != len(b or ()):
                print("      subtree length ours %d vs engine %d" % (len(a or ()), len(b or ())))
    return 0 if ok else 1


def experiments_mutator(spec: dict):
    """Append candidate records from an experiment file:
    ``{"page": "!PUMP_A_ACC", "records": [{"label", "payload_b64"}...]}``.
    Each record gets a fresh handle (900+i) so results map back by label."""
    labels = {}

    def mutate(p):
        hs = []
        for i, r in enumerate(spec["records"]):
            pl = base64.b64decode(r["payload_b64"])
            po = _parse_object(0, 0, len(pl), 0, pl)
            h = 900 + i
            if not r.get("keep_handle"):
                G.set_handle(po, h)
            else:
                h = G.get_handle(po)
            p.page.objects.append(po)
            hs.append(h)
            labels[h] = r.get("label", "exp%d" % i)
        return hs

    return mutate, labels


def run_experiments(path: str) -> str:
    """Run one experiment file through the engine; writes
    ``<file>.result.json`` next to it with ours/engine payloads + diffs."""
    with open(path, encoding="utf-8") as fh:
        spec = json.load(fh)
    mutate, labels = experiments_mutator(spec)
    cap = roundtrip(mutate, element=spec.get("page", "!PUMP_A_ACC"))
    out = {
        "engine_rewrote": cap["engine_rewrote"],
        "opened": cap["opened"],
        "saved": cap["saved"],
        "save_error": cap.get("save_error"),
        "records": [],
    }
    for h, (ours, eng) in cap["records"].items():
        out["records"].append(
            {
                "label": labels.get(h),
                "handle": h,
                # a page the engine did not rewrite proves nothing
                "fixed_point": cap["engine_rewrote"] and ours == eng,
                "ours": base64.b64encode(ours).decode() if ours else None,
                "engine": base64.b64encode(eng).decode() if eng else None,
                "diff": (field_diff(ours, eng) if ours else {"missing_ours": True})
                if cap["engine_rewrote"]
                else {"engine_did_not_save": True},
            }
        )
    res = os.path.splitext(path)[0] + ".result.json"
    with open(res, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, default=str)
    return res


def copies_mutator(page_path: str, source_path: str | None = None, per: int = 3, log=print):
    """Duplicate placed genie instances."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import genie_place as GP

    doc = EditableDocument.open(page_path)
    pb = doc.parsed.page
    labels = {}

    def uses_an(i):  # the %AN% list genies first
        for j in doc.subtree(i):
            try:
                if GP._refs_an(GP.decode(pb.objects[j])):
                    return True
            except Exception:  # noqa: BLE001
                pass
        return False

    roots = sorted((i for i, _ in doc.genie_instances()), key=lambda i: not uses_an(i))
    for r in roots[:per]:
        new = doc.duplicate_objects([r])[0]
        log(
            "  duplicate [%d] AN %d -> [%d] AN %d"
            % (r, G.get_handle(pb.objects[r]) + 1, new, G.get_handle(pb.objects[new]) + 1)
        )
        for j in range(new, len(pb.objects)):
            labels[G.get_handle(pb.objects[j])] = "dup[%d]<%d" % (j, r)
    if source_path:
        src = EditableDocument.open(source_path)
        pick = [i for i, _ in src.genie_instances()][:per]
        pick += [
            i
            for i, po in enumerate(src.parsed.page.objects)
            if src.page.objects[i].parent is None and po.subtype_size & 0x1 and po.type_code == 0x07
        ][:per]
        if pick:
            new = doc.paste_payload(src.copy_payload(pick))
            log("  pasted %d records from %s" % (len(new), os.path.basename(source_path)))
            for j in new:
                labels.setdefault(G.get_handle(pb.objects[j]), "paste[%d]" % j)
    edited = list(pb.objects)
    link = (pb.link_table_raw, pb.link_count)

    def mutate(p):
        p.page.objects[:] = edited
        p.page.link_table_raw, p.page.link_count = link
        return list(labels)

    return mutate, labels


def copies_check(project: str, element: str, source: str | None = None) -> int:
    """CLI 'copies': duplicate / paste genies (copies_mutator) and print what
    the engine left unchanged (records, link table)."""
    from ctgedit.format import links

    pdir = live_project_dir(project)
    mutate, labels = copies_mutator(
        os.path.join(pdir, element + ".ctg"), os.path.join(pdir, source + ".ctg") if source else None
    )
    cap = roundtrip(mutate, element=element, project=project)
    path = save_capture("copies_%s" % element.strip("!"), cap, labels)
    fixed = sum(1 for _h, (o, e) in cap["records"].items() if o == e and cap["opened"] and cap["saved"])
    ours, eng = parse_ctg_bytes(cap["ours_bytes"]), parse_ctg_bytes(cap["engine_bytes"])
    lt = lambda pc: (links.parse_link_table(pc.page.link_table_raw) or ("", []))[1]  # noqa: E731
    print(
        "copies %s/%s%s: engine_rewrote=%s opened=%s saved=%s"
        % (
            project,
            element,
            " <- " + source if source else "",
            cap["engine_rewrote"],
            cap["opened"],
            cap["saved"],
        )
    )
    print("  records fixed-point %d/%d" % (fixed, len(cap["records"])))
    print("  link table identical: %s" % (lt(ours) == lt(eng)))
    if lt(ours) != lt(eng):
        print("    ours  :", [n for _g, n in lt(ours)])
        print("    engine:", [n for _g, n in lt(eng)])
    for h, (o, e) in sorted(cap["records"].items()):
        fd = field_diff(o, e) if o else {"missing_ours": True}
        if fd:
            print("%5d %-20s %s" % (h, labels.get(h, ""), json.dumps(fd, default=str)[:300]))
    print("  capture:", path)
    return 0 if fixed == len(cap["records"]) and lt(ours) == lt(eng) else 1


TEMPLATE_KINDS = ("update", "switch", "new", "new_unlinked", "unlink")


def template_mutator(kind: str, page_path: str, target: str | None = None):
    """A page template operation."""
    from ctgedit.editing import EditableDocument
    from ctgedit.format import template_place as TP

    doc = EditableDocument.open(page_path)
    if kind == "update":
        doc.update_template(force=True)
    elif kind == "switch":
        doc.change_template(target)
    elif kind == "unlink":
        doc.cut_template_link()
    elif kind in ("new", "new_unlinked"):
        res = doc._resolver()
        tpl = TP.resolve_template(res, TP.reference_for(target))
        if tpl is None:
            raise KeyError("template %s does not resolve for %s" % (target, page_path))
        at = doc.parsed.attributes
        data = TP.new_page(
            tpl,
            target,
            page_name=os.path.splitext(os.path.basename(page_path))[0],
            linked=kind == "new",
            background=(at.bg_color0, at.bg_color1),
            resolver=res,
        )
        new = parse_ctg_bytes(data)
        pb, nb = doc.parsed.page, new.page
        pb.objects[:] = nb.objects
        pb.link_table_raw, pb.link_count = nb.link_table_raw, nb.link_count
        pb.page_width, pb.page_height = nb.page_width, nb.page_height
        na = new.attributes
        at.width, at.height = na.width, na.height
        at.bg_color0, at.bg_color1 = na.bg_color0, na.bg_color1
        at.list1.pairs[:] = na.list1.pairs
        at.list2.pairs[:] = na.list2.pairs
    else:
        raise ValueError("template operation must be one of %s" % ", ".join(TEMPLATE_KINDS))
    edited = doc.parsed
    labels = {
        G.get_handle(po): "%s[%d] tc=0x%x" % (kind, i, po.type_code)
        for i, po in enumerate(edited.page.objects)
    }

    def mutate(p):
        p.page.objects[:] = edited.page.objects
        p.page.link_table_raw, p.page.link_count = (edited.page.link_table_raw, edited.page.link_count)
        p.page.page_width, p.page.page_height = (edited.page.page_width, edited.page.page_height)
        if p.attributes is not None and edited.attributes is not None:
            a, e = p.attributes, edited.attributes
            a.width, a.height = e.width, e.height
            a.bg_color0, a.bg_color1 = e.bg_color0, e.bg_color1
            a.list1.pairs[:] = e.list1.pairs
            a.list2.pairs[:] = e.list2.pairs
        return [G.get_handle(po) for po in edited.page.objects]

    return mutate, labels, edited


def template_check(kind: str, project: str, element: str, target: str | None = None) -> int:
    """CLI 'template': run one template operation through the engine and print
    what it left unchanged (records, link table, attributes)."""
    from ctgedit.format import links

    src = os.path.join(live_project_dir(project), element + ".ctg")
    mutate, labels, _edited = template_mutator(kind, src, target)
    cap = roundtrip(mutate, element=element, project=project)
    path = save_capture("template_%s_%s" % (kind, element.strip("!")), cap, labels)
    fixed = sum(1 for _h, (o, e) in cap["records"].items() if o == e)
    ours, eng = parse_ctg_bytes(cap["ours_bytes"]), parse_ctg_bytes(cap["engine_bytes"])
    lt = lambda pc: (links.parse_link_table(pc.page.link_table_raw) or ("", []))[1]  # noqa: E731
    attr = lambda pc: (
        pc.attributes.width,
        pc.attributes.height,
        pc.attributes.bg_color0,  # noqa: E731
        list(pc.attributes.list1.pairs),
        list(pc.attributes.list2.pairs),
    )
    print(
        "template %s %s/%s%s: engine_rewrote=%s opened=%s saved=%s"
        % (
            kind,
            project,
            element,
            " -> " + target if target else "",
            cap["engine_rewrote"],
            cap["opened"],
            cap["saved"],
        )
    )
    print(
        "  records fixed-point %d/%d (count ours %d, engine %d)"
        % (fixed, len(cap["records"]), len(ours.page.objects), len(eng.page.objects))
    )
    print("  link table identical: %s" % (lt(ours) == lt(eng)))
    if lt(ours) != lt(eng):
        print("    ours  :", [n for _g, n in lt(ours)][:12])
        print("    engine:", [n for _g, n in lt(eng)][:12])
    print("  attributes identical: %s" % (attr(ours) == attr(eng)))
    if attr(ours) != attr(eng):
        print("    ours  :", attr(ours))
        print("    engine:", attr(eng))
    for h, (o, e) in sorted(cap["records"].items()):
        fd = field_diff(o, e) if o else {"missing_ours": True}
        if fd:
            print("%5d %-28s %s" % (h, labels.get(h, ""), json.dumps(fd, default=str)[:300]))
    print("  capture:", path)
    return 0


def main(argv):
    mode = argv[1] if len(argv) > 1 else "factory"
    if mode == "template":
        # template <update|switch|new|new_unlinked|unlink> <project> <page> [target ref]
        if len(argv) < 5:
            print(
                "usage: engine_roundtrip.py template <%s> <project> <page> "
                "[<style>.<name>@[-]<RES>.<ctt]" % "|".join(TEMPLATE_KINDS)
            )
            return 2
        return template_check(argv[2], argv[3], argv[4], argv[5] if len(argv) > 5 else None)
    if mode == "copies":
        # copies <project> <page> [<source page>]
        if len(argv) < 4:
            print("usage: engine_roundtrip.py copies <project> <page> [<source page>]")
            return 2
        return copies_check(argv[2], argv[3], argv[4] if len(argv) > 4 else None)
    if mode == "experiments":
        for path in argv[2:]:
            res = run_experiments(path)
            with open(res, encoding="utf-8") as fh:
                r = json.load(fh)
            print(
                "%s: rewrote=%s saved=%s fixed %d/%d -> %s"
                % (
                    os.path.basename(path),
                    r["engine_rewrote"],
                    r["saved"],
                    sum(x["fixed_point"] for x in r["records"]),
                    len(r["records"]),
                    res,
                )
            )
        return 0
    if mode == "inputs":
        return run_inputs(argv[2] if len(argv) > 2 else "!About")
    if mode == "appearance_write":
        # appearance_write <source .ctg> <out .ctg>: the appearance cases on a copy of a
        # page, written OUTSIDE the live project (for a side-by-side render check)
        if len(argv) < 4:
            print("usage: engine_roundtrip.py appearance_write <source.ctg> <out.ctg>")
            return 2
        mutate, labels = appearance_mutator(argv[2])
        p = parse_ctg(argv[2])
        mutate(p)
        p.page.object_count = len(p.page.objects)
        with open(argv[3], "wb") as fh:
            fh.write(writer.serialize_ctg(p.container, p.page, p.attributes))
        for h, label in sorted(labels.items()):
            print("%5d %s" % (h, label))
        return 0
    if mode in ("appearance", "reshape", "bitmaps", "special", "colours"):
        # appearance [page] [label,label...]: the Appearance / 3D Effects fields;
        # reshape [page] [label,...]: the node edits (RESHAPE_CASES + the page's own);
        # bitmaps [page] [label,...]: the bitmap edits + WMF import (BITMAP_CASES);
        # special [page] [label,...]: the trends / web content / Cicode objects /
        # ActiveX controls (SPECIAL_CASES)
        element = argv[2] if len(argv) > 2 else "!PUMP_A_ACC"
        only = set(argv[3].split(",")) if len(argv) > 3 else None
        src = os.path.join(live_project_dir("Example"), element + ".ctg")
        mutator = {
            "appearance": appearance_mutator,
            "reshape": reshape_mutator,
            "bitmaps": bitmaps_mutator,
            "special": special_mutator,
            "colours": colours_mutator,
        }[mode]
        mutate, labels = mutator(src, only)
        # node edits carry vertex animation, trends their pgtrend rows: register
        # first, like ctgedit's save
        cap = roundtrip(mutate, element=element, register_first=mode in ("reshape", "special"))
        path = save_capture("%s_%s" % (mode, element.strip("!")), cap, labels)
        # a page the engine did not open + save proves nothing (its "engine"
        # payloads are ours): count none fixed
        fixed = sum(1 for h, (o, e) in cap["records"].items() if o == e and cap["opened"] and cap["saved"])
        print(
            "engine_rewrote=%s opened=%s saved=%s fixed-point %d/%d -> %s"
            % (cap["engine_rewrote"], cap["opened"], cap["saved"], fixed, len(cap["records"]), path)
        )
        for h, (o, e) in sorted(cap["records"].items()):
            fd = field_diff(o, e) if o else {"missing_ours": True}
            print(
                "%5d %-28s %s"
                % (h, labels.get(h, ""), "FIXED" if not fd else json.dumps(fd, default=str)[:400])
            )
        return 0 if fixed == len(cap["records"]) else 1
    if mode in ("control", "edits", "turns", "dspval"):
        element = argv[2] if len(argv) > 2 else "!PUMP_A_ACC"
        if mode == "control":
            cap = roundtrip(control_mutator, element=element)
            labels = {}
        else:
            src = os.path.join(live_project_dir("Example"), element + ".ctg")
            if mode == "turns":
                mutate, labels = edit_mutator(src, ops=TURN_OPS, unit_ops=TURN_UNIT_OPS)
            elif mode == "dspval":
                mutate, labels = edit_mutator(src, per_kind=1, ops=DSPVAL_OPS, unit_ops=[])
            else:
                mutate, labels = edit_mutator(src)
            cap = roundtrip(mutate, element=element)
        path = save_capture("%s_%s" % (mode, element.strip("!")), cap, labels)
        fixed = sum(1 for h, (o, e) in cap["records"].items() if o == e and cap["opened"] and cap["saved"])
        print(
            "engine_rewrote=%s opened=%s saved=%s fixed-point %d/%d -> %s"
            % (cap["engine_rewrote"], cap["opened"], cap["saved"], fixed, len(cap["records"]), path)
        )
        for h, (o, e) in sorted(cap["records"].items()):
            fd = field_diff(o, e) if o else {"missing_ours": True}
            if fd:
                print("%5d %-28s %s" % (h, labels.get(h, ""), json.dumps(fd, default=str)[:300]))
        return 0
    if mode == "factory":
        kinds = argv[2].split(",") if len(argv) > 2 else None
        # only the kinds the factory can create take a handle (factory_mutator
        # skips an unseeded Database Exchange / Pelco)
        have = set(factory.available_kinds())
        cases = [
            k
            for k, _ in FACTORY_CASES
            if (not kinds or k in kinds) and (k in have or k not in factory.TOOL_SEEDS)
        ]
        labels = {900 + i: k for i, k in enumerate(cases)}
        labels[900 + len(cases)] = "bitmap"
        labels[900 + len(cases) + 1] = "flashing_bitmap"
        cap = roundtrip(factory_mutator(kinds))
        path = save_capture("factory", cap, labels)
        fixed = sum(1 for h, (o, e) in cap["records"].items() if o == e and cap["opened"] and cap["saved"])
        print(
            "engine_rewrote=%s opened=%s saved=%s fixed-point %d/%d -> %s"
            % (cap["engine_rewrote"], cap["opened"], cap["saved"], fixed, len(cap["records"]), path)
        )
        for h, (o, e) in sorted(cap["records"].items()):
            fd = field_diff(o, e) if o else {"missing_ours": True}
            print(
                "%4d %-20s %s"
                % (h, labels.get(h, ""), "FIXED" if not fd else json.dumps(fd, default=str)[:400])
            )
        return 0
    print("unknown mode", mode)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
