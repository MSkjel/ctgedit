"""The object property sheet."""

from __future__ import annotations

from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ...infra import caches as _caches
from . import modules as M
from . import registry as R
from .expression import ExpressionContext, ExpressionField
from .fields import _GREY_STYLES, _SL_CONTINUOUS, _SL_ON, _atoi, _Field, _OptionalSpin, _RadioChoice, _Sub
from .range_field import RangeField
from .scroll import FitTabWidget, ensure_visible, scroll_page, unwrap_page
from ..panels import panel_options as _panels
from ..style.theme import color, set_note_role

#: the page shown last in a sheet of each kind of object (kind -> registry page id): where
#: the next sheet of that kind opens with Settings 'Object Properties opens on: The tab used
#: last' (this session only; emptied for each test module: a sheet a test opened must not
#: choose the next test's tab)
_LAST_PAGE: dict = {}
_caches.register("objprops.sheet._LAST_PAGE", _LAST_PAGE, on=("tests",))

#: the choice widgets (QComboBox calls)
_CHOICES = (QtWidgets.QComboBox, _RadioChoice)

#: the Appearance > General / 3D Effects widgets (appearance.GeneralEditor /
#: EffectsEditor set the ones the object's class has; the rest stay None)
_APPEARANCE_ATTRS = (
    "_sp_width",
    "_cb_lstyle",
    "_chk_filled",
    "_btn_colour",
    "_new_colour",
    "_ed_caption",
    "_sp_radius",
    "_cb_rstyle",
    "_cb_estyle",
    "_sp_astart",
    "_sp_aend",
    "_chk_open",
    "_cb_grad",
    "_cb_effect",
    "_col_line",
    "_col_fill",
    "_col_grad",
    "_col_hilight",
    "_col_lolight",
    "_chk_border",
    "_chk_extra",
    "_cb_font",
    "_cb_fstyle",
    "_sp_fsize",
    "_cb_justify",
    "_chk_strike",
    "_chk_under",
    "_col_fore",
    "_cb_script",
    "_lbl_script_warn",
    "_rb_btext",
    "_rb_bsym",
    "_rb_btarget",
    "_chk_xp",
    "_ed_symbol",
    "_btn_symset",
    "_cb_bmode",
    "_chk_custom",
    "_col_up",
    "_col_down",
    "_rb_effects",
    "_sp_depth",
    "_chk_auto_depth",
    "_col_shadow",
    "_col_hilight3d",
    "_col_lolight3d",
)


def _paint_icon(draw) -> QtGui.QIcon:
    pm = QtGui.QPixmap(14, 14)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    try:
        draw(p)
    finally:
        p.end()
    return QtGui.QIcon(pm)


def widget_state(w):
    """What the user can change in control ``w`` (None: nothing), to tell
    whether a page was touched since it was loaded."""
    try:
        if isinstance(w, QtWidgets.QLineEdit):
            return ("e", w.text())
        if isinstance(w, QtWidgets.QPlainTextEdit):
            return ("p", w.toPlainText())
        if isinstance(w, QtWidgets.QTextEdit):
            return ("x", w.toPlainText())
        if isinstance(w, QtWidgets.QDoubleSpinBox):
            return ("d", w.value())
        if isinstance(w, QtWidgets.QSpinBox):
            return ("s", w.value())
        if isinstance(w, QtWidgets.QAbstractButton):
            return ("b", w.isChecked()) if w.isCheckable() else None
        if isinstance(w, QtWidgets.QComboBox):
            return ("c", w.currentIndex(), w.currentText())
        if isinstance(w, QtWidgets.QTableWidget):
            cells = []
            for r in range(w.rowCount()):
                for c in range(w.columnCount()):
                    it = w.item(r, c)
                    cells.append(None if it is None else (it.text(), it.checkState() == QtCore.Qt.Checked))
            return ("t", w.rowCount(), tuple(cells))
        if isinstance(w, QtWidgets.QListWidget):
            return (
                "l",
                tuple(
                    (w.item(r).text(), w.item(r).checkState() == QtCore.Qt.Checked) for r in range(w.count())
                ),
            )
        for name in ("pair", "point"):
            fn = getattr(w, name, None)
            if callable(fn):
                return (name, fn())
    except RuntimeError, TypeError, AttributeError:
        return None
    return None


def check_icon() -> QtGui.QIcon:
    """CtDraw's 'configured' tab mark."""

    def draw(p):
        pen = QtGui.QPen(color("tip"), 2.2)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        pen.setJoinStyle(QtCore.Qt.RoundJoin)
        p.setPen(pen)
        p.drawPolyline(
            QtGui.QPolygonF([QtCore.QPointF(2.5, 7.5), QtCore.QPointF(5.5, 10.5), QtCore.QPointF(11.5, 3.5)])
        )

    return _paint_icon(draw)


def blocked_icon() -> QtGui.QIcon:
    """A page another configured page disables."""

    def draw(p):
        pen = QtGui.QPen(color("text_dim"), 1.6)
        p.setPen(pen)
        p.drawEllipse(QtCore.QRectF(2, 2, 10, 10))
        p.drawLine(QtCore.QPointF(3.8, 10.2), QtCore.QPointF(10.2, 3.8))

    return _paint_icon(draw)


class ObjectPropertiesDialog(QtWidgets.QDialog):
    """The object property sheet. One undo step per Apply or OK."""

    #: Apply / OK / Convert changed the document (the window refreshes)
    applied = QtCore.Signal()
    #: 'Edit Definition' was clicked (read-only mode)
    editDefinitionRequested = QtCore.Signal()
    # the pages take the extra room both ways
    RESIZE = "both"

    def __init__(self, doc, index: int, parent=None, tag_names=None, read_only=None, definition=None):
        super().__init__(parent)
        self._doc = doc
        self._index = index
        obj = doc.page.objects[index]
        kind = obj.kind.value
        self._kind = kind
        self.changed = False
        self._error = False
        #: the keys the last apply() could not store (reported in the sheet)
        self.failures: list = []
        self._syncing = False
        # key -> its (first) field widget; _fields: key -> [(field, widget)]
        # (a key two properties share -- a slider's offsets -- has two). Only the pages
        # built so far: a page is built when it is first shown (_build_group); code
        # outside the sheet reads _widgets / _editors, which build every page first.
        self._first: dict[str, QtWidgets.QWidget] = {}
        self._fields: dict[str, list] = {}
        #: the place of each field key and editor in the eager build's order: (page
        #: position, creation number): edits are written in that order, whatever order
        #: the pages were built in
        self._field_rank: dict = {}
        self._ed_rank: dict = {}
        self._rank_now = 0
        self._seq = 0
        #: page module -> the placeholders of its pages not built yet (_build_pages)
        self._unbuilt: dict = {}
        #: id(placeholder) -> its page module
        self._slot_module: dict = {}
        self._building_pages = False
        self._planning = False
        self._initial: dict[str, object] = {}
        self._cleared: list = []
        self._locked_keys: set = set()
        self._comment_marker = False
        #: keys whose stored value carried CtDraw's '<User>' marker (kept on write)
        self._user_markers: set = set()
        self._props = dict(doc.object_properties(index))
        for attr in _APPEARANCE_ATTRS:
            setattr(self, attr, None)
        self._input_tab = None
        self._eds: list = []
        #: the built pages: (registry.Page, the tab widget holding it, its index)
        self._pages: list = []
        #: id(page editor) -> the page widget its builder returned
        self._editor_pages: dict = {}
        #: id(page widget) -> the state of its controls when last loaded
        self._snapshots: dict = {}
        self.context = R.SheetContext.for_object(doc, index)
        self._set_title()
        if read_only is None:
            reason = self.context.read_only
        elif read_only is True:
            reason = self.context.read_only or "These properties are shown read-only."
        else:
            reason = str(read_only or "")
        self.read_only_reason = reason
        # a genie MEMBER's read-only sheet still edits its Access > General
        # (CtDraw 8.50's member sheet) -- unless the caller asked for all read-only
        editable = R.member_editable_keys(doc, index) if reason and read_only is not True else frozenset()
        #: upper-case keys a read-only sheet still edits (a genie member's Access > General)
        self._editable_keys = frozenset(k.upper() for k in editable)
        self._partial = bool(reason and self._editable_keys)
        self._definition = definition
        self.expression_context = ExpressionContext.for_document(doc, tag_names or ())

        lay = QtWidgets.QVBoxLayout(self)
        self._ro_box = self._build_read_only_banner()
        lay.addWidget(self._ro_box)
        self._tabs = FitTabWidget(self)
        lay.addWidget(self._tabs, 1)
        self._build_pages()

        self._status = QtWidgets.QLabel()
        self._status.setWordWrap(True)
        set_note_role(self._status, "danger")
        self._status.hide()
        lay.addWidget(self._status)
        if self.read_only_reason and not self._partial:
            buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        else:
            buttons = QtWidgets.QDialogButtonBox(
                QtWidgets.QDialogButtonBox.Ok
                | QtWidgets.QDialogButtonBox.Cancel
                | QtWidgets.QDialogButtonBox.Apply
            )
            buttons.accepted.connect(self._ok)
            buttons.button(QtWidgets.QDialogButtonBox.Apply).clicked.connect(self.apply)
        buttons.rejected.connect(self.reject)
        self._buttons = buttons
        lay.addWidget(buttons)
        self._load_values()
        self._disable_class_fields()
        self._disable_genie_root_drops()
        if self.read_only_reason:
            self._apply_read_only()
        for ed in self._eds:
            fn = getattr(ed, "enable_state", None)
            if fn is not None:
                fn()
        self._refresh_marks()
        self._take_snapshots()
        if _panels.OPEN_TAB == "last":
            # (Settings > Editing > Objects: the page a sheet of this kind showed last)
            last = _LAST_PAGE.get(self._kind)
            if last and last != self.current_page_id() and last in self.page_ids():
                self.show_page(last)
        # the keyboard focus on the first field, not on OK (else Enter after a
        # double-click on an object would close the sheet)
        QtCore.QTimer.singleShot(0, self, self._focus_first_field)

    def _focus_first_field(self) -> None:
        """The focus on the first enabled field of the page shown."""
        try:
            page = self._tabs.currentWidget()
            inner = page.currentWidget() if isinstance(page, QtWidgets.QTabWidget) else page
        except AttributeError, RuntimeError:
            return
        if inner is None:
            return
        for w in inner.findChildren(QtWidgets.QWidget):
            if isinstance(
                w,
                (
                    QtWidgets.QPushButton,
                    QtWidgets.QToolButton,
                    QtWidgets.QTabBar,
                    QtWidgets.QScrollBar,
                    QtWidgets.QScrollArea,
                    QtWidgets.QLabel,
                ),
            ):
                continue
            if w.isEnabled() and w.isVisibleTo(inner) and w.focusPolicy() & QtCore.Qt.TabFocus:
                w.setFocus(QtCore.Qt.OtherFocusReason)
                return

    def _check_live(self) -> None:
        """What OK would refuse, said at once and OK / Apply disabled meanwhile (UI guide
        section 3)."""
        self._live_check_queued = False
        buttons = getattr(self, "_buttons", None)
        if buttons is None or (self.read_only_reason and not self._partial):
            return
        try:
            _edits, problem = self._collect()
        except Exception:  # noqa: BLE001  # swallow-ok: the check at OK says it
            problem = None
        ok = buttons.button(QtWidgets.QDialogButtonBox.Ok)
        apply = buttons.button(QtWidgets.QDialogButtonBox.Apply)
        for b in (ok, apply):
            if b is not None:
                b.setEnabled(problem is None)
        if problem is not None:
            self._status.setText(problem[0])
            self._status.show()
            self._error = True
            self._mark_field(problem[1] if len(problem) > 1 else None, problem[0])
        elif self._error:
            self._error = False
            self._status.hide()
            self._mark_field(None, "")

    def _mark_field(self, widget, message: str) -> None:
        """Frame the field of the input problem in the danger colour (theme.mark_invalid,
        the message its tool tip); the field framed before is cleared."""
        from ..style.theme import mark_invalid

        old = getattr(self, "_marked", None)
        if old is not None and old is not widget:
            mark_invalid(old, "")
        self._marked = None
        if isinstance(widget, QtWidgets.QWidget) and message:
            mark_invalid(widget, message)
            self._marked = widget

    def _set_title(self) -> None:
        try:
            an = self._doc.page.objects[self._index].an
        except AttributeError, IndexError:
            an = -1
        title = "%s Properties" % R.sheet_title(self.context, self._kind)
        self.setWindowTitle("%s (AN %d)" % (title, an) if an is not None and an >= 0 else title)

    # -- the object's pages (registry) ------------------------------------------------
    def _note_editors(self, first: int, page: QtWidgets.QWidget) -> None:
        """The page editors added since ``first`` belong to ``page``."""
        for ed in self._eds[first:]:
            self._editor_pages.setdefault(id(ed), page)

    def _build_pages(self) -> None:
        """Add every page as a placeholder tab and build the shown one now."""
        self._planning = True
        try:
            for tab, pages in R.tabs_for(self.context):
                tb, tmod = None, None
                for mod in dict.fromkeys(p.module for p in pages):
                    tb = M.tab_builder(mod, tab)
                    if tb is not None:
                        tmod = mod
                        break
                if tb is not None:
                    # a module that builds the whole outer tab (Input): a placeholder tab
                    # with the inner tabs it will have, replaced when it is built
                    holder = FitTabWidget()
                    rank = len(self._pages)
                    for p in pages:
                        k = holder.addTab(QtWidgets.QWidget(), p.sub)
                        self._pages.append((p, holder, k))
                    self._tabs.addTab(holder, tab)
                    self._plan(tmod, ("tab", holder, tab, pages, tb, rank))
                    continue
                inner = FitTabWidget()
                for p in pages:
                    build = M.page_builder(p)
                    if build is None:
                        continue  # the page's module does not build it yet
                    # a smaller window scrolls the page instead of squeezing it
                    holder = scroll_page(QtWidgets.QWidget())
                    k = inner.addTab(holder, p.sub)
                    self._plan(p.module, ("page", holder, p, build, len(self._pages)))
                    self._pages.append((p, inner, k))
                if inner.count():
                    self._tabs.addTab(inner, tab)
                    inner.currentChanged.connect(self._on_page_shown)
        finally:
            self._planning = False
        self._tabs.currentChanged.connect(self._on_page_shown)
        self._build_shown()
        if not self._tabs.count():
            pages = R.pages_for(self.context)
            if pages:
                text = (
                    "ctgedit cannot edit this object's pages yet (%s). Its settings "
                    "are listed in the Properties panel."
                    % ", ".join("%s > %s" % (p.tab, p.sub) for p in pages)
                )
            else:
                text = (
                    "This kind of object has no property pages. Its settings are "
                    "listed in the Properties panel."
                )
            note = QtWidgets.QLabel(text)
            note.setWordWrap(True)
            note.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
            self._tabs.addTab(note, "General")

    _ROOT_DROP_WHY = (
        "A placed genie keeps only its name and alarm indicator. Set this in "
        "the genie definition (Edit Definition)."
    )

    def key_blocked(self, key: str) -> str:
        """Why ``key`` can't be edited here ('' if it can)."""
        why = R.DISABLED_FIELDS.get(self.context.type_code, {}).get(key)
        if why:
            return why
        drops = getattr(self._doc, "_genie_root_drops", None)
        if drops is not None:
            try:
                if drops(self._index, key):
                    return self._ROOT_DROP_WHY
            except Exception:  # noqa: BLE001 -- a hint only
                pass
        if self.read_only_reason and key.upper() not in self._editable_keys:
            return self.read_only_reason
        return ""

    def _disable_class_fields(self, fields: Optional[dict] = None) -> None:
        """The fields the object's class greys (registry.DISABLED_FIELDS)."""
        fields = self._fields if fields is None else fields
        for key, why in R.DISABLED_FIELDS.get(self.context.type_code, {}).items():
            for _f, w in fields.get(key, []):
                w.setEnabled(False)
                w.setToolTip(why)

    def _disable_genie_root_drops(self, fields: Optional[dict] = None) -> None:
        """A placed genie's ROOT keeps only its name and alarm indicator: the
        Graphics Builder re-composes the instance on page load and drops every
        other own setting (editing._genie_root_drops), so those fields are
        greyed instead of failing on Apply."""
        drops = getattr(self._doc, "_genie_root_drops", None)
        if drops is None:
            return
        for key, entries in (self._fields if fields is None else fields).items():
            try:
                dropped = bool(drops(self._index, key))
            except Exception:  # noqa: BLE001 -- a hint only
                dropped = False
            if dropped:
                for _f, w in entries:
                    w.setEnabled(False)
                    w.setToolTip(self._ROOT_DROP_WHY)

    def page_ids(self) -> list:
        """The ids of the pages this sheet shows, in order."""
        return [p.id for p, _w, _k in self._pages]

    def page_widget(self, page_id: str):
        """The page itself (not the scroll area it sits in), built if it was not yet."""
        self._build_page_id(page_id)
        for p, w, k in self._pages:
            if p.id == page_id:
                return unwrap_page(w.widget(k))
        return None

    def show_page(self, page_id: str) -> bool:
        """Bring a page (and its outer tab) to the front."""
        self._build_page_id(page_id)
        for p, w, k in self._pages:
            if p.id == page_id:
                self._tabs.setCurrentWidget(w)  # the page's outer tab
                w.setCurrentIndex(k)
                QtCore.QTimer.singleShot(0, self, self._focus_first_field)
                QtCore.QTimer.singleShot(0, self, self.fit_page_width)
                return True
        return False

    def fit_page_width(self) -> bool:
        """The sheet as wide as the page shown needs (its fields side by side, no sideways
        scrolling), within the screen; it never gets narrower here. True when it grew."""
        from .scroll import PageScroll, screen_size

        try:
            outer = self._tabs.currentWidget()
            inner = outer.currentWidget() if isinstance(outer, QtWidgets.QTabWidget) else outer
        except AttributeError, RuntimeError:
            return False
        if not isinstance(inner, PageScroll) or inner.widget() is None:
            return False
        need = (
            inner.widget().minimumSizeHint().width()
            + inner.verticalScrollBar().sizeHint().width()
            + 2 * inner.frameWidth()
        )
        extra = need - inner.viewport().width()
        if extra <= 0:
            return False
        width = min(self.width() + extra, screen_size(self).width())
        if width <= self.width():
            return False
        self.setMinimumWidth(max(self.minimumWidth(), min(width, self.minimumWidth() + extra)))
        self.resize(width, self.height())
        return True

    def add_editor(self, editor) -> None:
        """Register a custom page's editor (see the module doc)."""
        self._eds.append(editor)
        self._seq += 1
        self._ed_rank[id(editor)] = (self._rank_now, self._seq)

    # -- pages built when first shown -------------------------------------------------
    #: page modules whose pages are built together with another module's: the Display
    #: Value page owns the caption field of Appearance > General (display_value.py)
    _PAGE_GROUPS = {"display_value": "appearance"}

    def _plan(self, module: str, slot: tuple) -> None:
        group = self._PAGE_GROUPS.get(module, module)
        self._unbuilt.setdefault(group, []).append(slot)
        self._slot_module[id(slot[1])] = group

    @staticmethod
    def _slot_pages(slot) -> tuple:
        return tuple(slot[3]) if slot[0] == "tab" else (slot[2],)

    def is_page_built(self, page_id: str) -> bool:
        """Whether the page was built (shown, or needed for an Apply)."""
        for slots in self._unbuilt.values():
            for slot in slots:
                if any(p.id == page_id for p in self._slot_pages(slot)):
                    return False
        return any(p.id == page_id for p, _w, _k in self._pages)

    def _build_page_id(self, page_id: str) -> None:
        for group, slots in list(self._unbuilt.items()):
            for slot in slots:
                if any(p.id == page_id for p in self._slot_pages(slot)):
                    self._build_group(group)
                    return

    def _on_page_shown(self, *_a) -> None:
        if not (self._planning or self._building_pages):
            self._build_shown()
            pid = self.current_page_id()
            if pid:
                _LAST_PAGE[self._kind] = pid

    def current_page_id(self) -> str:
        """The registry id of the page shown ('' = none: an object without pages)."""
        outer = self._tabs.currentWidget()
        for p, w, k in self._pages:
            if w is outer and w.currentIndex() == k:
                return p.id
        return ""

    def _build_shown(self) -> None:
        """Build the page shown (its outer tab's current inner page)."""
        w = self._tabs.currentWidget()
        module = self._slot_module.get(id(w)) if w is not None else None
        if module is None and isinstance(w, QtWidgets.QTabWidget):
            inner = w.currentWidget()
            module = self._slot_module.get(id(inner)) if inner is not None else None
        if module is not None and module in self._unbuilt:
            self._build_group(module)

    def build_all_pages(self) -> None:
        """Build every page not built yet (the code outside the sheet that reads its
        fields and editors gets them all)."""
        if self._building_pages or self._planning:
            return
        for module in list(self._unbuilt):
            self._build_group(module)

    def _build_for_apply(self) -> None:
        """Build the unshown pages that can still matter to Apply."""
        for group, slots in list(self._unbuilt.items()):
            modules = {p.module for slot in slots for p in self._slot_pages(slot)}
            if not all(getattr(M.load(m), "INERT_UNTOUCHED", False) for m in modules):
                self._build_group(group)

    def _build_group(self, group: str) -> None:
        """Build the pages of ``group`` (a page module, or the modules _PAGE_GROUPS puts
        together) not built yet, in page order, and load them as the open loads the first
        page (fields, read-only, marks, snapshots)."""
        slots = self._unbuilt.pop(group, None)
        if not slots or self._planning:
            if slots:
                self._unbuilt[group] = slots
            return
        had = {k: len(v) for k, v in self._fields.items()}
        first_ed = len(self._eds)
        pages = []
        self._building_pages = True
        try:
            for slot in slots:
                self._slot_module.pop(id(slot[1]), None)
                w = self._build_slot(slot)
                if w is not None:
                    pages.append(w)
        finally:
            self._building_pages = False
        new_eds = self._eds[first_ed:]
        # the eager build's order: editors apply, and keys are written, in page order
        self._eds.sort(key=lambda ed: self._ed_rank.get(id(ed), (1 << 30, 0)))
        self._fields = dict(
            sorted(self._fields.items(), key=lambda kv: self._field_rank.get(kv[0], (1 << 30, 0)))
        )
        new = {k: v[had.get(k, 0) :] for k, v in self._fields.items() if len(v) > had.get(k, 0)}
        if not self.__dict__.get("_loaded"):
            return  # (the open's first page: __init__ loads it)
        self._load_new(new, had, new_eds, pages)

    def _build_slot(self, slot):
        """Build one placeholder's page (or whole outer tab); the page widget, or None."""
        if slot[0] == "page":
            _kind, holder, p, build, rank = slot
            self._rank_now = rank
            first = len(self._eds)
            w = build(self, p)
            if w is None:
                return None
            self._note_editors(first, w)
            holder.setWidget(w)  # (the empty placeholder page goes)
            return w
        _kind, holder, title, pages, tb, rank = slot
        self._rank_now = rank
        first = len(self._eds)
        w = tb(self, pages)
        if w is None:
            return None
        self._note_editors(first, w)
        k = self._tabs.indexOf(holder)
        current = self._tabs.currentIndex() == k
        self._tabs.blockSignals(True)
        try:
            self._tabs.removeTab(k)
            self._tabs.insertTab(k, w, title)
            if current:
                self._tabs.setCurrentIndex(k)
        finally:
            self._tabs.blockSignals(False)
        if isinstance(w, QtWidgets.QTabWidget):
            # (its inner pages count as the page shown too: Input > Keyboard Commands is
            # where the next sheet opens with Settings 'The tab used last')
            w.currentChanged.connect(self._on_page_shown)
        # (without a parent: a widget left to deleteLater under a parent PySide deleted
        # first crashes the next flush of the deferred deletes)
        holder.hide()
        holder.setParent(None)
        holder.deleteLater()
        ids = {p.id for p in pages}
        rows = [(p, w, next((i for i in range(w.count()) if w.tabText(i) == p.sub), -1)) for p in pages]
        out, done = [], False
        for entry in self._pages:
            if entry[0].id in ids:
                if not done:
                    out.extend(r for r in rows if r[2] >= 0)
                    done = True
                continue
            out.append(entry)
        self._pages = out
        return w

    def _load_new(self, new: dict, had: dict, editors: list, pages: list) -> None:
        """What the open does for its first page, for pages built later: the fields show
        what is stored (a key that other built pages show too follows them: a slider's
        offset typed on the movement page), the class's and a genie root's greyed
        fields, read-only, the editors' own state, the marks and the snapshots."""
        self._syncing = True
        try:
            for key, entries in new.items():
                old = self._fields[key][: had.get(key, 0)]
                for f, w in entries:
                    if old:
                        self._follow(w, old[0][1])
                    else:
                        self._set_widget(f, w, self._stored_display(f))
        finally:
            self._syncing = False
        for key, entries in new.items():
            if had.get(key, 0):
                continue
            self._initial[key] = self._state(key)
            if any(getattr(w, "locked", False) for _f, w in entries):
                self._locked_keys.add(key)
        for ed in editors:
            fn = getattr(ed, "fields_loaded", None)
            if fn is not None:
                fn()
        self._disable_class_fields(new)
        self._disable_genie_root_drops(new)
        if self.read_only_reason:
            self._apply_read_only(new, editors, pages)
        for ed in editors:
            fn = getattr(ed, "enable_state", None)
            if fn is not None:
                fn()
        self._update_slider_boxes()
        owners = {id(w) for w in self._field_owners()}
        for page in {
            id(self._editor_pages.get(id(ed))): self._editor_pages.get(id(ed)) for ed in editors
        }.values():
            if page is None:
                continue
            try:
                self._snapshots[id(page)] = self._page_state(page, owners)
            except RuntimeError:
                pass
        self._snapshot_owners = self._field_owners()
        self._refresh_marks(self._pending_props())

    @staticmethod
    def _follow(w, src) -> None:
        """Field ``w`` shows what ``src`` (a field of the same key) shows."""
        if isinstance(w, QtWidgets.QSpinBox):
            w.setValue(src.value())
        elif isinstance(w, QtWidgets.QCheckBox):
            w.setChecked(src.isChecked())
        elif isinstance(w, _CHOICES):
            w.setCurrentIndex(src.currentIndex())
        else:
            w.setText(src.text())

    # -- the pages' fields and editors, for code outside the sheet -------------------------
    @property
    def _widgets(self) -> dict:
        """key -> [(field, widget)] of every page (the pages not built yet are built)."""
        self.build_all_pages()
        return self._fields

    @property
    def _editors(self) -> dict:
        """key -> its (first) field widget, of every page."""
        self.build_all_pages()
        return self._first

    @property
    def _page_editors(self) -> list:
        """The editors of every page."""
        self.build_all_pages()
        return self._eds

    @property
    def _input(self):
        """The Input tab (its touch and keyboard commands), built if it was not yet."""
        if not (self._building_pages or self._planning):
            self._build_group("input")
        return self._input_tab

    def __getattr__(self, name: str):
        """What page builders set on the sheet besides fields ('<page>_editor',
        activex_info): every page is built first."""
        d = self.__dict__
        if (
            (name.endswith("_editor") or name == "activex_info")
            and d.get("_unbuilt")
            and not (d.get("_building_pages") or d.get("_planning"))
        ):
            self.build_all_pages()
            if name in d:
                return d[name]
        raise AttributeError(name)

    @_input.setter
    def _input(self, tab) -> None:
        self._input_tab = tab

    def field_entries(self, key: str) -> list:
        """[(field, widget)] of ``key`` on the pages built so far (page modules)."""
        return self._fields.get(key, [])

    def field_widget(self, key: str):
        """The first field of ``key`` on the pages built so far, or None."""
        return self._first.get(key)

    def built_editors(self) -> list:
        """The editors of the pages built so far (page modules)."""
        return self._eds

    # -- builders ------------------------------------------------------------------
    def _int_ok(self, f: _Field) -> bool:
        """A stored value a spin box can show as it is (else a text field keeps
        it verbatim: a genie '%param%', a padded number)."""
        import re

        v = self._props.get(f.key)
        return v is None or (re.fullmatch(r"-?\d+", v) is not None and f.lo <= int(v) <= f.hi)

    def make_field(self, f: _Field) -> QtWidgets.QWidget:
        """The widget of field ``f``, bound to its key."""
        kind = f.kind
        if kind == "int" and not self._int_ok(f):
            kind = "text"
        if kind == "int":
            w = _OptionalSpin(f.lo, f.hi, f.default, f.suffix)
            w.valueChanged.connect(lambda _v, k=f.key, s=w: self._on_edit(k, s))
        elif kind in ("check", "slmode"):
            w = QtWidgets.QCheckBox(f.label)
            w.toggled.connect(lambda _v, k=f.key, s=w: self._on_edit(k, s))
        elif kind in ("choice", "radio"):
            w = QtWidgets.QComboBox() if kind == "choice" else _RadioChoice()
            w.addItem(_OptionalSpin.NOT_SET, None)
            for label, value in f.choices:
                w.addItem(label, value)
            w.currentIndexChanged.connect(lambda _v, k=f.key, s=w: self._on_edit(k, s))
        elif kind == "expr":
            w = ExpressionField(self.expression_context, wizard=f.wizard)
            w.set_names(self.expression_context.completion_model())
            try:
                from ..expression_check import checker_for_document

                w.set_checker(checker_for_document(self._doc))
            except Exception:  # noqa: BLE001  # swallow-ok: no checker, the field works as before
                pass
            if f.range:
                w.attach_range(RangeField())
            w.valueChanged.connect(lambda _v, k=f.key, s=w: self._on_edit(k, s))
        else:
            w = QtWidgets.QLineEdit()
            w.textChanged.connect(lambda _v, k=f.key, s=w: self._on_edit(k, s))
        self._fields.setdefault(f.key, []).append((f, w))
        self._first.setdefault(f.key, w)
        self._seq += 1
        rank = (self._rank_now, self._seq)
        self._field_rank[f.key] = min(self._field_rank.get(f.key, rank), rank)
        return w

    _make_field = make_field

    def build_sub(self, sub: _Sub, extra=None) -> QtWidgets.QWidget:
        """A declarative property's page: its fields and Clear Property.
        ``extra(form)`` adds a page module's own rows before the button."""
        w = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(w)
        if sub.note:
            note = QtWidgets.QLabel(sub.note)
            note.setWordWrap(True)
            form.addRow(note)
        for f in sub.fields:
            widget = self.make_field(f)
            if isinstance(widget, QtWidgets.QCheckBox):
                form.addRow("", widget)
            else:
                form.addRow(f.label, widget)
            rf = getattr(widget, "range_field", None)
            if rf is not None:
                form.addRow("", rf)
        if extra is not None:
            extra(form)
        if sub.fields:
            clear = QtWidgets.QPushButton("Clear Property")
            clear.setToolTip("Clear the settings of this property")
            clear.clicked.connect(lambda _=False, s=sub: self._clear(s))
            form.addRow("", clear)
        return w

    _build_sub = build_sub

    # -- values ---------------------------------------------------------------
    def _stored_display(self, f: _Field):
        """What field ``f`` shows for the object as it is stored."""
        v = self._props.get(f.key)
        if f.kind == "name":
            return self._doc.object_name(self._index)
        from ...editing import USER_MARKED_KEYS

        if f.key.upper() in USER_MARKED_KEYS:
            # CtDraw's Access page shows a genie member's 'value<User>' without
            # the marker -- and keeps it
            marked = bool(v) and v.endswith("<User>")
            if marked:
                self._user_markers.add(f.key)
                v = v[: -len("<User>")]
            else:
                self._user_markers.discard(f.key)
            if f.kind == "tooltip":
                self._comment_marker = marked
                return v or ""
            return v
        if f.kind == "slmode":
            # a stored mode keeps its continuous bit (4096 on the axes a slider
            # is off); an ABSENT key reads unticked: CtDraw's sheet shows a new
            # object's 'Continuous update of tag' unticked and a tag typed there
            # is saved with *SLMODE '1'
            return False if v is None else bool(_atoi(v) & _SL_CONTINUOUS)
        if f.kind == "check":
            if v is not None and getattr(f, "loose", False):
                return v.strip() != ""
            return v is not None and v.strip().upper() == f.on.upper()
        return v

    def _set_widget(self, f: _Field, w, value) -> None:
        if isinstance(w, _OptionalSpin):
            w.set_key_value(value)
        elif isinstance(w, QtWidgets.QCheckBox):
            w.setChecked(bool(value))
        elif isinstance(w, _CHOICES):
            if value is None:
                w.setCurrentIndex(0)
                return
            want = value.strip().upper()
            if f.key == "GREY_STYLE":
                # CtDraw's Disable page: GRAY_ALL shows as Greyed, any other
                # value it does not name as the first style, Embossed
                want = "GRAY_PART" if want == "GRAY_ALL" else want
                if want not in {v.upper() for _l, v in _GREY_STYLES}:
                    want = _GREY_STYLES[0][1]
            for n in range(1, w.count()):
                if str(w.itemData(n) or "").upper() == want:
                    w.setCurrentIndex(n)
                    return
            w.addItem(value, value)  # a value the sheet does not name: kept as it is
            w.setCurrentIndex(w.count() - 1)
        else:
            text = "" if value is None else str(value)
            # never truncate a stored value that is longer than the limit
            w.setMaxLength(max(f.limit, len(text)) if f.limit else 32767)
            w.setText(text)

    def _state(self, key: str):
        """Comparable state of the (first) field of ``key``."""
        _f, w = self._fields[key][0]
        if isinstance(w, QtWidgets.QSpinBox):
            return w.value()
        if isinstance(w, QtWidgets.QCheckBox):
            return w.isChecked()
        if isinstance(w, _CHOICES):
            return w.currentIndex()
        return w.text()

    def _value(self, f: _Field, w):
        """The value field ``f`` stores (None = the key removed)."""
        if isinstance(w, _OptionalSpin):
            return w.key_value()
        if isinstance(w, QtWidgets.QCheckBox):
            return f.on if w.isChecked() else f.off
        if isinstance(w, _CHOICES):
            return w.currentData()
        text = w.text()
        if f.kind == "name":
            return text
        if text == "":
            return None
        if f.key in self._user_markers or (f.kind == "tooltip" and self._comment_marker):
            return text + "<User>"  # CtDraw's marker on this value stays
        # (a genie member's new Access value gets the marker in EditableDocument)
        return text

    def _load_values(self) -> None:
        """Show the object's stored settings and remember them as the baseline
        Apply compares against."""
        self._props = dict(self._doc.object_properties(self._index))
        self._syncing = True
        try:
            for entries in self._fields.values():
                for f, w in entries:
                    self._set_widget(f, w, self._stored_display(f))
        finally:
            self._syncing = False
        self._update_slider_boxes()
        self._initial = {k: self._state(k) for k in self._fields}
        # keys whose spin box could not show the stored value (Clear removes them)
        self._locked_keys = {
            k for k, entries in self._fields.items() if any(getattr(w, "locked", False) for _f, w in entries)
        }
        self._cleared = []
        # page editors whose own controls follow the loaded fields (Access >
        # General's 'Same area as page' / 'No privilege restrictions')
        for ed in self._eds:
            fn = getattr(ed, "fields_loaded", None)
            if fn is not None:
                fn()
        self._loaded = True

    def _on_edit(self, key: str, src) -> None:
        """Keep the fields of a shared key in step (a slider's offsets are the
        movement's) and the continuous boxes enabled only with a slider tag."""
        if self._syncing:
            return
        if not getattr(self, "_live_check_queued", False):
            self._live_check_queued = True
            QtCore.QTimer.singleShot(0, self, self._check_live)
        self._syncing = True
        try:
            for _f, w in self._fields.get(key, []):
                if w is src:
                    continue
                if isinstance(w, QtWidgets.QSpinBox):
                    w.setValue(src.value())
                elif isinstance(w, QtWidgets.QCheckBox):
                    w.setChecked(src.isChecked())
                elif isinstance(w, _CHOICES):
                    w.setCurrentIndex(src.currentIndex())
                else:
                    w.setText(src.text())
        finally:
            self._syncing = False
        self._update_slider_boxes()
        # CtDraw re-marks the tabs as a property is typed: a slider
        # typed greys its movement at once
        self._refresh_marks(self._pending_props())

    def _update_slider_boxes(self) -> None:
        for entries in self._fields.values():
            for f, w in entries:
                if f.kind == "slmode":
                    tag = self._first.get(f.tag)
                    w.setEnabled(bool(tag is not None and tag.text().strip()) and not self.read_only_reason)

    # -- check marks / disabled pages ------------------------------------------------
    def configured_pages(self) -> set:
        return R.configured_pages(self.context, self._props)

    def _pending_props(self) -> dict:
        """The object's keys as an Apply would leave them (the fields' and
        page editors' unapplied changes over the stored keys) -- what the
        check marks and the excluded pages follow while the user types."""
        props = dict(self._props)
        for key, value in self._collect_edits():
            if value is None:
                props.pop(key, None)
            else:
                props[key] = value
        for ed in self._eds:
            fn = getattr(ed, "pending_props", None)  # pages that write on their own
            if fn is not None:
                fn(props)
        return props

    def _refresh_marks(self, props: Optional[dict] = None) -> None:
        """Mark the configured pages and their outer tabs (CtDraw's check
        marks) and disable the pages a configured one excludes -- for the
        stored keys, or ``props`` (the pending ones)."""
        self.context.props = dict(self._props if props is None else props)
        configured = R.configured_pages(self.context, self.context.props)
        disabled = R.disabled_pages(configured, self.context.type_code)
        marked = set()
        check = check_icon()
        for p, tw, k in self._pages:
            page = unwrap_page(tw.widget(k))  # greyed, its scroll bars still work
            cause = disabled.get(p.id)
            if cause:
                tw.setTabIcon(k, blocked_icon())
                tw.setTabToolTip(
                    k,
                    cause[1:]
                    if cause.startswith("!")
                    else "Not available while %s is configured. Clear that "
                    "property first." % R.page_title(cause),
                )
                page.setEnabled(False)
            else:
                on = p.id in configured
                tw.setTabIcon(k, check if on else QtGui.QIcon())
                tw.setTabToolTip(k, "Configured" if on else "")
                page.setEnabled(True)
                if on:
                    marked.add(id(tw))
        for i in range(self._tabs.count()):
            on = id(self._tabs.widget(i)) in marked
            self._tabs.setTabIcon(i, check if on else QtGui.QIcon())
            self._tabs.setTabToolTip(i, "Configured" if on else "")

    def tab_marked(self, title: str, sub: Optional[str] = None) -> bool:
        """Whether a tab carries the 'configured' mark (tests / scripting)."""
        for i in range(self._tabs.count()):
            if self._tabs.tabText(i) != title:
                continue
            if sub is None:
                return self._tabs.tabToolTip(i) == "Configured"
            w = self._tabs.widget(i)
            if isinstance(w, QtWidgets.QTabWidget):
                for k in range(w.count()):
                    if w.tabText(k) == sub:
                        return w.tabToolTip(k) == "Configured"
        return False

    # -- Clear Property -------------------------------------------------------
    def _expand(self, family) -> list:
        """A family's keys, 'NAME_*' patterns matched against the stored keys."""
        out = []
        for key in family:
            if key.endswith("*"):
                pre = key[:-1]
                out.extend(k for k in self._props if k.startswith(pre) and k[len(pre) :].isdigit())
            else:
                out.append(key)
        return out

    def _shared_in_use(self, key: str, sub: _Sub) -> bool:
        """``key`` also belongs to another property that is switched on (its
        primary field is filled in): Clear of ``sub`` must keep it."""
        for other in M.all_subs():
            if other is sub or other.primary is None or other in self._cleared:
                continue
            if key not in other.family and key not in {f.key for f in other.fields}:
                continue
            w = self._first.get(other.primary)
            if isinstance(w, QtWidgets.QLineEdit) and w.text().strip():
                return True
        return False

    def _clear(self, sub: _Sub) -> None:
        """Clear Property: blank the sub-tab's fields and, on Apply, remove
        every key of the property -- the fields' and the ones without a field
        (ROTMODE / ROTXORG, SZXORG, the COL_STA_n states...)."""
        # (which keys another switched-on property keeps, and which fields decide their
        # own keys, are known only with every page built)
        self.build_all_pages()
        for f in sub.fields:
            if self._shared_in_use(f.key, sub):
                continue
            for ff, w in self._fields.get(f.key, []):
                if ff.kind == "slmode":
                    w.setChecked(True)
                else:
                    self._set_widget(ff, w, None)
        if sub not in self._cleared:
            self._cleared.append(sub)
        for ed in self._eds:
            fn = getattr(ed, "on_clear", None)
            if fn is not None:
                fn(sub)
        self._update_slider_boxes()
        self._refresh_marks(self._pending_props())

    # -- read-only ------------------------------------------------------------------
    def _build_read_only_banner(self) -> QtWidgets.QWidget:
        box = QtWidgets.QFrame()
        box.setFrameShape(QtWidgets.QFrame.StyledPanel)
        h = QtWidgets.QHBoxLayout(box)
        lab = QtWidgets.QLabel(self.read_only_reason)
        lab.setWordWrap(True)
        h.addWidget(lab, 1)
        self._btn_definition = QtWidgets.QPushButton("Edit Definition")
        self._btn_definition.setToolTip("Open the genie or template this object comes from")
        self._btn_definition.clicked.connect(self._edit_definition)
        self._btn_definition.setVisible(self._definition is not None)
        h.addWidget(self._btn_definition, 0, QtCore.Qt.AlignTop)
        box.setVisible(bool(self.read_only_reason))
        return box

    def _edit_definition(self) -> None:
        self.editDefinitionRequested.emit()

    def is_read_only(self) -> bool:
        return bool(self.read_only_reason)

    def is_partly_editable(self) -> bool:
        """A read-only sheet that still edits some keys (a genie member's
        Access > General)."""
        return self._partial

    def _apply_read_only(self, fields: Optional[dict] = None, editors=None, pages=None) -> None:
        """Every field and editor read-only (but a genie member's Access > General);
        ``fields`` / ``editors`` / ``pages``: only those (pages built later)."""
        for key, entries in (self._fields if fields is None else fields).items():
            if key.upper() in self._editable_keys:
                continue
            for _f, w in entries:
                if isinstance(w, (QtWidgets.QLineEdit, QtWidgets.QAbstractSpinBox)):
                    w.setReadOnly(True)
                else:
                    w.setEnabled(False)
        for root in [self] if pages is None else pages:
            for b in root.findChildren(QtWidgets.QPushButton):
                if b.text() == "Clear Property":
                    b.setEnabled(False)
        for ed in self._eds if editors is None else editors:
            if self._partial and getattr(ed, "member_editable", False):
                continue  # its own controls serve the editable keys
            fn = getattr(ed, "set_read_only", None)
            if fn is not None:
                fn(True)

    # -- apply --------------------------------------------------------------
    def _ok(self) -> None:
        if self.read_only_reason and not self._partial:
            self.accept()
            return
        self._build_for_apply()
        for ed in self._live_editors():
            fn = getattr(ed, "ok_problem", None)
            p = fn() if fn is not None else None
            if p:
                # shown in the sheet, above its buttons, with its tab brought forward (the
                # UI guide: input problems inline, not in a message box)
                _title, message, widget = p
                if widget is not None and self._tabs.indexOf(widget) >= 0:
                    self._tabs.setCurrentWidget(widget)
                self._fail(message)
                return
        self.apply()
        if not self._error:
            self.accept()

    def _reveal(self, widget) -> None:
        """Bring the (inner) tab holding ``widget`` to the front and focus it."""
        w = widget
        while w is not None and w is not self:
            parent = w.parentWidget()
            if isinstance(parent, QtWidgets.QStackedWidget) and isinstance(
                parent.parentWidget(), QtWidgets.QTabWidget
            ):
                parent.parentWidget().setCurrentWidget(w)
            w = parent
        ensure_visible(widget)
        widget.setFocus()

    def _fail(self, message: str, widget=None) -> None:
        self._error = True
        self._status.setText(message)
        self._status.show()
        if widget is not None:
            self._mark_field(widget, message)
            self._reveal(widget)

    def _collect_edits(self) -> list:
        """The ``(key, value)`` writes for the sheet's changes."""
        edits: dict = {}
        for key, entries in self._fields.items():
            f, w = entries[0]
            if f.kind != "slmode" and self._state(key) != self._initial.get(key):
                edits[key] = self._value(f, w)
        for key, entries in self._fields.items():
            f, w = entries[0]
            if f.kind != "slmode":
                continue
            cleared = any(key in s.family for s in self._cleared)
            if (
                not cleared
                and self._state(key) == self._initial.get(key)
                and self._state(f.tag) == self._initial.get(f.tag)
            ):
                continue
            tag = self._first[f.tag].text().strip()
            stored = self._props.get(key)
            if tag:
                edits[key] = str(_SL_ON | (_SL_CONTINUOUS if w.isChecked() else 0))
            elif stored is not None and (cleared or _atoi(stored) & _SL_ON):
                edits[key] = None  # no slider without a tag
        for ed in self._eds:
            fn = getattr(ed, "key_edits", None)
            for key, value in (fn() if fn is not None else None) or ():
                edits[key] = value
        for sub in self._cleared:
            for key in self._expand(sub.family):
                if key in edits or key not in self._props or self._shared_in_use(key, sub):
                    continue
                # a field's own change decides its key -- except a spin box
                # that was locked on a value it cannot show (its state never
                # changes when Clear blanks it)
                if key in self._fields and key not in self._locked_keys:
                    continue
                edits[key] = None
        # CtDraw's Disable page always stores the checked style radio, and an
        # absent GREY_STYLE checks the first one, Embossed
        if "GREY_STYLE" in self._fields and ("DISABLED" in edits or "GREY_STYLE" in edits):
            disabled = edits["DISABLED"] if "DISABLED" in edits else self._props.get("DISABLED")
            grey = edits["GREY_STYLE"] if "GREY_STYLE" in edits else self._props.get("GREY_STYLE")
            if disabled and not grey:
                edits["GREY_STYLE"] = _GREY_STYLES[0][1]
            elif disabled and "GREY_STYLE" not in edits:
                # the page stores the radio it shows: GRAY_ALL as GRAY_PART, an unknown
                # value as GRAY_SUNK
                g = grey.strip().upper()
                g = "GRAY_PART" if g == "GRAY_ALL" else g
                if g not in {v.upper() for _l, v in _GREY_STYLES}:
                    g = _GREY_STYLES[0][1]
                if g != grey:
                    edits["GREY_STYLE"] = g
        return list(edits.items())

    def _collect(self):
        """``(edits, problem)``: :meth:`_collect_edits`, or the first problem
        as ``(message, widget)``."""
        edits = dict(self._collect_edits())
        if self._partial:
            # a genie member: only its Access > General settings are written
            edits = {k: v for k, v in edits.items() if k.upper() in self._editable_keys}
        for key, value in edits.items():
            if key not in self._fields:
                continue
            f, w = self._fields[key][0]
            if value:
                text = w.text() if isinstance(w, QtWidgets.QLineEdit) else value
                if f.limit and len(text) > f.limit:
                    return [], (
                        "%s is too long: %d characters. The maximum is %d; shorten "
                        "it and apply again." % (f.label.rstrip(":"), len(text), f.limit),
                        w,
                    )
            fn = getattr(w, "range_problem", None)
            msg = fn() if fn is not None else None
            if msg:
                rf = w.range_field
                return [], ("%s: %s" % (f.label.rstrip(":"), msg), rf.minimum if rf is not None else w)
        from ...editing import UnencodableTextError, guard_text

        checks = [(v, self._fields[k][0][1] if k in self._fields else None) for k, v in edits.items() if v]
        for value, w in checks:
            try:
                guard_text(value)
            except UnencodableTextError as exc:
                return [], ("%s. Replace those characters and apply again." % exc, w)
        for ed in self._live_editors():
            fn = getattr(ed, "problem", None)
            p = fn() if fn is not None else None
            if p:
                return [], p
        return list(edits.items()), None

    def _live_editors(self) -> list:
        """The page editors that can write: all of them, or on a genie
        member's sheet the ones serving its Access > General settings."""
        if not self._partial:
            return list(self._eds)
        return [ed for ed in self._eds if getattr(ed, "member_editable", False)]

    def apply(self) -> bool:
        """Write every change on the sheet as ONE undo step."""
        if self.read_only_reason and not self._partial:
            return False
        self._build_for_apply()
        doc, i = self._doc, self._index
        self._error = False
        self._status.hide()
        edits, problem = self._collect()
        if problem is not None:
            self._fail(*problem)
            return False
        changed = False
        failed: list = []
        errors: list = []
        editors = self._live_editors()
        with doc.batch("object properties"):
            for ed in editors:
                if getattr(ed, "phase", "before") == "before":
                    changed |= bool(ed.apply())
            for key, value in edits:
                try:
                    if key == "__AN_NAME__":
                        ok = doc.set_object_name(i, value or "")
                    elif value is None:
                        if key not in self._props:
                            continue  # already absent: nothing to do
                        ok = doc.remove_property(i, key)
                    else:
                        ok = doc.ensure_property(i, key, value)
                except Exception as exc:  # noqa: BLE001 -- report it with the others
                    ok = False
                    errors.append("%s: %s" % (key, exc))
                if ok:
                    changed = True
                else:
                    failed.append(key)
            # the Input tab's touch / keyboard CHILD records (a structural
            # edit) after the bag edits, which address the records by index
            for ed in editors:
                if getattr(ed, "phase", "before") == "after":
                    changed |= bool(ed.apply())
        self._props = dict(doc.object_properties(i))
        # every field shows the object as it is stored now (the default style
        # written with a new 'Disable when', a value changed on the page
        # meanwhile) -- except a key that could not be stored
        self._syncing = True
        try:
            for key, entries in self._fields.items():
                if key in failed:
                    continue
                for f, w in entries:
                    self._set_widget(f, w, self._stored_display(f))
        finally:
            self._syncing = False
        self._update_slider_boxes()
        old = self._initial
        # a key that could not be stored stays 'changed', so Apply retries it
        self._initial = {k: (old.get(k) if k in failed else self._state(k)) for k in self._fields}
        self._locked_keys = {
            k for k, entries in self._fields.items() if any(getattr(w, "locked", False) for _f, w in entries)
        }
        self._cleared = []
        self.failures = list(failed)
        for ed in self._eds:
            fn = getattr(ed, "reload", None)
            if fn is not None:
                fn()
        if failed:
            wanted = dict(edits)
            broke = {e.split(":", 1)[0] for e in errors}
            adds = [k for k in failed if k not in broke and wanted.get(k) not in (None, "")]
            drops = [k for k in failed if k not in broke and wanted.get(k) in (None, "")]
            parts = ["Could not write %s." % e for e in errors]
            if adds:
                parts.append("Could not store %s: this object has no property list." % ", ".join(adds))
            if drops:
                parts.append("Could not remove %s from this object." % ", ".join(drops))
            if changed:
                parts.append("The other changes were applied.")
            self._fail(" ".join(parts))
        self._refresh_marks()
        self._take_snapshots()
        if changed:
            self.changed = True
            if hasattr(self, "_ctg_target"):
                # the window's stale-dialog check: still this sheet's object
                # (a genie-element member edit replaces the record)
                self._ctg_target = doc.parsed.page.objects[i]
            self.applied.emit()
            # (the step is written and the page drawn: an editor may tell what it did, with
            # an Undo of this step; the Linkage page's relink)
            for ed in editors:
                fn = getattr(ed, "after_step", None)
                if callable(fn):
                    fn()
        return changed

    # -- modeless: the page changes underneath ------------
    def _field_owners(self) -> list:
        """The field widgets (and their range fields): their own state is
        compared with ``_initial``, not with a page snapshot."""
        out = []
        for entries in self._fields.values():
            for _f, w in entries:
                out.append(w)
                rf = getattr(w, "range_field", None)
                if rf is not None:
                    out.append(rf)
        return out

    def _page_state(self, page: QtWidgets.QWidget, owners: set) -> tuple:
        """The state of the controls of ``page`` a page editor owns (the
        fields' widgets left out: ``owners`` = their ids)."""
        out = []
        for w in [page] + page.findChildren(QtWidgets.QWidget):
            p = w
            while p is not None and p is not page and id(p) not in owners:
                p = p.parentWidget()
            if p is not None and id(p) in owners:
                continue
            st = widget_state(w)
            if st is not None:
                out.append(st)
        return tuple(out)

    def _take_snapshots(self) -> None:
        """Remember the controls of every page an editor built (what a later
        ``reload`` / ``has_pending_edits`` compares with)."""
        owners_list = self._field_owners()  # (kept alive: their ids are the keys)
        owners = {id(w) for w in owners_list}
        self._snapshots = {}
        for page in {id(p): p for p in self._editor_pages.values()}.values():
            try:
                self._snapshots[id(page)] = self._page_state(page, owners)
            except RuntimeError:
                pass
        self._snapshot_owners = owners_list

    def _editor_touched(self, ed) -> bool:
        """The user changed something on ``ed``'s controls since they were loaded."""
        for name in ("has_pending_edits", "has_changes"):
            fn = getattr(ed, name, None)
            if callable(fn):
                try:
                    return bool(fn())
                except Exception:  # noqa: BLE001 -- when unsure, keep what is shown
                    return True
        fn = getattr(ed, "key_edits", None)
        if callable(fn):
            try:
                if fn():
                    return True
            except Exception:  # noqa: BLE001
                return True
        if hasattr(ed, "_initial"):
            # an editor that keeps what it loaded: its values tell (no false
            # 'changed' from selecting another row of a list)
            for name in ("values", "rows"):
                fn = getattr(ed, name, None)
                if callable(fn):
                    try:
                        return fn() != ed._initial
                    except Exception:  # noqa: BLE001
                        return True
        page = self._editor_pages.get(id(ed))
        if page is None or id(page) not in self._snapshots:
            return False
        owners = {id(w) for w in self._field_owners()}
        try:
            return self._page_state(page, owners) != self._snapshots[id(page)]
        except RuntimeError:
            return False

    def _touched_keys(self) -> set:
        """The field keys the user changed (or cleared) since they were loaded."""
        keys = {k for k in self._fields if self._state(k) != self._initial.get(k)}
        for sub in self._cleared:
            keys.update(f.key for f in sub.fields if f.key in self._fields)
        return keys

    def has_pending_edits(self) -> bool:
        """Something typed on the sheet is not applied yet."""
        if self.read_only_reason and not self._partial:
            return False
        if self._cleared:
            return True
        keys = self._touched_keys()
        if self._partial:
            keys = {k for k in keys if k.upper() in self._editable_keys}
        if keys:
            return True
        return any(self._editor_touched(ed) for ed in self._live_editors())

    def retarget(self, index: int) -> None:
        """The sheet's record now sits at ``index`` (a structural edit moved it)."""
        self._index = index
        self.context.index = index
        for ed in self._eds:
            fn = getattr(ed, "retarget", None)
            if fn is not None:
                fn(index)
        if hasattr(self, "_ctg_target"):
            objs = self._doc.parsed.page.objects
            if 0 <= index < len(objs):
                self._ctg_target = objs[index]
        self._set_title()

    def reload(self) -> None:
        """Show the object as the page has it now in every field and page the
        user has not changed; what was typed stays (and Apply still compares
        it with what the sheet loaded)."""
        doc, i = self._doc, self._index
        try:
            objs = doc.parsed.page.objects
        except AttributeError:
            return
        if not 0 <= i < len(objs):
            return
        # the pages the user changed, BEFORE the fields move (a field load
        # ticks the controls that follow it: Access > 'Same area as page')
        touched_editors = {id(ed) for ed in self._eds if self._editor_touched(ed)}
        touched = self._touched_keys()
        self._props = dict(doc.object_properties(i))
        self._syncing = True
        try:
            for key, entries in self._fields.items():
                if key in touched:
                    continue
                for f, w in entries:
                    self._set_widget(f, w, self._stored_display(f))
        finally:
            self._syncing = False
        for key, entries in self._fields.items():
            if key in touched:
                continue
            self._initial[key] = self._state(key)
            if any(getattr(w, "locked", False) for _f, w in entries):
                self._locked_keys.add(key)
            else:
                self._locked_keys.discard(key)
        for ed in self._eds:
            if id(ed) in touched_editors:
                continue
            fn = getattr(ed, "refresh", None) or getattr(ed, "reload", None)
            if fn is None:
                continue
            try:
                fn()
            except Exception:  # noqa: BLE001 -- a page that cannot re-read keeps its values
                pass
        self._update_slider_boxes()
        self._set_title()
        # a page the user changed keeps the state it was loaded with: what was
        # typed there stays 'changed' (the next reload keeps it, the save
        # question counts it)
        kept = {}
        for ed in self._eds:
            page = self._editor_pages.get(id(ed))
            if id(ed) in touched_editors and page is not None and id(page) in self._snapshots:
                kept[id(page)] = self._snapshots[id(page)]
        self._take_snapshots()
        self._snapshots.update(kept)
        self._refresh_marks(self._pending_props())
