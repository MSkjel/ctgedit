"""How Goto Object and Find name an object."""

from __future__ import annotations

from ..format import codes as _codes

#: CtDraw's display name of each object class ('Straight Line' ...
#: 'Size/Move'). The Goto
#: Object tree labels an object '<class> AN<n>' with it. CtDraw names the Touch class 'Unknown'; ctgedit
#: shows 'Touch' (its Input page's name) instead.
CLASS_LABELS = {
    0x01: "Straight Line",
    0x12: "Polyline Path",
    0x02: "Free Hand Line",
    0x03: "Rectangle",
    0x0E: "Rectangle",
    0x0F: "Rectangle",
    0x04: "Ellipse",
    0x11: "Ellipse",
    0x05: "Text",
    0x06: "Sparkle",
    0x07: "Bitmap",
    0x08: "Group",
    0x09: "Metafile",
    0x0A: "Polygon",
    0x10: "Polygon",
    0x0B: "Pipe",
    0x0C: "Symbol Set",
    0x0D: "ActiveX",
    0x13: "Web Content",
    0x1000: "Animation Point",
    0x1001: "Button",
    0x1002: "Bar Graph",
    0x1003: "Trend",
    0x1004: "Number",
    0x1005: "String",
    0x1006: "Text",
    0x1007: "Symbol",
    0x1008: "Color Flood",
    0x100D: "Animated Symbol",
    0x100F: "Slider",
    0x1009: "Cicode Object",
    _codes.TC.KEYBOARD: "Keyboard",
    0x100E: "Size/Move",
    _codes.TC.TOUCH: "Touch",
}
#: a linked record's label names its element
LINK_LABELS = {"ctl": "Symbol", "ctm": "Genie", "ctt": "Template"}


def object_label(doc, i: int) -> str:
    """'<class> AN<n> "<name>"' of record ``i`` of ``doc``, as Goto Object labels it."""
    try:
        o = doc.page.objects[i]
        po = doc.parsed.page.objects[i]
    except Exception:  # noqa: BLE001 -- swallow-ok: a row whose object is gone
        return "?"
    tc = po.type_code
    cls = CLASS_LABELS.get(tc, o.kind.value.replace("_", " ").title())
    if tc == 0x05 and o.kind.value == "number":
        cls = "Number"
    if tc == 0x08 and po.subtype_size & 0x200:
        cls = "Genie"
    if po.subtype_size & 0x1:
        try:
            from ..format.symbols import reference_from_body

            ref = reference_from_body(po.body_raw)
            if ref is not None:
                kind = LINK_LABELS.get(ref.ext, "Symbol")
                if o.kind.value == "composite_genie":
                    kind = "Composite Genie"
                cls = "%s %s.%s" % (kind, ref.library, ref.element)
        except Exception:  # noqa: BLE001 -- swallow-ok: a label only
            pass
    from ..format import geometry as G

    an = G.live_an(po)
    try:
        name = doc.object_name(i)
    except Exception:  # noqa: BLE001 -- swallow-ok: a label only
        name = ""
    text = "%s AN%d" % (cls, an) if an > 0 else cls
    return text + (' "%s"' % name if name else "")
