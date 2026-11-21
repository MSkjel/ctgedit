"""Small modal dialogs for genie parameters."""

from __future__ import annotations
from ..style.theme import sp as _sp

import os

from PySide6 import QtCore, QtGui, QtWidgets

from ..prefs.settings import app_settings


from ...format import codes as _codes
from ..pick_fields import TemplateCatalog, TemplatePicker, _preview_size, library_stem, thumbnail_pixmap


# ---------------------------------------------------------------------------
# Small Arrange/Tools dialogs (CtDraw parity)
# ---------------------------------------------------------------------------


class _ChoiceDialog(QtWidgets.QDialog):
    """A CtDraw-style radio-choice dialog (Rotate / Mirror / Align)."""

    def __init__(self, title: str, options: list[str], parent=None, current: int = 0):
        super().__init__(parent)
        self.setWindowTitle(title)
        lay = QtWidgets.QVBoxLayout(self)
        self._radios = []
        for i, label in enumerate(options):
            rb = QtWidgets.QRadioButton(label)
            rb.setChecked(i == current)
            self._radios.append(rb)
            lay.addWidget(rb)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def choice(self) -> int:
        for i, rb in enumerate(self._radios):
            if rb.isChecked():
                return i
        return 0


class CompileResultsDialog(QtWidgets.QDialog):
    """The compiler's messages (Logs\\errs): errors first, the current page's
    messages marked, filterable -- what Studio's compile error list shows."""

    def __init__(self, result, page_name: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Compile %s - %s" % (result.project, result.outcome))
        self.resize(900, 460)
        lay = QtWidgets.QVBoxLayout(self)
        head = "%d error(s), %d warning(s) in %.0f s" % (
            len(result.errors),
            len(result.warnings),
            result.seconds,
        )
        if result.note:
            head += "\n" + result.note
        lay.addWidget(QtWidgets.QLabel(head))
        from ..style import theme

        self._filter = theme.search_field("Search messages", self)
        self._filter.setToolTip("Shows the messages that contain the text")
        lay.addWidget(self._filter)
        cols = ["", "Code", "Project", "File", "Line", "Message", "Context"]
        self._table = QtWidgets.QTableWidget(0, len(cols))
        self._table.setHorizontalHeaderLabels(cols)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        low = page_name.lower()
        rank = {"error": 0, "warning": 1}
        msgs = sorted(
            result.messages,
            key=lambda m: (rank.get(m.severity, 2), 0 if low and low in m.file.lower() else 1),
        )
        for m in msgs:
            r = self._table.rowCount()
            self._table.insertRow(r)
            mark = {"error": "⛔", "warning": "⚠"}.get(m.severity, "")
            vals = [
                mark,
                m.code,
                m.project,
                m.file,
                "" if m.line is None else str(m.line),
                m.message,
                m.context,
            ]
            for c, v in enumerate(vals):
                it = QtWidgets.QTableWidgetItem(v)
                if low and low in m.file.lower():
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                self._table.setItem(r, c, it)
        # the Message column takes the extra width, not the last one
        from ..tables import enable_row_copy, make_resizable

        make_resizable(
            self._table,
            "CompileResultsDialog/messages",
            stretch=cols.index("Message"),
            caps={cols.index("Message"): 0.6},
        )
        enable_row_copy(self._table)
        lay.addWidget(self._table, 1)
        self._filter.searchChanged.connect(self._apply_filter)
        self._filter.set_list_view(self._table)
        self._filter.bind_find_shortcut(self)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        lay.addWidget(bb)

    def _apply_filter(self, text: str) -> None:
        m = self._filter.matcher(text)
        for r in range(self._table.rowCount()):
            cells = [
                (self._table.item(r, c).text() if self._table.item(r, c) else "")
                for c in range(self._table.columnCount())
            ]
            # (plain text: also across the cells, as one line)
            self._table.setRowHidden(r, not (m(*cells) or (not m.regex and m(" ".join(cells)))))


#: the class and link labels (object_labels: the helper processes use them too)
from ...features.object_labels import CLASS_LABELS as _CLASS_LABELS  # noqa: E402
from ...features.object_labels import LINK_LABELS as _LINK_LABELS  # noqa: E402

#: touch / keyboard command records: not rows of the Goto Object tree
_INPUT_TCS = _codes.INPUT_TCS

#: Goto Object's columns after 'Object': (key, header, shown by default). The user picks
#: them (a right-click on the header, or Columns); 'goto/columns' remembers the choice.
GOTO_COLUMNS = (
    ("where", "Position / text", True),
    ("position", "Position", False),
    ("size", "Size", False),
    ("name", "Name", False),
    ("text", "Text", False),
    ("fill", "Fill", False),
    ("line", "Line colour", False),
    ("animations", "Animations", False),
    ("hidden", "Hidden when", False),
    ("commands", "Commands", False),
    ("tooltip", "Tool tip", False),
    ("access", "Access", False),
    ("metadata", "Metadata", False),
    ("locked", "Locked", False),
)
GOTO_COLUMNS_KEY = "goto/columns"


def goto_columns() -> list:
    """The Goto Object columns chosen (keys of :data:`GOTO_COLUMNS`)."""
    from ..prefs.settings import app_settings

    keys = [k for k, _h, _d in GOTO_COLUMNS]
    v = app_settings().value(GOTO_COLUMNS_KEY, None)
    if v is None:
        return [k for k, _h, on in GOTO_COLUMNS if on]
    if isinstance(v, str):
        v = [x for x in v.split(",") if x]
    return [k for k in keys if k in [str(x) for x in v]]


class GotoObjectDialog(QtWidgets.QDialog):
    """Tools > Goto Object: the page's objects as a tree."""

    #: the current row's object index (the window selects it on the page)
    highlighted = QtCore.Signal(int)
    #: Go To: select the object and bring it into view (-1 = the page row)
    goToRequested = QtCore.Signal(int)
    #: Properties… / a double-click: its property sheet (-1 = Page Properties)
    propertiesRequested = QtCore.Signal(int)

    def __init__(self, doc, parent=None, current: int | None = None, modeless: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Goto Object")
        self.setMinimumSize(520, 480)
        self.selected_index: int | None = None
        #: OK on the page row: show the Page Properties
        self.page_selected = False
        #: an editor window keeps it open (Go To, Properties…, Close); else
        #: OK opens the properties and closes it
        self._modeless = bool(modeless)
        self._doc = doc
        self._serial = getattr(doc, "derive_serial", None)
        self._matches: list[int] = []
        self._match_pos = -1
        self._searching = False
        lay = QtWidgets.QVBoxLayout(self)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Object AN:"))
        self._filter = QtWidgets.QLineEdit()
        self._filter.setPlaceholderText("AN or the start of a name")
        self._filter.setToolTip(
            "A number finds the object with that AN. Text finds the objects whose name starts with it."
        )
        row.addWidget(self._filter, 1)
        from ..style.theme import icon as _icon

        self._btn_prev = QtWidgets.QToolButton()
        self._btn_prev.setIcon(_icon("prev_change"))
        self._btn_prev.setToolTip("Previous match")
        self._btn_prev.clicked.connect(lambda: self.step(-1))
        self._btn_next = QtWidgets.QToolButton()
        self._btn_next.setIcon(_icon("next_change"))
        self._btn_next.setToolTip("Next match")
        self._btn_next.clicked.connect(lambda: self.step(1))
        row.addWidget(self._btn_prev)
        row.addWidget(self._btn_next)
        lay.addLayout(row)
        self._btn_cols = QtWidgets.QToolButton()
        self._btn_cols.setText("Columns")
        self._btn_cols.setToolTip("Choose the columns to show (or right-click the column titles)")
        self._btn_cols.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self._cols_menu = QtWidgets.QMenu(self._btn_cols)
        self._cols_menu.aboutToShow.connect(self._fill_columns_menu)
        self._btn_cols.setMenu(self._cols_menu)
        row.addWidget(self._btn_cols)
        self._tree = QtWidgets.QTreeWidget()
        # every column is always there (hidden when not chosen), so the widths the user
        # sets stay with their column
        self._tree.setColumnCount(1 + len(GOTO_COLUMNS))
        self._tree.setHeaderLabels(["Object"] + [h for _k, h, _d in GOTO_COLUMNS])
        self._tree.setUniformRowHeights(True)
        self._shown = set(goto_columns())
        self._filled: set = set()
        for c, (key, _h, _d) in enumerate(GOTO_COLUMNS, 1):
            self._tree.setColumnHidden(c, key not in self._shown)
        hdr = self._tree.header()
        hdr.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        hdr.customContextMenuRequested.connect(
            lambda pos: (self._fill_columns_menu(), self._cols_menu.exec(hdr.mapToGlobal(pos)))
        )
        from ..tables import make_resizable

        make_resizable(self._tree, "GotoObjectDialog/objects")
        lay.addWidget(self._tree, 1)
        self._status = QtWidgets.QLabel()
        self._status.setWordWrap(True)
        lay.addWidget(self._status)
        self._items: dict[int, QtWidgets.QTreeWidgetItem] = {}
        #: row index -> its record (PageObject) when the tree was built
        self._records: dict = {}
        #: row index -> its object (GraphicObject) when the tree was built (the columns)
        self._objs: dict = {}
        #: the window's selection is being shown (no highlighted signal back)
        self._following = False
        self._an: dict[int, int] = {}
        self._name: dict[int, str] = {}
        self._tips: dict[int, str] = {}
        self._composite_templates = None
        self._build(doc)
        self._filter.textChanged.connect(self._search)
        self._tree.currentItemChanged.connect(self._on_current)
        self._tree.itemDoubleClicked.connect(lambda *_a: self._ok())
        if self._modeless:
            buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
            self._btn_go = buttons.addButton("Go To", QtWidgets.QDialogButtonBox.AcceptRole)
            self._btn_go.setToolTip("Select the object on the page and bring it into view")
            self._btn_go.setDefault(True)
            self._btn_go.clicked.connect(self._go_to)
            self._btn_props = buttons.addButton("Properties…", QtWidgets.QDialogButtonBox.ActionRole)
            self._btn_props.setToolTip("Open the object's properties (double-click a row)")
            self._btn_props.setAutoDefault(False)
            self._btn_props.clicked.connect(self._ok)
        else:
            buttons = QtWidgets.QDialogButtonBox(
                QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
            )
            buttons.accepted.connect(self._ok)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self._show_step_buttons()
        # the selected object's row is current, its AN typed
        if current is not None and current in self._items:
            self._tree.setCurrentItem(self._items[current])
            self._tree.scrollToItem(self._items[current])
        self._filter.setFocus()
        self._filter.selectAll()

    # -- the tree ----------------------------------------------------------------
    def set_document(self, doc) -> bool:
        """Show ``doc`` (the page shown now) as it is now: the tree is built
        again when the page or its content changed; the current row stays
        on its object (by record), the typed text and its matches stay.
        True when the tree was rebuilt."""
        serial = getattr(doc, "derive_serial", None)
        if doc is self._doc and serial == self._serial:
            return False
        cur = self.current_index()
        rec = self._records.get(cur) if isinstance(cur, int) else None
        if doc is not self._doc:
            self._composite_templates = None
        self._doc = doc
        self._serial = serial
        self._searching = True
        # (no highlighted signal: a rebuild never changes the page's selection)
        self._tree.blockSignals(True)
        try:
            self._tree.clear()
            self._items, self._an, self._name, self._tips = {}, {}, {}, {}
            self._records, self._objs, self._filled = {}, {}, set()
            self._build(doc)
            target = None
            if cur == -1:
                target = self._root
            elif rec is not None:
                objs = getattr(getattr(getattr(doc, "parsed", None), "page", None), "objects", None) or []
                i = next((k for k, po in enumerate(objs) if po is rec), None)
                target = self._items.get(i) if i is not None else None
            if target is not None:
                self._tree.setCurrentItem(target)
                self._tree.scrollToItem(target)
        finally:
            self._tree.blockSignals(False)
            self._searching = False
        t = self._filter.text().strip()
        self._matches = self._find(t) if t else []
        cur = self.current_index()
        self._match_pos = self._matches.index(cur) if cur in self._matches else -1
        self._show_step_buttons()
        return True

    def _build(self, doc) -> None:
        from ...editing import ElementDocument

        if isinstance(doc, ElementDocument):
            kind = {
                "symbol": "Symbol",
                "genie": "Genie",
                "supergenie": "Super Genie",
                "template": "Template",
            }.get(doc.kind, "Symbol")
            title = "%s %s" % (kind, getattr(doc, "display_name", "") or "")
        else:
            name = doc.page.name or os.path.splitext(os.path.basename(doc.path or ""))[0]
            title = "Page %s" % name
        self._root = QtWidgets.QTreeWidgetItem([title.strip(), ""])
        self._root.setData(0, QtCore.Qt.UserRole, -1)
        self._tree.addTopLevelItem(self._root)
        objs = doc.page.objects
        records = getattr(getattr(getattr(doc, "parsed", None), "page", None), "objects", None) or []
        for i, o in enumerate(objs):
            # CtDraw lists no touch / keyboard command records (they share the
            # owner's AN, so an AN finds the owner)
            if o.raw.get("_type_code") in _INPUT_TCS:
                continue
            parent = self._items.get(o.parent) if o.parent is not None else self._root
            item = QtWidgets.QTreeWidgetItem([self._label(doc, i, o), self._details(o)])
            if i in self._tips:
                item.setToolTip(0, self._tips[i])
            # the TOPMOST object first at every level, like CtDraw's tree (a
            # later record draws over an earlier one)
            (parent or self._root).insertChild(0, item)
            item.setData(0, QtCore.Qt.UserRole, i)
            # the record: a rebuild keeps the current row on its object
            self._records[i] = records[i] if i < len(records) else None
            self._objs[i] = o
            self._items[i] = item
        self._root.setExpanded(True)
        for key in self._shown:
            self._fill_column(key)

    # -- the columns ---------------------------------------------------------------
    def _fill_columns_menu(self) -> None:
        m = self._cols_menu
        m.clear()
        for key, header, _d in GOTO_COLUMNS:
            a = m.addAction(header)
            a.setCheckable(True)
            a.setChecked(key in self._shown)
            a.toggled.connect(lambda on, k=key: self.show_column(k, on))
        m.addSeparator()
        m.addAction("Default Columns", self.default_columns)
        # (the header's own menu is this one: the helper's item goes here)
        from ..tables import add_fit_item

        add_fit_item(m, self._tree)

    def show_column(self, key: str, on: bool = True) -> None:
        """Show or hide the column ``key`` (remembered)."""
        keys = [k for k, _h, _d in GOTO_COLUMNS]
        if key not in keys:
            return
        (self._shown.add if on else self._shown.discard)(key)
        if on:
            self._fill_column(key)
        c = 1 + keys.index(key)
        self._tree.setColumnHidden(c, not on)
        if on:
            # (fitted to its text as a fit does, capped; the helper fits a remembered 0 again
            # when the column is shown with rows)
            from ..tables import fit_column

            fit_column(self._tree, c)
        from ..prefs.settings import app_settings

        app_settings().setValue(GOTO_COLUMNS_KEY, [k for k in keys if k in self._shown])

    def default_columns(self) -> None:
        for key, _h, on in GOTO_COLUMNS:
            self.show_column(key, on)

    def shown_columns(self) -> list:
        return [k for k, _h, _d in GOTO_COLUMNS if k in self._shown]

    def column_text(self, index: int, key: str) -> str:
        """The text of column ``key`` in the row of object ``index`` (tests)."""
        item = self._items.get(index)
        keys = [k for k, _h, _d in GOTO_COLUMNS]
        return item.text(1 + keys.index(key)) if item is not None else ""

    def _fill_column(self, key: str) -> None:
        if key in self._filled:
            return
        self._filled.add(key)
        c = 1 + [k for k, _h, _d in GOTO_COLUMNS].index(key)
        for i, item in self._items.items():
            o = self._objs.get(i)
            if o is None:
                continue
            try:
                text, colour = self._cell(key, i, o)
            except Exception:  # noqa: BLE001 -- swallow-ok: a cell only, left blank
                text, colour = "", None
            item.setText(c, text)
            if text:
                item.setToolTip(c, text)
            if colour is not None:
                from ..colour_picker import qcolour

                q = qcolour(colour)
                if q is not None:
                    pm = QtGui.QPixmap(12, 12)
                    pm.fill(q)
                    item.setIcon(c, QtGui.QIcon(pm))

    def _cell(self, key: str, i: int, o):
        """(text, colour or None) of column ``key`` for object ``o`` (record ``i``)."""
        g, st = o.geometry, o.style
        props = o.raw.get("_props") or {}
        if key == "where":
            return self._details(o), None
        if key == "position":
            return "%d, %d" % (g.x, g.y), None
        if key == "size":
            return "%d x %d" % (g.extent_x, g.extent_y), None
        if key == "name":
            return self._name.get(i, ""), None
        if key == "text":
            return " ".join(str(st.text or "").split()), None
        if key in ("fill", "line"):
            from ..colour_picker import describe

            c = st.fill_colour if key == "fill" else st.line_colour
            if c is None or (key == "fill" and not st.filled):
                return "", None
            return describe(int(c)), int(c)
        if key == "animations":
            from ...format.colourstate import dynamic_keys

            return "; ".join(
                "%s = %s" % (k, props.get(k, ""))
                for k in dynamic_keys(props)
                if str(props.get(k, "")).strip()
            ), None
        if key == "hidden":
            return str(props.get("VISIBLE") or ""), None
        if key == "tooltip":
            return str(props.get("COMMENT") or ""), None
        if key == "access":
            parts = []
            if str(props.get("PRIV") or "").strip() not in ("", "0"):
                parts.append("privilege %s" % props["PRIV"])
            if str(props.get("AREA") or "").strip() not in ("", "0"):
                parts.append("area %s" % props["AREA"])
            if str(props.get("DISABLED") or "").strip():
                parts.append("disabled when %s" % props["DISABLED"])
            return ", ".join(parts), None
        if key == "metadata":
            return "; ".join("%s = %s" % (n, v) for n, v in self._metadata(i)), None
        if key == "commands":
            return self._commands(i), None
        if key == "locked":
            return ("Locked" if (o.raw.get("_dwflags") or 0) & 0x4 or o.locked else ""), None
        return "", None

    def _metadata(self, i: int) -> list:
        fn = getattr(self._doc, "metadata", None)
        try:
            return list(fn(i)) if fn is not None else []
        except Exception:  # noqa: BLE001 -- swallow-ok: a record without metadata
            return []

    def _commands(self, i: int) -> str:
        fn = getattr(self._doc, "input_commands", None)
        if fn is None:
            return ""
        try:
            cmds = fn(i)
        except Exception:  # noqa: BLE001 -- swallow-ok: a record without commands
            return ""
        out = []
        touch = cmds.get("touch") or {}
        for k, label in (
            ("CMD", "up"),
            ("DOWN_CMD", "down"),
            ("REPEAT_CMD", "repeat"),
            ("DBLCLK_CMD", "double-click"),
        ):
            if str(touch.get(k) or "").strip():
                out.append("%s: %s" % (label, touch[k]))
        for kd in cmds.get("keys") or ():
            if str(kd.get("CMD") or "").strip():
                out.append("%s: %s" % (kd.get("KEY_SEQ") or "key", kd["CMD"]))
        return "; ".join(out)

    def _label(self, doc, i: int, o) -> str:
        """The row text as the Objects panel shows an object: its name first, then
        '<class> AN<n>' ('FxLine  Line AN1'; CtDraw puts the name last); a linked group /
        symbol names its element instead of the class ('Genie lib.elem')."""
        tc = o.raw.get("_type_code")
        cls = _CLASS_LABELS.get(tc, o.kind.value.replace("_", " ").title())
        if tc == 0x05 and o.kind.value == "number":
            cls = "Number"  # CtDraw's TextAsNumber class
        if tc == 0x08 and (o.raw.get("_dwflags") or 0) & 0x200:
            cls = "Genie"  # a nested genie instance (no own link)
        try:
            po = doc.parsed.page.objects[i]
            if po.subtype_size & 0x1:
                from ...format.symbols import reference_from_body

                ref = reference_from_body(po.body_raw)
                if ref is not None and o.kind.value == "composite_genie":
                    # a composite links its composition cache: name its template
                    cls = "Composite Genie %s" % self._composite_name(doc, ref)
                    self._tips[i] = "%s.%s" % (ref.library, ref.element)
                elif ref is not None:
                    kind = _LINK_LABELS.get(ref.ext, "Symbol")
                    cls = "%s %s.%s" % (kind, ref.library, ref.element)
        except Exception:  # noqa: BLE001 -- a label only
            pass
        an = o.an if o.an is not None and o.an > 0 else None
        self._an[i] = an if an is not None else -1
        props = o.raw.get("_props") or {}
        name = str(props.get("__AN_NAME__") or o.raw.get("_name") or "")
        self._name[i] = name
        text = "%s AN%d" % (cls, an) if an is not None else cls
        return "%s  %s" % (name, text) if name else text

    def _composite_name(self, doc, ref) -> str:
        """The template name of a composite genie's cache link (its guid
        when the template is not found)."""
        from ...format import composites as C

        guid = C.cache_guid(getattr(ref, "raw", "") or "%s.%s" % (ref.library, ref.element))
        if not guid:
            return ref.element
        if self._composite_templates is None:
            try:
                self._composite_templates = C.load_templates(doc.path) if doc.path else {}
            except Exception:  # noqa: BLE001 -- a label only
                self._composite_templates = {}
        tpl = self._composite_templates.get(guid)
        if tpl is None and doc.path:
            try:
                for d in C.cache_dirs(doc.path, guid):
                    tpl = C.cached_template(d, guid)
                    if tpl is not None:
                        break
            except Exception:  # noqa: BLE001
                tpl = None
            self._composite_templates[guid] = tpl
        return tpl.name if tpl is not None and tpl.name else "{%s}" % guid

    @staticmethod
    def _details(o) -> str:
        g = o.geometry
        text = " ".join(str(o.style.text or "").split())[:40]
        pos = "%d, %d  %d×%d" % (g.x, g.y, g.extent_x, g.extent_y)
        return "%s   %s" % (pos, text) if text else pos

    def object_count(self) -> int:
        """The object rows (tests / scripting)."""
        return len(self._items)

    def follow_selection(self, index) -> bool:
        """Show the object the window selected (a click on the page): its row becomes
        current and is scrolled to, its AN in the field; nothing is selected back.
        True when the row moved."""
        item = self._items.get(index) if isinstance(index, int) else None
        if item is None or self._tree.currentItem() is item:
            return False
        self._following = True
        try:
            self._tree.setCurrentItem(item)
        finally:
            self._following = False
        self._tree.scrollToItem(item)
        return True

    def current_index(self):
        """The object of the current row (None: none, -1: the page row)."""
        item = self._tree.currentItem()
        return None if item is None else item.data(0, QtCore.Qt.UserRole)

    # -- finding ------------------------------------------------------------------
    def _search(self, text: str) -> None:
        t = text.strip()
        self._matches, self._match_pos = [], -1
        self._status.clear()
        if t:
            self._matches = self._find(t)
            if self._matches:
                self._go(0)
            else:
                # nothing found: OK must not open the row of an earlier search
                self._searching = True
                try:
                    self._tree.setCurrentIndex(QtCore.QModelIndex())
                    self._tree.clearSelection()
                finally:
                    self._searching = False
                an = self._typed_an(t)
                self._status.setText(
                    "Cannot find object AN %s" % self._an_text(t)
                    if an is not None
                    else "No object's name starts with '%s', and no object's text contains it." % t
                )
        self._show_step_buttons()

    @staticmethod
    def _typed_an(t: str):
        """The AN ``t`` asks for, or None when it's a name."""
        import re

        m = re.match(r"(?i)an\s*(?=[1-9])", t)
        if m:
            t = t[m.end() :]
        m = re.match(r"[1-9]\d*", t)
        if m is None:
            return None
        an = int(m.group(0))
        return an if an <= 31999 else 0

    @staticmethod
    def _an_text(t: str) -> str:
        """``t`` without an 'AN' label (the number the message names)."""
        import re

        return re.sub(r"(?i)^an\s*(?=[1-9])", "", t)

    def _find(self, t: str) -> list:
        """The rows ``t`` finds, in tree order."""
        order = sorted(self._items)
        an = self._typed_an(t)
        if an is not None:
            return [i for i in order if self._an.get(i) == an] if an else []
        low = t.lower()
        finder = getattr(self._doc, "find_objects_by_name", None)
        if "." in low and finder is not None:
            try:
                full = set(finder(t))
            except Exception:  # noqa: BLE001 -- the other rules still apply
                full = set()
            hits = [i for i in order if i in full]
            if hits:
                return hits
        hits = [i for i in order if self._name.get(i, "").lower().startswith(low)]
        if hits:
            return hits
        cols = [0] + [c for c in range(1, self._tree.columnCount()) if not self._tree.isColumnHidden(c)]
        return [i for i in order if low in " ".join(self._items[i].text(c) for c in cols).lower()]

    def matches(self) -> list:
        """The object indices the typed text finds (tests / scripting)."""
        return list(self._matches)

    def step(self, delta: int) -> None:
        """Previous (-1) / Next (+1) Match."""
        if not self._matches:
            return
        self._go((self._match_pos + delta) % len(self._matches))

    def _go(self, pos: int) -> None:
        self._match_pos = pos
        i = self._matches[pos]
        item = self._items[i]
        self._searching = True
        try:
            self._tree.setCurrentItem(item)
        finally:
            self._searching = False
        self._tree.scrollToItem(item)
        if len(self._matches) > 1:
            self._status.setText("Match %d of %d" % (pos + 1, len(self._matches)))

    def _show_step_buttons(self) -> None:
        many = len(self._matches) > 1
        self._btn_prev.setEnabled(many)
        self._btn_next.setEnabled(many)

    def _on_current(self, item, _prev) -> None:
        if item is None:
            return
        i = item.data(0, QtCore.Qt.UserRole)
        if not self._searching and isinstance(i, int) and i >= 0:
            # a row picked in the tree shows its AN (CtDraw writes it into the
            # field without searching again)
            an = self._an.get(i, -1)
            text = str(an) if an > 0 else ""
            self._filter.blockSignals(True)
            try:
                self._filter.setText(text)
            finally:
                self._filter.blockSignals(False)
            # Previous / Next step from the field's text (CtDraw's up /
            # down search it again)
            self._matches = self._find(text) if text else []
            self._match_pos = self._matches.index(i) if i in self._matches else -1
            self._status.clear()
            self._show_step_buttons()
        if isinstance(i, int) and i >= 0 and not self._following:
            self.highlighted.emit(i)

    def _ok(self) -> None:
        item = self._tree.currentItem()
        if item is None:
            t = self._filter.text().strip()
            if not t:
                msg = "Pick an object in the list, or type its AN or name."
            elif self._typed_an(t) is not None:
                msg = "Cannot find object AN %s" % self._an_text(t)
            else:
                msg = "No object's name starts with '%s', and no object's text contains it." % t
            self._status.setText(msg)
            return
        i = item.data(0, QtCore.Qt.UserRole)
        if i == -1:
            self.page_selected = True
        else:
            self.selected_index = i
        self.propertiesRequested.emit(i)
        if not self._modeless:
            self.accept()

    def _go_to(self) -> None:
        """Go To: the current row's object selected and in view."""
        item = self._tree.currentItem()
        if item is None:
            self._status.setText("Pick an object in the list, or type its AN or name.")
            return
        self.goToRequested.emit(item.data(0, QtCore.Qt.UserRole))


class FontDialog(QtWidgets.QDialog):
    """Text > Font."""

    #: Apply / OK: give the selected texts this font
    applyRequested = QtCore.Signal()

    def __init__(self, style, parent=None, targets=None):
        super().__init__(parent)
        self.setWindowTitle("Font")
        self._targets = targets
        self._count = 1 if style is not None else 0
        self._loaded = None
        lay = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self._cb_face = QtWidgets.QFontComboBox()
        self._cb_face.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        form.addRow("Font:", self._cb_face)
        self._sp_size = QtWidgets.QSpinBox()
        self._sp_size.setRange(1, 255)
        self._sp_size.setSuffix(" pt")
        form.addRow("Size:", self._sp_size)
        self._chk = {}
        row = QtWidgets.QHBoxLayout()
        for key, label in (
            ("bold", "Bold"),
            ("italic", "Italic"),
            ("underline", "Underline"),
            ("strikeout", "Strikeout"),
        ):
            cb = QtWidgets.QCheckBox(label)
            self._chk[key] = cb
            row.addWidget(cb)
        row.addStretch(1)
        form.addRow("Style:", row)
        self._cb_just = QtWidgets.QComboBox()
        self._cb_just.addItems(["Left", "Centre", "Right"])
        form.addRow("Alignment:", self._cb_just)
        lay.addLayout(form)
        from ..style import theme

        self._note = theme.note_label("", "warn", self)
        self._note.setWordWrap(True)
        lay.addWidget(self._note)
        lay.addStretch(1)
        self._buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok
            | QtWidgets.QDialogButtonBox.Cancel
            | QtWidgets.QDialogButtonBox.Apply
        )
        self._buttons.accepted.connect(self._ok)
        self._buttons.rejected.connect(self.reject)
        self._buttons.button(QtWidgets.QDialogButtonBox.Apply).clicked.connect(self._apply)
        lay.addWidget(self._buttons)
        if style is not None:
            self.load(style)
        self._update_state()

    def load(self, style) -> None:
        """Show ``style``'s font (what was typed is replaced)."""
        if style.font:
            self._cb_face.setCurrentFont(QtGui.QFont(style.font))
        self._sp_size.setValue(style.font_size or 10)
        for key in self._chk:
            self._chk[key].setChecked(bool(getattr(style, key, False)))
        self._cb_just.setCurrentIndex(
            {"left": 0, "centre": 1, "right": 2}.get(getattr(style.justify, "value", "left"), 0)
        )
        self._loaded = self.values()

    def has_pending_edits(self) -> bool:
        """A value was changed and not applied."""
        return self._loaded is not None and self.values() != self._loaded

    def mark_applied(self) -> None:
        self._loaded = self.values()

    def set_selection(self, doc, indices) -> None:
        """The selection changed: the texts the next Apply changes; the first
        one's font while nothing is typed."""
        targets = self._targets(doc, indices) if (self._targets is not None and doc is not None) else []
        self._count = len(targets)
        if targets and not self.has_pending_edits():
            self.load(doc.page.objects[targets[0]].style)
        self._update_state()

    def count(self) -> int:
        return self._count

    def _update_state(self) -> None:
        why = "" if self._count else "Select a text or a button."
        self._buttons.button(QtWidgets.QDialogButtonBox.Apply).setEnabled(not why)
        self._buttons.button(QtWidgets.QDialogButtonBox.Ok).setEnabled(not why)
        self._note.setText(why)
        self._note.setVisible(bool(why))

    def _apply(self) -> None:
        if self._count:
            self.applyRequested.emit()

    def _ok(self) -> None:
        if self._count:
            self.applyRequested.emit()
        self.accept()

    def values(self) -> dict:
        return {
            "face": self._cb_face.currentFont().family(),
            "size": self._sp_size.value(),
            "bold": self._chk["bold"].isChecked(),
            "italic": self._chk["italic"].isChecked(),
            "underline": self._chk["underline"].isChecked(),
            "strikeout": self._chk["strikeout"].isChecked(),
            "justify": ["left", "centre", "right"][self._cb_just.currentIndex()],
        }


class PasteSpecialDialog(_ChoiceDialog):
    """Edit > Paste Special...: choose the clipboard format to paste."""

    def __init__(self, formats: list[str], parent=None):
        super().__init__("Paste Special", formats, parent)


class GridSetupDialog(QtWidgets.QDialog):
    """View > Grid Setup."""

    PITCH_MAX = 128
    ORIGIN_MAX = 9999
    RESIZE = "none"

    #: Apply / OK: use these values now
    applyRequested = QtCore.Signal()

    def __init__(
        self, pitch: int, display: bool, snap: bool, parent=None, pitch_y: int | None = None, origin=(0, 0)
    ):
        super().__init__(parent)
        self.setWindowTitle("Grid Setup")
        self._loaded = None
        lay = QtWidgets.QVBoxLayout(self)

        def spin(lo, hi, value, tip):
            sp = QtWidgets.QSpinBox()
            sp.setRange(lo, hi)
            sp.setValue(max(lo, min(hi, int(value))))
            sp.setSuffix(" px")
            sp.setToolTip(tip)
            return sp

        pitch_box = QtWidgets.QGroupBox("Pitch")
        pf = QtWidgets.QFormLayout(pitch_box)
        self._sp_pitch = spin(1, self.PITCH_MAX, pitch, "Distance between the vertical grid lines, in pixels")
        self._sp_pitch_y = spin(
            1,
            self.PITCH_MAX,
            pitch if pitch_y is None else pitch_y,
            "Distance between the horizontal grid lines, in pixels",
        )
        pf.addRow("Horizontal (x):", self._sp_pitch)
        pf.addRow("Vertical (y):", self._sp_pitch_y)
        lay.addWidget(pitch_box)

        origin_box = QtWidgets.QGroupBox("Origin")
        of = QtWidgets.QFormLayout(origin_box)
        ox, oy = (list(origin) + [0, 0])[:2]
        self._sp_origin_x = spin(
            0, self.ORIGIN_MAX, ox, "The grid's base point: a vertical grid line runs through this x position"
        )
        self._sp_origin_y = spin(
            0,
            self.ORIGIN_MAX,
            oy,
            "The grid's base point: a horizontal grid line runs through this y position",
        )
        of.addRow("Horizontal (x):", self._sp_origin_x)
        of.addRow("Vertical (y):", self._sp_origin_y)
        lay.addWidget(origin_box)

        # the look of the grid (editor-only: Settings > Canvas shows the same)
        from ..prefs.settings import GRID_MAJOR_RANGE, GRID_STYLES, canvas_look

        look_box = QtWidgets.QGroupBox("Look")
        lf = QtWidgets.QFormLayout(look_box)
        self._sp_major = QtWidgets.QSpinBox()
        self._sp_major.setRange(*GRID_MAJOR_RANGE)
        self._sp_major.setSpecialValueText("None")
        self._sp_major.setToolTip(
            "A stronger line every so many grid lines (1: every "
            "line). It still shows when the grid is too fine to draw"
        )
        self._cb_style = QtWidgets.QComboBox()
        for value, label in GRID_STYLES:
            self._cb_style.addItem(label, value)
        self._cb_style.setToolTip("Dots: one point at each crossing")
        lf.addRow("Major line every:", self._sp_major)
        lf.addRow("Grid style:", self._cb_style)
        lay.addWidget(look_box)
        self._show_look(canvas_look())

        self._chk_display = QtWidgets.QCheckBox("Display grid")
        self._chk_display.setChecked(display)
        lay.addWidget(self._chk_display)
        self._chk_snap = QtWidgets.QCheckBox("Snap to grid (F8)")
        self._chk_snap.setToolTip("Objects you draw, move or resize snap to the nearest grid intersection")
        self._chk_snap.setChecked(snap)
        lay.addWidget(self._chk_snap)
        lay.addStretch(1)
        self._buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok
            | QtWidgets.QDialogButtonBox.Cancel
            | QtWidgets.QDialogButtonBox.Apply
        )
        self._buttons.accepted.connect(self._ok)
        self._buttons.rejected.connect(self.reject)
        self._buttons.button(QtWidgets.QDialogButtonBox.Apply).clicked.connect(self.applyRequested.emit)
        lay.addWidget(self._buttons)
        self._loaded = self._state()

    def _state(self) -> tuple:
        return self.grid() + (self._chk_display.isChecked(), self._chk_snap.isChecked()) + self.look()

    def _show_look(self, look: dict) -> None:
        self._sp_major.setValue(
            max(self._sp_major.minimum(), min(self._sp_major.maximum(), int(look.get("grid_major", 8))))
        )
        self._cb_style.setCurrentIndex(max(0, self._cb_style.findData(look.get("grid_style", "lines"))))

    def look(self) -> tuple:
        """(major line every, grid style)."""
        return self._sp_major.value(), self._cb_style.currentData()

    def load(
        self,
        pitch: int,
        display: bool,
        snap: bool,
        pitch_y: int | None = None,
        origin=(0, 0),
        look: dict | None = None,
    ) -> None:
        """Show the grid as it is now (what was typed is replaced)."""
        ox, oy = (list(origin) + [0, 0])[:2]
        for sp, v in (
            (self._sp_pitch, pitch),
            (self._sp_pitch_y, pitch if pitch_y is None else pitch_y),
            (self._sp_origin_x, ox),
            (self._sp_origin_y, oy),
        ):
            sp.setValue(max(sp.minimum(), min(sp.maximum(), int(v))))
        self._chk_display.setChecked(bool(display))
        self._chk_snap.setChecked(bool(snap))
        if look is not None:
            self._show_look(look)
        self._loaded = self._state()

    def has_pending_edits(self) -> bool:
        """A value was changed and not applied."""
        return self._loaded is not None and self._state() != self._loaded

    def mark_applied(self) -> None:
        """The values shown are the grid now."""
        self._loaded = self._state()

    def set_display(self, on: bool) -> None:
        """View > Show Grid changed."""
        self._chk_display.setChecked(bool(on))
        if self._loaded is not None:
            self._loaded = self._loaded[:4] + (bool(on),) + self._loaded[5:]

    def set_snap(self, on: bool) -> None:
        """Snap to Grid (F8) changed."""
        self._chk_snap.setChecked(bool(on))
        if self._loaded is not None:
            self._loaded = self._loaded[:5] + (bool(on),) + self._loaded[6:]

    def set_look(self, look: dict) -> None:
        """Settings > Canvas changed the grid's look: shown here unless the look shown was
        changed and not applied (the other values typed stay as they are)."""
        if self._loaded is not None and self.look() != self._loaded[6:]:
            return
        self._show_look(look)
        if self._loaded is not None:
            self._loaded = self._loaded[:6] + self.look()

    def _ok(self) -> None:
        self.applyRequested.emit()
        self.accept()

    def values(self):
        """(horizontal pitch, display grid, snap to grid)."""
        return (self._sp_pitch.value(), self._chk_display.isChecked(), self._chk_snap.isChecked())

    def grid(self) -> tuple[int, int, int, int]:
        """(horizontal pitch, vertical pitch, origin x, origin y)."""
        return (
            self._sp_pitch.value(),
            self._sp_pitch_y.value(),
            self._sp_origin_x.value(),
            self._sp_origin_y.value(),
        )


class GuidelinesDialog(QtWidgets.QDialog):
    """View > Guidelines Setup."""

    #: Apply / OK: use these guidelines now
    applyRequested = QtCore.Signal()

    #: the note under the check boxes (what the canvas does with guidelines)
    ON_THE_PAGE = (
        "On the page: drag a guideline to move it, hold Ctrl when you let go to "
        "copy it, drop it at the page edge to delete it, drag from a ruler to add "
        "one. Double-click a guideline, or right-click it, for this window."
    )

    def __init__(
        self, vert: list[int], horiz: list[int], display: bool, snap: bool, parent=None, page: str = ""
    ):
        super().__init__(parent)
        self.setWindowTitle("Guidelines Setup" + (" - %s" % page if page else ""))
        self._vert = sorted(set(int(v) for v in vert))
        self._horiz = sorted(set(int(h) for h in horiz))
        self._loaded = None

        lay = QtWidgets.QVBoxLayout(self)
        grp = QtWidgets.QGroupBox("Direction")
        gl = QtWidgets.QHBoxLayout(grp)
        self._rb_h = QtWidgets.QRadioButton("Horizontal")
        self._rb_v = QtWidgets.QRadioButton("Vertical")
        self._rb_v.setChecked(True)
        gl.addWidget(self._rb_h)
        gl.addWidget(self._rb_v)
        lay.addWidget(grp)

        mid = QtWidgets.QHBoxLayout()
        self._list = QtWidgets.QListWidget()
        mid.addWidget(self._list, 1)
        side = QtWidgets.QVBoxLayout()
        self._sp_pos = QtWidgets.QSpinBox()
        self._sp_pos.setRange(1, 32767)  # CtDraw keeps 0 for 'no guideline'
        self._sp_pos.setSuffix(" px")
        self._sp_pos.setToolTip(
            "Horizontal: distance from the top of the page. Vertical: distance from the left edge."
        )
        side.addWidget(self._sp_pos)
        b_set = QtWidgets.QPushButton("Set")
        b_clear = QtWidgets.QPushButton("Clear")
        b_clear_all = QtWidgets.QPushButton("Clear All")
        for b in (b_set, b_clear, b_clear_all):
            side.addWidget(b)
        side.addStretch(1)
        mid.addLayout(side)
        lay.addLayout(mid)

        self._chk_display = QtWidgets.QCheckBox("Display guidelines")
        self._chk_display.setChecked(display)
        self._chk_snap = QtWidgets.QCheckBox("Snap to guidelines (F7)")
        self._chk_snap.setChecked(snap)
        lay.addWidget(self._chk_display)
        lay.addWidget(self._chk_snap)
        # (the page's own ways with guidelines, which this list does not show)
        from ..style.theme import note_label

        self.note = note_label(self.ON_THE_PAGE)
        lay.addWidget(self.note)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok
            | QtWidgets.QDialogButtonBox.Cancel
            | QtWidgets.QDialogButtonBox.Apply
        )
        buttons.accepted.connect(self._ok)
        buttons.rejected.connect(self.reject)
        buttons.button(QtWidgets.QDialogButtonBox.Apply).clicked.connect(self.applyRequested.emit)
        lay.addWidget(buttons)

        b_set.clicked.connect(self._set_pos)
        b_clear.clicked.connect(self._clear_pos)
        b_clear_all.clicked.connect(self._clear_all)
        self._rb_h.toggled.connect(lambda _=False: self._fill())
        self._list.currentTextChanged.connect(lambda t: self._sp_pos.setValue(int(t)) if t else None)
        self._fill()
        self._loaded = self.values()

    def select(self, axis: str, pos: int) -> None:
        """Show the direction of the guideline ``axis`` ('v' | 'h') and select ``pos`` in
        its list (the right-click menu's Guideline Position)."""
        (self._rb_v if axis == "v" else self._rb_h).setChecked(True)
        self._fill()
        for k in range(self._list.count()):
            if self._list.item(k).text() == str(int(pos)):
                self._list.setCurrentRow(k)
                break
        self._sp_pos.setValue(int(pos))
        self._sp_pos.setFocus()
        self._sp_pos.selectAll()

    def load(self, vert, horiz, display: bool, snap: bool, page: str = "") -> None:
        """Show the guidelines of the page shown (what was typed is replaced)."""
        self._vert = sorted(set(int(v) for v in vert))
        self._horiz = sorted(set(int(h) for h in horiz))
        self._chk_display.setChecked(bool(display))
        self._chk_snap.setChecked(bool(snap))
        self.setWindowTitle("Guidelines Setup" + (" - %s" % page if page else ""))
        self._fill()
        self._loaded = self.values()

    def has_pending_edits(self) -> bool:
        """A guideline or check box was changed and not applied."""
        return self._loaded is not None and self.values() != self._loaded

    def mark_applied(self) -> None:
        self._loaded = self.values()

    def _ok(self) -> None:
        self.applyRequested.emit()
        self.accept()

    def _cur(self) -> list[int]:
        return self._horiz if self._rb_h.isChecked() else self._vert

    def _fill(self) -> None:
        self._list.clear()
        for p in self._cur():
            self._list.addItem(str(p))

    def _set_pos(self) -> None:
        cur = self._cur()
        p = self._sp_pos.value()
        if p not in cur:
            if not cur:
                # the first guideline of a direction turns display and snap on
                # (CtDraw's Set)
                self._chk_display.setChecked(True)
                self._chk_snap.setChecked(True)
            cur.append(p)
            cur.sort()
        self._fill()

    def _clear_pos(self) -> None:
        """Clear the guideline at the position in the box (CtDraw's Clear
        reads the typed position; picking a row fills the box)."""
        cur = self._cur()
        p = self._sp_pos.value()
        if p not in cur:
            item = self._list.currentItem()
            p = int(item.text()) if item is not None else None
        if p in cur:
            cur.remove(p)
            self._fill()

    def _clear_all(self) -> None:
        del self._cur()[:]
        self._fill()

    def values(self):
        return (
            list(self._vert),
            list(self._horiz),
            self._chk_display.isChecked(),
            self._chk_snap.isChecked(),
        )


class NumberExpressionDialog(QtWidgets.QDialog):
    """Number tool: what the new number shows."""

    def __init__(self, tag_names=None, parent=None, expression: str = "", fmt: str = "", *, doc=None):
        super().__init__(parent)
        from ..style import theme
        from ..objprops.expression import ExpressionContext, ExpressionField

        self.setWindowTitle("New Number")
        self.setMinimumWidth(420)
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel("The tag or Cicode expression the number shows."))
        form = QtWidgets.QFormLayout()
        doc = doc if doc is not None else getattr(parent, "doc", None)
        # the document's context already lists its project's tags
        extra = [] if doc is not None else list(tag_names or ())
        try:
            ctx = (
                ExpressionContext.for_document(doc, extra)
                if doc is not None
                else ExpressionContext(None, extra)
            )
        except Exception:  # noqa: BLE001 -- completion is an aid
            ctx = ExpressionContext(None, extra)
        self._context = ctx
        self._expr = ExpressionField(ctx, self)
        self._expr.set_value(expression)
        self._expr.setPlaceholderText("For example LOOP_1_PV")
        self._expr.set_names(ctx.completion_model())
        form.addRow("Expression:", self._expr)
        self._fmt = QtWidgets.QLineEdit(fmt)
        self._fmt.setPlaceholderText("Tag format")
        self._fmt.setToolTip("# for each digit. Empty: the format of the tag.")
        form.addRow("Format:", self._fmt)
        lay.addLayout(form)
        lay.addWidget(theme.note_label("Example: ###.# or #.## EU"))
        self._error = theme.note_label("", "danger")
        self._error.hide()
        lay.addWidget(self._error)
        self._buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        lay.addWidget(self._buttons)
        self._expr.valueChanged.connect(self._sync_ok)
        self._fmt.textChanged.connect(self._sync_ok)
        self._sync_ok()
        self._expr.setFocus()

    def _sync_ok(self) -> None:
        # the page stores text in the Windows code page: a character outside
        # it would be saved as '?', so say which and keep OK disabled
        from ...format import textcodec

        bad = textcodec.unencodable(self._expr.text() + self._fmt.text())
        if bad:
            self._error.setText(
                "%s cannot be stored in a page. Remove it or use another character."
                % ", ".join("'%s'" % c for c in bad)
            )
        self._error.setVisible(bool(bad))
        from ..style import theme

        # the field that holds the character is framed (the line above says why)
        theme.mark_invalid(
            self._expr, self._error.text() if bad and textcodec.unencodable(self._expr.text()) else ""
        )
        theme.mark_invalid(
            self._fmt, self._error.text() if bad and textcodec.unencodable(self._fmt.text()) else ""
        )
        self._buttons.button(QtWidgets.QDialogButtonBox.Ok).setEnabled(
            bool(self._expr.text().strip()) and not bad
        )

    @staticmethod
    def compose(expression: str, fmt: str = "") -> str:
        """TXT_STR = ``expression`` or ``expression:format`` (a leading ':'
        typed in the format field is not doubled)."""
        expression = expression.strip()
        fmt = fmt.strip().lstrip(":").strip()
        return "%s:%s" % (expression, fmt) if fmt else expression

    def txt_str(self) -> str:
        return self.compose(self._expr.text(), self._fmt.text())


class ZoomWindow(QtWidgets.QDialog):
    """View > Show Zoom (F10): a floating magnifier following the cursor.
    Zoom In / Zoom Out (CtDraw: its control menu and Num+ / Num-) = the + / -
    buttons, the keys, the wheel or the right-click menu (2x .. 16x)."""

    FACTORS = (2, 4, 8, 16)

    def __init__(self, view, parent=None):
        super().__init__(parent, QtCore.Qt.Tool)
        self.setWindowTitle("Magnifier")  # (one name, as the menu and help)
        self._view = view
        # the magnification the user last chose (editor setting)
        try:
            f = int(app_settings().value("view/zoom_window_factor", 4))
        except TypeError, ValueError:
            f = 4
        self._factor = f if f in self.FACTORS else 4
        self._label = QtWidgets.QLabel()
        # a minimum size and an Expanding policy keep the window resizable; _update_view renders
        # at the label's current size, so the magnified image fills the window
        self._label.setMinimumSize(120, 90)
        self._label.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self._label.setAlignment(QtCore.Qt.AlignCenter)
        self._label.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self._label.customContextMenuRequested.connect(self._context_menu)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(_sp(0), _sp(0), _sp(0), _sp(0))
        lay.setSpacing(_sp(0))
        bar = QtWidgets.QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        self.btn_out = QtWidgets.QToolButton()
        self.btn_out.setText("−")
        self.btn_out.clicked.connect(self.zoom_out)
        self.btn_in = QtWidgets.QToolButton()
        self.btn_in.setText("+")
        self.btn_in.clicked.connect(self.zoom_in)
        # the wheel's state (ui/editor/viewnav.wheel_intent) and the notches gathered so far
        self._pad_at = -1.0
        self._wheel_acc = 0.0
        self.apply_key_settings()
        self.lbl_factor = QtWidgets.QLabel()
        self.lbl_factor.setMinimumWidth(28)
        self.lbl_factor.setAlignment(QtCore.Qt.AlignCenter)
        bar.addWidget(self.btn_out)
        bar.addWidget(self.lbl_factor)
        bar.addWidget(self.btn_in)
        bar.addStretch(1)
        lay.addLayout(bar)
        lay.addWidget(self._label)
        self._sync_factor()
        self.resize(224, 188)  # default; the user can resize from here
        self._last_key = None
        self._last_sp = None
        self._dirty = True
        self._sig_scene = None  # the scene whose `changed` signal we're listening to
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._update_view)
        # ~60 fps: the per-frame render is a cheap scene.render of a small region (the BSP
        # index culls to the few items under the cursor). Redundant frames are skipped below.
        self._timer.start(16)

    def factor(self) -> int:
        return self._factor

    def zoom_in(self) -> None:
        self._set_factor(self._factor * 2)

    def zoom_out(self) -> None:
        self._set_factor(self._factor // 2)

    def _set_factor(self, f: int) -> None:
        f = max(self.FACTORS[0], min(self.FACTORS[-1], int(f)))
        if f != self._factor:
            self._factor = f
            self._dirty = True
            app_settings().setValue("view/zoom_window_factor", f)
        self._sync_factor()

    def _sync_factor(self) -> None:
        self.lbl_factor.setText("%d×" % self._factor)
        self.btn_in.setEnabled(self._factor < self.FACTORS[-1])
        self.btn_out.setEnabled(self._factor > self.FACTORS[0])

    def _context_menu(self, pos) -> None:
        m = QtWidgets.QMenu(self)
        a_in = m.addAction("Zoom In", self.zoom_in)
        a_in.setEnabled(self._factor < self.FACTORS[-1])
        a_out = m.addAction("Zoom Out", self.zoom_out)
        a_out.setEnabled(self._factor > self.FACTORS[0])
        m.exec(self._label.mapToGlobal(pos))

    def apply_key_settings(self) -> None:
        """Settings > Keyboard > "Plus and minus zoom in the Bitmap Editor and the magnifier": the
        buttons' tips name the keys (the keys themselves are read at the press)."""
        from ..editor.view_consistency import bare_zoom_keys

        bare = bare_zoom_keys()
        self.btn_in.setToolTip("Zoom In (Ctrl+=%s)" % (", Num +" if bare else ""))
        self.btn_out.setToolTip("Zoom Out (Ctrl+-%s)" % (", Num -" if bare else ""))

    def wheelEvent(self, e):
        # the canvas's wheel rules: a zoom turn steps the ladder a notch at a time, Alt a quarter notch; with
        # Settings > Canvas > Mouse wheel: scrolls, only Ctrl + the wheel zooms (the magnifier
        # has nothing to scroll)
        from ..editor import viewnav as VN

        e.accept()
        intent = VN.wheel_intent(self, e, VN.WheelPrefs.from_settings())
        if intent[0] != "zoom" or not intent[1]:
            return
        self._wheel_acc += VN.wheel_steps(intent[1], intent[2])
        while self._wheel_acc >= 1.0:
            self._wheel_acc -= 1.0
            self.zoom_in()
        while self._wheel_acc <= -1.0:
            self._wheel_acc += 1.0
            self.zoom_out()

    def keyPressEvent(self, e):
        """Ctrl+= / Ctrl++ / Ctrl+- (the editor's Zoom In / Out keys), and the bare + / = /
        - keys (the Graphics Builder's Num + / Num -) while Settings > Keyboard says so."""
        from ..editor.view_consistency import bare_zoom_keys

        key = e.key()
        ctrl = bool(e.modifiers() & QtCore.Qt.ControlModifier)
        if key in (QtCore.Qt.Key_Plus, QtCore.Qt.Key_Equal) and (ctrl or bare_zoom_keys()):
            self.zoom_in()
        elif key == QtCore.Qt.Key_Minus and (ctrl or bare_zoom_keys()):
            self.zoom_out()
        else:
            super().keyPressEvent(e)

    def _mark_dirty(self, *args) -> None:
        self._dirty = True

    def _update_view(self) -> None:
        if not self.isVisible():
            return
        scene = self._view.scene()
        if scene is None:
            return
        # follow the scene's `changed` signal so an EDIT that doesn't move the cursor -- a keyboard
        # NUDGE (arrow keys), a property change, undo/redo -- still re-renders the magnifier (the
        # key below would be unchanged). (Re)connect when the scene object changes (new document).
        if scene is not self._sig_scene:
            if self._sig_scene is not None:
                try:
                    self._sig_scene.changed.disconnect(self._mark_dirty)
                except RuntimeError, TypeError:
                    pass
            try:
                scene.changed.connect(self._mark_dirty)
            except RuntimeError, TypeError:
                pass
            self._sig_scene = scene
            self._dirty = True
        view = self._view
        gp = QtGui.QCursor.pos()
        vp_pt = view.viewport().mapFromGlobal(gp)
        if view.viewport().rect().contains(vp_pt):
            sp = view.mapToScene(vp_pt)
            self._last_sp = sp
        else:
            # cursor is off the canvas (e.g. dragging the magnifier's own resize edge) -- keep
            # showing the LAST point so a resize / selection change still re-renders instead of
            # freezing until the mouse returns to the canvas.
            sp = self._last_sp
            if sp is None:
                return
        sel = tuple(getattr(view, "_selection", ()) or ())
        # skip when nothing changed; the key includes the SIZE (so a resize re-renders crisply at
        # the new size) and the SELECTION (so selecting/deselecting updates the overlay at rest).
        # A scene EDIT sets _dirty (above) so it re-renders even when the key is unchanged.
        key = (round(sp.x()), round(sp.y()), self._factor, self._label.width(), self._label.height(), sel)
        if key == self._last_key and not self._dirty:
            return
        self._last_key = key
        self._dirty = False
        w = self._label.width() / self._factor
        h = self._label.height() / self._factor
        src = QtCore.QRectF(sp.x() - w / 2, sp.y() - h / 2, w, h)
        img = QtGui.QImage(self._label.width(), self._label.height(), QtGui.QImage.Format_RGB32)
        surround = getattr(scene, "surround_colour", None)
        from ..style.theme import color

        img.fill(surround() if surround is not None else color("desk"))
        painter = QtGui.QPainter(img)
        # match the main view's quality: without these the magnified scene is jagged (no edge AA)
        # and bitmaps blocky.
        painter.setRenderHints(
            QtGui.QPainter.Antialiasing
            | QtGui.QPainter.SmoothPixmapTransform
            | QtGui.QPainter.TextAntialiasing,
            True,
        )
        # the canvas's own look: the surround, the page edge and the grid
        chrome = getattr(scene, "editor_chrome", None)
        if chrome is not None:
            with chrome():
                scene.render(painter, QtCore.QRectF(0, 0, img.width(), img.height()), src)
        else:
            scene.render(painter, QtCore.QRectF(0, 0, img.width(), img.height()), src)
        self._draw_overlay(painter, src)
        painter.end()
        self._label.setPixmap(QtGui.QPixmap.fromImage(img))

    def _draw_overlay(self, painter: QtGui.QPainter, src: QtCore.QRectF) -> None:
        """Draw the canvas adorners into the magnifier."""
        fg = getattr(self._view, "drawForeground", None)
        if fg is None:
            return
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.scale(self._factor, self._factor)
        painter.translate(-src.left(), -src.top())
        try:
            fg(painter, src)
        except Exception:  # noqa: BLE001 -- never let the overlay break the render
            pass
        painter.restore()


# ---------------------------------------------------------------------------
# Object Properties sheet (CtDraw parity)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Page Properties (CtDraw parity)
# ---------------------------------------------------------------------------


class _NoPagesDelegate(QtWidgets.QStyledItemDelegate):
    """The Update Pages tree's rows of projects with no pages are drawn disabled, their
    check box too. The item itself stays enabled: a disabled item would disable the
    projects it includes below it."""

    def __init__(self, pages_role, parent=None):
        super().__init__(parent)
        self._role = pages_role

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        if not int(index.siblingAtColumn(0).data(self._role) or 0):
            option.state &= ~QtWidgets.QStyle.State_Enabled


class UpdatePagesDialog(QtWidgets.QDialog):
    """Tools > Update Pages."""

    #: the tree's roles: the project folder and its registered pages
    DIR_ROLE = QtCore.Qt.UserRole
    PAGES_ROLE = QtCore.Qt.UserRole + 1

    def __init__(self, start_dir=None, parent=None, projects=None, library_dir=None, chain=None):
        super().__init__(parent)
        from ..style import theme
        from ..prefs.settings import UPDATE_PROCESSES_RANGE, stored_bool

        self.setWindowTitle("Update Pages")
        self._catalog = None
        if projects is None:
            projects = self._install_projects()
        projects = sorted(((str(n), os.path.abspath(d)) for n, d in projects), key=lambda p: p[0].lower())
        self._listed = [d for _n, d in projects]
        # (a page outside the install's projects, or a folder picked with Browse last
        # time: its own folder is a root too)
        for loose in (start_dir, str(app_settings().value("updatepages/root_dir", "") or "")):
            if (
                loose
                and os.path.isdir(loose)
                and not any(_same_dir(d, loose) for _n, d in projects)
                and self._loose_root(loose)
            ):
                projects.append((os.path.basename(os.path.normpath(loose)), os.path.abspath(loose)))
        # (the form scrolls when the window is too short for it: the notes and buttons never
        # cover the project list; OK and Cancel stay in view below it)
        lay, outer = theme.scroll_body(self)
        intro = QtWidgets.QLabel(
            "Update the pages that use genies, symbols or templates changed since the "
            "pages were saved. Compile afterwards. Pages open in the Graphics Builder "
            "are skipped."
        )
        intro.setWordWrap(True)
        lay.addWidget(intro)
        box = QtWidgets.QGroupBox("Projects")
        bl = QtWidgets.QVBoxLayout(box)
        form = QtWidgets.QFormLayout()
        self._root = QtWidgets.QComboBox()
        self._root.setToolTip(
            "Any project of the install, also one with no pages of its "
            "own. The projects it includes are listed below."
        )
        for name, d in projects:
            self._root.addItem(name, d)
            # (two projects of one name, a listed one and a browsed copy, tell apart here)
            self._root.setItemData(self._root.count() - 1, d, QtCore.Qt.ToolTipRole)
        self._browse = QtWidgets.QPushButton("Browse…")
        self._browse.setToolTip("Pick a project folder that is not in the list")
        self._browse.clicked.connect(self._browse_root)
        rrow = QtWidgets.QHBoxLayout()
        rrow.addWidget(self._root, 1)
        rrow.addWidget(self._browse)
        form.addRow("Root project:", rrow)
        bl.addLayout(form)
        bl.addWidget(QtWidgets.QLabel("Projects to update:"))
        self._tree = QtWidgets.QTreeWidget()
        self._tree.setColumnCount(2)
        # (a header, so the user can size the columns: the widths are remembered)
        self._tree.setHeaderLabels(["Project", "Pages"])
        self._tree.setRootIsDecorated(True)
        self._tree.setUniformRowHeights(True)
        self._tree.setMinimumHeight(150)
        from ..tables import make_resizable

        # (the names take what the page counts leave; always the view's width)
        make_resizable(self._tree, "UpdatePagesDialog/projects", stretch=0, fit_view=True)
        self._tree.setItemDelegate(_NoPagesDelegate(self.PAGES_ROLE, self._tree))
        bl.addWidget(self._tree, 1)
        bl.addWidget(theme.note_label("Include and System are AVEVA's projects. They are never changed."))
        row = QtWidgets.QHBoxLayout()
        self._all = QtWidgets.QPushButton("Select All")
        self._none = QtWidgets.QPushButton("Clear All")
        self._all.clicked.connect(lambda: self._tick_all(True))
        self._none.clicked.connect(lambda: self._tick_all(False))
        row.addWidget(self._all)
        row.addWidget(self._none)
        row.addStretch(1)
        bl.addLayout(row)
        self._count = QtWidgets.QLabel()
        bl.addWidget(self._count)
        # (the choices that decide what the run writes come first, the project list after:
        # under a tall list they would be seen only after scrolling)
        what = QtWidgets.QGroupBox("Update")
        wl = QtWidgets.QVBoxLayout(what)
        self._tpl = QtWidgets.QCheckBox("Page templates")
        self._gen = QtWidgets.QCheckBox("Genies and super genies")
        self._gen.setToolTip(
            "Placed genies whose genie changed, and the pages made from a changed super genie"
        )
        self._sym = QtWidgets.QCheckBox("Symbols")
        self._sym.setToolTip(
            "Placed symbols whose symbol changed. Each keeps its position and takes the new size and shape."
        )
        self._tpl.setChecked(True)
        self._gen.setChecked(True)
        self._sym.setChecked(True)
        # [Animator] TemplateUpdate: an updated page also takes the template's
        # page properties (off unless the install sets it)
        try:
            from ...io.citect_config import template_update_enabled

            props_default = template_update_enabled()
        except Exception:  # noqa: BLE001
            props_default = False
        self._tpl_props = QtWidgets.QCheckBox(
            "Also apply the template's page size, page colour and page properties"
        )
        self._tpl_props.setToolTip("Replaces the page's own values with the template's")
        self._tpl_props.setChecked(props_default)
        self._tpl.toggled.connect(self._tpl_props.setEnabled)
        # the genies and templates of the ticked projects' libraries that place a changed
        # genie or template are saved again first (genieusage.chain_plan;
        # window/library._update_pages): with the genies or the templates (a template built
        # on a changed template is saved by it too)
        self._chain = QtWidgets.QCheckBox("Save the library genies and templates in the chain first")
        self._chain.setToolTip(
            (
                "Genies and templates that place a changed genie or template are saved again first, "
                "deepest first, as Update Users does. Then the pages are updated."
            )
        )
        self._chain_stored = stored_bool(app_settings().value("updatepages/chain", True), True)
        self._chain_given = chain
        self._chain.setChecked(self._chain_stored if chain is None else bool(chain))

        def chain_on(_on=None):
            self._chain.setEnabled(self._gen.isChecked() or self._tpl.isChecked())

        self._gen.toggled.connect(chain_on)
        self._tpl.toggled.connect(chain_on)
        # (a genie whose stamps say current but whose copies of the genies nested in it were
        # kept by an earlier save: composed again in full; each genie is composed to check,
        # so it is slow, and off by default)
        self._chain_all = QtWidgets.QCheckBox(
            "Also compose their nested genies again where they differ from their libraries"
        )
        self._chain_all.setToolTip(
            (
                "Composes each library genie to compare it with its libraries (slow). A genie whose "
                "nested genies differ is saved with them composed again, as Edit > Library > Compose "
                "Nested Genies Again does"
            )
        )
        self._chain_all.setChecked(
            stored_bool(app_settings().value("updatepages/chain_compose_all", False), False)
        )

        def chain_all_on(_on=None):
            self._chain_all.setEnabled(self._chain.isChecked() and self._chain.isEnabled())

        chain_all_on()
        self._chain.toggled.connect(chain_all_on)
        self._gen.toggled.connect(chain_all_on)
        self._tpl.toggled.connect(chain_all_on)
        # (each indented box under the box it belongs to; the chain's box, which goes with
        # the genies and the templates, after them, its own sub box one step in)
        for parent_box, sub in (
            (self._tpl, self._tpl_props),
            (self._gen, None),
            (self._sym, None),
            (self._chain, self._chain_all),
        ):
            if parent_box is not None:
                wl.addWidget(parent_box)
            if sub is not None:
                indent = QtWidgets.QHBoxLayout()
                indent.addSpacing(20)
                indent.addWidget(sub)
                wl.addLayout(indent)
        # (every page opened, composed again and saved, current or not: off each time, it
        # rewrites the whole project; updatepages.update_pages' force)
        self._force = QtWidgets.QCheckBox("Update every page, also the ones that are up to date")
        self._force.setToolTip(
            (
                "Opens each page, composes everything ticked above again from its library, and saves "
                "it. Use it when a page does not show its library although it reads up to date. Much "
                "slower"
            )
        )
        self._force.setChecked(False)
        wl.addWidget(self._force)
        lay.addWidget(what)
        self._backup = QtWidgets.QCheckBox("Back up each page before it is changed")
        self._backup.setChecked(True)
        # (the tips say where the backup goes and what Automatic does)
        from ...infra import backups
        from ..prefs.settings import HELPER_MB, UPDATE_PER_HELPER_RANGE, whole

        self._backup.setToolTip(
            "A copy of each page and its project database rows goes to "
            "%s. Tools > Restore Backup puts them back" % backups.backups_root()
        )
        lay.addWidget(self._backup)
        pform = QtWidgets.QFormLayout()
        self._procs = QtWidgets.QSpinBox()
        self._procs.setRange(*UPDATE_PROCESSES_RANGE)
        self._procs.setSpecialValueText("Automatic")
        self._procs.setToolTip(
            (
                "Automatic: half the processors, at most 4, fewer when memory is short. Each helper "
                "takes about %d MB while it runs and gets at least %d pages, so a small run uses fewer"
            )
            % (HELPER_MB, whole("opt/update_pages_per_helper", UPDATE_PER_HELPER_RANGE))
        )
        try:
            # (as text: Qt reads a garbled number as 0, not as an error)
            self._procs.setValue(int(str(app_settings().value("opt/update_processes", 0)).strip()))
        except TypeError, ValueError:  # swallow-ok: a garbled setting shows Automatic
            self._procs.setValue(0)
        # (the value is stored by accept(): Cancel leaves the setting as it was)
        pform.addRow("Helper processes:", self._procs)
        lay.addLayout(pform)
        lay.addWidget(box, 1)
        # (Tab goes down the window as it is laid out: the choices, then the projects)
        chain = [w for w in what.findChildren(QtWidgets.QAbstractButton)] + [
            self._backup,
            self._procs,
            self._root,
        ]
        for a_w, b_w in zip(chain, chain[1:]):
            QtWidgets.QWidget.setTabOrder(a_w, b_w)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        self._ok = buttons.button(QtWidgets.QDialogButtonBox.Ok)
        self._ok.setText("Update Pages")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self._root.setCurrentIndex(self._start_index(start_dir, library_dir))
        self._tree.itemChanged.connect(lambda *_a: self._update_count())
        self._root.currentIndexChanged.connect(lambda _i: self._rebuild())
        self._rebuild()
        self.resize(max(self.sizeHint().width(), 480), max(self.sizeHint().height(), 560))

    # -- the projects ---------------------------------------------------------------------
    def _install_projects(self) -> list:
        """The projects of the install (the page browser's list), without the implicit
        Include and System."""
        try:
            from ...io.catalog import catalog_for

            self._catalog = catalog_for()
            return [
                (p.name, p.dir)
                for p in self._catalog.projects()
                if p.exists and not p.is_folder and not p.is_implicit
            ]
        except Exception:  # noqa: BLE001
            from ...infra import logsetup

            logsetup.note_exception("dialogs.UpdatePagesDialog/projects")
            return []

    def _loose_root(self, d: str) -> bool:
        """A start folder that is none of the listed projects can be a root of its own (a
        page outside the install's projects), unless it is the implicit Include or System
        (AVEVA's projects: never changed) or a project of another install (not listed):
        then the next choice of root is taken."""
        from ...format.projects import IMPLICIT_INCLUDE_PROJECTS

        name = os.path.basename(os.path.normpath(d)).lower()
        if name in {n.lower() for n in IMPLICIT_INCLUDE_PROJECTS}:
            return False
        if self._catalog is not None:
            try:
                # (every project of this install that may be a root is listed already)
                return self._catalog.project_for_dir(d) is None
            except Exception:  # noqa: BLE001
                from ...infra import logsetup

                logsetup.note_exception("dialogs.UpdatePagesDialog/loose_root")
        return True

    def _browse_root(self) -> None:
        """Browse...: any folder as the root. A listed project is selected; Include,
        System and the projects of another install are refused (never changed here)."""
        start = os.path.dirname(str(self._root.currentData() or "")) or ""
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Root Project", start)
        if d:
            self.add_root(d)

    def add_root(self, d: str) -> bool:
        """Make folder ``d`` the root (Browse...): False when it cannot be one."""
        d = os.path.abspath(d)
        for k in range(self._root.count()):
            if _same_dir(self._root.itemData(k), d):
                self._root.setCurrentIndex(k)
                return True
        if not self._loose_root(d):
            QtWidgets.QMessageBox.information(
                self,
                "Update Pages",
                "%s cannot be the root. Include, System and the projects of another "
                "install are never changed here." % os.path.basename(os.path.normpath(d)),
            )
            return False
        self._root.addItem(os.path.basename(os.path.normpath(d)), d)
        self._root.setItemData(self._root.count() - 1, d, QtCore.Qt.ToolTipRole)
        self._root.setCurrentIndex(self._root.count() - 1)
        return True

    def _user_dir(self, d: str) -> str:
        """The User folder of the install ``d`` belongs to (its include names resolve
        there)."""
        if self._catalog is not None:
            try:
                info = self._catalog.project_for_dir(d)
                if info is not None and info.install is not None:
                    return info.install.user_dir
            except Exception:  # noqa: BLE001
                from ...infra import logsetup

                logsetup.note_exception("dialogs.UpdatePagesDialog/user_dir")
        return os.path.dirname(os.path.abspath(d))

    def _start_index(self, start_dir, library_dir=None) -> int:
        """The root project to start with."""
        n = self._root.count()
        if start_dir:
            for k in range(n):
                if _same_dir(self._root.itemData(k), start_dir):
                    return k
        cands = []
        saved = str(app_settings().value("updatepages/root_dir", "") or "")
        if saved:
            cands += [k for k in range(n) if _same_dir(self._root.itemData(k), saved)][:1]
        names = [str(app_settings().value("updatepages/root", "") or "")]
        try:
            from ...io.citect_config import ini_value

            names.append(ini_value("ctedit", "lastdatabase"))
        except Exception:  # noqa: BLE001
            from ...infra import logsetup

            logsetup.note_exception("dialogs.UpdatePagesDialog/lastdatabase")
        for name in names:
            name = (name or "").strip().lower()
            if name:
                cands += [k for k in range(n) if self._root.itemText(k).lower() == name][:1]
        if not library_dir:
            return cands[0] if cands else 0
        for k in cands:
            if self._includes(k, library_dir):
                return k
        for k in range(n):
            if not _same_dir(self._root.itemData(k), library_dir) and self._includes(k, library_dir):
                return k
        for k in range(n):
            if _same_dir(self._root.itemData(k), library_dir):
                return k
        return cands[0] if cands else 0

    def _includes(self, k: int, d: str) -> bool:
        """The root at index ``k`` is project ``d`` or includes it (at any depth)."""
        root = str(self._root.itemData(k) or "")
        if _same_dir(root, d):
            return True
        try:
            from ...format.projects import include_search_dirs

            return any(_same_dir(x, d) for x in include_search_dirs(root))
        except Exception:  # noqa: BLE001
            from ...infra import logsetup

            logsetup.note_exception("dialogs.UpdatePagesDialog/includes")
            return False

    def root_name(self) -> str:
        return self._root.currentText()

    def _ticks_key(self) -> str:
        """The settings key of the root's ticks: by name for a listed project, by folder
        for a browsed one (a copy of a project has its own)."""
        d = str(self._root.currentData() or "")
        if d and not any(_same_dir(x, d) for x in self._listed):
            return "updatepages/ticks/" + os.path.normcase(os.path.abspath(d))
        return "updatepages/ticks/" + self.root_name().lower()

    def _unticked_saved(self) -> set:
        import json

        try:
            got = json.loads(str(app_settings().value(self._ticks_key(), "[]") or "[]"))
            return {str(n).lower() for n in got} if isinstance(got, list) else set()
        except TypeError, ValueError:  # swallow-ok: a garbled setting ticks every project
            return set()

    def _rebuild(self) -> None:
        """The include tree of the chosen root (each project once, where the include rows
        first meet it); a project with pages starts ticked unless it was unticked the
        last time this root ran."""
        from ...io import updatepages
        from ..names import plural

        d = self._root.currentData()
        self._tree.blockSignals(True)
        try:
            self._tree.clear()
            if not d:
                return
            try:
                node = updatepages.include_tree(d, self._user_dir(d))
            except Exception:  # noqa: BLE001
                from ...infra import logsetup

                logsetup.note_exception("dialogs.UpdatePagesDialog/include_tree")
                node = updatepages.ProjectNode(d, self.root_name(), 0)
            unticked = self._unticked_saved()
            dim = self.palette().color(QtGui.QPalette.Disabled, QtGui.QPalette.Text)

            def add(parent, n):
                it = QtWidgets.QTreeWidgetItem([n.name, plural(n.pages, "page") if n.pages else "No pages"])
                it.setData(0, self.DIR_ROLE, n.dir)
                it.setData(0, self.PAGES_ROLE, int(n.pages))
                it.setToolTip(0, n.dir)
                if n.pages:
                    it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
                    it.setCheckState(
                        0, QtCore.Qt.Unchecked if n.name.lower() in unticked else QtCore.Qt.Checked
                    )
                else:
                    # (kept, so the tree reads right; nothing to tick)
                    it.setFlags(it.flags() & ~QtCore.Qt.ItemIsUserCheckable)
                    it.setCheckState(0, QtCore.Qt.Unchecked)
                    it.setForeground(0, dim)
                    it.setForeground(1, dim)
                    it.setToolTip(1, "This project has no pages of its own to update")
                if parent is None:
                    self._tree.addTopLevelItem(it)
                else:
                    parent.addChild(it)
                for c in n.children:
                    add(it, c)

            add(None, node)
            self._tree.expandAll()
        finally:
            self._tree.blockSignals(False)
            # (also with no root at all: the count says so and Update Pages is off)
            self._update_count()

    def _items(self):
        it = QtWidgets.QTreeWidgetItemIterator(self._tree)
        while it.value() is not None:
            yield it.value()
            it += 1

    def _tickable(self, item) -> bool:
        return bool(item.flags() & QtCore.Qt.ItemIsUserCheckable)

    def _tick_all(self, on: bool) -> None:
        self._tree.blockSignals(True)
        try:
            for item in self._items():
                if self._tickable(item):
                    item.setCheckState(0, QtCore.Qt.Checked if on else QtCore.Qt.Unchecked)
        finally:
            self._tree.blockSignals(False)
        self._update_count()

    def _update_count(self) -> None:
        from ..names import plural

        pages = projects = 0
        for item in self._items():
            if self._tickable(item) and item.checkState(0) == QtCore.Qt.Checked:
                n = int(item.data(0, self.PAGES_ROLE) or 0)
                if n:
                    pages += n
                    projects += 1
        self._count.setText(
            "%s in %s will be checked." % (plural(pages, "page"), plural(projects, "project"))
            if pages
            else "No pages will be checked."
        )
        self._ok.setEnabled(pages > 0)

    def accept(self) -> None:
        """Remember the root and the projects left unticked for it (a project that gains
        pages later starts ticked), the chain box and the helper processes (the same setting
        as Settings > Pages and libraries > Update Pages; Cancel leaves it as it was)."""
        import json

        st = app_settings()
        st.setValue("updatepages/root", self.root_name())
        # (a state given for this run, by the Template Saved question, is remembered only
        # when the box was changed)
        if self._chain_given is None or self._chain.isChecked() != bool(self._chain_given):
            st.setValue("updatepages/chain", self._chain.isChecked())
        st.setValue("updatepages/chain_compose_all", self._chain_all.isChecked())
        st.setValue("opt/update_processes", int(self._procs.value()))
        # (a root that is no listed project of the install comes back next time)
        d = str(self._root.currentData() or "")
        st.setValue("updatepages/root_dir", "" if any(_same_dir(x, d) for x in self._listed) else d)
        unticked = [
            item.text(0)
            for item in self._items()
            if self._tickable(item) and item.checkState(0) != QtCore.Qt.Checked
        ]
        st.setValue(self._ticks_key(), json.dumps(unticked))
        super().accept()

    # -- the answers ----------------------------------------------------------------------
    def projects(self) -> list:
        """The ticked projects with pages, in the tree's order (the root first)."""
        return [
            item.data(0, self.DIR_ROLE)
            for item in self._items()
            if self._tickable(item)
            and item.checkState(0) == QtCore.Qt.Checked
            and int(item.data(0, self.PAGES_ROLE) or 0)
        ]

    def chain_projects(self) -> list:
        """The projects whose libraries 'Save the library genies in the chain first' reads:
        the ticked ones and every listed one with no pages (a library project of the tree:
        nothing to tick), in the tree's order. Include and System are never listed."""
        return [
            item.data(0, self.DIR_ROLE)
            for item in self._items()
            if (self._tickable(item) and item.checkState(0) == QtCore.Qt.Checked)
            or not int(item.data(0, self.PAGES_ROLE) or 0)
        ]

    def processes(self) -> int:
        """The helper processes (0 = automatic: settings.update_parts)."""
        return int(self._procs.value())

    def options(self) -> dict:
        """What to update; ``chain`` = save the library genies and templates in the chain
        first (with the genies or the templates)."""
        chain = (self._gen.isChecked() or self._tpl.isChecked()) and self._chain.isChecked()
        return dict(
            templates=self._tpl.isChecked(),
            genies=self._gen.isChecked(),
            symbols=self._sym.isChecked(),
            backup=self._backup.isChecked(),
            page_properties=self._tpl.isChecked() and self._tpl_props.isChecked(),
            chain=chain,
            chain_compose_all=chain and self._chain_all.isChecked(),
            force=self._force.isChecked(),
        )


def _same_dir(a, b) -> bool:
    if not a or not b:
        return False
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


# ------------------------------------------------------------ page templates
#: the page background of a new page whose template sets none, for a folder
#: without a PROJECT.CIT (a project's own is its Page Defaults colour)
DEFAULT_PAGE_BACKGROUND = 0xE5E5E5

#: the Page Defaults resolution list (CtDraw's texts), in PROJECT.CIT 'Res' order
_DEFAULTS_RESOLUTIONS = (
    ("Default", "Default (this screen's resolution)"),
    ("VGA", "VGA (640x480, 4:3)"),
    ("SVGA", "SVGA (800x600, 4:3)"),
    ("XGA", "XGA (1024x768, 4:3)"),
    ("SXGA", "SXGA (1280x1024, 5:4)"),
    ("User", "User Defined"),
    ("WUXGA", "WUXGA (1920x1200, 16:10)"),
    ("HD1080", "HD1080 (1920x1080, 16:9)"),
    ("HD768", "HD768 (1366x768, 16:9)"),
    ("UHD4K", "UHD4K (3840x2160, 16:9)"),
)


def screen_resolution() -> str:
    """The template resolution matching the primary screen ('Default' in
    the Graphics Builder), 'HD1080' when none matches."""
    from ...format.template_place import RESOLUTIONS

    try:
        s = QtWidgets.QApplication.primaryScreen().size()
        for name, w, h in RESOLUTIONS:
            if (w, h) == (s.width(), s.height()):
                return name
    except Exception:  # noqa: BLE001
        pass
    return "HD1080"


def page_defaults(project_dir: str | None = None, resolve_default: bool = True) -> dict:
    """File > Page Defaults for a project."""
    d = None
    if project_dir:
        try:
            from ...io import projectcit

            d = projectcit.page_defaults(project_dir)
        except Exception:  # noqa: BLE001 -- an unreadable PROJECT.CIT: the fallback
            d = None
    if d is not None:
        d = dict(d, source="project")
        if d["background"] is None:
            d["background"] = DEFAULT_PAGE_BACKGROUND
    else:
        try:
            from ...io.citect_config import template_defaults

            ini = template_defaults()
        except Exception:  # noqa: BLE001
            ini = {"style": "Tab_Style_1", "resolution": "Default", "title_bar": False}
        st = app_settings()
        d = {
            "style": str(st.value("pagedefaults/style", ini["style"]) or ini["style"]),
            "resolution": str(st.value("pagedefaults/resolution", ini["resolution"]) or ini["resolution"]),
            "title_bar": st.value("pagedefaults/title_bar", ini["title_bar"], type=bool),
            "background": int(st.value("pagedefaults/background", DEFAULT_PAGE_BACKGROUND, type=int))
            & 0xFFFFFFFF,
            "source": "settings",
        }
    if resolve_default and d["resolution"].lower() == "default":
        d["resolution"] = screen_resolution()
    return d


def set_page_defaults(values: dict, project_dir: str | None = None) -> str:
    """Store Page Defaults ``values`` for project ``project_dir``: into its
    PROJECT.CIT when it has one (returns 'project'), else into ctgedit's
    settings ('settings'). '' = the project's PROJECT.CIT could not be
    written (nothing stored)."""
    if project_dir:
        from ...io import projectcit

        if projectcit.find(project_dir) is not None:
            return "project" if projectcit.set_page_defaults(project_dir, values) else ""
    st = app_settings()
    for key in ("style", "resolution", "title_bar", "background"):
        if key in values:
            st.setValue("pagedefaults/" + key, values[key])
    return "settings"


class UseTemplateDialog(QtWidgets.QDialog):
    """File > New > Page: pick a template."""

    ALL = TemplatePicker.ALL

    def __init__(
        self,
        project_dir: str,
        defaults: dict | None = None,
        parent=None,
        catalog: TemplateCatalog | None = None,
        title: str = "New Page from Template",
        *,
        mode: str = "new",
        current=None,
    ):
        super().__init__(parent)
        self._mode = mode
        choose = mode == "choose"
        self.setWindowTitle("Select Template" if choose else title)
        self.setMinimumSize(760, 480)
        self.edit_requested = False
        d = dict(defaults or page_defaults(project_dir))
        self._catalog = catalog or TemplateCatalog(project_dir)
        self._background = d.get("background")
        lay = QtWidgets.QVBoxLayout(self)
        where = QtWidgets.QLabel(
            "Templates of project %s and its included projects."
            % os.path.basename(os.path.normpath(project_dir))
        )
        where.setWordWrap(True)
        lay.addWidget(where)
        self.picker = TemplatePicker(
            project_dir,
            self,
            catalog=self._catalog,
            background=self._background,
            style=d.get("style", ""),
            resolution=d.get("resolution", ""),
            title_bar=bool(d.get("title_bar", False)),
        )
        lay.addWidget(self.picker, 1)
        # the picker's controls (callers and tests read them here)
        self.cb_style, self.cb_res, self.list = (self.picker.cb_style, self.picker.cb_res, self.picker.list)
        self.chk_titlebar, self.preview, self.info = (
            self.picker.chk_titlebar,
            self.picker.preview,
            self.picker.info,
        )
        self.empty = self.picker.note
        self.chk_linked = QtWidgets.QCheckBox("Linked")
        self.chk_linked.setToolTip(
            "Keep the page linked to the template. Template objects stay locked and follow "
            "template changes. The link cannot be added later."
        )
        self.chk_linked.setChecked(app_settings().value("newpage/linked", True, type=bool))
        self.picker.side.insertWidget(self.picker.side.count() - 1, self.chk_linked)
        self.chk_linked.setVisible(not choose)
        self.buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        self.ok_button = self.buttons.button(QtWidgets.QDialogButtonBox.Ok)
        self.ok_button.setText("Select" if choose else "Create Page")
        self.edit_button = self.buttons.addButton("Edit Template", QtWidgets.QDialogButtonBox.ActionRole)
        self.edit_button.setToolTip("Open the selected template for editing")
        self.edit_button.setVisible(not choose)
        self.buttons.accepted.connect(self._ok)
        self.buttons.rejected.connect(self.reject)
        self.edit_button.clicked.connect(self._edit)
        lay.addWidget(self.buttons)
        self.picker.changed.connect(self._sync)
        self.picker.activated.connect(self._ok)
        if current:
            path, element = current
            self.picker.set_current(library_stem(path), element)
        self._sync()

    def _sync(self) -> None:
        ok = self.picker.value() is not None
        self.ok_button.setEnabled(ok)
        self.edit_button.setEnabled(ok)

    # -- results ------------------------------------------------------------
    def template_ref(self) -> str:
        """The page's template reference, '<style>.<element>.<ctt'."""
        return self.picker.template_ref()

    def template_element(self):
        """(library path, element name) of the chosen template, or None."""
        return self.picker.value()

    def linked(self) -> bool:
        return self.chk_linked.isChecked()

    def _ok(self) -> None:
        if self.picker.value() is None:
            return
        if self._mode != "choose":
            app_settings().setValue("newpage/linked", self.chk_linked.isChecked())
        self.accept()

    def _edit(self) -> None:
        if self.picker.value() is None:
            return
        self.edit_requested = True
        self.accept()


class PageDefaultsDialog(QtWidgets.QDialog):
    """File > Page Defaults."""

    def __init__(self, project_dir: str, parent=None, catalog: TemplateCatalog | None = None):
        super().__init__(parent)
        from ..style import theme
        from ..colour_picker import ColourButton

        self.setWindowTitle("Page Defaults")
        self._project_dir = project_dir
        self.stored_in = ""
        self._catalog = catalog or TemplateCatalog(project_dir)
        d = page_defaults(project_dir, resolve_default=False)
        self._source = d["source"]
        self._background = d["background"]
        lay = QtWidgets.QVBoxLayout(self)
        name = os.path.basename(os.path.normpath(project_dir))
        where = QtWidgets.QLabel(
            "New pages of project %s start with these settings." % name
            if self._source == "project"
            else "%s has no project file, so these defaults are kept in ctgedit's settings." % name
        )
        where.setWordWrap(True)
        lay.addWidget(where)
        grp = QtWidgets.QGroupBox("Template")
        form = QtWidgets.QFormLayout(grp)
        self.cb_style = QtWidgets.QComboBox()
        self.cb_style.setEditable(False)
        for s in self._catalog.styles():
            self.cb_style.addItem(s, s)
        want = self._catalog.style_name(d["style"])
        i = self.cb_style.findData(want, QtCore.Qt.UserRole, QtCore.Qt.MatchFixedString)
        if i < 0 and d["style"]:
            self.cb_style.addItem("%s (not found)" % d["style"], d["style"])
            i = self.cb_style.count() - 1
        self.cb_style.setCurrentIndex(max(0, i))
        self.cb_res = QtWidgets.QComboBox()
        for res, label in _DEFAULTS_RESOLUTIONS:
            self.cb_res.addItem(label, res)
        self.chk_titlebar = QtWidgets.QCheckBox("Designed for showing title bar")
        self.chk_titlebar.setChecked(bool(d["title_bar"]))
        form.addRow("Style:", self.cb_style)
        form.addRow("Resolution:", self.cb_res)
        form.addRow(self.chk_titlebar)
        lay.addWidget(grp)
        row = QtWidgets.QHBoxLayout()
        bform = QtWidgets.QFormLayout()
        self.btn_colour = ColourButton(
            self._background & 0xFFFFFF, title="Page Colour", flash=False, transparent=False
        )
        self.btn_colour.changed.connect(self._colour_picked)
        bform.addRow("Page colour:", self.btn_colour)
        row.addLayout(bform)
        self.preview = QtWidgets.QLabel()
        self.preview.setFixedSize(_preview_size())
        row.addWidget(self.preview)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(theme.note_label("A template with its own page colour keeps it."))
        self._status = theme.note_label("", "danger")
        self._status.hide()
        lay.addWidget(self._status)
        self._buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        self._buttons.accepted.connect(self._ok)
        self._buttons.rejected.connect(self.reject)
        lay.addWidget(self._buttons)
        self.cb_style.currentIndexChanged.connect(lambda _i: self._update_preview())
        self.cb_res.currentIndexChanged.connect(lambda _i: self._update_preview())
        self.chk_titlebar.toggled.connect(lambda _b: self._update_preview())
        self.set_resolution(d["resolution"])
        self._update_preview()

    def set_resolution(self, res: str) -> None:
        want = (res or "").strip().lower()
        want = "uhd4k" if want == "4k" else want
        j = next((i for i in range(self.cb_res.count()) if str(self.cb_res.itemData(i)).lower() == want), 0)
        self.cb_res.setCurrentIndex(j)

    def resolution(self) -> str:
        return str(self.cb_res.currentData() or "Default")

    def style(self) -> str:
        return str(self.cb_style.currentData() or "").strip()

    def _colour_picked(self) -> None:
        pair = self.btn_colour.pair()
        if pair is not None:
            self._background = pair[0] & 0xFFFFFF
            self._update_preview()

    def _update_preview(self) -> None:
        """The style's 'normal' template (else its first) in the chosen
        resolution / title-bar form, over the page colour."""
        from ...format.template_place import split_element

        res = self.resolution()
        res = screen_resolution() if res == "Default" else res
        want = {res.lower()} | ({"4k", "uhd4k"} if res.upper() in ("4K", "UHD4K") else set())
        best = None
        for _p, s in self._catalog.elements(self.style()):
            name, r, tb = split_element(s.name)
            if r.lower() not in want:
                continue
            score = (name.lower() in ("normal", "pagecontent"), tb == self.chk_titlebar.isChecked())
            if best is None or score > best[0]:
                best = (score, s)
        self.preview.setPixmap(
            thumbnail_pixmap(best[1] if best else None, self.preview.size(), self._background)
        )

    def values(self) -> dict:
        return {
            "style": self.style(),
            "resolution": self.resolution(),
            "title_bar": self.chk_titlebar.isChecked(),
            "background": self._background,
        }

    def _problem(self, text: str) -> None:
        self._status.setText(text)
        self._status.setVisible(bool(text))

    def _ok(self) -> None:
        v = self.values()
        if not v["style"]:
            self._problem("Choose a template style.")
            return
        self._problem("")
        where = set_page_defaults(v, self._project_dir)
        if not where:
            QtWidgets.QMessageBox.warning(
                self,
                "Page Defaults",
                "The page defaults could not be written to %s. Check that the project "
                "folder can be written to." % os.path.join(self._project_dir, "PROJECT.CIT"),
            )
            return
        self.stored_in = where
        self.accept()
