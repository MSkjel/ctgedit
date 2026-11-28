"""The Objects panel: a filterable tree of the page's objects."""

from __future__ import annotations

from ..names import plural
from ..style.theme import sp as _sp  # (the spacing scale)

import bisect
import os
from typing import NamedTuple

from PySide6 import QtCore, QtGui, QtWidgets

from ...model.document import GraphicObject, ObjectKind, Page
from ...infra.textmatch import FieldQuery

# (at the top: an import inside _row_text runs PySide's import hook once per row)
from ..names import (
    kind_name,
    is_placed_symbol,
    input_badges,
    row_info,
    object_name_of,
    element_label,
    main_binding,
    composite_binding,
    first_tag,
    binding_text,
    chain_lines,
    values_text,
    copy_bindings,
)

try:
    from ..style.theme import icon
except ImportError:  # theme.py may be absent: blank-icon fallback

    def icon(name: str, size: int = 20) -> QtGui.QIcon:  # noqa: ARG001
        return QtGui.QIcon()


# (the rows' padding is the application stylesheet's: it follows Settings > Appearance >
# Density, ui/style/theme.DENSITY)
_QSS = """
QWidget { background: #26292e; font: 12px 'Segoe UI'; color: #d6d9de; }
QLineEdit {
    background: #1f2226; color: #d6d9de; border: 1px solid #6e7681;
    border-radius: 3px; padding: 3px 6px; selection-background-color: #2b4a6f;
}
QLineEdit:focus { border: 1px solid #4da3ff; }
QTreeWidget {
    background: #1f2226; border: 1px solid #3a3f46; border-radius: 3px;
    outline: none;
}
QTreeWidget::item { color: #d6d9de; }
QTreeWidget::item:hover { background: #2c3036; }
QTreeWidget[kbdFocus="true"] { border: 1px solid #4da3ff; }
QTreeWidget::item:selected { background: #2b4a6f; color: #d6d9de; }
QMenu { background: #26292e; color: #d6d9de; border: 1px solid #3a3f46; }
QMenu::item { padding: 4px 18px; }
QMenu::item:selected { background: #2b4a6f; }
QMenu::separator { height: 1px; background: #3a3f46; margin: 3px 6px; }
QToolButton#selectListed { border: none; color: #4da3ff; padding: 0 2px; }
QToolButton#selectListed:hover { text-decoration: underline; }
QToolButton#selectListed[kbdFocus="true"] { border: 1px solid #4da3ff; }
"""

_KIND_ICON = {
    ObjectKind.LINE: "line",
    ObjectKind.RECTANGLE: "rectangle",
    ObjectKind.ELLIPSE: "ellipse",
    ObjectKind.POLYGON: "polygon",
    ObjectKind.PIPE: "pipe",
    ObjectKind.TEXT: "text",
    ObjectKind.NUMBER: "number",
    ObjectKind.BUTTON: "button",
    ObjectKind.SYMBOL: "symbol",
    ObjectKind.SYMBOL_SET: "symbol",
    ObjectKind.GROUP: "group",
    ObjectKind.GENIE: "group",
    ObjectKind.COMPOSITE_GENIE: "group",
}

# Qt's enum values read for every row, looked up once
_USER_ROLE = QtCore.Qt.UserRole
#: the row's search keywords (the name, the bindings, the expressions: not drawn)
KEYWORDS_ROLE = QtCore.Qt.UserRole + 1
_A11Y_ROLE = QtCore.Qt.AccessibleDescriptionRole
_EDITABLE = QtCore.Qt.ItemIsEditable

# what a row is (RowLook.flags)
F_LOCKED = 1  # Edit > Lock (the record's dwFlags 0x4)
F_TEMPLATE = 2  # from the page template
F_RENAME = 4  # its name can be changed here
F_LARGE = 8  # a large picture
F_ROOT = 16  # a top-level row
F_NAMED = 32
F_ANIMATED = 64  # an expression is set
F_INPUT = 128  # touch or keyboard commands
F_PART = 256  # part of a placed genie, symbol or composite genie

#: the kind groups of the filter: (key, label), in the popover's order
FILTER_KINDS = (
    ("genie", "Genies"),
    ("composite", "Composite genies"),
    ("symbol", "Symbols"),
    ("symbol_set", "Symbol sets"),
    ("picture", "Pictures"),
    ("text", "Texts"),
    ("number", "Numbers"),
    ("button", "Buttons"),
    ("shape", "Shapes"),
    ("group", "Groups"),
    ("other", "Other"),
)
#: the state filters: (key, label)
FILTER_STATES = (
    ("hidden", "Hidden"),
    ("locked", "Locked"),
    ("template", "From the template"),
    ("animated", "Animated"),
    ("commands", "Has touch or keyboard commands"),
    ("large", "Large pictures"),
    ("named", "Named"),
    ("selected", "Selected only"),
)
_SHAPES = ("line", "rectangle", "ellipse", "polygon", "pipe")

#: the optional columns (objects/columns): key -> header
COLUMNS = (
    ("object", "Object"),
    ("name", "Name"),
    ("kind", "Kind"),
    ("an", "AN"),
    ("element", "Element"),
    ("tag", "Tag"),
    ("parameters", "Parameters"),
    ("position", "Position"),
)
_COLUMN_KEYS = tuple(k for k, _h in COLUMNS)

#: the tool tips of a refused rename / order / delete
TEMPLATE_TIP = "From the page template: edit the template to change it."
PART_TIP = "Part of a genie: change the genie's definition instead."
PART_RENAME_TIP = "Part of a genie: its name comes from the genie's definition."
SEARCH_TIP = (
    "Matches the name, the kind, the AN, the genie's element and tags, the text and "
    "the expressions. Words: name:Pump, tag:Mixer_, an:581, kind:genie, "
    "lib:valve, is:hidden, is:locked. Enter selects the objects listed. This page only: "
    "Edit > Find and Replace lists each matching field across pages"
)

#: a click on the name of the one selected row starts a rename after this pause (ms): the
#: default of Settings > Appearance > Objects panel > Rename after (objects/rename_click_ms,
#: under A second click on a selected row renames it)
SLOW_CLICK_MS = 600
#: what a double-click on a row does (objects/double_click): the window's request for each
DOUBLE_CLICK = ("zoom", "scroll", "props", "rename")


class RowLook(NamedTuple):
    """What a row shows (:meth:`ObjectsDock._row_look`), compared to set only what changed."""

    text: str  # the row's whole text (screen readers, the search)
    icon: str  # the kind's icon
    tone: str  # '' or the theme role of the text: 'warn', 'text_dim', 'disabled'
    primary: str
    secondary: str
    an: str  # 'AN 786' ('' when not shown)
    flags: int
    kind_key: str  # the filter's kind group
    keywords: str
    tags: str
    lib: str
    columns: tuple  # the values of the optional columns after the first


def _settings():
    from ..prefs.settings import app_settings

    return app_settings()


def _setting(key: str, default):
    try:
        st = _settings()
        if isinstance(default, bool):
            return bool(st.value(key, default, type=bool))
        if isinstance(default, list):
            v = st.value(key, default, type=list)
            return [str(x) for x in (v or ()) if str(x)]
        v = st.value(key, default)
        return default if v is None else str(v)
    except Exception:  # noqa: BLE001 -- swallow-ok: a garbled setting reads as its default
        return default


def _row_text(index: int, obj: GraphicObject, page: Page | None = None) -> str:
    """The classic row text (Settings > Appearance > Objects panel > Row labels > Kind and
    AN first): 'Genie (1)  AN 588 Â· example.pump_b'."""
    suffix = obj.cls or obj.style.text[:20] or obj.genie_template
    suffix = " ".join(suffix.split())
    if suffix.lower().endswith((".<ctt", ".<ctm", ".<ctl")):
        suffix = suffix[:-5]  # (the library reference, without its type mark)
    if "_CompositionCaches" in suffix:
        # a composite genie: its template's name, not its cache path
        from ..names import composite_template_name

        src = getattr(page, "source_path", "") or ""
        suffix = composite_template_name(suffix, os.path.dirname(src) if src else "")
    members, badges = input_badges(page, obj)
    label = kind_name(obj)
    if members:
        label += " (%d)" % members
    base = label  # (the record index: the row's tool tip)
    if obj.an > 0:  # the builder's AN (handle + 1)
        base += "  AN %d" % obj.an
    if badges:
        base += "  " + badges
    pic = picture_text(obj)
    if pic:
        base += "  " + pic
    return "%s Â· %s" % (base, suffix) if suffix else base


def picture_text(obj: GraphicObject) -> str:
    """A picture's pixels and the bytes it takes in the page ('640 x 480, 120 KB'), or ''
    for an object without a picture of its own."""
    n = obj.raw.get("_pic_bytes") or 0
    if not n:
        return ""
    from ..dialog.picture_import import size_text

    bmp = obj.raw.get("_embedded_bmp")
    if obj.raw.get("_metafile"):
        return "drawing, %s" % size_text(n)
    if bmp:
        return "%d x %d, %s" % (bmp[0], bmp[1], size_text(n))
    return size_text(n)


def picture_is_large(obj: GraphicObject, limit: int | None = None) -> bool:
    """``limit`` = picture_import.large_bytes() read once by a caller that asks per row."""
    if limit is None:
        from ..dialog.picture_import import large_bytes

        limit = large_bytes()
    return (obj.raw.get("_pic_bytes") or 0) > limit


def _kind_key(obj: GraphicObject) -> str:
    k = obj.kind.value
    if (obj.raw.get("_pic_bytes") or 0) and k not in ("genie", "composite_genie", "group"):
        return "picture"
    if k == "genie":
        ref = str(obj.raw.get("_ref") or obj.genie_template or "").lower()
        if obj.parent is None and ref.endswith("<ctt"):
            return "other"
        # (a placed group-root symbol is a Symbol, as its row says: names.kind_name)
        return "symbol" if is_placed_symbol(obj) else "genie"
    if k == "composite_genie":
        return "composite"
    if k in _SHAPES:
        return "shape"
    if k in ("symbol", "symbol_set", "text", "number", "button", "group"):
        return k
    return "other"


# --------------------------------------------------------------------------- the rows
class _RowDelegate(QtWidgets.QStyledItemDelegate):
    """Draws a row in two tones with its AN column and its eye and padlock cells, and edits
    its name (a rename, checked as it is typed: a name the Graphics Builder refuses keeps the
    editor open, framed in the danger colour, the reason in its tool tip)."""

    #: an eye / padlock cell and the gap before the AN (px at 100 %); the Touch density's
    #: cell (Settings > Appearance > Density); all of them times the text size
    CELL = 18
    TOUCH_CELL = 24
    GAP = 6

    def __init__(self, dock: "ObjectsDock"):
        super().__init__(dock._tree)
        self._dock = dock
        self._an_w = {}  # font key -> the AN column's width
        self.sync_sizes()

    def sync_sizes(self) -> None:
        """The cell and the gap at the text size and the density (made again by the dock's
        _theme_changed)."""
        from ..style.theme import current_density, px

        self.cell = px(self.TOUCH_CELL if current_density() == "touch" else self.CELL)
        self.gap = px(self.GAP)

    # -- layout -----------------------------------------------------------------------------
    def zones(self, rect: QtCore.QRect, index_i: int, flags: int) -> dict:
        """The eye, padlock, AN and text rectangles of a row's first cell ``rect``."""
        d = self._dock
        h = rect.height()
        c = min(self.cell, max(8, h))  # (never taller than the row)
        out = {}
        top = rect.top() + (h - c) // 2
        left_side = d.toggles_side == "left"
        if left_side:
            x = rect.left() + 1
            out["eye"] = QtCore.QRect(x, top, c, c)
            out["lock"] = QtCore.QRect(x + c, top, c, c) if flags & F_ROOT else None
            text_left = x + 2 * c + 2
            right = rect.right() - 2
        else:
            right_x = rect.right() - 1 - c
            out["eye"] = QtCore.QRect(right_x + 1, top, c, c)
            out["lock"] = QtCore.QRect(right_x + 1 - c, top, c, c) if flags & F_ROOT else None
            text_left = rect.left() + 2
            right = right_x - c - 2
        if d.show_an_column():
            an_w = self._an_width()
            # (a cell too narrow for the text and the AN keeps the text)
            if right - text_left - an_w - self.gap >= 60:
                out["an"] = QtCore.QRect(right - an_w, rect.top(), an_w, h)
                right -= an_w + self.gap
        out["text"] = QtCore.QRect(text_left, rect.top(), max(0, right - text_left), h)
        return out

    def _an_width(self) -> int:
        """The AN column: as wide as the page's largest AN needs ('AN 000' at least)."""
        f = self._dock._tree.font()
        digits = self._dock.an_digits()
        key = (f.family(), f.pixelSize(), f.pointSizeF(), digits)
        w = self._an_w.get(key)
        if w is None:
            w = self._an_w[key] = QtGui.QFontMetrics(f).horizontalAdvance("AN " + "0" * digits)
        return w

    # -- painting ---------------------------------------------------------------------------
    def paint(self, painter, option, index) -> None:  # noqa: C901 -- one row, in order
        d = self._dock
        idx = index.data(_USER_ROLE)
        look = d._looks.get(idx) if idx is not None else None
        if look is None:
            super().paint(painter, option, index)
            return
        from ..style.theme import COLORS

        opt = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        widget = opt.widget
        style = widget.style() if widget is not None else QtWidgets.QApplication.style()
        # the background (hover, selection) as the style draws it
        style.drawPrimitive(QtWidgets.QStyle.PE_PanelItemViewItem, opt, painter, widget)
        selected = bool(opt.state & QtWidgets.QStyle.State_Selected)
        hovered = bool(opt.state & QtWidgets.QStyle.State_MouseOver)
        if index.column() > 0:
            painter.save()
            painter.setPen(QtGui.QColor(COLORS["sel_text_dim" if selected else "text_dim"]))
            r = opt.rect.adjusted(4, 0, -4, 0)
            painter.drawText(
                r,
                QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft,
                opt.fontMetrics.elidedText(opt.text, QtCore.Qt.ElideRight, r.width()),
            )
            painter.restore()
            return
        hidden = idx in d._hidden
        covered = hidden or idx in d._cover
        z = self.zones(opt.rect, idx, look.flags)
        painter.save()
        try:
            # the kind's icon
            text_r = z["text"]
            ic = opt.icon
            isz = opt.decorationSize.width() if opt.decorationSize.isValid() else 16
            if not ic.isNull():
                ir = QtCore.QRect(text_r.left(), opt.rect.top() + (opt.rect.height() - isz) // 2, isz, isz)
                if covered:
                    painter.setOpacity(0.5)
                ic.paint(painter, ir)
                painter.setOpacity(1.0)
                text_r = text_r.adjusted(isz + 4, 0, 0, 0)
            # the text in two tones
            base = QtGui.QFont(opt.font)
            if covered:
                base.setItalic(True)
            if selected:
                fg, dim = COLORS["sel_text"], COLORS["sel_text_dim"]
            else:
                fg, dim = COLORS["text"], COLORS["text_dim"]
            if covered:
                fg = dim
            elif look.tone and not selected:
                fg = COLORS.get(look.tone, fg)
            q = d._query
            self._draw_two_tone(
                painter, text_r, base, look.primary, look.secondary, QtGui.QColor(fg), QtGui.QColor(dim), q
            )
            if "an" in z and look.an:
                painter.setFont(base)
                painter.setPen(QtGui.QColor(dim))
                painter.drawText(z["an"], QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight, look.an)
            self._draw_toggles(painter, z, look, idx, hidden, covered, hovered or selected)
        finally:
            painter.restore()

    def _draw_two_tone(self, painter, rect, font, primary, secondary, fg, dim, query) -> None:
        bold = QtGui.QFont(font)
        bold.setBold(True)
        fm, fmb = QtGui.QFontMetrics(font), QtGui.QFontMetrics(bold)
        gap = fm.horizontalAdvance("  ")
        p_runs = self._runs(primary, query)
        s_runs = self._runs(secondary, query) if secondary else []
        pw = sum((fmb if b else fm).horizontalAdvance(t) for t, b in p_runs)
        avail = rect.width()
        x = rect.left()
        y = rect.top()
        h = rect.height()
        x = self._draw_runs(painter, x, y, h, min(pw, avail), p_runs, font, bold, fm, fmb, fg)
        rest = rect.right() - x - gap
        if s_runs and rest > fm.horizontalAdvance("xâ€¦"):
            self._draw_runs(painter, x + gap, y, h, rest, s_runs, font, bold, fm, fmb, dim)

    @staticmethod
    def _runs(text: str, query) -> list:
        """[(part, bold)] of ``text``: the search's matches bold."""
        if not text:
            return []
        spans = query.spans(text) if query is not None and not query.free.empty else []
        if not spans:
            return [(text, False)]
        out, at = [], 0
        for s, e in spans:
            if s > at:
                out.append((text[at:s], False))
            out.append((text[s:e], True))
            at = e
        if at < len(text):
            out.append((text[at:], False))
        return out

    @staticmethod
    def _draw_runs(painter, x, y, h, width, runs, font, bold, fm, fmb, colour) -> int:
        """Draw ``runs`` from ``x`` in ``width`` (the last one that does not fit is cut with
        'â€¦'); the x after them."""
        painter.setPen(colour)
        end = x + width
        for text, b in runs:
            m = fmb if b else fm
            w = m.horizontalAdvance(text)
            painter.setFont(bold if b else font)
            if x + w > end:
                cut = m.elidedText(text, QtCore.Qt.ElideRight, max(0, end - x))
                painter.drawText(
                    QtCore.QRect(x, y, max(0, end - x), h), QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, cut
                )
                return end
            painter.drawText(QtCore.QRect(x, y, w + 1, h), QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, text)
            x += w
        return x

    def _draw_toggles(self, painter, z, look, idx, hidden, covered, hot) -> None:
        d = self._dock
        always = d.eye_mode == "always"
        eye = z.get("eye")
        if eye is not None:
            if hidden:
                painter.setOpacity(1.0)
                icon("eye_off").paint(painter, eye.adjusted(1, 1, -1, -1))
            elif hot or always:
                # (quieter than a hidden row's eye, which stays at full strength)
                painter.setOpacity(0.45 if covered else (0.75 if hot else 0.55))
                icon("eye").paint(painter, eye.adjusted(1, 1, -1, -1))
        lock = z.get("lock")
        if lock is not None:
            if look.flags & F_TEMPLATE:
                painter.setOpacity(0.45)
                icon("lock").paint(painter, lock.adjusted(1, 1, -1, -1))
            elif look.flags & F_LOCKED:
                painter.setOpacity(1.0)
                icon("lock").paint(painter, lock.adjusted(1, 1, -1, -1))
            elif hot or always:
                painter.setOpacity(0.75 if hot else 0.55)
                icon("unlock").paint(painter, lock.adjusted(1, 1, -1, -1))
        painter.setOpacity(1.0)

    # -- the rename editor --------------------------------------------------------------------
    def createEditor(self, parent, option, index):  # noqa: N802 -- Qt naming
        ed = QtWidgets.QLineEdit(parent)
        ed.setPlaceholderText("Object name")
        ed.setAccessibleName("Object name")
        ed.setFrame(True)
        idx = index.data(_USER_ROLE)
        ed.setProperty("ctgIndex", idx)
        ed.textEdited.connect(self._dock._check_name_edit)
        return ed

    def setEditorData(self, editor, index) -> None:  # noqa: N802
        idx = index.data(_USER_ROLE)
        editor.setText(self._dock.object_name(idx))
        editor.selectAll()

    def setModelData(self, editor, model, index) -> None:  # noqa: N802
        idx = index.data(_USER_ROLE)
        name = editor.text().strip()
        if idx is None or name == self._dock.object_name(idx):
            return
        self._dock.renameRequested.emit(idx, name)

    def updateEditorGeometry(self, editor, option, index) -> None:  # noqa: N802
        idx = index.data(_USER_ROLE)
        look = self._dock._looks.get(idx)
        r = (
            self.zones(option.rect, idx, look.flags if look else 0)["text"]
            if look is not None
            else option.rect
        )
        isz = option.decorationSize.width() if option.decorationSize.isValid() else 16
        editor.setGeometry(r.adjusted(isz + 2, 0, 0, 0))

    def eventFilter(self, editor, event) -> bool:  # noqa: N802
        t = event.type()
        if (
            isinstance(editor, QtWidgets.QLineEdit)
            and t == QtCore.QEvent.KeyPress
            and event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter)
        ):
            problem = self._dock._name_problem_of(editor)
            if problem:
                self._dock._show_name_problem(editor, problem)
                return True  # the editor stays open
        if isinstance(editor, QtWidgets.QLineEdit) and t == QtCore.QEvent.FocusOut:
            problem = self._dock._name_problem_of(editor)
            if problem:
                # (a name that cannot be kept: dropped when the focus leaves)
                self._dock.statusMessage.emit("Not renamed: %s" % problem)
                self.closeEditor.emit(editor, QtWidgets.QAbstractItemDelegate.RevertModelCache)
                return True
        return super().eventFilter(editor, event)


class _ObjectsTree(QtWidgets.QTreeWidget):
    """The tree: the eye and padlock cells take their clicks (a drag across the eyes sets
    every row it crosses as the first; Alt+click on an eye shows that object alone), a slow
    second click on the selected row's name renames it, the panel's keys (Enter, Shift+Enter,
    Space, F2, Ctrl+A) and the tool tips made when asked for."""

    def __init__(self, dock: "ObjectsDock"):
        super().__init__(dock)
        self._dock = dock
        self._eye_drag = None  # (hidden state set, rows done)
        self._press = None  # (index, was selected alone, pos) for a slow click
        self._rename_timer = QtCore.QTimer(self)
        self._rename_timer.setSingleShot(True)
        self._rename_timer.timeout.connect(self._slow_click_rename)
        self._rename_at = None
        from ..editor.viewnav import SpacePanForwarder

        self.setProperty(SpacePanForwarder.KEEP_SPACE_PROP, self.KEEPS_SPACE)

    # -- where a press lands ----------------------------------------------------------------
    def zone_at(self, pos: QtCore.QPoint):
        """(page index, 'eye' / 'lock' / 'text' / None) under viewport point ``pos``."""
        ix = self.indexAt(pos)
        if not ix.isValid():
            return None, None
        ix0 = ix.siblingAtColumn(0)
        idx = ix0.data(_USER_ROLE)
        look = self._dock._looks.get(idx)
        if look is None:
            return idx, None
        if ix.column() != 0:
            return idx, "text"
        z = self.itemDelegate().zones(self.visualRect(ix0), idx, look.flags)
        for name in ("eye", "lock"):
            r = z.get(name)
            if r is not None and r.adjusted(-1, -2, 1, 2).contains(pos):
                return idx, name
        if z["text"].contains(pos):
            return idx, "text"
        return idx, None

    def mousePressEvent(self, e) -> None:  # noqa: N802 -- Qt naming
        self._rename_timer.stop()
        if e.button() == QtCore.Qt.LeftButton:
            idx, zone = self.zone_at(e.position().toPoint())
            if zone in ("eye", "lock") and idx is not None:
                d = self._dock
                if zone == "eye":
                    if e.modifiers() & QtCore.Qt.AltModifier:
                        d.soloRequested.emit(idx)
                    else:
                        state = idx not in d._hidden
                        self._eye_drag = (state, {idx})
                        d.hideToggled.emit(idx, state)
                else:
                    d._lock_clicked(idx)
                e.accept()
                return
            sel = self.selectedItems()
            alone = len(sel) == 1 and sel[0].data(0, _USER_ROLE) == idx
            self._press = (
                idx,
                alone
                and zone == "text"
                and not (e.modifiers() & (QtCore.Qt.ShiftModifier | QtCore.Qt.ControlModifier)),
                e.position().toPoint(),
            )
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._eye_drag is not None:
            # (the row under the pointer, wherever it is across the row)
            ix = self.indexAt(e.position().toPoint())
            if not ix.isValid():
                ix = self.indexAt(QtCore.QPoint(self.viewport().width() // 2, int(e.position().y())))
            if ix.isValid():
                idx = ix.siblingAtColumn(0).data(_USER_ROLE)
                state, done = self._eye_drag
                if idx is not None and idx not in done:
                    done.add(idx)
                    if (idx in self._dock._hidden) != state:
                        self._dock.hideToggled.emit(idx, state)
            e.accept()
            return
        if self._press is not None and (e.position().toPoint() - self._press[2]).manhattanLength() > 4:
            self._press = None
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if self._eye_drag is not None:
            self._eye_drag = None
            e.accept()
            return
        press, self._press = self._press, None
        super().mouseReleaseEvent(e)
        if press is not None and press[1] and e.button() == QtCore.Qt.LeftButton and self._dock.rename_click:
            idx, zone = self.zone_at(e.position().toPoint())
            if idx == press[0] and zone == "text":
                self._rename_at = idx
                self._rename_timer.start(self._dock.rename_click_ms)

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        self._rename_timer.stop()
        # (the release after a double-click is no slow second click: Qt sends the second
        # press first, which took the row as selected alone)
        self._press = None
        _idx, zone = self.zone_at(e.position().toPoint())
        if zone in ("eye", "lock"):
            e.accept()  # (two quick clicks on an eye: two toggles)
            self.mousePressEvent(e)
            return
        super().mouseDoubleClickEvent(e)

    def _slow_click_rename(self) -> None:
        idx, self._rename_at = self._rename_at, None
        sel = self.selectedItems()
        if idx is not None and len(sel) == 1 and sel[0].data(0, _USER_ROLE) == idx:
            self._dock.start_rename(idx)

    # -- keys -----------------------------------------------------------------------------------
    _OWN_KEYS = (QtCore.Qt.Key_F2, QtCore.Qt.Key_Space, QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter)
    #: Space is the tree's own key (it hides the selection): the Space pan of the canvas
    #: under the pointer leaves it alone (viewnav.SpacePanForwarder.KEEP_SPACE_PROP)
    KEEPS_SPACE = True

    def _own_key(self, e) -> bool:
        """The keys the panel takes while it has the focus (not the window's commands)."""
        if self.state() == QtWidgets.QAbstractItemView.EditingState:
            return False
        mods = e.modifiers() & ~QtCore.Qt.KeypadModifier
        k = e.key()
        if k == QtCore.Qt.Key_A and mods == QtCore.Qt.ControlModifier:
            return True
        if k in self._OWN_KEYS and mods in (QtCore.Qt.NoModifier, QtCore.Qt.ShiftModifier):
            return k != QtCore.Qt.Key_F2 or mods == QtCore.Qt.NoModifier
        return False

    def event(self, e) -> bool:  # noqa: N802
        if e.type() == QtCore.QEvent.ShortcutOverride and self._own_key(e):
            e.accept()  # (F2 renames here, Ctrl+A selects the rows listed)
            return True
        return super().event(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if not self._own_key(e):
            super().keyPressEvent(e)
            return
        d = self._dock
        k = e.key()
        shift = bool(e.modifiers() & QtCore.Qt.ShiftModifier)
        e.accept()
        if k == QtCore.Qt.Key_A:
            d.select_listed()
        elif k == QtCore.Qt.Key_F2:
            d.start_rename()
        elif k == QtCore.Qt.Key_Space:
            d.hideSelectionRequested.emit()
        elif shift:
            d.select_parent()
        else:
            d.activate_current()

    # -- tool tips, made when one is asked for ---------------------------------------------
    def show_tip(self, e) -> bool:
        """The tool tip of the row and the cell under a ToolTip event (the eye, the padlock,
        the row): also while the panel shows columns, whose header helper would show the cut
        cell text instead (ObjectsDock.eventFilter runs this before it)."""
        pos = e.pos()
        idx, zone = self.zone_at(pos)
        tip = self._dock.tip_for(idx, zone) if idx is not None else ""
        if zone == "text":
            # (a cut cell of an optional column, the Tag column say: its whole text first,
            # as every table shows it)
            from .. import tables

            ix = self.indexAt(pos)
            text = ix.data(QtCore.Qt.DisplayRole) if ix.isValid() else None
            text = "" if text is None else str(text)
            if text and text not in tip and tables.is_cut(self, ix, text):
                tip = text + ("\n" + tip if tip else "")
        if tip:
            QtWidgets.QToolTip.showText(
                e.globalPos(), tip, self.viewport(), self.visualRect(self.indexAt(pos))
            )
        else:
            QtWidgets.QToolTip.hideText()
            e.ignore()
        return True

    def viewportEvent(self, e) -> bool:  # noqa: N802
        if e.type() == QtCore.QEvent.ToolTip:
            return self.show_tip(e)
        return super().viewportEvent(e)


class _FilterPopover(QtWidgets.QFrame):
    """The Objects panel's filter: kinds and states with their counts, Clear Filters."""

    def __init__(self, dock: "ObjectsDock"):
        super().__init__(dock, QtCore.Qt.Popup)
        # (one per opening: it goes when it closes, none piles up under the dock)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        self.setObjectName("objectsFilter")
        self.setStyleSheet(
            "#objectsFilter { background: #23262b; border: 1px solid #3a3f46; border-radius: 6px; }"
        )
        self._dock = dock
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(_sp(2), _sp(2), _sp(2), _sp(2))
        lay.setSpacing(_sp(0))
        self.boxes = {}
        counts = dock.filter_counts()
        on = set(dock.filters())
        from ..style.theme import font

        for title, prefix, rows in (("Kinds", "kind:", FILTER_KINDS), ("States", "state:", FILTER_STATES)):
            head = QtWidgets.QLabel(title, self)
            head.setFont(font("body_strong"))
            lay.addWidget(head)
            for key, label in rows:
                n = counts.get(prefix + key, 0)
                cb = QtWidgets.QCheckBox("%s (%d)" % (label, n), self)
                cb.setChecked(prefix + key in on)
                cb.setEnabled(bool(n) or prefix + key in on)
                cb.toggled.connect(lambda checked, k=prefix + key: dock.set_filter(k, checked))
                self.boxes[prefix + key] = cb
                lay.addWidget(cb)
            lay.addSpacing(6)
        clear = QtWidgets.QPushButton("Clear Filters", self)
        clear.setAutoDefault(False)
        clear.setEnabled(bool(on))
        clear.clicked.connect(self._clear)
        self.clear_button = clear
        lay.addWidget(clear, 0, QtCore.Qt.AlignLeft)

    def _clear(self) -> None:
        self._dock.clear_filters()
        for cb in self.boxes.values():
            blocked = cb.blockSignals(True)
            cb.setChecked(False)
            cb.blockSignals(blocked)
        self.clear_button.setEnabled(False)

    def open_at(self, widget: QtWidgets.QWidget) -> None:
        self.adjustSize()
        pos = widget.mapToGlobal(QtCore.QPoint(max(0, widget.width() - self.width()), widget.height() + 2))
        screen = QtGui.QGuiApplication.screenAt(pos)
        if screen is not None:
            g = screen.availableGeometry()
            pos.setX(max(g.left(), min(pos.x(), g.right() - self.width())))
            pos.setY(min(pos.y(), g.bottom() - self.height()))
        self.move(pos)
        self.show()


def _rows_complete(fn):
    """A method that reads the rows: a large page's rows still being made in batches are
    all made first (ObjectsDock.set_page), so it never sees a part of them."""
    import functools

    @functools.wraps(fn)
    def run(self, *a, **k):
        if self.__dict__.get("_pending_rows") is not None:
            self._finish_rows()
        return fn(self, *a, **k)

    return run


class ObjectsDock(QtWidgets.QWidget):
    """Object explorer for the open page: search, filter, select, rename, hide, lock."""

    selectionChanged = QtCore.Signal(list)
    deleteRequested = QtCore.Signal(list)
    zOrderRequested = QtCore.Signal(list, str)
    hideToggled = QtCore.Signal(int, bool)  # row index, hidden
    frameRequested = QtCore.Signal(int)  # double-click / F = frame
    revealRequested = QtCore.Signal(int)  # double-click (Scrolls to it): into view
    propertiesRequested = QtCore.Signal(int)  # the object's own sheet (a member's Enter)
    genieRequested = QtCore.Signal(int)  # the genie's parameters
    activated = QtCore.Signal(int)  # Enter on a top-level row (a double-click's
    # action on the canvas)
    renameRequested = QtCore.Signal(int, str)  # index, new name ('' = no name)
    lockToggled = QtCore.Signal(int, bool)  # root index, locked
    soloRequested = QtCore.Signal(int)  # Alt+click on an eye
    hideSelectionRequested = QtCore.Signal()  # Space
    statusMessage = QtCore.Signal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setStyleSheet(_QSS)
        self._guard = False
        self._items: dict[int, QtWidgets.QTreeWidgetItem] = {}
        # the model object each row shows (update_units keeps a moved unit's
        # rows expanded by object, not by the index it no longer has)
        self._row_obj: dict[int, GraphicObject] = {}
        # the order the rows were made in (the current row of a selection is
        # the first made; _select_indices)
        self._rank: dict[int, int] = {}
        self._made = 0
        # what each row shows (_row_look) and its parent row's index: an edit that keeps
        # the rows sets only what changed (_patch_rows)
        self._looks: dict[int, RowLook] = {}
        self._row_parent: dict[int, int | None] = {}
        # editor-only hidden rows (the window's set) and every row under one of them
        self._hidden: set[int] = set()
        self._cover: set[int] = set()
        self._query = None  # the search as it filters now (bold matches)
        self._actions: dict = {}
        #: (index) -> True when its unit can be selected (Ctrl+A, Select N): the window's
        #: lock rule; None = not locked and not from the template
        self.selectable = None
        #: a large page's rows still to be made (set_page): (generation, page, roots,
        #: selection); None when every row is made
        self._pending_rows = None
        self._rows_gen = 0
        #: fn() -> a key that changes whenever ``selectable`` could answer differently (the
        #: window's units key: its objects first, compared by identity); None = asked always
        self.selectable_key = None
        #: (index, name) -> why the name cannot be given ('' = it can): the window's
        #: document check (object_name_problem)
        self.name_problem = None
        #: (index) -> why the object cannot be renamed now ('' = it can): the window's lock
        #: rule (a locked unit, outside Break Lock Mode)
        self.rename_blocked = None
        self._read_view_settings()
        # (the filter of the last session, unless Settings says the panel starts unfiltered)
        self._filter_keys = (
            [k for k in _setting("objects/filter", []) if k.split(":", 1)[0] in ("kind", "state")]
            if _setting("objects/keep_filter", True)
            else []
        )

        from ..style.theme import SearchField

        self._search = SearchField(
            "Search objects, tags, names", self, tip=SEARCH_TIP, regex_key="Search objects"
        )
        self._filter_action = QtGui.QAction(icon("filter"), "Filter", self._search)
        self._filter_action.setObjectName("objects.filter")
        self._filter_action.setToolTip("Filter by kind and state")
        self._filter_action.triggered.connect(self.open_filter)
        self._search.addAction(self._filter_action, QtWidgets.QLineEdit.TrailingPosition)
        self._search.returnPressed.connect(self._select_from_search)

        self._tree = _ObjectsTree(self)
        self._tree.setObjectName("objectsTree")
        # (no name of its own: a screen reader names it after its panel, accessibility.py)
        self._tree.setHeaderHidden(True)
        self._tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self._tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self._tree.setUniformRowHeights(True)
        self._tree.setMouseTracking(True)
        self._tree.viewport().setAttribute(QtCore.Qt.WA_Hover, True)
        # (a rename starts with F2, the slow second click or Renameâ€¦: no double-click edit)
        self._tree.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._delegate = _RowDelegate(self)
        self._tree.setItemDelegate(self._delegate)
        self._sync_row_sizes()
        self._apply_columns()

        # the search's count and 'Select N'
        self._count_row = QtWidgets.QWidget(self)
        cl = QtWidgets.QHBoxLayout(self._count_row)
        cl.setContentsMargins(_sp(0), 0, _sp(0), 0)
        cl.setSpacing(_sp(1))
        from ..style.theme import note_label, font

        self._count = note_label("", "text_dim", self._count_row)
        self._count.setWordWrap(False)
        self._count.setFont(font("caption"))
        self._select_btn = QtWidgets.QToolButton(self._count_row)
        self._select_btn.setObjectName("selectListed")
        self._select_btn.setAutoRaise(True)
        self._select_btn.setToolTip("Select the objects listed (Enter in the search)")
        self._select_btn.setFont(font("caption"))
        self._select_btn.clicked.connect(self.select_listed)
        cl.addWidget(self._count, 1)
        cl.addWidget(self._select_btn)
        self._count_row.hide()

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(_sp(1), _sp(1), _sp(1), _sp(1))
        lay.setSpacing(_sp(1))
        lay.addWidget(self._search)
        lay.addWidget(self._count_row)
        lay.addWidget(self._tree, 1)

        self._search.searchChanged.connect(self._apply_filter)
        # (no Ctrl+F of its own: in the editor window that is Edit > Find)
        self._search.attach(self._tree)
        # (after the search's filter: F frames the selection before typing goes to the search)
        self._tree.installEventFilter(self)
        self._tree.itemSelectionChanged.connect(self._on_selection_changed)
        self._tree.customContextMenuRequested.connect(self._context_menu)
        self._tree.itemDoubleClicked.connect(self._on_double_click)
        # a search may have hidden rows (_apply_filter): set_page and attach_rows skip the
        # walk over every row when it did not and the search is empty
        self._filtered = False
        # the rows expanded before a search opened the paths to its matches (put back when
        # it is cleared)
        self._expanded_memo = None
        self._show_filter_mark()

        # Delete is the window's Edit > Delete (it acts on the selection the
        # tree mirrors); a second tree-local shortcut would make the key ambiguous
        # and nothing would be deleted

    # -- settings ---------------------------------------------------------------------------
    def _read_view_settings(self) -> None:
        self.label_mode = _setting("objects/row_label", "smart")
        self.show_an = _setting("objects/show_an", True)
        self.toggles_side = _setting("objects/toggles_side", "right")
        from ..prefs.settings import SETTINGS_KEYS

        self.eye_mode = _setting("ui/tree_eye", SETTINGS_KEYS["ui/tree_eye"])
        cols = [c for c in _setting("objects/columns", ["object"]) if c in _COLUMN_KEYS]
        self.columns = ["object"] + [c for c in cols if c != "object"]
        # the slow second click, the double-click, the tree following a selection made
        # elsewhere
        self.rename_click = _setting("objects/rename_click", True)
        # (the pause before the rename, within its span)
        from ..prefs.settings import RENAME_CLICK_RANGE, whole

        self.rename_click_ms = whole("objects/rename_click_ms", RENAME_CLICK_RANGE)
        dbl = _setting("objects/double_click", "zoom")
        self.double_click = dbl if dbl in DOUBLE_CLICK else "zoom"
        self.follow_selection = _setting("objects/follow_selection", True)

    def show_an_column(self) -> bool:
        """The AN drawn at the right of the row (not in classic labels or its own column)."""
        return self.show_an and self.label_mode != "classic" and "an" not in self.columns

    def an_digits(self) -> int:
        """The digits of the largest AN of the page shown (at least 3): the AN column's
        width. Counted once per page and length (the delegate asks for every row)."""
        page = getattr(self, "_shown_page", None)
        if page is None:
            return 4
        key = (id(page), len(page.objects))
        if self.__dict__.get("_an_digits_key") != key:
            top = max((o.an for o in page.objects), default=0)
            self._an_digits_key = key
            self._an_digits_n = max(3, len(str(max(0, top))))
        return self._an_digits_n

    def apply_settings(self) -> None:
        """Settings > Appearance > Objects panel changed: the rows again."""
        before = (self.label_mode, self.show_an, tuple(self.columns))
        self._read_view_settings()
        self._apply_columns()
        page = getattr(self, "_shown_page", None)
        if before != (self.label_mode, self.show_an, tuple(self.columns)) and page is not None:
            self.set_page(page, self._selected_indices(), hidden=self._hidden)
        self._tree.viewport().update()

    def set_columns(self, columns) -> None:
        """The optional columns shown (objects/columns): keys of :data:`COLUMNS`."""
        cols = ["object"] + [c for c in _COLUMN_KEYS if c != "object" and c in set(columns)]
        try:
            _settings().setValue("objects/columns", cols)
        except Exception:  # noqa: BLE001 -- swallow-ok: not remembered then
            pass
        if cols != self.columns:
            # (widths dragged for other columns do not fit these: fitted afresh)
            from .. import tables

            tables.forget_widths(self._tree, self.COLUMNS_KEY)
        self.apply_settings()

    def _apply_columns(self) -> None:
        tree = self._tree
        heads = dict(COLUMNS)
        n = len(self.columns)
        if tree.columnCount() != n:
            tree.setColumnCount(n)
        tree.setHeaderLabels([heads[c] for c in self.columns])
        tree.setHeaderHidden(n == 1)
        hdr = tree.header()
        if n > 1:
            from .. import tables

            # the optional columns fit their text (measured from the rows' text, up to the
            # Settings > Appearance > Tables share of the panel each), the Object column
            # what is left (at least the Tables least width: the others give way), the
            # panel never scrolls sideways (tables.make_resizable fit_view). The widths the
            # user sets are remembered and give way the same when the panel is narrower.
            text = set(range(1, n))
            state = tables.state_of(tree)
            if state is None:
                state = tables.make_resizable(
                    tree, self.COLUMNS_KEY, stretch=0, fit_view=True, text_columns=text
                )
                hdr.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
                hdr.customContextMenuRequested.connect(self._header_menu)
                # (after the helper's: this filter sees the tool tips first)
                tree.viewport().installEventFilter(self)
            state.text_columns = text
            state.schedule()

    #: the settings key of the columns' widths (tables.make_resizable)
    COLUMNS_KEY = "ObjectsDock/objects"

    def _columns_menu(self, menu: QtWidgets.QMenu) -> None:
        heads = dict(COLUMNS)
        for key in _COLUMN_KEYS[1:]:
            a = menu.addAction(heads[key])
            a.setObjectName("tree.column.%s" % key)
            a.setCheckable(True)
            a.setChecked(key in self.columns)
            a.toggled.connect(
                lambda on, k=key: self.set_columns([c for c in self.columns if c != k] + ([k] if on else []))
            )

    def _header_menu(self, pos) -> None:
        from .. import tables

        menu = QtWidgets.QMenu(self)
        self._columns_menu(menu)
        tables.add_fit_item(menu, self._tree)
        menu.exec(self._tree.header().mapToGlobal(pos))
        menu.deleteLater()

    #: the kind icon of a row (px at 100 %), and at the Touch density
    ROW_ICON = 16
    TOUCH_ROW_ICON = 20

    def _sync_row_sizes(self) -> None:
        """The kind icons and the eye and padlock cells at the text size and the density
        (Settings > Appearance)."""
        from ..style.theme import current_density, px

        n = px(self.TOUCH_ROW_ICON if current_density() == "touch" else self.ROW_ICON)
        if self._tree.iconSize() != QtCore.QSize(n, n):
            self._tree.setIconSize(QtCore.QSize(n, n))
        self._delegate.sync_sizes()

    def _theme_changed(self) -> None:
        """A theme change (theme._retheme_widgets): the rows, the AN column and the filter
        button are drawn in the new colours, the icons and cells at the new text size and
        density."""
        self._delegate._an_w.clear()
        self._sync_row_sizes()
        self._show_filter_mark()
        self._tree.viewport().update()

    # -- public API ---------------------------------------------------------
    def set_actions(self, actions: dict) -> None:
        """The window's commands the right-click menu is built from (once): names ->
        QAction, or a callable(index, menu) -> QAction | None made when the menu opens
        (Edit Definition). :data:`MENU` has the names and their order."""
        self._actions = dict(actions or {})

    def set_page(self, page: Page | None, selection: list, hidden: set | None = None) -> None:
        # the (document, derive) a caller tracks what the tree shows by
        # (MainWindow._refresh); any other page shown here invalidates it
        self.shown = None
        self._shown_page = page
        # (no page: nothing to search; the start page has New and Open)
        self._search.setVisible(page is not None)
        self._large_limit = None  # (the large-picture limit read once per build)
        self._guard = True
        # batch the (whole-tree) rebuild: suppressing per-item viewport updates while populating
        # is a large speedup -- this runs on every edit's _refresh.
        self._tree.setUpdatesEnabled(False)
        try:
            self._set_hidden_state(hidden, page, rows=False)
            self._tree.clear()
            self._items.clear()
            self._row_obj.clear()
            self._rank.clear()
            self._looks.clear()
            self._row_parent.clear()
            self._rows_gen += 1
            self._pending_rows = None
            if page is not None:
                # the rows are made outside the tree and added at once: a row made in it
                # is a row inserted into the model and an itemChanged per text, tool tip
                # and icon
                roots = [r for r in page.roots() if not page.objects[r].raw.get("_non_drawable")]
                if len(roots) > self.BATCH_FROM and not self._active():
                    # a large page: its first rows now, the rest on the next turns; whatever
                    # reads the rows meanwhile has them all made first (_rows_complete)
                    self._add_rows(page, roots[: self.BATCH_ROWS])
                    self._pending_rows = (self._rows_gen, page, roots[self.BATCH_ROWS :], list(selection))
                    QtCore.QTimer.singleShot(0, self, lambda g=self._rows_gen: self._rows_step(g))
                    return
                self._add_rows(page, roots)
            if self._active():
                self._apply_filter(self._search.text())
            else:
                # (new rows all show and none is expanded: an empty search changes nothing)
                self._filtered = False
                self._expanded_memo = None
                self._show_count(None)
            # (Settings 'Show the objects selected on the page' off: the tree is not opened
            # to the selection on a rebuild either)
            self._select_indices(selection, follow=self.follow_selection)
        finally:
            self._tree.setUpdatesEnabled(True)
            self._guard = False

    #: from this many top-level rows the rows of a page are made in batches
    BATCH_FROM = 1500
    #: the rows of one batch (one turn of the event loop)
    BATCH_ROWS = 500

    def _add_rows(self, page, roots) -> None:
        tops = []
        for r in roots:
            self._add_item(page, r, None)
            item = self._items.get(r)
            if item is not None:
                tops.append(item)
        self._tree.invisibleRootItem().addChildren(tops)

    def _rows_step(self, gen: int) -> None:
        """The next batch of a large page's rows (nothing when that page is no longer the
        one shown: another set_page, a detach)."""
        pend = self._pending_rows
        if pend is None or pend[0] != gen:
            return
        _gen, page, rest, selection = pend
        self._guard = True
        self._tree.setUpdatesEnabled(False)
        try:
            self._add_rows(page, rest[: self.BATCH_ROWS])
        finally:
            self._tree.setUpdatesEnabled(True)
            self._guard = False
        rest = rest[self.BATCH_ROWS :]
        if rest:
            self._pending_rows = (gen, page, rest, selection)
            QtCore.QTimer.singleShot(0, self, lambda: self._rows_step(gen))
        else:
            self._rows_done(selection)

    def _finish_rows(self) -> None:
        """Every row of the page shown, now (a batch build still running is completed)."""
        pend = self._pending_rows
        if pend is None:
            return
        _gen, page, rest, selection = pend
        self._guard = True
        self._tree.setUpdatesEnabled(False)
        try:
            self._add_rows(page, rest)
        finally:
            self._tree.setUpdatesEnabled(True)
            self._guard = False
        self._rows_done(selection)

    def _rows_done(self, selection) -> None:
        """The batch build's end: what set_page does after the rows (the filter, the
        selection)."""
        self._pending_rows = None
        self._guard = True
        self._tree.setUpdatesEnabled(False)
        try:
            if self._active():
                self._apply_filter(self._search.text())
            else:
                self._filtered = False
                self._expanded_memo = None
                self._show_count(None)
            self._select_indices(selection, follow=self.follow_selection)
        finally:
            self._tree.setUpdatesEnabled(True)
            self._guard = False

    def detach_rows(self):
        """The rows of the page shown, taken out of the tree (no item is destroyed):
        :meth:`attach_rows` puts them back when that page is shown again. None while a
        large page's rows are still being made: that work stops, and the page's rows are
        made again when it is shown (a part of them is never kept)."""
        if self._pending_rows is not None:
            self._pending_rows = None
            self._rows_gen += 1
            self._guard = True
            try:
                self._tree.clear()
            finally:
                self._guard = False
            for d in (self._items, self._row_obj, self._rank, self._looks, self._row_parent):
                d.clear()
            self.shown = None
            self._shown_page = None
            return None
        expanded = {i for i, it in self._items.items() if it.isExpanded()}
        self._guard = True
        try:
            tops = self._tree.invisibleRootItem().takeChildren()
        finally:
            self._guard = False
        state = (
            tops,
            dict(self._items),
            dict(self._row_obj),
            dict(self._rank),
            self._made,
            self.shown,
            expanded,
            dict(self._looks),
            dict(self._row_parent),
            self._filtered,
            self._expanded_memo,
            getattr(self, "_shown_page", None),
            self._hidden | self._cover,
        )
        self._filtered = False
        self._expanded_memo = None
        self._items.clear()
        self._row_obj.clear()
        self._rank.clear()
        self._looks.clear()
        self._row_parent.clear()
        self.shown = None
        return state

    @_rows_complete
    def attach_rows(self, state, selection: list, hidden: set | None = None) -> None:
        """Show rows :meth:`detach_rows` took out (``shown`` as it was then)."""
        tops, items, row_obj, rank, made, shown, expanded, looks, row_parent = state[:9]
        self._guard = True
        self._tree.setUpdatesEnabled(False)
        try:
            self._tree.clear()
            self._tree.invisibleRootItem().addChildren(tops)
            self._items, self._row_obj, self._rank = dict(items), dict(row_obj), dict(rank)
            self._looks, self._row_parent = dict(looks), dict(row_parent)
            self._made = made
            if len(state) > 11 and state[11] is not None:
                self._shown_page = state[11]
            self._set_hidden_state(hidden, getattr(self, "_shown_page", None), rows=False)
            self._a11y_rows((state[12] if len(state) > 12 else set()) | self._hidden | self._cover)
            for i in expanded:
                it = self._items.get(i)
                if it is not None:
                    it.setExpanded(True)
            self._expanded_memo = state[10] if len(state) > 10 else None
            # (whether a search hid some of these rows when they were taken out)
            self._filtered = bool(state[9]) if len(state) > 9 else True
            if self._active() or self._filtered:
                # (rows a search hid are shown again; rows taken out unfiltered with no
                # search now are all shown)
                self._apply_filter(self._search.text())
            else:
                self._filtered = False
                self._show_count(None)
            # (the rows the user had open came back above; Settings 'Show the objects
            # selected on the page' off: the selection opens no more of them)
            self._select_indices(selection, follow=self.follow_selection)
        finally:
            self._tree.setUpdatesEnabled(True)
            self._guard = False
        self.shown = shown

    #: an edit of more than this share of the page's records (and over 200) keeps the rows
    #: and sets what they show again (_patch_rows); a smaller one makes its units' rows again
    PATCH_SHARE = 0.5

    @_rows_complete
    def update_units(self, page: Page, roots, selection: list, hidden: set | None = None, kept=None) -> bool:
        """Rebuild only the rows of the root units ``roots``."""
        tree = self._tree
        if page is None:
            return False
        n = len(page.objects)
        spans = [
            (r, max(page.subtree(r)) + 1) for r in sorted({page.root_of(r) for r in roots if 0 <= r < n})
        ]
        if sum(e - s for s, e in spans) > max(200, int(n * self.PATCH_SHARE)):
            # most of the page: the rows set again in place when they are the same rows;
            # else what set_page makes, with the rows of the units that did not change put back
            return self._patch_rows(page, spans, selection, hidden) or self._rebuild_keeping(
                page, spans, selection, hidden, kept
            )
        moves = self._kept_unit_rows(page, spans, kept)
        rebuilt = set()
        for s, e in spans:
            rebuilt.update(range(s, e))
        self._large_limit = None
        self._shown_page = page
        self._guard = True
        tree.setUpdatesEnabled(False)
        try:
            # the expanded rows being rebuilt, by the object they showed: a unit
            # that moved (a delete / insert before it) keeps its objects, a unit
            # derived afresh keeps its index
            expanded = {
                id(self._row_obj.get(i)) for i in rebuilt if i in self._items and self._items[i].isExpanded()
            }
            expanded_at = {i for i in rebuilt if i in self._items and self._items[i].isExpanded()}
            shown = {id(o) for o in self._row_obj.values()}
            # the rows of the kept units, by their new index: taken out and put back with
            # the rows built again, as the tree took them (the same removes and inserts:
            # the scroll place and the current row follow alike), not made again
            keep = {}
            for r, o in moves.items():
                for i, _p in self._unit_rows(page, r):
                    old = o + i - r
                    keep[i] = (self._items[old], self._looks.get(old))
            # drop the rows of the rebuilt units and of everything past the end
            for k in reversed(range(tree.topLevelItemCount())):
                idx = tree.topLevelItem(k).data(0, QtCore.Qt.UserRole)
                if idx in rebuilt or idx >= n:
                    tree.takeTopLevelItem(k)
            for i in [i for i in self._items if i in rebuilt or i >= n]:
                del self._items[i]
                self._row_obj.pop(i, None)
                self._rank.pop(i, None)
                self._looks.pop(i, None)
                self._row_parent.pop(i, None)
            # (the hidden state of the new indices, before the rows are made with it)
            was_hidden = self._set_hidden_state(hidden, page, rows=False)
            tops = sorted(
                tree.topLevelItem(k).data(0, QtCore.Qt.UserRole) for k in range(tree.topLevelItemCount())
            )
            for r, _e in spans:
                if page.objects[r].raw.get("_non_drawable"):
                    continue
                if r in moves:
                    self._keep_rows(page, r, keep)
                else:
                    self._add_item(page, r, None)
                item = self._items[r]
                pos = bisect.bisect_left(tops, r)
                tree.insertTopLevelItem(pos, item)
                tops.insert(pos, r)
            for i in rebuilt:
                item, obj = self._items.get(i), self._row_obj.get(i)
                if item is not None and (id(obj) in expanded or (i in expanded_at and id(obj) not in shown)):
                    item.setExpanded(True)
            self._a11y_rows(was_hidden | self._hidden | self._cover)
            if self._active():
                self._apply_filter(self._search.text())
            self._select_indices(selection, follow=self.follow_selection)
        finally:
            tree.setUpdatesEnabled(True)
            self._guard = False
        return True

    @_rows_complete
    def _rebuild_keeping(self, page: Page, spans, selection: list, hidden, kept) -> bool:
        """Rebuild the tree, keeping expansion and selection."""
        n = len(page.objects)
        changed = {s for s, _e in spans}
        stay = [(r, max(page.subtree(r)) + 1) for r in page.roots() if r not in changed]
        reuse = self._kept_unit_rows(page, stay, {r: r for r, _e in stay})
        if kept:
            reuse.update(self._kept_unit_rows(page, spans, kept))
        if not reuse:
            return False
        keep = {}
        for r, o in reuse.items():
            for i, _p in self._unit_rows(page, r):
                keep[i] = (self._items[o + i - r], self._looks.get(o + i - r))
        self.shown = None
        self._shown_page = page
        self._large_limit = None
        self._guard = True
        tree = self._tree
        tree.setUpdatesEnabled(False)
        try:
            # (out of the tree, as clear() takes them; the others go with their last name)
            tree.invisibleRootItem().takeChildren()
            self._items.clear()
            self._row_obj.clear()
            self._rank.clear()
            self._looks.clear()
            self._row_parent.clear()
            was_hidden = self._set_hidden_state(hidden, page, rows=False)
            top = tree.invisibleRootItem()
            for r in page.roots():
                if r >= n or page.objects[r].raw.get("_non_drawable"):
                    continue
                if r in reuse:
                    self._keep_rows(page, r, keep)
                    top.addChild(self._items[r])
                else:
                    self._add_item(page, r, top)
            self._a11y_rows(was_hidden | self._hidden | self._cover)
            self._apply_filter(self._search.text())
            self._select_indices(selection, follow=self.follow_selection)
        finally:
            tree.setUpdatesEnabled(True)
            self._guard = False
        return True

    @_rows_complete
    def _kept_unit_rows(self, page: Page, spans, kept) -> dict:
        """The units of ``spans`` whose rows stay (update_units): {root now: root then} of
        ``kept`` whose old rows show the same objects in the same shape."""
        out = {}
        if not kept:
            return out
        items, objs, parents = self._items, page.objects, self._row_parent
        for s, e in spans:
            o = kept.get(s)
            if o is None:
                continue
            rows = self._unit_rows(page, s)
            if not rows:
                continue
            if sum(1 for i in range(o, o + e - s) if i in items) != len(rows) or any(
                self._row_obj.get(o + i - s) is not objs[i]
                or parents.get(o + i - s, -1) != (None if p is None else o + p - s)
                for i, p in rows
            ):
                continue
            out[s] = o
        return out

    @_rows_complete
    def _keep_rows(self, page: Page, root: int, keep: dict) -> None:
        """The rows of kept unit ``root`` (taken out of the tree) under their new indices, as
        making them again (_add_item, in its order) leaves them: index, look, rank, parent."""
        for i, p in self._unit_rows(page, root):
            item, old = keep[i]
            obj = page.objects[i]
            look = self._row_look(page, i, obj)
            self._show_look(item, look, old)
            item.setData(0, QtCore.Qt.UserRole, i)
            self._items[i] = item
            self._row_obj[i] = obj
            self._looks[i] = look
            self._row_parent[i] = p
            self._made += 1
            self._rank[i] = self._made

    @_rows_complete
    def set_selection(self, indices: list) -> None:
        """The selection made elsewhere (the canvas, Goto Object, Find): its rows are
        selected; the tree scrolls to them and opens the rows they are in unless Settings
        > Appearance > Objects panel > Show the objects selected on the page is off."""
        # (the order of a selection made elsewhere is the window's)
        self._sel_order = [i for i in (indices or []) if i in self._items]
        self._guard = True
        try:
            if "state:selected" in self._filter_keys:
                # (the filter 'Selected only' follows the selection made elsewhere)
                self._select_indices(indices, follow=self.follow_selection)
                self._apply_filter(self._search.text())
            self._select_indices(indices, follow=self.follow_selection)
        finally:
            self._guard = False

    # -- hidden in the editor ---------------------------------------------------------------
    def set_hidden(self, hidden) -> None:
        """The window's editor-only hidden rows changed (an eye, Isolate, Show All): the eyes,
        the dim rows and their accessible descriptions follow; nothing is made again."""
        self._set_hidden_state(hidden, getattr(self, "_shown_page", None))
        if "state:hidden" in self._filter_keys:
            self._apply_filter(self._search.text())
        self._tree.viewport().update()

    def hidden(self) -> set:
        return set(self._hidden)

    def _set_hidden_state(self, hidden, page, rows: bool = True) -> set:
        """``hidden`` and the rows under them (dimmed); with ``rows`` the accessible
        descriptions of the rows whose state changed. Returns the rows hidden before."""
        new = set(hidden or ())
        cover = set()
        if new and page is not None:
            n = len(page.objects)
            for r in new:
                if 0 <= r < n:
                    cover.update(page.subtree(r))
        before = self._hidden | self._cover
        self._hidden, self._cover = new, cover
        if rows:
            self._a11y_rows(before ^ (new | cover))
        return before

    @_rows_complete
    def _a11y_rows(self, indices) -> None:
        """The accessible descriptions of the rows ``indices`` (their hidden state)."""
        items, looks = self._items, self._looks
        for i in indices:
            item, look = items.get(i), looks.get(i)
            if item is not None and look is not None:
                item.setData(0, _A11Y_ROLE, self._a11y(i, look))

    def _a11y(self, i: int, look: RowLook) -> str:
        parts = ["Hidden in the editor" if (i in self._hidden or i in self._cover) else "Shown"]
        if look.flags & F_TEMPLATE:
            parts.append("from the page template")
        elif look.flags & F_LOCKED:
            parts.append("locked")
        return ", ".join(parts)

    # -- internals ----------------------------------------------------------
    def _row_look(self, page: Page, index: int, obj: GraphicObject) -> RowLook:
        """What the row of ``obj`` shows."""
        limit = self.__dict__.get("_large_limit")
        if limit is None:
            from ..dialog.picture_import import large_bytes

            limit = self._large_limit = large_bytes()
        raw = obj.raw
        flags = 0
        tone = ""
        if picture_is_large(obj, limit):
            flags |= F_LARGE
            tone = "warn"
        if obj.parent is None:
            flags |= F_ROOT
        if raw.get("_from_template"):
            flags |= F_TEMPLATE
            tone = "text_dim"
        elif (raw.get("_dwflags") or 0) & 0x4:
            # Edit > Lock (persisted dwFlags 0x4): the padlock shows it
            flags |= F_LOCKED
            tone = tone or "disabled"
        if obj.parent is not None and self.in_part(page, index):
            flags |= F_PART
        elif not flags & F_TEMPLATE:
            flags |= F_RENAME
        info = row_info(page, index, obj, self._genie_values)
        if info.name:
            flags |= F_NAMED
        if info.commands:
            flags |= F_INPUT
        if info.animated:
            flags |= F_ANIMATED
        keywords, tags, lib = info.keywords, info.tags, info.lib
        if self.label_mode == "classic":
            text = _row_text(index, obj, page)
            primary, secondary, an = text, "", ""
        else:
            primary, secondary = info.primary, info.secondary
            pic = picture_text(obj) if raw.get("_pic_bytes") else ""
            if pic:
                secondary = (secondary + "  " + pic) if secondary else pic
            an = ("AN %d" % obj.an) if obj.an > 0 and self.show_an else ""
            text = "  ".join(x for x in (primary, secondary, an) if x)
        cols = ()
        if len(self.columns) > 1:
            cols = tuple(
                self._column_value(page, index, obj, c, self._genie_values) for c in self.columns[1:]
            )
        key = _kind_key(obj)
        ic = "symbol" if key == "symbol" else _KIND_ICON.get(obj.kind, "unknown")
        return RowLook(text, ic, tone, primary, secondary, an, flags, key, keywords, tags, lib, cols)

    @staticmethod
    def in_part(page: Page, index: int) -> bool:
        """Whether row ``index`` is part of a placed genie, symbol or composite genie (its
        objects come from a library: not renamed, reordered or deleted alone)."""
        objs = page.objects
        for a in page.ancestors(index):
            o = objs[a]
            if o.is_genie or o.kind.value != "group" or o.raw.get("_ref"):
                return True
        return False

    #: the window's genie_values(index) of the page shown (EditableDocument.genie_values: the
    #: values a genie gets as the Graphics Builder composes the page); None: the stored ones
    genie_values = None

    def _genie_values(self, index: int) -> list:
        fn = self.genie_values
        if fn is None:
            return []
        try:
            return list(fn(index))
        except Exception:  # noqa: BLE001
            from ...infra.logsetup import note_exception

            note_exception("objects_dock._genie_values", index=index)
            return []

    @staticmethod
    def _column_value(page: Page, index: int, obj: GraphicObject, key: str, values=None) -> str:
        """Column ``key`` of the row of ``obj``; ``values``: the genie values of the page's
        genies (_genie_values), None for their stored bindings."""
        if key == "name":
            return object_name_of(obj)
        if key == "kind":
            return kind_name(obj)
        if key == "an":
            return str(obj.an) if obj.an > 0 else ""
        if key == "element":
            return element_label(obj, page, short=False)
        if key == "tag":
            # (an equipment path by its last part, as the row shows it: the tool tip has
            # the whole path)
            k, v = (
                composite_binding(page, index)
                if obj.kind.value == "composite_genie"
                else main_binding(obj, copy_bindings(values, index, obj))
            )
            return binding_text(k, v) if v else first_tag(obj)
        if key == "parameters":
            return values_text(page, index, values)
        if key == "position":
            g = obj.geometry
            return "%d, %d" % (g.x, g.y) if g is not None else ""
        return ""

    def _show_look(self, item, look: RowLook, old: RowLook | None = None) -> None:
        """Set what changed from ``old`` (None: a new row) to ``look`` (_row_look)."""
        if old is None or old.text != look.text:
            item.setText(0, look.text)
        if old is None or old.icon != look.icon:
            item.setIcon(0, icon(look.icon))
        if old is None or old.keywords != look.keywords:
            item.setData(0, KEYWORDS_ROLE, look.keywords)
        if old is None or old.columns != look.columns:
            for k, v in enumerate(look.columns, 1):
                item.setText(k, v)
        if old is None or (old.flags & F_RENAME) != (look.flags & F_RENAME):
            f = item.flags()
            item.setFlags((f | _EDITABLE) if look.flags & F_RENAME else (f & ~_EDITABLE))
        i = item.data(0, _USER_ROLE)
        if old is None or old.flags != look.flags or i is None:
            item.setData(0, _A11Y_ROLE, self._a11y(i if i is not None else -1, look))

    def _add_item(self, page: Page, index: int, parent, parent_index: int | None = None) -> None:
        """The rows of ``index`` and its members, in tree order (parent None: a loose
        top-level row the caller inserts itself)."""
        objs = page.objects
        items, row_obj, looks = self._items, self._row_obj, self._looks
        row_parent, rank, row_look = self._row_parent, self._rank, self._row_look
        hidden, cover, a11y = self._hidden, self._cover, self._a11y
        made = self._made
        # (a stack in place of a recursion: the same rows in the same order)
        stack = [(index, parent, parent_index)]
        try:
            while stack:
                i, par, pi = stack.pop()
                obj = objs[i]
                item = QtWidgets.QTreeWidgetItem() if par is None else QtWidgets.QTreeWidgetItem(par)
                look = row_look(page, i, obj)
                # (_show_look of a new row)
                item.setText(0, look.text)
                item.setIcon(0, icon(look.icon))
                item.setData(0, KEYWORDS_ROLE, look.keywords)
                for k, v in enumerate(look.columns, 1):
                    item.setText(k, v)
                if look.flags & F_RENAME:
                    item.setFlags(item.flags() | _EDITABLE)
                item.setData(0, _USER_ROLE, i)
                item.setData(
                    0,
                    _A11Y_ROLE,
                    a11y(i, look)
                    if (look.flags & (F_LOCKED | F_TEMPLATE) or i in hidden or i in cover)
                    else "Shown",
                )
                items[i] = item
                row_obj[i] = obj
                looks[i] = look
                row_parent[i] = pi
                made += 1
                rank[i] = made
                stack.extend(
                    (m, item, i)
                    for m in reversed([m for m in obj.members if not objs[m].raw.get("_non_drawable")])
                )
        finally:
            self._made = made

    @staticmethod
    def _unit_rows(page: Page, root: int) -> list:
        """(index, parent index) of the rows root unit ``root`` shows, in tree order."""
        objs = page.objects
        if objs[root].raw.get("_non_drawable"):
            return []
        out, stack = [], [(root, None)]
        while stack:
            i, p = stack.pop()
            out.append((i, p))
            stack.extend((m, i) for m in reversed(objs[i].members) if not objs[m].raw.get("_non_drawable"))
        return out

    @_rows_complete
    def _patch_rows(self, page: Page, spans, selection: list, hidden) -> bool:
        """Show the model in the rows of ``spans`` (root units as (start, end)) without
        making them again, when the units show the same rows as before (a turn, a move,
        their undo / redo). False when their rows changed: set_page then."""
        items, parents = self._items, self._row_parent
        n = len(page.objects)
        if items and max(items) >= n:
            return False
        plan = []
        for s, e in spans:
            rows = self._unit_rows(page, s)
            if sum(1 for i in range(s, e) if i in items) != len(rows) or any(
                i not in items or parents.get(i, -1) != p for i, p in rows
            ):
                return False
            plan.append((s, rows))
        self._large_limit = None
        self._guard = True
        self._tree.setUpdatesEnabled(False)
        try:
            self._set_hidden_state(hidden, page)
            looks, row_obj = self._looks, self._row_obj
            for _root, rows in plan:
                for i, _p in rows:
                    obj = page.objects[i]
                    look = self._row_look(page, i, obj)
                    old = looks.get(i)
                    if look != old:
                        self._show_look(items[i], look, old)
                        looks[i] = look
                    row_obj[i] = obj
            self._shown_page = page
            if self._active():
                self._apply_filter(self._search.text())
            self._select_indices(selection, follow=self.follow_selection)
        finally:
            self._tree.setUpdatesEnabled(True)
            self._guard = False
        return True

    #: up to this many rows changing are set one by one; more go in one selection (each
    #: setSelected merges into the model's ranges)
    BULK_SELECT = 32

    @_rows_complete
    def _select_indices(self, indices: list, follow: bool = True) -> None:
        """Select exactly the rows of ``indices``; the current row = the first
        selected in tree-build order. A few changed rows are set one by one, more
        in one selection of the model. ``follow``: the tree scrolls to the current row
        and opens the rows it is in (False: it stays as it is)."""
        items = self._items
        want = {i for i in (indices or []) if i in items}
        tree = self._tree
        sm = tree.selectionModel()
        if not want:
            if sm.hasSelection():
                blocked = tree.blockSignals(True)
                try:
                    sm.clearSelection()
                finally:
                    tree.blockSignals(blocked)
            return
        have = set()
        stray = []
        # every selected row, also those the filter hides (selectedItems()
        # leaves hidden rows out: they would stay selected)
        rows = {
            id(it): it for it in (tree.itemFromIndex(ix) for ix in sm.selectedIndexes()) if it is not None
        }
        for it in rows.values():
            idx = it.data(0, QtCore.Qt.UserRole)
            if idx in items and items[idx] is it:
                have.add(idx)
            else:
                stray.append(it)  # a row not in the index map
        drop, add = have - want, want - have
        if len(drop) + len(add) + len(stray) <= self.BULK_SELECT:
            for it in stray:
                it.setSelected(False)
            for idx in drop:
                items[idx].setSelected(False)
            for idx in add:
                items[idx].setSelected(True)
        elif drop or add or stray:
            sel = self._selection_of(want)
            # (the tree's own itemSelectionChanged: the dock emits nothing for a selection
            # set from outside; the model still tells the items and the view)
            blocked = tree.blockSignals(True)
            try:
                if have and want:
                    # Qt compares the old and new selections range by range when both are
                    # non-empty (quadratic): clear first, then select
                    sm.clearSelection()
                # (the Rows flag: the optional columns of a row are selected with it)
                sm.select(sel, QtCore.QItemSelectionModel.ClearAndSelect | QtCore.QItemSelectionModel.Rows)
            finally:
                tree.blockSignals(blocked)
        rank = self._rank
        first = items[min(want, key=lambda i: rank.get(i, 1 << 30))]
        if follow:
            tree.setCurrentItem(first, 0, QtCore.QItemSelectionModel.NoUpdate)
            tree.scrollToItem(first)
            return
        # (a new current row scrolls to it and opens its parents: Qt's auto scroll)
        auto = tree.hasAutoScroll()
        tree.setAutoScroll(False)
        try:
            tree.setCurrentItem(first, 0, QtCore.QItemSelectionModel.NoUpdate)
        finally:
            tree.setAutoScroll(auto)

    @_rows_complete
    def _selection_of(self, indices) -> QtCore.QItemSelection:
        """The rows of ``indices`` as a selection: rows next to each other under one
        parent make one range (fewer ranges: Qt looks a row up range by range)."""
        tree, items = self._tree, self._items
        sel = QtCore.QItemSelection()
        runs: dict = {}  # parent id -> [first index, last index, last row]
        for idx in sorted(indices):
            ix = tree.indexFromItem(items[idx], 0)
            key = ix.parent().internalId()
            run = runs.get(key)
            if run is not None and ix.row() == run[2] + 1:
                run[1], run[2] = ix, ix.row()
                continue
            if run is not None:
                sel.select(run[0], run[1])
            runs[key] = [ix, ix, ix.row()]
        for first, last, _row in runs.values():
            sel.select(first, last)
        return sel

    # -- search and filter ------------------------------------------------------------------
    def _active(self) -> bool:
        """A search or a filter is on."""
        return bool(self._search.text().strip() or self._filter_keys)

    def filters(self) -> list:
        """The filters on ('kind:genie', 'state:hidden'), as objects/filter stores them."""
        return list(self._filter_keys)

    def set_filter(self, key: str, on: bool) -> None:
        keys = [k for k in self._filter_keys if k != key] + ([key] if on else [])
        self._set_filters(keys)

    def clear_filters(self) -> None:
        self._set_filters([])

    def _set_filters(self, keys) -> None:
        self._filter_keys = list(keys)
        try:
            _settings().setValue("objects/filter", list(keys))
        except Exception:  # noqa: BLE001 -- swallow-ok: not remembered then
            pass
        self._show_filter_mark()
        self._apply_filter(self._search.text())

    def _states_of(self, i: int, look: RowLook, selected: set) -> set:
        out = set()
        f = look.flags
        if i in self._hidden or i in self._cover:
            out.add("hidden")
        if f & F_LOCKED:
            out.add("locked")
        if f & F_TEMPLATE:
            out.add("template")
        if f & F_ANIMATED:
            out.add("animated")
        if f & F_INPUT:
            out.add("commands")
        if f & F_LARGE:
            out.add("large")
        if f & F_NAMED:
            out.add("named")
        if i in selected:
            out.add("selected")
        return out

    @_rows_complete
    def filter_counts(self) -> dict:
        """{'kind:genie': rows, 'state:hidden': rows ...} of the page shown."""
        out: dict = {}
        selected = set(self._selected_indices())
        for i, look in self._looks.items():
            k = "kind:" + look.kind_key
            out[k] = out.get(k, 0) + 1
            for s in self._states_of(i, look, selected):
                out["state:" + s] = out.get("state:" + s, 0) + 1
        return out

    @_rows_complete
    def _row_fields(self, i: int, look: RowLook, states: set) -> dict:
        obj = self._row_obj.get(i)
        kind = kind_name(obj).lower() if obj is not None else ""
        return {
            "name": object_name_of(obj) if obj is not None else "",
            "tag": look.tags,
            "an": (str(obj.an),) if obj is not None and obj.an > 0 else (),
            # (the code name too, 'symbol_set'; a placed symbol's genie kind is not one)
            "kind": "%s %s %s"
            % (look.kind_key, kind, "" if obj is None or look.kind_key == "symbol" else obj.kind.value),
            "lib": look.lib,
            "is": tuple(states),
        }

    @_rows_complete
    def _apply_filter(self, text=None) -> None:
        """Show the rows the search and the filter let through (and the rows they are in).
        While a search runs only the paths to its matches are open; the rows open before it
        are open again when it is cleared, and so are the rows of the selection."""
        if text is None:
            text = self._search.text()
        q = FieldQuery(text, self._search.regex)
        keys = self._filter_keys
        kinds = {k[5:] for k in keys if k.startswith("kind:")}
        states = {k[6:] for k in keys if k.startswith("state:")}
        active = not q.empty or bool(kinds or states)
        if active and self._expanded_memo is None:
            self._expanded_memo = {i for i, it in self._items.items() if it.isExpanded()}
        self._query = q if not q.empty else None
        looks = self._looks
        selected = set(self._selected_indices()) if "selected" in states else set()
        need_states = bool(states) or any(f == "is" for f, _v in q.terms)
        tree = self._tree
        listed = []
        total = [0]

        others = states - {"selected"}

        def passes(i: int, look: RowLook) -> bool:
            # (a row of any kind ticked, in any state ticked; 'Selected only' as well)
            if kinds and look.kind_key not in kinds:
                return False
            st = self._states_of(i, look, selected) if need_states else set()
            if "selected" in states and "selected" not in st:
                return False
            if others and not others & st:
                return False
            if q.empty:
                return True
            fields = self._row_fields(i, look, st) if q.terms else None
            return q(fields, look.text, look.keywords)

        found = [0]

        def visit(item, under_match: bool) -> bool:
            i = item.data(0, _USER_ROLE)
            look = looks.get(i)
            total[0] += 1
            self_match = look is not None and passes(i, look)
            if self_match:
                found[0] += 1
                if not under_match:
                    listed.append(i)
            child_match = False
            for k in range(item.childCount()):
                child_match = visit(item.child(k), under_match or self_match) or child_match
            visible = self_match or child_match
            # (only what changes: each call tells the tree, thousands a keystroke)
            if item.isHidden() == visible:
                item.setHidden(not visible)
            if active and item.isExpanded() != child_match:
                item.setExpanded(child_match)
            return visible

        root = tree.invisibleRootItem()
        if active or self._filtered:
            for k in range(root.childCount()):
                visit(root.child(k), False)
        if not active and self._expanded_memo is not None:
            self._restore_expanded()
        self._filtered = active
        self._listed = listed if active else []
        self._show_count((found[0], total[0]) if active else None)
        tree.viewport().update()

    @_rows_complete
    def _restore_expanded(self) -> None:
        """The rows open before the search, and the rows of the selection's ancestors."""
        memo, self._expanded_memo = self._expanded_memo, None
        if memo is None:
            return
        page = getattr(self, "_shown_page", None)
        want = set(memo)
        if page is not None:
            n = len(page.objects)
            for i in self._selected_indices():
                if 0 <= i < n:
                    want.update(page.ancestors(i))
        for i, it in self._items.items():
            if it.childCount():
                it.setExpanded(i in want)
        cur = self._tree.currentItem()
        if cur is not None:
            self._tree.scrollToItem(cur)

    def _show_count(self, counts) -> None:
        if counts is None:
            self._count_row.hide()
            return
        n, total = counts
        self._count.setText("%d of %d" % (n, total))
        self._count.setToolTip("%d of the %d objects listed in the panel match" % (n, total))
        k = len(self.listed())
        self._select_btn.setText("Select %d" % k if k else "")
        self._select_btn.setVisible(bool(k))
        self._count_row.show()

    def _show_filter_mark(self) -> None:
        """The filter button: the funnel, with an accent dot while a filter is on."""
        on = bool(self._filter_keys)
        self._filter_action.setIcon(self._filter_icon(on))
        n = len(self._filter_keys)
        self._filter_action.setToolTip(
            "Filter by kind and state: %d on" % n if on else "Filter by kind and state"
        )

    @staticmethod
    def _filter_icon(on: bool) -> QtGui.QIcon:
        base = icon("filter")
        if not on:
            return base
        from ..style.theme import color

        out = QtGui.QIcon()
        for scale in (1, 2):
            pm = base.pixmap(QtCore.QSize(16, 16), scale)
            pm = QtGui.QPixmap(pm)
            p = QtGui.QPainter(pm)
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(color("accent"))
            p.drawEllipse(QtCore.QRectF(10.5, 0.5, 5.5, 5.5))
            p.end()
            out.addPixmap(pm)
        return out

    def open_filter(self) -> None:
        pop = _FilterPopover(self)
        self._popover = pop
        pop.open_at(self._search)

    # -- selecting what is listed -----------------------------------------------------------
    def _selectable_memo_now(self):
        """The kept answers of ``selectable`` (a dict) while its key is the same, None when
        there is no key (selectable_key)."""
        if self.selectable is None or self.selectable_key is None:
            return None
        key = self.selectable_key()
        if key is None:
            return None
        memo = self.__dict__.get("_selectable_memo")
        if (
            memo is None
            or memo[0] != key[1:]
            or len(memo[1]) != len(key[0])
            or any(a is not b for a, b in zip(memo[1], key[0]))
        ):
            memo = (key[1:], tuple(key[0]), {})
            self.__dict__["_selectable_memo"] = memo
        return memo[2]

    @_rows_complete
    def _can_select(self, i: int, memo=None) -> bool:
        """``memo``: :meth:`_selectable_memo_now` once for many rows."""
        if self.selectable is not None:
            if memo is not None:
                got = memo.get(i)
                if got is not None:
                    return got
            try:
                ok = bool(self.selectable(i))
            except Exception:  # noqa: BLE001 -- swallow-ok: the window went
                return False
            if memo is not None:
                memo[i] = ok
            return ok
        look = self._looks.get(i)
        return look is not None and not look.flags & (F_LOCKED | F_TEMPLATE)

    @_rows_complete
    def listed(self) -> list:
        """The rows a Ctrl+A or Select N selects: with a search or filter on, the rows that
        pass it themselves (not a row inside another one listed); without, every top-level
        row. Rows that cannot be selected (locked, the template) are left out."""
        if self._filtered:
            rows = getattr(self, "_listed", [])
        else:
            tree = self._tree
            rows = [tree.topLevelItem(k).data(0, _USER_ROLE) for k in range(tree.topLevelItemCount())]
        page = getattr(self, "_shown_page", None)
        memo = self._selectable_memo_now()
        out = []
        for i in rows:
            if i is None:
                continue
            root = page.root_of(i) if page is not None else i
            if self._can_select(root, memo):
                out.append(i)
        return out

    @_rows_complete
    def select_listed(self) -> None:
        """Ctrl+A in the panel, Enter in its search, 'Select N': select the rows listed, as
        one selection (not the rows a search hides)."""
        want = self.listed()
        self._guard = True
        try:
            self._select_indices(want)
        finally:
            self._guard = False
        self.selectionChanged.emit(sorted(want))
        if want:
            self.statusMessage.emit("%s selected." % (plural(len(want), "object")))

    @_rows_complete
    def _select_from_search(self) -> None:
        self._search.flush()
        if self._filtered:
            self.select_listed()
            self._tree.setFocus(QtCore.Qt.OtherFocusReason)

    def select_parent(self) -> None:
        """Shift+Enter: the selected rows' parents instead (a top-level row stays)."""
        page = getattr(self, "_shown_page", None)
        sel = self._selected_indices()
        if page is None or not sel:
            return
        n = len(page.objects)
        want = sorted(
            {page.objects[i].parent if 0 <= i < n and page.objects[i].parent is not None else i for i in sel}
        )
        self._guard = True
        try:
            self._select_indices(want)
        finally:
            self._guard = False
        self.selectionChanged.emit(want)

    def activate_current(self) -> None:
        """Enter: a top-level row does what a double-click on the page does (a genie's
        parameters, else its properties); a member row opens its own properties."""
        it = self._tree.currentItem()
        idx = it.data(0, _USER_ROLE) if it is not None else None
        if idx is not None:
            self._activate(idx)

    @_rows_complete
    def _activate(self, idx) -> None:
        """Enter on row ``idx`` (a double-click too, with Settings 'Double-click a row: Opens
        its properties')."""
        look = self._looks.get(idx)
        if look is not None and look.flags & F_ROOT:
            self.activated.emit(idx)
        else:
            self.propertiesRequested.emit(idx)

    # -- rename -----------------------------------------------------------------------------
    @_rows_complete
    def object_name(self, idx) -> str:
        obj = self._row_obj.get(idx)
        return object_name_of(obj) if obj is not None else ""

    @_rows_complete
    def rename_refusal(self, idx) -> str:
        """Why row ``idx`` cannot be renamed here ('' = it can)."""
        look = self._looks.get(idx)
        if look is None:
            return "Nothing to rename."
        if look.flags & F_TEMPLATE:
            return TEMPLATE_TIP
        if look.flags & F_PART:
            return PART_RENAME_TIP
        if self.rename_blocked is not None:
            try:
                return str(self.rename_blocked(idx) or "")
            except Exception:  # noqa: BLE001 -- swallow-ok: the window went; no refusal
                return ""
        return ""

    @_rows_complete
    def start_rename(self, idx=None) -> bool:
        """Edit the name of row ``idx`` (the current row) in place; False when it cannot be
        renamed (the reason goes to the status bar)."""
        if idx is None:
            it = self._tree.currentItem()
            idx = it.data(0, _USER_ROLE) if it is not None else None
        item = self._items.get(idx)
        if item is None:
            return False
        why = self.rename_refusal(idx)
        if why:
            self.statusMessage.emit(why)
            return False
        if self._row_hidden(item) and self._active():
            # (the search or a filter hides the row: they go, so the editor can open on it)
            self._search.clear()
            self.clear_filters()
            self._search.flush()
        if self._row_hidden(item):
            self.statusMessage.emit("That object is not listed in the Objects panel.")
            return False
        tree = self._tree
        tree.scrollToItem(item)
        tree.setCurrentItem(item, 0, QtCore.QItemSelectionModel.NoUpdate)
        # (the panel takes the focus first: focus moved to it after the editor opened would
        # close the editor at once)
        tree.setFocus(QtCore.Qt.ShortcutFocusReason)
        tree.editItem(item, 0)
        if tree.state() != QtWidgets.QAbstractItemView.EditingState:
            self.statusMessage.emit("The name cannot be edited here now.")
            return False
        return True

    @staticmethod
    def _row_hidden(item) -> bool:
        """``item`` or a row it is in is hidden (a search or a filter)."""
        while item is not None:
            if item.isHidden():
                return True
            item = item.parent()
        return False

    @_rows_complete
    def update_positions(self) -> None:
        """A move left the rows as they were (a drag, a nudge, a move's undo): the Position
        column shows where each object is now. Nothing to do without that column."""
        if "position" not in self.columns:
            return
        page = getattr(self, "_shown_page", None)
        if page is None:
            return
        col = self.columns.index("position")
        n = len(page.objects)
        for idx, item in self._items.items():
            obj = page.objects[idx] if 0 <= idx < n else None
            if obj is None:
                continue
            text = self._column_value(page, idx, obj, "position")
            if item.text(col) != text:
                item.setText(col, text)
                look = self._looks.get(idx)
                if look is not None and len(look.columns) >= col:
                    cols = list(look.columns)
                    cols[col - 1] = text
                    self._looks[idx] = look._replace(columns=tuple(cols))

    def _name_problem_of(self, editor) -> str:
        idx = editor.property("ctgIndex")
        name = editor.text().strip()
        if idx is None or self.name_problem is None or name == self.object_name(idx):
            return ""
        try:
            return str(self.name_problem(idx, name) or "")
        except Exception as e:  # noqa: BLE001  # swallow-ok: said, the editor stays
            return str(e)

    def _check_name_edit(self, _text: str = "") -> None:
        ed = self.sender()
        if isinstance(ed, QtWidgets.QLineEdit):
            problem = self._name_problem_of(ed)
            from ..style.theme import mark_invalid

            mark_invalid(ed, problem)

    def _show_name_problem(self, editor, problem: str) -> None:
        from ..style.theme import mark_invalid

        mark_invalid(editor, problem)
        self.statusMessage.emit(problem)

    # -- lock -------------------------------------------------------------------------------
    @_rows_complete
    def _lock_clicked(self, idx: int) -> None:
        look = self._looks.get(idx)
        if look is None or not look.flags & F_ROOT:
            return
        if look.flags & F_TEMPLATE:
            self.statusMessage.emit(TEMPLATE_TIP)
            return
        self.lockToggled.emit(idx, not look.flags & F_LOCKED)

    # -- tool tips --------------------------------------------------------------------------
    @_rows_complete
    def tip_for(self, idx, zone) -> str:
        """The tool tip of row ``idx`` at ``zone`` (made when asked for)."""
        look = self._looks.get(idx)
        obj = self._row_obj.get(idx)
        if look is None or obj is None:
            return ""
        # (the eye is the editor's only; the padlock is saved with the page: each tip says so,
        # since the two look like a pair)
        if zone == "eye":
            if idx in self._hidden:
                return "Hidden in this editor only (not saved; the runtime shows it): click to show it"
            return (
                "Hide it in this editor only: not saved, and the runtime still shows it. "
                "Alt+click: show only this object. Drag across the eyes to hide several"
            )
        if zone == "lock":
            if look.flags & F_TEMPLATE:
                return TEMPLATE_TIP
            if look.flags & F_LOCKED:
                return "Locked, and saved with the page: click to unlock it"
            return "Lock it, saved with the page: it cannot be selected or moved until it is unlocked"
        lines = [look.primary + (("  " + look.secondary) if look.secondary else "")]
        page = getattr(self, "_shown_page", None)
        k, v = (
            composite_binding(page, idx)
            if obj.kind.value == "composite_genie"
            else main_binding(obj, copy_bindings(self._genie_values, idx, obj))
        )
        if v and k.lower() in ("equipment", "equip", "equipmentref"):
            lines.append("%s: %s" % (k, v))
        # (a nested genie tree: each level's element and the values it gets, the outermost
        # first; this object's own last when it is one)
        chain = chain_lines(page, idx, values=self._genie_values)
        if len(chain) > 1:
            lines.append("Placed by, the outermost first:")
            lines.extend("  " + c for c in chain)
        else:
            el = element_label(obj, page, short=False)
            if el:
                lines.append("Element: %s" % el)
            params = values_text(page, idx, self._genie_values)
            if params:
                lines.append("Values: %s" % params)
        where = ("AN %d. " % obj.an if obj.an > 0 else "") + (
            "Position %d in the page's stacking order"
        ) % idx
        lines.append(where)
        if look.flags & F_LARGE:
            lines.append(
                "A large picture (%s): every page that shows it loads it. Tools > "
                "Pictures > Resample Picture to Its Size makes a stretched one "
                "smaller." % picture_text(obj)
            )
        if look.flags & F_TEMPLATE:
            lines.append("From the page template (locked).")
        elif look.flags & F_LOCKED:
            lines.append("Locked (Edit > Unlock Object, or the padlock).")
        if idx in self._hidden or idx in self._cover:
            lines.append("Hidden in the editor: it stays on the page.")
        return "\n".join(lines)

    # -- signals ----------------------------------------------------------------------------
    def _selected_indices(self) -> list:
        return sorted(
            {
                it.data(0, QtCore.Qt.UserRole)
                for it in self._tree.selectedItems()
                if it.data(0, QtCore.Qt.UserRole) is not None
            }
        )

    def selection_order(self) -> list:
        """The selected rows in the order they were selected (Rename Objects' 'Selection
        order'): rows kept from before first, then the ones a click added (in record order
        among themselves)."""
        now = self._selected_indices()
        keep = set(now)
        order = [i for i in getattr(self, "_sel_order", ()) if i in keep]
        have = set(order)
        order += [i for i in now if i not in have]
        return order

    def _on_selection_changed(self) -> None:
        if self._guard:
            return
        self._sel_order = self.selection_order()
        self.selectionChanged.emit(list(self._sel_order))

    def _on_double_click(self, item, _column) -> None:
        """Settings > Appearance > Objects panel > Double-click a row: zoom to the object
        (the default), scroll it into view, its properties (as Enter) or rename it."""
        idx = item.data(0, QtCore.Qt.UserRole)
        if idx is None:
            return
        what = self.double_click
        if what == "scroll":
            self.revealRequested.emit(idx)
        elif what == "props":
            self._activate(idx)
        elif what == "rename":
            self.start_rename(idx)
        else:
            self.frameRequested.emit(idx)

    def eventFilter(self, obj, e) -> bool:  # noqa: N802 -- Qt naming
        # F frames the selection (before typing in the tree goes on in the search; while a
        # search is being typed an F goes on in it)
        if (
            obj is self._tree
            and e.type() == QtCore.QEvent.KeyPress
            and e.key() == QtCore.Qt.Key_F
            and not (e.modifiers() & ~QtCore.Qt.KeypadModifier)
            and not self._search.text()
            and self._tree.state() != QtWidgets.QAbstractItemView.EditingState
        ):
            it = self._tree.currentItem()
            if it is not None and it.data(0, _USER_ROLE) is not None:
                self.frameRequested.emit(it.data(0, _USER_ROLE))
                return True
        if obj is self._tree.viewport():
            t = e.type()
            if t == QtCore.QEvent.ToolTip:
                # (before the column helper's filter, installed earlier: it would show the
                # cut cell text, never the eye's or the padlock's tip)
                return self._tree.show_tip(e)
        return super().eventFilter(obj, e)

    # -- the right-click menu -----------------------------------------------------------------
    #: the menu's commands by name, in the canvas menu's order (separators between groups)
    MENU = (
        ("props", "genie", "definition", "rename"),
        ("cut", "copy", "dup", "del"),
        ("front", "fwd", "bwd", "back"),
        ("group", "ungroup"),
        ("lock_toggle", "lock", "unlock"),
        ("hide_toggle", "isolate", "show_all"),
        ("zoom_sel",),
        ("select_parent", "select_same"),
    )
    #: the commands a part of a placed genie cannot take (they would act on the whole unit)
    PART_BLOCKED = ("cut", "del", "front", "fwd", "bwd", "back")

    def _context_menu(self, pos: QtCore.QPoint) -> None:
        item = self._tree.itemAt(pos)
        if item is not None and not item.isSelected():
            self._tree.setCurrentItem(item)
        if item is None:
            # (on the empty space below the rows: the panel's columns)
            menu = QtWidgets.QMenu(self)
            self._columns_menu(menu.addMenu("Columns"))
            menu.exec(self._tree.viewport().mapToGlobal(pos))
            menu.deleteLater()
            return
        menu = self.build_menu()
        if menu is not None:
            menu.exec(self._tree.viewport().mapToGlobal(pos))
            menu.deleteLater()

    @_rows_complete
    def build_menu(self) -> QtWidgets.QMenu | None:
        """The right-click menu of the selected rows (not shown), from the window's commands
        (:meth:`set_actions`); None when nothing is selected."""
        sel = self._selected_indices()
        if not sel:
            return None
        menu = QtWidgets.QMenu(self)
        menu.setToolTipsVisible(True)
        acts = self._actions
        one = sel[0] if len(sel) == 1 else None
        part = any(self._looks.get(i) is not None and self._looks[i].flags & F_PART for i in sel)
        obj = self._row_obj.get(one) if one is not None else None
        for group in self.MENU:
            added = False
            for name in group:
                a = acts.get(name)
                if callable(a) and not isinstance(a, QtGui.QAction):
                    a = a(one, menu) if one is not None else None
                if a is None:
                    continue
                if name == "genie" and not (obj is not None and getattr(obj, "is_genie", False)):
                    continue
                if name in ("lock", "unlock") and acts.get("lock_toggle") is not None:
                    continue  # (the toggle command shows instead)
                if name == "lock" and self._all_locked(sel):
                    continue
                if name == "unlock" and not self._any_locked(sel):
                    continue
                why = ""
                if name == "rename":
                    why = self.rename_refusal(one) if one is not None else ""
                elif part and name in self.PART_BLOCKED:
                    why = PART_TIP
                if why:
                    self._disabled_copy(menu, a, why)
                else:
                    menu.addAction(a)
                added = True
            if added:
                menu.addSeparator()
        while menu.actions() and menu.actions()[-1].isSeparator():
            menu.removeAction(menu.actions()[-1])
        # (the panel's columns are on every row menu: the header is hidden while only the
        # Object column shows, and a full panel has no empty space below its rows)
        menu.addSeparator()
        cols = menu.addMenu("Columns")
        cols.menuAction().setObjectName("tree.columns")
        self._columns_menu(cols)
        return menu

    @staticmethod
    def _disabled_copy(menu: QtWidgets.QMenu, a: QtGui.QAction, why: str) -> QtGui.QAction:
        """A disabled copy of command ``a`` that says ``why`` (the window's command stays as
        it is everywhere else). Its key shows as text after a tab, as the menu shows a
        command's key, without the copy taking the key."""
        keys = a.shortcut().toString(QtGui.QKeySequence.NativeText)
        b = menu.addAction(a.icon(), a.text() + ("\t" + keys if keys else ""))
        b.setObjectName("tree.local")
        b.setEnabled(False)
        b.setToolTip(why)
        b.setStatusTip(why)
        return b

    @_rows_complete
    def _all_locked(self, sel) -> bool:
        return all(self._looks.get(i) is not None and self._looks[i].flags & F_LOCKED for i in sel)

    @_rows_complete
    def _any_locked(self, sel) -> bool:
        page = getattr(self, "_shown_page", None)
        for i in sel:
            r = page.root_of(i) if page is not None else i
            look = self._looks.get(r)
            if look is not None and look.flags & F_LOCKED:
                return True
        return False
