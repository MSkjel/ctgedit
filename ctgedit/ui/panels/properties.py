"""The Properties panel."""

from __future__ import annotations
from ...format.cnum import c_atoi, is_digits
from ..style.theme import sp as _sp  # (the spacing scale)

import contextlib
import math

from PySide6 import QtCore, QtGui, QtWidgets

from ...format import inputs as IN
from ...model.document import ObjectKind
from ..number_field import NumberField, ScrubLabel, evaluate, round_half_away
from .properties_parts import (
    ADDABLE,
    ADDED_SECTION,
    ADD_PAGES,
    AXIS_ROWS,
    COMMAND_KEYS,
    DISPLAY_VALUE,
    EXPRESSION_MIN_WIDTH,
    EXPR_KEYS,
    FAMILY_DISABLE,
    FAMILY_DISPLAY,
    FAMILY_FILL_LEVEL,
    FAMILY_INPUT,
    FAMILY_MOVE,
    FAMILY_ROTATE,
    FAMILY_SCALE,
    FAMILY_VISIBILITY,
    FIELD_CLASSES,
    FIELD_MIN_WIDTH,
    FILL_DIRECTIONS,
    FieldKeys,
    GEOMETRY_MIN_WIDTH,
    INPUT_KEYBOARD,
    INPUT_TOUCH,
    JUSTIFY,
    KEY_LABELS,
    LABELS_ABOVE_UNDER,
    LABEL_LAYOUTS,
    LABEL_LAYOUT_KEY,
    LINE_STYLES,
    LOCK_ASPECT_KEY,
    MULTI_APPLY_KEY,
    PRIVATE_KEYS_KEY,
    PanelSection,
    QSS,
    REFERENCES,
    REFERENCE_KEY,
    REF_BY_NAME,
    ROW_TIPS,
    RefGrid,
    TEXT_EFFECTS,
    TOUCH_ROWS,
    chain_icon,
    geometry_reference,
    hatched_pixmap,
    is_colour_pair,
    label_layout,
    lock_aspect,
    match_font_family,
    multi_apply,
    panel_ALL_AREAS,
    panel_EFFECTS,
    panel_INPUT_TCS,
    panel_Pages,
    panel_is_field,
    panel_option,
    parse_colour_pair,
    search_keep,
    set_setting,
    show_private_keys,
    show_value_starts,
)
from .properties_widgets import (
    AddAnimationCombo,
    LabelledSwatch,
    MixedField,
    PanelSwatch,
    ValueSwatch,
    fill_level_companions,
    sheet_labels,
    styles_strip_tip,
)
from .properties_search import PanelSearch
from .properties_kept import KeptForms
from .properties_tool import ToolSection
from .properties_page import PageForm


# (the combo / spin arrows come from the application theme: theme.arrow_qss)


def _note_exc(where: str) -> None:
    from ...infra.logsetup import note_exception

    note_exception(where)


def _common_value(vals):
    """The value all of ``vals`` share, else None (shown as Mixed)."""
    vals = set(vals)
    return next(iter(vals)) if len(vals) == 1 else None


# --------------------------------------------------------------------------- the panel


class PropertyPanel(PageForm, ToolSection, KeptForms, PanelSearch, QtWidgets.QScrollArea):
    changed = QtCore.Signal()
    statusMessage = QtCore.Signal(str)
    editGenie = QtCore.Signal(int)  # open the genie dialog for object index
    editInput = QtCore.Signal(int)  # open the object sheet on its Input tab
    editPage = QtCore.Signal(int, str)  # open the object sheet on a page (registry id)
    #: 'Edit Definition' on a read-only object (a genie member / template record)
    editDefinition = QtCore.Signal(int)
    #: 'Properties…' on a read-only object: its (read-only) property sheet
    showSheet = QtCore.Signal(int)
    #: select these objects on the page (a use in a genie's Inputs list)
    selectObjects = QtCore.Signal(list)

    #: the width the dock opens with on a first run (a saved window layout wins)
    PREFERRED_WIDTH = 300

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        # the panel fits its dock's width (fields shrink, long texts elide): no sideways scroll
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setStyleSheet(QSS)
        self._doc = None
        self._indices: list[int] = []
        self._building = False
        # one ExpressionContext per document (its tag lists are shared by
        # every expression field of the panel)
        self._context = None
        self._context_doc = None
        self._geom: dict[str, QtWidgets.QSpinBox] = {}
        self._geom_ref = None  # the reference point the X / Y fields show
        self._show_touch: set = set()  # (doc id, index) with an empty touch section shown
        #: ``fn(index) -> str``: why the object is read-only here ('' = editable);
        #: the window sets it (it knows whether templates are locked)
        self.read_only_reason = None
        #: ``fn(index) -> str``: the 'lib.element' Edit Definition opens, or ''
        self.definition_label = None
        self._ro_reason = ""
        self._built_key = None
        # the page forms, one per document: id(doc) -> (weak reference, _page_key, root);
        # a deselect or a tab switch shows the kept one (moving it into a new root
        # re-polishes it)
        self._page_forms: dict = {}
        self._object_root = None  # the object form shown or last shown
        #: the object forms shown before, kept while the document is as it was (a
        #: selection that comes back to an object shows its form again, not a new one):
        #: build key -> (root, (geom, multi_geom, ro_reason)), the newest last; all for
        #: one :meth:`_kept_state`, dropped when that changes (see :meth:`_kept_form`)
        self._kept_forms: dict = {}
        self._kept_for = None
        # the X / Y fields of a selection of several objects: (roots, x, y, (w, h))
        self._multi_geom = None
        self._keys = FieldKeys(self)
        self._label_mode = None  # 'beside' / 'above' as the forms are laid out
        self._pending_focus = None  # (section title, field key) after Add animation
        #: the widget the sections of the form being built are made in (_build_root)
        self._form_parent = None
        #: the drawing tool armed ('select' = none): with nothing selected the panel shows
        #: the tool's 'New <Kind>' section (ui/editor/draw_styles)
        self._tool = "select"
        #: the next colour picker opens with the keyboard in its colour entry (a click on a
        #: swatch's value text)
        self._entry_focus = False
        self._stack = panel_Pages()
        self.setWidget(self._stack)
        # the property search, pinned above the rows (it stays when the form is built
        # again): in the scroll area's top margin
        from ..style import theme

        self.search = theme.SearchField("Search properties", self)
        self.search.setObjectName("propSearch")
        self.search.setToolTip(
            "Find a property by its name, its key or its value. Also "
            "lists the Object Properties pages that have it. Ctrl+F in "
            "the panel, Esc clears"
        )
        self.search.setProperty("propKey", "@panelSearch")
        from ..help import set_help

        set_help(self.search, "properties#search-properties")
        self.search.searchChanged.connect(self._on_search)
        self.search.installEventFilter(self._keys)
        self._search_box = None  # the 'In Object Properties' links shown
        self._search_bar = QtWidgets.QWidget(self)
        self._search_bar.setObjectName("propSearchBar")
        bar = QtWidgets.QHBoxLayout(self._search_bar)
        bar.setContentsMargins(_sp(1), _sp(1), _sp(1), _sp(0))
        bar.addWidget(self.search)
        self._search_bar.setVisible(self._doc is not None)  # (shown once a page is)
        self._place_search()
        # (Last used: the look of the object changed here is the next one's)
        self.changed.connect(self._record_last_edit)
        self._rebuild()

    def _place_search(self) -> None:
        """The search bar in the scroll area's top margin, as wide as the panel (none while
        no page is open: there is nothing to search, and the start page has New and Open)."""
        if self._search_bar.isHidden():
            self.setViewportMargins(0, 0, 0, 0)
            return
        h = self._search_bar.sizeHint().height()
        self.setViewportMargins(0, h, 0, 0)
        fw = self.frameWidth()
        self._search_bar.setGeometry(fw, fw, max(1, self.width() - 2 * fw), h)

    def sizeHint(self) -> QtCore.QSize:  # noqa: D102 -- the dock's first-run width
        s = super().sizeHint()
        return QtCore.QSize(self.PREFERRED_WIDTH, s.height())

    # -- public API ------------------------------------------------------------------
    def widget(self) -> QtWidgets.QWidget:  # noqa: D102 -- QScrollArea.widget
        """The form shown (the page form of the document, or the object form)."""
        stack = self.__dict__.get("_stack")
        if stack is None:
            return super().widget()
        return stack.currentWidget()

    def set_target(self, doc, indices: list) -> None:
        """Point the panel at a document + selection (indices may be empty)."""
        if indices is None:
            indices = []
        elif isinstance(indices, int):
            indices = [indices]
        indices = [int(i) for i in indices]
        if doc is not self._doc or indices != self._indices:
            # arrow steps still waiting for their pause belong to the form shown: they are
            # committed while the panel still points at its document and selection (else a
            # tab switch inside the pause edits the page switched to, a selection change the
            # objects selected now)
            self.flush_pending_steps()
            self.preview_end()
            if not search_keep() and self.search.text():
                self.search.clear()
        self._doc = doc
        self._indices = indices
        if self._search_bar.isHidden() != (doc is None):
            self._search_bar.setVisible(doc is not None)
            self._place_search()
        if doc is not self._context_doc:
            self._context = None
            self._context_doc = doc
        key = self._build_key()
        if key is not None and key == self._built_key:
            return  # nothing it shows changed
        if self._moved_in_place(key):
            self._built_key = key
            return
        self._rebuild()

    def show_pending(self) -> None:
        """Show nothing until the window asks for a document's form again (a large page's
        form comes after its first paint); the kept page forms stay."""
        self.flush_pending_steps()
        root = self._new_root()
        root.layout().addStretch(1)
        self._stack.addWidget(root)
        self._stack.setCurrentWidget(root)
        old, self._object_root = self._object_root, root
        if old is not None and old is not root:
            self._drop_root(old)
        self._geom = {}
        self._multi_geom = None
        self._built_key = None

    def section(self, title: str):
        """The section of the form shown whose title is ``title`` (or starts with it and a
        ' ('), else None."""
        root = self.widget()
        if root is None:
            return None
        for sec in root.findChildren(PanelSection):
            t = sec.title()
            if t == title or t.startswith(title + " ("):
                return sec
        return None

    def focus_canvas(self) -> None:
        """Give the keyboard to the page (a second Esc in a field)."""
        win = self.window()
        view = getattr(win, "view", None) if win is not self else None
        if isinstance(view, QtWidgets.QWidget):
            view.setFocus(QtCore.Qt.ShortcutFocusReason)
            return
        fw = QtWidgets.QApplication.focusWidget()
        if fw is not None and self.isAncestorOf(fw):
            fw.clearFocus()

    def _take_entry_focus(self) -> bool:
        """The colour picker about to open starts in its colour entry (once)."""
        on, self._entry_focus = self._entry_focus, False
        return bool(on)

    # -- B I U S and the alignment -------------------------------------------------------------
    def _style_row(self, flags: dict, justify, on_flag, on_justify, mixed: dict = None) -> QtWidgets.QWidget:
        """B I U S, then Left / Centre / Right (one exclusive group). ``on_flag(flag, on)``,
        ``on_justify(name)``; ``mixed``: the flags some of the texts have (unchecked, the
        tool tip says so); ``justify`` None = mixed."""
        from ..flow_layout import FlowLayout
        from ..style.theme import icon

        row = QtWidgets.QWidget()
        # (a narrow dock wraps the alignment under B I U S)
        sh = FlowLayout(row, spacing=2)
        mixed = mixed or {}
        for flag, glyph, tip in (
            ("bold", "bold", "Bold"),
            ("italic", "italic", "Italic"),
            ("underline", "underline", "Underline"),
            ("strikeout", "strikeout", "Strikeout"),
        ):
            tb = QtWidgets.QToolButton()
            tb.setIcon(icon(glyph))
            tb.setToolTip(tip + (". Some of the selected texts" if mixed.get(flag) else ""))
            tb.setAccessibleName(tip)
            tb.setCheckable(True)
            tb.setChecked(bool(flags.get(flag)))
            tb.setProperty("propKey", flag)
            tb.toggled.connect(lambda v, fl=flag: on_flag(fl, v))
            sh.addWidget(tb)
        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.VLine)
        sep.setFixedSize(6, 20)
        sh.addWidget(sep)
        group = QtWidgets.QButtonGroup(row)
        group.setExclusive(justify is not None)
        for name, tip, glyph in JUSTIFY:
            tb = QtWidgets.QToolButton()
            tb.setIcon(icon(glyph))
            tb.setToolTip("Align %s" % tip.lower() if name != "centre" else "Centre")
            tb.setAccessibleName({"left": "Align left", "centre": "Centre", "right": "Align right"}[name])
            tb.setCheckable(True)
            tb.setChecked(name == justify)
            tb.setProperty("propKey", "justify_" + name)
            group.addButton(tb)
            tb.clicked.connect(lambda _=False, n=name, g=group: (g.setExclusive(True), on_justify(n)))
            sh.addWidget(tb)
        row.setProperty("propKey", "text_style")
        return row

    # -- the styles' hover preview ------------------------------------------------------------
    def preview_end(self) -> None:
        """A selection change ends the canvas preview of a style being pointed at."""
        win = self.window()
        fn = getattr(win, "preview_style", None) if win is not self else None
        if callable(fn) and getattr(win, "_style_preview", None) is not None:
            with contextlib.suppress(RuntimeError):
                fn("")

    def _styles_strip(self):
        """The named styles' tiles (ui/panels/styles_gallery) for the head of the Appearance
        section; None without a window or without styles."""
        win = self.window()
        if win is self or not hasattr(win, "style_actions"):
            return None
        from .styles_gallery import StylesGallery, store

        if not store().all():
            return None
        g = StylesGallery(
            "strip",
            None,
            actions_fn=win.style_actions,
            preview_fn=win.preview_style,
            menu_fn=win.style_menu_for,
            page_colour_fn=win._page_colour,
        )
        g.setObjectName("panelStyles")
        g.setToolTip(styles_strip_tip())
        return g

    # -- the last used look -------------------------------------------------------------------
    def _record_last_edit(self) -> None:
        """Last used (opt/new_object_style): the look of the object just changed here is
        what the next one of its kind takes."""
        from ..editor import draw_styles as DS

        if self._doc is None or len(self._indices) != 1 or DS.mode() != "last":
            return
        try:
            i = self._indices[0]
            kind = DS.kind_of_object(self._doc.page.objects[i])
            if kind is not None:
                DS.record_last(kind, self._doc.appearance_of(i))
        except Exception:  # noqa: BLE001  # swallow-ok: a look not kept is only a convenience
            _note_exc("properties.record_last")

    # -- the label layout --------------------------------------------------------------------
    def _width_now(self) -> int:
        # the dock's width (not the viewport's: a scroll bar coming and going must not
        # switch the labels between beside and above)
        return self.width()

    def label_mode(self) -> str:
        """'beside' or 'above': how the forms lay out their labels now."""
        mode = label_layout()
        if mode == "auto":
            return "above" if self._width_now() < LABELS_ABOVE_UNDER else "beside"
        return mode

    def style_form(self, form: QtWidgets.QFormLayout, mode: str = "") -> None:
        """Lay out ``form`` for the label mode (``mode``: the panel's now)."""
        mode = mode or self.label_mode()
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        if mode == "above":
            form.setRowWrapPolicy(QtWidgets.QFormLayout.WrapAllRows)
            form.setLabelAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
            form.setVerticalSpacing(_sp(0))
        else:
            form.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
            form.setLabelAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            form.setVerticalSpacing(_sp(1))
        form.setHorizontalSpacing(_sp(2))

    def _roots(self) -> list:
        out = [ent[2] for ent in self._page_forms.values()]
        if self._object_root is not None:
            out.append(self._object_root)
        return out

    def _apply_label_mode(self, force: bool = False) -> None:
        mode = self.label_mode()
        if mode == self._label_mode and not force:
            return
        self._label_mode = mode
        for root in self._roots():
            with contextlib.suppress(RuntimeError):
                for form in root.findChildren(QtWidgets.QFormLayout):
                    self.style_form(form, mode)

    def set_label_layout(self, value: str) -> None:
        """Settings > the label layout ('auto' / 'beside' / 'above'), from a section menu."""
        set_setting(LABEL_LAYOUT_KEY, value if value in LABEL_LAYOUTS else "auto")
        self._apply_label_mode(force=True)

    def _fit_width(self) -> None:
        if not self.isVisible():
            return  # (the viewport's width is not known yet)
        w = self.viewport().width()
        for root in self._roots():
            with contextlib.suppress(RuntimeError):
                root.setMaximumWidth(max(1, w))

    def resizeEvent(self, e) -> None:  # noqa: N802 -- Qt API
        super().resizeEvent(e)
        self._place_search()
        self._fit_width()
        self._apply_label_mode()

    def showEvent(self, e) -> None:  # noqa: N802 -- Qt API
        super().showEvent(e)
        self._place_search()
        self._fit_width()
        self._apply_label_mode()

    def _moved_in_place(self, key) -> bool:
        """Only moves since the form was built (a nudge, a drag, their undo / redo): the
        position fields are set in place. False when the form must be built again."""
        old = self._built_key
        if (
            key is None
            or old is None
            or len(key) != 11
            or len(old) != 11
            or key[:3] != old[:3]
            or key[8:] != old[8:]
        ):
            return False
        if not self._geom and self._multi_geom is None:
            return False
        try:
            if not self._doc.pending_moves(old[3]):
                return False
        except Exception:  # noqa: BLE001  # swallow-ok: not known, the form is built again
            return False
        doc = self._doc
        self._building = True
        try:
            if self._geom:
                if len(self._indices) != 1:
                    return False
                g = doc.page.objects[self._indices[0]].geometry
                shown = self._shown_geometry(g.x, g.y, g.extent_x, g.extent_y)
                for k, v in zip(("x", "y", "w", "h"), shown):
                    sp = self._geom.get(k)
                    if sp is not None and sp.value() != int(v):
                        sp.load(int(v))
                return True
            roots, sx, sy, size = self._multi_geom
            box = doc.selection_extent(roots)
            if box is None:
                return False
            left, top, right, bottom = box
            shown = self._shown_geometry(left, top, right - left, bottom - top)
            for sp, v in zip((sx, sy) + tuple(size), shown):
                sp.setProperty("base", int(v))
                if sp.value() != int(v):
                    sp.load(int(v))
            return True
        except RuntimeError, IndexError, AttributeError:
            return False  # (a field gone with its form: build it again)
        finally:
            self._building = False

    def refresh(self) -> None:
        """Rebuild from current document state (same target)."""
        self.drop_kept_forms()
        self._built_key = None  # (the form shown is not kept either)
        self._rebuild()

    def _refresh_if_stale(self) -> None:
        """Rebuild unless the form shown is of the document as it is now. After an edit's
        ``changed`` the window has rebuilt the form already (a second rebuild would throw
        away the header flash of Add animation); a panel without a window has not."""
        key = self._build_key()
        if key is None or key != self._built_key:
            self._rebuild()

    def _build_key(self):
        """What the panel's content depends on: the document and its change counters (every
        edit, undo, redo and derive moves one), the selection, and the read-only verdicts the
        window gives (locks, Break Lock Mode). None = always rebuild."""
        doc = self._doc
        if doc is None:
            return ("none",)
        try:
            ro = tuple(self.read_only_reason(i) if self.read_only_reason else "" for i in self._indices[:1])
            return (
                id(doc),
                tuple(self._indices),
                getattr(doc, "derive_serial", 0),
                getattr(doc, "touch_serial", 0),
                getattr(doc, "edit_serial", 0),
                len(getattr(doc, "_undo", ()) or ()),
                len(getattr(doc, "_redo", ()) or ()),
                bool(getattr(doc, "dirty", False)),
                ro,
                frozenset(self._show_touch),
                # (with nothing selected an armed drawing tool shows its New <Kind>)
                "" if self._indices else self._tool_kind_key(),
            )
        except Exception:  # noqa: BLE001 -- unknown state: rebuild
            _note_exc("properties._build_key")
            return None

    # -- rebuild ------------------------------------------------------------------------
    @staticmethod
    def _focusables(root: QtWidgets.QWidget) -> list:
        return [
            w
            for w in root.findChildren(QtWidgets.QWidget)
            if w.focusPolicy() & QtCore.Qt.TabFocus
            and w.isEnabled()
            and not isinstance(w.parentWidget(), QtWidgets.QAbstractSpinBox)
        ]

    def _focus_key(self):
        """Which field has focus (its position among the panel's focusable
        widgets) -- a commit rebuilds the panel, and this keeps the focus
        (and Tab to the next field) after an edit."""
        old = self.widget()
        fw = QtWidgets.QApplication.focusWidget()
        if old is None or fw is None or not old.isAncestorOf(fw):
            return None
        fs = self._focusables(old)
        w = fw
        while w is not None and w not in fs:
            w = w.parentWidget()
        if w is None:
            return None
        return fs.index(w), len(fs), type(w).__name__

    def _restore_focus(self, key, scroll: int) -> None:
        self.verticalScrollBar().setValue(scroll)
        root = self.widget()
        if key is None or root is None:
            return
        fs = self._focusables(root)
        k, n, cls = key
        if len(fs) == n and type(fs[k]).__name__ == cls:
            fs[k].setFocus(QtCore.Qt.OtherFocusReason)

    @staticmethod
    def _new_root() -> QtWidgets.QWidget:
        root = QtWidgets.QWidget()
        root.setObjectName("propRoot")
        # the form follows the dock's width: its children's minimum widths decide, never
        # the size hint of a long value (fields cut at the panel's right edge)
        root.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        lay = QtWidgets.QVBoxLayout(root)
        lay.setContentsMargins(_sp(1), _sp(1), _sp(1), _sp(1))
        lay.setSpacing(_sp(1))
        return root

    def _finish_root(self, root: QtWidgets.QWidget) -> None:
        """What every built form gets: the fields shrink with the dock (never below
        FIELD_MIN_WIDTH), the panel's keys in each field, the sections folded as kept."""
        self._finish_fields(root)
        for sec in root.findChildren(PanelSection):
            sec.sync_fold()
        if self.isVisible():
            with contextlib.suppress(RuntimeError):
                root.setMaximumWidth(max(1, self.viewport().width()))

    def _finish_section(self, sec: "PanelSection") -> None:
        """A lazy section's rows, added on its first unfold: what :meth:`_finish_root`
        gives the others, and read-only like the rest of a read-only object."""
        self._finish_fields(sec)
        if self._ro_reason:
            self._make_read_only(sec, self._ro_reason)

    def _finish_fields(self, root: QtWidgets.QWidget) -> None:
        # (the field classes asked for by name, none a subclass of another: not a walk of
        # every widget of the form, each handed to Python)
        for cls in FIELD_CLASSES:
            for w in root.findChildren(cls):
                if panel_is_field(w):
                    self._finish_field(w)

    def _finish_field(self, w: QtWidgets.QWidget) -> None:
        pol = w.sizePolicy()
        if pol.horizontalPolicy() != QtWidgets.QSizePolicy.Fixed:
            pol.setHorizontalPolicy(QtWidgets.QSizePolicy.Ignored)
            w.setSizePolicy(pol)
            if not w.minimumWidth():  # (a field may ask for less: X / Y / W / H)
                w.setMinimumWidth(FIELD_MIN_WIDTH)
        w.installEventFilter(self._keys)

    #: how many object forms are kept for a selection that comes back
    KEPT_FORMS = 4

    def _drop_root(self, root) -> None:
        """Take ``root`` out of the panel and delete it later (a rebuild can run in a handler
        of one of its own widgets: deleting the sender mid-slot crashes). Without a parent:
        a widget left to deleteLater under a parent that PySide then deletes crashes the
        next flush of the deferred deletes. Not left for a later turn: where the event loop
        seldom runs, the dropped forms pile up."""
        if root is None:
            return
        with contextlib.suppress(RuntimeError):
            if self._stack.currentWidget() is root:
                self._stack.forget_current()
            root.hide()
            root.setParent(None)
            root.deleteLater()

    def flush_pending_steps(self) -> bool:
        """Commit the arrow steps of the form shown that still wait for the pause, to the
        document and selection the form was built for: a rebuild drops the field, its timer
        with it, and its focus-out commit would come while the rebuild guard is up. True when
        one was committed."""
        from ..number_field import pending_fields

        if self._building:
            return False
        root = self.widget()
        if root is None:
            return False
        done = False
        for f in pending_fields():
            with contextlib.suppress(RuntimeError):
                if root.isAncestorOf(f):
                    f.commit()
                    done = True
        return done

    def _rebuild(self) -> None:
        if not self._building and self.flush_pending_steps():
            # (the commit's refresh may have built the form already)
            key = self._build_key()
            if key is not None and key == self._built_key:
                return
        prev_key = self._built_key
        prev_state = (self._geom, self._multi_geom, self._ro_reason)
        self._built_key = self._build_key()
        self._building = True
        focus = self._focus_key()
        scroll = self.verticalScrollBar().value()
        self._label_mode = self.label_mode()
        try:
            self._geom = {}
            self._multi_geom = None
            self._prune_page_forms()
            doc = self._doc
            count = (
                min(len(doc.page.objects), len(doc.objects))
                if doc is not None and doc.page is not None
                else 0
            )
            idx = sorted({i for i in self._indices if 0 <= i < count})
            if doc is not None and doc.page is not None:
                self._ro_reason = ""
            if doc is not None and doc.page is not None and not idx and self._tool_kind_key():
                # an armed drawing tool: the look of the next object it draws
                root = self._new_root()
                self._stack.addWidget(root)
                try:
                    self._build_tool_root(root, doc)
                except BaseException:
                    self._drop_root(root)
                    raise
            elif doc is not None and doc.page is not None and not idx:
                root = self._page_root()
            else:
                again = self._kept_form(self._built_key) if doc is not None and idx else None
                if again is not None:
                    # this selection's form, shown before with the document as it is now
                    root, (self._geom, self._multi_geom, self._ro_reason) = again
                    self._restyle(root)
                else:
                    # (in the panel, hidden, before its rows: a form built on its own and
                    # then moved in has every widget styled once more)
                    root = self._new_root()
                    self._stack.addWidget(root)
                    try:
                        self._build_root(root, doc, idx)
                    except BaseException:
                        self._drop_root(root)
                        raise
                    if doc is not None and doc.page is not None and idx:
                        root.setProperty("ctgObjectForm", True)
                        root.layout().addStretch(1)
            kept = any(ent[2] is root for ent in self._page_forms.values())
            if not kept and not root.property("ctgObjectForm"):
                root.layout().addStretch(1)
            self._stack.setCurrentWidget(root)
            # (laid out first: then each field's width is known)
            QtCore.QTimer.singleShot(0, root, lambda r=root: show_value_starts(r))
            # the object form shown before goes (a kept page form stays in the stack)
            old, self._object_root = self._object_root, (None if kept else root)
            if old is not None and old is not root:
                self._keep_or_drop(old, prev_key, prev_state)
        finally:
            self._building = False
        shown = self.widget()
        if self.search.text() or (shown is not None and shown.property("ctgSearched")):
            self._apply_search()
        pending, self._pending_focus = self._pending_focus, None
        if pending is not None:
            QtCore.QTimer.singleShot(0, self, lambda p=pending: self._reveal(*p))
        elif focus is not None or scroll:
            QtCore.QTimer.singleShot(0, self, lambda: self._restore_focus(focus, scroll))

    def _build_root(self, root: QtWidgets.QWidget, doc, idx: list) -> None:
        """The object form (or the 'No page open' note) in ``root``. Its sections are made
        in the widget they go in (:attr:`_form_parent`): one made on its own and then moved
        in has each of its widgets styled once more."""
        lay = root.layout()
        self._form_parent = root
        try:
            if doc is None or doc.page is None:
                lay.addWidget(self._dim_label("No page open", centre=True))
            elif len(idx) == 1:
                reason = self._object_read_only(idx[0])
                if reason:
                    # a placed genie's member / a locked template record: CtDraw
                    # re-composes it from its definition, so its settings are shown, not
                    # edited (help 1172204)
                    self._ro_reason = reason
                    lay.addWidget(self._read_only_banner(idx[0], reason))
                    body = QtWidgets.QWidget(root)
                    body_lay = QtWidgets.QVBoxLayout(body)
                    body_lay.setContentsMargins(0, 0, 0, 0)
                    body_lay.setSpacing(_sp(1))
                    lay.addWidget(body)
                    self._form_parent = body
                    self._build_object(body_lay, idx[0])
                    self._sweep_strays(body)
                    self._make_read_only(body, reason)
                else:
                    self._build_object(lay, idx[0])
            else:
                self._build_multi(lay, idx)
        finally:
            self._form_parent = None
        self._sweep_strays(root)
        self._finish_root(root)

    @staticmethod
    def _sweep_strays(parent: QtWidgets.QWidget) -> None:
        """A section made in ``parent`` but not put in its layout (a group has no
        Appearance) goes: unmanaged, it would show over the form's top left corner."""

        def managed(lay, w) -> bool:
            if lay is None:
                return False
            if lay.indexOf(w) >= 0:
                return True
            for k in range(lay.count()):
                sub = lay.itemAt(k).layout()
                if sub is not None and managed(sub, w):
                    return True
            return False

        for sec in parent.children():
            if isinstance(sec, PanelSection) and not managed(parent.layout(), sec):
                sec.hide()
                sec.setParent(None)
                sec.deleteLater()

    def _reveal(self, title: str, key: str) -> None:
        """A section just added: scroll it into view, focus its field, flash its header."""
        sec = self.section(title)
        if sec is None:
            return
        if sec.folded():
            sec.set_folded(False)
        target = None
        for w in sec.findChildren(QtWidgets.QWidget):
            if w.property("propKey") == key and w.isEnabled():
                target = w
                break
        if target is None:
            fs = [w for w in self._focusables(sec) if w.objectName() not in ("sectionChevron", "sectionMenu")]
            target = fs[0] if fs else None
        self.ensureWidgetVisible(sec, 0, 0)
        if target is not None:
            self.ensureWidgetVisible(target)
            target.setFocus(QtCore.Qt.OtherFocusReason)
        sec.flash()

    # -- read-only objects -------------------------------------------------------------
    def _object_read_only(self, index: int) -> str:
        """Why object ``index`` is shown read-only ('' = editable)."""
        fn = self.read_only_reason
        try:
            if fn is not None:
                return str(fn(index) or "")
            from ..objprops.registry import read_only_reason

            return read_only_reason(self._doc, index)
        except Exception:  # noqa: BLE001 -- a hint only; never block the panel
            _note_exc("properties._object_read_only")
            return ""

    def is_read_only(self) -> bool:
        """The panel shows one object read-only (tests / scripting)."""
        return bool(self._ro_reason)

    def _read_only_banner(self, index: int, reason: str) -> QtWidgets.QWidget:
        box = QtWidgets.QFrame()
        box.setObjectName("roBanner")
        from ..style.theme import DARK

        # (the theme's warning colour: the stylesheet hook maps it to Light / high contrast)
        box.setStyleSheet("QFrame#roBanner { border: 1px solid %s; border-radius: 4px; }" % DARK["warn"])
        v = QtWidgets.QVBoxLayout(box)
        v.setContentsMargins(_sp(2), _sp(1), _sp(2), _sp(1))
        v.setSpacing(_sp(1))
        lab = QtWidgets.QLabel("⚠ " + reason)
        lab.setWordWrap(True)
        lab.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        from ..style.theme import set_note_role

        set_note_role(lab, "warn")
        v.addWidget(lab)
        # the buttons one under the other with short texts: side by side, a long 'Edit
        # Definition (...)' overflows a narrow dock
        row = QtWidgets.QVBoxLayout()
        row.setSpacing(_sp(1))
        label = ""
        try:
            if self.definition_label is not None:
                label = str(self.definition_label(index) or "")
        except Exception:  # noqa: BLE001
            _note_exc("properties._read_only_banner")
            label = ""
        if label:
            b = QtWidgets.QPushButton("Edit Definition")
            b.setToolTip("Open %s and edit it there; the pages that place it take the change" % label)
            b.clicked.connect(lambda _=False, i=index: self.editDefinition.emit(i))
            row.addWidget(b)
        p = QtWidgets.QPushButton("Object Properties…")
        p.setToolTip("Open Object Properties (read-only)")
        p.clicked.connect(lambda _=False, i=index: self.showSheet.emit(i))
        row.addWidget(p)
        v.addLayout(row)
        return box

    @staticmethod
    def _make_read_only(body: QtWidgets.QWidget, reason: str) -> None:
        """Every editor under ``body`` shows its value but takes no edit (the
        text fields stay selectable; the sections still fold)."""
        tip = "Read-only: " + reason
        from ..style.theme import SearchField

        for w in body.findChildren(QtWidgets.QWidget):
            if w.objectName() in ("sectionChevron", "sectionMenu"):
                continue
            if isinstance(w, SearchField) or isinstance(w.parentWidget(), SearchField):
                continue  # (a search box: the properties can be searched)
            if isinstance(w, (QtWidgets.QLineEdit, QtWidgets.QAbstractSpinBox, QtWidgets.QPlainTextEdit)):
                w.setReadOnly(True)
                w.setToolTip(tip)
            elif isinstance(w, (QtWidgets.QComboBox, QtWidgets.QAbstractButton)):
                w.setEnabled(False)
                w.setToolTip(tip)

    def _template_size(self, doc):
        """(width, height) of the page's template, or None."""
        try:
            te = doc.template_element()
            if not te:
                return None
            from ...format import template_place as TP

            t = TP.load_template(te[0], te[1])
            return (int(t.width), int(t.height)) if t.width and t.height else None
        except Exception:  # noqa: BLE001  # swallow-ok: no template known, no note
            return None

    @staticmethod
    def _area_value(cb: QtWidgets.QComboBox) -> str:
        t = cb.currentText().strip()
        return "" if t == panel_ALL_AREAS else t

    @staticmethod
    def _element_params(doc) -> dict:
        """A genie element's declared %parameters% (name -> default), as its
        paste dialog will list them (declarations of the current records)."""
        try:
            from ...format import genie_place as GP

            class _El:
                records = doc._tree()

            return dict(GP.declared_params(_El))
        except Exception:  # noqa: BLE001
            _note_exc("properties._element_params")
            return {}

    # -- single object mode ------------------------------------------------------------------
    def _class_has(self, index: int, page_id: str) -> bool:
        """The object's class has the object sheet page ``page_id`` (True when not known)."""
        try:
            from ..objprops import registry as R

            ctx = R.SheetContext.for_object(self._doc, index)
            return bool(ctx.mask & R.PAGE_BY_ID[page_id].bit)
        except Exception:  # noqa: BLE001  # swallow-ok: not known, offer it
            return True

    def _section(
        self,
        title: str,
        index=None,
        page_id: str = "",
        remove=None,
        remove_text: str = "Remove",
        extra_menu=None,
    ) -> PanelSection:
        """A section of the object form; ``page_id`` its Object Properties page."""
        open_page = None
        if index is not None and page_id:
            open_page = lambda i=index, pid=page_id: self.editPage.emit(i, pid)  # noqa: E731
        return PanelSection(
            title,
            self,
            open_page=open_page,
            remove=remove,
            remove_text=remove_text,
            extra_menu=extra_menu,
            parent=self._form_parent,
        )

    def _link(self, text: str, index: int, page_id: str, slot=None) -> QtWidgets.QToolButton:
        """A flat link button that opens Object Properties on ``page_id`` (or runs
        ``slot``)."""
        b = QtWidgets.QToolButton()
        b.setObjectName("linkButton")
        b.setText(text)
        b.setCursor(QtCore.Qt.PointingHandCursor)
        b.setAutoRaise(True)
        b.setToolTip("Open Object Properties on this page")
        b.setProperty("pageId", page_id)
        if slot is None:
            slot = lambda i=index, pid=page_id: self.editPage.emit(i, pid)  # noqa: E731
        b.clicked.connect(lambda _=False: slot())
        return b

    def _build_object(self, lay: QtWidgets.QVBoxLayout, index: int) -> None:
        doc = self._doc
        obj = doc.page.objects[index]
        try:
            props = dict(doc.object_properties(index))
        except Exception as exc:  # noqa: BLE001  # swallow-ok: the message is shown
            self.statusMessage.emit(str(exc))
            props = {}

        from ..names import kind_name
        from ..style.theme import COLORS

        identity = not self._ro_reason and self._class_has(index, "access")
        head = QtWidgets.QLabel(
            "<b>%s%s</b>%s"
            % (
                kind_name(obj),
                (" · " + obj.cls) if obj.cls else "",
                ""
                if identity
                else "&nbsp;&nbsp;<span style='color:%s'>AN %d</span>"
                % (COLORS.get("text_dim", "#969ca5"), obj.an),
            )
        )
        head.setTextFormat(QtCore.Qt.RichText)
        head.setToolTip("Position %d in the page's stacking order" % index)
        head.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        lay.addWidget(head)
        if identity:
            lay.addLayout(self._identity_rows(index, obj, props))

        # Group / genie / symbol-set container summary (subtree is one unit);
        # touch / keyboard records are the object's commands, not members
        members = [m for m in obj.members if doc.page.objects[m].raw.get("_type_code") not in panel_INPUT_TCS]
        sset = obj.animations.symbol_set if obj.kind is ObjectKind.SYMBOL_SET else None
        if members:
            cbox = self._section("Genie" if obj.is_genie else ("Symbol set" if sset is not None else "Group"))
            cform = cbox.form()
            if sset is not None:
                # a symbol set's children are its states (double-click edits them)
                from ...format import symbolset as _S

                info = doc.symbol_set_info(index) if hasattr(doc, "symbol_set_info") else None
                cform.addRow("Type", self._ro_label(_S.TYPE_LABELS.get(sset.type, str(sset.type))))
                cform.addRow("States", self._ro_label("%d" % (len(info["states"]) if info else len(members))))
            else:
                cform.addRow("Members", self._ro_label("%d objects" % len(members)))
            ref = obj.genie_template or obj.raw.get("_ref") or obj.raw.get("_resolved_ref")
            if ref and sset is None:
                import os
                from ..names import library_link_label

                pdir = os.path.dirname(os.path.abspath(doc.path)) if getattr(doc, "path", "") else ""
                kind, name = library_link_label(ref, pdir)
                # the reference wraps at its dots ('switches.' / 'KnobSwitch'), whole in the
                # tool tip
                lab = QtWidgets.QLabel(str(name).replace(".", ".\u200b"))
                lab.setObjectName("dim")
                lab.setWordWrap(True)
                lab.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
                lab.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
                lab.setToolTip(str(ref) if kind == "Composite genie" else str(name))
                cform.addRow(kind, lab)
            if obj.is_genie:
                bh = QtWidgets.QHBoxLayout()  # (a layout, not a widget)
                bh.setContentsMargins(0, 0, 0, 0)
                gbtn = QtWidgets.QPushButton("Genie Parameters…")
                gbtn.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
                gbtn.clicked.connect(lambda _=False, i=index: self.editGenie.emit(i))
                bh.addWidget(gbtn)
                bh.addStretch(1)
                cform.addRow(bh)
                # (its form completes equipment names: they are read while it is selected)
                if getattr(doc, "path", ""):
                    import os
                    from .sa_pickers import warm_equipment

                    warm_equipment(os.path.dirname(os.path.abspath(doc.path)))
            lay.addWidget(cbox)
            if obj.is_genie:
                from .properties_inputs import inputs_section

                ibox = inputs_section(self, index)
                if ibox is not None:
                    lay.addWidget(ibox)

        # an object in a placed genie: the fields the genie's inputs set
        if obj.parent is not None:
            from .properties_inputs import sources_section

            sbox = sources_section(self, index)
            if sbox is not None:
                lay.addWidget(sbox)

        # Geometry -> set_object_rect on a commit
        lay.addWidget(self._geometry_section(index, obj))

        # Appearance: colour swatch + filled + line width/style, via the same
        # byte-pinned setters as the ribbon SHAPE tab (kept in sync)
        st = obj.style
        is_shape = obj.kind in (
            ObjectKind.LINE,
            ObjectKind.PIPE,
            ObjectKind.POLYGON,
            ObjectKind.RECTANGLE,
            ObjectKind.ELLIPSE,
        )
        self._build_appearance_sections(is_shape, obj, st, lay, index)
        self._build_text_sections(obj, props, st, lay, index)

    def _build_appearance_sections(self, is_shape, obj, st, lay, index):
        is_closed = obj.kind in (ObjectKind.RECTANGLE, ObjectKind.ELLIPSE, ObjectKind.POLYGON)
        # a group / genie / symbol group has no appearance of its own: CtDraw's Group class
        # has no Appearance > General page (objprops.registry); its section is not shown
        group = obj.raw.get("_type_code") == 0x08
        made_in, self._form_parent = self._form_parent, (None if group else self._form_parent)
        try:
            abox = self._section("Appearance", index, "general")
        finally:
            self._form_parent = made_in
        aform = abox.form()
        if not group and not self._ro_reason:
            # the named styles head the section (a click applies one, pointing previews it)
            strip = self._styles_strip()
            if strip is not None:
                strip.setProperty("propKey", "styles")
                aform.addRow(strip)
        is_caption = obj.kind in (ObjectKind.TEXT, ObjectKind.NUMBER, ObjectKind.BUTTON)
        if is_caption:
            colour = st.text_colour
        elif is_closed:
            colour = self._fill_slot_colour(index)
        else:
            colour = st.fill_colour if st.filled else st.line_colour
        cbtn = self._swatch_button(colour)
        stored = self._slot_pair(index, "fill" if is_closed else None)
        if stored is not None and (stored[0] != stored[1] or (stored[0] >> 24) == 0xFF):
            self._set_swatch(cbtn, stored[0], stored[1])  # flashing / Transparent
        cbtn.clicked.connect(lambda _=False, i=index, b=cbtn: self._on_object_colour(i, b))
        # a closed shape's line and fill colours are separate slots (help 1172124);
        # a text's / button's colour is its caption ('Foreground')
        aform.addRow("Foreground" if is_caption else ("Fill colour" if is_closed else "Colour"), cbtn)
        if is_closed:
            lbtn = self._swatch_button(st.line_colour)
            stored = self._slot_pair(index, "line")
            if stored is not None and (stored[0] != stored[1] or (stored[0] >> 24) == 0xFF):
                self._set_swatch(lbtn, stored[0], stored[1])
            lbtn.clicked.connect(lambda _=False, i=index, b=lbtn: self._on_line_colour(i, b))
            aform.addRow("Line colour", lbtn)
        if is_closed:
            filled = QtWidgets.QCheckBox()
            filled.setChecked(bool(st.filled))
            filled.toggled.connect(lambda v, i=index: self._on_filled(i, v))
            aform.addRow("Filled", filled)
        if is_shape:
            wsp = self._number(1, 32, " px", max(1, st.line_width or 1))
            wsp.setProperty("propKey", "line_width")
            wsp.valueCommitted.connect(lambda v, i=index: self._on_line_width(i, v))
            aform.addRow(self._scrub("Line width", wsp), wsp)
            scb = QtWidgets.QComboBox()
            scb.addItems(LINE_STYLES)
            scb.setCurrentIndex(st.line_style if 0 <= st.line_style < 5 else 0)
            scb.activated.connect(lambda v, i=index: self._on_line_style(i, v))
            scb.setProperty("propKey", "line_style")
            aform.addRow("Line style", scb)
        if obj.kind is ObjectKind.RECTANGLE and self._has_corner_radius(index):
            rad = self._number(0, 32, " px", int(st.corner_radius or 0))
            rad.setProperty("propKey", "corner_radius")
            rad.setToolTip("0 = square corners. Rounded corners have no border or extra line")
            rad.valueCommitted.connect(lambda v, i=index: self._on_corner_radius(i, v))
            aform.addRow(self._scrub("Corner radius", rad), rad)
        if is_shape and self._class_has(index, "effects"):
            self._add_effect_rows(aform, index, st)
        if not group:
            lay.addWidget(abox)

    def _build_text_sections(self, obj, props, st, lay, index):
        # Text: caption + editable font / size / style (matches the ribbon TEXT tab)
        is_text = obj.kind in (ObjectKind.TEXT, ObjectKind.NUMBER)
        if is_text or obj.style.text:
            tbox = self._section("Text", index, "general")
            tform = tbox.form()
            # several lines: Enter starts a new one (CtDraw's text entry)
            from ..editor.caption_edit import CaptionEdit

            ted = CaptionEdit(str(obj.style.text or ""))
            ted.setProperty("orig", str(obj.style.text or ""))
            ted.setProperty("propKey", "caption")
            ted.editingFinished.connect(lambda e=ted, i=index: self._on_text(i, e))
            tform.addRow("Caption", ted)
            if is_text:
                fc = QtWidgets.QFontComboBox()
                # (Enter on a name no font has must not add it to the list)
                fc.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
                if st.font:
                    fc.setCurrentFont(QtGui.QFont(st.font))
                    fc.setEditText(st.font)
                fc.setProperty("orig", fc.currentText())
                fc.setProperty("propKey", "font")
                # a typed name applies once, when it is done (Enter / leaving the field) or
                # chosen from the list: never at each key
                fc.activated.connect(lambda pos, i=index, c=fc: self._on_font_typed(i, c, c.itemText(pos)))
                fc.lineEdit().editingFinished.connect(
                    lambda i=index, c=fc: self._on_font_typed(i, c, c.currentText())
                )
                self._watch_font_typing(fc)
                tform.addRow("Font", fc)
                szc = self._number(4, 96, " pt", st.font_size or 10)
                szc.setProperty("propKey", "font_size")
                szc.valueCommitted.connect(lambda v, i=index: self._on_font_size(i, v))
                tform.addRow(self._scrub("Size", szc), szc)
                justify = getattr(st.justify, "value", st.justify) or "left"
                style_row = self._style_row(
                    {f: bool(getattr(st, f)) for f in ("bold", "italic", "underline", "strikeout")},
                    justify,
                    lambda fl, v, i=index: self._on_font_flag(i, fl, v),
                    lambda j, i=index: self._on_justify(i, j),
                )
                tform.addRow("Style", style_row)
                if self._class_has(index, "effects"):
                    eff = QtWidgets.QComboBox()
                    for label, value in TEXT_EFFECTS:
                        eff.addItem(label, value)
                    cur = (st.style_flags[0] & 0x0F) if st.style_flags else 0
                    eff.setCurrentIndex(max(0, eff.findData(cur)))
                    eff.setProperty("propKey", "text_effect")
                    eff.setToolTip(
                        "A 3D look for the text (Appearance > 3D Effects has its colours and depth)"
                    )
                    eff.activated.connect(lambda _p, i=index, c=eff: self._on_text_effect(i, c.currentData()))
                    tform.addRow("Effect", eff)
            else:
                tform.addRow("Font", self._ro_label(st.font or ""))
            lay.addWidget(tbox)

        # Animation families (driven by which raw keys are present)
        self._add_family(
            lay, index, props, "Visibility", FAMILY_VISIBILITY, page_id="visibility", remove="visibility"
        )
        self._add_fill_colour(lay, index, props)
        self._add_family(
            lay,
            index,
            props,
            "Fill level",
            FAMILY_FILL_LEVEL,
            extra=lambda form, i=index, p=props: self._add_fill_level_rows(form, i, p),
            keys=("FILMODE", "FILCOL"),
            page_id="fill_level",
            remove="fill_level",
        )
        self._add_family(
            lay,
            index,
            props,
            "Movement / Scaling",
            FAMILY_MOVE + FAMILY_ROTATE + FAMILY_SCALE,
            extra=lambda form, i=index, p=props: self._add_axis_rows(form, i, p),
            # a centre that is set (a rotational slider owns its axis with no
            # ROT expression) keeps the group
            keys=("ROTMODE", "ROTXORG", "ROTYORG", "SZXMODE", "SZXORG", "SZYMODE", "SZYORG"),
            page_id=self._motion_page(props),
            remove="motion",
            link=True,
        )
        self._add_input(lay, index, props)
        self._add_family(
            lay,
            index,
            props,
            "Disable",
            FAMILY_DISABLE,
            extra=lambda form, i=index, p=props: self._add_disable_style(form, i, p),
            keys=("GREY_STYLE",),
            page_id="disable",
            remove="disable",
        )
        self._add_display_value(lay, index, props)

        # Add animation… (its entries are worked out and listed when it is first used, not
        # with every selection)
        combo = AddAnimationCombo(lambda i=index, p=props: self._addable(i, p))
        combo.setToolTip(
            "Add an animation to this object; its section opens with the focus in its first field"
        )
        combo.activated.connect(lambda pos, c=combo, i=index: self._on_add_animation(i, c, pos))
        lay.addWidget(combo)

        # All properties: every raw key/value pair (escape hatch)
        if props:
            lay.addWidget(self._all_properties(index, props))

    # -- identity: name, AN, tool tip --------------------------------------------------------
    def _identity_rows(self, index: int, obj, props: dict) -> QtWidgets.QFormLayout:
        """Name (Access > General: set_object_name; its problem shown as you type, Enter
        does nothing while there is one), AN (editable with Settings > Modify AN:
        set_object_an) and Tool tip (COMMENT, CtDraw's '<User>' marker kept). A layout,
        not a widget of its own; a field's problem note comes with its first problem."""
        from ..objprops.access import GENERAL

        limits = {f.key: f.limit for f in GENERAL.fields}
        doc = self._doc
        form = QtWidgets.QFormLayout()
        form.setObjectName("identityRows")
        form.setContentsMargins(0, _sp(0), _sp(1), _sp(1))
        self.style_form(form)
        drops = getattr(doc, "_genie_root_drops", None)

        def keeps(key):
            try:
                return drops is None or not drops(index, key)
            except Exception:  # noqa: BLE001  # swallow-ok: not known, show it
                return True

        name = ""
        try:
            name = doc.object_name(index)
        except Exception:  # noqa: BLE001  # swallow-ok: a record with no name header
            name = ""
        ned = self._line(name)
        ned.setMaxLength(max(int(limits.get("__AN_NAME__") or 32), len(name)))
        ned.setPlaceholderText("No name")
        ned.setToolTip("The object's name (Access > General); Cicode finds the object by it")
        ned.setProperty("propKey", "__AN_NAME__")
        self._note_later(ned, form)
        ned.textEdited.connect(lambda _t, e=ned, i=index: self._check_name(i, e))
        ned.editingFinished.connect(lambda e=ned, i=index: self._on_name(i, e))
        form.addRow("Name", ned)
        # AN
        an_why = ""
        try:
            an_why = doc.an_change_problem(index)
        except Exception as exc:  # noqa: BLE001  # swallow-ok: reported
            an_why = str(exc)
        if panel_option("modify_an") and not an_why:
            # (clamp off: an AN outside the range is refused, never brought into it)
            aed = NumberField(lo=doc.AN_MIN, hi=doc.AN_MAX, value=int(obj.an), clamp=False)
            aed.setToolTip("The object's AN. Its touch and keyboard commands follow it")
            self._note_later(aed, form)
            aed.lineEdit().textEdited.connect(lambda _t, e=aed, i=index: self._check_an(i, e))
            aed.valueCommitted.connect(lambda v, e=aed, i=index: self._on_an(i, e, v))
            aed.setProperty("propKey", "an")
            form.addRow("AN", aed)
        else:
            lab = self._ro_label(str(obj.an) if obj.an >= 0 else "")
            lab.setToolTip(
                an_why if an_why else "Settings > Editing > Allow changing an object's AN makes this editable"
            )
            form.addRow("AN", lab)
        if keeps("COMMENT"):
            tip = self._multi_shown("COMMENT", props.get("COMMENT"))
            ted = self._line(tip)
            ted.setMaxLength(max(int(limits.get("COMMENT") or 48), len(tip)))
            ted.setToolTip("Shown when the operator points at the object at runtime")
            ted.setProperty("propKey", "COMMENT")
            ted.editingFinished.connect(lambda e=ted, i=index: self._on_tooltip(i, e))
            form.addRow("Tool tip", ted)
        return form

    @staticmethod
    def _note_later(ed, form: QtWidgets.QFormLayout) -> None:
        """``ed``'s problem note (a danger line under it in ``form``) is made when it first
        has a problem to show (:meth:`_show_problem`)."""
        ed.problem_note = None
        ed.note_form = form

    @staticmethod
    def _problem_note() -> QtWidgets.QLabel:
        from ..style import theme

        lab = theme.note_label("", "danger")
        lab.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        lab.hide()
        return lab

    @classmethod
    def _show_problem(cls, ed, message: str) -> None:
        from ..style import theme

        note = getattr(ed, "problem_note", None)
        theme.mark_invalid(ed, message)
        form = getattr(ed, "note_form", None)
        if note is None and message and form is not None:
            with contextlib.suppress(RuntimeError):
                row, _role = form.getWidgetPosition(ed)
                if row >= 0:
                    note = cls._problem_note()
                    form.insertRow(row + 1, None, note)
                    ed.problem_note = note
        if note is not None:
            with contextlib.suppress(RuntimeError):
                note.setText(message)
                note.setVisible(bool(message))

    def _check_name(self, index: int, ed) -> str:
        if self._doc is None:
            return ""
        text = ed.text()
        try:
            msg = self._doc.object_name_problem(index, text) if text != ed.property("orig") else ""
        except Exception as exc:  # noqa: BLE001  # swallow-ok: reported
            msg = str(exc)
        if not msg:
            from ...editing import UnencodableTextError, guard_text

            try:
                guard_text(text)
            except UnencodableTextError as exc:
                msg = "%s." % str(exc).rstrip(".")
        self._show_problem(ed, msg)
        return msg

    def _on_name(self, index: int, ed) -> None:
        if self._building or self._doc is None or ed.isReadOnly():
            return
        if ed.text() == ed.property("orig"):
            self._show_problem(ed, "")
            return
        if self._check_name(index, ed):
            return  # (Enter does nothing while the name fails)
        try:
            ok = self._doc.set_object_name(index, ed.text())
        except Exception as exc:  # noqa: BLE001  # swallow-ok: reported
            self._show_problem(ed, "%s" % exc)
            return
        if ok:
            ed.setProperty("orig", ed.text())
            self.changed.emit()
        else:
            self.statusMessage.emit(self._not_stored(index, "__AN_NAME__"))

    def _check_an(self, index: int, ed) -> str:
        text = ed.lineEdit().text().strip()
        v = evaluate(text, ed.value())
        try:
            msg = self._doc.an_change_problem(index, str(int(v)) if v is not None else text)
        except Exception as exc:  # noqa: BLE001  # swallow-ok: reported
            msg = str(exc)
        self._show_problem(ed, msg)
        return msg

    def _stored_an(self, index: int) -> int | None:
        """Object ``index``'s AN as the page stores it (its handle + 1), or None."""
        from ...format import geometry as geom

        try:
            h = geom.get_handle(self._doc.parsed.page.objects[index])
        except Exception:  # noqa: BLE001  # swallow-ok: no such record, nothing to reload
            return None
        return None if h is None else h + 1

    def _on_an(self, index: int, ed, value: int) -> None:
        if self._building or self._doc is None:
            return
        # (a typed AN outside the range never gets here: the field does not clamp it, it
        # keeps the old value and _check_an's note says why)
        try:
            msg = self._doc.an_change_problem(index, str(value))
        except Exception as exc:  # noqa: BLE001  # swallow-ok: reported
            msg = str(exc)
        ok = False
        if not msg:
            try:
                ok = self._doc.set_object_an(index, str(value))
            except Exception as exc:  # noqa: BLE001  # swallow-ok: reported
                msg = str(exc)
        if msg:
            # refused: the field shows the stored AN again (Esc has it to go back to) and
            # the note keeps saying why
            stored = self._stored_an(index)
            if stored is not None:
                with contextlib.suppress(RuntimeError):
                    ed.load(stored)
            self._show_problem(ed, msg)
            return
        self._show_problem(ed, "")
        if ok:
            self.changed.emit()

    def _on_tooltip(self, index: int, ed) -> None:
        """Tool tip (COMMENT): empty removes it; CtDraw's '<User>' marker on a stored one
        stays (the sheet's rule)."""
        if self._building or self._doc is None or ed.isReadOnly():
            return
        text = ed.text()
        if text == ed.property("orig"):
            return
        from ...editing import UnencodableTextError, guard_text

        try:
            guard_text(text)
        except UnencodableTextError as exc:
            self._show_problem(ed, "%s." % str(exc).rstrip("."))
            return
        doc = self._doc
        stored = doc.object_properties(index).get("COMMENT")
        try:
            if text:
                value = text
                if (stored or "").endswith(self._USER_MARK):
                    value = text + self._USER_MARK
                ok = doc.ensure_property(index, "COMMENT", value)
            elif stored is not None:
                ok = doc.remove_property(index, "COMMENT")
            else:
                ok = False
        except Exception as exc:  # noqa: BLE001  # swallow-ok: reported
            self.statusMessage.emit(str(exc))
            return
        if ok:
            ed.setProperty("orig", text)
            self._show_problem(ed, "")
            self.changed.emit()
        else:
            self.statusMessage.emit(self._not_stored(index, "COMMENT"))

    # -- geometry -------------------------------------------------------------------------------
    def _grid_pitch(self, axis: int) -> int:
        view = getattr(self.window(), "view", None)
        try:
            return int(view.grid()[axis])
        except Exception:  # noqa: BLE001  # swallow-ok: no canvas (a test): Ctrl steps 8
            return 8

    def _shown_geometry(self, x, y, w, h, ref: str = ""):
        """(X, Y, W, H) as the fields show them: X / Y = the reference point."""
        name, _label, fx, fy = REF_BY_NAME.get(ref or self._geom_ref or geometry_reference(), REFERENCES[0])
        return (int(x) + (int(w) * fx) // 2, int(y) + (int(h) * fy) // 2, int(w), int(h))

    def _box_from_fields(self, X, Y, W, H, ref: str = ""):
        """The box (x, y, w, h) the fields X / Y / W / H mean."""
        _name, _label, fx, fy = REF_BY_NAME.get(ref or self._geom_ref or geometry_reference(), REFERENCES[0])
        return (int(X) - (int(W) * fx) // 2, int(Y) - (int(H) * fy) // 2, int(W), int(H))

    def _geometry_rows(self, form, fields: dict, *, lo_w: int = 0, tips=None, field_cls=None):
        """The compact geometry rows."""
        ref = geometry_reference()
        self._geom_ref = ref
        grid = RefGrid(ref)
        grid.changed.connect(self._on_reference)
        th = QtWidgets.QHBoxLayout()
        th.setContentsMargins(0, 0, 0, 0)
        th.setSpacing(_sp(1))
        th.addWidget(grid, 0, QtCore.Qt.AlignVCenter)
        cap = self._dim_label(REF_BY_NAME[ref][1])
        cap.setToolTip("X and Y show this point of the box")
        cap.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        th.addWidget(cap, 1)
        form.addRow(th)
        scrub = panel_option("props_scrub")
        tips = tips or {}
        rows = [th]
        for pair in (("x", "y"), ("w", "h")):
            h = QtWidgets.QHBoxLayout()
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(_sp(1))
            for k in pair:
                lo = lo_w if k in ("w", "h") else -32000
                sp = (field_cls or NumberField)(
                    lo=lo,
                    hi=32000,
                    suffix=" px",
                    grid_step=(lambda a=0 if k in ("x", "w") else 1: self._grid_pitch(a)),
                    live_steps=panel_option("props_arrow_live"),
                )
                sp.setProperty("propKey", k)
                # (two to a row: a little narrower than other fields before they shrink)
                sp.setMinimumWidth(GEOMETRY_MIN_WIDTH)
                if k in tips:
                    sp.setToolTip(tips[k])
                lab = ScrubLabel(k.upper(), sp, enabled=scrub)
                lab.setMinimumWidth(lab.fontMetrics().horizontalAdvance("W") + 2)
                h.addWidget(lab)
                h.addWidget(sp, 1)
                fields[k] = sp
                if k == "w":
                    chain = QtWidgets.QToolButton()
                    chain.setObjectName("chain")
                    chain.setCheckable(True)
                    chain.setChecked(lock_aspect())
                    chain.setIcon(chain_icon())
                    chain.setAutoRaise(True)
                    chain.setToolTip("Keep the proportions: a new width or height changes the other in step")
                    chain.setAccessibleName("Keep the proportions")
                    chain.toggled.connect(lambda on: set_setting(LOCK_ASPECT_KEY, bool(on)))
                    h.addWidget(chain)
                    fields["chain"] = chain
            rows.append(h)
            form.addRow(h)
        return rows

    def _geometry_section(self, index: int, obj) -> PanelSection:
        g = obj.geometry
        gbox = self._section("Geometry")
        gform = gbox.form()
        tips = {
            "x": "The horizontal position of the reference point",
            "y": "The vertical position of the reference point",
            "w": "The width",
            "h": "The height",
        }
        self._geometry_rows(gform, self._geom, tips=tips)
        for k, v in zip(("x", "y", "w", "h"), self._shown_geometry(g.x, g.y, g.extent_x, g.extent_y)):
            self._geom[k].load(int(v))
            self._geom[k].valueCommitted.connect(lambda _v, i=index, key=k: self._on_geometry(i, key))
        if obj.kind is ObjectKind.LINE:
            length, angle = self._line_measure(obj)
            gform.addRow("Length", self._ro_label("%d px" % round(length)))
            gform.addRow("Angle", self._ro_label("%g°" % round(angle, 1)))
        return gbox

    @staticmethod
    def _line_measure(obj):
        """(length, angle in degrees anticlockwise from the x axis) of a line."""
        g = obj.geometry
        nodes = list(getattr(g, "nodes", None) or ())
        if len(nodes) >= 2:
            (x0, y0), (x1, y1) = nodes[0], nodes[-1]
            dx, dy = x1 - x0, y1 - y0
        else:
            dx, dy = g.extent_x, g.extent_y
        length = math.hypot(dx, dy)
        angle = math.degrees(math.atan2(-dy, dx)) if length else 0.0
        if angle < 0:
            angle += 360.0
        return length, (0.0 if abs(angle - 360.0) < 1e-9 else angle)

    def _on_reference(self, name: str) -> None:
        set_setting(REFERENCE_KEY, name if name in REF_BY_NAME else "top_left")
        QtCore.QTimer.singleShot(0, self, self.refresh)

    def _aspect(self, which: str, W: int, H: int, w0: int, h0: int):
        """W / H with the proportions kept (the aspect lock) after ``which`` changed."""
        chain = self._geom.get("chain") if self._geom else None
        if chain is None or not chain.isChecked():
            return W, H
        if which == "w" and w0:
            H = max(0, round(h0 * W / w0))
        elif which == "h" and h0:
            W = max(0, round(w0 * H / h0))
        return W, H

    def _on_geometry(self, index: int, which: str = "") -> None:
        if self._building or self._doc is None or not self._geom:
            return
        try:
            g = self._doc.page.objects[index].geometry
            X, Y = self._geom["x"].value(), self._geom["y"].value()
            W, H = self._geom["w"].value(), self._geom["h"].value()
            if which in ("w", "h"):
                W, H = self._aspect(which, W, H, int(g.extent_x), int(g.extent_y))
                # the reference point stays where it is
                X, Y, _w, _h = self._shown_geometry(g.x, g.y, g.extent_x, g.extent_y)
            x, y, w, h = self._box_from_fields(X, Y, W, H)
            if (x, y, w, h) == (g.x, g.y, g.extent_x, g.extent_y):
                return
            self._doc.set_object_rect(index, (x, y, x + w, y + h))
        except Exception as exc:  # noqa: BLE001  # swallow-ok: the message is shown
            self.statusMessage.emit(str(exc))
            return
        self.changed.emit()

    def _add_family(
        self,
        lay,
        index: int,
        props: dict,
        title: str,
        rows: list,
        extra=None,
        keys=(),
        page_id: str = "",
        remove: str = "",
        link: bool = False,
    ) -> None:
        # a companion row (an 'at minimum / maximum' value) shows with its expression only: the
        # sheet leaves its defaults behind. An expression row shows whenever its key
        # is stored, even empty (Add animation writes it empty: the user types the tag next)
        driver, last = {}, None
        for k, _label, kind in rows:
            if kind in ("expr", "command"):
                last = k
            elif last is not None:
                driver[k] = last
        present = [
            (k, label, kind)
            for k, label, kind in rows
            if k in props and (k not in driver or driver[k] in props)
        ]
        if not present and not any(k in props for k in keys):
            return
        box = self._section(
            title,
            index,
            page_id,
            remove=(lambda i=index, w=remove: self._remove_family(i, w)) if remove else None,
        )
        form = box.form()
        for key, label, kind in present:
            if kind == "number":
                # (the row's tip goes in first: a framed row's problem shows over it)
                ed = self._number_row(index, key, props[key], ROW_TIPS.get(key, ""))
            else:
                ed = self._prop_edit(index, key, props[key], kind)
                if key in ROW_TIPS:
                    ed.setToolTip(ROW_TIPS[key])
            lab = self._scrub(label, ed) if isinstance(ed, NumberField) else label
            form.addRow(lab, ed)
        if extra is not None:
            extra(form)
        if link and page_id:
            form.addRow(self._link("Edit in Object Properties…", index, page_id))
        lay.addWidget(box)

    @staticmethod
    def _motion_page(props: dict) -> str:
        """The first movement / scaling page the object configures (move_h when none)."""
        for key, pid in (
            ("MVX", "move_h"),
            ("MVY", "move_v"),
            ("ROT", "move_rot"),
            ("SZX", "scale_h"),
            ("SZY", "scale_v"),
        ):
            if key in props:
                return pid
        return "move_h"

    def _add_display_value(self, lay, index: int, props: dict) -> None:
        """The Display Value rows."""
        if not any(k in props for k, _l, _kind in FAMILY_DISPLAY) and "TXT_STR" not in props:
            return
        from ...format import dspval as DV

        mode = c_atoi(props.get("TXTMODE"))
        box = self._section(
            "Display value",
            index,
            "display_value",
            remove=lambda i=index: self._remove_family(i, "display_value"),
            remove_text="Clear Property",
        )
        form = box.form()
        if "TXTMODE" in props:
            form.addRow("Type", self._ro_label(DV.TYPE_LABELS.get(mode, str(props.get("TXTMODE") or ""))))
        if mode in (DV.NUMERIC, DV.STRING):
            expr = (
                DV.split_numeric(props.get("TXT_STR") or "")[0]
                if mode == DV.NUMERIC
                else (props.get("TXT_STR") or "")
            )
            ed = self._expr_line(expr, "expr")
            ed.setProperty("propKey", "TXT_STR")
            ed.setToolTip(
                "The value the text shows" + (" (its format is kept)" if mode == DV.NUMERIC else "")
            )
            ed.editingFinished.connect(lambda e=ed, i=index: self._on_display_expression(i, e))
            form.addRow("Numeric expression" if mode == DV.NUMERIC else "String expression", ed)
            fmt = DV.split_numeric(props.get("TXT_STR") or "")[1] if mode == DV.NUMERIC else ""
            if fmt:
                form.addRow("Format", self._ro_label(fmt))
        for key, label, kind in FAMILY_DISPLAY:
            if key not in props or key == "TXTMODE":
                continue
            if key == "TXT_A" and mode == DV.ON_OFF:
                label = "ON text when"
            elif key == "TXT_A" and mode == DV.ARRAY:
                label = "Array expression"
            form.addRow(label, self._prop_edit(index, key, props[key], kind))
        form.addRow(self._link("Edit in Object Properties…", index, "display_value"))
        lay.addWidget(box)

    def _on_display_expression(self, index: int, ed) -> None:
        """A Numeric / String display's expression, written as the sheet's Display Value
        page writes it (doc.apply_text_display: TXT_STR with the format it had)."""
        if self._building or self._doc is None or ed.isReadOnly():
            return
        if ed.text() == ed.property("orig"):
            return
        import dataclasses

        doc = self._doc
        try:
            before = doc.text_display(index)
            after = dataclasses.replace(before, expression=ed.text())
            ok = doc.apply_text_display(index, before, after)
        except Exception as exc:  # noqa: BLE001  # swallow-ok: reported
            self.statusMessage.emit(str(exc))
            return
        if ok is False:
            self.statusMessage.emit(self._not_stored(index, "TXT_STR"))
            return
        ed.setProperty("orig", ed.text())
        if ok:
            self.changed.emit()

    def _add_fill_level_rows(self, form, index: int, props: dict) -> None:
        """Fill level: the direction (FILMODE: Up = no key, 1 Down, 2 Left,
        3 Right) and the colour of the unfilled part (FILCOL)."""
        if not any(k in props for k in ("FIL", "FILMODE", "FILCOL", "FILMINPC", "FILMAXPC")):
            return
        from ...format import fillcolour as FC

        combo = QtWidgets.QComboBox()
        combo.addItems(list(FILL_DIRECTIONS))
        combo.setCurrentIndex(FC.direction(props))
        combo.setToolTip("The direction the level fills in")
        combo.activated.connect(lambda pos, i=index: self._on_fill_direction(i, pos))
        form.addRow("Fill direction", combo)
        pair = FC.level_background(props)
        btn = self._swatch_button(pair[0], off=pair[1], title="Background Colour")
        btn.clicked.connect(lambda _=False, i=index, b=btn: self._on_fill_background(i, b))
        form.addRow("Background colour", btn)

    def _on_fill_direction(self, index: int, pos: int) -> None:
        if self._building or self._doc is None:
            return
        doc = self._doc
        try:
            if pos == 0:
                ok = "FILMODE" not in doc.object_properties(index) or doc.remove_property(index, "FILMODE")
            else:
                ok = doc.ensure_property(index, "FILMODE", str(pos))
        except Exception as exc:  # noqa: BLE001  # swallow-ok: the message is shown
            self.statusMessage.emit(str(exc))
            return
        if ok:
            self.changed.emit()
        else:
            self.statusMessage.emit(self._not_stored(index, "the fill direction"))

    def _on_fill_background(self, index: int, btn) -> None:
        if self._building or self._doc is None:
            return
        from .. import colour_picker
        from ...format import fillcolour as FC

        cur = FC.level_background(self._doc.object_properties(index))
        got = colour_picker.choose_colour(
            self, cur, title="Background Colour", anchor=btn if isinstance(btn, QtWidgets.QWidget) else None
        )
        if got is None:
            return
        try:
            ok = self._doc.set_property(index, "FILCOL", FC.fmt_pair(got))
        except Exception as exc:  # noqa: BLE001  # swallow-ok: the message is shown
            self.statusMessage.emit(str(exc))
            return
        if ok:
            self._set_swatch(btn, got[0], got[1])
            self.changed.emit()
        else:
            self.statusMessage.emit(self._not_stored(index, "the background colour"))

    def _add_disable_style(self, form, index: int, props: dict) -> None:
        """Disable style (GREY_STYLE): Embossed / Grayed / Hidden / Unchanged
        as on Object Properties > Access > Disable."""
        if "GREY_STYLE" not in props:
            return
        from ..objprops.fields import GREY_STYLES

        combo = QtWidgets.QComboBox()
        for label, value in GREY_STYLES:
            combo.addItem(label, value)
        cur = props.get("GREY_STYLE") or ""
        k = combo.findData(cur)
        if k < 0 and cur:
            combo.addItem("%s (not found)" % cur, cur)
            k = combo.count() - 1
        combo.setCurrentIndex(max(0, k))
        combo.activated.connect(lambda pos, i=index, c=combo: self._on_disable_style(i, c.itemData(pos)))
        form.addRow("Disable style", combo)

    def _on_disable_style(self, index: int, value) -> None:
        if self._building or self._doc is None or not value:
            return
        if (self._doc.object_properties(index).get("GREY_STYLE") or "") == value:
            return
        self._apply(
            lambda: self._doc.set_property(index, "GREY_STYLE", str(value)),
            self._not_stored(index, "the disable style"),
        )

    # -- Center axis offsets --------------------------------------------------------------
    def _add_axis_rows(self, form, index: int, props: dict) -> None:
        """'Rotation centre' / 'Scale X anchor' / 'Scale Y anchor': the Center
        axis offset as a choice of the Express points; a Custom offset shows
        as its own entry, and 'Custom offset…' opens the object sheet's page
        (objprops.motion: the keys CtDraw's page writes)."""
        from ..objprops import motion as MO

        for label, family, keys, page_id in AXIS_ROWS:
            if not any((props.get(k) or "").strip() for k in keys):
                continue
            if family == "rot" and (props.get("ROTSL") or "").strip() and c_atoi(props.get("ROTSLMODE")) & 1:
                page_id = "slider_rot"  # the slider owns it: move_rot is greyed
            axis = MO.AXES[family]
            state, locked = MO.axis_state(props, axis)
            combo = QtWidgets.QComboBox()
            rotation = family == "rot"
            choices = MO.axis_choices(family)
            for name, choice in choices:
                combo.addItem(name, choice)
            if state[0] or locked:
                combo.addItem("Custom: %s" % (locked or MO.offset_text(state, axis.axes)), state)
                combo.setCurrentIndex(combo.count() - 1)
            else:
                combo.setCurrentIndex([c for _n, c in choices].index(state))
            combo.addItem("Custom offset…", "custom")
            combo.setToolTip(MO.axis_summary(axis.axes, state, rotation))
            combo.setEnabled(not locked)
            combo.activated.connect(
                lambda pos, c=combo, i=index, f=family, s=state, pid=page_id: self._on_axis_choice(
                    i, f, c, pos, s, pid
                )
            )
            form.addRow(label, combo)

    def _on_axis_choice(self, index: int, family: str, combo, pos: int, initial, page_id: str) -> None:
        if self._building or self._doc is None:
            return
        data = combo.itemData(pos)
        if data == "custom":
            self.editPage.emit(index, page_id)
            QtCore.QTimer.singleShot(0, self, self.refresh)  # the row shows the stored choice
            return
        state = tuple(data)
        if state == tuple(initial):
            return
        from ..objprops import motion as MO

        doc = self._doc
        props = doc.object_properties(index)
        label = {"rot": "rotation centre", "sx": "scale anchor", "sy": "scale anchor"}[family]
        ok = True
        try:
            with doc.batch(label):
                for key, value in MO.axis_edits(MO.AXES[family], state):
                    if value is None:
                        if key in props:
                            ok = doc.remove_property(index, key) and ok
                    else:
                        ok = doc.ensure_property(index, key, value) and ok
        except Exception as exc:  # noqa: BLE001  # swallow-ok: the message is shown
            self.statusMessage.emit(str(exc))
            return
        if not ok:
            self.statusMessage.emit(self._not_stored(index, "the %s" % label))
        self.changed.emit()
        QtCore.QTimer.singleShot(0, self, self.refresh)

    def _addable(self, index: int, props: dict) -> list:
        """The 'Add animation…' entries this object can take: its class has
        the page (the object sheet's registry), no configured page excludes
        it (a slider owns its axis), and the property is not set yet."""
        try:
            from ..objprops import registry as R

            ctx = R.SheetContext.for_object(self._doc, index)
            disabled = R.disabled_pages(R.configured_pages(ctx, props), ctx.type_code)
        except Exception:  # noqa: BLE001 -- offer everything rather than nothing
            _note_exc("properties._addable")
            return [e for e in ADDABLE if e[1] != DISPLAY_VALUE]
        drops = getattr(self._doc, "_genie_root_drops", None)
        from ...format import dspval as DV

        display_keys = {k for k, _l, _kind in FAMILY_DISPLAY} | {"TXT_STR"}
        out = []
        for label, key, default in ADDABLE:
            pid = ADD_PAGES.get(key)
            if pid is not None:
                page = R.PAGE_BY_ID[pid]
                if not ctx.mask & page.bit or pid in disabled:
                    continue
            if key == DISPLAY_VALUE:
                if any(k in props for k in display_keys) or c_atoi(props.get("TXTMODE")) != DV.ON_OFF:
                    continue
            elif key == "COL_MAXON":
                from ...format import fillcolour as FC

                if any(k in props for k in FC.KEYS):
                    continue
            elif key == INPUT_TOUCH:
                try:
                    if (id(self._doc), index) in self._show_touch or self._doc.input_commands(index)["touch"]:
                        continue  # (its rows are shown already)
                except Exception:  # noqa: BLE001  # swallow-ok: not known, offer it
                    pass
            elif key != INPUT_KEYBOARD and key in props:
                continue
            if key not in (INPUT_TOUCH, INPUT_KEYBOARD, DISPLAY_VALUE):
                try:
                    if drops is not None and drops(index, key):
                        continue  # a placed genie root drops it on load
                except Exception:  # noqa: BLE001
                    _note_exc("properties._addable/drops")
                    pass
            out.append((label, key, default))
        return out

    def _add_input(self, lay, index: int, props: dict) -> None:
        """Input: the object's touch command (a button's own keys, else its
        Touch child record) and one row per keyboard command (Keyboard child
        records). Other classes (Cicode object, trend...) keep the raw rows
        of their own CMD / ACTION keys."""
        doc = self._doc
        po = doc.objects[index]
        if po.type_code >= 0x1000 and po.type_code != 0x1001:
            self._add_family(lay, index, props, "Input", FAMILY_INPUT, page_id="touch")
            return
        try:
            info = doc.input_commands(index)
        except Exception as exc:  # noqa: BLE001  # swallow-ok: the message is shown
            self.statusMessage.emit(str(exc))
            return
        touch = info["touch"] or {}
        shown = bool(touch) or (id(doc), index) in self._show_touch
        if not (shown or info["keys"]):
            return
        box = self._section(
            "Input",
            index,
            "touch",
            remove=(lambda i=index: self._remove_family(i, "input")) if info["editable"] else None,
        )
        form = box.form()
        # (with the read-only banner shown, it says it once)
        if not info["editable"] and not self._ro_reason:
            note = self._dim_label(info["reason"])
            note.setWordWrap(True)
            note.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
            form.addRow(note)
        multi_tip = "Edit multi-line commands in Object Properties (Input tab)."
        if shown:
            for key, label in TOUCH_ROWS:
                if info["button"] and key not in IN.BUTTON_TOUCH_KEYS:
                    continue  # a button has no double-click command
                val = touch.get(key, "")
                if key == "RPT_RATE":
                    ed = self._rate_field(val)
                    ed.setEnabled(info["editable"])
                    ed.valueCommitted.connect(lambda _v, e=ed, i=index: self._on_touch_rate(i, e))
                    form.addRow(self._scrub(label, ed), ed)
                    continue
                ed = self._expr_line(val, "command")
                ed.setProperty("propKey", key)
                if "\n" in val or not info["editable"]:
                    ed.setReadOnly(True)
                    ed.setToolTip(multi_tip if "\n" in val else info["reason"])
                ed.editingFinished.connect(lambda e=ed, i=index, k=key: self._on_touch_value(i, k, e))
                form.addRow(label, ed)
        kids = doc.keyboard_children(index)
        for child, vals in zip(kids, info["keys"]):
            row = QtWidgets.QWidget()
            h = QtWidgets.QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(_sp(1))
            cmd = vals.get("CMD", "")
            ed = self._expr_line(cmd, "command")
            ed.setProperty("propKey", "@key")
            if "\n" in cmd or not info["editable"]:
                ed.setReadOnly(True)
                if "\n" in cmd:
                    ed.setToolTip(multi_tip)
            ed.editingFinished.connect(lambda e=ed, c=child: self._on_key_command(c, e))
            h.addWidget(ed, 1)
            rm = QtWidgets.QToolButton()
            rm.setText("×")
            rm.setToolTip("Remove this keyboard command")
            rm.setEnabled(info["editable"])
            rm.clicked.connect(lambda _=False, c=child: self._on_remove_input(c))
            h.addWidget(rm)
            seq = vals.get("KEY_SEQ") or ""
            form.addRow("Key %s" % seq if seq else "Key", row)
        brow = QtWidgets.QWidget()
        bh = QtWidgets.QHBoxLayout(brow)
        bh.setContentsMargins(0, 0, 0, 0)
        add = QtWidgets.QPushButton("Add Key Sequence…")
        add.setEnabled(info["editable"])
        add.clicked.connect(lambda _=False, i=index: self._on_add_key(i))
        bh.addWidget(add)
        bh.addStretch(1)
        form.addRow(brow)
        more = self._link("Edit Input…", index, "touch", slot=lambda i=index: self.editInput.emit(i))
        more.setToolTip(
            "Open Object Properties on the Input tab (log messages, security, multi-line commands)"
        )
        form.addRow(more)
        lay.addWidget(box)

    def _rate_field(self, value) -> NumberField:
        """The touch repeat rate: a number of ms; 'Default' = no value of its own."""
        from ..objprops.fields import number_range

        lo, hi, unit = number_range("RPT_RATE")
        ed = NumberField(lo=lo - 1, hi=hi, suffix=unit)
        ed.setSpecialValueText("Default (%s ms)" % IN.DEFAULT_RPT_RATE)
        ed.setProperty("propKey", "RPT_RATE")
        text = str(value or "").strip()
        ed.load(int(text) if is_digits(text) and lo <= int(text) <= hi else lo - 1)
        ed.setToolTip("Delay before the first repeat and between repeats")
        return ed

    def _on_touch_rate(self, index: int, ed) -> None:
        if self._building or self._doc is None or ed.isReadOnly():
            return
        text = "" if ed.value() < ed.minimum() + 1 else str(ed.value())
        cur = dict(self._doc.input_commands(index)["touch"] or {})
        if (cur.get("RPT_RATE") or "") == text:
            return
        cur["RPT_RATE"] = text
        self._input_edit(
            lambda: self._doc.set_input_commands(index, touch=cur, label="touch command"),
            "Could not set the repeat rate",
        )

    def _input_edit(self, fn, err: str) -> bool:
        """Run a document input edit; report a refusal instead of raising."""
        if self._building or self._doc is None:
            return False
        try:
            ok = fn()
        except ValueError as exc:
            self.statusMessage.emit(str(exc))
            return False
        except Exception as exc:  # noqa: BLE001  # swallow-ok: reported
            self.statusMessage.emit("%s: %s" % (err, exc))
            return False
        if ok:
            self.changed.emit()
            # (child indices changed: the form is built again when the window did not)
            QtCore.QTimer.singleShot(0, self, self._refresh_if_stale)
        return bool(ok)

    def _on_touch_value(self, index: int, key: str, ed: QtWidgets.QLineEdit) -> None:
        if self._building or self._doc is None or ed.isReadOnly():
            return
        if ed.text() == ed.property("orig"):
            return
        text = ed.text().strip() if key == "RPT_RATE" else ed.text()
        if key == "RPT_RATE" and text and not is_digits(text):
            self.statusMessage.emit("The repeat rate is a number of milliseconds.")
            ed.setText(str(ed.property("orig")))
            return
        cur = dict(self._doc.input_commands(index)["touch"] or {})
        cur[key] = text
        if self._input_edit(
            lambda: self._doc.set_input_commands(index, touch=cur, label="touch command"),
            "Could not set the touch command",
        ):
            ed.setProperty("orig", ed.text())

    def _on_key_command(self, child: int, ed: QtWidgets.QLineEdit) -> None:
        if self._building or self._doc is None or ed.isReadOnly():
            return
        if ed.text() == ed.property("orig"):
            return
        if self._input_edit(
            lambda: self._doc.set_input_property(child, "CMD", ed.text()),
            "Could not set the keyboard command",
        ):
            ed.setProperty("orig", ed.text())

    def _on_remove_input(self, child: int) -> None:
        self._input_edit(lambda: self._doc.remove_input(child), "Could not remove the command")

    def _on_add_key(self, index: int) -> bool:
        if self._doc is None:
            return False
        from ..objprops.input import KeySequenceDialog

        others = [k.get("KEY_SEQ", "") for k in self._doc.input_commands(index)["keys"]]
        seq = KeySequenceDialog.get_sequence(self, self._doc, "", others)
        if seq:
            return self._input_edit(
                lambda: self._doc.add_input(index, "keyboard", {"KEY_SEQ": seq}) is not None,
                "Could not add the keyboard command",
            )
        return False

    def _add_fill_colour(self, lay, index: int, props: dict) -> None:
        from ...format import fillcolour as FC

        keys = ("COL_MAXON", "COL_A", "COL_B", "COL_C", "COL_D", "COL_E", "COLMODE", "COL_ARR", "COL_EXP")
        if not any(k in props for k in keys):
            return
        box = self._section(
            "Fill colour", index, "fill_color", remove=lambda i=index: self._remove_family(i, "fill_color")
        )
        form = box.form()
        mode = c_atoi(props.get("COLMODE"))
        form.addRow("Type", self._ro_label(FC.TYPE_LABELS.get(mode, str(props.get("COLMODE")))))
        if "COL_MAXON" in props:
            # COL_MAXON's two halves are the two FLASH phases of ONE colour (the
            # ON colour; a gradient's at-maximum) -- the same colour twice =
            # steady. The OFF colour is the object's own fill colour (above).
            pair = parse_colour_pair(props["COL_MAXON"])
            row = QtWidgets.QWidget()
            h = QtWidgets.QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(_sp(1))
            for part, name in ((0, "Colour"), (1, "Flash to")):
                btn = self._swatch_button(
                    pair[part], text=name, title="ON Colour" if part == 0 else "Flash To"
                )
                btn.setToolTip(
                    "The colour"
                    if part == 0
                    else "The second phase of a flashing colour (the same colour again: steady)"
                )
                btn.clicked.connect(lambda _=False, i=index, p=part, b=btn: self._on_colour_pair(i, p, b))
                h.addWidget(btn)
            h.addStretch(1)
            form.addRow("At maximum" if mode == FC.GRADIENT else "ON colour", row)
        for k in ("COL_A", "COL_B", "COL_C", "COL_D", "COL_E"):
            # an On / Off colour's condition shows even before it is typed (Add animation >
            # Fill colour stores the ON colour; the condition is the next thing to type)
            if k in props or (k == "COL_A" and mode == FC.ON_OFF):
                label = "ON colour when" if k == "COL_A" and mode == FC.ON_OFF else "Condition " + k[-1]
                form.addRow(label, self._prop_edit(index, k, props.get(k, ""), "expr"))
        for k, label in (("COL_ARR", "Array expression"), ("COL_EXP", "Colour expression")):
            if k in props:
                form.addRow(label, self._prop_edit(index, k, props[k], "expr"))
        form.addRow(self._link("Edit in Object Properties…", index, "fill_color"))
        lay.addWidget(box)

    # -- removing an animation -------------------------------------------------------------
    def _family_keys(self, index: int, which: str) -> list:
        """The stored keys of animation ``which`` (the keys the sheet's Clear Property
        clears: objprops sub families); a key a switched-on slider shares stays."""
        props = self._doc.object_properties(index)
        from ..objprops import access as AC, appearance as AP, motion as MO
        from ...format import fillcolour as FC

        fams = {
            "visibility": AP.VISIBILITY_SUB.family,
            "disable": AC.DISABLE.family,
            "fill_color": FC.KEYS,
            "fill_level": FC.LEVEL_KEYS,
        }
        if which == "motion":
            fam = [k for sub in MO.MOVEMENT_SUBS + MO.SCALING_SUBS for k in sub.family]
            keep = set()
            for sub in MO.SLIDER_SUBS:
                if (props.get(sub.primary) or "").strip():
                    keep.update(sub.family)
            fam = [k for k in fam if k not in keep]
        else:
            fam = list(fams.get(which, ()))
        out = []
        for key in fam:
            if key.endswith("*"):
                pre = key[:-1]
                out.extend(k for k in props if k.startswith(pre) and is_digits(k[len(pre) :]))
            elif key in props and key not in out:
                out.append(key)
        return out

    def _remove_family(self, index: int, which: str) -> None:
        """'Remove' in a section's menu: the animation's keys go in one undo step (the
        fill keys through the fill writer, as the sheet's Apply after Clear Property)."""
        if self._building or self._doc is None or self._ro_reason:
            return
        doc = self._doc
        titles = {
            "visibility": "visibility",
            "disable": "disable",
            "fill_color": "fill colour",
            "fill_level": "fill level",
            "motion": "movement and scaling",
            "input": "input commands",
            "display_value": "display value",
        }
        label = "remove %s" % titles.get(which, which)
        try:
            if which == "input":
                info = doc.input_commands(index)
                if not info["touch"] and not info["keys"]:
                    # the empty Input section Add animation > Touch command shows: nothing
                    # is stored yet, so it just goes
                    self._show_touch.discard((id(doc), index))
                    QtCore.QTimer.singleShot(0, self, self.refresh)  # (from the section's menu)
                    return
                touch = {k: "" for k in (info["touch"] or {})}
                ok = doc.set_input_commands(index, touch=touch, keys=[], label=label)
                if ok:
                    self._show_touch.discard((id(doc), index))
            elif which == "display_value":
                from ...format import dspval as DV

                before = doc.text_display(index)
                ok = doc.apply_text_display(index, before, DV.cleared(before))
            else:
                keys = self._family_keys(index, which)
                if not keys:
                    return
                if which in ("fill_color", "fill_level"):
                    ok = doc.set_fill_keys(index, [(k, None) for k in keys], label=label)
                else:
                    ok = True
                    with doc.batch(label):
                        for k in keys:
                            ok = doc.remove_property(index, k) and ok
        except Exception as exc:  # noqa: BLE001  # swallow-ok: reported
            self.statusMessage.emit(str(exc))
            return
        if ok:
            self.changed.emit()
            QtCore.QTimer.singleShot(0, self, self._refresh_if_stale)
        elif ok is False:
            self.statusMessage.emit(
                "Could not remove the %s. Nothing was changed." % titles.get(which, which)
            )

    # -- All properties -----------------------------------------------------------------------
    def _all_properties(self, index: int, props: dict) -> PanelSection:
        """Every stored key: labelled by the setting it is (the key itself in the tool tip
        and a dim second line), a colour pair as a swatch, a search at the top. The Graphics
        Builder's own notes ('!*' keys) only with 'Show Graphics Builder Notes'."""

        def extra(menu):
            a = menu.addAction("Show Graphics Builder Notes")
            a.setCheckable(True)
            a.setChecked(show_private_keys())
            a.toggled.connect(self._on_private_keys)

        box = self._section("All properties", extra_menu=extra)
        # (folded by default: its rows come on the first unfold, PanelSection.set_lazy)

        def fill():
            form = box.form()
            from ..style import theme

            search = theme.SearchField("Search properties")
            search.setProperty("propKey", "@search")
            form.addRow(search)
            labels = sheet_labels()
            private = show_private_keys()
            rows = []
            dim = theme.COLORS.get("text_dim", "#969ca5")
            hidden = 0
            for key, val in props.items():
                if key.startswith("!*") and not private:
                    hidden += 1
                    continue
                name = KEY_LABELS.get(key) or labels.get(key.upper()) or ""
                lab = QtWidgets.QLabel()
                lab.setTextFormat(QtCore.Qt.RichText)
                import html

                if name and name != key:
                    lab.setText(
                        "%s<br><span style='color:%s; font-size:%dpx'>%s</span>"
                        % (html.escape(name), dim, theme.type_px("caption"), html.escape(key))
                    )
                else:
                    lab.setText(html.escape(key))
                lab.setToolTip(key)
                if is_colour_pair(val):
                    pair = parse_colour_pair(val)
                    ed = self._swatch_button(pair[0], off=pair[1], title=name or key)
                    ed.clicked.connect(lambda _=False, i=index, k=key, b=ed: self._on_raw_colour(i, k, b))
                else:
                    kind = "command" if key in COMMAND_KEYS else "expr" if key in EXPR_KEYS else ""
                    ed = self._prop_edit(index, key, val, kind)
                form.addRow(lab, ed)
                rows.append((form.rowCount() - 1, ("%s %s %s" % (name, key, val)).lower()))
            if hidden:
                note = self._dim_label(
                    "%d Graphics Builder %s hidden (the section menu shows "
                    "them)." % (hidden, "note" if hidden == 1 else "notes")
                )
                note.setWordWrap(True)
                note.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
                form.addRow(note)

            def filt(text, f=form, rs=rows, b=box):
                if b.folded():
                    return
                m = search.matcher(text)  # (a TextMatch: called with the row's text)
                for r, hay in rs:
                    f.setRowVisible(r, m.empty or m(hay))

            search.searchChanged.connect(filt)
            # (an unfold shows every row again: the search still applies)
            box.foldChanged.connect(lambda folded: folded or filt(search.text()))

        box.set_lazy(fill)
        return box

    def _on_private_keys(self, on: bool) -> None:
        set_setting(PRIVATE_KEYS_KEY, bool(on))
        QtCore.QTimer.singleShot(0, self, self.refresh)

    def _on_raw_colour(self, index: int, key: str, btn) -> None:
        if self._building or self._doc is None:
            return
        from .. import colour_picker

        value = self._doc.object_properties(index).get(key, "")
        pair = parse_colour_pair(value)
        got = colour_picker.choose_colour(
            self,
            (pair[0], pair[1]),
            title=KEY_LABELS.get(key, key),
            anchor=btn if isinstance(btn, QtWidgets.QWidget) else None,
            focus_entry=self._take_entry_focus(),
        )
        if got is None:
            return
        self._apply(
            lambda: self._doc.set_property(index, key, "0x%08x,0x%08x" % got), self._not_stored(index, key)
        )

    # -- object edit handlers ------------------------------------------------------------------
    def _on_object_colour(self, index: int, btn: QtWidgets.QPushButton) -> None:
        if self._building or self._doc is None:
            return
        try:
            obj = self._doc.page.objects[index]
        except IndexError:
            return
        caption = obj.kind in (ObjectKind.TEXT, ObjectKind.NUMBER, ObjectKind.BUTTON)
        closed = obj.kind in (ObjectKind.RECTANGLE, ObjectKind.ELLIPSE, ObjectKind.POLYGON)
        stored = self._slot_pair(index, "fill" if closed else None)
        if stored is not None:
            current = stored
        elif caption:
            current = obj.style.text_colour
        elif closed:
            current = self._fill_slot_colour(index)
        else:
            current = obj.style.fill_colour if obj.style.filled else obj.style.line_colour
        from .. import colour_picker
        from ...format import geometry as G

        po = self._doc.parsed.page.objects[index]
        # a colour of the object's own style block can flash / be Transparent
        # (the pair is stored); a button's caption (CtDraw: 'font transparent
        # flash color is not supported') and the other channels take one
        # steady colour. A text's foreground can flash but never be
        # Transparent: CtDraw's text pages turn that off on their foreground
        # selector
        pairs = bool(po.subtype_size & 0x2) and (not caption or po.type_code == 0x05)
        pair = colour_picker.choose_colour(
            self,
            current,
            title="Fill Colour" if closed else ("Text Colour" if caption else "Colour"),
            flash=pairs,
            transparent=pairs and not caption,
            anchor=btn if isinstance(btn, QtWidgets.QWidget) else None,
            focus_entry=self._take_entry_focus(),
        )
        if pair is None:
            return
        on, off = pair
        packed = on & 0xFFFFFF
        try:
            if caption:
                # a text's fill slot / a button's font-block text colour
                ok = self._doc.set_foreground(index, on, off)
            elif closed:
                # the FILL slot only (the Line colour row edits the line slot, the Filled
                # box the fill style -- like the sheet's [Fill] Colour); a hollow shape
                # keeps its outline
                ok = self._doc.set_colour_slot(index, G.SLOT_FILL, on, off, label="fill colour")
            elif obj.kind is ObjectKind.PIPE and pairs:
                ok = self._doc.set_pipe_colour(index, on, off)
            elif obj.kind is ObjectKind.LINE and pairs:
                ok = self._doc.set_colour_slot(index, G.SLOT_LINE, on, off, label="line colour")
            else:
                ok = self._doc.set_colour(index, packed, line=obj.kind in (ObjectKind.LINE, ObjectKind.PIPE))
        except Exception as exc:  # noqa: BLE001  # swallow-ok: the message is shown
            self.statusMessage.emit(str(exc))
            return
        if ok:
            self._set_swatch(btn, on, off)
            self.changed.emit()
        else:
            self.statusMessage.emit(
                ("This object takes its colours from its library element. Edit the element to change them.")
            )

    def _fill_slot_colour(self, index: int):
        """A closed shape's stored FILL colour (the style block's fill slot, the
        design-time second half) -- also for a hollow shape, whose model fill is
        None; None for the no-colour sentinel or a record without a style block."""
        from ...format import geometry as G

        try:
            po = self._doc.parsed.page.objects[index]
        except AttributeError, IndexError:
            return None
        cb = G.find_colour_block(po)
        if cb is None:
            obj = self._doc.page.objects[index]
            return obj.style.fill_colour
        c = cb.slots[G.SLOT_FILL][1]
        return None if (c >> 24) & 0xFF == 0xFF else c & 0xFFFFFF

    def _slot_pair(self, index: int, which: str | None = None):
        """The stored (on, off) pair a swatch edits: ``which`` 'fill' / 'line'
        = that slot of the object's own style block, None = the object's own
        colour (a pipe's, a text's, a line's); None without a style block."""
        from ...format import colourstate, geometry as G

        try:
            po = self._doc.parsed.page.objects[index]
        except AttributeError, IndexError:
            return None
        if which in ("fill", "line"):
            L = colourstate.layout(po) if po.subtype_size & 0x2 else None
            if L is None or "style_at" not in L:
                return None
            import struct

            slot = G.SLOT_FILL if which == "fill" else G.SLOT_LINE
            return struct.unpack_from("<II", po.body_raw, L["style_at"] + 6 + 8 * slot)
        ch = colourstate.colour_channels(po)
        if ch is None or po.type_code == 0x1001:
            return None
        if "class" in ch:
            return ch["class"]
        return ch["line"] if po.type_code == colourstate.LINE_TC else ch["fill"]

    def _on_line_colour(self, index: int, btn: QtWidgets.QPushButton) -> None:
        """A closed shape's LINE colour (the style block's line slot), separate
        from its fill; Transparent = no border."""
        if self._building or self._doc is None:
            return
        try:
            obj = self._doc.page.objects[index]
        except IndexError:
            return
        from .. import colour_picker
        from ...format import geometry as G

        cur = self._slot_pair(index, "line")
        if cur is None:
            cur = obj.style.line_colour
        pair = colour_picker.choose_colour(
            self,
            cur,
            title="Line Colour",
            anchor=btn if isinstance(btn, QtWidgets.QWidget) else None,
            focus_entry=self._take_entry_focus(),
        )
        if pair is None:
            return
        try:
            ok = self._doc.set_colour_slot(index, G.SLOT_LINE, pair[0], pair[1], label="line colour")
        except Exception as exc:  # noqa: BLE001  # swallow-ok: the message is shown
            self.statusMessage.emit(str(exc))
            return
        if ok:
            self._set_swatch(btn, pair[0], pair[1])
            self.changed.emit()
        else:
            self.statusMessage.emit(
                "This object takes its colours from its library element: edit the element to change them."
            )

    def _on_filled(self, index: int, value: bool) -> None:
        self._apply(lambda: self._doc.set_filled(index, value), "Could not change the fill")

    def _on_line_width(self, index: int, value: int) -> None:
        self._apply(lambda: self._doc.set_line_width(index, value), "Could not set the line width")

    def _on_line_style(self, index: int, value: int) -> None:
        self._apply(lambda: self._doc.set_line_style(index, value), "Could not set the line style")

    def _on_font_face(self, index: int, font: QtGui.QFont) -> None:
        self._apply(lambda: self._doc.set_font(index, face=font.family()), "Could not set the font")

    @staticmethod
    def _watch_font_typing(combo) -> None:
        """A key in the font field starts a new commit: the name refused last is
        forgotten (:meth:`_font_unchanged`)."""
        combo.lineEdit().textEdited.connect(lambda _t, c=combo: c.setProperty("ctgRefused", None))

    @staticmethod
    def _font_unchanged(combo, text: str, stored: str) -> bool:
        """Nothing to do for this commit of the font field: the text is the stored face
        (an untouched field, even when that face is not installed here), or the same name
        this commit already refused (Enter fires both activated and editingFinished)."""
        t = (text or "").strip().lower()
        refused = combo.property("ctgRefused")
        if t and t == (stored or "").strip().lower():
            if refused is None:  # (the stored face shown again after a refusal
                from ..style import theme  # keeps the refusal's frame)

                theme.mark_invalid(combo, "")
            return True
        return bool(t) and refused is not None and t == str(refused).lower()

    def _resolve_font(self, combo, text: str, stored: str):
        """The family a typed / chosen font name means; None (the stored face shown again,
        the field framed, the status bar told) when no font has that name."""
        from ..style import theme

        text = (text or "").strip()
        if not text:
            combo.setEditText(stored)
            theme.mark_invalid(combo, "")
            return None
        fam = match_font_family(text)
        if fam is None:
            msg = "No font called '%s'." % text
            combo.setProperty("ctgRefused", text)
            combo.setEditText(stored)
            theme.mark_invalid(combo, msg)
            self.statusMessage.emit(msg)
            return None
        theme.mark_invalid(combo, "")
        if combo.currentText() != fam:
            combo.setEditText(fam)
        return fam

    def _on_font_typed(self, index: int, combo, text: str) -> None:
        """The font of a text, typed or chosen: one set_font per commit."""
        if self._building or self._doc is None or not combo.isEnabled():
            return
        try:
            stored = self._doc.page.objects[index].style.font or ""
        except IndexError:
            return
        if self._font_unchanged(combo, text, stored):
            return
        fam = self._resolve_font(combo, text, stored)
        if fam is None or fam == stored:
            return
        combo.setProperty("orig", fam)
        self._apply(lambda: self._doc.set_font(index, face=fam), "Could not set the font")

    def _on_font_size(self, index: int, pts: int) -> None:
        self._apply(lambda: self._doc.set_font(index, size=pts), "Could not set the font size")

    def _on_font_flag(self, index: int, flag: str, value: bool) -> None:
        self._apply(lambda: self._doc.set_font(index, **{flag: value}), "Could not set the text style")

    def _on_justify(self, index: int, justify: str) -> None:
        """Alignment: Left / Centre / Right (set_font justify, as the sheet's Alignment)."""
        try:
            cur = getattr(self._doc.page.objects[index].style.justify, "value", "left")
        except AttributeError, IndexError:
            return
        if cur == justify:
            return
        self._apply(lambda: self._doc.set_font(index, justify=justify), "Could not set the alignment")

    def _on_text_effect(self, index: int, effect) -> None:
        """A text's 3D effect (Normal, Shadowed, Raised, Lowered, Embossed)."""
        try:
            fl = self._doc.page.objects[index].style.style_flags
        except AttributeError, IndexError:
            return
        if effect is None or (fl and (fl[0] & 0x0F) == int(effect)):
            return
        self._apply(lambda: self._doc.set_text_effect(index, int(effect)), "Could not set the text effect")

    def _has_corner_radius(self, index: int) -> bool:
        """The rectangle has a shape tail with a corner radius (as the sheet shows it)."""
        from ...format import geometry as G

        try:
            tail = G.parse_shape_tail(self._doc.parsed.page.objects[index])
        except Exception:  # noqa: BLE001  # swallow-ok: no tail known, no row
            return False
        return tail is not None and tail.get("kind") == "rect"

    def _on_corner_radius(self, index: int, value: int) -> None:
        try:
            if int(self._doc.page.objects[index].style.corner_radius or 0) == int(value):
                return
        except AttributeError, IndexError:
            return
        self._apply(lambda: self._doc.set_corner_radius(index, int(value)), "Could not set the corner radius")

    def _add_effect_rows(self, form, index: int, st) -> None:
        """'3D effect' (None, Shadowed, Raised, Lowered, Embossed) and 'Depth' (Automatic
        or 0 to 32 px): set_effect, read as Appearance > 3D Effects reads them."""
        fl = st.style_flags
        if not fl or len(fl) < 6:
            return
        eff = QtWidgets.QComboBox()
        for label, value in panel_EFFECTS:
            eff.addItem(label, value)
        cur = fl[0] & 0x0F
        eff.setCurrentIndex(max(0, eff.findData(cur)))
        eff.setProperty("propKey", "effect")
        eff.setToolTip("A 3D edge or a shadow (Appearance > 3D Effects has its colours)")
        eff.activated.connect(lambda _p, i=index, c=eff: self._on_effect(i, c.currentData()))
        form.addRow("3D effect", eff)
        if cur:
            depth = fl[5] - 256 if fl[5] >= 128 else fl[5]
            dsp = self._number(-1, 32, " px", depth if depth >= 0 else -1)
            dsp.setSpecialValueText("Automatic")
            dsp.setProperty("propKey", "effect_depth")
            dsp.setToolTip("How far the 3D edge or the shadow reaches. Automatic: from the object's size")
            dsp.valueCommitted.connect(lambda v, i=index: self._on_effect_depth(i, v))
            form.addRow(self._scrub("Depth", dsp), dsp)

    def _on_effect(self, index: int, effect) -> None:
        try:
            fl = self._doc.page.objects[index].style.style_flags
        except AttributeError, IndexError:
            return
        if effect is None or (fl and (fl[0] & 0x0F) == int(effect)):
            return
        self._apply(lambda: self._doc.set_effect(index, int(effect)), "Could not set the 3D effect")

    def _on_effect_depth(self, index: int, depth: int) -> None:
        try:
            fl = self._doc.page.objects[index].style.style_flags
        except AttributeError, IndexError:
            return
        cur = (fl[5] - 256 if fl[5] >= 128 else fl[5]) if fl and len(fl) > 5 else None
        want = -1 if depth < 0 else int(depth)
        if cur is None or cur == want:
            return
        self._apply(lambda: self._doc.set_effect(index, fl[0] & 0x0F, want), "Could not set the depth")

    def _apply(self, fn, err: str) -> None:
        """Run a document setter (rebuild-guarded), emit changed."""
        if self._building or self._doc is None:
            return
        try:
            ok = fn()
        except Exception as exc:  # noqa: BLE001  # swallow-ok: the message is shown
            self.statusMessage.emit(str(exc))
            return
        if ok:
            self.changed.emit()
        else:
            self.statusMessage.emit(err)

    def _on_text(self, index: int, ed) -> None:
        if self._building or self._doc is None:
            return
        if ed.text() == ed.property("orig"):
            return
        from ..style.theme import mark_invalid

        try:
            ok = self._doc.set_text(index, ed.text())
        except Exception as exc:  # noqa: BLE001  # swallow-ok: the message is shown
            # the field keeps the rejected text, framed, with the reason as its tool tip
            mark_invalid(ed, "%s." % str(exc).rstrip("."))
            self.statusMessage.emit("%s." % str(exc).rstrip("."))
            return
        if ok:
            ed.setProperty("orig", ed.text())
            mark_invalid(ed, "")
            self.changed.emit()
        else:
            self.statusMessage.emit("This object cannot take a caption.")

    def _not_stored(self, index: int, key: str) -> str:
        """The status text for a property the document could not store."""
        try:
            kind = self._doc.page.objects[index].kind.value.replace("_", " ")
        except AttributeError, IndexError:
            kind = "object"
        what = KEY_LABELS.get(key)
        if what:
            what = "'%s'" % what
        elif key.startswith("the "):
            what = key
        else:
            what = "this property"
        return "Could not store %s on this %s. Nothing was changed." % (what, kind)

    def _on_object_prop(self, index: int, key: str, ed: QtWidgets.QLineEdit) -> None:
        if self._building or self._doc is None:
            return
        if ed.text() == ed.property("orig"):
            return
        doc = self._doc
        try:
            if key in doc.object_properties(index):
                ok = doc.set_property(index, key, ed.text())
            else:
                # a row shown before its key is stored (an On / Off colour's condition)
                ok = doc.ensure_property(index, key, ed.text())
        except Exception as exc:  # noqa: BLE001  # swallow-ok: the message is shown
            self.statusMessage.emit(str(exc))
            return
        if ok:
            ed.setProperty("orig", ed.text())
            self.changed.emit()
        else:
            self.statusMessage.emit(self._not_stored(index, key))

    def _number_row(self, index: int, key: str, value, tip: str = "") -> QtWidgets.QWidget:
        """A number row of an animation (offsets, angles, sizes, fill percentages): a
        NumberField in the range the object sheet uses (objprops.fields.NUMBER_RANGES). A
        stored value that is not a number shows as text in a framed field until a number
        replaces it; a genie parameter ('%offset%') stays text."""
        from ..objprops.fields import number_range

        rng = number_range(key)
        text = str(value if value is not None else "").strip()
        lo, hi, unit = rng if rng is not None else (-32767, 32767, "")
        import re

        whole = re.fullmatch(r"-?\d+", text) is not None
        if whole and lo <= int(text) <= hi:
            sp = self._number(lo, hi, unit, int(text))
            sp.setProperty("propKey", key)
            if tip:
                sp.setToolTip(tip)
            sp.valueCommitted.connect(lambda v, i=index, k=key: self._on_number(i, k, v))
            return sp
        ed = self._line(text)
        ed.setProperty("propKey", key)
        if tip:
            ed.setToolTip(tip)  # (back once a number replaces the stored value)
        if "%" not in text:
            from ..style.theme import mark_invalid

            if whole:
                mark_invalid(
                    ed,
                    "The stored value %d is outside %d to %d. Type a number "
                    "in that range to replace it." % (int(text), lo, hi),
                )
            else:
                mark_invalid(
                    ed,
                    "The stored value '%s' is not a number. Type a number "
                    "from %d to %d to replace it." % (text, lo, hi),
                )
        ed.editingFinished.connect(lambda e=ed, i=index, k=key, r=(lo, hi): self._on_number_text(i, k, e, r))
        return ed

    def _on_number(self, index: int, key: str, value: int) -> None:
        if self._building or self._doc is None:
            return
        if (self._doc.object_properties(index).get(key) or "").strip() == str(value):
            return
        self._apply(lambda: self._doc.set_property(index, key, str(int(value))), self._not_stored(index, key))

    def _on_number_text(self, index: int, key: str, ed, rng) -> None:
        """A number row that holds text (a stored value that is not a number): only a
        number (or a sum) in range, or a genie parameter, replaces it."""
        if self._building or self._doc is None or ed.isReadOnly():
            return
        text = ed.text().strip()
        if text == str(ed.property("orig") or "").strip():
            return
        from ..style.theme import mark_invalid
        import re

        if re.fullmatch(r"%[^%\s]+%", text):
            value = text
        else:
            v = evaluate(text)
            if v is None or not rng[0] <= round_half_away(v) <= rng[1]:
                mark_invalid(ed, "Type a number from %d to %d." % rng)
                self.statusMessage.emit("Type a number from %d to %d. Nothing was changed." % rng)
                return
            value = str(round_half_away(v))
        mark_invalid(ed, "")
        ed.setProperty("orig", value)
        self._apply(lambda: self._doc.set_property(index, key, value), self._not_stored(index, key))

    def _on_colour_pair(self, index: int, part: int, btn: QtWidgets.QPushButton) -> None:
        if self._building or self._doc is None:
            return
        try:
            value = self._doc.object_properties(index).get("COL_MAXON", "")
        except Exception as exc:  # noqa: BLE001  # swallow-ok: the message is shown
            self.statusMessage.emit(str(exc))
            return
        pair = parse_colour_pair(value)
        title = "ON colour" if part == 0 else "Flash to (the second phase of the ON colour)"
        from .. import colour_picker

        cur = (pair[0], pair[1]) if part == 0 else (pair[1], pair[1])
        got = colour_picker.choose_colour(
            self,
            cur,
            title=title,
            flash=part == 0,
            transparent=part == 0,
            anchor=btn if isinstance(btn, QtWidgets.QWidget) else None,
            focus_entry=self._take_entry_focus(),
        )
        if got is None:
            return
        if part == 0 and got[0] != got[1]:
            pair[0], pair[1] = got  # a flashing favourite: both phases
        elif part == 0 and (pair[0] == pair[1] or colour_picker.T.is_clear(got[0])):
            pair[0] = pair[1] = got[0]  # a steady colour stays steady
        else:
            pair[part] = got[0]
        try:
            ok = self._doc.set_property(index, "COL_MAXON", "0x%08x,0x%08x" % (pair[0], pair[1]))
        except Exception as exc:  # noqa: BLE001  # swallow-ok: the message is shown
            self.statusMessage.emit(str(exc))
            return
        if ok:
            self._set_swatch(btn, pair[part])
            self.changed.emit()
        else:
            self.statusMessage.emit(self._not_stored(index, "COL_MAXON"))

    def _on_add_animation(self, index: int, combo: QtWidgets.QComboBox, pos: int) -> None:
        if self._building or self._doc is None or pos <= 0:
            return
        data = combo.itemData(pos)
        combo.setCurrentIndex(0)
        if not data:
            return
        key, default = data
        target = ADDED_SECTION.get(key)
        if key in (INPUT_TOUCH, INPUT_KEYBOARD):
            self._pending_focus = target
            self._add_input_command(index, key)
            return
        doc = self._doc
        try:
            if key == DISPLAY_VALUE:
                ok = self._add_display_value_animation(index)
            elif key == "FIL":
                ok = self._add_fill_level(index)
            else:
                from ..objprops.motion import companion_defaults

                # a movement / rotation / scaling comes with CtDraw's offsets /
                # angles / sizes (its page always writes them): one undo step
                with doc.batch("add %s" % combo.itemText(pos).lower()):
                    ok = doc.ensure_property(index, key, default)
                    if ok:
                        for ck, cv in companion_defaults(key, doc.object_properties(index)):
                            doc.ensure_property(index, ck, cv)
        except Exception as exc:  # noqa: BLE001  # swallow-ok: the message is shown
            self.statusMessage.emit(str(exc))
            return
        if not ok:
            self.statusMessage.emit(
                self._not_stored(index, key if key in KEY_LABELS else "the %s" % combo.itemText(pos).lower())
            )
            return
        self._pending_focus = target
        self.changed.emit()
        # surface the new family section (the window's refresh after changed built it: then
        # the form stays, and with it the header flash _reveal queued)
        QtCore.QTimer.singleShot(0, self, self._refresh_if_stale)

    def _add_fill_level(self, index: int) -> bool:
        """Add animation > Fill level: the level expression (empty: typed next) and the keys
        the sheet's Fill > Level page stores with a new level (LevelEditor.apply:
        FILMINPC 0, FILMAXPC 100, the background FILCOL), in one undo step."""
        doc = self._doc
        with doc.batch("add fill level"):
            if not doc.ensure_property(index, "FIL", ""):
                return False
            edits = fill_level_companions(doc.object_properties(index))
            if edits and not doc.set_fill_keys(index, edits, label="fill level"):
                return False
        return "FIL" in doc.object_properties(index)

    def _add_display_value_animation(self, index: int) -> bool:
        """Add animation > Display value: the text shows a value (the sheet's Display Value
        page switched to Numeric, its expression typed next: doc.apply_text_display)."""
        import dataclasses
        from ...format import dspval as DV

        doc = self._doc
        before = doc.text_display(index)
        after = dataclasses.replace(before, mode=DV.NUMERIC, expression="", fmt="")
        ok = doc.apply_text_display(index, before, after)
        return bool(ok)

    def _add_input_command(self, index: int, which: str) -> None:
        """'Add animation > Touch / Keyboard command': a touch command shows
        the (empty) touch rows -- the record is created once a command is
        typed; a keyboard command asks for its key sequence and creates the
        record."""
        ok, reason = self._doc.input_owner_status(index)
        if not ok:
            self._pending_focus = None
            self.statusMessage.emit(reason)
            return
        if which == INPUT_TOUCH:
            self._show_touch.add((id(self._doc), index))
            self.refresh()
            return
        if not self._on_add_key(index):
            self._pending_focus = None

    # -- multi-selection mode ---------------------------------------------------------------
    def _build_multi(self, lay: QtWidgets.QVBoxLayout, idx: list[int]) -> None:
        """Properties for several objects at once."""
        doc = self._doc
        chosen = list(idx)  # (the selection the X / Y / W / H rows apply to)
        roots = sorted({doc.root_of(i) for i in idx})
        head = QtWidgets.QLabel("<b>%d objects selected</b>" % len(roots))
        head.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        lay.addWidget(head)
        arrange = self._arrange_row()
        if arrange is not None:
            lay.addWidget(arrange)
        # a placed genie's members selected without their genie are shown,
        # not changed (CtDraw re-composes them from the genie's element)
        from ..objprops.registry import genie_member_edits

        members = genie_member_edits(doc, idx)
        if members:
            note = self._dim_label(
                "%d of them are part of a genie and are not changed "
                "here: edit the genie's definition instead." % len(members)
            )
            note.setWordWrap(True)
            note.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
            lay.addWidget(note)
            idx = [i for i in idx if i not in members]
        box = doc.selection_extent(roots) if hasattr(doc, "selection_extent") else None
        if box is not None:
            lay.addWidget(self._multi_geometry_section(doc, roots, chosen, box))
        objs = doc.page.objects
        shapes = [
            i
            for i in idx
            if i < len(objs)
            and objs[i].kind
            in (
                ObjectKind.LINE,
                ObjectKind.PIPE,
                ObjectKind.POLYGON,
                ObjectKind.RECTANGLE,
                ObjectKind.ELLIPSE,
            )
        ]
        closed = [
            i
            for i in shapes
            if objs[i].kind in (ObjectKind.POLYGON, ObjectKind.RECTANGLE, ObjectKind.ELLIPSE)
        ]
        texts = [i for i in idx if i < len(objs) and objs[i].kind in (ObjectKind.TEXT, ObjectKind.NUMBER)]

        if shapes:
            self._multi_appearance_section(lay, objs, shapes, closed)
        if texts:
            self._multi_text_section(lay, objs, texts)
        # the selected objects themselves: a tree selection of members sets
        # THEIR settings, not their groups' (a canvas selection is roots)
        animated = self._add_multi_animation(lay, idx)
        if not shapes and not texts and not animated:
            lay.addWidget(self._dim_label("No common editable properties"))

    def _multi_appearance_section(
        self, lay: QtWidgets.QVBoxLayout, objs: list, shapes: list[int], closed: list[int]
    ) -> None:
        """The Appearance section of several shapes: fill and line colour, filled, line
        width and style (a value they do not share shows as Mixed)."""
        sts = [objs[i].style for i in shapes]
        abox = self._section("Appearance (%d %s)" % (len(shapes), "shape" if len(shapes) == 1 else "shapes"))
        aform = abox.form()
        if closed:
            fills = [self._fill_slot_colour(i) for i in closed]
            fbtn = self._multi_swatch(fills, "Fill colour")
            fbtn.clicked.connect(
                lambda _=False, b=fbtn, t=list(closed): self._on_multi_slot_colour(t, "fill", b)
            )
            aform.addRow("Fill colour", fbtn)
        lines = [objs[i].style.line_colour for i in shapes]
        lbtn = self._multi_swatch(lines, "Line colour")
        lbtn.clicked.connect(lambda _=False, b=lbtn, t=list(shapes): self._on_multi_slot_colour(t, "line", b))
        aform.addRow("Line colour", lbtn)
        if closed:
            fl = QtWidgets.QCheckBox()
            fl.setTristate(False)
            states = {bool(objs[i].style.filled) for i in closed}
            if len(states) > 1:
                fl.setTristate(True)
                fl.setCheckState(QtCore.Qt.PartiallyChecked)
                fl.setToolTip("Mixed: some are filled. A click fills them all")
            else:
                fl.setChecked(states.pop())
            fl.setProperty("propKey", "filled")
            fl.clicked.connect(lambda _c=False, c=fl, t=list(closed): self._on_multi_filled(t, c))
            aform.addRow("Filled", fl)
        wsp = MixedField(lo=1, hi=32, suffix=" px", live_steps=panel_option("props_arrow_live"))
        widths = [max(1, st.line_width or 1) for st in sts]
        wsp.set_mixed(_common_value(widths) is None, widths[0])
        wsp.setProperty("propKey", "line_width")
        wsp.valueCommitted.connect(
            lambda v, f=wsp: self._multi_apply(
                "line width",
                shapes,
                lambda i: self._doc.set_line_width(
                    i, self._each_value(f, v, max(1, self._doc.page.objects[i].style.line_width or 1))
                ),
            )
        )
        aform.addRow(self._scrub("Line width", wsp), wsp)
        scb = QtWidgets.QComboBox()
        scb.addItems(LINE_STYLES)
        scb.setPlaceholderText("Mixed")
        style = _common_value(st.line_style if 0 <= st.line_style < 5 else 0 for st in sts)
        scb.setCurrentIndex(style if style is not None else -1)
        scb.setProperty("propKey", "line_style")
        scb.activated.connect(
            lambda v: self._multi_apply("line style", shapes, lambda i: self._doc.set_line_style(i, v))
        )
        aform.addRow("Line style", scb)
        lay.addWidget(abox)

    def _multi_text_section(self, lay: QtWidgets.QVBoxLayout, objs: list, texts: list[int]) -> None:
        """The Text section of several texts: the font, its size and style, the foreground
        (a value they do not share shows as Mixed)."""
        sts = [objs[i].style for i in texts]
        tbox = self._section("Text (%d %s)" % (len(texts), "text" if len(texts) == 1 else "texts"))
        tform = tbox.form()
        fc = QtWidgets.QFontComboBox()
        fc.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        face = _common_value(st.font or "" for st in sts)
        fc.setPlaceholderText("Mixed")
        if face:
            fc.setCurrentFont(QtGui.QFont(face))
            fc.setEditText(face)
        elif face is None:
            fc.setCurrentIndex(-1)
            fc.setEditText("")
            fc.lineEdit().setPlaceholderText("Mixed")
        fc.setProperty("orig", fc.currentText())
        fc.setProperty("propKey", "font")
        fc.activated.connect(lambda pos, c=fc: self._on_multi_font(texts, c, c.itemText(pos)))
        fc.lineEdit().editingFinished.connect(lambda c=fc: self._on_multi_font(texts, c, c.currentText()))
        self._watch_font_typing(fc)
        tform.addRow("Font", fc)
        szc = MixedField(lo=4, hi=96, suffix=" pt", live_steps=panel_option("props_arrow_live"))
        sizes = [st.font_size or 10 for st in sts]
        szc.set_mixed(_common_value(sizes) is None, sizes[0])
        szc.setProperty("propKey", "font_size")
        szc.valueCommitted.connect(
            lambda v, f=szc: self._multi_apply(
                "font size",
                texts,
                lambda i: self._doc.set_font(
                    i, size=self._each_value(f, v, self._doc.page.objects[i].style.font_size or 10)
                ),
            )
        )
        tform.addRow(self._scrub("Size", szc), szc)
        flags, mixed = {}, {}
        for f in ("bold", "italic", "underline", "strikeout"):
            vals = {bool(getattr(st, f)) for st in sts}
            flags[f] = vals == {True}
            mixed[f] = len(vals) > 1
        justify = _common_value(getattr(st.justify, "value", "left") for st in sts)
        tform.addRow(
            "Style",
            self._style_row(
                flags,
                justify,
                lambda fl, v, t=list(texts): self._multi_apply(
                    "text style", t, lambda i: self._doc.set_font(i, **{fl: bool(v)})
                ),
                lambda j, t=list(texts): self._multi_apply(
                    "alignment", t, lambda i: self._doc.set_font(i, justify=j)
                ),
                mixed=mixed,
            ),
        )
        fg = self._multi_swatch([st.text_colour for st in sts], "Foreground")
        fg.clicked.connect(lambda _=False, b=fg, t=list(texts): self._on_multi_foreground(t, b))
        tform.addRow("Foreground", fg)
        lay.addWidget(tbox)

    def _arrange_row(self):
        """The Arrange row: tool buttons hosting the window's own align, distribute, space
        and same-size commands (the menus' QActions: their state, keys and tool tips), and
        the window's 'Align to' menu when it has one. None without a window."""
        win = self.window()
        if win is self:
            return None
        acts = list(getattr(win, "align_actions", ()) or ())
        for name in ("act_dist_h", "act_dist_v", "act_space_equally", "act_same_size"):
            a = getattr(win, name, None)
            if isinstance(a, QtGui.QAction):
                acts.append(a)
        if not acts:
            return None
        row = QtWidgets.QWidget()
        row.setObjectName("arrangeRow")
        row.setProperty("propKey", "arrange")
        from ..flow_layout import FlowLayout

        h = FlowLayout(row, spacing=2)
        h.setContentsMargins(0, _sp(0), 0, _sp(1))
        for a in acts:
            b = QtWidgets.QToolButton(row)
            b.setObjectName("arrangeButton")
            b.setDefaultAction(a)
            b.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
            b.setAutoRaise(True)
            b.setFocusPolicy(QtCore.Qt.TabFocus)
            h.addWidget(b)
        # (Arrange > Align To: a menu of the window's, when it offers one)
        menu = getattr(win, "align_to_menu", None)
        if isinstance(menu, QtWidgets.QMenu):
            b = QtWidgets.QToolButton(row)
            b.setText("Align to")
            b.setMenu(menu)
            b.setPopupMode(QtWidgets.QToolButton.InstantPopup)
            b.setProperty("ctgMenu", True)
            h.addWidget(b)
        return row

    def _multi_geometry_section(self, doc, roots: list, chosen: list, box):
        """'Selection': the switch 'Whole selection | Each object' (props/multi_apply), the
        reference point and X / Y / W / H. Whole selection: they are the selection box's
        (a change moves or scales the selection as one); each object: they are each
        object's (Mixed when they differ; X = 100 lines up their reference points)."""
        gbox = self._section("Selection")
        gform = gbox.form()
        each = multi_apply() == "each"
        sw = QtWidgets.QWidget()
        from ..flow_layout import FlowLayout

        sh = FlowLayout(sw, spacing=0)
        grp = QtWidgets.QButtonGroup(sw)
        for value, text, tip in (
            ("selection", "Whole selection", "X, Y, W and H are the selection's box"),
            (
                "each",
                "Each object",
                "X, Y, W and H are each object's: a value lines them all up or sizes them all",
            ),
        ):
            b = QtWidgets.QToolButton()
            b.setObjectName("segment")
            b.setText(text)
            b.setToolTip(tip)
            b.setCheckable(True)
            b.setChecked((value == "each") == each)
            b.setProperty("propKey", "multi_apply_" + value)
            grp.addButton(b)
            b.clicked.connect(lambda _=False, v=value: self._set_multi_apply(v))
            sh.addWidget(b)
        gform.addRow(sw)
        fields: dict = {}
        verb = "each object" if each else "the whole selection"
        self._geometry_rows(
            gform,
            fields,
            lo_w=0 if each else 1,
            field_cls=MixedField,
            tips={
                "x": "Moves %s" % verb,
                "y": "Moves %s" % verb,
                "w": "Scales %s" % verb,
                "h": "Scales %s" % verb,
            },
        )
        if each:
            shown = []
            for r in roots:
                b = doc.selection_extent([r])
                if b is not None:
                    shown.append(self._shown_geometry(b[0], b[1], b[2] - b[0], b[3] - b[1]))
            for k, col in zip(("x", "y", "w", "h"), zip(*shown) if shown else ()):
                vals = set(int(v) for v in col)
                fields[k].set_mixed(len(vals) > 1, int(col[0]))
            for k in ("x", "y", "w", "h"):
                fields[k].valueCommitted.connect(
                    lambda _v, key=k, f=fields, d=doc, sel=list(chosen): self._on_each_geometry(
                        key, f, d, sel
                    )
                )
            self._multi_geom = None
        else:
            l, t, r, b = box
            for k, v in zip(("x", "y", "w", "h"), self._shown_geometry(l, t, r - l, b - t)):
                sp = fields[k]
                sp.load(int(v))
                # (the position the move starts from: a move sets it in place)
                sp.setProperty("base", int(v))
            # (the document and selection the form was built for, not the panel's at the
            # commit: arrow steps commit after a pause)
            for k in ("x", "y"):
                fields[k].valueCommitted.connect(
                    lambda _v, s=fields[k], key=k, d=doc, sel=chosen: self._on_multi_move(
                        key, s.value() - int(s.property("base")), d, sel
                    )
                )
            for k in ("w", "h"):
                fields[k].valueCommitted.connect(
                    lambda _v, key=k, f=fields, d=doc, sel=chosen: self._on_multi_size(key, f, d, sel)
                )
            self._multi_geom = (roots, fields["x"], fields["y"], (fields["w"], fields["h"]))
        self._multi_fields = fields
        return gbox

    def _set_multi_apply(self, value: str) -> None:
        if value == multi_apply():
            return
        set_setting(MULTI_APPLY_KEY, value)
        QtCore.QTimer.singleShot(0, self, self.refresh)

    def _on_each_geometry(self, which: str, fields: dict, doc=None, chosen=None) -> None:
        """Each object: X / Y put every object's reference point there, W / H give every
        object that size (its reference point stays). One undo step."""
        doc = self._doc if doc is None else doc
        chosen = list(self._indices if chosen is None else chosen)
        if self._building or doc is None:
            return
        f = fields.get(which)
        if f is None or f.is_mixed():
            return
        from ..objprops.registry import genie_member_edits

        members = genie_member_edits(doc, chosen)
        roots = sorted({doc.root_of(i) for i in chosen if i not in members})
        value = int(f.value())
        chain = fields.get("chain")
        keep = chain is not None and chain.isChecked()
        changed = False
        try:
            with doc.batch("%s each" % ("move" if which in ("x", "y") else "size")):
                for r in roots:
                    b = doc.selection_extent([r])
                    if b is None:
                        continue
                    w0, h0 = b[2] - b[0], b[3] - b[1]
                    X, Y, W, H = self._shown_geometry(b[0], b[1], w0, h0)
                    if which == "x":
                        X = self._each_value(f, value, X)
                    elif which == "y":
                        Y = self._each_value(f, value, Y)
                    elif which == "w":
                        W = self._each_value(f, value, W)
                        if keep and w0:
                            H = max(1, round(h0 * W / w0))
                    else:
                        H = self._each_value(f, value, H)
                        if keep and h0:
                            W = max(1, round(w0 * H / h0))
                    if (X, Y, W, H) == self._shown_geometry(b[0], b[1], w0, h0):
                        continue
                    x, y, _w, _h = self._box_from_fields(X, Y, W, H)
                    sx = W / w0 if w0 and which in ("w", "h") else 1.0
                    sy = H / h0 if h0 and which in ("w", "h") else 1.0
                    if which in ("w", "h") and not (w0 and h0):
                        continue
                    changed |= bool(doc.adjust_position_and_size([r], x, y, sx, sy))
        except Exception as exc:  # noqa: BLE001  # swallow-ok: reported
            self.statusMessage.emit(str(exc))
            return
        if changed:
            self.changed.emit()

    def _multi_swatch(self, colours: list, title: str):
        """A swatch of the colour the objects share, or a hatched one ('Mixed')."""
        distinct = list(dict.fromkeys(colours))
        btn = self._swatch_button(distinct[0] if len(distinct) == 1 else None, title=title)
        if len(distinct) > 1:
            sw = getattr(btn, "swatch", btn)
            sw.setIcon(QtGui.QIcon(hatched_pixmap(sw.iconSize().width(), sw.iconSize().height())))
            sw.setToolTip("Mixed: %d colours. A colour picked here goes on all of them" % len(distinct))
            value = getattr(btn, "value_text", None)
            if value is not None:
                value.setText("Mixed")
        return btn

    def _on_multi_slot_colour(self, indices: list, which: str, btn) -> None:
        """Several objects' fill or line colour: each object's own slot (a pipe's line is
        its colour, as the panel sets one pipe's), one undo step."""
        if self._building or self._doc is None:
            return
        from .. import colour_picker
        from ...format import geometry as G

        got = colour_picker.choose_colour(
            self,
            None,
            title="Fill Colour" if which == "fill" else "Line Colour",
            flash=False,
            transparent=False,
            anchor=btn if isinstance(btn, QtWidgets.QWidget) else None,
            focus_entry=self._take_entry_focus(),
        )
        if got is None:
            return
        c = int(got[0]) & 0xFFFFFF
        doc = self._doc

        def one(i):
            if doc.parsed.page.objects[i].type_code == 0x0B:
                return doc.set_pipe_colour(i, c)
            slot = G.SLOT_FILL if which == "fill" else G.SLOT_LINE
            return doc.set_colour_slot(i, slot, c, label="%s colour" % which)

        self._multi_apply("%s colour" % which, indices, one)
        self._set_swatch(btn, c)

    def _on_multi_foreground(self, indices: list, btn) -> None:
        if self._building or self._doc is None:
            return
        from .. import colour_picker

        got = colour_picker.choose_colour(
            self,
            None,
            title="Text Colour",
            flash=False,
            transparent=False,
            anchor=btn if isinstance(btn, QtWidgets.QWidget) else None,
            focus_entry=self._take_entry_focus(),
        )
        if got is None:
            return
        c = int(got[0]) & 0xFFFFFF
        self._multi_apply("text colour", indices, lambda i: self._doc.set_foreground(i, c))
        self._set_swatch(btn, c)

    def _on_multi_filled(self, indices: list, box: QtWidgets.QCheckBox) -> None:
        """Filled of several shapes: a click on the mixed box fills them all."""
        on = box.checkState() != QtCore.Qt.Unchecked
        box.setTristate(False)
        box.setChecked(on)
        self._multi_apply("fill style", indices, lambda i: self._doc.set_filled(i, on))

    def _on_multi_font(self, texts: list, combo, text: str) -> None:
        if self._building or self._doc is None:
            return
        faces = {self._doc.page.objects[i].style.font or "" for i in texts}
        stored = next(iter(faces)) if len(faces) == 1 else ""
        if not (text or "").strip() and len(faces) > 1:
            return  # '(different values)' left alone
        if self._font_unchanged(combo, text, stored):
            return
        fam = self._resolve_font(combo, text, stored)
        if fam is None:
            return
        todo = [i for i in texts if (self._doc.page.objects[i].style.font or "") != fam]
        if not todo:
            return
        combo.setProperty("orig", fam)
        self._multi_apply("font", todo, lambda i: self._doc.set_font(i, face=fam))

    # the animation fields a multi-selection sets on every object at once:
    # (registry page id, bag key, label, expression?)
    _MULTI_ANIMATION = [
        ("visibility", "VISIBLE", "Hidden when", True),
        ("disable", "DISABLED", "Disable when", True),
        ("access", "COMMENT", "Tool tip", False),
        ("access", "DESC", "Description", False),
        ("access", "AREA", "Area", False),
        ("access", "PRIV", "Privilege level", False),
    ]
    _MULTI_LIMITS = {"VISIBLE": 254, "DISABLED": 254, "COMMENT": 48, "DESC": 128, "AREA": 16, "PRIV": 16}

    def _add_multi_animation(self, lay, roots: list) -> bool:
        """Shared animation settings for several objects."""
        from ..objprops import registry as R

        doc = self._doc
        ctxs = {}
        for r in roots:
            try:
                ctx = R.SheetContext.for_object(doc, r)
            except Exception:  # noqa: BLE001
                _note_exc("properties._add_multi_animation")
                continue
            if not ctx.read_only:
                ctxs[r] = ctx
        drops = getattr(doc, "_genie_root_drops", None)

        def keeps(r, key):
            # a placed genie ROOT keeps only its name / alarm indicator (the
            # Graphics Builder drops the rest on page load): not a target
            try:
                return drops is None or not drops(r, key)
            except Exception:  # noqa: BLE001
                _note_exc("properties._add_multi_animation/keeps")
                return True

        rows = []
        for page_id, key, label, expr in self._MULTI_ANIMATION:
            page = R.PAGE_BY_ID[page_id]
            targets = [r for r, c in ctxs.items() if c.mask & page.bit and keeps(r, key)]
            if len(targets) >= 2:
                rows.append((key, label, expr, targets))
        if not rows:
            return False
        box = self._section("Animation (%d objects)" % len(ctxs))
        form = box.form()
        for key, label, expr, targets in rows:
            vals = {self._multi_shown(key, ctxs[r].props.get(key)) for r in targets}
            same = len(vals) == 1
            ed = self._expr_line("") if expr else QtWidgets.QLineEdit()
            ed.setText(next(iter(vals)) if same else "")
            if not same:
                ed.setPlaceholderText("(different values)")
            ed.setMaxLength(max(self._MULTI_LIMITS.get(key, 254), len(ed.text())))
            ed.setProperty("orig", ed.text())
            ed.setToolTip(
                "Sets this on the %d selected objects that have it. Empty removes it." % len(targets)
            )
            ed.editingFinished.connect(
                lambda e=ed, k=key, lab=label, t=list(targets): self._on_multi_prop(k, lab, t, e)
            )
            form.addRow(label, ed)
            if len(targets) != len(ctxs):
                cover = self._dim_label("%d of %d objects" % (len(targets), len(ctxs)))
                cover.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
                form.setWidget(form.rowCount(), QtWidgets.QFormLayout.FieldRole, cover)
        lay.addWidget(box)
        return True

    def _on_multi_prop(self, key: str, label: str, targets: list, ed) -> None:
        if self._building or self._doc is None:
            return
        text = ed.text()
        if text == (ed.property("orig") or "") and not ed.placeholderText():
            return
        if text == "" and ed.placeholderText() and not ed.isModified():
            return  # '(different values)' left alone
        from ...editing import UnencodableTextError, guard_text

        try:
            guard_text(text)
        except UnencodableTextError as exc:
            self.statusMessage.emit("%s. Nothing was changed." % exc)
            return
        doc = self._doc

        def one(i):
            stored = doc.object_properties(i).get(key)
            if text:
                value = text
                if key == "COMMENT" and (stored or "").endswith(self._USER_MARK):
                    value = text + self._USER_MARK  # CtDraw's marker stays (the sheet's rule)
                if value == stored:
                    return False
                return doc.ensure_property(i, key, value)
            if stored is not None:
                return doc.remove_property(i, key)
            return False

        self._multi_apply(label.lower(), targets, one)
        ed.setProperty("orig", text)
        # the field now holds one value for every target
        ed.setPlaceholderText("")
        ed.setModified(False)

    #: CtDraw's marker on a tool tip the user typed (the object sheet keeps it)
    _USER_MARK = "<User>"

    @classmethod
    def _multi_shown(cls, key: str, value) -> str:
        """What the multi-selection field shows for a stored value."""
        v = value or ""
        if key == "COMMENT" and v.endswith(cls._USER_MARK):
            v = v[: -len(cls._USER_MARK)]
        return v

    @staticmethod
    def _each_value(field, value: int, own: int) -> int:
        """One object's new value from a field of several (MixedField): its own value
        changed as the field's sum, step or drag says, else the field's value."""
        op = field.relative_op() if isinstance(field, MixedField) else None
        return int(op(int(own))) if op is not None else int(value)

    def _multi_apply(self, label: str, indices: list, fn) -> None:
        if self._building or self._doc is None:
            return
        changed = False
        try:
            with self._doc.batch(label):
                for i in indices:
                    changed |= bool(fn(i))
        except Exception as exc:  # noqa: BLE001  # swallow-ok: the message is shown
            self.statusMessage.emit(str(exc))
            return
        if changed:
            self.changed.emit()

    def _on_multi_move(self, axis: str, delta: int, doc=None, chosen=None) -> None:
        """X / Y of a selection of several objects: ``doc`` and ``chosen`` = the document
        and selection the form was built for (default: the panel's now)."""
        doc = self._doc if doc is None else doc
        chosen = list(self._indices if chosen is None else chosen)
        if self._building or doc is None or not delta:
            return
        from ..objprops.registry import genie_member_edits

        members = genie_member_edits(doc, chosen)
        sel = [i for i in chosen if i not in members]
        if not sel:
            self.statusMessage.emit(
                "The selected objects are part of a genie and were "
                "not moved: edit the genie's definition instead."
            )
            return
        idx = doc.expand_to_subtrees(sel)
        try:
            doc.move_objects(idx, delta if axis == "x" else 0, delta if axis == "y" else 0)
        except ValueError as e:  # (TransformError: a position the page cannot store)
            self.statusMessage.emit(str(e))
            self.changed.emit()  # (the fields show the positions as they are)
            return
        self.changed.emit()

    def _on_multi_size(self, which: str, fields: dict, doc=None, chosen=None) -> None:
        """W / H of a selection of several objects: the selection scales as one about its
        reference point (Arrange > Adjust Position and Size's way, one undo step).
        ``doc`` / ``chosen``: as :meth:`_on_multi_move`."""
        doc = self._doc if doc is None else doc
        chosen = list(self._indices if chosen is None else chosen)
        if self._building or doc is None:
            return
        from ..objprops.registry import genie_member_edits

        members = genie_member_edits(doc, chosen)
        sel = [i for i in chosen if i not in members]
        roots = sorted({doc.root_of(i) for i in sel})
        box = doc.selection_extent(roots) if roots else None
        if box is None:
            return
        l, t, r, b = box
        w0, h0 = r - l, b - t
        W, H = fields["w"].value(), fields["h"].value()
        chain = fields.get("chain")
        if chain is not None and chain.isChecked():
            if which == "w" and w0:
                H = max(1, round(h0 * W / w0))
            elif which == "h" and h0:
                W = max(1, round(w0 * H / h0))
        if (W, H) == (w0, h0) or not w0 or not h0:
            return
        X, Y, _w, _h = self._shown_geometry(l, t, w0, h0)
        x, y, _W, _H = self._box_from_fields(X, Y, W, H)
        try:
            ok = doc.adjust_position_and_size(sel, x, y, W / w0, H / h0)
        except Exception as exc:  # noqa: BLE001  # swallow-ok: reported
            self.statusMessage.emit(str(exc))
            return
        if ok:
            self.changed.emit()

    # -- widget factories ---------------------------------------------------------------------
    def _expr_context(self):
        """The document's ExpressionContext (one per document: every field
        of the panel shares its tag lists and completion model)."""
        if self._context is None or self._context_doc is not self._doc:
            from ..objprops.expression import ExpressionContext

            try:
                self._context = ExpressionContext.for_document(self._doc)
            except Exception:  # noqa: BLE001 -- completion is an aid
                _note_exc("properties._expr_context")
                self._context = ExpressionContext(None)
            self._context_doc = self._doc
        return self._context

    def _expr_line(self, value, kind: str = "expr"):
        """An ExpressionField (Wizard, completion of the name under the
        cursor, advice, the Cicode check of the sheet); ``kind`` 'command' for a command."""
        from ..objprops.expression import ExpressionField

        ctx = self._expr_context()
        ed = ExpressionField(ctx, field_kind="command" if kind == "command" else "expr")
        ed.set_names(ctx.completion_model())
        try:
            from ..expression_check import checker_for_document

            ed.set_checker(checker_for_document(self._doc))
        except Exception:  # noqa: BLE001  # swallow-ok: no checker, the field works as before
            pass
        ed.set_value(str(value))
        ed.setProperty("orig", str(value))
        ed.setCursorPosition(0)
        # (narrower than this, it goes under its label: _form's WrapLongRows)
        ed.setMinimumWidth(EXPRESSION_MIN_WIDTH)
        ed.setProperty("expressionRow", True)
        return ed

    def _prop_edit(self, index: int, key: str, value, kind="") -> QtWidgets.QLineEdit:
        """A row editing raw key ``key``: an ExpressionField for 'expr' /
        'command' (True = 'expr'), else a plain line edit."""
        if kind is True:
            kind = "expr"
        ed = self._expr_line(value, kind) if kind in ("expr", "command") else self._line(value)
        ed.setProperty("propKey", key)
        ed.editingFinished.connect(lambda e=ed, i=index, k=key: self._on_object_prop(i, k, e))
        return ed

    def _number(self, lo: int, hi: int, suffix: str, value: int) -> NumberField:
        """A NumberField with the panel's step rule (opt/props_arrow_live)."""
        sp = NumberField(
            lo=lo, hi=hi, suffix=suffix, value=value, live_steps=panel_option("props_arrow_live")
        )
        return sp

    def _scrub(self, text: str, field: NumberField) -> ScrubLabel:
        """A number row's label you drag to change the value (opt/props_scrub)."""
        return ScrubLabel(text, field, enabled=panel_option("props_scrub"))

    def _line(self, value) -> QtWidgets.QLineEdit:
        ed = QtWidgets.QLineEdit(str(value))
        ed.setProperty("orig", str(value))
        ed.setCursorPosition(0)  # a long value shows its start, not its end
        return ed

    def _form(self, box: QtWidgets.QWidget) -> QtWidgets.QFormLayout:
        form = QtWidgets.QFormLayout(box)
        # the docked panel keeps full-width fields (the app style lets only
        # text fields grow in dialogs:)
        if isinstance(self, PropertyPanel):
            self.style_form(form)
        else:
            form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        form.setContentsMargins(_sp(2), _sp(2), _sp(1), _sp(2))
        return form

    def _ro_label(self, text: str) -> QtWidgets.QLabel:
        from ..names import ElidedLabel

        lab = ElidedLabel(str(text))
        lab.setObjectName("dim")
        lab.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        return lab

    def _dim_label(self, text: str, centre: bool = False) -> QtWidgets.QLabel:
        lab = QtWidgets.QLabel(text)
        lab.setObjectName("dim")
        if centre:
            lab.setAlignment(QtCore.Qt.AlignCenter)
        return lab

    def _swatch_button(self, packed, text: str = "", off=None, title: str = "Colour"):
        """A colour swatch (a ColourButton) whose click the panel handles, with its value
        beside it (the palette's name, '#RRGGBB', 'Flashing', 'Transparent': a click on it
        opens the picker with the colour entry ready); ``text`` = a label shown before the
        swatch instead."""
        btn = PanelSwatch(title=title)
        btn.setCursor(QtCore.Qt.PointingHandCursor)
        if not text:
            wrap = ValueSwatch(btn, self)
            self._set_swatch(wrap, packed, off)
            return wrap
        self._set_swatch(btn, packed, off)
        wrap = LabelledSwatch(btn, text)
        return wrap

    def _set_swatch(self, btn, packed, off=None) -> None:
        """Show a colour (a flashing pair when ``off`` differs; None / the
        0xFF high byte = no colour) on a swatch."""
        value = getattr(btn, "value_text", None)
        btn = getattr(btn, "swatch", btn)
        if hasattr(btn, "set_pair"):
            if packed is None:
                btn.set_pair(None)
            else:
                on = int(packed) & 0xFFFFFFFF
                btn.set_pair((on, on if off is None else int(off) & 0xFFFFFFFF))
            btn.setProperty("packed", packed)
            if value is not None:
                from .. import colour_picker

                pair = btn.pair()
                with contextlib.suppress(RuntimeError):
                    value.setText(
                        colour_picker.short_text(pair[0], pair[1], colour_picker.context_dir(self))
                        if pair is not None
                        else ""
                    )
            return
        # a plain push button (callers outside the panel)
        from .. import colour_picker

        off_c = None if off is None else int(off) & 0xFFFFFFFF
        if packed is not None:
            btn.setIcon(QtGui.QIcon(colour_picker.swatch_pixmap(int(packed) & 0xFFFFFFFF, off_c, 36, 14)))
            btn.setIconSize(QtCore.QSize(36, 14))
            btn.setToolTip(colour_picker.describe(int(packed), off_c))
        btn.setProperty("packed", packed)
