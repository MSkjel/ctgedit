"""Appearance pages: General, 3D Effects and Visibility."""

from __future__ import annotations

import os

from PySide6 import QtGui, QtWidgets

from .colour_field import ColourPairField
from .fields import _L_COND, _LINE_STYLES, _Field, _Sub

VISIBILITY_SUB = _Sub("Visibility", [_Field("Hidden when:", "VISIBLE", limit=_L_COND)], family=("VISIBLE",))
SUBS = [VISIBILITY_SUB]
#: the bag keys the button page writes (EditableDocument.set_button_face): the
#: draw style and the caption / '#lib.element' symbol face
FIELDS = [_Field("Mode:", "VIS_MODE", "text"), _Field("Text:", "TEXT", "text")]

#: the gradient directions (0 = no gradient), help 1172124
GRADIENTS = [
    "Off",
    "Left to Right",
    "Right to Left",
    "Top to Bottom",
    "Bottom to Top",
    "Horizontal Gradient to Middle",
    "Horizontal Gradient from Middle",
    "Vertical Gradient to Middle",
    "Vertical Gradient from Middle",
]
#: ChooseFont's styles -> (bold, italic)
FONT_STYLES = [
    ("Regular", False, False),
    ("Italic", False, True),
    ("Bold", True, False),
    ("Bold Italic", True, True),
]
#: Windows character sets (the Script combo) -> the Qt writing system that
#: must be in the font for it
SCRIPTS = [
    (0, "Western", QtGui.QFontDatabase.Latin),
    (238, "Central European", QtGui.QFontDatabase.Latin),
    (204, "Cyrillic", QtGui.QFontDatabase.Cyrillic),
    (161, "Greek", QtGui.QFontDatabase.Greek),
    (162, "Turkish", QtGui.QFontDatabase.Latin),
    (186, "Baltic", QtGui.QFontDatabase.Latin),
    (163, "Vietnamese", QtGui.QFontDatabase.Vietnamese),
    (177, "Hebrew", QtGui.QFontDatabase.Hebrew),
    (178, "Arabic", QtGui.QFontDatabase.Arabic),
    (222, "Thai", QtGui.QFontDatabase.Thai),
    (128, "Japanese", QtGui.QFontDatabase.Japanese),
    (129, "Korean (Hangul)", QtGui.QFontDatabase.Korean),
    (130, "Korean (Johab)", QtGui.QFontDatabase.Korean),
    (134, "Chinese Simplified (GB2312)", QtGui.QFontDatabase.SimplifiedChinese),
    (136, "Chinese Traditional (Big5)", QtGui.QFontDatabase.TraditionalChinese),
    (2, "Symbol", QtGui.QFontDatabase.Symbol),
    (255, "OEM / DOS", None),
    (77, "Mac", None),
]
#: 3D effects in the page's order -> the style block's effects value
EFFECTS = [("None", 0), ("Shadowed", 3), ("Raised", 1), ("Lowered", 2), ("Embossed", 4)]
#: the text justify codes (style-block attribs low nibble: 0 left, 1 right,
#: 2 centre) and the order the Alignment drop-down lists them in
_JUSTIFY_CODES = ("left", "right", "centre")
_JUSTIFY_ORDER = ("left", "centre", "right")
#: a target button's modes (help 1172135)
TARGET_MODES = ["BORDER_3D", "BORDER", "TARGET"]

_LINE_TCS = (0x01, 0x02, 0x12)
_RECT_TCS = (0x03, 0x0E, 0x0F)
_ELLIPSE_TCS = (0x04, 0x11)
_POLY_TCS = (0x0A, 0x10)
_PIPE_TC, _TEXT_TC, _BUTTON_TC = 0x0B, 0x05, 0x1001


def script_supported(family: str, charset: int) -> bool:
    """Whether ``family`` has the characters of Windows charset ``charset``."""
    ws = next((w for c, _n, w in SCRIPTS if c == charset), None)
    if ws is None:
        return True
    try:
        return ws in QtGui.QFontDatabase.writingSystems(family)
    except Exception:  # noqa: BLE001 -- font database unavailable
        return True


def _expanding(w):
    """A choice list with long entries grows with the window."""
    pol = w.sizePolicy()
    pol.setHorizontalPolicy(QtWidgets.QSizePolicy.Expanding)
    w.setSizePolicy(pol)
    return w


def _stored(doc, index) -> dict:
    """What the record stores: style flags + slots, shape / text tails, the
    button font block and face."""
    from ...format import geometry as G

    po = doc.parsed.page.objects[index]
    cb = G.find_colour_block(po)
    out = {
        "tc": po.type_code,
        "flags": bytes(cb.flags) if cb else None,
        "slots": [tuple(s) for s in cb.slots] if cb else None,
        "shape": G.parse_shape_tail(po),
        "text": None,
        "font": None,
        "button": None,
    }
    if po.type_code == _TEXT_TC:
        out["text"] = G.parse_text_tail(po)
    if po.type_code == _BUTTON_TC:
        out["font"] = G.parse_button_font(po)
        out["button"] = doc.button_face(index)
    return out


class _Section:
    """A group of fields on the General page (``build`` adds rows, ``apply``
    writes what changed, ``widgets`` for read-only)."""

    def __init__(self, editor):
        self.editor = editor
        self.sheet = editor.sheet
        self.widgets = []

    def add(self, w):
        self.widgets.append(w)
        return w


# -------------------------------------------------------------------------- shape sections
class _LineSection(_Section):
    """[Line] Width / Style / Colour (a pipe: Width + Highlight / Lowlight)."""

    def build(self, form, st, stored):
        s = self.sheet
        box = QtWidgets.QGroupBox("Line")
        f = QtWidgets.QFormLayout(box)
        s._sp_width = self.add(QtWidgets.QSpinBox())
        s._sp_width.setRange(1, 32)
        s._sp_width.setSuffix(" px")
        s._sp_width.setToolTip("The line width (1 to 32)")
        f.addRow("Width:", s._sp_width)
        if stored["tc"] == _PIPE_TC:
            s._col_hilight = self.add(ColourPairField("Highlight colour"))
            s._col_hilight.setToolTip("The colour of the lit side of the pipe")
            f.addRow("Highlight colour:", s._col_hilight)
            s._col_lolight = self.add(ColourPairField("Lowlight colour"))
            s._col_lolight.setToolTip("The colour of the shaded side of the pipe")
            f.addRow("Lowlight colour:", s._col_lolight)
        else:
            s._cb_lstyle = self.add(QtWidgets.QComboBox())
            s._cb_lstyle.addItems(_LINE_STYLES)
            f.addRow("Style:", s._cb_lstyle)
            # (Transparent = no line: CtDraw's shape pages keep their selectors'
            # Transparent -- only the text / button pages turn it off)
            s._col_line = self.add(ColourPairField("Line colour", transparent=True))
            f.addRow("Colour:", s._col_line)
            s._sp_width.valueChanged.connect(self._sync)
        self.load(st, stored)
        form.addRow(box)

    def load(self, st, stored):
        """Show the stored line settings."""
        s = self.sheet
        fl, slots = stored["flags"], stored["slots"]
        # (before the width: its change signal runs _sync, which reads it)
        self._geometric = bool(fl and fl[1] & 0xF0)
        s._sp_width.setValue(max(1, (fl[4] if fl else 0) or st.line_width or 1))
        if stored["tc"] == _PIPE_TC:
            s._col_hilight.set_pair(*(slots[3] if slots else (None, None)))
            s._col_lolight.set_pair(*(slots[4] if slots else (None, None)))
            return
        s._cb_lstyle.setCurrentIndex(min(4, max(0, (fl[1] & 0x0F) if fl else (st.line_style or 0))))
        s._col_line.set_pair(*(slots[0] if slots else (None, None)))
        self._sync()

    def _sync(self, *_a):
        s = self.sheet
        wide = s._sp_width.value() > 1 and not self._geometric
        s._cb_lstyle.setEnabled(not wide and not self.editor.read_only)
        s._cb_lstyle.setToolTip(
            "A line wider than 1 pixel is drawn solid: set the width to 1 for a dashed or dotted line."
            if wide
            else ""
        )

    def apply(self, doc, i, stored) -> bool:
        from ...format import geometry as G

        s = self.sheet
        fl, slots = stored["flags"], stored["slots"]
        ch = False
        if s._sp_width.value() != ((fl[4] if fl else 0) or 1):
            ch |= bool(doc.set_line_width(i, s._sp_width.value()))
        if stored["tc"] == _PIPE_TC:
            if slots and s._col_hilight.pair() != slots[3]:
                ch |= bool(
                    doc.set_colour_slot(i, G.SLOT_HILIGHT, *s._col_hilight.pair(), label="highlight colour")
                )
            if slots and s._col_lolight.pair() != slots[4]:
                ch |= bool(
                    doc.set_colour_slot(i, G.SLOT_LOLIGHT, *s._col_lolight.pair(), label="lowlight colour")
                )
            return ch
        if s._cb_lstyle.currentIndex() != ((fl[1] & 0x0F) if fl else 0):
            ch |= bool(doc.set_line_style(i, s._cb_lstyle.currentIndex()))
        if slots and s._col_line.pair() != slots[0]:
            ch |= bool(doc.set_colour_slot(i, G.SLOT_LINE, *s._col_line.pair(), label="line colour"))
        return ch


class _FillSection(_Section):
    """[Fill] Filled / Colour and [Gradient] Direction / Colour."""

    def build(self, form, st, stored):
        s = self.sheet
        tail = stored["shape"]
        # (the fill as drawn; the Fill tab of the sheet animates it at runtime: two names for
        # one word read as the same place)
        box = QtWidgets.QGroupBox("Static fill")
        f = QtWidgets.QFormLayout(box)
        s._chk_filled = self.add(QtWidgets.QCheckBox("Filled"))
        f.addRow("", s._chk_filled)
        s._col_fill = self.add(ColourPairField("Fill colour", transparent=True))
        s._col_fill.setToolTip("The static fill colour (the Fill tab animates it)")
        f.addRow("Colour:", s._col_fill)
        self._has_tail = tail is not None
        if tail is not None:
            s._cb_grad = self.add(QtWidgets.QComboBox())
            s._cb_grad.addItems(GRADIENTS)
            f.addRow("Gradient:", s._cb_grad)
            s._col_grad = self.add(ColourPairField("Gradient colour"))
            s._col_grad.setToolTip("The colour the fill blends into")
            f.addRow("Gradient colour:", s._col_grad)
            s._cb_grad.currentIndexChanged.connect(self._sync)
            s._chk_filled.toggled.connect(self._sync)
        self.load(st, stored)
        form.addRow(box)

    def load(self, st, stored):
        """Show the stored fill and gradient."""
        s = self.sheet
        fl, slots, tail = stored["flags"], stored["slots"], stored["shape"]
        s._chk_filled.setChecked(bool(fl[2] & 0x0F) if fl else bool(st.filled))
        s._col_fill.set_pair(*(slots[1] if slots else (None, None)))
        if self._has_tail and tail is not None:
            s._cb_grad.setCurrentIndex(min(8, tail["grad_mode"] or 0))
            s._col_grad.set_pair(tail["grad_on"], tail["grad_off"])
            self._sync()

    def _sync(self, *_a):
        s = self.sheet
        on = s._chk_filled.isChecked() and s._cb_grad.currentIndex() > 0
        s._col_grad.set_read_only(not on or self.editor.read_only)
        s._cb_grad.setEnabled(s._chk_filled.isChecked() and not self.editor.read_only)

    def apply(self, doc, i, stored) -> bool:
        from ...format import geometry as G

        s = self.sheet
        fl, slots, tail = stored["flags"], stored["slots"], stored["shape"]
        ch = False
        if fl is not None and s._chk_filled.isChecked() != bool(fl[2] & 0x0F):
            ch |= bool(doc.set_filled(i, s._chk_filled.isChecked()))
        if slots and s._col_fill.pair() != slots[1]:
            ch |= bool(doc.set_colour_slot(i, G.SLOT_FILL, *s._col_fill.pair(), label="fill colour"))
        if tail is not None:
            mode = s._cb_grad.currentIndex()
            pair = s._col_grad.pair()
            new_col = pair != (tail["grad_on"], tail["grad_off"])
            if mode != (tail["grad_mode"] or 0) or new_col:
                ch |= bool(
                    doc.set_gradient(i, mode, pair[0] if new_col else None, pair[1] if new_col else None)
                )
        return ch


class _RectSection(_Section):
    """[Object type] Border / Extra line / Corner radius."""

    def build(self, form, st, stored):
        s = self.sheet
        tail = stored["shape"]
        box = QtWidgets.QGroupBox("Object type")
        f = QtWidgets.QFormLayout(box)
        s._chk_border = self.add(QtWidgets.QCheckBox("Border"))
        s._chk_border.setToolTip("A 1 pixel black line around the rectangle")
        f.addRow("", s._chk_border)
        s._chk_extra = self.add(QtWidgets.QCheckBox("Extra line"))
        f.addRow("", s._chk_extra)
        s._sp_radius = None
        if tail is not None and tail["kind"] == "rect":
            s._sp_radius = self.add(QtWidgets.QSpinBox())
            s._sp_radius.setRange(0, 32)
            s._sp_radius.setSuffix(" px")
            s._sp_radius.setToolTip("0 = square corners (0 to 32)")
            f.addRow("Corner radius:", s._sp_radius)
            s._sp_radius.valueChanged.connect(self.sync)
        self.load(st, stored)
        form.addRow(box)

    def load(self, st, stored):
        """Show the stored rectangle style and corner radius."""
        s = self.sheet
        fl, tail = stored["flags"], stored["shape"]
        rs = ((fl[3] >> 4) & 0x3) if fl else (st.rectangle_style or 0)
        s._chk_border.setChecked(bool(rs & 1))
        s._chk_extra.setChecked(bool(rs & 2))
        if s._sp_radius is not None:
            s._sp_radius.setValue(((tail["radius"] if tail and tail["kind"] == "rect" else 0) or 0))
        self.sync()

    def sync(self, *_a):
        """A corner radius disables Border and Extra line; Extra line shows
        only with a Raised / Lowered effect (help 1172124)."""
        s = self.sheet
        ro = self.editor.read_only
        rounded = s._sp_radius is not None and s._sp_radius.value() > 0
        eff = self.editor.effect_choice()
        s._chk_border.setEnabled(not rounded and not ro)
        s._chk_extra.setEnabled(not rounded and not ro and eff in (1, 2))
        why = "Not available with rounded corners: set the corner radius to 0." if rounded else ""
        s._chk_border.setToolTip(why or "A 1 pixel black line around the rectangle")
        s._chk_extra.setToolTip(
            why
            or (
                "An extra 1 pixel lowlight line on the 3D edge"
                if eff in (1, 2)
                else "Only with a Raised or Lowered 3D effect (3D Effects tab)"
            )
        )

    def apply(self, doc, i, stored) -> bool:
        s = self.sheet
        fl, tail = stored["flags"], stored["shape"]
        ch = False
        rs = ((fl[3] >> 4) & 0x3) if fl else 0
        want = (1 if s._chk_border.isChecked() else 0) | (2 if s._chk_extra.isChecked() else 0)
        if fl is not None and want != rs:
            ch |= bool(doc.set_rectangle_style(i, want))
        if s._sp_radius is not None and s._sp_radius.value() != (tail["radius"] or 0):
            ch |= bool(doc.set_corner_radius(i, s._sp_radius.value()))
        return ch


class _EllipseSection(_Section):
    """[Object type] Ellipse / Pie-slice / Arc + Start / End angle."""

    def build(self, form, st, stored):
        s = self.sheet
        box = QtWidgets.QGroupBox("Object type")
        f = QtWidgets.QFormLayout(box)
        s._cb_estyle = self.add(QtWidgets.QComboBox())
        s._cb_estyle.addItems(["Ellipse", "Pie-slice", "Arc"])
        f.addRow("Type:", s._cb_estyle)
        s._sp_astart = self.add(QtWidgets.QSpinBox())
        s._sp_astart.setRange(-360, 360)
        s._sp_astart.setSuffix("°")
        s._sp_astart.setToolTip("Degrees clockwise from 12 o'clock")
        f.addRow("Start angle:", s._sp_astart)
        s._sp_aend = self.add(QtWidgets.QSpinBox())
        s._sp_aend.setRange(-360, 360)
        s._sp_aend.setSuffix("°")
        s._sp_aend.setToolTip("Degrees clockwise from 12 o'clock")
        f.addRow("End angle:", s._sp_aend)
        s._cb_estyle.currentIndexChanged.connect(self._sync)
        self.load(st, stored)
        form.addRow(box)

    def load(self, st, stored):
        """Show the stored ellipse type and angles."""
        s = self.sheet
        fl, tail = stored["flags"], stored["shape"]
        mode = (fl[3] & 0x0F) if fl else (st.ellipse_style or 0)
        s._cb_estyle.setCurrentIndex(mode if mode <= 2 else 0)
        s._sp_astart.setValue(
            int(tail["start"]) if tail and tail["kind"] == "ellipse" else int(st.start_angle)
        )
        s._sp_aend.setValue(int(tail["end"]) if tail and tail["kind"] == "ellipse" else int(st.end_angle))
        self._sync()

    def _sync(self, *_a):
        s = self.sheet
        on = s._cb_estyle.currentIndex() in (1, 2) and not self.editor.read_only
        s._sp_astart.setEnabled(on)
        s._sp_aend.setEnabled(on)

    def apply(self, doc, i, stored) -> bool:
        s = self.sheet
        fl, tail = stored["flags"], stored["shape"]
        mode = (fl[3] & 0x0F) if fl else 0
        start = int(tail["start"]) if tail and tail["kind"] == "ellipse" else 0
        end = int(tail["end"]) if tail and tail["kind"] == "ellipse" else 0
        if s._cb_estyle.currentIndex() != mode or s._sp_astart.value() != start or s._sp_aend.value() != end:
            return bool(
                doc.set_ellipse(
                    i, style=s._cb_estyle.currentIndex(), start=s._sp_astart.value(), end=s._sp_aend.value()
                )
            )
        return False


class _PolygonSection(_Section):
    """[Object type] open (polyline) / closed."""

    def build(self, form, st, stored):
        s = self.sheet
        box = QtWidgets.QGroupBox("Object type")
        f = QtWidgets.QFormLayout(box)
        s._chk_open = self.add(QtWidgets.QCheckBox("Open (polyline)"))
        s._chk_open.setToolTip("An open polygon does not join its last point to the first")
        f.addRow("", s._chk_open)
        self.load(st, stored)
        form.addRow(box)

    def load(self, st, stored):
        fl = stored["flags"]
        self.sheet._chk_open.setChecked(bool(fl[3] & 0x01) if fl else bool(st.polygon_open))

    def apply(self, doc, i, stored) -> bool:
        s = self.sheet
        fl = stored["flags"]
        if fl is not None and s._chk_open.isChecked() != bool(fl[3] & 0x01):
            return bool(doc.set_polygon_open(i, s._chk_open.isChecked()))
        return False


# -------------------------------------------------------------------------- font section
class _FontSection(_Section):
    """Font / Style / Size / Alignment / Effect / Text / Foreground / Script
    (the Text page, and the Text type of the Button page)."""

    def build(self, form, st, stored, caption: str, button: bool):
        s = self.sheet
        from ..editor.caption_edit import CaptionEdit
        from ..editor.inplace_text import enter_hint  # (Settings > Editing > Text)

        self._button = button
        box = QtWidgets.QGroupBox("Text" if not button else "Caption")
        f = QtWidgets.QFormLayout(box)
        s._ed_caption = self.add(CaptionEdit(caption))
        more = " '^n' also breaks the line on a button." if button else ""
        s._ed_caption.set_tip(lambda: enter_hint() + more)  # (follows the setting)
        f.addRow("Text:", s._ed_caption)
        s._cb_font = self.add(_expanding(QtWidgets.QFontComboBox()))
        s._cb_font.setEditable(True)
        f.addRow("Font:", s._cb_font)
        s._cb_fstyle = self.add(QtWidgets.QComboBox())
        s._cb_fstyle.addItems([n for n, _b, _i in FONT_STYLES])
        f.addRow("Style:", s._cb_fstyle)
        s._sp_fsize = self.add(QtWidgets.QSpinBox())
        s._sp_fsize.setRange(1, 255)
        s._sp_fsize.setSuffix(" pt")
        f.addRow("Size:", s._sp_fsize)
        s._cb_justify = self.add(QtWidgets.QComboBox())
        s._cb_justify.addItems(["Left", "Centre", "Right"])  # _JUSTIFY_ORDER
        if button:
            s._cb_justify.setToolTip(
                "How the lines of a multi-line caption align; the caption stays centred on the button"
            )
        f.addRow("Alignment:", s._cb_justify)
        eff = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(eff)
        h.setContentsMargins(0, 0, 0, 0)
        s._chk_strike = self.add(QtWidgets.QCheckBox("Strikeout"))
        s._chk_under = self.add(QtWidgets.QCheckBox("Underline"))
        h.addWidget(s._chk_strike)
        h.addWidget(s._chk_under)
        h.addStretch(1)
        f.addRow("Effect:", eff)
        s._col_fore = self.add(ColourPairField("Foreground"))
        s._col_fore.setToolTip("The colour of the text")
        f.addRow("Foreground:", s._col_fore)
        row = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        s._cb_script = self.add(_expanding(QtWidgets.QComboBox()))
        for cs, name, _ws in SCRIPTS:
            s._cb_script.addItem(name, cs)
        self.load(st, stored, caption, set_caption=False)
        h.addWidget(s._cb_script, 1)
        s._lbl_script_warn = QtWidgets.QLabel()
        from ..notify import level_icon

        s._lbl_script_warn.setPixmap(level_icon("warning").pixmap(16, 16))
        h.addWidget(s._lbl_script_warn)
        f.addRow("Script:", row)
        s._cb_script.currentIndexChanged.connect(self._sync_script)
        s._cb_font.currentFontChanged.connect(self._sync_script)
        self._sync_script()
        self.font_fields = (
            s._cb_font,
            s._cb_fstyle,
            s._sp_fsize,
            s._cb_justify,
            s._chk_strike,
            s._chk_under,
            s._col_fore,
            s._cb_script,
        )
        self.no_font = button and stored["font"] is None
        if self.no_font:
            # an old button without a font block of its own (no dwFlags 0x40
            # section) draws with the default button font
            for w in self.font_fields:
                w.setEnabled(False)
                w.setToolTip("This button has no font of its own: it uses the default button font.")
            self.widgets = [w for w in self.widgets if w not in self.font_fields]
        form.addRow(box)

    def load(self, st, stored, caption: str, set_caption: bool = True):
        """Show the stored font, colour, script (and the caption, unless
        ``set_caption`` is False: its owner shows it)."""
        s = self.sheet
        if self._button:
            f_ = stored["font"] or {}
            face, size, attribs = f_.get("face") or "Arial", f_.get("size") or 11, f_.get("attribs", 0)
            charset = f_.get("charset", 0)
            fg = f_.get("text") or (0, 0)
        else:
            tail, fl, slots = stored["text"], stored["flags"], stored["slots"]
            face = (tail["face"] if tail else st.font) or "Arial"
            size = (tail["size"] if tail else st.font_size) or 10
            attribs = fl[3] if fl else 0
            from ...format import geometry as _G

            charset = tail["charset"] if tail else _G.DEFAULT_CHARSET
            fg = slots[1] if slots else (None, None)
        self.base = {
            "face": face,
            "size": size,
            "attribs": attribs,
            "charset": charset,
            "fg": tuple(fg),
            "caption": caption,
        }
        if set_caption:
            s._ed_caption.setText(caption)
        s._cb_font.setCurrentFont(QtGui.QFont(face))
        s._cb_font.setEditText(face)
        s._cb_fstyle.setCurrentIndex(
            next(
                k
                for k, (_n, b, it) in enumerate(FONT_STYLES)
                if b == bool(attribs & 0x10) and it == bool(attribs & 0x20)
            )
        )
        s._sp_fsize.setValue(int(size))
        s._cb_justify.setCurrentIndex(_JUSTIFY_ORDER.index(_JUSTIFY_CODES[min(2, attribs & 0x0F)]))
        s._chk_strike.setChecked(bool(attribs & 0x80))
        s._chk_under.setChecked(bool(attribs & 0x40))
        s._col_fore.set_pair(*fg)
        k = s._cb_script.findData(charset)
        if k < 0:
            s._cb_script.addItem("Character set %d" % charset, charset)
            k = s._cb_script.count() - 1
        s._cb_script.setCurrentIndex(k)
        if getattr(s, "_lbl_script_warn", None) is not None:
            self._sync_script()

    def _sync_script(self, *_a):
        s = self.sheet
        fam = s._cb_font.currentText() or s._cb_font.currentFont().family()
        cs = s._cb_script.currentData()
        ok = script_supported(fam, cs)
        s._lbl_script_warn.setVisible(not ok)
        s._lbl_script_warn.setToolTip(
            ""
            if ok
            else "The font '%s' has no %s characters: Plant SCADA uses a different "
            "font for this text at runtime. Choose a font that supports the "
            "script." % (fam, s._cb_script.currentText())
        )

    def values(self) -> dict:
        s = self.sheet
        _n, bold, italic = FONT_STYLES[s._cb_fstyle.currentIndex()]
        return {
            "face": s._cb_font.currentText().strip() or self.base["face"],
            "size": s._sp_fsize.value(),
            "bold": bold,
            "italic": italic,
            "underline": s._chk_under.isChecked(),
            "strikeout": s._chk_strike.isChecked(),
            "justify": _JUSTIFY_ORDER[s._cb_justify.currentIndex()],
            "charset": s._cb_script.currentData(),
            "fg": s._col_fore.pair(),
            "caption": s._ed_caption.text(),
        }

    def apply_font(self, doc, i) -> bool:
        """Font, style, size, alignment, effect, foreground, script (not the
        caption: its owner writes it)."""
        if self.no_font:
            return False
        b, v = self.base, self.values()
        a = b["attribs"]
        kw = {}
        if v["face"] != b["face"]:
            kw["face"] = v["face"]
        if v["size"] != b["size"]:
            kw["size"] = v["size"]
        for key, bit in (("bold", 0x10), ("italic", 0x20), ("underline", 0x40), ("strikeout", 0x80)):
            if v[key] != bool(a & bit):
                kw[key] = v[key]
        if _JUSTIFY_CODES.index(v["justify"]) != min(2, a & 0x0F):
            kw["justify"] = v["justify"]
        ch = False
        if kw:
            ch |= bool(doc.set_font(i, **kw))
        if v["fg"] != b["fg"]:
            ch |= bool(doc.set_foreground(i, *v["fg"]))
        if v["charset"] != b["charset"]:
            ch |= bool(doc.set_text_charset(i, v["charset"]))
        return ch


# -------------------------------------------------------------------------- button section
class _ButtonSection(_Section):
    """[Type] Text / Symbol / Target, Mode, XP Style, the symbol, Custom Fill
    Color + Fill Color Up / Down (the Button page)."""

    def build(self, form, st, stored):
        s = self.sheet
        bf = stored["button"] or {}
        self.base = bf
        box = QtWidgets.QGroupBox("Type")
        v = QtWidgets.QVBoxLayout(box)
        h = QtWidgets.QHBoxLayout()
        s._rb_btext = self.add(QtWidgets.QRadioButton("Text"))
        s._rb_bsym = self.add(QtWidgets.QRadioButton("Symbol"))
        s._rb_btarget = self.add(QtWidgets.QRadioButton("Target"))
        self._types = QtWidgets.QButtonGroup(box)
        for k, rb in enumerate((s._rb_btext, s._rb_bsym, s._rb_btarget)):
            self._types.addButton(rb, k)
            h.addWidget(rb)
        h.addStretch(1)
        v.addLayout(h)
        f = QtWidgets.QFormLayout()
        v.addLayout(f)
        s._chk_xp = self.add(QtWidgets.QCheckBox("XP style"))
        s._chk_xp.setChecked(bool(bf.get("xp", True)) if bf.get("type") != "target" else True)
        s._chk_xp.setToolTip("Draw the button in the Windows XP style")
        f.addRow("", s._chk_xp)
        # symbol type: the '#lib.element' face
        from ..pick_fields import LibraryElementField

        doc = s._doc
        pdir = os.path.dirname(os.path.abspath(doc.path)) if getattr(doc, "path", None) else None
        s._ed_symbol = self.add(
            LibraryElementField(pdir, "symbol", title="Select Symbol", resolve=self._symbol_path)
        )
        s._ed_symbol.setToolTip("The library symbol shown on the button")
        s._ed_symbol.setText(bf.get("symbol", ""))
        s._btn_symset = s._ed_symbol.btn_browse
        s._btn_symset.setToolTip("Choose the symbol from the project's libraries")
        self._sym_row = s._ed_symbol
        f.addRow("Symbol:", s._ed_symbol)
        # target type: the mode
        s._cb_bmode = self.add(QtWidgets.QComboBox())
        s._cb_bmode.addItems(TARGET_MODES)
        mode = bf.get("mode") or "TARGET"
        s._cb_bmode.setCurrentIndex(TARGET_MODES.index(mode) if mode in TARGET_MODES else 2)
        s._cb_bmode.setToolTip(
            "BORDER_3D: 3D border only. BORDER: thin border. TARGET: invisible (a screen target)."
        )
        self._mode_label = QtWidgets.QLabel("Mode:")
        f.addRow(self._mode_label, s._cb_bmode)
        # custom fill
        s._chk_custom = self.add(QtWidgets.QCheckBox("Custom fill colour"))
        s._chk_custom.setChecked(bool(bf.get("custom_fill")))
        f.addRow("", s._chk_custom)
        s._col_up = self.add(ColourPairField("Fill Colour Up", flash=True))
        s._col_down = self.add(ColourPairField("Fill Colour Down", flash=True))
        self._stored_up, self._stored_down = bf.get("up"), bf.get("down")
        if bf.get("custom_fill"):
            s._col_up.set_pair(*bf["up"])
            s._col_down.set_pair(*bf["down"])
        else:
            self._show_defaults()
        s._col_up.setToolTip("The button colour in the Up state")
        s._col_down.setToolTip("The button colour in the Down state")
        f.addRow("Fill colour up:", s._col_up)
        f.addRow("Fill colour down:", s._col_down)
        self._fill_form = f
        {"text": s._rb_btext, "symbol": s._rb_bsym, "target": s._rb_btarget}.get(
            bf.get("type", "text"), s._rb_btext
        ).setChecked(True)
        self._types.idToggled.connect(self.sync)
        s._chk_custom.toggled.connect(self._custom_toggled)
        s._chk_xp.toggled.connect(self._xp_toggled)
        form.addRow(box)

    def load(self, stored):
        """Show the stored button type, symbol, mode and fill (a later read
        of the object)."""
        s = self.sheet
        bf = stored["button"] or {}
        self.base = bf
        self._stored_up, self._stored_down = bf.get("up"), bf.get("down")
        quiet = (s._chk_xp, s._chk_custom, s._rb_btext, s._rb_bsym, s._rb_btarget)
        for w in quiet:
            w.blockSignals(True)
        try:
            {"text": s._rb_btext, "symbol": s._rb_bsym, "target": s._rb_btarget}.get(
                bf.get("type", "text"), s._rb_btext
            ).setChecked(True)
            s._chk_xp.setChecked(bool(bf.get("xp", True)) if bf.get("type") != "target" else True)
            s._chk_custom.setChecked(bool(bf.get("custom_fill")))
        finally:
            for w in quiet:
                w.blockSignals(False)
        s._ed_symbol.setText(bf.get("symbol", ""))
        mode = bf.get("mode") or "TARGET"
        s._cb_bmode.setCurrentIndex(TARGET_MODES.index(mode) if mode in TARGET_MODES else 2)
        if bf.get("custom_fill"):
            s._col_up.set_pair(*bf["up"])
            s._col_down.set_pair(*bf["down"])
        else:
            self._show_defaults()
        self.sync()

    def _show_defaults(self):
        from ...editing import EditableDocument

        s = self.sheet
        up, down = EditableDocument.button_default_fill(s._chk_xp.isChecked())
        s._col_up.set_pair(up, up)
        s._col_down.set_pair(down, down)

    def _custom_toggled(self, on):
        if not on:
            self._show_defaults()  # CtDraw resets the pickers
        self.sync()

    def _xp_toggled(self, _on):
        if not self.sheet._chk_custom.isChecked():
            self._show_defaults()

    def type(self) -> str:
        return ("text", "symbol", "target")[max(0, self._types.checkedId())]

    def sync(self, *_a):
        """the Symbol fields show for the Symbol
        type, Mode for Target; XP Style and Custom Fill are off for Target,
        the fill colours need Custom Fill."""
        s = self.sheet
        ro = self.editor.read_only
        t = self.type()
        self._sym_row.setVisible(t == "symbol")
        self._fill_form.labelForField(self._sym_row).setVisible(t == "symbol")
        s._ed_symbol.set_read_only(ro)
        s._cb_bmode.setVisible(t == "target")
        self._mode_label.setVisible(t == "target")
        s._chk_xp.setEnabled(t != "target" and not ro)
        own = self.editor.stored["flags"] is not None  # a style block to hold the fill
        s._chk_custom.setEnabled(t != "target" and not ro and own)
        fill_on = t != "target" and s._chk_custom.isChecked() and not ro and own
        s._col_up.set_read_only(not fill_on)
        s._col_down.set_read_only(not fill_on)
        font = self.editor.font_section
        if font is not None:
            font.box_visible(t == "text")

    def _symbol_path(self, library: str, element: str):
        """The library file of ``library.element`` on the page's include path
        (the field's thumbnail)."""
        try:
            path = self.sheet._doc.library_path(library, element, "ctl")
        except Exception:  # noqa: BLE001 -- no thumbnail then
            return None
        return path or None

    def _pick_symbol(self):
        try:
            self.sheet._ed_symbol.browse()
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(
                self.sheet, "Select Symbol", "The symbol libraries could not be listed: %s" % exc
            )

    def problem(self):
        s = self.sheet
        if self.type() == "symbol" and not s._ed_symbol.text().strip():
            return (
                "Choose the symbol to show on the button (Browse…), or pick the Text type.",
                s._ed_symbol.btn_browse,
            )
        return None

    def apply(self, doc, i, stored) -> bool:
        s = self.sheet
        b = self.base
        t = self.type()
        font = self.editor.font_section
        caption = font.values()["caption"] if font is not None else b.get("caption", "")
        custom = s._chk_custom.isChecked() if t != "target" else None
        kw = dict(
            type=t,
            caption=caption,
            symbol=s._ed_symbol.text().strip(),
            mode=s._cb_bmode.currentText(),
            xp=s._chk_xp.isChecked(),
            custom_fill=custom,
            up=s._col_up.pair() if custom else None,
            down=s._col_down.pair() if custom else None,
        )
        from ...editing import crlf_text

        same_type = (
            t == b.get("type")
            and (
                (t == "text" and crlf_text(caption) == (b.get("caption") or ""))
                or (t == "symbol" and kw["symbol"] == b.get("symbol"))
                or (t == "target" and kw["mode"] == b.get("mode"))
            )
            and (t == "target" or bool(kw["xp"]) == bool(b.get("xp")))
        )
        fill_same = custom is None or (
            custom == bool(b.get("custom_fill"))
            and (not custom or (kw["up"] == self._stored_up and kw["down"] == self._stored_down))
        )
        if same_type and fill_same:
            return False
        return bool(doc.set_button_face(i, **kw))


# -------------------------------------------------------------------------- the editors
class GeneralEditor:
    """Appearance > General: builds its widgets on the sheet and writes them
    (before the sheet's key edits, in the same undo step)."""

    phase = "before"

    def __init__(self, sheet):
        self.sheet = sheet
        self.read_only = bool(getattr(sheet, "read_only_reason", ""))
        self.sections = []
        self.font_section = None
        self.button_section = None
        self.rect_section = None
        self._font_box = None
        self.widget = self._build(sheet, sheet._doc.page.objects[sheet._index])

    def effect_choice(self) -> int:
        """The effect the 3D Effects page shows now (else the stored one)."""
        for ed in self.sheet.built_editors():
            if isinstance(ed, EffectsEditor):
                return ed.choice()
        fl = _stored(self.sheet._doc, self.sheet._index)["flags"]
        return (fl[0] & 0x0F) if fl else 0

    def _build(self, sheet, obj) -> QtWidgets.QWidget:
        doc, i = sheet._doc, sheet._index
        stored = _stored(doc, i)
        self.stored = stored
        tc = stored["tc"]
        st = obj.style
        gen = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(gen)
        form = QtWidgets.QFormLayout()
        if tc in (_TEXT_TC, _BUTTON_TC):
            outer.addLayout(form, 1)  # the text box takes the extra height
        else:
            outer.addLayout(form)
            outer.addStretch(1)  # the groups keep their natural height
        if tc not in _LINE_TCS + _RECT_TCS + _ELLIPSE_TCS + _POLY_TCS + (_PIPE_TC, _TEXT_TC, _BUTTON_TC):
            # a V3/V4 class with its own page (Digital Text 0x1006): its states and
            # fonts are bag properties, listed in the Properties panel
            note = QtWidgets.QLabel(
                "This object's own settings (its text states and fonts) are listed in the Properties panel."
            )
            note.setWordWrap(True)
            form.addRow(note)
            return gen
        if stored["flags"] is None and tc != _BUTTON_TC and not self.read_only:
            note = QtWidgets.QLabel(
                "This object takes its appearance from its library element, so it has no "
                "colours or line settings of its own. Edit the element to change them."
            )
            note.setWordWrap(True)
            form.addRow(note)
            self.read_only = True
        if tc == _BUTTON_TC:
            self.button_section = _ButtonSection(self)
            self.button_section.build(form, st, stored)
            self.sections.append(self.button_section)
            self.font_section = _FontSection(self)
            self._font_box_form = form
            self.font_section.build(form, st, stored, (stored["button"] or {}).get("caption", ""), True)
            self._font_box = form.itemAt(form.rowCount() - 1, QtWidgets.QFormLayout.SpanningRole)
            self.font_section.box_visible = self._font_visible
            self.button_section.sync()
        elif tc == _TEXT_TC:
            self.font_section = _FontSection(self)
            self.font_section.build(form, st, stored, st.text or "", False)
            self.font_section.box_visible = lambda on: None
        else:
            line = _LineSection(self)
            line.build(form, st, stored)
            self.sections.append(line)
            if tc in _RECT_TCS + _ELLIPSE_TCS + _POLY_TCS:
                fill = _FillSection(self)
                fill.build(form, st, stored)
                self.sections.append(fill)
            if tc in _RECT_TCS:
                self.rect_section = _RectSection(self)
                self.rect_section.build(form, st, stored)
                self.sections.append(self.rect_section)
            elif tc in _ELLIPSE_TCS:
                sec = _EllipseSection(self)
                sec.build(form, st, stored)
                self.sections.append(sec)
            elif tc in _POLY_TCS:
                sec = _PolygonSection(self)
                sec.build(form, st, stored)
                self.sections.append(sec)
        if self.read_only:
            self.set_read_only(True)
        return gen

    def _font_visible(self, on: bool) -> None:
        item = self._font_box
        w = item.widget() if item is not None else None
        if w is not None:
            w.setVisible(on)

    # -- the sheet's editor contract -------------------------------------------------
    def problem(self):
        """(message, widget) when the caption cannot be stored."""
        s = self.sheet
        if self.button_section is not None:
            p = self.button_section.problem()
            if p:
                return p
        from ...editing import UnencodableTextError, guard_text

        for w in (s._ed_caption, getattr(s, "_ed_symbol", None)):
            if w is None:
                continue
            try:
                guard_text(w.text())
            except UnencodableTextError as exc:
                return "%s. Replace those characters and apply again." % exc, w
        return None

    def apply(self) -> bool:
        s = self.sheet
        doc, i = s._doc, s._index
        if self.read_only:
            return False
        changed = False
        # compared with what the page LOADED, never with the object as it is
        # now: a change made on the page meanwhile is not written back
        stored = self.stored
        for sec in self.sections:
            changed |= bool(sec.apply(doc, i, stored))
        if self.font_section is not None:
            # a text's caption is also the Display Value page's static text: that
            # page's editor writes it (with the keys its type keeps it in)
            if self.button_section is None and getattr(s, "_caption_owner", None) is None:
                cap = s._ed_caption.text()
                if cap != self.font_section.base["caption"]:
                    changed |= bool(doc.set_text(i, cap))
                    self.font_section.base["caption"] = cap
            if self.button_section is None or self.button_section.type() == "text":
                changed |= bool(self.font_section.apply_font(doc, i))
        self._reload_baseline()
        return changed

    def reload(self) -> None:
        """After an Apply (the sheet calls it once its undo step is done):
        show the object as it is stored now, the baseline of the next Apply
        (a value changed on the page meanwhile included)."""
        self.refresh()

    def _reload_baseline(self) -> None:
        """Re-read the stored state the next Apply compares against."""
        s = self.sheet
        doc, i = s._doc, s._index
        stored = _stored(doc, i)
        self.stored = stored
        if self.font_section is not None:
            b = self.font_section.base
            if stored["font"]:
                f = stored["font"]
                b.update(
                    face=f["face"] or "Arial",
                    size=f["size"] or 11,
                    attribs=f["attribs"],
                    charset=f["charset"],
                    fg=tuple(f["text"]),
                )
            elif stored["text"]:
                t = stored["text"]
                b.update(
                    face=t["face"] or "Arial",
                    size=t["size"] or 10,
                    attribs=stored["flags"][3] if stored["flags"] else 0,
                    charset=t["charset"],
                    fg=tuple(stored["slots"][1]) if stored["slots"] else b["fg"],
                )
            if stored["button"] is not None:
                b["caption"] = stored["button"].get("caption", "")
        if self.button_section is not None and stored["button"] is not None:
            self.button_section.base = stored["button"]
            self.button_section._stored_up = stored["button"].get("up")
            self.button_section._stored_down = stored["button"].get("down")

    def refresh(self) -> None:
        """The page changed underneath and nothing here was typed: show the
        object as it is now (the caption's owner, Display Value, shows the
        caption itself)."""
        s = self.sheet
        doc, i = s._doc, s._index
        stored = _stored(doc, i)
        self.stored = stored
        st = doc.page.objects[i].style
        for sec in self.sections:
            if isinstance(sec, _ButtonSection):
                sec.load(stored)
            else:
                sec.load(st, stored)
        if self.font_section is not None:
            if self.button_section is not None:
                caption = (stored["button"] or {}).get("caption", "")
            else:
                caption = st.text or ""
            self.font_section.load(
                st, stored, caption, set_caption=getattr(s, "_caption_owner", None) is None
            )
        if self.button_section is not None:
            self.button_section.sync()

    def set_read_only(self, ro: bool) -> None:
        s = self.sheet
        self.read_only = self.read_only or ro
        secs = list(self.sections) + ([self.font_section] if self.font_section else [])
        for sec in secs:
            for w in sec.widgets:
                if isinstance(w, ColourPairField):
                    w.set_read_only(ro)
                elif isinstance(w, QtWidgets.QPlainTextEdit):
                    w.setReadOnly(ro)
                else:
                    w.setEnabled(not ro)
        if s._ed_caption is not None:
            s._ed_caption.setReadOnly(ro)


class EffectsEditor:
    """Appearance > 3D Effects."""

    phase = "before"

    def __init__(self, sheet):
        self.sheet = sheet
        self.read_only = bool(getattr(sheet, "read_only_reason", ""))
        self.widget = self._build()

    def _build(self) -> QtWidgets.QWidget:
        from ...format import geometry as G

        s = self.sheet
        stored = _stored(s._doc, s._index)
        self.stored = stored
        fl, slots = stored["flags"], stored["slots"]
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        if fl is None:
            note = QtWidgets.QLabel(
                "This object takes its appearance from its library element; it has no 3D effect of its own."
            )
            note.setWordWrap(True)
            v.addWidget(note)
            v.addStretch(1)
            self.read_only = True
            self._group = None
            return w
        box = QtWidgets.QGroupBox("Effects")
        h = QtWidgets.QHBoxLayout(box)
        self._group = QtWidgets.QButtonGroup(box)
        s._rb_effects = {}
        cur = fl[0] & 0x0F
        for label, value in EFFECTS:
            rb = QtWidgets.QRadioButton(label)
            self._group.addButton(rb, value)
            s._rb_effects[value] = rb
            h.addWidget(rb)
        (s._rb_effects.get(cur) or s._rb_effects[0]).setChecked(True)
        h.addStretch(1)
        v.addWidget(box)
        form = QtWidgets.QFormLayout()
        v.addLayout(form)
        drow = QtWidgets.QWidget()
        dh = QtWidgets.QHBoxLayout(drow)
        dh.setContentsMargins(0, 0, 0, 0)
        s._sp_depth = QtWidgets.QSpinBox()
        s._sp_depth.setRange(0, 32)
        s._sp_depth.setSuffix(" px")
        s._sp_depth.setToolTip("How far the 3D edge or the shadow reaches")
        dh.addWidget(s._sp_depth)
        s._chk_auto_depth = QtWidgets.QCheckBox("Automatic")
        s._chk_auto_depth.setToolTip(
            "Size the depth from the object: 1 pixel per 500 pixels of width + height (200 for a shadow)"
        )
        dh.addWidget(s._chk_auto_depth)
        dh.addStretch(1)
        form.addRow("Depth:", drow)
        dp = fl[5] - 256 if fl[5] >= 128 else fl[5]
        self._syncing = True
        s._chk_auto_depth.setChecked(dp == -1)
        s._sp_depth.setValue(G.effect_depth(stored["tc"], self._rect1(), fl) if dp == -1 else max(0, dp))
        self._syncing = False
        s._col_shadow = ColourPairField("Shadow colour")
        s._col_shadow.set_pair(*slots[5])
        s._col_shadow.setToolTip("The shadow's colour (it does not change at runtime)")
        s._col_hilight3d = ColourPairField("Highlight colour")
        s._col_hilight3d.set_pair(*slots[3])
        s._col_hilight3d.setToolTip("The colour of the lit edges (Embossed: the right and lower edges)")
        s._col_lolight3d = ColourPairField("Lowlight colour")
        s._col_lolight3d.set_pair(*slots[4])
        s._col_lolight3d.setToolTip("The colour of the shaded edges (Embossed: the left and upper edges)")
        self._rows = {}
        for label, fld in (
            ("Shadow colour:", s._col_shadow),
            ("Highlight colour:", s._col_hilight3d),
            ("Lowlight colour:", s._col_lolight3d),
        ):
            lab = QtWidgets.QLabel(label)
            form.addRow(lab, fld)
            self._rows[id(fld)] = lab
        from ..style import theme

        v.addWidget(theme.note_label("The 3D effect adds its depth to the object's size."))
        v.addStretch(1)
        self._group.idToggled.connect(self._on_choice)
        s._sp_depth.valueChanged.connect(self._on_depth)
        s._chk_auto_depth.toggled.connect(self._on_auto)
        self._sync()
        if self.read_only:
            self.set_read_only(True)
        return w

    def _rect1(self):
        from ...format import geometry as G

        gh = G.parse_geom_header(self.sheet._doc.parsed.page.objects[self.sheet._index].body_raw)
        return gh.rect1 if gh is not None else (0, 0, 0, 0)

    def choice(self) -> int:
        if self._group is None:
            return 0
        return max(0, self._group.checkedId())

    def _flags_now(self) -> bytes:
        fl = bytearray(self.stored["flags"])
        fl[0] = (fl[0] & 0xF0) | self.choice()
        fl[5] = 0xFF
        return bytes(fl)

    def _on_choice(self, *_a):
        from ...format import geometry as G

        s = self.sheet
        if s._chk_auto_depth.isChecked():
            self._syncing = True
            s._sp_depth.setValue(G.effect_depth(self.stored["tc"], self._rect1(), self._flags_now()))
            self._syncing = False
        self._sync()
        gen = next((e for e in s.built_editors() if isinstance(e, GeneralEditor)), None)
        if gen is not None and gen.rect_section is not None:
            gen.rect_section.sync()

    def _on_depth(self, *_a):
        # typing a depth switches 'Automatic' off (as in CtDraw)
        if not self._syncing:
            self.sheet._chk_auto_depth.setChecked(False)

    def _on_auto(self, on):
        from ...format import geometry as G

        if on:
            self._syncing = True
            self.sheet._sp_depth.setValue(G.effect_depth(self.stored["tc"], self._rect1(), self._flags_now()))
            self._syncing = False

    def _sync(self):
        """None disables the depth and the colours; Shadowed shows the
        shadow colour, the other effects the highlight / lowlight colours."""
        s = self.sheet
        e = self.choice()
        ro = self.read_only
        s._sp_depth.setEnabled(e != 0 and not ro)
        s._chk_auto_depth.setEnabled(e != 0 and not ro)
        for fld, show in (
            (s._col_shadow, e == 3),
            (s._col_hilight3d, e in (1, 2, 4)),
            (s._col_lolight3d, e in (1, 2, 4)),
        ):
            fld.setVisible(show)
            self._rows[id(fld)].setVisible(show)
            fld.set_read_only(ro)

    def depth_value(self) -> int:
        s = self.sheet
        return -1 if s._chk_auto_depth.isChecked() else s._sp_depth.value()

    def problem(self):
        return None

    def apply(self) -> bool:
        from ...format import geometry as G

        s = self.sheet
        if self.read_only or self._group is None:
            return False
        doc, i = s._doc, s._index
        # compared with what the page LOADED (a pipe's General page writes the
        # same highlight / lowlight slots; a change made meanwhile stays)
        stored = self.stored
        fl, slots = stored["flags"], stored["slots"]
        if fl is None:
            return False
        ch = False
        cur_e = fl[0] & 0x0F
        cur_d = fl[5] - 256 if fl[5] >= 128 else fl[5]
        e, d = self.choice(), self.depth_value()
        if e != cur_e or (e != 0 and d != cur_d):
            ch |= bool(doc.set_effect(i, e, d if e != 0 else None))
        for fld, slot, label in (
            (s._col_shadow, G.SLOT_SHADOW, "shadow colour"),
            (s._col_hilight3d, G.SLOT_HILIGHT, "highlight colour"),
            (s._col_lolight3d, G.SLOT_LOLIGHT, "lowlight colour"),
        ):
            if fld.pair() != tuple(slots[slot]):
                ch |= bool(doc.set_colour_slot(i, slot, *fld.pair(), label=label))
        self.stored = _stored(doc, i)
        return ch

    def reload(self) -> None:
        """After an Apply: show the stored effect (the next Apply's baseline)."""
        self.refresh()

    def refresh(self) -> None:
        """The page changed underneath and nothing here was typed: show the
        object's effect, depth and colours as they are now."""
        from ...format import geometry as G

        s = self.sheet
        stored = _stored(s._doc, s._index)
        fl, slots = stored["flags"], stored["slots"]
        if self._group is None or fl is None:
            self.stored = stored
            return
        self.stored = stored
        (s._rb_effects.get(fl[0] & 0x0F) or s._rb_effects[0]).setChecked(True)
        dp = fl[5] - 256 if fl[5] >= 128 else fl[5]
        self._syncing = True
        try:
            s._sp_depth.setValue(G.effect_depth(stored["tc"], self._rect1(), fl) if dp == -1 else max(0, dp))
        finally:
            self._syncing = False
        s._chk_auto_depth.blockSignals(True)
        try:
            s._chk_auto_depth.setChecked(dp == -1)
        finally:
            s._chk_auto_depth.blockSignals(False)
        s._col_shadow.set_pair(*slots[5])
        s._col_hilight3d.set_pair(*slots[3])
        s._col_lolight3d.set_pair(*slots[4])
        self._sync()

    def set_read_only(self, ro: bool) -> None:
        self.read_only = self.read_only or ro
        if self._group is None:
            return
        for b in self._group.buttons():
            b.setEnabled(not ro)
        self._sync()


def build_general(sheet, page):
    ed = GeneralEditor(sheet)
    sheet.add_editor(ed)
    return ed.widget


def build_effects(sheet, page):
    ed = EffectsEditor(sheet)
    sheet.add_editor(ed)
    return ed.widget


def build_visibility(sheet, page):
    return sheet.build_sub(VISIBILITY_SUB)


#: an untouched page writes nothing and refuses nothing (the sheet builds a page when it is
#: first shown; an Apply reads a page not built as the record holds it: objprops/sheet.py)
INERT_UNTOUCHED = True
BUILDERS = {"general": build_general, "effects": build_effects, "visibility": build_visibility}
