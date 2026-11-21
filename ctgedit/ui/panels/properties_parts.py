"""The Properties panel's building blocks: its settings and look, the section widget, the
field keys and the helpers its forms share."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..style.theme import sp as _sp
import contextlib
import weakref
from PySide6 import QtCore, QtGui, QtWidgets
from ...format import inputs as IN
from ..number_field import NumberField

if TYPE_CHECKING:
    from .properties import PropertyPanel


NO_COLOUR = 0xFF000000
panel_INPUT_TCS = IN.INPUT_TCS  # Touch / Keyboard child records
LINE_STYLES = ["Solid", "Dash", "Dot", "Dash Dot", "Dash Dot Dot"]
QSS = """
QScrollArea { background: #1f2226; border: none; }
#propRoot { background: #1f2226; }
QWidget { font: 12px 'Segoe UI'; }
QLabel { color: #d6d9de; }
QLabel#dim { color: #969ca5; }
QGroupBox {
    background: #26292e; border: 1px solid #3a3f46; border-radius: 4px;
    margin-top: 10px; padding-top: 8px; color: #969ca5;
}
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 3px; }
QGroupBox#panelSection {
    background: transparent; border: none; border-top: 1px solid #3a3f46;
    border-radius: 0px; margin-top: 0px; padding-top: 0px;
}
/* (check boxes: the application style draws them, ui/style/theme.AppStyle) */
QLineEdit, QPlainTextEdit, QSpinBox, QComboBox {
    background: #1f2226; color: #d6d9de; border: 1px solid #6e7681;
    border-radius: 3px; padding: 2px 4px; selection-background-color: #2b4a6f;
}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {
    border: 1px solid #4da3ff;
}
QLineEdit:disabled, QPlainTextEdit:disabled, QSpinBox:disabled, QComboBox:disabled {
    background: #26292e; color: #969ca5; border: 1px solid #3a3f46;
}
QComboBox QAbstractItemView {
    background: #26292e; color: #d6d9de; border: 1px solid #3a3f46;
    selection-background-color: #2b4a6f;
}
QCheckBox { color: #d6d9de; }
QPushButton#swatch { border-radius: 3px; }
QToolButton {
    background: #1f2226; color: #d6d9de; border: 1px solid #3a3f46;
    border-radius: 3px; min-width: 20px; min-height: 18px; font-weight: 600;
}
QToolButton:hover { border-color: #4da3ff; }
QToolButton:checked { background: #2b4a6f; border-color: #4da3ff; }
QToolButton#sectionMenu {
    background: transparent; border: none; min-width: 16px; max-width: 18px;
    min-height: 16px; padding: 0px; font-weight: normal;
}
QToolButton#sectionChevron {
    background: transparent; border: none; min-width: 16px; min-height: 16px;
    padding: 0px 2px 0px 0px; color: #f0f2f5; font-weight: 600;
}
QToolButton#sectionChevron:hover, QToolButton#sectionMenu:hover { background: #2c3036; }
QToolButton#sectionMenu::menu-indicator { image: none; width: 0px; }
QToolButton#linkButton {
    background: transparent; border: none; color: #4da3ff; font-weight: normal;
    min-width: 0px; padding: 0px;
}
QToolButton#linkButton:hover { text-decoration: underline; }
QToolButton#chain {
    min-width: 18px; max-width: 18px; padding: 0px; border: none; background: transparent;
}
QToolButton#chain:hover { background: #2c3036; }
QToolButton#chain:checked { background: rgba(77, 163, 255, 56); }
QToolButton#swatchValue {
    background: transparent; border: none; color: #969ca5; font-weight: normal;
    min-width: 0px; padding: 0px 2px; text-align: left;
}
QToolButton#swatchValue:hover { color: #d6d9de; text-decoration: underline; }
QToolButton#arrangeButton { min-width: 22px; max-width: 26px; padding: 1px; }
QToolButton#segment { font-weight: normal; padding: 1px 6px; border-radius: 0px; }
QToolButton#segment:checked { background: #2b4a6f; border-color: #4da3ff; }
QToolButton[kbdFocus="true"], QToolButton#sectionChevron[kbdFocus="true"],
QToolButton#sectionMenu[kbdFocus="true"], QToolButton#linkButton[kbdFocus="true"],
QToolButton#chain[kbdFocus="true"], QToolButton#swatchValue[kbdFocus="true"],
QToolButton#arrangeButton[kbdFocus="true"], QToolButton#segment[kbdFocus="true"] {
    border: 2px solid #4da3ff; padding: 1px; }
*[searchHit="true"] { border: 1px solid #4da3ff; border-radius: 3px; }
"""
#: the dock width under which 'props/label_layout' Automatic puts the labels above the
#: fields (px)
LABELS_ABOVE_UNDER = 300
#: the narrowest a field gets (px): the panel fits its dock, fields shrink to this
FIELD_MIN_WIDTH = 60
#: the narrowest X / Y / W / H get (two of them share a row)
GEOMETRY_MIN_WIDTH = 50
#: an expression field narrower than this goes under its label (WrapLongRows)
EXPRESSION_MIN_WIDTH = 150
#: Settings: the label layout ('auto' / 'beside' / 'above'), the folded sections, the
#: Graphics Builder's private keys under All properties, the aspect lock and the point X / Y
#: show
LABEL_LAYOUT_KEY = "props/label_layout"
LABEL_LAYOUTS = ("auto", "beside", "above")
COLLAPSED_KEY = "props/collapsed"
PRIVATE_KEYS_KEY = "props/show_private_keys"
LOCK_ASPECT_KEY = "props/lock_aspect"
REFERENCE_KEY = "ui/geometry_reference"
#: several objects: X / Y / W / H apply to the selection box ('selection') or to each object
#: ('each'); the property search keeps its text when the selection changes
MULTI_APPLY_KEY = "props/multi_apply"
SEARCH_KEEP_KEY = "props/search_keep"
#: a text's 3D effect (set_text_effect: the style block's effects nibble) and a shape's
#: (set_effect), in the object sheet's order (objprops.appearance.EFFECTS)
panel_EFFECTS = (("None", 0), ("Shadowed", 3), ("Raised", 1), ("Lowered", 2), ("Embossed", 4))
TEXT_EFFECTS = (("Normal", 0), ("Shadowed", 3), ("Raised", 1), ("Lowered", 2), ("Embossed", 4))
JUSTIFY = (
    ("left", "Left", "justify_left"),
    ("centre", "Centre", "justify_centre"),
    ("right", "Right", "justify_right"),
)
#: what a search finds an Object Properties page by besides its title and its fields' labels
PAGE_WORDS = {
    "general": "colour fill line width style font size alignment caption text",
    "effects": "3d effect depth shadow highlight lowlight raised lowered embossed",
    "touch": "touch command up down repeat click",
    "keyboard": "keyboard key sequence command",
    "access": "name tool tip description area privilege device",
    "disable": "disable grey embossed",
    "metadata": "metadata",
    "identification": "identification equipment item",
    "vertices": "vertices points nodes",
    "linkage": "link library",
}
#: the 9 reference points: (setting value, label, x fraction, y fraction) in halves
REFERENCES = (
    ("top_left", "Top left", 0, 0),
    ("top", "Top centre", 1, 0),
    ("top_right", "Top right", 2, 0),
    ("left", "Left centre", 0, 1),
    ("centre", "Centre", 1, 1),
    ("right", "Right centre", 2, 1),
    ("bottom_left", "Bottom left", 0, 2),
    ("bottom", "Bottom centre", 1, 2),
    ("bottom_right", "Bottom right", 2, 2),
)
REF_BY_NAME = {r[0]: r for r in REFERENCES}
#: the section folded by default
DEFAULT_COLLAPSED = ["All properties"]
# The page's pages.DBF fields the panel edits (the rest: Page Properties):
# (column, label)
PAGE_RECORD_ROWS = [
    ("TITLE", "Title"),
    ("COMMENT", "Description"),
    ("AREA", "Area"),
    ("SCANTIME", "Scan time"),
    ("PREV_PAGE", "Previous page"),
    ("NEXT_PAGE", "Next page"),
    ("PARENT", "Parent page"),
    ("CON_TYPE", "Content type"),
]
#: the text an area field shows for no area (Page Properties > General)
panel_ALL_AREAS = "<All areas>"
# Animation families: (group title, [(prop key, row label, kind)...]) where
# kind is 'expr' (an expression field), 'command', 'number' (a number field: the range
# table of objprops.fields), or '' (plain text). The labels follow Object Properties.
FAMILY_VISIBILITY = [("VISIBLE", "Hidden when", "expr")]
FAMILY_FILL_LEVEL = [
    ("FIL", "Level expression", "expr"),
    # FILMODE = CtDraw's direction radio: a combo row
    # (_add_fill_direction); FILCOL = the unfilled part (a colour row)
    ("FILMINPC", "Fill at minimum", "number"),
    ("FILMAXPC", "Fill at maximum", "number"),
]
FAMILY_MOVE = [
    ("MVX", "Horizontal movement", "expr"),
    ("MVXMINPT", "Min offset", "number"),
    ("MVXMAXPT", "Max offset", "number"),
    ("MVY", "Vertical movement", "expr"),
    ("MVYMINPT", "Min offset", "number"),
    ("MVYMAXPT", "Max offset", "number"),
]
FAMILY_ROTATE = [
    ("ROT", "Rotation", "expr"),
    # ROTMINPC / ROTMAXPC hold DEGREES despite the 'PC' suffix (CtDraw's
    # Rotational tab 'Angle at minimum / maximum')
    ("ROTMINPC", "Min angle", "number"),
    ("ROTMAXPC", "Max angle", "number"),
]
FAMILY_SCALE = [
    ("SZX", "Horizontal scaling", "expr"),
    ("SZXMINPC", "Min size", "number"),
    ("SZXMAXPC", "Max size", "number"),
    ("SZY", "Vertical scaling", "expr"),
    ("SZYMINPC", "Min size", "number"),
    ("SZYMAXPC", "Max size", "number"),
]
# (the Center axis offsets -- ROTMODE / ROTXORG / ROTYORG, SZ?MODE / SZ?ORG --
# are the 'centre' / 'anchor' rows of their group: _add_axis_rows)
#: what each movement / scaling row means (the rows' tool tips: the long labels)
ROW_TIPS = {
    "MVXMINPT": "Offset at minimum (left). How far the object moves left at the minimum (negative = right)",
    "MVXMAXPT": "Offset at maximum (right). How far the object moves right at the maximum (negative = left)",
    "MVYMINPT": "Offset at minimum (down). How far the object moves down at the minimum (negative = up)",
    "MVYMAXPT": "Offset at maximum (up). How far the object moves up at the maximum (negative = down)",
    "ROTMINPC": "Angle at minimum (anticlockwise). How far the object turns anticlockwise at the minimum",
    "ROTMAXPC": "Angle at maximum (clockwise). How far the object turns clockwise at the maximum",
    "SZXMINPC": "Width at minimum. The width at the minimum, in % of the drawn width",
    "SZXMAXPC": "Width at maximum. The width at the maximum, in % of the drawn width",
    "SZYMINPC": "Height at minimum. The height at the minimum, in % of the drawn height",
    "SZYMAXPC": "Height at maximum. The height at the maximum, in % of the drawn height",
    "FILMINPC": "How full (0 to 100 %) the object is at the expression's minimum",
    "FILMAXPC": "How full (0 to 100 %) the object is at the expression's maximum",
}
#: the panel's Center axis offset rows: (label, objprops.motion.AXES family,
#: the keys that switch the row on, the object sheet page that edits a custom one)
AXIS_ROWS = [
    ("Rotation centre", "rot", ("ROT", "ROTSL"), "move_rot"),
    ("Horizontal scaling anchor", "sx", ("SZX",), "scale_h"),
    ("Vertical scaling anchor", "sy", ("SZY",), "scale_v"),
]
FAMILY_INPUT = [
    ("CMD", "Command", "command"),
    ("ACTION", "Action", "command"),
    ("KEY_SEQ", "Key sequence", ""),
    ("RPT_RATE", "Repeat rate", "number"),
]
FAMILY_DISABLE = [
    ("DISABLED", "Disable when", "expr"),
]
# (no TXT_OFF row: a text's OFF text IS its caption -- the Text field above,
# which writes the tail the loader reads; a TXT_OFF edited alone is lost on load)
FAMILY_DISPLAY = [
    ("TXT_ON", "ON text", ""),
    ("TXT_A", "Condition A", "expr"),
    ("TXT_B", "Condition B", "expr"),
    ("TXT_C", "Condition C", "expr"),
    ("TXT_D", "Condition D", "expr"),
    ("TXT_E", "Condition E", "expr"),
    ("FORMAT", "Format", ""),
    ("TXTMODE", "Mode", ""),
]
#: the raw property keys the All properties rows edit as expressions / commands
EXPR_KEYS = {
    k
    for fam in (
        FAMILY_VISIBILITY,
        FAMILY_FILL_LEVEL,
        FAMILY_MOVE,
        FAMILY_ROTATE,
        FAMILY_SCALE,
        FAMILY_INPUT,
        FAMILY_DISABLE,
        FAMILY_DISPLAY,
    )
    for k, _l, kind in fam
    if kind == "expr"
} | {"COL_A", "COL_B", "COL_C", "COL_D", "COL_E", "COL_ARR", "COL_EXP", "MVXSL", "MVYSL", "ROTSL", "TXT_STR"}
COMMAND_KEYS = {"CMD", "ACTION", "DOWN_CMD", "REPEAT_CMD", "DBLCLK_CMD"}
#: raw key -> its row label (status messages name the setting, not the key)
KEY_LABELS = {
    k: label
    for fam in (
        FAMILY_VISIBILITY,
        FAMILY_FILL_LEVEL,
        FAMILY_MOVE,
        FAMILY_ROTATE,
        FAMILY_SCALE,
        FAMILY_INPUT,
        FAMILY_DISABLE,
        FAMILY_DISPLAY,
    )
    for k, label, _kind in fam
}
KEY_LABELS.update(
    {
        "COL_MAXON": "ON colour",
        "COMMENT": "Tool tip",
        "DESC": "Description",
        "AREA": "Area",
        "PRIV": "Privilege level",
        "__AN_NAME__": "Name",
        "TXT_STR": "Display expression",
        "COLMODE": "Fill colour type",
        "COL_A": "Colour condition A",
        "COL_B": "Colour condition B",
        "COL_C": "Colour condition C",
        "COL_D": "Colour condition D",
        "COL_E": "Colour condition E",
        "COL_ARR": "Colour array expression",
        "COL_EXP": "Colour expression",
        "FILMODE": "Fill direction",
        "FILCOL": "Level background colour",
        "GREY_STYLE": "Disable style",
    }
)
# (the movement / rotation / scaling rows share short labels; messages and All properties
# name them in full)
KEY_LABELS.update(
    {
        "MVXMINPT": "Horizontal offset at minimum",
        "MVXMAXPT": "Horizontal offset at maximum",
        "MVYMINPT": "Vertical offset at minimum",
        "MVYMAXPT": "Vertical offset at maximum",
        "ROTMINPC": "Angle at minimum",
        "ROTMAXPC": "Angle at maximum",
        "SZXMINPC": "Width at minimum",
        "SZXMAXPC": "Width at maximum",
        "SZYMINPC": "Height at minimum",
        "SZYMAXPC": "Height at maximum",
    }
)
#: Fill level direction (FILMODE: absent / 0 Up, 1 Down, 2 Left, 3 Right)
FILL_DIRECTIONS = ("Up", "Down", "Left", "Right")
# 'Add animation…' entries: (menu label, property key, default value). Touch
# and keyboard commands are CHILD records (format.inputs), not bag keys: the
# INPUT_* markers route them to the Input section. (The tool tip is a field of the
# identity rows, always shown.)
INPUT_TOUCH, INPUT_KEYBOARD = "@touch", "@keyboard"
#: 'Display value': a text shows a value (Appearance > Display Value, Numeric)
DISPLAY_VALUE = "@display"
# (movement / rotation / scaling also write CtDraw's companion defaults --
# objprops.motion.COMPANIONS -- so the offsets / angles / sizes are set; a fill level
# the keys the sheet's Fill > Level page writes with a new level: _on_add_animation)
ADDABLE = [
    ("Visibility", "VISIBLE", ""),
    ("Movement (horizontal)", "MVX", ""),
    ("Movement (vertical)", "MVY", ""),
    ("Rotation", "ROT", ""),
    ("Scaling (horizontal)", "SZX", ""),
    ("Scaling (vertical)", "SZY", ""),
    ("Fill colour", "COL_MAXON", "0x00ffffff,0x00ffffff"),
    ("Fill level", "FIL", ""),
    ("Touch command", INPUT_TOUCH, ""),
    ("Keyboard command", INPUT_KEYBOARD, ""),
    ("Disable", "DISABLED", ""),
    ("Display value", DISPLAY_VALUE, ""),
]
#: the section each 'Add animation…' entry shows, and the field that takes the focus
ADDED_SECTION = {
    "VISIBLE": ("Visibility", "VISIBLE"),
    "MVX": ("Movement / Scaling", "MVX"),
    "MVY": ("Movement / Scaling", "MVY"),
    "ROT": ("Movement / Scaling", "ROT"),
    "SZX": ("Movement / Scaling", "SZX"),
    "SZY": ("Movement / Scaling", "SZY"),
    "COL_MAXON": ("Fill colour", "COL_A"),
    "FIL": ("Fill level", "FIL"),
    INPUT_TOUCH: ("Input", "CMD"),
    INPUT_KEYBOARD: ("Input", "@key"),
    "DISABLED": ("Disable", "DISABLED"),
    DISPLAY_VALUE: ("Display value", "TXT_STR"),
}
#: the object sheet page an 'Add animation…' key needs (registry ids)
ADD_PAGES = {
    "VISIBLE": "visibility",
    "MVX": "move_h",
    "MVY": "move_v",
    "ROT": "move_rot",
    "SZX": "scale_h",
    "SZY": "scale_v",
    "COL_MAXON": "fill_color",
    "FIL": "fill_level",
    INPUT_TOUCH: "touch",
    INPUT_KEYBOARD: "keyboard",
    "DISABLED": "disable",
    DISPLAY_VALUE: "display_value",
}
# the panel's touch rows: (key, label)
TOUCH_ROWS = [
    ("CMD", "Up command"),
    ("DOWN_CMD", "Down command"),
    ("REPEAT_CMD", "Repeat command"),
    ("DBLCLK_CMD", "Double-click command"),
    ("RPT_RATE", "Repeat rate"),
]


def parse_colour_pair(value: str) -> list[int]:
    """Parse a 'COL_MAXON'-style '0x%08x,0x%08x' pair; tolerant of junk."""
    try:
        a, b = (s.strip() for s in value.split(",")[:2])
        return [int(a, 16) & 0xFFFFFFFF, int(b, 16) & 0xFFFFFFFF]
    except ValueError, AttributeError:
        return [0x00FFFFFF, 0x00FFFFFF]


def is_colour_pair(value) -> bool:
    """``value`` is a stored '0x%08x,0x%08x' colour pair."""
    import re

    return bool(re.fullmatch(r"\s*0x[0-9a-fA-F]{8}\s*,\s*0x[0-9a-fA-F]{8}\s*", str(value or "")))


def match_font_family(text: str):
    """The installed font family ``text`` names: the exact name without case, else the
    first family that starts with it; None when there is none."""
    t = (text or "").strip().lower()
    if not t:
        return None
    families = QtGui.QFontDatabase.families()
    for f in families:
        if f.lower() == t:
            return f
    for f in families:
        if f.lower().startswith(t):
            return f
    return None


# --------------------------------------------------------------------------- settings
def panel_setting(key: str, default):
    try:
        from ..prefs.settings import app_settings

        v = app_settings().value(key, default)
    except Exception:  # noqa: BLE001  # swallow-ok: no settings (a tool): the default
        return default
    return default if v is None else v


def set_setting(key: str, value) -> None:
    try:
        from ..prefs.settings import app_settings

        app_settings().setValue(key, value)
    except Exception:  # noqa: BLE001  # swallow-ok: not remembered then
        pass


def label_layout() -> str:
    """'auto' (labels above the fields under LABELS_ABOVE_UNDER px), 'beside' or 'above'."""
    v = str(panel_setting(LABEL_LAYOUT_KEY, "auto") or "auto")
    return v if v in LABEL_LAYOUTS else "auto"


def collapsed_sections() -> list:
    try:
        from ..prefs.settings import app_settings

        st = app_settings()
        # (QSettings keeps an empty list as @Invalid(), read back as None: a key that is
        # there but empty means none is folded, not the default)
        if st.contains(COLLAPSED_KEY) and st.value(COLLAPSED_KEY) in (None, ""):
            return []
    except Exception:  # noqa: BLE001  # swallow-ok: no settings (a tool): the default
        pass
    v = panel_setting(COLLAPSED_KEY, list(DEFAULT_COLLAPSED))
    if isinstance(v, str):
        v = [v] if v else []
    return [str(x) for x in (v or [])]


def show_private_keys() -> bool:
    v = panel_setting(PRIVATE_KEYS_KEY, False)
    return v is True or str(v).lower() == "true"


def lock_aspect() -> bool:
    v = panel_setting(LOCK_ASPECT_KEY, False)
    return v is True or str(v).lower() == "true"


def geometry_reference() -> str:
    v = str(panel_setting(REFERENCE_KEY, "top_left") or "top_left")
    return v if v in REF_BY_NAME else "top_left"


def multi_apply() -> str:
    """'selection' (X / Y / W / H of several objects move and size the selection box) or
    'each' (every object takes the value)."""
    v = str(panel_setting(MULTI_APPLY_KEY, "selection") or "selection")
    return v if v in ("selection", "each") else "selection"


def search_keep() -> bool:
    v = panel_setting(SEARCH_KEEP_KEY, True)
    return v is True or str(v).lower() in ("true", "1")


def panel_option(name: str) -> bool:
    try:
        from ..prefs.settings import option

        return bool(option(name))
    except Exception:  # noqa: BLE001  # swallow-ok: no settings: the default
        from ..prefs.settings import OPTION_DEFAULTS

        return bool(OPTION_DEFAULTS.get(name, False))


def panel_fold_key(title: str) -> str:
    """The name a section's folded state is kept under ('Text (2 texts)' -> 'Text')."""
    return str(title).split(" (")[0]


# --------------------------------------------------------------------------- small icons
def paint_icon(draw, w: int = 16, h: int = 16) -> QtGui.QIcon:
    ic = QtGui.QIcon()
    for scale in (1, 2):
        pm = QtGui.QPixmap(w * scale, h * scale)
        pm.setDevicePixelRatio(scale)
        pm.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        draw(p)
        p.end()
        ic.addPixmap(pm)
    return ic


#: (icon name, colours) -> QIcon: the panel's painted icons, painted once per theme colour
#: (every section header has one)
ICON_CACHE: dict = {}


def cached_icon(name: str, colours: tuple, paint) -> QtGui.QIcon:
    key = (name,) + tuple(colours)
    ic = ICON_CACHE.get(key)
    if ic is None:
        ic = ICON_CACHE[key] = paint()
    return ic


def dots_icon() -> QtGui.QIcon:
    from ..style.theme import COLORS

    name = COLORS.get("text_dim", "#969ca5")
    return cached_icon("dots", (name,), lambda: paint_dots(name))


def paint_dots(colour: str) -> QtGui.QIcon:
    col = QtGui.QColor(colour)

    def draw(p):
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(col)
        for x in (3.5, 8.0, 12.5):
            p.drawEllipse(QtCore.QPointF(x, 8.0), 1.4, 1.4)

    return paint_icon(draw)


def chain_icon() -> QtGui.QIcon:
    from ..style.theme import COLORS

    names = (COLORS.get("text_dim", "#969ca5"), COLORS.get("accent", "#4da3ff"))
    return cached_icon("chain", names, lambda: paint_chain(*names))


def paint_chain(off_colour: str, on_colour: str) -> QtGui.QIcon:
    off = QtGui.QColor(off_colour)
    on = QtGui.QColor(on_colour)
    ic = QtGui.QIcon()
    for state, col in ((QtGui.QIcon.Off, off), (QtGui.QIcon.On, on)):
        for scale in (1, 2):
            pm = QtGui.QPixmap(14 * scale, 14 * scale)
            pm.setDevicePixelRatio(scale)
            pm.fill(QtCore.Qt.transparent)
            p = QtGui.QPainter(pm)
            p.setRenderHint(QtGui.QPainter.Antialiasing)
            p.setPen(QtGui.QPen(col, 1.4))
            p.setBrush(QtCore.Qt.NoBrush)
            # two chain links, joined (on) or apart (off), turned 45 degrees
            p.translate(7.0, 7.0)
            p.rotate(-45.0)
            gap = 0.0 if state == QtGui.QIcon.On else 1.6
            p.drawRoundedRect(QtCore.QRectF(-6.0 - gap, -2.2, 6.6, 4.4), 2.2, 2.2)
            p.drawRoundedRect(QtCore.QRectF(-0.6 + gap, -2.2, 6.6, 4.4), 2.2, 2.2)
            p.end()
            ic.addPixmap(pm, QtGui.QIcon.Normal, state)
    return ic


def hatched_pixmap(w: int = 40, h: int = 14) -> QtGui.QPixmap:
    """The swatch of a colour the objects do not share ('Mixed'): diagonal hatching."""
    from ..style.theme import color

    pm = QtGui.QPixmap(w, h)
    pm.fill(color("bg"))
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    p.setPen(QtGui.QPen(color("text_dim"), 1))
    for x in range(-h, w, 5):
        p.drawLine(QtCore.QPointF(x, h), QtCore.QPointF(x + h, 0))
    p.setPen(QtGui.QPen(QtGui.QColor("#555555"), 1))
    p.setBrush(QtCore.Qt.NoBrush)
    p.drawRect(QtCore.QRect(0, 0, w - 1, h - 1))
    p.end()
    return pm


# --------------------------------------------------------------------------- panel pieces
class panel_Pages(QtWidgets.QWidget):
    """What the panel shows: the page forms (one per open document, kept, never moved to
    another parent) and the object form, one at a time; the others are hidden, so they take
    no room (a QStackedWidget is as big as its biggest page)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._current = None

    def addWidget(self, w: QtWidgets.QWidget) -> None:  # noqa: N802 -- QStackedWidget's
        w.hide()
        self.layout().addWidget(w)

    def currentWidget(self):  # noqa: N802
        w = self._current
        try:
            if w is not None:
                w.isHidden()  # (deleted: RuntimeError)
        except RuntimeError:
            self._current = w = None
        return w

    def setCurrentWidget(self, w: QtWidgets.QWidget) -> None:  # noqa: N802
        cur = self.currentWidget()
        if cur is w:
            return
        if cur is not None:
            cur.hide()
        self._current = w
        w.show()

    def forget_current(self) -> None:
        """No form is shown (the one shown is being dropped)."""
        self._current = None


class MenuButton(QtWidgets.QToolButton):
    """A section header's '⋯' button: its menu is made the first time it is wanted (a
    press, a key, :meth:`menu`), not with every section of every build."""

    def __init__(self, section: "PanelSection", parent=None):
        super().__init__(parent)
        self._section = weakref.ref(section)

    def menu(self):  # noqa: D102 -- QToolButton.menu, made on demand
        m = super().menu()
        sec = self._section()
        if m is None and sec is not None:
            m = QtWidgets.QMenu(self)
            m.aboutToShow.connect(lambda mm=m: sec._fill_menu(mm))
            self.setMenu(m)
        return m

    def mousePressEvent(self, e) -> None:  # noqa: N802 -- Qt API
        self.menu()
        super().mousePressEvent(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802 -- Qt API
        self.menu()
        super().keyPressEvent(e)


class SectionHeader(QtWidgets.QHBoxLayout):
    """A section's header row, a layout of two buttons: the chevron with the title beside it
    (a click or Space folds; the title elides in a narrow dock) and the '⋯' menu button. Two
    widgets, not a header widget with a chevron, a title label and the menu (every widget of
    a form is built, styled and dropped with each selection)."""

    def __init__(self, section: "PanelSection"):
        super().__init__()
        self.setContentsMargins(0, _sp(0), 0, _sp(0))
        self.setSpacing(_sp(0))
        self.chevron = QtWidgets.QToolButton(section)
        self.chevron.setObjectName("sectionChevron")
        self.chevron.setAutoRaise(True)
        self.chevron.setArrowType(QtCore.Qt.DownArrow)
        self.chevron.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.chevron.setFocusPolicy(QtCore.Qt.TabFocus)
        # (its width follows the dock, never the title's: a long title elides)
        self.chevron.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        self.chevron.setMinimumWidth(40)
        self.chevron.clicked.connect(lambda: section.toggle())
        self.addWidget(self.chevron, 1)
        self.menu_button = MenuButton(section, section)
        self.menu_button.setObjectName("sectionMenu")
        self.menu_button.setAutoRaise(True)
        self.menu_button.setIcon(dots_icon())
        self.menu_button.setToolTip("More")
        self.menu_button.setAccessibleName("Section menu")
        self.menu_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.menu_button.setFocusPolicy(QtCore.Qt.TabFocus)
        self.addWidget(self.menu_button)

    def set_title(self, title: str) -> None:
        self.chevron.setText(title)


class PanelSection(QtWidgets.QGroupBox):
    """A folding section of the Properties panel."""

    foldChanged = QtCore.Signal(bool)

    def __init__(
        self,
        title: str,
        panel: "PropertyPanel",
        *,
        open_page=None,
        remove=None,
        remove_text: str = "Remove",
        extra_menu=None,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("panelSection")
        self.setFlat(True)
        self._title = str(title)
        self._panel = weakref.ref(panel)
        self._open_page = open_page
        self._remove = remove
        self._remove_text = remove_text
        self._extra_menu = extra_menu
        form = QtWidgets.QFormLayout(self)
        panel.style_form(form)
        form.setContentsMargins(_sp(1), _sp(1), _sp(1), _sp(2))
        self.header = SectionHeader(self)
        self.header.set_title(self._title)
        form.addRow(self.header)
        self._folded = panel_fold_key(title) in collapsed_sections()
        #: the rows were hidden once (an unfolded section that never was skips the loop)
        self._rows_hidden = False
        #: ``fn()`` that adds the rows on the first unfold (:meth:`set_lazy`)
        self._lazy = None

    # -- the QGroupBox calls -------------------------------------------------------------
    def title(self) -> str:  # noqa: D102 -- the section's title (Qt's own stays empty)
        return self._title

    def setTitle(self, title: str) -> None:  # noqa: N802,D102
        self._title = str(title)
        self.header.set_title(self._title)

    def form(self) -> QtWidgets.QFormLayout:
        return self.layout()

    # -- folding ------------------------------------------------------------------------
    def folded(self) -> bool:
        return self._folded

    def set_lazy(self, fill) -> None:
        """Add the rows with ``fill()`` when the section is first unfolded (now when it is
        not folded): a section folded by default costs nothing to build."""
        if self._folded:
            self._lazy = fill
        else:
            fill()

    def is_filled(self) -> bool:
        """The rows are there (a lazy section folded since it was built has none yet)."""
        return self._lazy is None

    def _fill_now(self) -> None:
        fill, self._lazy = self._lazy, None
        if fill is None:
            return
        fill()
        panel = self._panel()
        if panel is not None:
            panel._finish_section(self)

    def set_folded(self, on: bool, remember: bool = True) -> None:
        self._folded = bool(on)
        if not self._folded:
            self._fill_now()
        self.sync_fold()
        if remember:
            key = panel_fold_key(self._title)
            keys = [k for k in collapsed_sections() if k != key]
            if on:
                keys.append(key)
            set_setting(COLLAPSED_KEY, keys)
        self.foldChanged.emit(self._folded)

    def toggle(self) -> None:
        self.set_folded(not self._folded)

    def sync_fold(self) -> None:
        """Show / hide the rows as the folded state says (rows added after it was set)."""
        form = self.layout()
        if self._folded or self._rows_hidden:
            for r in range(1, form.rowCount()):
                form.setRowVisible(r, not self._folded)
            self._rows_hidden = self._folded
        self.header.chevron.setArrowType(QtCore.Qt.RightArrow if self._folded else QtCore.Qt.DownArrow)
        self.header.chevron.setToolTip("Unfold" if self._folded else "Fold")
        self.header.chevron.setAccessibleName("%s %s" % ("Unfold" if self._folded else "Fold", self._title))

    def flash(self, ms: int = 600) -> None:
        """Highlight the header in the accent colour for a moment (a section just added)."""
        hdr = self.header.chevron
        hdr.setStyleSheet(
            "QToolButton#sectionChevron { background: rgba(77, 163, 255, 90); border-radius: 3px; }"
        )

        def clear():
            with contextlib.suppress(RuntimeError):
                hdr.setStyleSheet("")

        QtCore.QTimer.singleShot(int(ms), hdr, clear)

    # -- the menu -------------------------------------------------------------------------
    def _fill_menu(self, menu: QtWidgets.QMenu) -> None:
        menu.clear()
        panel = self._panel()
        if self._open_page is not None:
            a = menu.addAction("Open in Object Properties")
            a.triggered.connect(lambda: self._open_page())
        if self._remove is not None:
            a = menu.addAction(self._remove_text)
            a.setEnabled(panel is not None and not panel.is_read_only())
            a.triggered.connect(lambda: self._remove())
        if self._extra_menu is not None:
            self._extra_menu(menu)
        if not menu.isEmpty():
            menu.addSeparator()
        a = menu.addAction("Fold All Others")
        a.triggered.connect(self._fold_others)
        if panel is not None:
            sub = menu.addMenu("Labels")
            grp = QtGui.QActionGroup(sub)
            now = label_layout()
            for value, text in (
                ("auto", "Automatic"),
                ("beside", "Beside Fields"),
                ("above", "Above Fields"),
            ):
                act = sub.addAction(text)
                act.setCheckable(True)
                act.setChecked(value == now)
                grp.addAction(act)
                act.triggered.connect(lambda _=False, v=value: panel.set_label_layout(v))

    def _fold_others(self) -> None:
        parent = self.parentWidget()
        while parent is not None and parent.objectName() != "propRoot":
            parent = parent.parentWidget()
        if parent is None:
            return
        for sec in parent.findChildren(PanelSection):
            if sec is not self and not sec.folded():
                sec.set_folded(True)
        if self._folded:
            self.set_folded(False)


class RefGrid(QtWidgets.QWidget):
    """The 9 reference points (3 x 3 squares): which point of the box X / Y show and which
    stays where it is when W / H change. ``changed(name)``. One painted widget: a click picks
    the square under it, the arrow keys move the choice, each square has its own tool tip."""

    changed = QtCore.Signal(str)
    #: a square's side and the gap between two (px)
    BOX, GAP = 9, 3

    def __init__(self, current: str, parent=None):
        super().__init__(parent)
        self._current = current if current in REF_BY_NAME else "top_left"
        self._hover = None
        side = 3 * self.BOX + 2 * self.GAP
        self.setFixedSize(side, side)
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self.setFocusPolicy(QtCore.Qt.TabFocus)
        self.setMouseTracking(True)
        self.setObjectName("refGrid")
        self._sync_name()

    def current(self) -> str:
        return self._current

    def pick(self, name: str) -> None:
        """Choose point ``name`` as a click on it does: ``changed`` when it is new."""
        if name not in REF_BY_NAME or name == self._current:
            return
        self._current = name
        self._sync_name()
        self.update()
        self.changed.emit(name)

    def _sync_name(self) -> None:
        label = REF_BY_NAME[self._current][1].lower()
        self.setAccessibleName("Reference point: %s" % label)
        self.setToolTip(
            "X and Y show the %s point. It stays where it is when W or H "
            "change. Click a square or use the arrow keys" % label
        )

    def _square(self, fx: int, fy: int) -> QtCore.QRect:
        step = self.BOX + self.GAP
        return QtCore.QRect(fx * step, fy * step, self.BOX, self.BOX)

    def _at(self, pos) -> str:
        step = self.BOX + self.GAP
        fx = min(2, max(0, int(pos.x() + self.GAP / 2) // step))
        fy = min(2, max(0, int(pos.y() + self.GAP / 2) // step))
        return next(r[0] for r in REFERENCES if (r[2], r[3]) == (fx, fy))

    def paintEvent(self, _e) -> None:  # noqa: N802 -- Qt API
        from ..style.theme import COLORS

        p = QtGui.QPainter(self)
        dim = QtGui.QColor(COLORS.get("text_dim", "#969ca5"))
        accent = QtGui.QColor(COLORS.get("accent", "#4da3ff"))
        for name, _label, fx, fy in REFERENCES:
            r = self._square(fx, fy).adjusted(0, 0, -1, -1)
            on = name == self._current
            p.setPen(QtGui.QPen(accent if on or name == self._hover else dim, 1))
            p.setBrush(accent if on else QtCore.Qt.NoBrush)
            p.drawRect(r)
        if self.hasFocus():
            fx, fy = REF_BY_NAME[self._current][2:]
            p.setPen(QtGui.QPen(accent, 1, QtCore.Qt.DotLine))
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawRect(self._square(fx, fy).adjusted(-2, -2, 1, 1))
        p.end()

    def mousePressEvent(self, e) -> None:  # noqa: N802 -- Qt API
        if e.button() == QtCore.Qt.LeftButton:
            self.pick(self._at(e.position()))
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:  # noqa: N802 -- Qt API
        name = self._at(e.position())
        if name != self._hover:
            self._hover = name
            self.update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802 -- Qt API
        self._hover = None
        self.update()
        super().leaveEvent(e)

    _MOVES = {
        QtCore.Qt.Key_Left: (-1, 0),
        QtCore.Qt.Key_Right: (1, 0),
        QtCore.Qt.Key_Up: (0, -1),
        QtCore.Qt.Key_Down: (0, 1),
    }

    def keyPressEvent(self, e) -> None:  # noqa: N802 -- Qt API
        mv = self._MOVES.get(e.key())
        if mv is None:
            super().keyPressEvent(e)
            return
        fx, fy = REF_BY_NAME[self._current][2:]
        fx, fy = min(2, max(0, fx + mv[0])), min(2, max(0, fy + mv[1]))
        self.pick(next(r[0] for r in REFERENCES if (r[2], r[3]) == (fx, fy)))
        e.accept()

    def event(self, e) -> bool:  # noqa: D102 -- Qt API
        t = e.type()
        if t == QtCore.QEvent.ShortcutOverride and e.key() in self._MOVES:
            e.accept()  # (the arrows move the choice, not the object)
            return True
        if t == QtCore.QEvent.ToolTip:
            name = self._at(e.pos())
            QtWidgets.QToolTip.showText(
                e.globalPos(),
                "X and Y show the %s point. It stays where it is when W or "
                "H change" % REF_BY_NAME[name][1].lower(),
                self,
            )
            return True
        if t in (QtCore.QEvent.FocusIn, QtCore.QEvent.FocusOut):
            self.update()
        return super().event(e)


class FieldKeys(QtCore.QObject):
    """The panel's keys in a field: Esc puts the value the field had back (a second Esc
    gives the focus to the page); Ctrl+Z / Ctrl+Y in a field with nothing typed is the
    window's Undo / Redo (the field's own text undo only while it holds typing)."""

    def __init__(self, panel: "PropertyPanel"):
        super().__init__(panel)
        self._panel = weakref.ref(panel)

    @staticmethod
    def modified(w) -> bool:
        try:
            if isinstance(w, NumberField):
                return w.is_modified()
            if isinstance(w, QtWidgets.QComboBox):
                orig = w.property("orig")
                return w.isEditable() and orig is not None and w.currentText() != str(orig)
            orig = w.property("orig")
            if orig is None or not hasattr(w, "text"):
                return False
            return w.text() != str(orig)
        except RuntimeError:
            return False

    @classmethod
    def revert(cls, w) -> bool:
        if not cls.modified(w):
            return False
        from ..style import theme

        if isinstance(w, NumberField):
            w.revert()
        elif isinstance(w, QtWidgets.QComboBox):
            w.setEditText(str(w.property("orig")))
        else:
            w.setText(str(w.property("orig")))
            if isinstance(w, QtWidgets.QLineEdit):
                w.setModified(False)
                w.setCursorPosition(0)
        theme.mark_invalid(w, "")
        note = getattr(w, "problem_note", None)
        if note is not None:
            with contextlib.suppress(RuntimeError):
                note.hide()
        return True

    @staticmethod
    def _search_text(w):
        """The text of a search box (a SearchField has no value of its own, so nothing to
        put back), else None."""
        from ..style.theme import SearchField

        if not isinstance(w, SearchField):
            return None
        try:
            return w.text()
        except RuntimeError:
            return ""

    def eventFilter(self, obj, e) -> bool:  # noqa: N802 -- Qt API
        t = e.type()
        if t in (QtCore.QEvent.ShortcutOverride, QtCore.QEvent.KeyPress) and e.matches(
            QtGui.QKeySequence.Find
        ):
            # Ctrl+F in the panel: its property search (Edit > Find is the page's)
            panel = self._panel()
            if panel is not None:
                e.accept()
                if t == QtCore.QEvent.KeyPress:
                    panel.focus_search()
                return True
        search = (
            self._search_text(obj) if t in (QtCore.QEvent.KeyPress, QtCore.QEvent.ShortcutOverride) else None
        )
        if search:
            # a search box with text keeps its own keys: Esc clears it,
            # Ctrl+Z undoes the typing
            return False
        if (
            t == QtCore.QEvent.KeyPress
            and e.key() == QtCore.Qt.Key_Escape
            and not e.modifiers() & ~QtCore.Qt.KeypadModifier
        ):
            if QtWidgets.QApplication.activePopupWidget() is not None:
                return False  # the completion list / menu takes it
            if search is None and self.revert(obj):
                e.accept()
                return True
            panel = self._panel()
            if panel is not None:
                panel.focus_canvas()
                e.accept()
                return True
            return False
        if t == QtCore.QEvent.ShortcutOverride and (
            e.matches(QtGui.QKeySequence.Undo) or e.matches(QtGui.QKeySequence.Redo)
        ):
            if search is not None or not self.modified(obj):
                e.ignore()  # not the field's: Edit > Undo / Redo runs
                return True
        return False


#: the field classes the panel's keys and the fit-the-dock rule apply to (:func:`panel_is_field`;
#: none is a subclass of another)
FIELD_CLASSES = (
    QtWidgets.QAbstractSpinBox,
    QtWidgets.QComboBox,
    QtWidgets.QPlainTextEdit,
    QtWidgets.QLineEdit,
)


def panel_is_field(w) -> bool:
    if isinstance(w, (QtWidgets.QAbstractSpinBox, QtWidgets.QComboBox, QtWidgets.QPlainTextEdit)):
        return True
    return isinstance(w, QtWidgets.QLineEdit) and not isinstance(
        w.parentWidget(), (QtWidgets.QAbstractSpinBox, QtWidgets.QComboBox)
    )


def show_value_starts(form) -> None:
    """Every text field of ``form`` not being typed in shows its value from the start, and a
    field too narrow for its value has the whole value as its tool tip."""
    for ed in form.findChildren(QtWidgets.QLineEdit):
        try:
            if ed.hasFocus():
                continue
            ed.setCursorPosition(0)
            own = ed.property("ctg_own_tip")
            if own is None:
                own = ed.toolTip() or ""
                ed.setProperty("ctg_own_tip", own)
            text = ed.text()
            cut = bool(text) and ed.fontMetrics().horizontalAdvance(text) > ed.width() - 10
            tip = (text + ("\n" + own if own else "")) if cut else own
            if ed.toolTip() != tip:
                ed.setToolTip(tip)
        except RuntimeError:  # swallow-ok: a field of a form being replaced
            continue
