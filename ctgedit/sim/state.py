"""What the simulator says about one animated object for one scan."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, fields
from typing import Any, Dict, Iterable, List, Optional, Tuple

Point = Tuple[float, float]

# Disable styles (Include LABELS.DBF): what a disabled object looks like.
DISABLE_GRAY_SUNK = 1
DISABLE_GRAY_PART = 2
DISABLE_GRAY_ALL = 3
DISABLE_HIDE = 4
DISABLE_UNCHANGED = 5

# Fill level directions (FILMODE).
FILL_UP, FILL_DOWN, FILL_LEFT, FILL_RIGHT = 0, 1, 2, 3


@dataclass
class ObjectState:
    an: int
    shown: bool = True  # False = hidden (Hidden when TRUE, or DISABLE_HIDE)
    disabled: bool = False
    disable_style: int = DISABLE_UNCHANGED
    move: Optional[Point] = None  # pixel offset from the drawn position
    rotate: Optional[float] = None  # degrees, clockwise positive
    rotate_origin: Optional[Point] = None  # page coordinates of the pivot
    scale: Optional[Point] = None  # (sx, sy) factors
    scale_origin: Optional[Point] = None  # page coordinates of the fixed point
    fill_colour: Optional[int] = None  # the class's animated colour (fill, line, text)
    fill_level: Optional[float] = None  # 0.0 .. 1.0
    fill_level_direction: int = FILL_UP
    fill_level_colour: Optional[int] = None  # FILCOL: the colour of the UNFILLED part (the
    # filled part takes fill_colour, else the static
    # fill; doc 1172169)
    text: Optional[str] = None  # display value / string after formatting
    symbol: Optional[str] = None  # 'lib.element' a symbol set / animated symbol shows;
    # '' = no symbol
    symbol_state: Optional[int] = None  # a symbol set (tc 0x0C): the index of the state
    # child it shows (its direct children in order,
    # touch / keyboard records not counted;
    # format/symbolset.py). Wins over ``symbol``; an
    # index with no state (or a DummyMarker) shows
    # nothing. Genie copies of a set carry no
    # 'lib.element' of their own, an index always works.
    vertex_offsets: Optional[List[Point]] = None  # per vertex (dx, dy), vertex animation
    error: Optional[str] = None  # '#COM', '#RANGE', '#DIV/0', '#ERR' ... shown instead
    tooltip: Optional[str] = None
    # the engine's additions (engine/PROTOCOL.md, Pages: CicodeSim.Page.ObjectState)
    indication: Optional[str] = None  # 'bad' | 'uncertain' | 'error' | 'override' |
    # 'inhibit': the first quality / error flag of the
    # object's expressions this scan
    alarm: Optional[Dict[str, Any]] = None  # an alarm indicator's {priority, state: unack_on |
    # ack_on | unack_off | none, colour, flash}
    trend: Optional[Dict[str, Any]] = None  # a trend's {period, pens: [[[t, v] ...] per pen]}
    # -- alarm lists (engine Page/PageState.cs AlarmListState / AlarmRowState)
    alarm_list: Optional[Dict[str, Any]] = None  # on a list's first AN: {type, count, offset,
    # total, array, no_draw, columns: [[field,
    # width] ...], rows: [alarm_row ...],
    # fontInfo?, headerFontInfo?}
    alarm_row: Optional[Dict[str, Any]] = None  # a drawn row's AN (AlarmDsp, no_draw false):
    # {row, text, fields: [[name, value] ...], tag,
    # priority, category, state, font,
    # font_handle, colour, background, flash,
    # fontInfo?}

    def is_default(self) -> bool:
        return self == ObjectState(self.an)


@dataclass
class PageState:
    """One window's page at one scan."""

    window: int
    page: str
    objects: Dict[int, ObjectState] = field(default_factory=dict)  # by AN
    flash_on: bool = True  # the global flash phase of this scan
    serial: int = 0  # increases every scan that changed something
    replace: bool = False  # ``objects`` is the whole set (a pageState
    # reply): every other AN is static again
    drawn: Optional[Dict[int, Dict[str, Any]]] = None
    # what the page's Cicode drew (DspText, DspSym
    # ...: PAGE.drawn), by AN in the order the ANs
    # were first drawn: always the WHOLE list (the
    # engine sends it on every pageState and
    # pageChanged; an absent list = nothing drawn).
    # None = no news (the demo values): what is
    # drawn stays. Entries are the engine's JSON
    # objects (render/sim_drawn.py draws them).
    controls: Optional[Dict[int, Dict[str, Any]]] = None
    # the page's ActiveX controls the code or a viewer
    # used (PAGE.controls: {AN: {an,
    # name, properties: {name: text}, serial}}). None
    # = no news (the engine sends them when they
    # changed): what the fields show stays.


# -- JSON (the engine's pageChanged event and pageState reply) ------------------------------
_CAMEL = re.compile(r"(?<=[a-z0-9])([A-Z])")
_FIELDS = {f.name: f for f in fields(ObjectState)}
_POINTS = ("move", "rotate_origin", "scale", "scale_origin")
_INTS = ("disable_style", "fill_level_direction", "fill_colour", "fill_level_colour", "symbol_state")
_FLOATS = ("rotate", "fill_level")


def _field_name(key: str) -> str:
    """'fillColour' / 'fillColor' / 'fill_color' -> 'fill_colour' (the names above are the
    contract; the engine may send them camelCase, as DAP bodies usually are)."""
    k = _CAMEL.sub(lambda m: "_" + m.group(1).lower(), str(key)).lower()
    return k.replace("color", "colour")


def _number(v: Any) -> Optional[float]:
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except TypeError, ValueError:
        return None
    return f if math.isfinite(f) else None


def _point(v: Any) -> Optional[Tuple[float, float]]:
    if isinstance(v, dict):
        v = (v.get("x"), v.get("y"))
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        return None
    x, y = _number(v[0]), _number(v[1])
    return None if x is None or y is None else (x, y)


def _colour(v: Any) -> Optional[int]:
    if isinstance(v, str):
        t = v.strip().lower()
        try:
            if t.startswith("0x"):
                return int(t[2:], 16)
            return int(t[1:], 16) if t.startswith("#") else int(t)
        except ValueError:
            return None
    n = _number(v)
    return None if n is None else int(n) & 0xFFFFFFFF


def _alarm_dict(v: Any) -> Optional[Dict[str, Any]]:
    """An alarm_list / alarm_row as the engine sent it (a dict), its 'rows' kept only when they
    are dicts; anything else is not an alarm list."""
    if not isinstance(v, dict):
        return None
    out = dict(v)
    rows = out.get("rows")
    if rows is not None:
        out["rows"] = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    return out


def object_state_from_json(an: int, data: Any) -> ObjectState:
    """One ``{an, state}`` of a pageChanged event / pageState reply as an ObjectState. A null
    or missing state = the static look; unknown fields are left out; a number that is not
    finite is not animated (the renderer never gets NaN)."""
    st = ObjectState(int(an))
    if not isinstance(data, dict):
        return st
    for key, value in data.items():
        name = _field_name(key)
        if name == "an" or name not in _FIELDS or value is None:
            continue
        if name in _POINTS:
            value = _point(value)
        elif name == "vertex_offsets":
            pts = [_point(p) for p in value] if isinstance(value, (list, tuple)) else []
            value = pts if pts and all(p is not None for p in pts) else None
        elif name in ("fill_colour", "fill_level_colour"):
            value = _colour(value)
        elif name in _INTS:
            n = _number(value)
            value = None if n is None else int(n)
        elif name in _FLOATS:
            value = _number(value)
        elif name in ("shown", "disabled"):
            value = bool(value)
        elif name in ("text", "symbol", "error", "tooltip", "indication"):
            value = str(value)
        elif name in ("alarm", "trend"):
            value = value if isinstance(value, dict) else None
        elif name in ("alarm_list", "alarm_row"):
            value = _alarm_dict(value)
        if value is None and name in ("disable_style", "fill_level_direction"):
            continue
        setattr(st, name, value)
    return st


def object_states_from_json(items: Iterable[Any]) -> Dict[int, ObjectState]:
    """``[{an, state}, ...]`` (or a ``{an: state}`` object) as ``{AN: ObjectState}``; an
    entry without a usable AN is left out."""
    out: Dict[int, ObjectState] = {}
    if isinstance(items, dict):
        items = [{"an": k, "state": v} for k, v in items.items()]
    for it in items or ():
        if not isinstance(it, dict):
            continue
        an = _number(it.get("an"))
        if an is None or an <= 0 or an != int(an):
            continue
        state = it.get("state", {k: v for k, v in it.items() if k != "an"})
        out[int(an)] = object_state_from_json(int(an), state)
    return out


def drawn_from_json(items: Any) -> Dict[int, Dict[str, Any]]:
    """PAGE.drawn ``[{an, kind, x, y, text?, font?, fontInfo?, args, serial}, ...]`` as
    ``{AN: entry}`` in list order (the last entry of an AN wins; one without a usable AN or
    kind is left out). The entries are copies of the JSON objects, ``an`` an int."""
    out: Dict[int, Dict[str, Any]] = {}
    for it in items if isinstance(items, (list, tuple)) else ():
        if not isinstance(it, dict):
            continue
        an = _number(it.get("an"))
        if an is None or an <= 0 or an != int(an) or not isinstance(it.get("kind"), str):
            continue
        e = dict(it)
        e["an"] = int(an)
        out[int(an)] = e
    return out


def controls_from_json(items: Any) -> Optional[Dict[int, Dict[str, Any]]]:
    """PAGE.controls ``[{an, name, properties, serial}, ...]`` as ``{AN: entry}`` (properties
    as text); None when the body has none (no news)."""
    if not isinstance(items, (list, tuple)):
        return None
    out: Dict[int, Dict[str, Any]] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        an = _number(it.get("an"))
        if an is None or an <= 0 or an != int(an):
            continue
        props = it.get("properties")
        props = (
            {str(k): ("" if v is None else str(v)) for k, v in props.items()}
            if isinstance(props, dict)
            else {}
        )
        out[int(an)] = {
            "an": int(an),
            "name": str(it.get("name") or ""),
            "properties": props,
            "serial": it.get("serial"),
        }
        if isinstance(it.get("items"), list):
            out[int(an)]["items"] = [str(item) for item in it["items"]]
    return out


def page_state_from_json(body: Any, page: str = "", replace: bool = False) -> PageState:
    """A pageChanged event body / pageState reply ``{window, page?, objects, flashOn?,
    serial?, drawn?}`` as a PageState (``drawn`` = {} when the body has none)."""
    body = body if isinstance(body, dict) else {}
    window = _number(body.get("window"))
    flash = body.get("flashOn", body.get("flash_on", True))
    serial = _number(body.get("serial"))
    return PageState(
        window=int(window) if window is not None else 0,
        page=str(body.get("page") or page or ""),
        objects=object_states_from_json(body.get("objects") or ()),
        flash_on=bool(flash) if flash is not None else True,
        serial=int(serial) if serial is not None else 0,
        replace=replace,
        drawn=drawn_from_json(body.get("drawn")),
        controls=controls_from_json(body.get("controls")),
    )
