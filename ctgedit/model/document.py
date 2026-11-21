"""Document model for graphics pages."""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# --------------------------------------------------------------------------- enums
class ObjectKind(str, Enum):
    LINE = "line"
    RECTANGLE = "rectangle"
    ELLIPSE = "ellipse"
    POLYGON = "polygon"
    PIPE = "pipe"
    TEXT = "text"
    NUMBER = "number"
    BUTTON = "button"
    SYMBOL_SET = "symbol_set"  # symbol-swap animated set (tc 0x0C with its state
    # children; also the 0x100D / SYMBOL-carrying 0x1009
    # symbol displays)
    TREND = "trend"
    CICODE_OBJECT = "cicode_object"
    SYMBOL = "symbol"  # placed library symbol
    GENIE = "genie"
    COMPOSITE_GENIE = "composite_genie"
    GROUP = "group"
    UNKNOWN = "unknown"


class TextJustify(str, Enum):
    LEFT = "left"
    CENTRE = "centre"
    RIGHT = "right"


# --------------------------------------------------------------------------- geometry/style
@dataclass
class Transform:
    """2x3 affine matrix as exposed by AttributeTransformationMatrix (A,B,C,D,H,K)."""

    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    h: float = 0.0  # translate x
    k: float = 0.0  # translate y

    def is_identity(self) -> bool:
        return (self.a, self.b, self.c, self.d, self.h, self.k) == (1, 0, 0, 1, 0, 0)


@dataclass
class Geometry:
    x: int = 0
    y: int = 0
    extent_x: int = 0
    extent_y: int = 0
    # node coordinates for polygons / multi-point lines / pipes: list of (x, y)
    nodes: list[tuple[int, int]] = field(default_factory=list)
    transform: Transform = field(default_factory=Transform)


@dataclass
class Style:
    line_width: int = 1
    line_style: int = 0
    line_colour: Optional[int] = None  # packed RGB (0x00BBGGRR style; reader normalizes)
    filled: bool = False
    fill_colour: Optional[int] = None
    three_d: int = 0
    three_d_depth: int = 0
    shadow_colour: Optional[int] = None
    hilight_colour: Optional[int] = None
    lolight_colour: Optional[int] = None
    rectangle_style: int = 0  # 0 none / 1 border / 2 extra line / 3 both
    corner_radius: int = 0  # rounded rects (0..32)
    gradient_mode: int = 0  # 0 off, 1 L>R .. 8 Middle>VEdge
    gradient_colour: Optional[int] = None  # second gradient colour (packed RGB)
    ellipse_style: int = 0  # 0 ellipse / 1 pie / 2 arc
    start_angle: float = 0.0
    end_angle: float = 0.0
    polygon_open: bool = False
    # text
    text: str = ""
    font: str = ""
    font_size: int = 0  # POINTS (0 = renderer default)
    text_colour: Optional[int] = None
    justify: TextJustify = TextJustify.LEFT
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strikeout: bool = False
    vertical: bool = False  # orientation 3 = vertical text
    # the record's own 6 style flag bytes [effects, line_style, fill_style,
    # attribs, line_width, effect_depth] (b"" without a style block): the
    # renderer's 3D effects / pen caps read the raw values
    style_flags: bytes = b""


# --------------------------------------------------------------------------- animations
@dataclass
class Visibility:
    expression: str = ""


@dataclass
class Movement:
    """Transformation animation: rotation / horizontal+vertical scaling / slider."""

    rotation_expr: str = ""
    scale_h_expr: str = ""
    scale_v_expr: str = ""
    move_h_expr: str = ""
    move_v_expr: str = ""
    slider_expr: str = ""
    range_min: Optional[float] = None
    range_max: Optional[float] = None
    offset_min: Optional[int] = None
    offset_max: Optional[int] = None
    centre_offset_right: int = 0
    centre_offset_down: int = 0
    raw: dict = field(default_factory=dict)


@dataclass
class FillColourThreshold:
    expression: str = ""
    colour: Optional[int] = None
    limit: Optional[float] = None
    operator: int = 0


@dataclass
class FillColour:
    range_on: bool = False
    thresholds: list[FillColourThreshold] = field(default_factory=list)


@dataclass
class FillLevel:
    expression: str = ""
    use_range: bool = False
    direction: int = 0
    background_colour: Optional[int] = None
    range_min: Optional[float] = None
    range_max: Optional[float] = None


@dataclass
class SymbolSetAnim:
    type: int = 0  # TXTMODE: 0 On/Off, 1 Multi-state, 2 Array, 3 Animated
    expressions: list[str] = field(default_factory=list)  # TXT_A..E (up to 5) / SET_ARR
    symbol_index: int = 0
    raw: dict = field(default_factory=dict)


@dataclass
class DisplayValue:
    expression: str = ""
    is_text: bool = False
    format: str = ""


@dataclass
class InputTouch:
    command: str = ""
    logging: str = ""
    repeat_rate: int = 0


@dataclass
class InputKeyboard:
    key_sequence: str = ""
    command: str = ""
    area: int = 0
    privilege: int = 0


@dataclass
class AccessGeneral:
    name: str = ""
    description: str = ""
    tooltip: str = ""
    log_device: str = ""


@dataclass
class AccessDisable:
    insufficient: bool = False
    disable_style: int = 0
    expression: str = ""


@dataclass
class TrendAnim:
    samples: int = 0
    pixels_per_sample: int = 0
    pens: list[tuple[str, Optional[int]]] = field(default_factory=list)  # (expression, colour) up to 8


@dataclass
class Animations:
    """All dynamic behaviours attached to one object (each optional)."""

    visibility: Optional[Visibility] = None
    movement: Optional[Movement] = None
    fill_colour: Optional[FillColour] = None
    fill_level: Optional[FillLevel] = None
    symbol_set: Optional[SymbolSetAnim] = None
    display_value: Optional[DisplayValue] = None
    touch: list[InputTouch] = field(default_factory=list)
    keyboard: list[InputKeyboard] = field(default_factory=list)
    access_general: Optional[AccessGeneral] = None
    access_disable: Optional[AccessDisable] = None
    trend: Optional[TrendAnim] = None
    cicode_expression: str = ""


# --------------------------------------------------------------------------- objects / page
@dataclass
class GraphicObject:
    kind: ObjectKind = ObjectKind.UNKNOWN
    an: int = 0  # live animation number = geometry handle + 1 (-1 none)
    geometry: Geometry = field(default_factory=Geometry)
    style: Style = field(default_factory=Style)
    animations: Animations = field(default_factory=Animations)
    cls: str = ""  # AttributeClass
    locked: bool = False
    # group / genie membership
    children: list["GraphicObject"] = field(default_factory=list)
    genie_template: str = ""
    genie_params: dict = field(default_factory=dict)
    # record-forest relationships (indices into Page.objects). The on-disk object
    # region is a depth-first forest: a container record (Group/Genie, type_code
    # 0x08) is immediately followed by its inlined members. ``parent`` = container
    # index (None at top level); ``members`` = direct child indices;
    # ``container_count`` = stored direct-child count (u16 @ payload 0). Let the UI
    # treat a subtree as one unit.
    parent: Optional[int] = None
    members: list[int] = field(default_factory=list)
    container_count: int = 0
    is_genie: bool = False  # a container that carries a substitution table
    substitutions: dict = field(default_factory=dict)  # %param% -> value (genies)
    # preserve anything not yet modeled (key -> decoded/raw value)
    raw: dict = field(default_factory=dict)


@dataclass
class PageEvents:
    on_entry: str = ""
    on_exit: str = ""
    while_shown: str = ""
    on_shown: str = ""


@dataclass
class PageAppearance:
    template: str = ""
    style: str = ""
    resolution: str = ""
    width: int = 0
    height: int = 0
    background_colour: Optional[int] = None
    titlebar: bool = False


@dataclass
class Page:
    name: str = ""
    title: str = ""
    description: str = ""
    area: str = ""
    scan_time: str = ""
    log_device: str = ""
    guid: str = ""
    appearance: PageAppearance = field(default_factory=PageAppearance)
    events: PageEvents = field(default_factory=PageEvents)
    environment: dict = field(default_factory=dict)
    keyboard_commands: list[InputKeyboard] = field(default_factory=list)
    objects: list[GraphicObject] = field(default_factory=list)
    # provenance / round-trip aids
    source_path: str = ""
    ts_created: int = 0
    ts_modified: int = 0
    raw_attributes: dict = field(default_factory=dict)
    identity: str = ""  # 20-byte page identity (hex)
    thumbnail_bmp: bytes = b""  # Citect's own rendered page thumbnail (BMP)

    def add(self, obj: GraphicObject) -> GraphicObject:
        self.objects.append(obj)
        return obj

    # -- record-forest navigation -----------------------------------------
    def roots(self) -> list[int]:
        """Indices of top-level objects (no container parent)."""
        return [i for i, o in enumerate(self.objects) if o.parent is None]

    def root_of(self, index: int) -> int:
        """The top-level ancestor of object ``index`` (itself if top-level)."""
        seen = set()
        cur = index
        while 0 <= cur < len(self.objects):
            o = self.objects[cur]
            if o.parent is None or o.parent in seen:
                return cur
            seen.add(cur)
            cur = o.parent
        return index

    def ancestors(self, index: int) -> list[int]:
        """Container ancestors of ``index`` from nearest parent to top-level root."""
        out = []
        seen = set()
        cur = self.objects[index].parent if 0 <= index < len(self.objects) else None
        while cur is not None and cur not in seen:
            out.append(cur)
            seen.add(cur)
            cur = self.objects[cur].parent
        return out

    def nearest_genie(self, index: int) -> int | None:
        """The clicked object or its nearest genie ancestor, else None."""
        if 0 <= index < len(self.objects) and self.objects[index].is_genie:
            return index
        for a in self.ancestors(index):
            if self.objects[a].is_genie:
                return a
        return None

    def subtree(self, index: int) -> list[int]:
        """``index`` plus all descendant indices (depth-first)."""
        ends = self.__dict__.get("_subtree_ends")
        if ends is not None and 0 <= index < len(ends):
            # (a derive's record forest, wired from the records' child counts: a subtree is
            # the contiguous run up to its end -- io.reader sets this while it builds)
            return list(range(index, ends[index]))
        out = [index]
        stack = list(self.objects[index].members) if index < len(self.objects) else []
        while stack:
            i = stack.pop()
            out.append(i)
            if i < len(self.objects):
                stack.extend(self.objects[i].members)
        out.sort()
        return out
