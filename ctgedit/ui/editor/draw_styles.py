"""The look of new objects: a style per drawing tool and the last colours used."""

from __future__ import annotations

import json
from typing import Dict, Optional, Tuple

STYLE_KEY = "draw/style"
LAST_KEY = "draw/last_style"
MODE_KEY = "opt/new_object_style"
MODES = (("tool", "This tool's own"), ("last", "Last used"))
FILL_KEY = "draw/fill_colour"
LINE_KEY = "draw/line_colour"
TEXT_KEY = "draw/text_colour"
NO_FILL_KEY = "draw/no_fill"
COLOUR_MODE_KEY = "draw/colour_mode"
COLOUR_MODES = (("auto", "Contrast with the page"), ("fixed", "The colours I picked"))

#: the colours 'auto' gives: (fill, line, text) on a light page and on a dark one
LIGHT_PAGE = (0xC0C0C0, 0x202020, 0x202020)
DARK_PAGE = (0x404040, 0xE0E0E0, 0xE0E0E0)
#: a page with no colour of its own: the window's default (CtDraw's button face)
DEFAULT_PAGE = 0xD4D0C8
#: a page lighter than this (0 to 255, the perceived lightness) is light
LIGHT_ABOVE = 128

#: drawing tool -> the object kind it makes
TOOL_KIND = {
    "line": "line",
    "freehand": "line",
    "pipe": "pipe",
    "rectangle": "rectangle",
    "rectangle_outline": "rectangle",
    "ellipse": "ellipse",
    "ellipse_outline": "ellipse",
    "polygon": "polygon",
    "text": "text",
    "number": "number",
    "button": "button",
}
#: the tools that draw hollow shapes (a style's Filled does not apply to them)
OUTLINE_TOOLS = ("rectangle_outline", "ellipse_outline")
#: kind -> (one, several): 'New Rectangle', 'Use Selection as Default for New Rectangles'
KIND_NAMES = {
    "line": ("Line", "Lines"),
    "pipe": ("Pipe", "Pipes"),
    "rectangle": ("Rectangle", "Rectangles"),
    "ellipse": ("Ellipse", "Ellipses"),
    "polygon": ("Polygon", "Polygons"),
    "text": ("Text", "Texts"),
    "number": ("Number", "Numbers"),
    "button": ("Button", "Buttons"),
}
_TEXT_FIELDS = ("font", "font_size", "bold", "italic", "underline", "strikeout", "justify")
_SHAPE_FIELDS = (
    "line_width",
    "line_style",
    "filled",
    "fill_colour",
    "line_colour",
    "gradient_mode",
    "gradient_colour",
)
#: kind -> the appearance fields it takes (DocumentProperties.APPEARANCE_FIELDS, a pipe's
#: lowlight)
KIND_FIELDS = {
    "line": ("line_width", "line_style", "line_colour"),
    "pipe": ("line_width", "line_colour", "lolight_colour"),
    "rectangle": _SHAPE_FIELDS + ("corner_radius", "rectangle_style"),
    "ellipse": _SHAPE_FIELDS,
    "polygon": _SHAPE_FIELDS,
    "text": _TEXT_FIELDS + ("text_colour",),
    "number": _TEXT_FIELDS + ("text_colour",),
    "button": _TEXT_FIELDS,
}
#: the factory look of each kind (the seeds of format/factory; tests/test_draw_styles.py
#: checks them against objects drawn without a style)
FACTORY = {
    "line": {"line_width": 1, "line_style": 0},
    "pipe": {"line_width": 3},
    "rectangle": {
        "line_width": 1,
        "line_style": 0,
        "filled": True,
        "gradient_mode": 0,
        "corner_radius": 0,
        "rectangle_style": 0,
    },
    "ellipse": {"line_width": 1, "line_style": 0, "filled": True, "gradient_mode": 0},
    "polygon": {"line_width": 1, "line_style": 0, "filled": True, "gradient_mode": 0},
    "text": {
        "font": "Arial",
        "font_size": 32,
        "bold": False,
        "italic": False,
        "underline": False,
        "strikeout": False,
        "justify": "left",
    },
    "number": {
        "font": "Arial",
        "font_size": 32,
        "bold": False,
        "italic": False,
        "underline": False,
        "strikeout": False,
        "justify": "left",
    },
    "button": {
        "font": "Arial",
        "font_size": 10,
        "bold": True,
        "italic": False,
        "underline": False,
        "strikeout": False,
        "justify": "centre",
    },
}
#: the fields that are colours (0x00RRGGBB)
COLOUR_FIELDS = ("fill_colour", "line_colour", "text_colour", "gradient_colour", "lolight_colour")
#: the box a click (no drag) with these tools makes, (W, H) in px: the factory sizes. A text
#: and a number are then fitted to their text (the height sets the seed's font size, which
#: a style's Size replaces), so only the Properties panel's New Button shows its own
CLICK_SIZES = {"text": (140, 22), "button": (110, 36), "number": (90, 22)}
#: the fields of a tool's own click size in its draw/style entry, and their range
CLICK_FIELDS = ("click_w", "click_h")
CLICK_SIZE_RANGE = (1, 2000)


# --------------------------------------------------------------------------- settings
def _st():
    from ..prefs.settings import app_settings

    return app_settings()


def _json(key: str) -> dict:
    try:
        raw = _st().value(key, "{}")
        data = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    except Exception:  # noqa: BLE001  # swallow-ok: a damaged setting is the factory look
        return {}
    return data if isinstance(data, dict) else {}


def _put_json(key: str, data: dict) -> None:
    _st().setValue(key, json.dumps(data, sort_keys=True))


def packed(v) -> Optional[int]:
    """A stored colour as 0x00RRGGBB, None when unset or garbled."""
    if v is None or v == "":
        return None
    try:
        n = int(str(v).strip(), 0)
    except TypeError, ValueError:
        return None
    return n & 0xFFFFFF if 0 <= n <= 0xFFFFFFFF else None


def _clean(kind: str, values: dict) -> dict:
    """``values`` limited to the fields ``kind`` takes, in their types."""
    fields = KIND_FIELDS.get(kind, ())
    out = {}
    for k, v in (values or {}).items():
        if k not in fields or v is None:
            continue
        if k in COLOUR_FIELDS:
            v = packed(v)
            if v is None:
                continue
        elif k in ("filled", "bold", "italic", "underline", "strikeout"):
            v = bool(v) if not isinstance(v, str) else v.lower() in ("true", "1")
        elif k in ("font", "justify"):
            v = str(v)
            if not v:
                continue
            if k == "justify" and v not in ("left", "centre", "right"):
                continue
        else:
            try:
                v = int(v)
            except TypeError, ValueError:
                continue
        out[k] = v
    return out


def kind_of_tool(tool: str) -> Optional[str]:
    return TOOL_KIND.get(tool or "")


def tools_of_kind(kind: str) -> Tuple[str, ...]:
    return tuple(t for t, k in TOOL_KIND.items() if k == kind)


def kind_of_object(obj) -> Optional[str]:
    """The style kind of a model object (None: no style of a drawing tool)."""
    from ...model.document import ObjectKind

    k = getattr(obj, "kind", None)
    if k is ObjectKind.TEXT:
        # (a text showing a number: the Number tool's)
        dv = getattr(getattr(obj, "animations", None), "display_value", None)
        return "number" if dv is not None and not dv.is_text else "text"
    return {
        ObjectKind.LINE: "line",
        ObjectKind.PIPE: "pipe",
        ObjectKind.RECTANGLE: "rectangle",
        ObjectKind.ELLIPSE: "ellipse",
        ObjectKind.POLYGON: "polygon",
        ObjectKind.NUMBER: "number",
        ObjectKind.BUTTON: "button",
    }.get(k)


def mode() -> str:
    """'tool' (each tool's own style) or 'last' (the last used look of the kind)."""
    v = str(_st().value(MODE_KEY, "tool") or "tool")
    return v if v in dict(MODES) else "tool"


def set_mode(value: str) -> None:
    _st().setValue(MODE_KEY, value if value in dict(MODES) else "tool")


def tool_style(tool: str) -> dict:
    """The tool's own fields (empty: the factory look)."""
    kind = kind_of_tool(tool)
    if kind is None:
        return {}
    return _clean(kind, _json(STYLE_KEY).get(tool) or {})


def set_tool_style(tool: str, values: dict) -> None:
    """The tool's own style is ``values`` (the fields its kind takes)."""
    kind = kind_of_tool(tool)
    if kind is None:
        return
    data = _json(STYLE_KEY)
    clean = _clean(kind, values)
    if tool in OUTLINE_TOOLS:
        clean.pop("filled", None)
        clean.pop("fill_colour", None)
    # (the tool's own click size is not part of its look: kept)
    old = data.get(tool)
    if isinstance(old, dict):
        clean.update({k: v for k, v in old.items() if k in CLICK_FIELDS})
    if clean:
        data[tool] = clean
    else:
        data.pop(tool, None)
    _put_json(STYLE_KEY, data)


def set_tool_field(tool: str, field: str, value) -> None:
    """One field of the tool's own style; None removes it (the factory / swatch value)."""
    cur = tool_style(tool)
    if value is None:
        cur.pop(field, None)
    else:
        cur[field] = value
    set_tool_style(tool, cur)


def reset_tool(tool: str) -> None:
    """The tool draws with the factory look again (and the swatch colours), and a click
    with it makes the factory size again."""
    data = _json(STYLE_KEY)
    if data.pop(tool, None) is not None:
        _put_json(STYLE_KEY, data)


def click_size(tool: str) -> Optional[Tuple[int, int]]:
    """(W, H) of the box a click (no drag) with ``tool`` makes: the tool's own size, else
    the factory size (:data:`CLICK_SIZES`); None for a tool not in it."""
    base = CLICK_SIZES.get(tool or "")
    if base is None:
        return None
    raw = _json(STYLE_KEY).get(tool)
    raw = raw if isinstance(raw, dict) else {}
    lo, hi = CLICK_SIZE_RANGE
    out = []
    for field, dflt in zip(CLICK_FIELDS, base):
        try:
            v = int(raw.get(field, dflt))
        except TypeError, ValueError:
            v = dflt
        out.append(max(lo, min(hi, v)))
    return out[0], out[1]


def set_click_size(tool: str, w: Optional[int] = None, h: Optional[int] = None) -> None:
    """The tool's own click size, ``w`` and ``h`` in px (None or the factory value: the
    factory size)."""
    base = CLICK_SIZES.get(tool or "")
    if base is None:
        return
    data = _json(STYLE_KEY)
    entry = data.get(tool)
    entry = dict(entry) if isinstance(entry, dict) else {}
    lo, hi = CLICK_SIZE_RANGE
    for field, v, dflt in zip(CLICK_FIELDS, (w, h), base):
        v = None if v is None else max(lo, min(hi, int(v)))
        if v is None or v == dflt:
            entry.pop(field, None)
        else:
            entry[field] = v
    if entry:
        data[tool] = entry
    else:
        data.pop(tool, None)
    _put_json(STYLE_KEY, data)


def set_kind_style(kind: str, values: dict) -> None:
    """Every tool of ``kind`` takes ``values`` (Use Selection as Default for New ...)."""
    for t in tools_of_kind(kind):
        set_tool_style(t, values)


def last_style(kind: str) -> dict:
    return _clean(kind, _json(LAST_KEY).get(kind) or {})


def record_last(kind: str, appearance: dict) -> None:
    """The look of the object of ``kind`` the user edited last (``opt/new_object_style``
    'last' draws new objects of that kind with it)."""
    if kind not in KIND_FIELDS:
        return
    clean = _clean(kind, appearance)
    data = _json(LAST_KEY)
    if data.get(kind) == clean:
        return
    data[kind] = clean
    _put_json(LAST_KEY, data)


def clear_last(kind: str) -> None:
    """The kind's last used look is forgotten (Last used: the factory look again)."""
    data = _json(LAST_KEY)
    if data.pop(kind, None) is not None:
        _put_json(LAST_KEY, data)


def set_last_field(kind: str, field: str, value) -> None:
    cur = last_style(kind)
    if value is None:
        cur.pop(field, None)
    else:
        cur[field] = value
    data = _json(LAST_KEY)
    data[kind] = _clean(kind, cur)
    _put_json(LAST_KEY, data)


# --------------------------------------------------------------------------- colours
def page_colour(page) -> int:
    """The page's colour as 0x00RRGGBB (its own, else the window's default)."""
    ap = getattr(page, "appearance", None) if page is not None else None
    bg = getattr(ap, "background_colour", None) if ap is not None else None
    if bg is None or (int(bg) >> 24) & 0xFF == 0xFF:
        return DEFAULT_PAGE
    return int(bg) & 0xFFFFFF


def lightness(colour: int) -> float:
    """The perceived lightness (0 to 255) of a 0x00RRGGBB colour."""
    c = int(colour) & 0xFFFFFF
    return 0.299 * ((c >> 16) & 0xFF) + 0.587 * ((c >> 8) & 0xFF) + 0.114 * (c & 0xFF)


def page_is_light(page) -> bool:
    return lightness(page_colour(page)) > LIGHT_ABOVE


def auto_colours(page) -> Dict[str, int]:
    """The colours that contrast with the page: {'fill', 'line', 'text'}."""
    fill, line, text = LIGHT_PAGE if page_is_light(page) else DARK_PAGE
    return {"fill": fill, "line": line, "text": text}


def colour_mode() -> str:
    v = str(_st().value(COLOUR_MODE_KEY, "auto") or "auto")
    return v if v in dict(COLOUR_MODES) else "auto"


def set_colour_mode(value: str, page=None) -> None:
    """'auto' or 'fixed'. Going to 'fixed' keeps the colours shown for ``page``."""
    value = value if value in dict(COLOUR_MODES) else "auto"
    if value == "fixed" and colour_mode() == "auto":
        _freeze(page)
    _st().setValue(COLOUR_MODE_KEY, value)


def _freeze(page) -> None:
    """The colours 'auto' shows now become the picked ones."""
    st = _st()
    for which, v in auto_colours(page).items():
        st.setValue(KEYS[which], int(v))


#: the swatch -> its setting
KEYS = {"fill": FILL_KEY, "line": LINE_KEY, "text": TEXT_KEY}


def no_fill() -> bool:
    v = _st().value(NO_FILL_KEY, False)
    return v is True or str(v).lower() in ("true", "1")


def set_no_fill(on: bool) -> None:
    _st().setValue(NO_FILL_KEY, bool(on))


def colours(page=None) -> Dict[str, int]:
    """The swatch colours new objects take on ``page``: {'fill', 'line', 'text'}."""
    auto = auto_colours(page)
    if colour_mode() == "auto":
        return auto
    st = _st()
    out = {}
    for which, key in KEYS.items():
        v = packed(st.value(key, None))
        out[which] = auto[which] if v is None else v
    return out


def set_colour(which: str, colour: int, page=None) -> None:
    """The user picked ``colour`` for the 'fill' / 'line' / 'text' swatch: the colours are
    the picked ones from now on (the others keep what they showed on ``page``)."""
    if which not in KEYS:
        raise ValueError(which)
    set_colour_mode("fixed", page)
    _st().setValue(KEYS[which], int(colour) & 0xFFFFFF)
    if which == "fill":
        set_no_fill(False)


def swap_fill_and_line(page=None) -> None:
    """Swap the fill and line colours of new objects (the swatch's arrow, Shift+X)."""
    c = colours(page)
    set_colour_mode("fixed", page)
    st = _st()
    st.setValue(FILL_KEY, int(c["line"]))
    st.setValue(LINE_KEY, int(c["fill"]))


def creation_colour(tool: str, page=None) -> Optional[int]:
    """The one colour a tool passes to ``doc.create_object(colour=...)``: the text colour
    for Text and Number, the line colour for lines, pipes and the outline tools, the fill
    colour for the filled shapes; None for the tools that take no colour."""
    kind = kind_of_tool(tool)
    if kind is None or kind == "button":
        return None
    c = colours(page)
    if kind in ("text", "number"):
        return c["text"]
    if (
        kind in ("line", "pipe")
        or tool in OUTLINE_TOOLS
        or (no_fill() and kind in ("rectangle", "ellipse", "polygon"))
    ):
        return c["line"]
    return c["fill"]


# --------------------------------------------------------------------------- the API
def own_style(kind: str, tool: Optional[str] = None) -> dict:
    """The stored fields the next object of ``kind`` takes besides the swatch colours: the
    tool's own style ('tool') or the kind's last used look ('last')."""
    if mode() == "last":
        return last_style(kind)
    return tool_style(tool or (tools_of_kind(kind) or ("",))[0])


def style_for(kind: str, tool: Optional[str] = None, page=None) -> dict:
    """The appearance a new object of ``kind`` drawn with ``tool`` on ``page`` gets: a dict
    for ``doc.apply_appearance`` (only the fields the kind takes; colours as 0x00RRGGBB)."""
    fields = KIND_FIELDS.get(kind)
    if fields is None:
        return {}
    tool = tool or (tools_of_kind(kind) or ("",))[0]
    c = colours(page)
    out: dict = {}
    if "fill_colour" in fields:
        if no_fill():
            out["filled"] = False
        else:
            out["fill_colour"] = c["fill"]
    if "line_colour" in fields:
        out["line_colour"] = c["line"]
    if "text_colour" in fields:
        out["text_colour"] = c["text"]
    out.update(own_style(kind, tool))
    if tool in OUTLINE_TOOLS:
        out["filled"] = False
    if out.get("filled") is False:
        # (apply_appearance's fill colour would make the shape solid again)
        out.pop("fill_colour", None)
    elif out.get("filled") is True and "fill_colour" not in out and "fill_colour" in fields:
        out["fill_colour"] = c["fill"]
    if not out.get("gradient_mode"):
        out.pop("gradient_colour", None)
    return {k: v for k, v in out.items() if k in fields}


def shown_values(kind: str, tool: Optional[str] = None, page=None) -> dict:
    """What the Properties panel's 'New <Kind>' section shows: the factory look, then what
    :func:`style_for` gives (the factory values of the fields it leaves out)."""
    out = dict(FACTORY.get(kind, {}))
    if tool in OUTLINE_TOOLS:
        out["filled"] = False
    out.update(style_for(kind, tool, page))
    return out


def label(kind: str, plural: bool = False) -> str:
    names = KIND_NAMES.get(kind)
    if names is None:
        return kind
    return names[1 if plural else 0]
