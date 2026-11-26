"""Genie parameter dialogs, for placed and pasted genies."""

from __future__ import annotations
from ..style.theme import sp as _sp

import html as _html
import weakref
from collections import OrderedDict
from typing import Callable, Dict, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets
from ..style.theme import set_note_role

# display labels for common genie binding keys (the default form)
_LABELS = {
    "Equipment": "Equipment",
    "MenuName": "Menu",
    "TagPrefix": "Tag prefix",
    "Tag": "Tag",
    "argTagName": "Tag name",
    "RefAN": "Reference AN",
    "HideWhenCondition": "Hidden when",
    "TimeRemaining": "Time remaining",
    "MaxTime": "Max time",
    "OnClickFunction": "On click",
    "OnRightClickFunction": "On right-click",
}

#: the default form's field length (0xfe per parameter; a
#: substituted value is capped at 254 characters too)
DEFAULT_MAX = 254

AN_TIP = "The genie's animation number (AN). It is set when the genie is placed."
#: the list forms whose values are variable tags: a text box with completion, not a
#: drop-down of every tag (a large project scope has hundreds of thousands)
_TAG_LIST_FORMS = ("variable tags", "variable")


def _friendly_ref(ref: str) -> str:
    """'lib.element.<ctm' -> 'lib.element' (the stored link carries the
    file-type tag CtDraw appends; users know genies as library.element)."""
    ref = " ".join(ref.split())
    for tag in (".<ctm", ".<ctl", ".<ctt", ".ctm", ".ctl", ".ctt"):
        if ref.lower().endswith(tag):
            return ref[: -len(tag)]
    return ref


def _is_scope_model(names) -> bool:
    """``names`` is the scope's shared completion model (ui/objprops/expression.py)."""
    return (
        isinstance(names, QtCore.QAbstractItemModel)
        and hasattr(names, "update")
        and hasattr(names, "project_dir")
    )


def _completer(names, parent) -> Optional[QtWidgets.QCompleter]:
    if not names or _is_scope_model(names):
        return None
    if isinstance(names, QtCore.QAbstractItemModel):
        c = QtWidgets.QCompleter(names, parent)
        c.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
        c.setFilterMode(QtCore.Qt.MatchContains)
        c.setMaxVisibleItems(14)
        return c
    model = QtCore.QStringListModel(list(names), parent)
    c = QtWidgets.QCompleter(model, parent)
    c.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
    c.setFilterMode(QtCore.Qt.MatchContains)
    c.setMaxVisibleItems(14)
    return c


class _MultiListField(QtWidgets.QWidget):
    """A comma list picked from a list (a pane's Content Types): the text box
    plus a drop-down of check boxes; ticking one adds it, unticking removes it."""

    def __init__(self, items: List[str], value: str, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(_sp(0))
        self.edit = QtWidgets.QLineEdit(value)
        self.button = QtWidgets.QToolButton()
        self.button.setArrowType(QtCore.Qt.DownArrow)
        self.button.setToolTip("Choose from the list (several allowed, separated by commas)")
        self.button.setAccessibleName("Choose from the list")
        self.button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self._menu = QtWidgets.QMenu(self.button)
        self._items = list(items)
        for it in self._items:
            a = self._menu.addAction(it)
            a.setCheckable(True)
            a.toggled.connect(lambda on, t=it: self._toggle(t, on))
        self._menu.aboutToShow.connect(self._sync)
        self.button.setMenu(self._menu)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.button)

    def _parts(self) -> List[str]:
        return [p.strip() for p in self.edit.text().split(",") if p.strip()]

    def _sync(self) -> None:
        have = {p.lower() for p in self._parts()}
        for a in self._menu.actions():
            a.blockSignals(True)
            a.setChecked(a.text().lower() in have)
            a.blockSignals(False)

    def _toggle(self, item: str, on: bool) -> None:
        parts = self._parts()
        low = [p.lower() for p in parts]
        if on and item.lower() not in low:
            parts.append(item)
        elif not on and item.lower() in low:
            parts = [p for p in parts if p.lower() != item.lower()]
        self.edit.setText(",".join(parts))

    def text(self) -> str:
        return self.edit.text()

    def setText(self, text: str) -> None:
        self.edit.setText(text)

    def setMaxLength(self, n: int) -> None:
        self.edit.setMaxLength(n)


class _BrowseField(QtWidgets.QWidget):
    """A text box with a 'Browse…' button that picks the value from a list
    (equipment, a menu item, a tab bar -- ui/panels/sa_pickers.py)."""

    def __init__(self, value: str, browse: Callable[[str], Optional[str]], tip: str = "", parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(_sp(1))
        self.edit = QtWidgets.QLineEdit(value)
        self.button = QtWidgets.QPushButton("Browse…")
        self.button.setAutoDefault(False)
        self.button.setToolTip(tip or "Choose from a list")
        self._browse = browse
        self.button.clicked.connect(self.browse)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.button)

    def browse(self) -> None:
        new = self._browse(self.edit.text())
        if new is not None:
            self.edit.setText(new)

    def text(self) -> str:
        return self.edit.text()

    def setText(self, text: str) -> None:
        self.edit.setText(text)

    def setMaxLength(self, n: int) -> None:
        self.edit.setMaxLength(n)

    def setCompleter(self, c) -> None:
        self.edit.setCompleter(c)


#: a form field that widens with its window: a field in the right-hand column
#: of its section (nothing there starts right of it) that is this many columns
#: wide (braces included) and holds this many characters
GROW_COLUMNS = 12
GROW_SIZE = 16
#: a drop-down whose values are all this short (TRUE / FALSE, 1 to 8) keeps
#: its size (UI guide section 9)
SHORT_CHOICE = 8
#: the list forms of the install's fixed help lists (io/project.list_values)
_HELP_LISTS = ("help", "scadahelp")


def _separator_text(text: str) -> bool:
    """A prompt drawn as a rule (a line of underscores or dashes)."""
    t = text.strip()
    return len(t) >= 3 and set(t) <= set("_-=~")


def _form_sections(form) -> Dict[int, int]:
    """line -> section of a FORM's NORMAL layout: runs of lines between rules,
    blank lines and lines that hold only a separator prompt."""
    by_line: Dict[int, list] = {}
    for it in form.layout:
        by_line.setdefault(it.line, []).append(it)
    out: Dict[int, int] = {}
    sid = 0
    for ln in range(1, max(by_line, default=0) + 1):
        items = by_line.get(ln, [])
        if not items or all(
            it.kind == "line" or (it.kind == "text" and _separator_text(it.text)) for it in items
        ):
            sid += 1
            continue
        out[ln] = sid
    return out


def _short_choice(items) -> bool:
    """True when every value of a drop-down is a short choice."""
    items = [str(i) for i in (items or [])]
    return bool(items) and max(len(i) for i in items) <= SHORT_CHOICE


class _FormCanvas(QtWidgets.QWidget):
    """A genie form's layout with every prompt and field at its .frm position."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: List[tuple] = []  # (widget, base rect, grows)
        self._natural = QtCore.QSize(0, 0)
        self.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)

    def add(self, widget: QtWidgets.QWidget, rect: QtCore.QRect, grows: bool = False) -> None:
        widget.setParent(self)
        widget.setGeometry(rect)
        self._items.append((widget, QtCore.QRect(rect), bool(grows)))

    def set_natural_size(self, size: QtCore.QSize) -> None:
        self._natural = QtCore.QSize(size)
        self.resize(self._natural.expandedTo(self.size()) if self.isVisible() else self._natural)
        self.updateGeometry()

    def natural_size(self) -> QtCore.QSize:
        return QtCore.QSize(self._natural)

    def growing(self) -> List[QtWidgets.QWidget]:
        """The items that take extra width."""
        return [w for w, _r, g in self._items if g]

    def base_rect(self, widget: QtWidgets.QWidget) -> Optional[QtCore.QRect]:
        for w, r, _g in self._items:
            if w is widget:
                return QtCore.QRect(r)
        return None

    def sizeHint(self) -> QtCore.QSize:  # noqa: N802 -- Qt naming
        return QtCore.QSize(self._natural)

    def minimumSizeHint(self) -> QtCore.QSize:  # noqa: N802 -- Qt naming
        return QtCore.QSize(self._natural)

    def resizeEvent(self, ev) -> None:  # noqa: N802 -- Qt naming
        super().resizeEvent(ev)
        extra = max(0, self.width() - self._natural.width())
        for w, r, grows in self._items:
            g = QtCore.QRect(r)
            if grows:
                g.setWidth(r.width() + extra)
            if w.geometry() != g:
                w.setGeometry(g)


class _FormScroll(QtWidgets.QScrollArea):
    """The scroll area of a genie form: it asks for the whole form (Qt caps a
    scroll area's size hint at about 540 x 360), and it can be made small."""

    MINIMUM = QtCore.QSize(240, 120)

    def sizeHint(self) -> QtCore.QSize:  # noqa: N802 -- Qt naming
        w = self.widget()
        if w is None:
            return super().sizeHint()
        f = 2 * self.frameWidth()
        return w.sizeHint() + QtCore.QSize(f, f)

    def minimumSizeHint(self) -> QtCore.QSize:  # noqa: N802 -- Qt naming
        # (a form smaller than the minimum asks for its own size: no empty
        # band between a three-line form and the buttons)
        return QtCore.QSize(self.MINIMUM).boundedTo(self.sizeHint())

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802 -- Qt naming
        # the content changed size ('Other parameters' opened): the hints above
        # change with it (QScrollArea itself only updates its scroll bars)
        if ev.type() == QtCore.QEvent.Type.LayoutRequest and obj is self.widget():
            self.updateGeometry()
        return super().eventFilter(obj, ev)


def _set_text(w: QtWidgets.QWidget, value: str) -> None:
    """Show ``value`` in a row's widget, never cut by its maximum length."""
    edit = w.lineEdit() if isinstance(w, QtWidgets.QComboBox) else getattr(w, "edit", w)
    if isinstance(edit, QtWidgets.QLineEdit) and edit.maxLength() < len(value):
        edit.setMaxLength(len(value))
    if isinstance(w, QtWidgets.QComboBox):
        w.setEditText(value)
    elif hasattr(w, "setText"):
        w.setText(value)
    _show_start(w)


def _show_start(w: QtWidgets.QWidget) -> None:
    """A value longer than its field shows its start, not its end (a line
    edit scrolls to the cursor, which setText puts at the end)."""
    edit = w.lineEdit() if isinstance(w, QtWidgets.QComboBox) else getattr(w, "edit", w)
    if isinstance(edit, QtWidgets.QLineEdit):
        edit.setCursorPosition(0)


class GenieParamsForm(QtWidgets.QWidget):
    """The rows of a genie dialog: the genie's FORM (``form``) or the default
    one-row-per-parameter form. ``params`` = the parameter names the genie
    declares -> their current value (the dialog's starting values)."""

    def __init__(
        self,
        params,
        *,
        form=None,
        list_values: Optional[Callable] = None,
        tag_names=None,
        an: Optional[int] = None,
        descriptions: Optional[Dict[str, str]] = None,
        parent=None,
        pickers=None,
        uses=None,
    ):
        super().__init__(parent)
        self.form = form
        self._params = OrderedDict(params or {})
        #: where each parameter goes in the genie (features/genie_uses.ParamUses by
        #: lower-case name; None = not known): what kind of field it gets
        self.uses = uses
        self._list_values = list_values
        # ui/panels/sa_pickers.ParamPickers: equipment / menu / pane pickers for the
        # parameters a form gives no list (None = plain text boxes)
        self._pickers = pickers
        self._an = an
        self._desc = descriptions or {}
        self._names_model = tag_names if _is_scope_model(tag_names) else None
        self._context = None
        self._completer = _completer(tag_names, self)
        self._decl = {k.lower(): k for k in self._params}
        #: parameter name -> (text getter, widget)
        self.editors: "OrderedDict[str, tuple]" = OrderedDict()
        self.combos: Dict[str, QtWidgets.QComboBox] = {}
        #: the parameters whose drop-down holds only short choices
        self._short: set = set()
        self.an_editor: Optional[QtWidgets.QLineEdit] = None
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.other_box: Optional[QtWidgets.QGroupBox] = None
        if form is not None:
            self.canvas = self._build_form(form)
            # the form, then 'Other parameters' under it, in one scroll area: a
            # smaller window scrolls, a larger one widens the form's long fields
            content = QtWidgets.QWidget()
            cl = QtWidgets.QVBoxLayout(content)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.addWidget(self.canvas)
            on_form = {self._param_name(f.name).lower() for f in form.placed_fields()}
            rest = [k for k in self._params if k.lower() not in on_form]
            if rest:
                box = QtWidgets.QGroupBox("Other parameters (not on this form)")
                box.setCheckable(True)
                box.setChecked(False)
                box.setToolTip("Parameters the genie has that its form does not show")
                inner = QtWidgets.QWidget()
                fl = QtWidgets.QFormLayout(inner)
                fl.setLabelAlignment(QtCore.Qt.AlignRight)
                for k in rest:
                    w = self._editor_for(k, self._params[k], None)
                    fl.addRow(k + ":", w)
                bl = QtWidgets.QVBoxLayout(box)
                bl.addWidget(inner)
                inner.setVisible(False)
                box.toggled.connect(inner.setVisible)
                self.other_box = box
                cl.addWidget(box)
            cl.addStretch(1)
            scroll = _FormScroll()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
            scroll.setWidget(content)
            self.scroll = scroll
            lay.addWidget(scroll, 1)
        else:
            self.canvas = None
            inner = QtWidgets.QWidget()
            fl = QtWidgets.QFormLayout(inner)
            fl.setLabelAlignment(QtCore.Qt.AlignRight)
            for k, v in self._params.items():
                w = self._editor_for(k, v, None)
                label = QtWidgets.QLabel(_LABELS.get(k, k) + ":")
                tip = self._description(k)
                if tip:
                    label.setToolTip(tip)
                fl.addRow(label, w)
            scroll = QtWidgets.QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
            scroll.setWidget(inner)
            self.scroll = scroll
            lay.addWidget(scroll, 1)

    # ----------------------------------------------------------------- editors
    def _param_name(self, name: str) -> str:
        """A form field's parameter as the genie declares it (case-insensitive
        -- substitution ignores ASCII case), else the form's spelling."""
        return self._decl.get(name.lower(), name)

    def _description(self, *labels) -> str:
        from ...io.genie_help import norm_label

        for lab in labels:
            d = self._desc.get(norm_label(lab))
            if d:
                return d
        return ""

    def _editor_for(self, name: str, value: str, fld) -> QtWidgets.QWidget:
        """The widget of one parameter (``fld`` = its form field, if any)."""
        value = "" if value is None else str(value)
        if name.upper() == "AN":
            ed = QtWidgets.QLineEdit("" if self._an is None or self._an < 0 else str(self._an))
            if self._an is None:
                ed.setPlaceholderText("Set when the genie is placed")
            ed.setReadOnly(True)
            ed.setToolTip(AN_TIP)
            set_note_role(ed, "text_dim")
            self.an_editor = ed
            return ed
        limit = fld.size if (fld is not None and fld.size > 0) else DEFAULT_MAX
        limit = max(limit, len(value))  # never cut a stored value
        items = None
        tag_list = (
            fld is not None
            and fld.has_list
            and fld.list_form.strip().lower() in _TAG_LIST_FORMS
            and self._names_model is not None
        )
        if (
            fld is not None
            and fld.has_list
            and fld.list_form.strip().lower() in _HELP_LISTS
            and fld.list_field.strip().lower() == "fonttype"
        ):
            items = QtGui.QFontDatabase.families() or None
        elif fld is not None and fld.has_list and self._list_values is not None and not tag_list:
            try:
                items = self._list_values(fld)
            except Exception:  # noqa: BLE001 -- a list is a convenience
                items = None
        tip = self._description(*(([self.form.label_for(fld.number)] if fld is not None else []) + [name]))
        spec = None
        if items is None and self._pickers is not None:
            try:
                spec = self._pickers.for_param(name, value)
            except Exception:  # noqa: BLE001 -- a picker is a convenience
                spec = None
            if spec is not None and spec.kind == "combo":
                items = spec.items
            tip = tip or (spec.tip if spec is not None else "")
        multi = items is not None and fld is not None and fld.list_form.strip().lower() == "contype"
        if spec is not None and spec.kind == "multi":
            items, multi = spec.items, True
        if spec is not None and spec.kind == "browse":
            # (a weak reference: the field must not keep its form alive;
            # a parentless form in a reference cycle can crash PySide's gc)
            form_ref = weakref.ref(self)

            def browse(cur, s=spec):
                form = form_ref()
                return s.browse(form, cur, form.values()) if form is not None else None

            factory = getattr(spec, "factory", None)
            w = (
                factory(value, browse, spec.tip)
                if factory is not None
                else _BrowseField(value, browse, spec.tip)
            )
            w.setMaxLength(limit)
            if self._completer is not None and factory is None:
                w.setCompleter(self._completer)
            getter = w.text
        elif multi:
            w = _MultiListField(items, value)
            w.setMaxLength(limit)
            getter = w.text
        elif items is not None:
            w = QtWidgets.QComboBox()
            w.setEditable(True)
            w.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
            w.setMaxVisibleItems(16)
            w.setModel(QtCore.QStringListModel(list(items), w))
            w.lineEdit().setMaxLength(limit)
            w.setEditText(value)
            c = QtWidgets.QCompleter(w.model(), w)
            c.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
            c.setFilterMode(QtCore.Qt.MatchContains)
            w.setCompleter(c)
            w.setMinimumContentsLength(4)
            w.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
            if fld is not None and fld.list_form.strip().lower() in _HELP_LISTS and _short_choice(items):
                # a fixed list of short choices (TRUE / FALSE, 1 to 8) keeps its
                # size (UI guide section 9); names from the project (pages, panes)
                # grow whatever their length
                self._short.add(name)
            elif limit >= GROW_SIZE:
                # long values (pages, content types, labels) take the row's width
                w.setSizePolicy(QtWidgets.QSizePolicy.Expanding, w.sizePolicy().verticalPolicy())
            self.combos[name] = w
            getter = w.currentText
        else:
            w = self._value_editor(name, value, limit)
            getter = w.text
        if tip:
            w.setToolTip(tip)
        _show_start(w)
        self.editors[name] = (getter, w)
        return w

    def param_uses(self, name: str):
        """The ParamUses of parameter ``name`` (None: not known)."""
        return self.uses.get(name.lower()) if self.uses else None

    def _value_editor(self, name: str, value: str, limit: int) -> QtWidgets.QWidget:
        """The text box of a parameter without a list, made for what the genie does with
        its value (features/genie_uses): a command or an expression gets the Wizard's tags
        and functions and the tag advice; a part of tag names ('%Tag%_PV') completes the
        values that make the scope's tags; a text or a setting is a plain box. A parameter
        whose uses are not known completes tag names. A field whose parameter goes into the
        genie's Cicode is checked by compiling that Cicode with the form's values
        (:class:`GenieCicodeCheck`)."""
        from ...features import genie_uses as GU

        use = self.param_uses(name)
        role = use.role if use is not None else ""
        plain = role in (GU.ROLE_TEXT, GU.ROLE_OTHER)
        if self._names_model is None or plain:
            w = QtWidgets.QLineEdit(value)
            w.setMaxLength(limit)
            if self._completer is not None and not plain:
                w.setCompleter(self._completer)
            return w
        from ..objprops.expression import ExpressionContext, ExpressionField, NameCompletionModel

        if self._context is None:
            self._context = ExpressionContext(self._names_model.project_dir)
        if role in (GU.ROLE_COMMAND, GU.ROLE_EXPRESSION):
            kind = "command" if role == GU.ROLE_COMMAND else "expr"
            w = ExpressionField(self._context, wizard=("tag", "function"), field_kind=kind)
        else:
            w = ExpressionField(self._context, wizard=("tag",), field_kind="tag")
        w.setText(value)
        w.setMaxLength(limit)
        if role == GU.ROLE_NAME and use.patterns:
            w.set_names(NameCompletionModel(self._names_model.project_dir, use.patterns, w))
        else:
            w.set_names(self._names_model)
        if use is not None and any(u.kind in GenieCicodeCheck.KINDS for u in use.uses):
            checker = checker_for(self._names_model.project_dir)
            if checker is not None:
                w.set_checker(GenieCicodeCheck(self, name, checker))
        return w

    def _build_form(self, form) -> "_FormCanvas":
        """Lay out the form at CtDraw's positions, scaled to this font."""
        canvas = _FormCanvas()
        fm = QtGui.QFontMetrics(canvas.font())
        probe = QtWidgets.QLineEdit()
        edit_h = max(probe.sizeHint().height(), fm.height() + 6)
        # a column: CtDraw's 4 DLU = one average character of its dialog font;
        # here as wide as the widest prompt needs per character (the prompts
        # are padded for the Graphics Builder's narrower font)
        col = float(fm.averageCharWidth())
        for it in form.layout:
            t = it.text.rstrip()
            if it.kind == "text" and len(t) >= 3:
                col = max(col, (fm.horizontalAdvance(t) + 2) / float(len(t)))
        col = min(col, 2.0 * fm.averageCharWidth())
        px = max(1.0, col / 4.0)
        py = max(fm.height() / 8.0, (edit_h + 6) / 15.0)
        order: List[QtWidgets.QWidget] = []
        right = bottom = 0
        placed = []  # (widget, rect, layout item, field)
        rules = []  # (widget, x, y, width)
        for it in form.layout:
            x, y, cx, cy = it.dlu_rect()
            if it.kind == "text":
                lab = QtWidgets.QLabel(it.text.rstrip())
                lab.setTextFormat(QtCore.Qt.PlainText)
                h = fm.height() + 2
                top = int((it.line * 15 - 6) * py - h / 2)
                w = int(max(cx * px, 8))
                rect = QtCore.QRect(int(x * px), top, w, h)
                placed.append((lab, rect, it, None))
                right, bottom = max(right, rect.right() + 1), max(bottom, top + h)
                continue
            if it.kind == "line":
                rule = QtWidgets.QFrame()
                rule.setFrameShape(QtWidgets.QFrame.HLine)
                rule.setFrameShadow(QtWidgets.QFrame.Sunken)
                rules.append((rule, 0, int((it.line * 15 - 5) * py) - 1, int(max(cx * px, 8))))
                continue
            fld = form.field_by_number(it.field)
            if fld is None:
                continue
            name = self._param_name(fld.name)
            if name in self.editors:  # one field placed twice: the first
                continue
            w = self._editor_for(name, self._params.get(name, ""), fld)
            top = int((it.line * 15 - 6) * py - edit_h / 2)
            width = int(max(cx * px, 3 * fm.averageCharWidth()))
            rect = QtCore.QRect(int(x * px), top, width, edit_h)
            placed.append((w, rect, it, fld))
            order.append(w)
            right, bottom = max(right, rect.right() + 1), max(bottom, top + edit_h)
        # a field grows when it is long (12 columns or more, a value of 16
        # characters or more, not a drop-down of short choices) and sits in the
        # right-hand column of its section: no prompt or field of the section
        # starts right of it (so a left-column field never grows under the
        # right-hand column). Sections are split by rules, blank lines and
        # lines of underscores / dashes.
        section = _form_sections(form)
        lefts: Dict[int, List[int]] = {}
        for _w, r, it, _f in placed:
            lefts.setdefault(section.get(it.line, -1), []).append(r.left())
        for w, rect, it, fld in placed:
            name = self._param_name(fld.name) if fld is not None else ""
            grows = (
                fld is not None
                and it.width >= GROW_COLUMNS
                and fld.size >= GROW_SIZE
                and name not in self._short
                and not any(x > rect.right() for x in lefts.get(section.get(it.line, -1), ()))
            )
            canvas.add(w, rect, grows)
        for rule, x, y, width in rules:
            # a rule longer than the form is cut at its right edge, and one that
            # reaches the edge follows the form's width
            reach = right - x
            canvas.add(rule, QtCore.QRect(x, y, max(8, min(width, reach)), 3), width >= reach)
            bottom = max(bottom, y + 3)
        for a, b in zip(order, order[1:]):
            QtWidgets.QWidget.setTabOrder(a, b)
        canvas.set_natural_size(QtCore.QSize(right + 8, bottom + 6))
        return canvas

    # ------------------------------------------------------------------ values
    def values(self) -> "OrderedDict[str, str]":
        """parameter -> value of every editable row (the AN row is not a value)."""
        return OrderedDict((k, get()) for k, (get, _w) in self.editors.items())

    def changed_values(self, original: Optional[dict] = None) -> "OrderedDict[str, str]":
        """Only the parameters whose value differs from ``original`` (default:
        the starting values); a name is matched case-insensitively."""
        orig = original if original is not None else self._params
        low = {str(k).lower(): str(v) for k, v in (orig or {}).items()}
        return OrderedDict((k, v) for k, v in self.values().items() if low.get(k.lower(), "") != v)

    def editor(self, name: str) -> Optional[QtWidgets.QWidget]:
        hit = self.editors.get(name) or self.editors.get(self._param_name(name))
        return hit[1] if hit else None

    def _loaded(self, name: str) -> str:
        """The value a row was loaded with (case-insensitive)."""
        low = name.lower()
        for k, v in self._params.items():
            if str(k).lower() == low:
                return "" if v is None else str(v)
        return ""

    def has_pending_edits(self) -> bool:
        """True when a row shows something else than it was loaded with."""
        return any(get() != self._loaded(k) for k, (get, _w) in self.editors.items())

    def set_values(self, values) -> None:
        """New values from the page (an edit, Undo, Replace): a row the user
        has not changed shows its new value and takes it as its loaded value;
        a row the user typed in keeps what was typed (and its loaded value, so
        Apply still writes it)."""
        new = {str(k).lower(): ("" if v is None else str(v)) for k, v in (values or {}).items()}
        keys = {str(k).lower(): k for k in self._params}
        for name, (get, w) in self.editors.items():
            low = name.lower()
            if low not in new or get() != self._loaded(name):
                continue
            value = new[low]
            if get() != value:
                _set_text(w, value)
            self._params[keys.get(low, name)] = value


#: the most uses the panel lists, and the longest field it shows whole
_USES_LISTED = 6
_USE_CHARS = 140


def checker_for(project_dir: Optional[str]):
    """The Cicode check of ``project_dir`` (ui/expression_check; tests put in a fake)."""
    from ..expression_check import checker_for as _checker_for

    return _checker_for(project_dir)


class GenieCicodeCheck:
    """Does the genie's Cicode work with this value? The checker of a parameter's field:
    each command and expression of the genie the parameter goes into, with the form's
    values put in (the value checked for this parameter), compiled as the page compiler
    would, running nothing (ctsim/checkExpression). The parameters not filled in yet are
    left to the template rule (expression_check.without_template). Each problem names the
    field it is in."""

    KINDS = ("command", "expression")

    def __init__(self, form: "GenieParamsForm", name: str, checker):
        self._form = weakref.ref(form)
        self._name = name
        self._checker = checker

    def texts(self, value: str) -> List[tuple]:
        """(the field's label, its kind, its Cicode with the values) of each use."""
        from ...features import genie_uses as GU

        form = self._form()
        use = form.param_uses(self._name) if form is not None else None
        if use is None:
            return []
        values = {k.lower(): v for k, v in form.values().items()}
        values[self._name.lower()] = value
        out, seen = [], set()
        for u in use.uses:
            if u.kind not in self.KINDS:
                continue
            text = GU.substitute(u.template, values, keep_missing=True).strip()
            if text and (u.kind, text) not in seen:
                seen.add((u.kind, text))
                out.append((u.label, u.kind, text))
        return out

    def check(self, value: str, _kind: str, callback) -> None:
        jobs = self.texts(value)
        if not jobs:
            callback({"ok": True, "errors": [], "warnings": []})
            return
        got: Dict[int, tuple] = {}

        def done(k: int, label: str, result: dict) -> None:
            got[k] = (label, result)
            if len(got) == len(jobs):
                callback(_merged([got[i] for i in range(len(jobs))]))

        for k, (label, kind, text) in enumerate(jobs):
            self._checker.check(text, kind, lambda r, k=k, label=label: done(k, label, r))


def _merged(results) -> dict:
    """One result of the results of several fields [(label, result)]: their problems, each
    headed by its field (the column is the field's, not the value's: left out)."""
    errors, warnings = [], []
    for label, r in results:
        if r.get("error"):
            return {"error": r["error"]}
        for e in r.get("errors") or ():
            errors.append(dict(e, message="%s: %s" % (label, e.get("message") or ""), column=0))
        for w in r.get("warnings") or ():
            warnings.append(dict(w, message="%s: %s" % (label, w.get("message") or ""), column=0))
    return {"ok": not errors, "errors": errors, "warnings": warnings}


def uses_html(name: str, use, values: Dict[str, str]) -> str:
    """The uses panel's text for parameter ``name``: each field it goes into with the
    form's values in it (``values`` by lower-case name)."""
    from ...features import genie_uses as GU

    head = "<b>%s</b>" % _html.escape(name)
    if use is None:
        return ""
    if not use.uses:
        return "The genie does not use %s." % head
    lines = ["Where the genie uses %s:" % head]
    for u in use.uses[:_USES_LISTED]:
        text = " ".join(GU.substitute(u.template, values, keep_missing=True).split())
        if len(text) > _USE_CHARS:
            text = text[: _USE_CHARS - 1] + "\u2026"
        lines.append("%s: <code>%s</code>" % (_html.escape(u.label), _html.escape(text)))
    more = len(use.uses) - _USES_LISTED
    if more > 0:
        lines.append("And %d more." % more)
    return "<br>".join(lines)


def _descriptions(library: str, element: str):
    """(AVEVA topic id, field descriptions) of a system genie, or (None, {})."""
    try:
        from ...io import genie_help as GH

        tid = GH.topic_for(library, element)
        return tid, (GH.field_descriptions(tid) if tid else {})
    except Exception:  # noqa: BLE001 -- the reference is optional
        return None, {}


class _GenieDialogBase(QtWidgets.QDialog):
    """What both genie dialogs share: header, form rows, notes, buttons."""

    groupRequested = QtCore.Signal()
    #: the form scrolls and its long fields widen (UI guide section 9)
    RESIZE = "both"
    #: where a form's size and position are kept (one entry per genie)
    GEOMETRY_KEY = "geometry/GenieForm/"

    def _setup(
        self,
        ref: str,
        params,
        *,
        form,
        list_values,
        tag_names,
        an,
        group_button: bool,
        problems,
        default_title: str,
        pickers=None,
        uses=None,
    ) -> None:
        self.form = form
        self.ref = ref
        # sized to its own form: each genie's form keeps its own size (a size
        # remembered from another genie's form would not fit this one)
        if ref:
            self.setProperty("_ctgGeometryKey", self.GEOMETRY_KEY + ref)
        else:
            self.setProperty("_ctgNoGeometry", True)
        lib, _, elem = ref.rpartition(".")
        self.help_topic, desc = _descriptions(lib, elem)
        if form is not None:
            # the FORM's own title (CtDraw's caption); a form without one
            # gets the plain dialog title with the genie's name
            self.setWindowTitle(form.title or default_title)
        else:
            self.setWindowTitle(default_title)
            self.setMinimumWidth(460)
        lay = QtWidgets.QVBoxLayout(self)
        if ref:
            # the instance's AN in the header for every genie
            an_text = ""
            if an is not None and an >= 0:
                an_text = "&nbsp;&nbsp;AN %d" % an
            head = QtWidgets.QLabel("<b>%s</b>%s" % (_html.escape(ref), an_text))
            head.setToolTip(AN_TIP if an_text else "")
            head.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            head.setWordWrap(True)
            lay.addWidget(head)
        if form is None and problems:
            import os

            path, reason = problems[0]
            note = QtWidgets.QLabel(
                "The form for this genie in %s has an error (%s), so all parameters are "
                "listed." % (os.path.basename(path), reason)
            )
            note.setWordWrap(True)
            set_note_role(note, "warn")
            note.setToolTip(path)
            lay.addWidget(note)
        self.params_form = GenieParamsForm(
            params,
            form=form,
            list_values=list_values,
            tag_names=tag_names,
            an=an,
            descriptions=desc,
            parent=self,
            pickers=pickers,
            uses=uses,
        )
        lay.addWidget(self.params_form, 1)
        self.uses_label = None
        if uses:
            self._add_uses_panel(lay)
        if self.params_form.other_box is not None:
            # opening 'Other parameters' makes the window taller to show them
            # (within the screen; the form scrolls when they do not fit)
            # (a bound method: a lambda would keep the dialog alive)
            self.params_form.other_box.toggled.connect(self._other_toggled)
        if not params and form is None:
            note = QtWidgets.QLabel("This genie has no parameters.")
            note.setWordWrap(True)
            set_note_role(note, "text_dim")
            lay.addWidget(note)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.group_button = None
        if group_button:
            b = buttons.addButton("Group…", QtWidgets.QDialogButtonBox.ActionRole)
            b.setToolTip("Open the genie's Object Properties")
            b.clicked.connect(self.groupRequested.emit)
            self.group_button = b
        self.reference_button = None
        if self.help_topic:
            b = buttons.addButton("Genie Reference", QtWidgets.QDialogButtonBox.HelpRole)
            b.setToolTip("Open the Plant SCADA help for this genie")
            b.clicked.connect(self._open_reference)
            self.reference_button = b
        lay.addWidget(buttons)
        self.buttons = buttons
        first = next(iter(self.params_form.editors.values()), None)
        if first is not None:
            first[1].setFocus()
        if form is None:
            n = max(1, len(self.params_form.editors))
            self.resize(520, min(600, 150 + 34 * n))
        else:
            # the whole form at its natural size, within the screen (Qt would
            # open it at most two thirds of the screen)
            self.resize(self._open_size())

    def _add_uses_panel(self, lay) -> None:
        """Under the form: where the genie uses the parameter being edited, with the
        values typed."""
        lab = QtWidgets.QLabel()
        lab.setWordWrap(True)
        lab.setTextFormat(QtCore.Qt.RichText)
        lab.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        lab.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        lab.setMinimumHeight(4 * lab.fontMetrics().lineSpacing())
        lab.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        set_note_role(lab, "text_dim")
        lay.addWidget(lab)
        self.uses_label = lab
        self._uses_param = next(iter(self.params_form.editors), None)
        for _name, (_g, w) in self.params_form.editors.items():
            edit = w.lineEdit() if isinstance(w, QtWidgets.QComboBox) else getattr(w, "edit", w)
            if isinstance(edit, QtWidgets.QLineEdit):
                edit.textChanged.connect(self._update_uses)
        QtWidgets.QApplication.instance().focusChanged.connect(self._focus_moved)
        self._update_uses()

    def _focus_moved(self, _old, new) -> None:
        if new is None or self.uses_label is None:
            return
        for name, (_g, w) in self.params_form.editors.items():
            if w is new or w.isAncestorOf(new):
                if name != self._uses_param:
                    self._uses_param = name
                    self._update_uses()
                return

    def _update_uses(self, *_args) -> None:
        name = self._uses_param
        if self.uses_label is None or name is None:
            return
        values = {k.lower(): v for k, v in self.params_form.values().items()}
        self.uses_label.setText(uses_html(name, self.params_form.param_uses(name), values))

    def uses_text(self) -> str:
        """The uses panel's text, plain (tests)."""
        if self.uses_label is None:
            return ""
        doc = QtGui.QTextDocument()
        doc.setHtml(self.uses_label.text())
        return doc.toPlainText()

    def _open_size(self) -> QtCore.QSize:
        want = self.layout().totalSizeHint()
        screen = self.screen() or QtWidgets.QApplication.primaryScreen()
        if screen is None:
            return want
        room = screen.availableGeometry().size() - QtCore.QSize(16, 48)  # the frame
        if want.height() > room.height():
            # taller than the screen: the form scrolls down, so it needs room for
            # the vertical scroll bar (else a horizontal one appears as well)
            want.setWidth(
                want.width() + self.style().pixelMetric(QtWidgets.QStyle.PM_ScrollBarExtent, None, self)
            )
        return want.boundedTo(room).expandedTo(self.layout().totalMinimumSize())

    def _other_toggled(self, on: bool) -> None:
        if on:
            QtCore.QTimer.singleShot(0, self, self._fit_height)

    def _fit_height(self) -> None:
        """Taller, when the content needs it: kept on the screen."""
        try:
            if not self.isVisible():
                return
            # (the box's new size reaches this layout through a chain of
            # layout requests: let them all run first)
            QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.LayoutRequest)
            want = self.layout().totalSizeHint().height()
            if want <= self.height():
                return
            screen = self.screen() or QtWidgets.QApplication.primaryScreen()
            if screen is None:
                self.resize(self.width(), want)
                return
            avail = screen.availableGeometry()
            extra = self.frameGeometry().height() - self.height()
            h = min(want, avail.height() - max(0, extra))
            self.resize(self.width(), max(self.height(), h))
            over = self.frameGeometry().bottom() - avail.bottom()
            if over > 0:
                self.move(self.x(), max(avail.top(), self.y() - over))
        except RuntimeError:  # closed meanwhile
            pass

    def _open_reference(self) -> None:
        from ... import help as H

        QtGui.QDesktopServices.openUrl(QtCore.QUrl(H.aveva_target(self.help_topic)))

    @property
    def _editors(self) -> Dict[str, QtWidgets.QWidget]:
        return {k: w for k, (_g, w) in self.params_form.editors.items()}

    def values(self) -> "OrderedDict[str, str]":
        return self.params_form.values()

    def changed_values(self, original: Optional[dict] = None) -> "OrderedDict[str, str]":
        return self.params_form.changed_values(original)

    def has_pending_edits(self) -> bool:
        """True when a field shows something else than it was loaded with."""
        return self.params_form.has_pending_edits()

    def set_values(self, values) -> None:
        """New values from the page: the fields the user has not changed show
        them, typed fields keep what was typed."""
        self.params_form.set_values(values)


class GenieSubstitutionDialog(_GenieDialogBase):
    """Edit a placed genie's parameters."""

    def __init__(
        self,
        obj,
        tag_names=None,
        parent=None,
        bindings=None,
        *,
        form=None,
        list_values: Optional[Callable] = None,
        an: Optional[int] = None,
        group_button: bool = False,
        problems=(),
        ref: str = "",
        pickers=None,
        uses=None,
    ):
        super().__init__(parent)
        if not ref and obj is not None:
            ref = (
                getattr(obj, "genie_template", None)
                or obj.raw.get("_ref")
                or obj.raw.get("_resolved_ref")
                or ""
            )
        ref = _friendly_ref(str(ref))
        params = dict(bindings) if bindings is not None else dict(getattr(obj, "substitutions", None) or {})
        self._setup(
            ref,
            params,
            form=form,
            list_values=list_values,
            tag_names=tag_names,
            an=an,
            group_button=group_button,
            problems=problems,
            default_title="Genie Parameters" + (" - %s" % ref if ref else ""),
            pickers=pickers,
            uses=uses,
        )

    def retarget(self, index: int) -> None:
        """The genie now sits at ``index`` (an edit or a genie update moved it). Nothing to
        change: the window holds no index of its own, its OK applies where the editor
        window has followed the genie (ui/modeless.py; without this method the window
        would close on a move)."""


class GeniePasteDialog(_GenieDialogBase):
    """Paste Genie: the genie's parameters before it is placed (its FORM when
    ``form`` is given, else its declared parameters with their defaults --
    CtDraw's list = LibraryObjectFirstPropertyEx), with completion."""

    def __init__(
        self,
        title: str,
        params,
        tag_names=None,
        parent=None,
        *,
        form=None,
        list_values: Optional[Callable] = None,
        an: Optional[int] = None,
        problems=(),
        pickers=None,
        uses=None,
    ):
        super().__init__(parent)
        self._setup(
            title,
            OrderedDict(params or {}),
            form=form,
            list_values=list_values,
            tag_names=tag_names,
            an=an,
            group_button=False,
            problems=problems,
            default_title="Paste Genie - %s" % title,
            pickers=pickers,
            uses=uses,
        )

    def values(self) -> "OrderedDict[str, str]":
        """name -> value in declaration order (an empty value clears it)."""
        return self.params_form.values()
