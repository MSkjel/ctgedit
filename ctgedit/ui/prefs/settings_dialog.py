"""Tools > Settings."""

from __future__ import annotations

from ...format.projects import is_project_dir
from ...infra import logsetup
from ..style.theme import sp as _sp

import os
from dataclasses import dataclass
from typing import Dict, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ..panels import panel_options as _PO
from .settings import (
    CTDRAW_OPTIONS,
    OPTION_DEFAULTS,
    SETTINGS_KEYS,
    SURROUND_LABELS,
    VIEW_DEFAULTS,
    SettingsFileError,
    app_settings,
    surround_mode,
)
from ..messages import warn


def live_delay_ms() -> int:
    from .settings import SETTINGS_LIVE_DELAY_RANGE, whole

    return whole("ui/settings_live_delay_ms", SETTINGS_LIVE_DELAY_RANGE)


@dataclass(frozen=True)
class SettingRow:
    """One settings row, built from its declaration."""

    page: str
    section: str
    key: str
    label: str
    kind: str = "bool"
    choices: tuple = ()
    span: tuple = (0, 100)
    suffix: str = ""
    special: str = ""
    tip: str = ""
    apply: str = ""
    sub: bool = False
    step: int = 1


def declared_default(key: str):
    """The default ``key`` is declared with (None: none declared)."""
    if key.startswith("opt/") and key[4:] in OPTION_DEFAULTS:
        return OPTION_DEFAULTS[key[4:]]
    from ..style.theme import APPEARANCE_DEFAULTS

    for table in (VIEW_DEFAULTS, APPEARANCE_DEFAULTS, SETTINGS_KEYS):
        if key in table and table[key] is not None:
            return table[key]
    return None


def _stored(key: str, default):
    st = app_settings()
    try:
        if isinstance(default, bool):
            # (as option() reads it: only true / false / 1 / 0, else the default)
            from .settings import stored_bool

            return stored_bool(st.value(key, default), default)
        if isinstance(default, int):
            return int(str(st.value(key, default)).strip())
        if isinstance(default, (list, tuple)):
            # (a list of one is read back as its text without type=list; an empty list is
            # a choice of its own)
            if not st.contains(key):
                return list(default)
            return [str(x) for x in (st.value(key, default, type=list) or [])]
        v = st.value(key, default)
        return default if v is None else v
    except TypeError, ValueError:  # swallow-ok: a garbled setting reads as its default
        return default


# --------------------------------------------------------------------------- widgets
def _theme_thumb(scheme: str, w: int = 76, h: int = 46) -> QtGui.QPixmap:
    """A small picture of the editor in ``scheme`` (dark / light / system)."""
    from ..style import theme

    pm = QtGui.QPixmap(w, h)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)

    def draw(c, clip=None):
        p.save()
        if clip is not None:
            p.setClipPath(clip)
        p.setPen(QtGui.QPen(QtGui.QColor(c["border"]), 1))
        p.setBrush(QtGui.QColor(c["bg"]))
        p.drawRoundedRect(QtCore.QRectF(0.5, 0.5, w - 1, h - 1), 4, 4)
        p.fillRect(QtCore.QRectF(1, 1, w - 2, 9), QtGui.QColor(c["panel"]))
        p.fillRect(QtCore.QRectF(4, 4, 14, 3), QtGui.QColor(c["accent"]))
        p.fillRect(QtCore.QRectF(4, 14, 16, h - 18), QtGui.QColor(c["panel"]))
        p.fillRect(QtCore.QRectF(5, 18, 14, 3), QtGui.QColor(c["selection"]))
        p.fillRect(QtCore.QRectF(24, 14, w - 28, h - 18), QtGui.QColor(c["desk"]))
        p.fillRect(QtCore.QRectF(30, 18, w - 40, h - 26), QtGui.QColor("#ffffff"))
        p.restore()

    if scheme == "system":
        left = QtGui.QPainterPath()
        left.addPolygon(
            QtGui.QPolygonF(
                [
                    QtCore.QPointF(0, 0),
                    QtCore.QPointF(w * 0.62, 0),
                    QtCore.QPointF(w * 0.38, h),
                    QtCore.QPointF(0, h),
                ]
            )
        )
        right = QtGui.QPainterPath()
        right.addRect(QtCore.QRectF(0, 0, w, h))
        right = right.subtracted(left)
        draw(theme.LIGHT, left)
        draw(theme.DARK, right)
    else:
        draw(theme.LIGHT if scheme == "light" else theme.DARK)
    p.end()
    return pm


class ThemeTiles(QtWidgets.QWidget):
    """Theme as three tiles (Dark, Light, Match Windows); answers as a combo box does
    (``currentData``, ``findData``, ``setCurrentIndex``)."""

    currentIndexChanged = QtCore.Signal(int)

    CHOICES = (("dark", "Dark"), ("light", "Light"), ("system", "Match Windows"))

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(_sp(2))
        self.buttons: List[QtWidgets.QToolButton] = []
        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)
        for k, (value, label) in enumerate(self.CHOICES):
            b = QtWidgets.QToolButton()
            b.setCheckable(True)
            b.setText(label)
            b.setIcon(QtGui.QIcon(_theme_thumb(value)))
            b.setIconSize(QtCore.QSize(64, 39))
            b.setToolButtonStyle(QtCore.Qt.ToolButtonTextUnderIcon)
            b.setAccessibleName("Theme: %s" % label)
            b.setToolTip("Pages keep their own colours")
            self._group.addButton(b, k)
            self.buttons.append(b)
            lay.addWidget(b)
        lay.addStretch(1)
        self.buttons[0].setChecked(True)
        self._group.idToggled.connect(lambda k, on: on and self.currentIndexChanged.emit(k))

    def count(self) -> int:
        return len(self.CHOICES)

    def itemText(self, k: int) -> str:  # noqa: N802 -- Qt naming
        return self.CHOICES[k][1]

    def currentIndex(self) -> int:  # noqa: N802 -- Qt naming
        return max(0, self._group.checkedId())

    def currentData(self):  # noqa: N802 -- Qt naming
        return self.CHOICES[self.currentIndex()][0]

    def currentText(self) -> str:  # noqa: N802 -- Qt naming
        return self.CHOICES[self.currentIndex()][1]

    def findData(self, value) -> int:  # noqa: N802 -- Qt naming
        return next((k for k, (v, _l) in enumerate(self.CHOICES) if v == value), -1)

    def setCurrentIndex(self, k: int) -> None:  # noqa: N802 -- Qt naming
        if 0 <= k < len(self.buttons):
            self.buttons[k].setChecked(True)


class AppearancePreview(QtWidgets.QWidget):
    """The look the Appearance choices give, before they are applied: a ribbon row, a
    selected tree row, a field, a check box and a page corner with a selected rectangle."""

    def __init__(self, dialog, parent=None):
        super().__init__(parent)
        self._dlg = dialog
        self.setFixedSize(360, 90)
        self.setAccessibleName("Preview of the appearance")

    def tokens(self) -> Dict[str, str]:
        """The colours the preview paints with now."""
        from ..style import theme

        d = self._dlg
        scheme = d._theme.currentData() if hasattr(d, "_theme") else "dark"
        if scheme == "system":
            scheme = "light" if theme.windows_light_apps() else "dark"
        c = dict(theme.LIGHT if scheme == "light" else theme.DARK)
        acc = theme.accent_for(d._accent_choice(), scheme) if hasattr(d, "_accent") else c["accent"]
        c["accent"] = acc
        a, b = QtGui.QColor(acc), QtGui.QColor(c["bg"])
        mix = lambda t: QtGui.QColor(  # noqa: E731
            round(b.red() + (a.red() - b.red()) * t),
            round(b.green() + (a.green() - b.green()) * t),
            round(b.blue() + (a.blue() - b.blue()) * t),
        ).name()
        c["selection"] = mix(0.45)
        c["checked"] = mix(0.18)
        sel = d._look_value("view/selection_colour") if hasattr(d, "_look_value") else "accent"
        c["canvas_sel"] = acc if sel in (None, "accent") else str(sel)
        c["scheme"] = scheme
        return c

    def paintEvent(self, _e):  # noqa: N802 -- Qt naming
        c = self.tokens()
        d = self._dlg
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        W, H = self.width(), self.height()
        p.setPen(QtGui.QPen(QtGui.QColor(c["border"]), 1))
        p.setBrush(QtGui.QColor(c["bg"]))
        p.drawRoundedRect(QtCore.QRectF(0.5, 0.5, W - 1, H - 1), 5, 5)
        # the ribbon row
        p.fillRect(QtCore.QRectF(1, 1, 190, 26), QtGui.QColor(c["panel"]))
        for k in range(4):
            r = QtCore.QRectF(8 + k * 30, 5, 24, 18)
            if k == 1:
                p.setPen(QtGui.QPen(QtGui.QColor(c["accent"]), 1))
                p.setBrush(QtGui.QColor(c["checked"]))
                p.drawRoundedRect(r, 3, 3)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(c["text_dim"] if k != 1 else c["accent"]))
            p.drawRoundedRect(r.adjusted(7, 5, -7, -5), 1.5, 1.5)
        # a selected tree row and a plain one
        f = QtGui.QFont(self.font())
        f.setPointSizeF(max(6.5, f.pointSizeF() * 0.85))
        p.setFont(f)
        p.fillRect(QtCore.QRectF(6, 32, 180, 16), QtGui.QColor(c["selection"]))
        p.setPen(QtGui.QColor(c["on_selection"]))
        p.drawText(QtCore.QRectF(12, 32, 170, 16), QtCore.Qt.AlignVCenter, "Tank 1")
        p.setPen(QtGui.QColor(c["text"]))
        p.drawText(QtCore.QRectF(12, 48, 170, 14), QtCore.Qt.AlignVCenter, "Pump 2")
        # a field and a check box
        p.setPen(QtGui.QPen(QtGui.QColor(c["border"]), 1))
        p.setBrush(QtGui.QColor(c["bg"] if c["scheme"] != "light" else c["card"]))
        p.drawRoundedRect(QtCore.QRectF(8, 66, 100, 18), 3, 3)
        p.setPen(QtGui.QColor(c["text"]))
        p.drawText(QtCore.QRectF(14, 66, 90, 18), QtCore.Qt.AlignVCenter, "Mixer")
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(c["accent"]))
        box = QtCore.QRectF(120, 69, 12, 12)
        p.drawRoundedRect(box, 2, 2)
        p.setPen(QtGui.QPen(QtGui.QColor(c["on_selection"]), 1.6))
        p.drawPolyline(
            QtGui.QPolygonF([QtCore.QPointF(122.5, 75), QtCore.QPointF(125.5, 78), QtCore.QPointF(130, 72)])
        )
        p.setPen(QtGui.QColor(c["text"]))
        p.drawText(QtCore.QRectF(138, 66, 50, 18), QtCore.Qt.AlignVCenter, "Snap")
        # the page corner on the desk
        desk = QtCore.QRectF(198, 1, W - 199, H - 2)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(c["desk"]))
        p.drawRoundedRect(desk, 4, 4)
        page = QtCore.QRectF(214, 14, W - 214, H - 14)
        if d._look_value("opt/page_shadow") is not False:
            for k, alpha in enumerate((40, 25, 12, 6)):
                p.fillRect(page.translated(2 + k * 0.6, 2 + k * 0.6), QtGui.QColor(0, 0, 0, alpha))
        p.fillRect(page, QtGui.QColor("#ffffff"))
        edge = d._look_value("view/page_edge") or "dotted"
        if edge != "none":
            pen = QtGui.QPen(
                QtGui.QColor("#808080"), 1, QtCore.Qt.DotLine if edge == "dotted" else QtCore.Qt.SolidLine
            )
            p.setPen(pen)
            p.drawLine(page.topLeft(), page.topRight())
            p.drawLine(page.topLeft(), page.bottomLeft())
        style = d._look_value("view/grid_style") or "lines"
        grid = QtGui.QColor(0, 0, 0, 28)
        step = 8
        if style == "dots":
            p.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, 90), 1))
            for x in range(int(page.left()) + step, W, step):
                for y in range(int(page.top()) + step, H, step):
                    p.drawPoint(x, y)
        else:
            p.setPen(QtGui.QPen(grid, 1))
            for x in range(int(page.left()) + step, W, step):
                p.drawLine(x, int(page.top()), x, H)
            for y in range(int(page.top()) + step, H, step):
                p.drawLine(int(page.left()), y, W, y)
        rect = QtCore.QRectF(250, 34, 64, 34)
        p.setPen(QtGui.QPen(QtGui.QColor("#3a6ea5"), 1))
        p.setBrush(QtGui.QColor("#9fc4e8"))
        p.drawRect(rect)
        sel = QtGui.QColor(c["canvas_sel"])
        ow = {"medium": 2, "thick": 3}.get(d._look_value("view/outline_width") or "thin", 1)
        p.setPen(QtGui.QPen(sel, ow, QtCore.Qt.DashLine))
        p.setBrush(QtCore.Qt.NoBrush)
        p.drawRect(rect.adjusted(-2, -2, 2, 2))
        hs = {"small": 6, "medium": 8, "large": 11}.get(d._look_value("view/handle_size") or "medium", 8)
        p.setPen(QtGui.QPen(sel, 1))
        p.setBrush(QtGui.QColor("#ffffff"))
        for pt in (rect.topLeft(), rect.topRight(), rect.bottomLeft(), rect.bottomRight()):
            p.drawRect(QtCore.QRectF(pt.x() - hs / 2.0, pt.y() - hs / 2.0, hs, hs))
        p.end()


# --------------------------------------------------------------------------- widget state
def _colour_button(w) -> bool:
    """A colour button (ui/colour_picker.ColourButton): its colour is a value, not a check."""
    return (
        isinstance(w, QtWidgets.QAbstractButton)
        and not w.isCheckable()
        and hasattr(w, "pair")
        and hasattr(w, "set_pair")
    )


def _stateful(w) -> bool:
    if w.property("ctgNoState"):
        return False
    p = w.parentWidget()
    while p is not None:
        if p.property("ctgNoState"):
            return False
        p = p.parentWidget()
    if isinstance(w, QtWidgets.QAbstractButton):
        if w.isCheckable():
            return True
        # a row's Custom colour (Guideline colour, Selection colour, Accent colour) is part
        # of its state; not the swatches of a widget that keeps them itself (a
        # ColourPairField's pair, the colours of NewColoursRow.state)
        p = w.parentWidget()
        return _colour_button(w) and not (
            p is not None and (hasattr(p, "set_pair") or hasattr(p, "set_state"))
        )
    if isinstance(w, (QtWidgets.QAbstractSpinBox, QtWidgets.QComboBox, QtWidgets.QListWidget)):
        return True
    if isinstance(w, QtWidgets.QLineEdit):
        return not isinstance(w.parentWidget(), (QtWidgets.QAbstractSpinBox, QtWidgets.QComboBox))
    return hasattr(w, "pair") and hasattr(w, "set_pair")


def _read(w):
    if _colour_button(w):
        # (a hidden one is not in use: its drop-down shows a colour that is not Custom)
        pair = w.pair()
        return None if w.isHidden() or pair is None else tuple(pair)
    if isinstance(w, QtWidgets.QAbstractButton):
        return w.isChecked()
    if isinstance(w, QtWidgets.QAbstractSpinBox):
        return w.value()
    if isinstance(w, QtWidgets.QComboBox):
        return w.currentIndex()
    if isinstance(w, QtWidgets.QListWidget):
        return tuple((w.item(k).text(), w.item(k).data(QtCore.Qt.UserRole)) for k in range(w.count()))
    if isinstance(w, QtWidgets.QLineEdit):
        return w.text()
    pair = w.pair()
    return tuple(pair) if pair is not None else None


def _write(w, v) -> None:
    if _read(w) == v:
        return
    if _colour_button(w):
        if v is not None:  # (None: not in use, the colour it holds stays)
            w.set_pair(tuple(v))
            w.changed.emit()  # (the row applies it, as a picked colour)
    elif isinstance(w, QtWidgets.QAbstractButton):
        w.setChecked(bool(v))
    elif isinstance(w, QtWidgets.QAbstractSpinBox):
        w.setValue(v)
    elif isinstance(w, QtWidgets.QComboBox):
        w.setCurrentIndex(int(v))
    elif isinstance(w, QtWidgets.QListWidget):
        w.clear()
        for text, data in v:
            it = QtWidgets.QListWidgetItem(text)
            it.setData(QtCore.Qt.UserRole, data)
            w.addItem(it)
    elif isinstance(w, QtWidgets.QLineEdit):
        w.setText(v)
    else:
        from ..objprops.colour_field import ColourPairField

        if isinstance(w, ColourPairField) and isinstance(v, tuple):
            w.set_pair(*v)  # (on, off)
        else:
            w.set_pair(v)
        sig = getattr(w, "changed", None)
        if sig is not None:
            sig.emit()


def _shown(w, v) -> str:
    """A widget's value ``v`` as the row shows it (the 'Default:' of its tool tip)."""
    if _colour_button(w):
        return "None" if v is None else "#%06x" % (int(v[0]) & 0xFFFFFF)
    if isinstance(w, QtWidgets.QAbstractButton):
        return "On" if v else "Off"
    if isinstance(w, QtWidgets.QAbstractSpinBox):
        if v == w.minimum() and w.specialValueText():
            return w.specialValueText()
        num = ("%g" % v) if isinstance(v, float) else str(v)
        return "%s%s%s" % (w.prefix(), num, w.suffix())
    if isinstance(w, QtWidgets.QComboBox):
        return w.itemText(int(v)) if 0 <= int(v) < w.count() else ""
    if isinstance(w, QtWidgets.QListWidget):
        return "None" if not v else ", ".join(t for t, _d in v)
    if isinstance(w, QtWidgets.QLineEdit):
        return v or "None"
    if v is None:
        return "None"
    return "#%06x" % (int(v[0]) & 0xFFFFFF)


def _same(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):

        def tb(x):
            return str(x).lower() in ("true", "1") if isinstance(x, str) else bool(x)

        return tb(a) == tb(b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return [str(x) for x in a] == [str(x) for x in b]
    return str(a) == str(b)


def _each(wins, name: str, *args, **kw) -> None:
    """Call each window's method ``name`` (a window without it, or one being closed, is
    left out)."""
    for win in wins:
        fn = getattr(win, name, None)
        if callable(fn):
            try:
                fn(*args, **kw)
            except RuntimeError:  # swallow-ok: a window being closed
                continue


def _redraw_pages(win, current_done: bool) -> None:
    """``win``'s open pages drawn again after a drawing choice changed (its current page
    unless ``current_done``: the label mode just drew it)."""
    doc = getattr(win, "doc", None)
    if not current_done and doc is not None:
        try:
            doc._rerender_page()
        except Exception:  # noqa: BLE001 -- swallow-ok: as _set_label_mode, the page keeps its drawing
            pass
        win._refresh()
    for e in list(getattr(win, "_docs", ())):
        d = e.get("doc")
        if d is not None and d is not doc and hasattr(d, "_rerender_page"):
            try:
                d._rerender_page()
            except Exception:  # noqa: BLE001 -- swallow-ok: the page derives again when shown
                pass


class SettingsDialog(QtWidgets.QDialog):
    """Editor settings. Applied to every open editor window and persisted to QSettings."""

    #: check boxes, spin boxes and short choices: a fixed size (UI guide section 9)
    #: the page list and the pages grow with the window
    RESIZE = "both"

    #: Settings > Appearance > Canvas > Selection colour (view/selection_colour,
    #: theme.canvas_colour): the canvas's selection, hover, box and handle pens read it
    SELECTION_COLOUR_ROW = True

    #: every on / off option the dialog shows (``opt/<key>``) and its label
    FLAGS = [
        # the Graphics Builder's Tools > Options (its names: GB_NAMES)
        ("props_on_new", "New objects open their properties"),
        ("props_on_copy", "Pasted and duplicated objects open their properties"),
        ("save_template_warning", "Ask what to update after saving a template"),
        ("modify_an", "Allow changing an object's AN"),
        ("disable_genie_forms", "Skip the genie form and open the object properties"),
        ("genie_group_button", "Add a Group button to genie forms"),
        ("compile_enquiry", "Ask whether to compile before Run"),
        ("list_system_pages", "List system pages in Page Properties"),
        ("show_legacy_tools", "Show the old V3 and V4 tools"),
        ("xp_buttons", "Draw new buttons in the XP style"),
        # editing
        ("dblclick_edits_text", "Double-click a text to edit it in place"),
        ("stretch_texts", "Stretch a text that is resized unevenly"),
        ("colour_cicode", "Colour Cicode and underline problems"),
        ("function_hints", "Show a function's arguments while typing"),
        ("confirm_delete", "Confirm before deleting objects"),
        ("unlock_template", "Allow editing template objects on pages"),
        ("props_arrow_live", "Arrow keys in number fields apply at once"),
        ("props_scrub", "Drag labels to change numbers"),
        # pages and libraries
        ("auto_update_template", "Apply a changed template again"),
        ("auto_update_symbols", "Update changed symbols"),
        ("auto_update_genies", "Update changed genies"),
        ("genie_nested_full", "Rebuild a whole genie when only a genie nested in it changed"),
        ("auto_placeholder_missing", "Use placeholders for objects whose library is missing"),
        ("show_auto_changes", "Show a bar listing the automatic updates"),
        ("library_siblings", "Also look for libraries in the other projects of this install"),
        ("text_question_marks", "Save characters the code page cannot hold as ?"),
        ("turn_gradients", "Turn a gradient with its shape"),
        ("reversible_turns", "Turn again about the same centre"),
        ("ctf_rebuild_stale", "Replace an out-of-date fast display file"),
        ("save_supergenie_warning", "Offer Update Pages after saving a super genie"),
        ("auto_close_builder", "Close the Graphics Builder after saving a library"),
        ("warn_shipped_template", "Warn before saving into a library supplied with Plant SCADA"),
        # advanced
        ("developer_tools", "Developer and diagnostic tools"),
        ("dyn_badges", "Show dynamics badges on animated objects"),
        ("display_check", "Check each opened page's display"),
    ]
    #: what a search for the Graphics Builder's name of an option finds (its Tools > Options)
    GB_NAMES = {
        o.setting[len("opt/") :] if o.setting.startswith("opt/") else o.setting: o.label
        for o in CTDRAW_OPTIONS
        if o.setting
    }
    #: the options' tool tips
    TIPS = {
        "props_on_new": "After you draw an object or paste a symbol. A genie with parameters "
        "asks for them first",
        "props_on_copy": "After a paste or a duplicate",
        "save_template_warning": "The question offers the update in the background and "
        "Update Pages. Off, nothing is asked",
        "modify_an": "On Access > General. The object's touch and keyboard commands follow it",
        "disable_genie_forms": "When a genie is placed and when it is edited",
        "genie_group_button": "The button opens the genie's object properties",
        "compile_enquiry": "When the project has changes that are not compiled",
        "list_system_pages": "System pages (!) in the Previous and Next page lists",
        "show_legacy_tools": "String, Bar Graph, Slider, Animated Symbol and others, in the "
        "Tools menu and on the ribbon",
        "xp_buttons": "Off: the classic raised look",
        "dblclick_edits_text": "Off: a double-click opens the text's properties, as in the Graphics Builder",
        "stretch_texts": "As the Graphics Builder does. Off: the text keeps its shape and its "
        "point size follows the new height",
        "colour_cicode": "Keywords, strings, numbers, functions, tags and genie parameters each "
        "get a colour. Unknown tags and functions get a wavy underline",
        "function_hints": "Above the field, with the argument you are typing in bold. Esc hides it",
        "unlock_template": "Lets you select and change the template's objects on a page",
        "props_arrow_live": "In the Properties panel, after a short pause, as one undo step. "
        "Off: Enter applies",
        "props_scrub": "In the Properties panel: X, Y, W, H and the other number labels. "
        "Shift: faster, Alt: finer, Esc: back",
        "auto_update_template": "The page takes the new version of its template",
        "auto_update_symbols": "The page takes the new versions of its symbols, also while "
        "it is open. So does a genie you open",
        "auto_update_genies": "Also when a genie nested in them changed, when the "
        "page's tab is shown again, and in a genie you open",
        "genie_nested_full": "Off: only the nested copies are updated. Faster on big pages",
        "auto_placeholder_missing": "The placeholder keeps the object's library name, place "
        "and values, and turns back into the object when the "
        "library is found",
        "show_auto_changes": "The bar above the page, with an Undo button. Off: the status "
        "bar still says what changed, and Edit > Undo takes it back",
        "reversible_turns": "Four quarter turns give back exactly what was there. The "
        "Graphics Builder can move it a pixel",
        "turn_gradients": "The Graphics Builder leaves a gradient pointing the same way",
        "ctf_rebuild_stale": "Off: the old .ctF is deleted and the runtime draws the page from its .ctg",
        "text_question_marks": "Off: ctgedit refuses the text and names the characters",
        "library_siblings": "The Graphics Builder only searches the page's project and its included projects",
        "auto_close_builder": "It keeps the old symbols and genies in memory until it closes. When "
        "it has a page open, ctgedit asks first",
        "developer_tools": "The thumbnail underlay and the dynamics badges",
        "display_check": "A second process draws each opened page again and reports any "
        "difference in the status bar",
    }
    #: options that are ON until the user turns them off
    DEFAULTS = {k: v for k, v in OPTION_DEFAULTS.items() if v}
    #: which page each option is on (the pages without on / off options are left out)
    TABS = (
        (
            "Editing",
            (
                "dblclick_edits_text",
                "stretch_texts",
                "colour_cicode",
                "function_hints",
                "props_on_new",
                "xp_buttons",
                "modify_an",
                "disable_genie_forms",
                "genie_group_button",
                "turn_gradients",
                "reversible_turns",
                "confirm_delete",
                "show_legacy_tools",
                "props_arrow_live",
                "props_scrub",
            ),
        ),
        ("Canvas", ("props_on_copy",)),
        (
            "Pages and libraries",
            (
                "auto_update_template",
                "auto_update_symbols",
                "auto_update_genies",
                "genie_nested_full",
                "auto_placeholder_missing",
                "show_auto_changes",
                "ctf_rebuild_stale",
                "save_template_warning",
                "save_supergenie_warning",
                "auto_close_builder",
                "warn_shipped_template",
                "unlock_template",
                "list_system_pages",
                "compile_enquiry",
                "library_siblings",
                "text_question_marks",
            ),
        ),
        ("Advanced", ("developer_tools", "dyn_badges", "display_check")),
    )

    #: the rows built from their declaration (SettingRow); a later setting appends one. The
    #: Appearance page's look controls are the theme's, the tabs' and the navigation's own
    #: (one control a setting); a setting nothing reads yet gets its row with the package
    #: that reads it (the handle size, the panel tabs, the Objects panel's eye).
    ROWS: List[SettingRow] = [
        SettingRow(
            "Canvas",
            "Grid and guidelines",
            "view/grid",
            "Show the grid",
            apply="action:act_grid",
            tip="Also View > Grid > Show Grid",
        ),
        SettingRow(
            "Canvas",
            "Grid and guidelines",
            "view/snap",
            "Snap to the grid",
            apply="action:act_snap",
            tip="Also View > Grid > Snap to Grid (F8)",
        ),
        SettingRow(
            "Canvas",
            "Grid and guidelines",
            "view/guides_visible",
            "Show the guidelines",
            apply="action:act_show_guides",
            tip="Also View > Guidelines > Display Guidelines",
        ),
        SettingRow(
            "Canvas",
            "Grid and guidelines",
            "view/snap_guides",
            "Snap to the guidelines",
            apply="action:act_snap_guides",
            tip="Also View > Guidelines > Snap to Guidelines (F7)",
        ),
        SettingRow(
            "Canvas",
            "Grid and guidelines",
            "view/smart_guides",
            "Show smart guides while dragging",
            apply="action:act_smart_guides",
            tip="Snap the edges and centre to other objects. Hold Alt to drag freely",
        ),
        SettingRow(
            "Canvas",
            "Grid and guidelines",
            "view/guide_width",
            "Guidelines:",
            kind="choice",
            choices=(("thin", "Thin (1 px)"), ("medium", "Medium (2 px)"), ("thick", "Thick (3 px)")),
            apply="window:apply_select_options",
            tip="How wide the guidelines are drawn. A wide one is easy to grab anywhere on it",
        ),
        SettingRow(
            "Canvas",
            "Grid and guidelines",
            "view/guide_reach",
            "Grab a guideline within:",
            kind="choice",
            choices=(("near", "3 px"), ("medium", "6 px"), ("far", "10 px")),
            apply="window:apply_select_options",
            tip="How near the pointer must be to a guideline to drag it",
        ),
        # the earlier versions of each page (File > Page History: ui/dialog/history_versions)
        SettingRow(
            "Editing",
            "Safety",
            "opt/history_versions",
            "Keep earlier versions of each page:",
            kind="int",
            span=(0, 100),
            suffix=" saves",
            special="Off",
            tip="Each save keeps the page as it was before it (File > Page History)",
        ),
        SettingRow(
            "Editing",
            "Safety",
            "opt/history_mb",
            "Earlier versions use at most:",
            kind="int",
            span=(10, 10000),
            suffix=" MB",
            tip="All pages together. Past it, the oldest versions go first",
        ),
        SettingRow(
            "Canvas",
            "Undo and Redo",
            "ui/undo_reveal",
            "Undo and Redo select and show what they change",
            tip="The objects a step changed are selected, scrolled into view when "
            "none of them shows, and flash",
        ),
        SettingRow(
            "Advanced",
            "Updates",
            "opt/check_updates",
            "Check for a new version when ctgedit starts",
            tip="Once a day at most, on GitHub. Help > Check for Updates looks at any time",
        ),
        SettingRow(
            "Advanced",
            "The settings window",
            "ui/settings_live",
            "Apply each change at once",
            tip="Off: OK, Cancel and Apply. Takes effect the next time this window opens",
        ),
        # the messages, the results over the page, the taskbar, a changed page
        SettingRow(
            "Appearance",
            "Messages",
            "ui/message_time",
            "Messages stay:",
            kind="choice",
            choices=(("short", "Short"), ("normal", "Normal"), ("long", "Long")),
            tip="How long every message stays in the status bar. At Normal, "
            "warnings and errors stay at least 10 s. The bell lists every "
            "message again",
        ),
        SettingRow(
            "Appearance",
            "Messages",
            "ui/toasts",
            "Show results over the page",
            tip="A deleted, pasted or replaced group of objects is also told over the page, with Undo",
        ),
        SettingRow(
            "Appearance",
            "Messages",
            "ui/taskbar_progress",
            "Show progress on the taskbar button",
            tip="Background tasks and long steps fill the taskbar button; it "
            "flashes when one ends while you work in another program",
        ),
        SettingRow(
            "Pages and libraries",
            "Files and folders",
            "ui/disk_change_prompt",
            "Tell me about a changed page:",
            kind="choice",
            choices=(("bar", "In a bar over the page"), ("dialog", "With a question box")),
            tip="When another program changes a page open here and it is not reloaded by itself",
        ),
        # the Objects panel's rows (ui/panels/objects_dock.py)
        SettingRow(
            "Appearance",
            "Objects panel",
            "objects/row_label",
            "Rows show:",
            kind="choice",
            choices=(("smart", "Name, element and tag first"), ("classic", "Kind and AN first")),
            apply="window:_objects_panel_settings",
            tip="What each row of the Objects panel leads with",
        ),
        SettingRow(
            "Appearance",
            "Objects panel",
            "objects/show_an",
            "Show the AN at the right of each row",
            apply="window:_objects_panel_settings",
        ),
        SettingRow(
            "Appearance",
            "Objects panel",
            "ui/tree_eye",
            "Eye and padlock:",
            kind="choice",
            choices=(("always", "Always"), ("hover", "When the pointer is on the row")),
            apply="window:_objects_panel_settings",
            tip="A hidden or locked object always shows its eye or padlock",
        ),
        SettingRow(
            "Appearance",
            "Objects panel",
            "objects/toggles_side",
            "Eye and padlock side:",
            kind="choice",
            choices=(("right", "Right"), ("left", "Left")),
            apply="window:_objects_panel_settings",
        ),
        # selecting and transforming on the canvas (MainWindow.apply_select_options)
        SettingRow(
            "Canvas",
            "Selecting",
            "canvas/box_preview",
            "Show what a selection box takes while you drag it",
            apply="window:apply_select_options",
            tip="The objects it would select are outlined, and their number shows",
        ),
        SettingRow(
            "Canvas",
            "Selecting",
            "canvas/box_from_background",
            "A drag on a large object starts a selection box",
            apply="window:apply_select_options",
            tip="An object that covers more than a quarter of the view, such as a "
            "background panel. A click still selects it",
        ),
        SettingRow(
            "Canvas",
            "Selecting",
            "canvas/click_selected_isolates",
            "A click on one of several selected objects selects only it",
            apply="window:apply_select_options",
            tip="Off: the click keeps them all, so a drag moves them all",
        ),
        SettingRow(
            "Canvas",
            "Selecting",
            "opt/tab_selects_objects",
            "Tab selects the next object on the canvas",
            apply="window:apply_select_options",
            tip="Shift+Tab the one before. Off: Tab moves to the next panel",
        ),
        SettingRow(
            "Canvas",
            "Selecting",
            "sel/tab_order",
            "Tab order:",
            kind="choice",
            choices=(("reading", "Reading order"), ("drawing", "Drawing order")),
            apply="window:apply_select_options",
            tip="Reading order: rows from the top, each from the left. Drawing order: "
            "from the back to the front",
        ),
        SettingRow(
            "Canvas",
            "Selecting",
            "canvas/ctrl_shift_drag",
            "Ctrl+Shift + drag:",
            kind="choice",
            choices=(("zoom", "Zoom into the box"), ("touch", "Select with a Touch box")),
            apply="window:apply_select_options",
            tip="A Touch box selects every object it crosses, also when the drag "
            "starts on an object. The Hand tool's Ctrl+Shift + drag always zooms",
        ),
        SettingRow(
            "Canvas",
            "Selecting",
            "opt/reveal_selection",
            "Scroll to objects selected in a panel",
            tip="The Objects panel, Goto Object, Find, Select Previous and Select "
            "Objects Using Tag. The zoom stays as it is",
        ),
        SettingRow(
            "Canvas",
            "Moving and copying",
            "canvas/modifier_scheme",
            "Modifier keys:",
            kind="choice",
            choices=(("builder", "Graphics Builder"), ("design", "Design tools")),
            apply="window:apply_select_options",
            tip="For drawing, resizing and moving. Graphics Builder: Ctrl keeps the "
            "proportions, Shift works from the centre. Design tools: Shift keeps "
            "the proportions, Alt works from the centre",
        ),
        SettingRow(
            "Canvas",
            "Moving and copying",
            "canvas/drag_copy_key",
            "Drag to copy with:",
            kind="choice",
            choices=(("ctrl", "Ctrl"), ("alt", "Alt")),
            apply="window:apply_select_options",
            tip="Hold it when you let go of a drag: the objects stay and a copy lands. "
            "With Alt, Alt+Shift moves without snapping",
        ),
        SettingRow(
            "Canvas",
            "Moving and copying",
            "edit/smart_duplicate",
            "Duplicate repeats the last offset",
            tip="Move a copy made by Duplicate, and the next Duplicate puts its copy the same distance on",
        ),
        SettingRow(
            "Editing",
            "Moving and resizing",
            "opt/ctrl_arrow",
            "Ctrl + arrow keys:",
            kind="choice",
            choices=(
                ("move", "Same as arrow keys"),
                ("x10", "Move one large step"),
                ("grid", "Move one grid step"),
            ),
        ),
        SettingRow(
            "Editing",
            "Moving and resizing",
            "opt/arrow_grid",
            "With Snap to Grid, the arrow keys move to the next grid line",
        ),
        SettingRow(
            "Editing",
            "Moving and resizing",
            "opt/arrows_pan",
            "With nothing selected, the arrow keys scroll the page",
            tip="Shift: a whole view. Home and End show the page's left and right edge",
        ),
        # (the choices carry their figure: canvas.HANDLE_SIZES)
        SettingRow(
            "Appearance",
            "Canvas",
            "view/handle_size",
            "Handle size:",
            kind="choice",
            choices=(("small", "Small (6 px)"), ("medium", "Medium (8 px)"), ("large", "Large (11 px)")),
            apply="window:apply_select_options",
            tip="The sizing handles round the selection, and how near a press must be to grab one",
        ),
        SettingRow(
            "Appearance",
            "Canvas",
            "view/outline_width",
            "Selection outlines:",
            kind="choice",
            choices=(("thin", "Thin (1 px)"), ("medium", "Medium (2 px)"), ("thick", "Thick (3 px)")),
            apply="window:apply_select_options",
            tip=(
                "The outlines of selected objects and of the object under the pointer. Medium and "
                "thick also widen the smart guides, the selection and zoom boxes, the drawing "
                "preview and the change marks"
            ),
        ),
        SettingRow(
            "Appearance",
            "Canvas",
            "view/selection_box",
            "Show a box around several selected objects",
            apply="window:apply_select_options",
            tip="With handles that scale the whole selection",
        ),
        # the look of new objects (ui/editor/draw_styles) and the Properties panel
        SettingRow(
            "Editing",
            "New objects",
            "opt/new_object_style",
            "Style for new objects:",
            kind="choice",
            choices=(("tool", "This tool's own"), ("last", "Last used")),
            tip=(
                "This tool's own: each drawing tool keeps its own style. Last used: the look of "
                "the last object of that kind you changed"
            ),
        ),
        SettingRow(
            "Editing",
            "Properties panel",
            "props/multi_apply",
            "Several objects: X, Y, W and H change",
            kind="choice",
            choices=(("selection", "The whole selection"), ("each", "Each object")),
            tip="Also the switch above them in the panel",
        ),
        SettingRow(
            "Editing",
            "Properties panel",
            "props/search_keep",
            "Keep the property search when the selection changes",
        ),
        # the underlay (View > Underlay: ui/editor/underlay.py); the window of each underlay changes
        # its own, these are what a new one starts with
        SettingRow(
            "Canvas",
            "Underlay",
            "opt/underlay_opacity",
            "New underlays show at:",
            kind="int",
            span=(5, 100),
            suffix=" %",
            tip="The opacity a new underlay starts with. Its window changes it",
        ),
        SettingRow(
            "Canvas",
            "Underlay",
            "opt/underlay_tint",
            "Tint of new underlays:",
            kind="choice",
            choices=(("none", "None"), ("grey", "Grey"), ("blue", "Blue")),
            tip="Grey or Blue make an underlay read as a reference, not as objects",
        ),
        SettingRow(
            "Canvas",
            "Underlay",
            "opt/underlay_above",
            "Draw new underlays above the objects",
            tip="Off: under the objects, over the page and the grid",
        ),
        SettingRow(
            "Canvas",
            "Underlay",
            "opt/underlay_snap",
            "New page underlays snap to their objects",
            tip="Objects you drag, draw or resize snap to the edges and centres of the "
            "underlaid page's objects",
        ),
        SettingRow(
            "Canvas",
            "Underlay",
            "opt/underlay_remember",
            "Remember each page's underlay",
            tip="It comes back, where you left it, when the page opens again",
        ),
        SettingRow(
            "Canvas",
            "Underlay",
            "opt/underlay_resolution",
            "Draw page underlays at:",
            kind="choice",
            choices=((1, "1x the page size"), (2, "2x the page size"), (4, "4x the page size")),
            tip="More is sharper when you zoom in, and takes more memory",
        ),
        # ui/editor/canvas_options.apply_canvas_settings applies them: an
        # apply hook of each key
        # (the choices carry their figure: canvas.POINTER_REACHES)
        SettingRow(
            "Canvas",
            "Mouse",
            "canvas/pointer_reach",
            "Grab handles and nodes within:",
            kind="choice",
            choices=(
                ("near", "Near (the handle itself)"),
                ("medium", "Medium (3 px more)"),
                ("far", "Far (6 px more)"),
            ),
            tip="Also the rotation grip, the turn zone at a corner and a symbol's or "
            "genie's hot spot. Far suits a pen or a touchpad",
        ),
        SettingRow(
            "Canvas",
            "Mouse",
            "canvas/drag_start",
            "A drag starts after the pointer moves:",
            kind="choice",
            choices=(("short", "3 px"), ("medium", "6 px"), ("long", "10 px")),
            tip="A shorter move is a click. For moving, node and guideline drags, "
            "selection boxes and drawn boxes",
        ),
        SettingRow(
            "Canvas",
            "Mouse",
            "opt/mouse_back_forward",
            "Mouse back and forward buttons:",
            kind="choice",
            choices=(
                ("view", "Previous and Next View"),
                ("tabs", "The previous and next tab"),
                ("nothing", "Nothing"),
            ),
            tip="The side buttons of the mouse, over the page",
        ),
        SettingRow(
            "Canvas",
            "Mouse",
            "opt/page_double_click",
            "Double-click on the empty page:",
            kind="choice",
            choices=(("nothing", "Does nothing"), ("page_props", "Opens Page Properties")),
            tip="Where there is no object or guideline",
        ),
        SettingRow(
            "Canvas",
            "Selecting",
            "view/hover_outline",
            "Outline the object under the pointer",
            tip="With the Select tool. Off, pointing at a row of the Objects panel still outlines its object",
        ),
        SettingRow(
            "Canvas",
            "Grid and guidelines",
            "view/guide_snap",
            "Snap to a guideline within:",
            kind="choice",
            choices=(("near", "4 px"), ("normal", "8 px (as the Graphics Builder)"), ("far", "16 px")),
            tip="How near a moved edge, a drawn point or a dragged handle must come to "
            "a guideline. With Snap to the grid, at least one grid step",
        ),
        SettingRow(
            "Canvas",
            "Zoom and scroll",
            "view/frame_zoom_max",
            "Zoom to an object zooms in at most:",
            kind="choice",
            choices=(
                ("100", "100 %"),
                ("200", "200 %"),
                ("400", "400 %"),
                ("800", "800 %"),
                ("0", "No limit"),
            ),
            tip="Zoom to Selection, Goto Object, a double-click in the Objects panel, Find and Quick Open",
        ),
        # (the choices carry their figure: viewnav.EDGE_SPEED x EDGE_SPEED_FACTORS,
        # navigator.MAP_SIZES)
        SettingRow(
            "Canvas",
            "Zoom and scroll",
            "opt/edge_scroll_speed",
            "Edge scroll speed:",
            kind="choice",
            sub=True,
            choices=(
                ("slow", "Slow (up to 9 px a step)"),
                ("normal", "Normal (up to 18 px a step)"),
                ("fast", "Fast (up to 36 px a step)"),
            ),
            tip="How fast the page scrolls while you drag at its edge. Nearer the edge is faster",
        ),
        SettingRow(
            "Canvas",
            "Navigation",
            "view/navigator_size",
            "Navigator size:",
            kind="choice",
            choices=(
                ("small", "Small (150 x 90 px)"),
                ("medium", "Medium (200 x 120 px)"),
                ("large", "Large (280 x 168 px)"),
            ),
            tip="The map over the canvas (View > Navigator). The Navigator panel keeps its own size",
        ),
        SettingRow(
            "Canvas",
            "Navigation",
            "view/navigator_corner",
            "Navigator corner:",
            kind="choice",
            choices=(
                ("bottom_right", "Bottom right"),
                ("bottom_left", "Bottom left"),
                ("top_right", "Top right"),
                ("top_left", "Top left"),
            ),
            tip="It moves clear of the navigation bar",
        ),
        SettingRow(
            "Editing",
            "New objects",
            "opt/esc_while_drawing",
            "Esc while drawing point by point:",
            kind="choice",
            choices=(("cancel", "Throws the shape away"), ("finish", "Keeps the shape")),
            tip="A polygon, a line of several points or a pipe. Enter or a double-click always keeps it",
        ),
        SettingRow(
            "Editing",
            "Moving and resizing",
            "view/slow_mouse_factor",
            "Slow mouse speed:",
            kind="choice",
            choices=(
                ("half", "50% of normal"),
                ("quarter", "25% of normal"),
                ("tenth", "10% of normal"),
            ),
            tip="Tools > Advanced > Mouse Slow Speed: how much slower a drag follows the pointer",
        ),
        SettingRow(
            "Editing",
            "Text",
            "opt/text_enter",
            "In a text, Enter:",
            kind="choice",
            choices=(("newline", "Starts a new line"), ("store", "Stores the text")),
            tip=(
                "Starts a new line: Ctrl+Enter stores the text, as in the Graphics Builder. Stores "
                "the text: Shift+Enter starts a new line"
            ),
        ),
        SettingRow(
            "Appearance",
            "Canvas",
            "view/guide_colour",
            "Guideline colour:",
            kind="colour",
            choices=(("default", "Light blue"), ("custom", "Custom")),
            tip="The guidelines and their marks on the rulers. Pick one that shows on your pages",
        ),
        # MainWindow.apply_editing_options reads them once a change;
        # the Safety rows set infra/backups and infra/history; Print pages in is read by
        # File > Print
        SettingRow(
            "Editing",
            "Tools",
            "opt/keep_tool",
            "Keep the tool after drawing or placing an object",
            apply="window:apply_editing_options",
            tip="Also a symbol or genie you place. Esc goes back to Select",
        ),
        SettingRow(
            "Editing",
            "Moving and resizing",
            "opt/arrows_pan_step",
            "Arrow keys scroll by:",
            kind="choice",
            choices=(
                (16, "A sixteenth of the view"),
                (8, "An eighth of the view"),
                (4, "A quarter of the view"),
            ),
            apply="window:apply_editing_options",
            tip="With nothing selected. Shift + arrow scrolls a whole view",
        ),
        SettingRow(
            "Editing",
            "Moving and resizing",
            "opt/big_nudge",
            "Large arrow key step:",
            kind="int",
            span=(2, 100),
            suffix=" steps",
            apply="window:apply_editing_options",
            tip="How far Ctrl + arrow and Shift + arrow move when they are set to move one large step",
        ),
        SettingRow(
            "Editing",
            "New objects",
            "opt/place_anchor",
            "A placed symbol or genie lands with:",
            kind="choice",
            choices=(("top_left", "Its top left at the pointer"), ("centre", "Its centre at the pointer")),
            apply="window:apply_editing_options",
            tip=(
                "Top left matches the Graphics Builder. For the Symbol tool, Paste Symbol, Paste "
                "Genie and cards dropped from the Library panel"
            ),
        ),
        SettingRow(
            "Editing",
            "Arrange",
            "opt/align_to",
            "Align to:",
            kind="choice",
            choices=(
                ("selection", "The selection box"),
                ("last", "The object selected last"),
                ("page", "The page"),
            ),
            apply="window:apply_editing_options",
            tip="The selection box is the Graphics Builder's. The object selected "
            "last stays where it is. The page aligns one object too",
        ),
        SettingRow(
            "Editing",
            "Safety",
            "opt/history_every_min",
            "New earlier version at most every:",
            kind="int",
            span=(0, 120),
            suffix=" minutes",
            special="Every save",
            tip="A save sooner after the newest version keeps none, so older "
            "versions stay. A save after Restore always keeps one",
        ),
        SettingRow(
            "Editing",
            "Safety",
            "opt/backup_keep",
            "Keep backups of batch changes:",
            kind="int",
            span=(3, 100),
            suffix=" backups",
            apply="window:apply_editing_options",
            tip="Update Pages, Pack Libraries, Replace All, Delete Page and "
            "Duplicate Pictures. They go to the backups folder, which Tools > "
            "Restore Backup lists and opens",
        ),
        SettingRow(
            "Editing",
            "Safety",
            "opt/backup_days",
            "Remove backups older than:",
            kind="int",
            span=(0, 365),
            suffix=" days",
            special="Never",
            apply="window:apply_editing_options",
            tip="The newest 3 always stay",
        ),
        SettingRow(
            "Canvas",
            "Copy and paste",
            "canvas/paste_at",
            "Paste puts the objects:",
            kind="choice",
            choices=(("offset", "Next to the copied objects"), ("pointer", "At the pointer")),
            apply="window:apply_editing_options",
            tip="At the pointer: their top left corner goes there, or where you "
            "right-clicked for Paste. Also a picture from another program",
        ),
        SettingRow(
            "Canvas",
            "Copy and paste",
            "opt/copy_sheets_max",
            "Pasted objects whose properties open, at most:",
            kind="int",
            span=(0, 100),
            suffix=" objects",
            special="No limit (as the Graphics Builder)",
            apply="window:apply_editing_options",
            tip="For one paste or duplicate. The status bar names the others",
        ),
        SettingRow(
            "Pages and libraries",
            "Starting and switching pages",
            "opt/mru_max",
            "Recent pages kept:",
            kind="int",
            span=(4, 50),
            suffix=" pages",
            apply="window:apply_editing_options",
            tip="File > Recent Pages, Quick Open and the command palette",
        ),
        SettingRow(
            "Pages and libraries",
            "Printing",
            "opt/print_orientation",
            "Print pages in:",
            kind="choice",
            choices=(
                ("landscape", "Landscape"),
                ("portrait", "Portrait"),
                ("page", "The page's shape"),
                ("last", "The last print's choice"),
            ),
            tip="How File > Print starts. The print window can still change it",
        ),
        # ui/prefs/window_options.py: read where used, or applied by its
        # settings_pages hooks
        SettingRow(
            "Appearance",
            "Document tabs",
            "ui/new_tab_place",
            "Open a new tab:",
            kind="choice",
            choices=(("end", "At the end"), ("next", "Next to the current tab")),
            tip="Where the tab of a page you open goes. Pinned tabs stay first",
        ),
        SettingRow(
            "Appearance",
            "Document tabs",
            "ui/after_close",
            "After closing a tab, show:",
            kind="choice",
            choices=(("next", "The tab next to it"), ("last", "The page used before it")),
            tip="When you close the tab shown. The page used before it is the page you had open just before",
        ),
        SettingRow(
            "Appearance",
            "Document tabs",
            "ui/ask_unpin",
            "Ask before closing a pinned tab",
            tip="Ctrl+W, the middle button or Close Page on a pinned tab. Off: it closes at once",
        ),
        SettingRow(
            "Appearance",
            "Document tabs",
            "ui/tab_width",
            "Tab width:",
            kind="choice",
            choices=(
                ("narrow", "Narrow (8 characters, 160 px)"),
                ("normal", "Normal (12 characters, 240 px)"),
                ("wide", "Wide (16 characters, 360 px)"),
            ),
            tip="How wide a tab can get. Narrow fits more tabs before the bar scrolls",
        ),
        SettingRow(
            "Appearance",
            "Status bar",
            "opt/an_warn",
            "Warn about pages with more animation points than:",
            kind="int",
            span=(500, 20000),
            step=500,
            tip="The count in the status bar turns amber, with advice. The Graphics "
            "Builder help suggests fewer than 3000 a page",
        ),
        SettingRow(
            "Ribbon",
            "Contextual tabs",
            "ui/context_tabs_open",
            "Selecting a text, shape or genie opens its tab",
            tip=(
                "On the canvas, while the Design tab is shown. Off (the default): its tab is "
                "only tinted, and the drawing tools stay"
            ),
        ),
        SettingRow(
            "Ribbon",
            "Quick Access",
            "ui/qat",
            "Quick Access commands:",
            kind="list",
            tip="The small buttons left of the ribbon's tabs. Save keeps its arrow "
            "with Save As, Save All and the recent pages",
        ),
        SettingRow(
            "Ribbon",
            "Right-click menu",
            "ui/menu_quick_row",
            "Quick row of the right-click menu:",
            kind="list",
            tip="The icons at the top of the menu on a selection. Leave it empty for no row",
        ),
        SettingRow(
            "Ribbon",
            "Selection toolbar",
            "ui/hud_buttons",
            "Selection toolbar commands:",
            kind="list",
            tip="The bar next to the selection (View > Selection Toolbar). Properties "
            "becomes Genie Parameters on a genie",
        ),
        SettingRow(
            "Ribbon",
            "Search",
            "ui/palette_keep_search",
            "The command palette opens with the last search",
            tip="Ctrl+K. The text is selected, so typing replaces it",
        ),
        SettingRow(
            "Ribbon",
            "Search",
            "ui/quick_open_scope",
            "Quick Open searches first:",
            kind="choice",
            choices=(("project", "This project"), ("install", "All projects"), ("last", "The one used last")),
            tip="Tab switches between this project and all projects while Quick Open is shown",
        ),
        SettingRow(
            "Pages and libraries",
            "New pages",
            "opt/new_page_name",
            "Name new pages:",
            kind="text",
            special="NewPage",
            tip=(
                "For File > New, a page from the last template and Quick Open. A name already "
                "taken gets _2, _3 and so on"
            ),
        ),
        SettingRow(
            "Pages and libraries",
            "Run",
            "opt/compile_results",
            "After Compile, show the results:",
            kind="choice",
            choices=(
                ("always", "Always"),
                ("problems", "With errors or warnings"),
                ("errors", "With errors only"),
            ),
            tip="File > Compile Project. The status bar always says how it went, "
            "with Show Results. Run shows them only when the compile fails",
        ),
        SettingRow(
            "Pages and libraries",
            "Starting and switching pages",
            "opt/single_instance",
            "Open pages in the ctgedit already running",
            tip="A page double-clicked in Explorer, or ctgedit started again. Off: "
            "each start opens a window of its own",
        ),
        SettingRow(
            "Pages and libraries",
            "Starting and switching pages",
            "opt/explorer_session",
            "A page opened from Explorer also reopens the last pages",
            tip="When ctgedit starts with a page, and On start is Reopen the last pages",
        ),
        # ui/panels/panel_options.py applies the ones without a window
        # method: its hooks run when one changes
        SettingRow(
            "Editing",
            "Properties panel",
            "opt/scrub_speed",
            "Label drag speed:",
            kind="choice",
            choices=_PO.SCRUB_SPEEDS,
            sub=True,
            tip="How fast a number changes as you drag its label. Shift is 10 times as fast, Alt a quarter",
        ),
        SettingRow(
            "Editing",
            "Expressions",
            "opt/tag_suggest",
            "Suggest names after:",
            kind="choice",
            choices=_PO.TAG_SUGGEST,
            tip="The letters you type in an expression field before it lists the "
            "tags, variables, equipment and functions that match",
        ),
        SettingRow(
            "Editing",
            "Objects",
            "objprops/open_tab",
            "Object Properties opens on:",
            kind="choice",
            choices=_PO.OPEN_TABS,
            tip="The tab used last: the tab you left open in the last properties of "
            "that kind of object, such as a text",
        ),
        SettingRow(
            "Editing",
            "Styles",
            "draw/style_preview",
            "Preview a style on the page when you point at it",
            tip="The style tiles of the ribbon and the Properties panel, and the "
            "Styles menu. Off: the page changes when you click a style",
        ),
        SettingRow(
            "Appearance",
            "Objects panel",
            "objects/rename_click",
            "A second click on a selected row renames it",
            apply="window:_objects_panel_settings",
            tip="Click the name, then wait a moment. F2 always renames",
        ),
        SettingRow(
            "Appearance",
            "Objects panel",
            "objects/double_click",
            "Double-click a row:",
            kind="choice",
            choices=_PO.OBJECTS_DOUBLE_CLICK,
            apply="window:_objects_panel_settings",
            tip="Opens its properties: as Enter does, a genie's parameters, else its "
            "properties. Scrolls to it: the zoom stays",
        ),
        SettingRow(
            "Appearance",
            "Objects panel",
            "objects/follow_selection",
            "Show the objects selected on the page",
            apply="window:_objects_panel_settings",
            tip="The panel scrolls to them and opens the groups they are in. Off: it stays as you left it",
        ),
        SettingRow(
            "Appearance",
            "Objects panel",
            "objects/keep_filter",
            "Keep the panel's filter when ctgedit starts again",
            tip="Off: the panel starts with every object listed",
        ),
        SettingRow(
            "Pages and libraries",
            "Library panel",
            "library/hover_preview",
            "Preview of a card you point at:",
            kind="choice",
            choices=_PO.LIBRARY_PREVIEWS,
            tip="A larger picture of the element beside the panel, with its size. "
            "Large is half as big again. Off: a tool tip with its name",
        ),
        SettingRow(
            "Pages and libraries",
            "Searching",
            "browser/fuzzy",
            "Also find names with the letters in order",
            tip="In the Library panel and the page browser: 'mtr' finds 'motor'. Closer matches come first",
        ),
        # Tools > Update Pages (settings.update_parts) and the waits of a save next to
        # another ctgedit process (settings.lock_waits, format/projectlock)
        SettingRow(
            "Pages and libraries",
            "Update Pages",
            "opt/update_pages_per_helper",
            "Pages each helper gets at least:",
            kind="int",
            span=(1, 1000),
            suffix=" pages",
            tip="A run with fewer pages starts fewer helpers. Lower it to share a small run between helpers",
        ),
        # the figures of the long runs while they go (ui/jobs/run_stats, settings.run_figures)
        SettingRow(
            "Pages and libraries",
            "Progress figures",
            "opt/run_figures",
            "Show the time so far, the time left and the counts",
            tip=(
                "In the progress windows of long runs and in the status bar. The summary says how "
                "long the run took"
            ),
        ),
        SettingRow(
            "Pages and libraries",
            "Progress figures",
            "opt/run_estimate_items",
            "Estimate the time left after:",
            kind="int",
            span=(1, 1000),
            suffix=" items",
            sub=True,
            tip="Pages or genies done. With several helper processes, at least one for each helper",
        ),
        SettingRow(
            "Pages and libraries",
            "Progress figures",
            "opt/run_estimate_seconds",
            "And after:",
            kind="int",
            span=(0, 600),
            suffix=" s",
            sub=True,
            tip="Until both are reached, the window shows no estimate",
        ),
        SettingRow(
            "Pages and libraries",
            "Progress figures",
            "opt/run_estimate_smoothing",
            "Smooth the estimate over:",
            kind="int",
            span=(0, 600),
            suffix=" s",
            sub=True,
            tip="Longer: the estimate changes more slowly when the pages take "
            "different times. 0: it follows the rate at once",
        ),
        SettingRow(
            "Pages and libraries",
            "Busy projects",
            "opt/wait_project",
            "Wait for a project database in use:",
            kind="int",
            span=(1, 600),
            suffix=" s",
            apply="window:apply_lock_waits",
            tip="When another ctgedit process is writing the project's database, a "
            "save here waits this long, then says the project is busy",
        ),
        SettingRow(
            "Pages and libraries",
            "Busy projects",
            "opt/wait_page",
            "Wait for a page another process is saving:",
            kind="int",
            span=(1, 600),
            suffix=" s",
            apply="window:apply_lock_waits",
            tip="When an Update Pages helper is saving the same page, a save here "
            "waits this long, then says the page is busy",
        ),
        SettingRow(
            "Pages and libraries",
            "Busy projects",
            "opt/wait_helpers",
            "Helper processes wait:",
            kind="int",
            span=(1, 600),
            suffix=" s",
            apply="window:apply_lock_waits",
            tip="How long a helper of Update Pages or Find waits for a busy project "
            "or page before that page fails",
        ),
        SettingRow(
            "Pages and libraries",
            "Open and Save As",
            "browser/short_names",
            "Shorten a name start most pages share",
            tip="The cards show '…Pump_01'. The full name is in the tool tip and the preview",
        ),
        SettingRow(
            "Pages and libraries",
            "Open and Save As",
            "browser/preview_pane",
            "Preview pane:",
            kind="choice",
            choices=_PO.PREVIEW_PANES,
            tip="File > Open, Save As and the pickers. When the window is wide: from 1150 px",
        ),
        # ui/editor/view_consistency.apply_consistency_settings applies them
        SettingRow(
            "Canvas",
            "Zoom and scroll",
            "view/fit_margin",
            "Fit Page keeps around the page:",
            kind="int",
            span=(0, 400),
            suffix=" px",
            tip="Page pixels around the page when Fit Page fits it, on the canvas and "
            "in the Simulate window alike",
        ),
        SettingRow(
            "Canvas",
            "Navigation",
            "opt/corner_double_click",
            "Double-click the navigator or the ruler corner:",
            kind="choice",
            choices=(("fit_page", "Fit Page"), ("fit_all", "Fit All Objects"), ("nothing", "Nothing")),
            tip="A double-click on the navigator's map or on the square where the rulers meet",
        ),
        SettingRow(
            "Keyboard",
            "Other windows",
            "opt/bare_zoom_keys",
            "Plus and minus zoom in the Bitmap Editor and the magnifier",
            tip="The Graphics Builder's keys. Off: only Ctrl+= and Ctrl+- zoom there, "
            "as on the canvas, where - deletes the selection",
        ),
        SettingRow(
            "Keyboard",
            "Other windows",
            "help/zoom_reset_key",
            "Text size 100 % in the manual:",
            kind="choice",
            choices=(("ctrl0", "Ctrl+0 (as in a browser)"), ("ctrl1", "Ctrl+1 (as View > Zoom 100%)")),
            tip="Ctrl+= and Ctrl+- make the manual's text larger and smaller",
        ),
        SettingRow(
            "Pages and libraries",
            "Library panel",
            "opt/card_wheel_step",
            "Ctrl + wheel sizes the cards by:",
            kind="int",
            span=(2, 40),
            suffix=" px",
            tip=(
                "How far one wheel notch scrolls the cards in the Library panel, Open, Save As and "
                "the pickers. Alt: a quarter of it"
            ),
        ),
        SettingRow(
            "Pages and libraries",
            "Open and Save As",
            "browser/preview_live",
            "The preview shows:",
            kind="choice",
            choices=_PO.PREVIEW_SHOWS,
            tip=(
                "The saved picture can be out of date: Space or Live Render draws the page now. A "
                "fresh drawing draws each page you select"
            ),
        ),
        # how long an automatic update waits for
        # the steps to redo it would clear (ui/window/open_genie_updates.redo_wait_s, read at each look)
        SettingRow(
            "Pages and libraries",
            "When a page opens",
            "opt/auto_update_redo_wait_s",
            "An update waits for your steps to redo:",
            kind="int",
            span=(0, 3600),
            suffix=" s",
            step=10,
            tip=(
                "An automatic update of an open page or genie clears the steps you could redo. It "
                "waits this long for you to redo them first. 0: no wait"
            ),
        ),
        SettingRow(
            "Pages and libraries",
            "When a page opens",
            "opt/tabs_wait_for_runs",
            "Open tabs wait for the update of what uses a saved genie",
            tip=(
                "Off: an open page or genie takes a saved genie, symbol or template at once. On: "
                "it waits until the background update of the closed pages and library genies has "
                "finished. A Simulate page that follows the tab does the same"
            ),
        ),
        SettingRow(
            "Pages and libraries",
            "When a page opens",
            "opt/genie_jobs_order",
            "After a genie save, update the open tabs:",
            kind="choice",
            choices=(("near", "Nearest the genie first, smaller ones first"), ("tabs", "In tab order")),
            tip=(
                "The tab you look at goes first, then the tabs you show. Nearest first: then the "
                "genies that hold the saved genie, then the genies that hold those, then the "
                "pages, smallest first. In tab order: left to right"
            ),
        ),
        SettingRow(
            "Pages and libraries",
            "When a page opens",
            "opt/genie_helpers",
            "Update this many open tabs at a time:",
            kind="int",
            span=(1, 8),
            tip=(
                "Each tab gets its own helper, so tabs do not wait for each other. A helper uses "
                "about as much memory as an open page, and none starts while free memory is below "
                "the Long jobs limit. 1: one tab at a time"
            ),
        ),
        SettingRow(
            "Pages and libraries",
            "Files and folders",
            "opt/library_watch",
            "Follow libraries changed by another program",
            apply="window:_apply_library_watch",
            tip=(
                "A symbol, genie or template saved by the Graphics Builder or another ctgedit is "
                "redrawn on the open pages, and the Library panel refreshes"
            ),
        ),
        SettingRow(
            "Pages and libraries",
            "Files and folders",
            "opt/library_watch_delay_ms",
            "Wait after a library change:",
            kind="int",
            span=(100, 10000),
            suffix=" ms",
            step=100,
            sub=True,
            apply="window:_apply_library_watch",
            tip="A save writes a library in several steps: the pages are drawn "
            "again this long after the last one",
        ),
        # the timings of the long jobs (ui/prefs/job_options, applied live through
        # MainWindow.apply_job_options) and Find's pages per helper
        SettingRow(
            "Pages and libraries",
            "Find in closed pages",
            "opt/find_pages_per_helper",
            "Pages each helper gets at least:",
            kind="int",
            span=(1, 1000),
            suffix=" pages",
            apply="window:apply_job_options",
            tip="A search over fewer pages starts fewer helpers. Lower it to share a "
            "small project between helpers",
        ),
        SettingRow(
            "Pages and libraries",
            "Long jobs",
            "opt/task_report_s",
            "Say how a task ended after it ran for:",
            kind="int",
            span=(0, 600),
            suffix=" s",
            apply="window:apply_job_options",
            tip="A background task that ran longer than this says how it ended in "
            "the window's messages (the bell). 0: every task",
        ),
        SettingRow(
            "Pages and libraries",
            "Long jobs",
            "opt/job_poll_ms",
            "Read a helper's progress every:",
            kind="int",
            span=(50, 5000),
            suffix=" ms",
            step=50,
            apply="window:apply_job_options",
            tip=(
                "How often the progress windows and the status bar check on long runs and background updates"
            ),
        ),
        SettingRow(
            "Pages and libraries",
            "Long jobs",
            "opt/worker_idle_s",
            "Stop the idle genie helper after:",
            kind="int",
            span=(10, 3600),
            suffix=" s",
            step=10,
            apply="window:apply_job_options",
            tip="The helper process that updates genies stays between requests, so "
            "the next update is faster. Idle this long, it ends and its memory "
            "is freed",
        ),
        SettingRow(
            "Pages and libraries",
            "Long jobs",
            "opt/worker_low_memory_mb",
            "Stop it sooner when free memory is below:",
            kind="int",
            span=(0, 65536),
            suffix=" MB",
            step=100,
            sub=True,
            apply="window:apply_job_options",
            tip="After 20 s idle. 0: never for memory",
        ),
        SettingRow(
            "Pages and libraries",
            "Long jobs",
            "opt/preview_timeout_s",
            "Give up a live preview after:",
            kind="int",
            span=(10, 3600),
            suffix=" s",
            step=10,
            apply="window:apply_job_options",
            tip="File > Open's live preview of a page is drawn in a helper process; "
            "one that takes longer is stopped and the pane says so",
        ),
        SettingRow(
            "Pages and libraries",
            "Long jobs",
            "opt/model_wait_s",
            "Wait for the simulator's page model:",
            kind="int",
            span=(10, 3600),
            suffix=" s",
            step=10,
            apply="window:apply_job_options",
            tip="Simulate Page makes the page's model in a helper process; after "
            "this the page runs from its compiled files instead",
        ),
        SettingRow(
            "Pages and libraries",
            "Long jobs",
            "opt/busy_note_ms",
            "Show the busy note after:",
            kind="int",
            span=(0, 10000),
            suffix=" ms",
            step=100,
            apply="window:apply_job_options",
            tip="A step that holds the window (Edit Definition, a genie dialog, "
            "composing a composite genie) shows a note over the page when it "
            "takes longer than this",
        ),
        SettingRow(
            "Pages and libraries",
            "Long jobs",
            "opt/users_read_window_ms",
            "Show the search for what uses an element after:",
            kind="int",
            span=(0, 60000),
            suffix=" ms",
            step=500,
            apply="window:apply_job_options",
            tip=(
                "After you save a genie, symbol or template, and for Show Users, Used By and "
                "Update Users, ctgedit finds what uses it. Its progress window shows only when "
                "that takes longer than this"
            ),
        ),
        SettingRow(
            "Pages and libraries",
            "Long jobs",
            "opt/save_helper_records",
            "Save an element in a helper from:",
            kind="int",
            span=(0, 1000000),
            suffix=" objects",
            step=500,
            apply="window:apply_job_options",
            tip=(
                "Saving a large library element composes its nested genies again, which takes "
                "seconds. From this size a helper process does it, so the window stays responsive. "
                "0: never"
            ),
        ),
        SettingRow(
            "Pages and libraries",
            "Long jobs",
            "opt/save_window_ms",
            "Show the save of a large element after:",
            kind="int",
            span=(0, 60000),
            suffix=" ms",
            step=500,
            sub=True,
            apply="window:apply_job_options",
            tip="Its progress window shows only when the save takes longer than "
            "this. Until then the status bar shows it",
        ),
        SettingRow(
            "Pages and libraries",
            "Long jobs",
            "opt/users_note_s",
            "Keep the note of a finished genie update for:",
            kind="int",
            span=(0, 3600),
            suffix=" s",
            step=10,
            apply="window:apply_job_options",
            tip=(
                "How long the result of the update after a genie save stays in the status bar, as "
                "a multiple of Appearance > Messages. 0: until you close it. It also stays in the "
                "messages"
            ),
        ),
        # the pauses and counts (ui/prefs/timing_options reads them; the
        # panels' tabs and titles: ui/panels/docking.apply_settings)
        SettingRow(
            "Editing",
            "Expressions",
            "opt/expression_check_ms",
            "Check expressions after a pause of:",
            kind="int",
            span=(200, 3000),
            suffix=" ms",
            step=50,
            tip="How long after the last letter the check runs",
        ),
        SettingRow(
            "Editing",
            "Properties panel",
            "opt/props_step_pause_ms",
            "Apply the steps after a pause of:",
            kind="int",
            span=(100, 2000),
            suffix=" ms",
            step=50,
            sub=True,
            tip="A run of arrow steps within the pause is one undo step. A big page "
            "draws again at each apply",
        ),
        SettingRow(
            "Editing",
            "Safety",
            "opt/autosave_idle_s",
            "Also after a pause in editing of:",
            kind="int",
            span=(0, 600),
            suffix=" s",
            special="Never",
            sub=True,
            tip="A recovery copy is also written when you stop editing for this "
            "long. Never: only by the minutes above",
        ),
        SettingRow(
            "Canvas",
            "Undo and Redo",
            "view/undo_flash_ms",
            "Flash the changed objects for:",
            kind="int",
            span=(200, 3000),
            suffix=" ms",
            step=100,
            sub=True,
        ),
        SettingRow(
            "Drawing",
            "Embedded controls",
            "view/control_pictures",
            "Draw embedded controls as they look",
            tip="ActiveX, Process Analyst and the other controls run once in a separate program to "
            "take their picture. Off, they show as boxes",
        ),
        SettingRow(
            "Canvas",
            "Opening a page",
            "view/last_views_kept",
            "Remember the view of at most:",
            kind="int",
            span=(50, 5000),
            suffix=" pages",
            step=50,
            tip="For Where I left it. The pages left longest ago are forgotten first and open fitted",
        ),
        SettingRow(
            "Canvas",
            "Underlay",
            "opt/underlays_kept",
            "Remember the underlay of at most:",
            kind="int",
            span=(50, 5000),
            suffix=" pages",
            step=50,
            sub=True,
            tip="The pages left longest ago are forgotten first",
        ),
        SettingRow(
            "Canvas",
            "Zoom and scroll",
            "view/saved_views_max",
            "Saved views a page keeps:",
            kind="int",
            span=(10, 500),
            suffix=" views",
            tip="View > Saved Views. Past it the last ones are not kept",
        ),
        SettingRow(
            "Canvas",
            "Navigation",
            "ui/navbar_fade_px",
            "Fade the navigation bar when the pointer is within:",
            kind="int",
            span=(0, 200),
            suffix=" px",
            step=10,
            tip="So the page under it can be seen. 0 px: only while dragging",
        ),
        SettingRow(
            "Canvas",
            "Navigation",
            "ui/navbar_faded_pct",
            "Faded to:",
            kind="int",
            span=(10, 90),
            suffix=" %",
            step=5,
            sub=True,
            tip="How much of the bar still shows while it is faded",
        ),
        SettingRow(
            "Appearance",
            "Objects panel",
            "objects/rename_click_ms",
            "Rename after:",
            kind="int",
            span=(300, 2000),
            suffix=" ms",
            step=50,
            sub=True,
            apply="window:_objects_panel_settings",
            tip="How long after the click the name opens for editing. Shorter than "
            "Windows' double-click time renames on a double-click",
        ),
        SettingRow(
            "Appearance",
            "Panels",
            "ui/dock_tab_position",
            "Panel tabs:",
            kind="choice",
            choices=(("top", "Above the panels"), ("bottom", "Below the panels")),
            tip="Where the tabs of panels that share a side sit (Objects, Library "
            "and Tags). Also the Simulate window's panels",
        ),
        SettingRow(
            "Appearance",
            "Panels",
            "ui/compact_dock_titles",
            "Compact panel titles",
            tip="Off: taller title bars with larger buttons. Also the Simulate window's panels",
        ),
        SettingRow(
            "Ribbon",
            "Selection toolbar",
            "ui/hud_delay_ms",
            "Show it again after the view settles for:",
            kind="int",
            span=(0, 2000),
            suffix=" ms",
            step=50,
            tip="After a scroll, a zoom or an edit. 0 ms: at once",
        ),
        SettingRow(
            "Ribbon",
            "Search",
            "ui/quick_open_rows",
            "Quick Open shows at most:",
            kind="int",
            span=(10, 500),
            suffix=" rows",
            step=10,
            tip="Ctrl+P. The tags and objects it searches grow with it",
        ),
        SettingRow(
            "Ribbon",
            "Search",
            "ui/palette_rows",
            "The command palette lists at most:",
            kind="int",
            span=(10, 500),
            suffix=" commands",
            step=10,
            tip="Ctrl+K, for one search",
        ),
        SettingRow(
            "Pages and libraries",
            "Library panel",
            "library/recent_kept",
            "Recent elements kept:",
            kind="int",
            span=(10, 500),
            suffix=" elements",
            step=10,
            tip="The panel's Recent section, newest first",
        ),
        SettingRow(
            "Advanced",
            "The settings window",
            "ui/settings_live_delay_ms",
            "Apply after a pause of:",
            kind="int",
            span=(100, 2000),
            suffix=" ms",
            step=50,
            sub=True,
            tip="How long after the last change it applies. A drawing choice draws "
            "every open page again at each apply",
        ),
        # Appearance > Tables (ui/tables.SETTINGS) and Tags panel
        # (ui/panels/tags_dock.COLUMN_SPANS); applied live by their apply hooks
        SettingRow(
            "Appearance",
            "Tables",
            "tables/max_fit_width",
            "A column fits to at most:",
            kind="int",
            span=(100, 2000),
            suffix=" px",
            tip="The first fit, Fit Columns to Contents and a double-click on a "
            "divider never make a column wider. Drag it for more",
        ),
        SettingRow(
            "Appearance",
            "Tables",
            "tables/fit_share",
            "In a panel, a column fits to at most:",
            kind="int",
            span=(10, 90),
            suffix=" %",
            tip="Of the panel's width. The Objects panel and the trees of Update "
            "Pages and the like fit their columns into the width they have",
        ),
        SettingRow(
            "Appearance",
            "Tables",
            "tables/main_share",
            "In a panel, the main column keeps at least:",
            kind="int",
            span=(10, 80),
            suffix=" %",
            tip="Of the panel's width. The other columns give way to it",
        ),
        SettingRow(
            "Appearance",
            "Tables",
            "tables/min_main_width",
            "The main column is never narrower than:",
            kind="int",
            span=(20, 400),
            suffix=" px",
            tip="When a window or panel gets narrower",
        ),
        SettingRow(
            "Appearance",
            "Tables",
            "tables/min_squeezed_width",
            "A column that gives way is never narrower than:",
            kind="int",
            span=(16, 200),
            suffix=" px",
            tip="In a panel that fits its columns into its width",
        ),
        SettingRow(
            "Appearance",
            "Tables",
            "tables/fit_sample",
            "A fit measures at most:",
            kind="int",
            span=(100, 100000),
            suffix=" rows",
            tip="Of a long list. More rows take longer",
        ),
        SettingRow(
            "Appearance",
            "Tags panel",
            "tags/type_max_width",
            "Type column at most:",
            kind="int",
            span=(40, 400),
            suffix=" px",
            tip="The first widths when the panel opens. Drag a divider or use Fit "
            "Columns to Contents on the column titles for other widths",
        ),
        SettingRow(
            "Appearance",
            "Tags panel",
            "tags/name_share",
            "Tag column:",
            kind="int",
            span=(10, 80),
            suffix=" %",
            tip=(
                "Share of the width left after Type. Equipment takes its share and Project the "
                "rest; scroll to see Comment"
            ),
        ),
        SettingRow(
            "Appearance",
            "Tags panel",
            "tags/equipment_share",
            "Equipment column:",
            kind="int",
            span=(10, 80),
            suffix=" %",
            tip="Of the width left by Type",
        ),
        SettingRow(
            "Appearance",
            "Tags panel",
            "tags/name_min_width",
            "Tag column at least:",
            kind="int",
            span=(24, 400),
            suffix=" px",
            tip="In a narrow panel",
        ),
        SettingRow(
            "Appearance",
            "Tags panel",
            "tags/min_column_width",
            "Equipment and Project at least:",
            kind="int",
            span=(24, 400),
            suffix=" px",
            tip="In a narrow panel",
        ),
    ]

    def __init__(self, window, page: str = ""):
        super().__init__(window)
        self._w = window
        self._building = True
        self.setWindowTitle("Settings - ctgedit")
        st = app_settings()
        self._live = bool(st.value("ui/settings_live", True, type=bool))
        self._flags = {}
        for key, label in self.FLAGS:
            cb = QtWidgets.QCheckBox(label)
            cb.setChecked(st.value("opt/" + key, OPTION_DEFAULTS.get(key, False), type=bool))
            if key in self.TIPS:
                cb.setToolTip(self.TIPS[key])
            self._flags[key] = cb
        self._make_editing_controls(st)
        self._make_canvas_controls()
        from ..editor.canvas_options import CanvasNavControls

        self._nav = CanvasNavControls(self)  # (the canvas's navigation choices)
        self._make_appearance_controls()
        self._make_ribbon_controls(st)
        self._make_library_controls()
        # (the document tabs, the start, the opening zoom: doc_tabs.TabSettingsRows)
        from ..panels.doc_tabs import TabSettingsRows

        self._tab_rows = TabSettingsRows()
        self._make_keyboard_controls()
        self._make_row_controls()
        from ..simulate.settings_dialog import SimSettingsControls
        from ..simulate.window import engine_setting

        self._sim = SimSettingsControls(self, engine_path=engine_setting())
        self._sim_engine_before = engine_setting()

        from .settings_pages import SettingsPages

        #: the page list and the pages (it answers as a tab widget: F1, the tests)
        self._tabs = SettingsPages(self)
        self._lay_out_pages()

        dev = self._flags["developer_tools"]
        badges = self._flags["dyn_badges"]
        badges.setEnabled(dev.isChecked())
        dev.toggled.connect(badges.setEnabled)
        # a row that only acts with the one above it ticked
        self._enable_with("opt/arrows_pan_step", self._row_widgets.get("opt/arrows_pan"))
        self._enable_with("opt/copy_sheets_max", self._flags.get("props_on_copy"))

        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(self._tabs, 1)
        row = QtWidgets.QHBoxLayout()
        # (every page's settings, not the page or the search shown: named so, and asked)
        self._btn_defaults = QtWidgets.QPushButton("Reset All Settings…")
        self._btn_defaults.setAutoDefault(False)
        self._btn_defaults.setToolTip(
            "Put every setting on every page back to its default. Fast display files are left as they are"
        )
        self._btn_defaults.clicked.connect(self._ask_reset_all)
        # (after a reset: the values from just before it, one click away)
        self._btn_undo_reset = QtWidgets.QPushButton("Undo Reset")
        self._btn_undo_reset.setAutoDefault(False)
        self._btn_undo_reset.setToolTip("Put back the settings from just before Reset All Settings")
        self._btn_undo_reset.setVisible(False)
        self._btn_undo_reset.clicked.connect(self.undo_reset)
        self._before_reset = None
        self._after_reset = None
        self._btn_export = QtWidgets.QPushButton("Export…")
        self._btn_export.setAutoDefault(False)
        self._btn_export.setToolTip("Save settings to a file: choose which")
        self._btn_export.clicked.connect(self._export)
        self._btn_import = QtWidgets.QPushButton("Import…")
        self._btn_import.setAutoDefault(False)
        self._btn_import.setToolTip("Load settings saved with Export: choose which")
        self._btn_import.clicked.connect(self._import)
        for b in (self._btn_defaults, self._btn_undo_reset, self._btn_export, self._btn_import):
            row.addWidget(b)
        row.addStretch(1)
        B = QtWidgets.QDialogButtonBox
        if self._live:
            bb = B(B.Close)
            # one change back at a time (Revert to Opening Values puts back every change of
            # the session)
            self._btn_undo_last = bb.addButton("Undo Last Change", B.ActionRole)
            self._btn_undo_last.setToolTip("Put back the setting changed last, as it was before")
            self._btn_undo_last.setEnabled(False)
            self._btn_undo_last.clicked.connect(self.undo_last_change)
            self._btn_undo = bb.addButton("Revert to Opening Values", B.ResetRole)
            self._btn_undo.setToolTip("Put back the settings this window opened with")
            self._btn_undo.setEnabled(False)
            self._btn_undo.clicked.connect(self.undo_changes)
            bb.button(B.Close).clicked.connect(self.close_live)
            self._btn_apply = None
        else:
            bb = B(B.Ok | B.Cancel | B.Apply)
            bb.accepted.connect(self._apply)
            bb.rejected.connect(self.reject)
            self._btn_apply = bb.button(B.Apply)
            self._btn_apply.clicked.connect(lambda: self._apply(accept=False))
            self._btn_undo = None
            self._btn_undo_last = None
        self._buttons = bb
        row.addWidget(bb)
        lay.addLayout(row)
        self.resize(900, 640)
        self._first_page = page or ""
        if page:
            self._tabs.show_page(page)
        # Ctrl+F: the search
        sc = QtGui.QShortcut(QtGui.QKeySequence.Find, self)
        sc.activated.connect(lambda: (self._tabs.search.setFocus(), self._tabs.search.selectAll()))
        # live apply: one pause after the last change (Advanced > Apply after a pause of)
        self._live_timer = QtCore.QTimer(self)
        self._live_timer.setSingleShot(True)
        self._live_timer.setInterval(live_delay_ms())
        self._live_timer.timeout.connect(self._live_apply)
        self._enable_with("ui/settings_live_delay_ms", self._row_widgets.get("ui/settings_live"))
        self._armed = False
        self._bind_rows()
        self._opening = self._state()
        #: the states applied in this window, the opening one first (Undo Last Change)
        self._applied = [self._opening]
        self._building = False
        self._update_marks()

    # -- the pages ----------------------------------------------------------------------
    def _lay_out_pages(self) -> None:
        """The controls on their pages, in headed sections."""
        self._lay_out_editing()
        self._lay_out_canvas()
        self._lay_out_drawing()
        self._lay_out_appearance()
        self._lay_out_keyboard()
        self._lay_out_ribbon()
        self._lay_out_libraries()
        self._lay_out_simulate()
        self._lay_out_advanced()

    def _gb_name(self, key: str) -> str:
        """The Graphics Builder's name of option ``key`` for the search (a search for it
        finds the option), or ''."""
        name = self.GB_NAMES.get(key)
        return "Graphics Builder: %s" % name if name else ""

    def _flag_row(self, s, key: str, extra: str = "", sub: bool = False):
        """Option ``key``'s check box on section ``s`` (``sub``: indented under the one
        above it)."""
        return (s.sub if sub else s.check)(self._flags[key], extra, key="opt/" + key)

    def _lay_out_editing(self) -> None:
        """The Editing page."""
        f = self._flags
        p = self._tabs.add_page("Editing")
        s = p.section("Tools")
        self._flag_row(s, "show_legacy_tools", self._gb_name("show_legacy_tools"))
        self._add_rows(p, "Editing", "Tools", section=s)
        s.note("The keys of the tools and of every command are on the Keyboard page.")
        s = p.section("Text")
        self._flag_row(s, "dblclick_edits_text")
        self._flag_row(s, "stretch_texts")
        self._add_rows(p, "Editing", "Text", section=s)
        s = p.section("Expressions")
        self._flag_row(s, "colour_cicode")
        self._flag_row(s, "function_hints")
        self._add_rows(p, "Editing", "Expressions", section=s)
        s = p.section("New objects")
        s.row("Colours of new objects:", self._new_colours, key="draw/colour_mode")
        self._add_rows(p, "Editing", "New objects", section=s)
        self._flag_row(s, "props_on_new", self._gb_name("props_on_new"))
        self._flag_row(s, "xp_buttons", self._gb_name("xp_buttons"))
        self._add_rows(p, "Editing", "Styles", section=s)
        s = p.section("Objects")
        self._flag_row(s, "modify_an", self._gb_name("modify_an"))
        self._add_rows(p, "Editing", "Objects", section=s)
        s = p.section("Genie forms")
        self._flag_row(s, "disable_genie_forms", self._gb_name("disable_genie_forms"))
        self._flag_row(s, "genie_group_button", self._gb_name("genie_group_button"))
        s = p.section("Arrange")
        self._flag_row(s, "turn_gradients")
        self._flag_row(s, "reversible_turns")
        self._add_rows(p, "Editing", "Arrange", section=s)
        s = p.section("Safety")
        self._flag_row(s, "confirm_delete")
        s.row("Autosave recovery copies every:", self._autosave, key="opt/autosave_minutes")
        # (the pause in editing that also writes a copy)
        self._add_rows(p, "Editing", "Safety", section=s, keys=("opt/autosave_idle_s",))
        s.row("Undo memory:", self._undo_mb, key="opt/undo_mb")
        # (the earlier versions' rows over their disk use and Clear...; the backups of
        # batch changes under them)
        self._add_rows(
            p,
            "Editing",
            "Safety",
            section=s,
            keys=("opt/history_versions", "opt/history_mb", "opt/history_every_min"),
        )
        s.row("Earlier versions now use:", self._history_use_row())
        self._add_rows(p, "Editing", "Safety", section=s)
        s = p.section("Properties panel")
        self._flag_row(s, "props_arrow_live")
        # (the pause under the switch it needs)
        self._add_rows(p, "Editing", "Properties panel", section=s, keys=("opt/props_step_pause_ms",))
        self._enable_with("opt/props_step_pause_ms", f["props_arrow_live"])
        self._flag_row(s, "props_scrub")
        # (the drag speed under the switch it needs)
        self._add_rows(p, "Editing", "Properties panel", section=s, keys=("opt/scrub_speed",))
        speed = self._row_widgets.get("opt/scrub_speed")
        if speed is not None:
            speed.setEnabled(f["props_scrub"].isChecked())
            f["props_scrub"].toggled.connect(speed.setEnabled)
        self._add_rows(p, "Editing", "Properties panel", section=s)
        # (last: the fine adjustments; the page opens on what tools, text and new objects do)
        s = p.section("Moving and resizing")
        s.row("Arrow-key move step:", self._nudge, key="view/nudge_step")
        s.row("Shift + arrow keys:", self._shift_arrow, key="opt/shift_arrow")
        s.check(self._sg_outline, key="view/smart_guide_outline")
        s.row("Join arrow-key presses within:", self._nudge_burst, key="opt/nudge_burst_ms")
        s.row("Smart-guide snap radius:", self._tol, key="view/smart_guide_tol")
        self._add_rows(p, "Editing", "Moving and resizing", section=s)

    def _lay_out_canvas(self) -> None:
        """The Canvas page."""
        p = self._tabs.add_page("Canvas")
        s = p.section("Mouse")
        s.row("Mouse wheel:", self._wheel, key="opt/wheel_scrolls")
        self._nav.lay_out_mouse(s)  # (the touchpad, right under the wheel)
        self._add_rows(p, "Canvas", "Mouse", section=s)
        self._nav.lay_out(
            p, rows=lambda sec, sub=None: self._add_rows(p, "Canvas", sec.title, section=sec, sub=sub)
        )
        edge = self._row_widgets.get("opt/edge_scroll_speed")
        if edge is not None:
            # (the speed of the scroll it turns on)
            edge.setEnabled(self._nav.autoscroll.isChecked())
            self._nav.autoscroll.toggled.connect(edge.setEnabled)
        self._add_rows(p, "Canvas", "Grid and guidelines")
        self._add_rows(p, "Canvas", "Selecting")
        s = p.section("Moving, copying and pasting")
        self._add_rows(p, "Canvas", "Moving and copying", section=s)
        s.row("Paste and duplicate offset:", self._paste_offset, key="opt/paste_offset")
        # where to paste; how many property windows a paste opens
        self._add_rows(p, "Canvas", "Copy and paste", section=s, keys=("canvas/paste_at",))
        self._flag_row(s, "props_on_copy", self._gb_name("props_on_copy"))
        self._add_rows(p, "Canvas", "Copy and paste", section=s)
        s = p.section("Pictures")
        s.row("Picture options open:", self._picture_options, key="opt/picture_options")
        s.row("Paste from another program:", self._paste_drawing, key="opt/paste_drawing")
        s.row("Flag pictures larger than:", self._large_picture, key="opt/large_picture_kb")
        from ..dialog.bitmap_import import SETTING as TRANSPARENT

        s.check(self._build_transparent(), self._gb_name("transparent_paste"), key=TRANSPARENT)
        s = p.section("Opening a page")
        s.row("Open a page at:", self._open_zoom, key="opt/open_zoom")
        # (the pages Where I left it remembers)
        self._add_rows(p, "Canvas", "Opening a page", section=s)
        self._tab_rows.canvas_rows(s)
        self._add_rows(p, "Canvas", "Undo and Redo", keys=("ui/undo_reveal",))
        self._add_rows(p, "Canvas", "Undo and Redo")
        self._enable_with("view/undo_flash_ms", self._row_widgets.get("ui/undo_reveal"))
        # (the pages whose underlay is remembered, under the switch)
        self._add_rows(
            p,
            "Canvas",
            "Underlay",
            keys=(
                "opt/underlay_opacity",
                "opt/underlay_tint",
                "opt/underlay_above",
                "opt/underlay_snap",
                "opt/underlay_remember",
                "opt/underlays_kept",
            ),
        )
        self._enable_with("opt/underlays_kept", self._row_widgets.get("opt/underlay_remember"))
        self._add_rows(p, "Canvas", "Underlay")

    def _lay_out_drawing(self) -> None:
        """The Drawing page."""
        p = self._tabs.add_page("Drawing")
        p.note(
            "Where ctgedit and the Graphics Builder draw differently, choose which you "
            "want. Open pages are redrawn."
        )
        s = p.section("Labels")
        s.row("Labels:", self._labels, key="view/label_mode")
        s.row("Stacked labels:", self._stacked, key="opt/stacked_labels")
        s = p.section("Genies")
        s.row("Draw a composite genie from:", self._composite, key="opt/composite_genies")
        self._add_rows(p, "Drawing", "Embedded controls")

    def _lay_out_appearance(self) -> None:
        """The Appearance page, its preview on top."""
        p = self._tabs.add_page("Appearance")
        self._preview = AppearancePreview(self)
        holder = QtWidgets.QWidget()
        hl = QtWidgets.QHBoxLayout(holder)
        hl.setContentsMargins(_sp(3), _sp(1), 0, _sp(1))
        hl.addWidget(self._preview)
        hl.addStretch(1)
        p.insert_widget(holder)
        s = p.section("The window")
        s.row("Theme:", self._theme, key="ui/style/theme")
        s.row("Accent colour:", self._accent_row, key="ui/accent")
        s.row("Density:", self._density, key="ui/density")
        s.row("Text size:", self._text_size, key="ui/text_size")
        s.check(self._follow_text, key="ui/follow_text_size")
        s.check(self._high_contrast, key="ui/high_contrast")
        s.row("Show the keyboard focus:", self._focus_ring, key="ui/focus_ring")
        s = p.section("Canvas")
        s.row("Outside the page:", self._surround, key="view/surround")
        s.row("Page edge:", self._page_edge, key="view/page_edge")
        s.check(self._page_shadow, key="opt/page_shadow")
        s.row("Grid style:", self._grid_style, key="view/grid_style")
        s.row("Major line every:", self._grid_major, key="view/grid_major")
        s.check(self._grid_outside, key="opt/grid_outside_page")
        s.check(self._pixel_grid, key="view/pixel_grid")
        if self.SELECTION_COLOUR_ROW:
            s.row("Selection colour:", self._sel_colour_row, key="view/selection_colour")
        self._add_rows(p, "Appearance", "Canvas", section=s)
        # Document tabs, Status bar
        s = self._tab_rows.appearance_rows(p)
        self._add_rows(p, "Appearance", "Document tabs", section=s)
        self._add_rows(p, "Appearance", "Messages")
        self._add_rows(p, "Appearance", "Status bar")
        # (the rename pause under its switch; the panels' tabs and titles)
        self._add_rows(
            p,
            "Appearance",
            "Objects panel",
            keys=(
                "objects/row_label",
                "objects/show_an",
                "ui/tree_eye",
                "objects/toggles_side",
                "objects/rename_click",
                "objects/rename_click_ms",
            ),
        )
        self._enable_with("objects/rename_click_ms", self._row_widgets.get("objects/rename_click"))
        self._add_rows(p, "Appearance", "Objects panel")
        self._add_rows(p, "Appearance", "Panels")
        self._add_rows(p, "Appearance", "Tables")
        self._add_rows(p, "Appearance", "Tags panel")
        p.note(
            (
                "These change at once and are never saved into a page. The selection toolbar keeps its "
                "size until ctgedit restarts."
            )
        )

    def _lay_out_keyboard(self) -> None:
        """The Keyboard page."""
        p = self._tabs.add_page("Keyboard")
        s = p.section("")
        s.check(
            self._keys,
            "keyboard shortcuts keys keymap record keys Graphics Builder "
            "S = Symbol Design tools Simulate window Run Pause Step",
            key="ui/prefs/keymap",
        )
        self._add_rows(p, "Keyboard", "Other windows")

    def _lay_out_ribbon(self) -> None:
        """The Ribbon page."""
        p = self._tabs.add_page("Ribbon", help_topic="settings#ribbon")
        s = p.section("Groups shown")
        for key, cb in self._ribbon_boxes.items():
            s.check(cb, key="ui/ribbon_hidden_groups")
        s.note("A hidden group's commands stay in the menus and the command palette.")
        s = p.section("My Commands")
        s.row("", self._ribbon_custom_widget, key="ui/ribbon_custom")
        s.note("Your own group at the end of the Design tab. Leave it empty for no group.")
        s = p.section("Tool tips")
        s.check(self._rich_tips)
        for sec in ("Quick Access", "Right-click menu", "Selection toolbar", "Contextual tabs", "Search"):
            self._add_rows(p, "Ribbon", sec)

    def _lay_out_libraries(self) -> None:
        """The Pages and libraries page."""
        f = self._flags
        p = self._tabs.add_page("Pages and libraries")
        s = p.section("When a page opens")
        for k in (
            "auto_update_template",
            "auto_update_symbols",
            "auto_update_genies",
            "genie_nested_full",
            "auto_placeholder_missing",
            "show_auto_changes",
        ):
            self._flag_row(s, k)
        self._add_rows(p, "Pages and libraries", "When a page opens", section=s)
        s = p.section("After saving")
        s.check(self._build_fast_display(), self._gb_name("citect.ini [Animator] FastDisplay"))
        self._flag_row(s, "ctf_rebuild_stale", sub=True)
        f["ctf_rebuild_stale"].setEnabled(self._fast_display.isChecked())
        self._fast_display.toggled.connect(f["ctf_rebuild_stale"].setEnabled)
        self._flag_row(s, "save_template_warning", self._gb_name("save_template_warning"))
        self._flag_row(s, "save_supergenie_warning")
        self._flag_row(s, "auto_close_builder")
        s.row(
            "Genies and closed pages that use a saved genie:", self._genie_pages, key="opt/genie_save_pages"
        )
        s.row(
            "Genies and closed pages that use a saved symbol:",
            self._symbol_pages,
            key="opt/symbol_save_pages",
        )
        s.row(
            "Templates and closed pages that use a saved template:",
            self._template_pages,
            key="opt/template_save_pages",
        )
        self._flag_row(s, "warn_shipped_template")
        s = p.section("Pages")
        self._add_rows(p, "Pages and libraries", "New pages", section=s)
        self._flag_row(s, "unlock_template")
        self._flag_row(s, "list_system_pages", self._gb_name("list_system_pages"))
        self._add_rows(p, "Pages and libraries", "Printing", section=s)
        s = p.section("Run")
        self._flag_row(s, "compile_enquiry", self._gb_name("compile_enquiry"))
        self._add_rows(p, "Pages and libraries", "Run", section=s)
        s = p.section("Files and folders")
        s.row("When another program changes an open page:", self._disk_reload, key="opt/disk_reload")
        self._add_rows(p, "Pages and libraries", "Files and folders", section=s)
        s.row("Extra library folders:", self._lib_row, key="opt/library_folders")
        s.row("Shared styles file:", self._shared_styles, key="styles/shared_file")
        self._flag_row(s, "library_siblings")
        s.row("Text code page (restart needed):", self._codepage, key="opt/codepage")
        self._flag_row(s, "text_question_marks")
        s = p.section("Find in closed pages")
        s.row("Helper processes:", self._find_procs, key="opt/find_processes")
        self._add_rows(p, "Pages and libraries", "Find in closed pages", section=s)
        s = p.section("Update Pages")
        s.row("Helper processes:", self._update_procs, key="opt/update_processes")
        self._add_rows(p, "Pages and libraries", "Update Pages", section=s)
        self._add_rows(p, "Pages and libraries", "Progress figures")
        for k in ("opt/run_estimate_items", "opt/run_estimate_seconds", "opt/run_estimate_smoothing"):
            self._enable_with(k, self._row_widgets.get("opt/run_figures"))
        self._add_rows(p, "Pages and libraries", "Busy projects")
        self._add_rows(p, "Pages and libraries", "Long jobs")
        self._add_rows(p, "Pages and libraries", "Library panel")
        self._add_rows(p, "Pages and libraries", "Searching")
        self._add_rows(p, "Pages and libraries", "Open and Save As")
        s = self._tab_rows.library_rows(p)
        # the recent pages, the window's rows and File > Print, into the section above
        self._add_rows(p, "Pages and libraries", "Starting and switching pages", section=s)

    def _lay_out_simulate(self) -> None:
        """The Simulate page."""
        p = self._tabs.add_page("Simulate", help_topic="simulate#simulate-settings")
        self._sim.lay_out(p)
        # (in the one long scroll the widest row sets every page's width)
        self._sim.engine.setMinimumWidth(140)

    def _lay_out_advanced(self) -> None:
        """The Advanced page."""
        p = self._tabs.add_page("Advanced")
        s = p.section("Diagnostics")
        self._flag_row(s, "developer_tools")
        self._flag_row(s, "dyn_badges", sub=True)
        self._flag_row(s, "display_check")
        self._add_rows(p, "Advanced", "Updates")
        self._add_rows(p, "Advanced", "The settings window")

    # -- the declared rows ----------------------------------------------------------------
    def _make_row_controls(self) -> None:
        """A control per :data:`ROWS` entry, showing its value."""
        self._row_widgets: Dict[str, QtWidgets.QWidget] = {}
        self._row_specs: Dict[str, SettingRow] = {}
        #: the rows on a page already (_add_rows places each once)
        self._rows_placed: set = set()
        for spec in self.ROWS:
            if spec.apply.startswith("action:") and not isinstance(
                getattr(self._w, spec.apply[7:], None), QtGui.QAction
            ):
                continue  # (no such command in this window)
            w = self._row_widget(spec, self._w)
            if spec.tip:
                w.setToolTip(spec.tip)
            self._row_widgets[spec.key] = w
            self._row_specs[spec.key] = spec
            self._set_row_value(spec.key, self._row_stored(spec))

    @staticmethod
    def _row_widget(spec: SettingRow, window=None) -> QtWidgets.QWidget:
        if spec.kind == "list":
            from .command_list import CommandListField

            return CommandListField(window)
        if spec.kind == "text":
            w = QtWidgets.QLineEdit()
            w.setMinimumWidth(160)
            if spec.special:
                w.setPlaceholderText(spec.special)
            return w
        if spec.kind == "bool":
            return QtWidgets.QCheckBox(spec.label)
        if spec.kind == "colour":
            # (a drop-down of the default and Custom, with a colour button: '#rrggbb')
            from ..editor.canvas_options import ColourChoice

            return ColourChoice(spec.choices, title=spec.label.rstrip(":"))
        if spec.kind == "choice":
            w = QtWidgets.QComboBox()
            for value, label in spec.choices:
                w.addItem(label, value)
            # (as wide as its longest value at the text size now, also after a change of it)
            w.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToContents)
            return w
        if spec.kind == "int":
            w = QtWidgets.QSpinBox()
            w.setRange(*spec.span)
            w.setSingleStep(spec.step)
            w.setSuffix(spec.suffix)
            if spec.special:
                w.setSpecialValueText(spec.special)
            return w
        raise ValueError("unknown setting row kind %r" % spec.kind)

    def _row_stored(self, spec: SettingRow):
        if spec.apply.startswith("action:"):
            return getattr(self._w, spec.apply[7:]).isChecked()
        from .settings import WHOLE_SPANS, whole

        if spec.key in WHOLE_SPANS:
            # (a number outside its span shows as the default, as
            # the window reads it)
            return whole(spec.key, WHOLE_SPANS[spec.key])
        # a garbled value shows as the window reads it: its default
        from . import window_options as WO

        if spec.key in WO.FLAG_KEYS:
            return WO.flag(spec.key)
        if spec.key in WO.LIST_KEYS and self._w is not None:
            return WO.command_ids(self._w, spec.key)
        return _stored(spec.key, declared_default(spec.key))

    def _look_value(self, key: str):
        """What the Appearance page shows for ``key`` now (the preview): the canvas look's
        controls, else the row of ``key`` (None: neither)."""
        if hasattr(self, "_page_edge"):
            look = self._canvas_look_choices()
            if key in look:
                return look[key]
        return self._row_value(key)

    def _row_value(self, key: str):
        """The value the row of ``key`` shows (None: no such row)."""
        w = getattr(self, "_row_widgets", {}).get(key)
        if w is None:
            return None
        if isinstance(w, QtWidgets.QCheckBox):
            return w.isChecked()
        if isinstance(w, QtWidgets.QComboBox):
            return w.currentData()
        if isinstance(w, QtWidgets.QSpinBox):
            return w.value()
        if isinstance(w, QtWidgets.QLineEdit):
            return w.text().strip()
        return w.value()

    def _set_row_value(self, key: str, value) -> None:
        w = self._row_widgets.get(key)
        if w is None:
            return
        if isinstance(w, QtWidgets.QCheckBox):
            w.setChecked(bool(value) if not isinstance(value, str) else value.lower() in ("true", "1"))
        elif isinstance(w, QtWidgets.QComboBox):
            k = w.findData(value)
            w.setCurrentIndex(k if k >= 0 else max(0, w.findData(declared_default(key))))
        elif isinstance(w, QtWidgets.QSpinBox):
            try:
                w.setValue(int(value))
            except TypeError, ValueError:  # swallow-ok: a garbled value shows its default
                w.setValue(int(declared_default(key) or 0))
        elif isinstance(w, QtWidgets.QLineEdit):
            w.setText("" if value is None else str(value))
        else:
            w.set_value(value)

    def _add_rows(self, page, page_title: str, section_title: str, section=None, sub=None, keys=None) -> None:
        """The declared rows of ``section_title`` on ``page`` (``keys``: only those, placed
        before the others; ``sub`` True / False: only the rows indented under another / only
        the others), into ``section``, else the page's section of that title, else a new one.
        A row is placed once: a second call for its section leaves it where it is."""
        placed = self._rows_placed
        specs = [
            s
            for s in self.ROWS
            if s.page == page_title
            and s.section == section_title
            and s.key in self._row_widgets
            and s.key not in placed
            and (keys is None or s.key in keys)
            and (sub is None or s.sub == sub)
        ]
        if not specs:
            return
        sec = (
            section
            if section is not None
            else next((x for x in getattr(page, "sections", ()) if x.title == section_title), None)
        )
        if sec is None:
            sec = page.section(section_title)
        for spec in specs:
            placed.add(spec.key)
            w = self._row_widgets[spec.key]
            if spec.kind == "bool":
                (sec.sub if spec.sub else sec.check)(w, key=spec.key)
            elif spec.sub:
                sec.sub(w, key=spec.key, label=spec.label)
            else:
                sec.row(spec.label, w, key=spec.key)

    def _enable_with(self, key: str, box) -> None:
        """The row of ``key`` is enabled while the check box ``box`` is ticked."""
        w = self._row_widgets.get(key)
        if w is None or not isinstance(box, QtWidgets.QAbstractButton):
            return
        w.setEnabled(box.isChecked())
        box.toggled.connect(w.setEnabled)

    def _history_use_row(self) -> QtWidgets.QWidget:
        """Settings > Editing > Safety: what the earlier versions take on disk, and Clear…"""
        from ..dialog.history_versions import disk_use_text

        holder = QtWidgets.QWidget()
        hl = QtWidgets.QHBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        self._history_use = QtWidgets.QLabel(disk_use_text())
        hl.addWidget(self._history_use, 1)
        self._history_clear = QtWidgets.QPushButton("Clear…")
        self._history_clear.setAutoDefault(False)
        self._history_clear.setToolTip("Remove the earlier versions of every page")
        self._history_clear.clicked.connect(self._clear_history)
        hl.addWidget(self._history_clear)
        return holder

    def _clear_history(self) -> None:
        from ..dialog.history_versions import clear_all_versions, disk_use_text

        if clear_all_versions(self):
            self._history_use.setText(disk_use_text())

    def _rows_choices(self) -> dict:
        return {k: self._row_value(k) for k in self._row_widgets}

    def _rows_defaults(self) -> None:
        for key, spec in self._row_specs.items():
            d = declared_default(key)
            if d is not None:
                self._set_row_value(key, d)

    def _rows_load(self) -> None:
        for key, spec in self._row_specs.items():
            self._set_row_value(key, self._row_stored(spec))

    def _apply_rows(self, before: dict) -> None:
        """What the declared rows that changed need (their ``apply``)."""
        now = self._rows_choices()
        changed = [k for k in now if not _same(before.get(k), now[k])]
        kinds = {self._row_specs[k].apply for k in changed}
        for k in changed:
            spec = self._row_specs[k]
            if spec.apply.startswith("action:"):
                # (a View menu toggle: every editor window's, not only the one Settings was
                # opened from; a window that has it already is left as it is)
                for win in self._windows_with(self._w):
                    a = getattr(win, spec.apply[7:], None)
                    if a is None:
                        continue
                    try:
                        if a.isChecked() != bool(now[k]):
                            a.trigger()
                    except RuntimeError:  # swallow-ok: a window being closed
                        continue
        from .settings_pages import run_apply_hooks

        run_apply_hooks(changed)
        if "ui/settings_live_delay_ms" in changed:
            self._live_timer.setInterval(live_delay_ms())  # (this window's own pause)
        if "theme" in kinds:
            self._retheme()
        if "canvas" in kinds or "theme" in kinds:
            self._redraw_canvases()
        for kind in kinds:
            if kind.startswith("window:"):
                for w in self._windows():
                    fn = getattr(w, kind[7:], None)
                    if callable(fn):
                        fn()

    @staticmethod
    def _windows() -> list:
        try:
            from ..main_window import MainWindow

            return MainWindow.editor_windows()
        except ImportError:  # swallow-ok: no editor windows
            return []

    def _retheme(self) -> None:
        from ..style import theme

        app = QtWidgets.QApplication.instance()
        if app is not None:
            theme.apply_theme(app)

    def _redraw_canvases(self) -> None:
        for w in self._windows():
            try:
                w.scene.invalidate(QtCore.QRectF(), QtWidgets.QGraphicsScene.AllLayers)
                w.view.viewport().update()
            except AttributeError, RuntimeError:  # swallow-ok: a window being closed
                continue

    # -- the controls ---------------------------------------------------------------------
    def _make_keyboard_controls(self) -> None:
        from .keymap_page import KeymapPage

        self._keys = KeymapPage(self._w, self)
        self._keys.setProperty("ctgNoState", True)
        self._keys.presetChanged.connect(self._keymap_preset_changed)
        from . import keymap

        self._keymap_shown = keymap.preset_name()  # (the preset a change leaves)

    def _keymap_preset_changed(self, name: str) -> None:
        """A keymap's own choices with it (Design tools: Shift + arrow moves one large step,
        Settings 'Large arrow key step'), and the default again for a choice only the keymap
        before made."""
        from . import keymap

        old = self._keymap_shown
        self._keymap_shown = name
        sa = keymap.preset_switch_settings(old, name).get("opt/shift_arrow")
        if sa:
            self._shift_arrow.setCurrentIndex(max(0, self._shift_arrow.findData(sa)))

    def _make_ribbon_controls(self, st) -> None:
        """Settings > Ribbon: a check box per group and the list of the user's own group."""
        rb = getattr(self._w, "ribbon", None)
        names = rb.group_names() if rb is not None else []
        hidden = {str(x) for x in (st.value("ui/ribbon_hidden_groups", [], type=list) or [])}
        custom = getattr(self._w, "CUSTOM_GROUP", "My Commands")
        self._ribbon_boxes = {}
        for title, caption in names:
            if caption == custom:
                continue
            key = "%s/%s" % (title, caption)
            cb = QtWidgets.QCheckBox("%s: %s" % (title, caption))
            cb.setChecked(key not in hidden)
            cb.setToolTip("Show the %s group of the %s tab" % (caption, title))
            self._ribbon_boxes[key] = cb
        from ..style.tips import SETTING as _RICH_TIPS
        from .settings import SETTINGS_KEYS as _KEYS

        self._rich_tips = QtWidgets.QCheckBox("Show the key and a description in tool tips")
        self._rich_tips.setToolTip("Off: tool tips show the name and the key only")
        self._rich_tips.setChecked(st.value(_RICH_TIPS, _KEYS[_RICH_TIPS], type=bool))
        from .command_list import CommandListField

        # (the list settings' own field: Add, Remove, Move Up and Move Down)
        self._ribbon_field = CommandListField(self._w, tip="The commands of your own ribbon group, in order")
        self._ribbon_field.set_value(st.value("ui/ribbon_custom", [], type=list) or [])
        self._ribbon_list = self._ribbon_field.list
        self._ribbon_custom_widget = self._ribbon_field

    def ribbon_choices(self) -> dict:
        """The Ribbon page's settings (My Commands by command id)."""
        hidden = [key for key, cb in self._ribbon_boxes.items() if not cb.isChecked()]
        return {"ui/ribbon_hidden_groups": hidden, "ui/ribbon_custom": self._ribbon_field.value()}

    def _build_transparent(self) -> QtWidgets.QHBoxLayout:
        from ..dialog.bitmap_import import transparent_colour
        from ..objprops.colour_field import ColourPairField

        cur = transparent_colour()
        row = QtWidgets.QHBoxLayout()
        self._chk_transparent = QtWidgets.QCheckBox("Make a colour transparent in pasted pictures:")
        self._chk_transparent.setChecked(cur is not None)
        self._chk_transparent.setToolTip("In the pictures you paste or import")
        self._transparent = ColourPairField("Transparent paste colour", flash=False)
        self._transparent.set_pair(0xFFFFFF if cur is None else cur)
        self._transparent.setEnabled(cur is not None)
        self._chk_transparent.toggled.connect(self._transparent.setEnabled)
        row.addWidget(self._chk_transparent)
        row.addWidget(self._transparent)
        row.addStretch(1)
        return row

    def _build_fast_display(self) -> QtWidgets.QCheckBox:
        from ...io import citect_config

        tip = (
            "A Plant SCADA setting, shared with the runtime and the Graphics Builder. "
            "Off: saves delete them and the runtime draws each page from its .ctg"
        )
        self._fast_display = QtWidgets.QCheckBox("Write fast display files (.ctF)")
        ini = citect_config.active_ini_path()
        try:
            on = citect_config.fast_display_enabled()
        except Exception:  # noqa: BLE001 -- an unreadable ini reads as the default
            logsetup.note_exception("settings_dialog.fast_display")
            on = True
        self._fast_initial = on
        self._fast_display.setChecked(on)
        if ini:
            self._fast_display.setToolTip("%s\n\nStored in %s." % (tip, ini))
        else:
            self._fast_display.setEnabled(False)
            self._fast_display.setToolTip("No Plant SCADA configuration file was found")
        return self._fast_display

    def _make_editing_controls(self, st) -> None:
        window = self._w
        self._tol = QtWidgets.QSpinBox()
        self._tol.setRange(2, 40)
        self._tol.setSuffix(" px")
        self._tol.setValue(int(window.view.SMART_GUIDE_TOL))
        self._tol.setToolTip("How close an object must come to snap to a guide")
        self._sg_outline = QtWidgets.QCheckBox("Outline the objects a smart guide lines up with")
        self._sg_outline.setChecked(bool(window.view.SMART_GUIDE_OUTLINE))
        self._sg_outline.setToolTip("While you drag")
        self._nudge = QtWidgets.QSpinBox()
        self._nudge.setRange(1, 256)
        self._nudge.setSuffix(" px")
        self._nudge.setValue(getattr(window, "_nudge_step", 1))
        self._nudge.setToolTip("How far one arrow-key press moves the selection")
        from .settings import NUDGE_BURST_RANGE, nudge_burst_ms

        self._nudge_burst = QtWidgets.QSpinBox()
        self._nudge_burst.setRange(*NUDGE_BURST_RANGE)
        self._nudge_burst.setSingleStep(50)
        self._nudge_burst.setSuffix(" ms")
        self._nudge_burst.setSpecialValueText("Off")
        self._nudge_burst.setValue(nudge_burst_ms())
        self._nudge_burst.setToolTip("Presses this close together make one move and one undo step")
        # CtDraw: Shift + an arrow key RESIZES (help 1172187); or it moves 10 steps, per user
        self._shift_arrow = QtWidgets.QComboBox()
        self._shift_arrow.addItem("Resize the selection", "resize")
        # (Large arrow key step, 10 steps unless changed)
        self._shift_arrow.addItem("Move the selection one large step", "nudge10")
        self._shift_arrow.setCurrentIndex(
            max(0, self._shift_arrow.findData(st.value("opt/shift_arrow", "resize", type=str)))
        )
        self._shift_arrow.setToolTip("Ctrl+Shift+arrow always resizes and keeps the proportions")
        self._labels = QtWidgets.QComboBox()
        # the View > Labels names
        self._labels.addItem("As Stored: @(text)", "literal")
        self._labels.addItem("Native Text", "native")
        self._labels.setToolTip("How @(text) labels show on the canvas")
        try:
            if window.doc is not None:
                from ...io.lang import LanguageDB

                db = LanguageDB.for_page(window.doc.path)
                for lang in db.languages() if db else []:
                    self._labels.addItem(lang, lang)
        except Exception:  # noqa: BLE001
            logsetup.note_exception("settings_dialog.languages")
        self._labels.setCurrentIndex(max(0, self._labels.findData(getattr(window, "_label_mode", "literal"))))
        # View > Outside the Page: what the canvas shows around the page
        self._surround = QtWidgets.QComboBox()
        labels = list(SURROUND_LABELS)
        try:
            from ...render.scene import SURROUND_MODES

            if "desk" in SURROUND_MODES and "desk" not in dict(labels):
                labels.insert(0, ("desk", "Desk with a page shadow"))
        except ImportError:  # swallow-ok: the modes of the labels then
            pass
        for mode, label in labels:
            self._surround.addItem(label, mode)
        self._surround.setCurrentIndex(max(0, self._surround.findData(surround_mode())))
        self._surround.setToolTip(
            "The colour around the page (also View > Outside the Page). Never saved or printed"
        )
        # the colours of new objects (ui/editor/draw_styles: the ribbon's swatch pair)
        from ..swatch_pair import NewColoursRow

        self._new_colours = NewColoursRow()
        from ..recovery import autosave_minutes
        from .settings import AUTOSAVE_MINUTES_RANGE

        self._autosave = QtWidgets.QSpinBox()
        self._autosave.setRange(*AUTOSAVE_MINUTES_RANGE)
        self._autosave.setSpecialValueText("Off")
        self._autosave.setSuffix(" min")
        self._autosave.setValue(autosave_minutes())
        self._autosave.setToolTip(
            (
                "At most this often while a page has unsaved changes, and after a pause in editing "
                "(the row below). After a crash, ctgedit offers to restore the copies"
            )
        )
        from .settings import UNDO_MB_RANGE, undo_mb

        self._undo_mb = QtWidgets.QSpinBox()
        self._undo_mb.setRange(*UNDO_MB_RANGE)
        self._undo_mb.setSingleStep(64)
        self._undo_mb.setSuffix(" MB")
        self._undo_mb.setValue(undo_mb())
        self._undo_mb.setToolTip(
            "Shared by all open pages. One page keeps at most two "
            "thirds of it. Past it, the oldest steps are dropped"
        )

    def _make_canvas_controls(self) -> None:
        """How the canvas reacts and draws: the wheel, the paste offset, the opening zoom,
        two drawing choices where ctgedit and the Graphics Builder differ."""
        from .settings import OPEN_ZOOM_DEFAULT
        from .settings import (
            COMPOSITE_GENIES,
            OPEN_ZOOM,
            PASTE_OFFSET_RANGE,
            STACKED_LABELS,
            choice,
            option,
            paste_offset,
        )

        self._wheel = QtWidgets.QComboBox()
        self._wheel.addItem("Zoom", False)
        self._wheel.addItem("Scroll (Ctrl+wheel zooms)", True)
        self._wheel.setCurrentIndex(1 if option("wheel_scrolls") else 0)
        self._wheel.setToolTip("When it scrolls, Shift+wheel scrolls sideways")
        self._paste_offset = QtWidgets.QSpinBox()
        self._paste_offset.setRange(*PASTE_OFFSET_RANGE)
        self._paste_offset.setSuffix(" px")
        self._paste_offset.setSpecialValueText("On the original")
        self._paste_offset.setValue(paste_offset())
        self._paste_offset.setToolTip(
            "Down and to the right of the original. Each further "
            "paste moves as far again, and so do several "
            "pictures dropped at once"
        )
        from ..dialog import picture_import as PI

        self._picture_options = QtWidgets.QComboBox()
        for value, label in PI.WHEN:
            self._picture_options.addItem(label, value)
        self._picture_options.setCurrentIndex(max(0, self._picture_options.findData(PI.when())))
        self._picture_options.setToolTip("Size, resolution and transparency, with a preview")
        self._paste_drawing = QtWidgets.QComboBox()
        for value, label in PI.PASTE_KINDS:
            self._paste_drawing.addItem(label, value)
        self._paste_drawing.setCurrentIndex(max(0, self._paste_drawing.findData(PI.paste_kind())))
        self._paste_drawing.setToolTip(
            "Many programs copy both a drawing and a picture of "
            "it. Edit > Paste Special lets you choose each time"
        )
        self._large_picture = QtWidgets.QSpinBox()
        self._large_picture.setRange(*PI.LARGE_RANGE)
        self._large_picture.setSingleStep(256)
        self._large_picture.setSuffix(" KB")
        self._large_picture.setValue(PI.large_bytes() // 1024)
        self._large_picture.setToolTip(
            "Shown in the warning colour in the Objects panel and the picture options"
        )
        self._open_zoom = QtWidgets.QComboBox()
        for value, label in OPEN_ZOOM:
            self._open_zoom.addItem(label, value)
        self._open_zoom.setCurrentIndex(
            max(0, self._open_zoom.findData(choice("opt/open_zoom", OPEN_ZOOM, OPEN_ZOOM_DEFAULT)))
        )
        self._open_zoom.setToolTip("A tab you come back to keeps its zoom")
        self._stacked = QtWidgets.QComboBox()
        for value, label in STACKED_LABELS:
            self._stacked.addItem(label, value)
        self._stacked.setCurrentIndex(
            max(0, self._stacked.findData(choice("opt/stacked_labels", STACKED_LABELS, "top")))
        )
        self._stacked.setToolTip(
            "Copies of a label at one place, each shown under its own "
            "condition. Drawn all at once they look bold or blurred"
        )
        self._composite = QtWidgets.QComboBox()
        for value, label in COMPOSITE_GENIES:
            self._composite.addItem(label, value)
        self._composite.setCurrentIndex(
            max(0, self._composite.findData(choice("opt/composite_genies", COMPOSITE_GENIES, "members")))
        )
        self._composite.setToolTip("The cache holds the art the genie was composed with")
        self._make_canvas_look_controls()

    def _make_canvas_look_controls(self) -> None:
        """Settings > Canvas: the page edge and shadow, the grid's look, the selection
        colour (editor-only: never saved in a page or printed)."""
        from .settings import GRID_MAJOR_RANGE, GRID_STYLES, PAGE_EDGES

        self._page_edge = QtWidgets.QComboBox()
        for value, label in PAGE_EDGES:
            self._page_edge.addItem(label, value)
        self._page_edge.setToolTip("The line just outside the page")
        self._page_shadow = QtWidgets.QCheckBox("Page shadow")
        self._page_shadow.setToolTip("A soft shadow under the page, whatever is around it")
        self._grid_style = QtWidgets.QComboBox()
        for value, label in GRID_STYLES:
            self._grid_style.addItem(label, value)
        self._grid_style.setToolTip("Dots: one point at each crossing (also Grid Setup)")
        self._grid_major = QtWidgets.QSpinBox()
        self._grid_major.setRange(*GRID_MAJOR_RANGE)
        self._grid_major.setSpecialValueText("None")
        self._grid_major.setSuffix(" grid lines")
        self._grid_major.setToolTip(
            "A stronger line every so many grid lines (1: every "
            "line). It still shows when the grid is too fine to "
            "draw (also Grid Setup)"
        )
        self._grid_outside = QtWidgets.QCheckBox("Grid outside the page")
        self._grid_outside.setToolTip("As the Graphics Builder. Not with the desk around the page")
        self._pixel_grid = QtWidgets.QCheckBox("Pixel grid at 800 % and more")
        self._pixel_grid.setToolTip("A faint line at every pixel of the page (also View > Grid)")
        self._sel_colour = QtWidgets.QComboBox()
        self._sel_colour.addItem("The accent colour", "accent")
        self._sel_colour.addItem("Custom", "custom")
        self._sel_colour.setToolTip(
            "Selection outlines, handles and the hover outline on "
            "the canvas. Pick one that shows on your pages"
        )
        from ..colour_picker import ColourButton

        self._sel_custom = ColourButton(0xFF00FF, title="Selection Colour", flash=False, transparent=False)
        self._sel_custom.setToolTip("The selection colour")
        self._sel_colour.currentIndexChanged.connect(
            lambda _i: self._sel_custom.setVisible(self._sel_colour.currentData() == "custom")
        )
        self._sel_colour_row = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(self._sel_colour_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self._sel_colour)
        row.addWidget(self._sel_custom)
        row.addStretch(1)
        self._show_canvas_look(lambda key, default: app_settings().value(key, default))

    def _show_canvas_look(self, get) -> None:
        """The Canvas page's look controls from ``get(key, default)``."""

        def flag(key, default):
            v = get(key, default)
            return v in (True, "true", "1", 1) if not isinstance(v, bool) else v

        self._page_edge.setCurrentIndex(max(0, self._page_edge.findData(get("view/page_edge", "dotted"))))
        self._page_shadow.setChecked(flag("opt/page_shadow", True))
        self._grid_style.setCurrentIndex(max(0, self._grid_style.findData(get("view/grid_style", "lines"))))
        try:
            self._grid_major.setValue(int(get("view/grid_major", 8)))
        except TypeError, ValueError:  # swallow-ok: a garbled setting shows its default
            self._grid_major.setValue(8)
        self._grid_outside.setChecked(flag("opt/grid_outside_page", False))
        self._pixel_grid.setChecked(flag("view/pixel_grid", True))
        sel = str(get("view/selection_colour", "accent") or "accent").strip().lower()
        custom = sel != "accent"
        self._sel_colour.setCurrentIndex(1 if custom else 0)
        self._sel_custom.setVisible(custom)
        if custom:
            try:
                n = int(sel[1:], 16) if sel.startswith("#") else int(sel, 0)
                self._sel_custom.set_pair((n & 0xFFFFFF, n & 0xFFFFFF))
            except ValueError:  # swallow-ok: a garbled colour shows the accent
                self._sel_colour.setCurrentIndex(0)

    def _canvas_look_choices(self) -> dict:
        out = {
            "view/page_edge": self._page_edge.currentData(),
            "opt/page_shadow": self._page_shadow.isChecked(),
            "view/grid_style": self._grid_style.currentData(),
            "view/grid_major": self._grid_major.value(),
            "opt/grid_outside_page": self._grid_outside.isChecked(),
            "view/pixel_grid": self._pixel_grid.isChecked(),
        }
        if self.SELECTION_COLOUR_ROW:
            sel = "accent"
            if self._sel_colour.currentData() == "custom":
                pair = self._sel_custom.pair()
                if pair is not None:
                    sel = "#%06x" % (pair[0] & 0xFFFFFF)
            out["view/selection_colour"] = sel
        return out

    def _make_appearance_controls(self) -> None:
        from ..style import theme

        self._theme = ThemeTiles()
        self._text_size = QtWidgets.QSpinBox()
        lo, hi = theme.TEXT_SIZE_RANGE
        self._text_size.setRange(lo, hi)
        self._text_size.setSingleStep(5)
        self._text_size.setSuffix(" %")
        self._text_size.setToolTip(
            "Menus, panels and dialogs, and the canvas's readouts, count badges and Page chip"
        )
        self._follow_text = QtWidgets.QCheckBox(
            "Also follow the Windows text size (now %d %%)" % theme.windows_text_scale()
        )
        hc_now = theme.windows_high_contrast()
        self._high_contrast = QtWidgets.QCheckBox(
            "Use the Windows high-contrast colours when high contrast is on (now %s)"
            % ("on" if hc_now else "off")
        )
        self._high_contrast.setToolTip("Overrides the theme")
        self._accent = QtWidgets.QComboBox()
        for value, label in theme.ACCENT_CHOICES:
            self._accent.addItem(label, value)
        self._accent.setToolTip(
            "Selections, focus rings and checked buttons. Made lighter "
            "or darker when needed, so text on it stays readable"
        )
        from ..colour_picker import ColourButton

        self._accent_custom = ColourButton(0x0A64C8, title="Accent Colour", flash=False, transparent=False)
        self._accent_custom.setToolTip("Your own accent colour")
        self._accent.currentIndexChanged.connect(
            lambda _i: self._accent_custom.setVisible(self._accent.currentData() == "custom")
        )
        self._accent_row = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(self._accent_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self._accent)
        row.addWidget(self._accent_custom)
        row.addStretch(1)
        self._density = QtWidgets.QComboBox()
        for value, label in theme.DENSITIES:
            self._density.addItem(label, value)
        self._density.setToolTip(
            "The space around menu items, list rows, tabs and buttons. Compact leaves more room for the page"
        )
        self._focus_ring = QtWidgets.QComboBox()
        for value, label in theme.FOCUS_RINGS:
            self._focus_ring.addItem(label, value)
        self._focus_ring.setToolTip(
            "An accent ring round the button, box or list that the "
            "keys act on, and a frame round the canvas. After "
            "keyboard use: a mouse click hides it"
        )
        self._show_appearance(theme.appearance())

    def _make_library_controls(self) -> None:
        from .settings import (
            CODEPAGES,
            DISK_RELOAD,
            GENIE_SAVE_PAGES,
            SYMBOL_SAVE_PAGES,
            TEMPLATE_SAVE_PAGES,
            choice,
            disk_reload,
            genie_save_pages,
            library_folders,
            symbol_save_pages,
            template_save_pages,
        )

        self._disk_reload = QtWidgets.QComboBox()
        for mode, label in DISK_RELOAD:
            self._disk_reload.addItem(label, mode)
        self._disk_reload.setCurrentIndex(max(0, self._disk_reload.findData(disk_reload())))
        self._disk_reload.setToolTip(
            "Such as the Graphics Builder. A page with unsaved changes is always asked about"
        )
        self._genie_pages = QtWidgets.QComboBox()
        for mode, label in GENIE_SAVE_PAGES:
            self._genie_pages.addItem(label, mode)
        self._genie_pages.setCurrentIndex(max(0, self._genie_pages.findData(genie_save_pages())))
        self._genie_pages.setToolTip(
            "The library genies that place it are saved again "
            "first. Open pages and genies are always updated in "
            "their tabs"
        )
        self._symbol_pages = QtWidgets.QComboBox()
        for mode, label in SYMBOL_SAVE_PAGES:
            self._symbol_pages.addItem(label, mode)
        self._symbol_pages.setCurrentIndex(max(0, self._symbol_pages.findData(symbol_save_pages())))
        self._symbol_pages.setToolTip(
            "The library genies and templates that place it are "
            "saved again first. Open pages are always updated in "
            "their tabs"
        )
        self._template_pages = QtWidgets.QComboBox()
        for mode, label in TEMPLATE_SAVE_PAGES:
            self._template_pages.addItem(label, mode)
        self._template_pages.setCurrentIndex(max(0, self._template_pages.findData(template_save_pages())))
        self._template_pages.setToolTip(
            (
                "Templates built on it are saved again first, and open pages take it at once. Ask me "
                "asks only while the save template warning is on"
            )
        )
        self._lib_row = QtWidgets.QHBoxLayout()
        self._lib_folders = QtWidgets.QLineEdit(";".join(library_folders()))
        self._lib_folders.setPlaceholderText("None")
        self._lib_folders.setMinimumWidth(140)
        self._lib_folders.setToolTip(
            "Searched after the page's project and its included projects. Separate folders with ;"
        )
        add = QtWidgets.QPushButton("Add…")
        add.setAutoDefault(False)
        add.clicked.connect(self._add_library_folder)
        self._lib_row.addWidget(self._lib_folders, 1)
        self._lib_row.addWidget(add)
        # Edit > Styles: a team's styles file, listed read-only (ui/panels/styles_gallery)
        from ..panels.styles_gallery import SharedStylesRow

        self._shared_styles = SharedStylesRow()
        self._codepage = QtWidgets.QComboBox()
        for value, label in CODEPAGES:
            self._codepage.addItem(label, value)
        self._codepage.setCurrentIndex(
            max(0, self._codepage.findData(choice("opt/codepage", CODEPAGES, "auto")))
        )
        self._codepage.setToolTip("Change it only for projects made on Windows set to another language")
        from .job_options import processes_box

        self._find_procs = processes_box("opt/find_processes")
        self._update_procs = processes_box("opt/update_processes")

    def _show_appearance(self, a: dict) -> None:
        self._theme.setCurrentIndex(max(0, self._theme.findData(a["ui/style/theme"])))
        self._text_size.setValue(int(a["ui/text_size"]))
        self._follow_text.setChecked(bool(a["ui/follow_text_size"]))
        self._high_contrast.setChecked(bool(a["ui/high_contrast"]))
        acc = str(a.get("ui/accent", "blue") or "blue")
        if acc.startswith("#"):
            self._accent.setCurrentIndex(self._accent.findData("custom"))
            try:
                n = int(acc[1:], 16)
                self._accent_custom.set_pair((n, n))
            except ValueError:  # swallow-ok: a garbled colour shows the default blue
                self._accent.setCurrentIndex(0)
        else:
            self._accent.setCurrentIndex(max(0, self._accent.findData(acc)))
        self._accent_custom.setVisible(self._accent.currentData() == "custom")
        self._density.setCurrentIndex(max(0, self._density.findData(a.get("ui/density", "comfortable"))))
        self._focus_ring.setCurrentIndex(
            max(0, self._focus_ring.findData(a.get("ui/focus_ring", "keyboard")))
        )

    def _accent_choice(self) -> str:
        acc = self._accent.currentData()
        if acc == "custom":
            pair = self._accent_custom.pair()
            return "#%06x" % (pair[0] & 0xFFFFFF) if pair is not None else "blue"
        return acc

    def _appearance_choices(self) -> dict:
        return {
            "ui/style/theme": self._theme.currentData(),
            "ui/text_size": self._text_size.value(),
            "ui/follow_text_size": self._follow_text.isChecked(),
            "ui/high_contrast": self._high_contrast.isChecked(),
            "ui/accent": self._accent_choice(),
            "ui/density": self._density.currentData(),
            "ui/focus_ring": self._focus_ring.currentData(),
        }

    def _apply_appearance(self, before: dict) -> None:
        """Re-theme the application when an Appearance choice changed."""
        from ..style import theme

        if before == theme.appearance():
            return
        app = QtWidgets.QApplication.instance()
        if app is None:
            return
        scheme, _scale = theme.apply_theme(app)
        for win in self._windows_with(self._w):
            ribbon = getattr(win, "ribbon", None)
            if ribbon is not None and hasattr(ribbon, "_apply_height"):
                try:
                    ribbon._apply_height()
                except RuntimeError:  # swallow-ok: a window being closed
                    continue
        names = {"dark": "dark", "light": "light", "contrast": "Windows high-contrast"}
        self._status("Now using the %s colours" % names.get(scheme, scheme))

    # -- the rows' settings: markers, reset, live apply -------------------------------------
    def _row_state_widgets(self, row) -> list:
        ws = getattr(row, "_ctg_state_ws", None)
        if ws is None:
            field = row.field
            ws = [field] if isinstance(field, QtWidgets.QWidget) and _stateful(field) else []
            if isinstance(field, QtWidgets.QWidget):
                ws += [w for w in field.findChildren(QtWidgets.QWidget) if _stateful(w)]
            row._ctg_state_ws = ws
        return ws

    def _row_state(self, row):
        st = tuple(_read(w) for w in self._row_state_widgets(row))
        if row.field is self._keys:
            st += (
                self._keys.state()[0],
                tuple(sorted((k, tuple(v)) for k, v in self._keys.state()[1].items())),
            )
        return st

    def _state(self) -> dict:
        """Every row's controls (and the values kept beside them): what Revert to Opening Values and
        Reset to Default put back."""
        rows = {id(r): (r, self._row_state(r)) for r in self._tabs.rows()}
        return {"rows": rows, "draw": self._new_colours.state(), "keys": self._keys.state()}

    def _set_state(self, state: dict, rows=None) -> None:
        for _i, (r, values) in state["rows"].items():
            if rows is not None and r not in rows:
                continue
            ws = self._row_state_widgets(r)
            for w, v in zip(ws, values):
                _write(w, v)
            if r.field is self._keys:
                preset, user = state["keys"]
                self._keys.set_state(preset, user)
        if rows is None or any(r.key == "draw/colour_mode" for r in rows):
            self._new_colours.set_state(state["draw"])

    def _bind_rows(self) -> None:
        """Every row's default (Reset All Settings' values) and the setting of the rows not
        bound by name (_probe_key); the context menu's Reset to Default."""
        opening = self._state()
        base = self._choices()
        self.restore_defaults()
        self._default_state = self._state()
        self._set_state(opening)
        for _i, (r, dvals) in self._default_state["rows"].items():
            if not r.key:
                key = self._probe_key(r, base)
                if key:
                    r.key = key
            ws = self._row_state_widgets(r)
            if r.field is self._keys:
                text = "%s, no keys of your own" % self._keys.preset.itemText(0)
            elif isinstance(r.field, ThemeTiles):
                text = r.field.itemText(next((k for k, v in enumerate(dvals) if v), 0))
            else:
                text = _shown(ws[0], dvals[0]) if ws else ""
            r.bind(r.key, text if r.key else "")
            r.changed.connect(self._row_changed)
            r.edited.connect(self._row_edited)
            r.resetRequested.connect(self.reset_row)
            if self._live:
                # (a typed number applies on Enter or when the box is left, as typed text
                # does: half of '500' MB is no undo budget; the arrows apply at once)
                for w in ws:
                    if isinstance(w, QtWidgets.QAbstractSpinBox):
                        w.setKeyboardTracking(False)
        self._keys.changed.connect(self._row_changed)

    def _probe_key(self, row, base: dict) -> str:
        """The setting a row's control writes: the one key of the dialog's choices that
        changes when the control is changed once (put back at once)."""
        ws = self._row_state_widgets(row)
        if not ws:
            return ""
        w = ws[0]
        old = _read(w)
        new = None
        if isinstance(w, QtWidgets.QAbstractButton) and not _colour_button(w):
            new = not old
        elif isinstance(w, QtWidgets.QComboBox) and w.count() > 1:
            new = (old + 1) % w.count()
        elif isinstance(w, QtWidgets.QAbstractSpinBox):
            step = w.singleStep()
            new = old + step if old + step <= w.maximum() else old - step
        if new is None:
            return ""
        w.blockSignals(True)
        try:
            _write(w, new)
            after = self._choices()
        finally:
            _write(w, old)
            w.blockSignals(False)
        diff = [k for k in after if not _same(after[k], base.get(k))]
        return diff[0] if len(diff) == 1 else ""

    def row_modified(self, row) -> bool:
        if row.field is self._keys:
            preset, user = self._keys.state()
            from . import keymap

            return preset != keymap.DEFAULT_PRESET or bool(user)
        d = self._default_state["rows"].get(id(row))
        return d is not None and self._row_state(row) != d[1]

    def _update_marks(self) -> None:
        for r in self._tabs.rows():
            r.set_modified(bool(r.key) and self.row_modified(r))
        self._tabs.refilter()
        if self._btn_undo is not None:
            self._btn_undo.setEnabled(self._changed_since_open())
        # (Undo Reset stands only while nothing changed after the reset)
        undo_reset = getattr(self, "_btn_undo_reset", None)
        if undo_reset is None:
            return
        if self._after_reset is not None and not self._same_state(self._state(), self._after_reset):
            self._before_reset = self._after_reset = None
        undo_reset.setVisible(self._before_reset is not None)

    @staticmethod
    def _same_state(a: dict, b: dict) -> bool:
        return (
            a["draw"] == b["draw"]
            and a["keys"] == b["keys"]
            and all(a["rows"].get(k, (None, None))[1] == v[1] for k, v in b["rows"].items())
        )

    def _changed_since_open(self) -> bool:
        now = self._state()
        return (
            now["draw"] != self._opening["draw"]
            or now["keys"] != self._opening["keys"]
            or any(now["rows"][k][1] != v[1] for k, v in self._opening["rows"].items())
        )

    def _row_changed(self) -> None:
        if self._building:
            return
        self._row_edited()
        if self._live and self._armed:
            self._live_timer.start()

    def _row_edited(self) -> None:
        """Text being typed: the marks and the preview follow; it applies when the typing
        is finished (Enter, the box left, the window closed)."""
        if self._building:
            return
        self._update_marks()
        if hasattr(self, "_preview"):
            self._preview.update()

    def _finish_typing(self) -> None:
        """What is typed and not finished yet (a path, a number) counts as changed."""
        fw = QtWidgets.QApplication.focusWidget()
        if isinstance(fw, QtWidgets.QAbstractSpinBox) and self.isAncestorOf(fw):
            fw.interpretText()
        for r in self._tabs.rows():
            r.finish_typing()

    def reset_row(self, row) -> None:
        """Reset to Default of one row (applied like any change)."""
        self._set_state(self._default_state, rows=[row])
        if row.field is self._keys:
            from . import keymap

            self._keys.set_state(keymap.DEFAULT_PRESET, {})
        self._row_changed()

    def showEvent(self, e):  # noqa: N802 -- Qt naming
        super().showEvent(e)
        self._armed = True
        # the page asked for, once the window has its size (before it, the scroll range is
        # empty and the jump lands at the top)
        page, self._first_page = getattr(self, "_first_page", ""), ""
        if page:
            QtCore.QTimer.singleShot(0, self, lambda: self._tabs.show_page(page))

    def _live_apply(self) -> None:
        if self.isVisible():
            self._apply(accept=False)
            self._note_applied()
            self._update_marks()

    def _note_applied(self) -> None:
        """A live apply: its state is the one Undo Last Change goes back from."""
        now = self._state()
        if not self._same_state(now, self._applied[-1]):
            self._applied.append(now)
        self._sync_undo_last()

    def _sync_undo_last(self) -> None:
        if getattr(self, "_btn_undo_last", None) is not None:
            self._btn_undo_last.setEnabled(len(self._applied) > 1)

    def _changed_rows(self, a: dict, b: dict) -> list:
        """The labels of the rows whose values differ between states ``a`` and ``b``."""
        out = []
        for k, (r, v) in a["rows"].items():
            if b["rows"].get(k, (None, None))[1] != v:
                text = (getattr(r, "label_text", "") or "").rstrip(":")
                if not text and isinstance(getattr(r, "field", None), QtWidgets.QAbstractButton):
                    text = r.field.text()
                if text:
                    out.append(text.replace("&", ""))
        return out

    def undo_last_change(self) -> None:
        """Undo Last Change: the state applied before the last one, applied again."""
        self._finish_typing()
        if self._live_timer.isActive():
            self._live_timer.stop()
            self._apply(accept=False)
            self._note_applied()
        if len(self._applied) < 2:
            return
        last = self._applied.pop()
        before = self._applied[-1]
        names = self._changed_rows(last, before)
        self._building = True
        try:
            self._set_state(before)
        finally:
            self._building = False
        self._apply(accept=False)
        self._update_marks()
        self._sync_undo_last()
        self._status("Put back: %s" % ", ".join(names[:3]) if names else "The last change is undone")

    def close_live(self) -> None:
        """Close (live): what is pending is applied first."""
        self._finish_typing()
        if self._live_timer.isActive():
            self._live_timer.stop()
            self._apply(accept=False)
        self.accept()

    def reject(self) -> None:
        if self._live and self._armed:
            self._finish_typing()
        if self._live and self._armed and self._live_timer.isActive():
            self._live_timer.stop()
            self._apply(accept=False)
        super().reject()

    def undo_changes(self) -> None:
        """Put back what the window opened with, and apply it."""
        self._live_timer.stop()
        self._building = True
        try:
            self._set_state(self._opening)
        finally:
            self._building = False
        self._apply(accept=False)
        self._applied = [self._opening]
        self._sync_undo_last()
        self._update_marks()
        self._status("The settings are as they were when this window opened")

    # -- Reset All Settings / Export / Import ------------------------------------------
    def _ask_reset_all(self) -> None:
        from ..messages import confirm

        if confirm(
            self,
            "Reset All Settings",
            "Put every setting on every page back to its default, also the pages and rows "
            "the search hides? Undo Reset puts them back.",
            "Reset All",
        ):
            self.reset_all()

    def reset_all(self) -> None:
        """Reset All Settings, with the values from just before it kept for Undo Reset."""
        self._live_timer.stop()
        before = self._state()
        self.restore_defaults()
        if self._live:
            self._live_timer.stop()
            self._apply(accept=False)
        self._before_reset, self._after_reset = before, self._state()
        self._update_marks()

    def undo_reset(self) -> None:
        """Put back the settings from just before Reset All Settings, and apply them."""
        before = self._before_reset
        if before is None:
            return
        self._live_timer.stop()
        self._building = True
        try:
            self._set_state(before)
        finally:
            self._building = False
        self._before_reset = self._after_reset = None
        if self._live:
            self._apply(accept=False)
        self._update_marks()
        self._status("The settings are as they were before the reset")

    def restore_defaults(self) -> None:
        """Every option of the dialog back to its default (applied like any change).
        Fast runtime display stays as it is (a Plant SCADA setting)."""
        for key, cb in self._flags.items():
            cb.setChecked(bool(OPTION_DEFAULTS.get(key, False)))
        for cb in self._ribbon_boxes.values():
            cb.setChecked(True)
        self._ribbon_field.set_value([])
        self._rich_tips.setChecked(True)
        self._tol.setValue(int(getattr(type(self._w.view), "SMART_GUIDE_TOL", 12)))
        self._sg_outline.setChecked(True)
        self._nudge.setValue(1)
        from .settings import NUDGE_BURST_DEFAULT

        self._nudge_burst.setValue(NUDGE_BURST_DEFAULT)
        self._shift_arrow.setCurrentIndex(max(0, self._shift_arrow.findData("resize")))
        self._genie_pages.setCurrentIndex(max(0, self._genie_pages.findData("ask")))
        self._symbol_pages.setCurrentIndex(max(0, self._symbol_pages.findData("ask")))
        self._template_pages.setCurrentIndex(max(0, self._template_pages.findData("ask")))
        self._disk_reload.setCurrentIndex(max(0, self._disk_reload.findData("ask")))
        self._labels.setCurrentIndex(max(0, self._labels.findData("literal")))
        self._surround.setCurrentIndex(max(0, self._surround.findData(VIEW_DEFAULTS["view/surround"])))
        self._new_colours.defaults()
        self._shared_styles.set_value("")
        from ..recovery import DEFAULT_MINUTES

        self._autosave.setValue(DEFAULT_MINUTES)
        self._chk_transparent.setChecked(False)
        self._transparent.set_pair(0xFFFFFF)
        from ..dialog import picture_import as PI

        self._picture_options.setCurrentIndex(
            max(0, self._picture_options.findData(declared_default("opt/picture_options")))
        )
        self._paste_drawing.setCurrentIndex(max(0, self._paste_drawing.findData("bitmap")))
        self._large_picture.setValue(PI.LARGE_DEFAULT)
        from ..style.theme import APPEARANCE_DEFAULTS

        self._show_appearance(APPEARANCE_DEFAULTS)
        self._show_canvas(lambda key, default: default)
        self._show_canvas_look(lambda key, default: default)
        self._nav.defaults()
        self._rows_defaults()
        from . import keymap

        self._keys.set_state(keymap.DEFAULT_PRESET, {}, emit=False)
        self._sim.defaults()

    def _add_library_folder(self) -> None:
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Extra Library Folder")
        if not path:
            return
        parts = [p for p in self._lib_folders.text().split(";") if p.strip()]
        if os.path.normpath(path) not in (os.path.normpath(p) for p in parts):
            parts.append(os.path.normpath(path))
        self._lib_folders.setText(";".join(parts))

    def _show_canvas(self, get) -> None:
        """The Canvas tab's choices from ``get(key, default)``."""
        self._wheel.setCurrentIndex(1 if get("opt/wheel_scrolls", False) in (True, "true") else 0)
        try:
            self._paste_offset.setValue(int(get("opt/paste_offset", 10)))
        except TypeError, ValueError:  # swallow-ok: a garbled setting shows its default
            self._paste_offset.setValue(10)
        self._stacked.setCurrentIndex(max(0, self._stacked.findData(get("opt/stacked_labels", "top"))))
        self._composite.setCurrentIndex(
            max(0, self._composite.findData(get("opt/composite_genies", "members")))
        )
        self._open_zoom.setCurrentIndex(max(0, self._open_zoom.findData(get("opt/open_zoom", "last"))))
        self._tab_rows.load(get)
        try:
            self._undo_mb.setValue(int(get("opt/undo_mb", 384)))
        except TypeError, ValueError:  # swallow-ok: a garbled setting shows its default
            self._undo_mb.setValue(384)
        self._lib_folders.setText(str(get("opt/library_folders", "") or ""))
        self._codepage.setCurrentIndex(max(0, self._codepage.findData(get("opt/codepage", "auto"))))
        try:
            self._find_procs.setValue(int(get("opt/find_processes", 0)))
        except TypeError, ValueError:  # swallow-ok: a garbled setting shows Automatic
            self._find_procs.setValue(0)
        try:
            self._update_procs.setValue(int(get("opt/update_processes", 0)))
        except TypeError, ValueError:  # swallow-ok: a garbled setting shows Automatic
            self._update_procs.setValue(0)

    def _load(self) -> None:
        """Show the stored settings (after an Import)."""
        st = app_settings()
        for key, cb in self._flags.items():
            cb.setChecked(st.value("opt/" + key, OPTION_DEFAULTS.get(key, False), type=bool))
        self._nudge.setValue(int(st.value("view/nudge_step", 1, type=int)))
        from .settings import nudge_burst_ms

        self._nudge_burst.setValue(nudge_burst_ms())
        self._tol.setValue(int(float(st.value("view/smart_guide_tol", self._tol.value(), type=float))))
        self._sg_outline.setChecked(st.value("view/smart_guide_outline", True, type=bool))
        self._shift_arrow.setCurrentIndex(
            max(0, self._shift_arrow.findData(st.value("opt/shift_arrow", "resize", type=str)))
        )
        from .settings import disk_reload, genie_save_pages, symbol_save_pages, template_save_pages

        self._genie_pages.setCurrentIndex(max(0, self._genie_pages.findData(genie_save_pages())))
        self._symbol_pages.setCurrentIndex(max(0, self._symbol_pages.findData(symbol_save_pages())))
        self._template_pages.setCurrentIndex(max(0, self._template_pages.findData(template_save_pages())))
        self._disk_reload.setCurrentIndex(max(0, self._disk_reload.findData(disk_reload())))
        self._labels.setCurrentIndex(
            max(0, self._labels.findData(st.value("view/label_mode", "literal", type=str)))
        )
        self._surround.setCurrentIndex(max(0, self._surround.findData(surround_mode())))
        self._show_canvas(lambda key, default: st.value(key, default))
        self._show_canvas_look(lambda key, default: st.value(key, default))
        self._nav.show(lambda key, default: st.value(key, default))
        self._sim.load()
        from ..simulate.window import engine_setting

        self._sim.engine.setText(engine_setting())
        self._new_colours.load()
        self._shared_styles.set_value(str(st.value("styles/shared_file", "") or ""))
        from ..recovery import autosave_minutes

        self._autosave.setValue(autosave_minutes())
        from ..dialog.bitmap_import import transparent_colour

        cur = transparent_colour()
        self._chk_transparent.setChecked(cur is not None)
        self._transparent.set_pair(0xFFFFFF if cur is None else cur)
        from ..style.theme import appearance

        self._show_appearance(appearance())
        hidden = {str(x) for x in (st.value("ui/ribbon_hidden_groups", [], type=list) or [])}
        for key, cb in self._ribbon_boxes.items():
            cb.setChecked(key not in hidden)
        self._ribbon_field.set_value(st.value("ui/ribbon_custom", [], type=list) or [])
        self._rows_load()
        from . import keymap

        self._keys.set_state(keymap.preset_name(), keymap.user_keys(), emit=False)

    def _export(self) -> None:
        from .settings_transfer import SettingsTransferDialog

        dlg = SettingsTransferDialog(self, "export")
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        from ..remember import file_path

        path = file_path(
            self,
            "Export Settings",
            "settings",
            "Settings files (*.ini);;All files (*.*)",
            save=True,
            file_name="ctgedit-settings.ini",
        )
        if not path:
            return
        self.export_to(path, dlg.groups())

    def _overrides(self) -> dict:
        """The dialog's choices as settings (with the pending keyboard map)."""
        from . import keymap

        out = self._choices()
        preset, user = self._keys.state()
        out[keymap.KEYMAP_SETTING] = preset
        for cid, ks in user.items():
            out[keymap.KEYS_PREFIX + cid] = keymap.stored_form(cid, ks)
        return out

    def export_to(self, path: str, groups=None) -> bool:
        """Save the settings of ``groups`` (None: all) -- with the dialog's current choices
        -- to ``path``. The choices are not applied or stored by it: Cancel still discards
        them."""
        from .settings_transfer import export_groups, summary

        try:
            n, gs = export_groups(path, groups, overrides=self._overrides())
        except (SettingsFileError, OSError) as e:
            warn(self, "Export Settings", str(e))
            return False
        self._message = "%s To %s." % (summary(n, gs, "Exported"), path)
        self._status(self._message)
        return True

    def _import(self) -> None:
        from .settings_transfer import SettingsTransferDialog, file_groups

        from ..remember import file_path

        path = file_path(
            self, "Import Settings", "settings", "Settings files (*.ini);;All files (*.*)", save=False
        )
        if not path:
            return
        try:
            available = file_groups(path)
        except (SettingsFileError, OSError) as e:
            warn(self, "Import Settings", str(e))
            return
        dlg = SettingsTransferDialog(self, "import", available=available)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        self.import_from(path, dlg.groups(), dlg.import_mode())

    def import_from(self, path: str, groups=None, mode: str = "replace") -> bool:
        """Take ``groups`` (None: all) of ``path``'s settings (``mode`` 'merge' or 'replace')
        and apply them to the window (the dialog stays open, showing them)."""
        from .settings_transfer import import_groups, summary
        from ..style.theme import appearance
        from . import keymap

        looks = appearance()
        # (the rows as stored before the import: the rows it changes run their apply, as a
        # change made here does)
        rows_before = {k: self._row_stored(s) for k, s in self._row_specs.items()}
        toggles = [k for k, s in self._row_specs.items() if s.apply.startswith("action:")]
        toggles_before = {k: _stored(k, declared_default(k)) for k in toggles}
        try:
            n, gs = import_groups(path, groups, mode)
        except (SettingsFileError, OSError) as e:
            warn(self, "Import Settings", str(e))
            return False
        self._building = True
        try:
            self._load()
            # (a View menu toggle the import changed shows the imported value, not this
            # window's own: the apply below gives it to every window and keeps it stored)
            for k in toggles:
                v = _stored(k, declared_default(k))
                if not _same(v, toggles_before[k]):
                    self._set_row_value(k, v)
        finally:
            self._building = False
        self._apply(accept=False, rows_before=rows_before)
        self._apply_appearance(looks)  # an imported theme / text size, now
        keymap.apply_everywhere()  # the imported keys, now
        # the window layout, grid, snap and view toggles now, too: the window
        # writes its own state back when it closes, which would otherwise
        # overwrite the imported one
        restore = getattr(self._w, "restore_session", None)
        if restore is not None:
            try:
                restore(reopen=False)
            except Exception:  # noqa: BLE001 -- the settings are stored either way
                logsetup.note_exception("settings_dialog.import_from/restore")
        self._rows_load()
        self._update_marks()
        self._message = "%s From %s." % (summary(n, gs), os.path.basename(path))
        self._status(self._message)
        return True

    def _status(self, text: str) -> None:
        try:
            self._w.statusBar().showMessage(text, 8000)
        except Exception:  # noqa: BLE001  # swallow-ok: the window may be gone
            pass

    # -- apply ----------------------------------------------------------------------
    def _transparent_value(self) -> Optional[int]:
        if not self._chk_transparent.isChecked():
            return None
        return self._transparent.pair()[0] & 0xFFFFFF

    def _choices(self) -> dict:
        """The dialog's choices as settings keys and values."""
        from ..dialog.bitmap_import import SETTING as TRANSPARENT

        out = {
            "view/nudge_step": self._nudge.value(),
            "opt/nudge_burst_ms": self._nudge_burst.value(),
            "view/smart_guide_tol": float(self._tol.value()),
            "view/smart_guide_outline": self._sg_outline.isChecked(),
            "opt/shift_arrow": self._shift_arrow.currentData(),
            "opt/genie_save_pages": self._genie_pages.currentData(),
            "opt/symbol_save_pages": self._symbol_pages.currentData(),
            "opt/template_save_pages": self._template_pages.currentData(),
            "opt/disk_reload": self._disk_reload.currentData(),
            "view/label_mode": self._labels.currentData(),
            "view/surround": self._surround.currentData(),
            "opt/autosave_minutes": self._autosave.value(),
            "opt/wheel_scrolls": bool(self._wheel.currentData()),
            "opt/paste_offset": self._paste_offset.value(),
            "opt/picture_options": self._picture_options.currentData(),
            "opt/paste_drawing": self._paste_drawing.currentData(),
            "opt/large_picture_kb": self._large_picture.value(),
            "opt/stacked_labels": self._stacked.currentData(),
            "opt/composite_genies": self._composite.currentData(),
            "opt/open_zoom": self._open_zoom.currentData(),
            "opt/undo_mb": self._undo_mb.value(),
            "opt/find_processes": self._find_procs.value(),
            "opt/update_processes": self._update_procs.value(),
            "opt/library_folders": ";".join(
                p.strip() for p in self._lib_folders.text().split(";") if p.strip()
            ),
            "opt/codepage": self._codepage.currentData(),
            "styles/shared_file": self._shared_styles.value(),
            "ui/rich_tooltips": self._rich_tips.isChecked(),
        }
        dev = self._flags["developer_tools"].isChecked()
        for key, cb in self._flags.items():
            out["opt/" + key] = cb.isChecked() and (dev or key != "dyn_badges")
        colour = self._transparent_value()
        out[TRANSPARENT] = "" if colour is None else "%06x" % colour
        out.update(self._appearance_choices())
        out.update(self._canvas_look_choices())
        out.update(self._nav.choices())
        out.update(self._tab_rows.choices())
        out.update(self.ribbon_choices())
        out.update(self._rows_choices())
        out.update(self._new_colours.choices())
        out.update(self._sim.choices())
        return out

    def _store(self) -> None:
        """Persist the dialog's choices (nothing applied to the window)."""
        if not self._flags["developer_tools"].isChecked():
            self._flags["dyn_badges"].setChecked(False)
        st = app_settings()
        for key, value in self._choices().items():
            st.setValue(key, value)

    def _apply_fast_display(self) -> bool:
        """Write Fast runtime display to citect.ini when it changed. Turning
        it on offers to remove the out-of-date .ctF files of the open page's
        project first. False = Cancel (the dialog stays open)."""
        cb = getattr(self, "_fast_display", None)
        if cb is None or not cb.isEnabled() or cb.isChecked() == self._fast_initial:
            return True
        on = cb.isChecked()
        removed, failed = [], []
        if on:
            stale = self._stale_fast_display()
            if stale:
                answer = self.ask_remove_stale(stale)
                if answer == "cancel":
                    if self._live and self._armed:
                        # (a change that applies at once: Cancel takes the tick back)
                        cb.blockSignals(True)
                        cb.setChecked(self._fast_initial)
                        cb.blockSignals(False)
                    return False
                if answer == "remove":
                    for p in stale:
                        try:
                            os.remove(p)
                            removed.append(p)
                        except OSError:
                            failed.append(p)
                    if removed:
                        try:
                            from ...infra import logsetup

                            logsetup.files_written(
                                "removed out-of-date fast-display files", ["removed " + p for p in removed]
                            )
                        except Exception:  # noqa: BLE001  # swallow-ok: the log line is a record only
                            pass
        from ...io import citect_config

        try:
            citect_config.set_fast_display(on)
        except OSError as e:
            warn(self, "Fast Display Files", "The fast display setting was not changed: %s" % e)
            return True
        self._fast_initial = on
        msg = "Fast display files turned %s." % ("on" if on else "off")
        if removed:
            msg += " Removed %d out-of-date .ctF file(s)." % len(removed)
        if failed:
            msg += " Could not remove %d (%s)." % (
                len(failed),
                ", ".join(os.path.basename(p) for p in failed[:3]),
            )
        self._status(msg)
        return True

    def ask_remove_stale(self, stale: list) -> str:
        """'remove' / 'keep' / 'cancel' for the out-of-date .ctF files
        ``stale`` (CtDraw asks to update its pages at this point,
        Yes / No / Cancel)."""
        pdir = os.path.dirname(stale[0])
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Question)
        box.setWindowTitle("Fast Display Files")
        box.setText(
            "%d page(s) of project %s have a fast-display file (.ctF) older than the page. "
            "The runtime would show the old drawings." % (len(stale), os.path.basename(pdir))
        )
        box.setInformativeText(
            "Remove the out-of-date .ctF files now? The runtime then draws those pages from their .ctg."
        )
        yes = box.addButton("Remove Them", QtWidgets.QMessageBox.YesRole)
        box.addButton("Keep Them", QtWidgets.QMessageBox.NoRole)
        box.addButton(QtWidgets.QMessageBox.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is yes:
            return "remove"
        if clicked is box.button(QtWidgets.QMessageBox.Cancel):
            return "cancel"
        return "keep"

    def _stale_fast_display(self) -> list:
        """The out-of-date .ctF files of the open page's project (format/ctf
        .stale_ctf_files; CtDraw updates the pages of its current project)."""
        doc = getattr(self._w, "doc", None)
        path = getattr(doc, "path", None) if doc is not None else None
        if not path or not path.lower().endswith(".ctg"):
            return []
        pdir = os.path.dirname(os.path.abspath(path))
        if not is_project_dir(pdir):
            return []
        try:
            from ...format.ctf import stale_ctf_files

            return stale_ctf_files(pdir)
        except Exception:  # noqa: BLE001 -- the offer is an aid only
            logsetup.note_exception("settings_dialog.stale_ctf_files")
            return []

    def _apply_simulate(self) -> None:
        """The Simulate page's options to the Simulate window when one is open (they are
        stored with the others); a changed ctsim.exe is stored, and the window's tabs
        reconnect to it."""
        engine = self._sim.engine_path()
        changed = engine != self._sim_engine_before
        sw = None
        try:
            from ..simulate.window import simulate_window

            sw = simulate_window(create=False)
        except Exception:  # noqa: BLE001 -- swallow-ok: no Simulate window then
            sw = None
        if sw is not None:
            sw.apply_settings(engine if changed else None)
        elif changed:
            from ...sim import client as C

            st = app_settings()
            if engine:
                st.setValue(C.SETTING, engine)
            else:
                st.remove(C.SETTING)
        self._sim_engine_before = engine

    def _apply_keymap(self) -> None:
        """The Keyboard page's keymap: stored, and given to every editor window when it
        changed."""
        from . import keymap

        preset, user = self._keys.state()
        if preset == keymap.preset_name() and user == keymap.user_keys():
            return
        self._keys.store()
        keymap.apply_everywhere()

    def _apply(self, accept: bool = True, rows_before: Optional[dict] = None) -> None:
        """Store the window's choices and apply them (``rows_before``: the declared rows as
        stored before an import wrote them; None = as stored now)."""
        w = self._w
        if not self._apply_fast_display():
            return
        from ..style.theme import appearance

        looks_before = appearance()
        st_before = app_settings()
        ribbon_before = (
            list(st_before.value("ui/ribbon_hidden_groups", [], type=list) or []),
            list(st_before.value("ui/ribbon_custom", [], type=list) or []),
        )
        if rows_before is None:
            rows_before = {k: self._row_stored(s) for k, s in self._row_specs.items()}
        self._store()
        self._apply_keymap()
        # (what a window keeps on itself reaches every editor window, not only the one this
        # window was opened from: a second window, a window a tab moved to)
        wins = self._windows_with(w)
        rc = self.ribbon_choices()
        if (rc["ui/ribbon_hidden_groups"], rc["ui/ribbon_custom"]) != ribbon_before:
            _each(wins, "apply_ribbon_layout")
        self._apply_appearance(looks_before)
        self._apply_rows(rows_before)
        # the wheel and the canvas's other navigation choices, the smart guides, the arrow-key
        # step, the paste offset and the opening zoom (stored above: each window reads them)
        self._nav.apply(w)
        _each(wins, "apply_canvas_options")
        from ...io import reader as _reader

        drawn_before = (_reader.get_render_option("stacked_labels"), _reader.get_composite_members())
        from .settings import apply_drawing_options, apply_library_folders, apply_undo_budget

        apply_drawing_options()
        apply_undo_budget()
        _each(wins, "_refresh_tabs")  # (the tab close buttons, pinned tabs)
        from ...doc.library import composition_environment

        composed_before = composition_environment()
        if apply_library_folders():
            # (the libraries are looked for again: the pages derive with the new folders)
            from ...infra import caches as _caches

            _caches.clear("libraries")
            drawn_before = None
        if composition_environment() != composed_before:
            # (the open pages' genies composed as the new settings say, in every window)
            _each([w], "_look_at_open_genies_again")
        redraw = drawn_before != (
            _reader.get_render_option("stacked_labels"),
            _reader.get_composite_members(),
        )
        _each(wins, "_update_colour_button")
        from ..panels.styles_gallery import store as _styles

        _styles().reload()
        if self._flags["dyn_badges"].isChecked():
            os.environ["CTG_DYN_BADGES"] = "1"
        else:
            os.environ.pop("CTG_DYN_BADGES", None)
        _each(wins, "_apply_surface_options")
        surround = self._surround.currentData()
        for win in wins:
            # (stored once, by the window Settings was opened from)
            if hasattr(win, "set_surround_mode"):
                try:
                    win.set_surround_mode(surround, store=win is w)
                except RuntimeError:  # swallow-ok: a window being closed
                    continue
        # the canvas look (the grid, the page edge and shadow, the selection colour)
        from ..style import theme as _theme

        _theme.reload_canvas_colours()
        _each(wins, "apply_canvas_look")
        # (the Cicode colours and hints of the open fields follow their options)
        from ..objprops.cicode_assist import refresh_all

        refresh_all()
        _each(wins, "_sync_grid_dialog")  # an open Grid Setup shows the new look
        for win in wins:
            view = getattr(win, "view", None)
            if view is not None:
                try:
                    view.viewport().update()
                except RuntimeError:  # swallow-ok: a window being closed
                    pass
        self._apply_simulate()
        # (the page is derived again only when the label mode or a drawing choice changed,
        # or with OK: a live change of another setting must not redraw a big page)
        labels = self._labels.currentData()
        for win in wins:
            try:
                current_done = False
                if labels != getattr(win, "_label_mode", None) or (accept and win is w):
                    win._set_label_mode(labels)  # re-renders the page
                    current_done = True
                if redraw:
                    _redraw_pages(win, current_done)
            except RuntimeError:  # swallow-ok: a window being closed
                continue
        if redraw:
            self._status("Open pages are drawn again with the new drawing choices")
        if accept:
            self.accept()

    @staticmethod
    def _windows_with(w) -> list:
        """Every editor window, ``w`` (the one Settings was opened from) with them."""
        from ..editor.canvas_options import editor_windows

        return editor_windows(w)
