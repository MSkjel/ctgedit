"""How the canvas looks and reacts, from Settings: handle sizes, guideline and outline
widths, pointer reach, the guideline colour and the modifier keys."""

from __future__ import annotations

from PySide6 import QtCore, QtGui


#: Settings > Appearance > Canvas > Handle size (view/handle_size): the sizing handles' size
#: on screen, in px (the edge handles are 2 px smaller, the one under the pointer 2 px larger)
HANDLE_SIZES = {"small": 6.0, "medium": 8.0, "large": 11.0}
HANDLE_PX = {"px": HANDLE_SIZES["medium"]}
#: a node is caught within CtDraw's 7 x 7 box round it, at least
NODE_BOX_PX = 7.0
#: the rotation grip sits this many screen px above the top-middle handle
GRIP_GAP_PX = 18.0
#: the curved-arrow zone just outside a corner of the selection: this far past the handle
#: (where no other object is: a press there on an object still selects it)
ROTATE_ZONE_PX = 9.0


def handle_size() -> float:
    """The sizing handles' size on screen in px (view/handle_size); the hit tests use it too."""
    return HANDLE_PX["px"]


def set_handle_size(name) -> None:
    """'small' 6 / 'medium' 8 / 'large' 11 px (anything else: medium)."""
    HANDLE_PX["px"] = HANDLE_SIZES.get(str(name or "medium"), HANDLE_SIZES["medium"])


#: Settings > Appearance > Canvas > Guidelines (view/guide_width), Grab a guideline within
#: (view/guide_reach) and Selection outlines (view/outline_width), in screen px; the first of
#: each is the default
GUIDE_WIDTHS = {"thin": 1.0, "medium": 2.0, "thick": 3.0}
GUIDE_REACHES = {"near": 3.0, "medium": 6.0, "far": 10.0}
OUTLINE_WIDTHS = {"thin": 1.0, "medium": 2.0, "thick": 3.0}
LINES_PX = {"guide": 1.0, "reach": 3.0, "outline": 1.0}


def guide_width() -> float:
    """A guideline's width on screen in px (view/guide_width)."""
    return LINES_PX["guide"]


def guide_reach() -> float:
    """How near a press must be to a guideline to grab it, in screen px (view/guide_reach);
    anywhere on a wide one."""
    return max(LINES_PX["reach"], LINES_PX["guide"] / 2.0 + 1.0)


def outline_width() -> float:
    """The width on screen in px of the selected objects' outlines, the box round several,
    the hover outline and the outlines a selection box will select (view/outline_width)."""
    return LINES_PX["outline"]


def mark_width(base: float = 1.0) -> float:
    """The width on screen in px of the canvas's other marks: ``base`` (their look with thin
    outlines) and what Selection outlines adds (medium 1 px, thick 2 px). The smart guides,
    the selection and zoom boxes, the drawing preview, the resize and rotate ghosts, a node
    drag, the changes and the Undo flash take it."""
    return base + LINES_PX["outline"] - OUTLINE_WIDTHS["thin"]


def still() -> bool:
    """Nothing on the canvas changes on its own (CTG_NO_ANIMATION, the tests): no glide, no
    fade, and a note stays until it is ended (:func:`start_note`)."""
    import os

    return bool(os.environ.get("CTG_NO_ANIMATION"))


def start_note(timer: QtCore.QTimer, ms: int) -> None:
    """``timer`` (the end of a note the canvas shows) set to ``ms`` and started; with
    :func:`still` it is set but not started."""
    timer.setInterval(ms)
    if still():
        timer.stop()
    else:
        timer.start()


def note_ms(ms: float) -> int:
    """``ms`` times Settings > Appearance > Messages stay (ui/notify.TIME_FACTOR: short 0.7,
    normal 1, long 1.6): how long the canvas's notes stay (the arrow keys' readout, an
    Alt+click's note and its flash, the alignment lines after an arrow key)."""
    from ..notify import TIME_FACTOR, message_time

    return max(1, int(round(float(ms) * TIME_FACTOR[message_time()])))


def set_canvas_lines(guide=None, reach=None, outline=None) -> None:
    """The three settings by name ('thin' / 'medium' / 'thick', the reach 'near' / 'medium' /
    'far'); anything else: the first."""
    LINES_PX["guide"] = GUIDE_WIDTHS.get(str(guide or ""), GUIDE_WIDTHS["thin"])
    LINES_PX["reach"] = GUIDE_REACHES.get(str(reach or ""), GUIDE_REACHES["near"])
    LINES_PX["outline"] = OUTLINE_WIDTHS.get(str(outline or ""), OUTLINE_WIDTHS["thin"])


# Settings > Canvas > Mouse (canvas/pointer_reach, canvas/drag_start), Editing > Moving and
# resizing (view/slow_mouse_factor) and Appearance > Canvas (view/guide_colour), set by
# ui/editor/canvas_options.apply_canvas_settings
#: the screen px every grab gets on top of its own reach: the sizing handles, the nodes, the
#: rotation grip, a corner's turn zone, the hot spot and the outline a node is added on
#: ('near' keeps the Graphics Builder's 7 x 7 node box)
POINTER_REACHES = {"near": 0.0, "medium": 3.0, "far": 6.0}
#: how far the pointer moves after a press before a click becomes a drag (screen px)
DRAG_STARTS = {"short": 3, "medium": 6, "long": 10}
#: Mouse Slow Speed: the part of the pointer's travel a drag follows
SLOW_MOUSE_FACTORS = {"half": 0.5, "quarter": 0.25, "tenth": 0.1}
POINTER = {"reach": 0.0, "drag": DRAG_STARTS["short"]}
SLOW_MOUSE = {"factor": SLOW_MOUSE_FACTORS["quarter"]}
#: the guideline colour: None = the theme's light blue (theme.FIXED['guide']), else 0xRRGGBB
GUIDE_RGB = {"rgb": None}


def pointer_reach() -> float:
    """The extra screen px a press may be away from a handle, a node, the rotation grip, a
    corner's turn zone, the hot spot or a reshaped outline (canvas/pointer_reach)."""
    return POINTER["reach"]


def drag_start() -> int:
    """How far the pointer moves after a press before it drags, in screen px
    (canvas/drag_start)."""
    return POINTER["drag"]


def drag_page_px(base: float, zoom: float) -> float:
    """A click-or-drag test made in page px (a drawn box, a line, a selection box): ``base``
    page px at the shortest drag start, else at least the drag start on screen."""
    n = POINTER["drag"]
    if n <= DRAG_STARTS["short"]:
        return float(base)
    return max(float(base), n / max(zoom, 0.01))


def set_pointer_options(reach=None, drag=None) -> None:
    """The two settings by name ('near' / 'medium' / 'far', 'short' / 'medium' / 'long');
    anything else: the first. The edge scroll's own drag test follows (ui/editor/viewnav)."""
    from . import viewnav

    POINTER["reach"] = POINTER_REACHES.get(str(reach or ""), POINTER_REACHES["near"])
    POINTER["drag"] = DRAG_STARTS.get(str(drag or ""), DRAG_STARTS["short"])
    viewnav.set_edge_options(drag_px=POINTER["drag"])


def set_slow_mouse_factor(name) -> None:
    """'half' / 'quarter' / 'tenth' (anything else: a quarter)."""
    SLOW_MOUSE["factor"] = SLOW_MOUSE_FACTORS.get(str(name or ""), SLOW_MOUSE_FACTORS["quarter"])


def slow_mouse_factor() -> float:
    """The part of the pointer's travel a slow drag follows (Settings > Editing)."""
    return SLOW_MOUSE["factor"]


def guide_colour(alpha: int = 255) -> QtGui.QColor:
    """The guidelines' colour (view/guide_colour: the theme's light blue unless one is picked)
    with ``alpha``: the canvas draws them with it, the rulers their marks."""
    rgb = GUIDE_RGB["rgb"]
    if rgb is None:
        from ..style.theme import fixed

        c = fixed("guide")
    else:
        c = QtGui.QColor((rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF)
    c.setAlpha(int(alpha))
    return c


def set_guide_colour(value) -> None:
    """'default' or '#rrggbb' (anything else: the default)."""
    s = str(value or "").strip().lower()
    rgb = None
    if len(s) == 7 and s.startswith("#"):
        try:
            rgb = int(s[1:], 16)
        except ValueError:  # swallow-ok: a garbled colour reads as the default
            rgb = None
    GUIDE_RGB["rgb"] = rgb


#: Settings > Canvas > Modifier keys (canvas/modifier_scheme) and Drag to copy with
#: (canvas/drag_copy_key): read by :func:`modifier_roles`
MODIFIER_SCHEMES = ("builder", "design")
DRAG_COPY_KEYS = ("ctrl", "alt")
KEY_ROLES = {"scheme": "builder", "copy": "ctrl"}


def set_modifier_scheme(scheme: str, copy_key: str | None = None) -> None:
    """The keys the canvas reads: 'builder' (Ctrl keeps proportions and constrains, Shift
    works from the centre: the Graphics Builder's draw keys) or 'design' (Shift keeps
    proportions and constrains, Alt from the centre); the key that makes a drag copy."""
    KEY_ROLES["scheme"] = scheme if scheme in MODIFIER_SCHEMES else "builder"
    if copy_key is not None:
        KEY_ROLES["copy"] = copy_key if copy_key in DRAG_COPY_KEYS else "ctrl"


def modifier_scheme() -> str:
    return KEY_ROLES["scheme"]


def drag_copy_key() -> str:
    return KEY_ROLES["copy"]


def modifier_roles(mods, moving: bool = False) -> frozenset:
    """What the held modifier keys mean on the canvas."""
    ctrl = bool(mods & QtCore.Qt.ControlModifier)
    shift = bool(mods & QtCore.Qt.ShiftModifier)
    alt = bool(mods & (QtCore.Qt.AltModifier | QtCore.Qt.MetaModifier))
    design = KEY_ROLES["scheme"] == "design"
    out = set()
    prop_key = shift if design else ctrl
    if prop_key:
        out.add("proportional")
    if alt if design else shift:
        out.add("centre")
    copy_alt = KEY_ROLES["copy"] == "alt"
    constrain = prop_key
    if moving:
        if alt if copy_alt else ctrl:
            out.add("copy")
        if not design and not copy_alt:
            constrain = shift  # (Ctrl copies: Shift constrains the move)
    if constrain:
        out.add("constrain")
    if alt and (shift or not copy_alt):
        out.add("free")
    return frozenset(out)


def signed(v: float) -> str:
    """An offset for a readout: '+40', '-3', '0'."""
    n = int(round(v))
    return "%+d" % n if n else "0"
