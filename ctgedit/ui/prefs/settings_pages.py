"""The settings window frame: one scrolling list of pages with search."""

from __future__ import annotations
from ..style.theme import sp as _sp

import html
import re
from typing import List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

#: the width of a row's label column (px at 100 % text size)
LABEL_WIDTH = 210
#: the narrowest a row's label gets in a narrow window (it wraps): the window keeps no
#: sideways scroll down to a small size
LABEL_MIN_WIDTH = 130
#: the search word that keeps the settings that differ from their defaults
MODIFIED_TOKEN = "@modified"

#: what applies a setting when the Settings window changes it: key -> [fn(window)] (a
#: setting read where it is drawn needs none; one that must reach a live object registers)
APPLY_HOOKS: dict = {}


def register_apply(key: str, fn) -> None:
    """Call ``fn(window)`` for every editor window after the Settings window changed
    ``key``."""
    APPLY_HOOKS.setdefault(key, []).append(fn)


def run_apply_hooks(keys) -> None:
    try:
        from ..main_window import MainWindow
    except ImportError:  # swallow-ok: no editor windows to tell
        return
    for key in keys:
        for fn in APPLY_HOOKS.get(key, ()):
            for w in MainWindow.editor_windows():
                try:
                    fn(w)
                except RuntimeError:  # swallow-ok: a window being closed
                    continue


def _texts_of(w) -> List[str]:
    """What a search looks at in a row's widget: its text, its choices, its tool tip; a
    container's children too."""
    out: List[str] = []
    if w is None:
        return out
    if isinstance(w, QtWidgets.QLayout):
        for k in range(w.count()):
            item = w.itemAt(k)
            out += _texts_of(item.widget() or item.layout())
        return out
    for attr in ("text", "placeholderText", "toolTip", "title"):
        fn = getattr(w, attr, None)
        if callable(fn):
            try:
                t = fn()
            except TypeError, RuntimeError:
                continue
            if isinstance(t, str) and t:
                out.append(t)
    if isinstance(w, QtWidgets.QComboBox):
        out += [w.itemText(k) for k in range(w.count())]
    lay = w.layout() if isinstance(w, QtWidgets.QWidget) else None
    if lay is not None:
        out += _texts_of(lay)
    return out


def _pattern(text: str, regex: bool):
    if not text:
        return None
    try:
        return re.compile(text if regex else re.escape(text), re.IGNORECASE)
    except re.error:  # swallow-ok: an expression that does not compile marks nothing
        return None


def highlight_html(text: str, pattern) -> str:
    """``text`` as escaped rich text with the parts ``pattern`` matches in the accent
    colour."""
    if pattern is None:
        return html.escape(text, quote=False)
    from ..style.theme import COLORS

    out, pos = [], 0
    for m in pattern.finditer(text):
        if m.end() <= m.start():
            continue
        out.append(html.escape(text[pos : m.start()], quote=False))
        out.append(
            "<span style='color:%s; font-weight:600;'>%s</span>"
            % (COLORS.get("accent", "#4da3ff"), html.escape(m.group(0), quote=False))
        )
        pos = m.end()
    out.append(html.escape(text[pos:], quote=False))
    return "".join(out)


class _CheckLabel(QtWidgets.QLabel):
    """The highlighted name of a check box during a search (a click ticks the box)."""

    def __init__(self, box: QtWidgets.QAbstractButton, parent=None):
        super().__init__(parent)
        self._box = box
        self.setTextFormat(QtCore.Qt.RichText)
        self.setCursor(QtCore.Qt.PointingHandCursor)

    def mousePressEvent(self, e):  # noqa: N802 -- Qt naming
        if self._box.isEnabled() and e.button() == QtCore.Qt.LeftButton:
            self._box.click()
        e.accept()


class _RowLabel(QtWidgets.QLabel):
    """A row's label: as wide as the label column (the fields line up), narrower and
    wrapped when the window is too narrow for the row."""

    def __init__(self, text: str, width: int):
        super().__init__(text)
        self._column = max(LABEL_MIN_WIDTH, width)

    def sizeHint(self):  # noqa: N802 -- Qt naming
        return QtCore.QSize(self._column, super().sizeHint().height())

    def minimumSizeHint(self):  # noqa: N802 -- Qt naming
        return QtCore.QSize(LABEL_MIN_WIDTH, super().minimumSizeHint().height())


class _Row(QtWidgets.QWidget):
    """One setting: a label and its field, or one widget across the row."""

    #: the field's value changed (any of its controls; typed text once it is finished:
    #: Enter or the field left)
    changed = QtCore.Signal()
    #: text is being typed in one of the field's text boxes (the marks follow it; the
    #: value counts as changed when the typing is finished: :attr:`changed`)
    edited = QtCore.Signal()
    #: Reset to Default chosen in the row's menu
    resetRequested = QtCore.Signal(object)

    def __init__(self, label: str, field, indent: int = 0, extra: str = "", parent=None, key: str = ""):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(indent, 2, 0, 2)
        lay.setSpacing(_sp(2))
        self.label = None
        self.label_text = label
        if label:
            self.label = _RowLabel(label, LABEL_WIDTH - indent)
            self.label.setMaximumWidth(LABEL_WIDTH + 80)
            self.label.setWordWrap(True)
            lay.addWidget(self.label)
            if isinstance(field, QtWidgets.QWidget):
                self.label.setBuddy(field)
        if isinstance(field, QtWidgets.QLayout):
            holder = QtWidgets.QWidget()
            field.setContentsMargins(0, 0, 0, 0)
            holder.setLayout(field)
            field = holder
        self.field = field
        self.search_text = "\n".join([label, extra] + _texts_of(field))
        self._hl: Optional[_CheckLabel] = None
        if not label and isinstance(field, QtWidgets.QCheckBox) and field.text():
            # a check box's name wraps in a label of its own (a long name would make the whole
            # scroll as wide as itself); a click on it ticks the box, a search marks it
            text = field.text()
            field.setProperty("ctgText", text)
            field.setAccessibleName(text.replace("&", ""))
            field.setText("")
            self._hl = _CheckLabel(field, self)
            self._hl.setWordWrap(True)
            self._hl.setTextFormat(QtCore.Qt.PlainText)
            self._hl.setText(text.replace("&&", "\x00").replace("&", "").replace("\x00", "&"))
            self._hl.setToolTip(field.toolTip())
            self._hl.setEnabled(field.isEnabled())
            field.installEventFilter(self)
            lay.addWidget(field, 0)
            lay.addWidget(self._hl, 1)
        else:
            lay.addWidget(field, 0 if label else 1)
        if self.label is not None and isinstance(field, QtWidgets.QWidget):
            # (a field greyed by the switch above it greys its label too: Label drag speed)
            self.label.setEnabled(field.isEnabled())
            field.installEventFilter(self)
        if label:
            lay.addStretch(1)
        if isinstance(field, QtWidgets.QComboBox):
            # (a long choice must not widen the whole window: the list shows it in full)
            longest = max([len(field.itemText(k)) for k in range(field.count())] + [0])
            if longest > 22:
                field.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
                field.setMinimumContentsLength(22)
        #: the setting this row shows ('' = none known) and its default's display text
        self.key = key
        self.default_text = ""
        self.modified = False
        self._tips = None
        self._watch(field)

    def eventFilter(self, obj, e) -> bool:  # noqa: N802 -- Qt naming
        if obj is self.field:
            if e.type() == QtCore.QEvent.EnabledChange:
                for lab in (self._hl, self.label):
                    if lab is not None:
                        lab.setEnabled(obj.isEnabled())
            elif e.type() == QtCore.QEvent.ToolTipChange and self._hl is not None:
                self._hl.setToolTip(obj.toolTip())
        return False

    # -- the setting --------------------------------------------------------------------
    def bind(self, key: str, default_text: str = "") -> None:
        """This row shows setting ``key`` (its default reads ``default_text``)."""
        self.key = key
        self.default_text = default_text
        self._tip_default()

    def _tip_default(self) -> None:
        targets = [w for w in (self.label, self._hl, self.field) if w is not None]
        if isinstance(self.field, QtWidgets.QWidget):
            targets += [w for w in self.field.findChildren(QtWidgets.QWidget) if w.toolTip()]
        if self._tips is None:
            self._tips = {id(w): (w, w.toolTip()) for w in targets}
        if not self.default_text:
            return
        for _i, (w, tip) in self._tips.items():
            base = tip.split("\n\nDefault: ")[0] if tip else ""
            w.setToolTip(
                ("%s\n\nDefault: %s" % (base, self.default_text))
                if base
                else "Default: %s" % self.default_text
            )

    def set_modified(self, on: bool) -> None:
        on = bool(on and self.key)
        if on != self.modified:
            self.modified = on
            self.update()
            self.setAccessibleDescription("Changed from the default" if on else "")

    def _watch(self, field) -> None:
        """Every control of the field reports a change as :attr:`changed` (a field marked
        ``ctgNoState`` reports its own changes: the Keyboard page)."""
        self._typed: List[QtWidgets.QLineEdit] = []
        if isinstance(field, QtWidgets.QWidget) and field.property("ctgNoState"):
            return
        ws = [field] if isinstance(field, QtWidgets.QWidget) else []
        if isinstance(field, QtWidgets.QWidget):
            ws += field.findChildren(QtWidgets.QWidget)
        for w in ws:
            sig = None
            if isinstance(w, QtWidgets.QAbstractButton) and w.isCheckable():
                sig = w.toggled
            elif isinstance(w, QtWidgets.QAbstractSpinBox) and hasattr(w, "valueChanged"):
                sig = w.valueChanged
            elif isinstance(w, QtWidgets.QComboBox):
                sig = w.currentIndexChanged
            elif isinstance(w, QtWidgets.QLineEdit) and not isinstance(
                w.parentWidget(), (QtWidgets.QAbstractSpinBox, QtWidgets.QComboBox)
            ):
                # (a half-typed path is no setting yet: typing reports 'edited', and Enter
                # or leaving the box 'changed'; a text set by a button changes at once)
                w.textChanged.connect(
                    lambda *_a, e=w: (self.edited if e.isModified() else self.changed).emit()
                )
                w.editingFinished.connect(lambda e=w: self._finish_typing(e))
                self._typed.append(w)
            elif isinstance(w, QtWidgets.QListWidget):
                m = w.model()
                for s in (m.rowsInserted, m.rowsRemoved, m.rowsMoved):
                    s.connect(lambda *_a: self.changed.emit())
            elif hasattr(w, "changed") and isinstance(getattr(type(w), "changed", None), QtCore.Signal):
                sig = w.changed
            if sig is not None:
                sig.connect(lambda *_a: self.changed.emit())

    def _finish_typing(self, edit) -> None:
        if edit.isModified():
            edit.setModified(False)
            self.changed.emit()

    def finish_typing(self) -> None:
        """What is typed in the row's text boxes counts as changed now (the window closes)."""
        for edit in list(self._typed):
            try:
                self._finish_typing(edit)
            except RuntimeError:  # swallow-ok: the box was deleted
                continue

    # -- look -------------------------------------------------------------------------
    def paintEvent(self, e):  # noqa: N802 -- Qt naming
        super().paintEvent(e)
        if self.modified:
            from ..style.theme import COLORS

            p = QtGui.QPainter(self)
            p.fillRect(
                QtCore.QRect(0, 3, 2, max(4, self.height() - 6)),
                QtGui.QColor(COLORS.get("accent", "#4da3ff")),
            )
            p.end()

    def contextMenuEvent(self, e):  # noqa: N802 -- Qt naming
        if not self.key:
            return super().contextMenuEvent(e)
        menu = QtWidgets.QMenu(self)
        reset = menu.addAction("Reset to Default")
        reset.setEnabled(self.modified)
        copy = menu.addAction("Copy Setting Name")
        chosen = menu.exec(e.globalPos())
        if chosen is reset:
            self.resetRequested.emit(self)
        elif chosen is copy:
            QtWidgets.QApplication.clipboard().setText(self.key)
        e.accept()

    def highlight(self, pattern) -> None:
        """Mark what the search matched in the row's name (None: plain again)."""
        if self.label is not None:
            if pattern is None:
                self.label.setTextFormat(QtCore.Qt.AutoText)
                self.label.setText(self.label_text)
            else:
                self.label.setTextFormat(QtCore.Qt.RichText)
                self.label.setText(highlight_html(self.label_text, pattern))
            return
        if self._hl is None:
            return
        text = self._plain_check_text()
        if pattern is None or not pattern.search(text):
            if self._hl.textFormat() != QtCore.Qt.PlainText:
                self._hl.setTextFormat(QtCore.Qt.PlainText)
                self._hl.setText(text)
            return
        self._hl.setTextFormat(QtCore.Qt.RichText)
        self._hl.setText(highlight_html(text, pattern))

    def _plain_check_text(self) -> str:
        text = str(self.field.property("ctgText") or "")
        return text.replace("&&", "\x00").replace("&", "").replace("\x00", "&")


class Section(QtWidgets.QWidget):
    """A headed group of rows on a page."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.rows: List[_Row] = []
        self._lay = QtWidgets.QVBoxLayout(self)
        self._lay.setContentsMargins(0, _sp(1), 0, _sp(1))
        self._lay.setSpacing(_sp(0))
        self.title = title
        self.heading = None
        if title:
            from ..style import theme

            self.heading = QtWidgets.QLabel(title)
            self.heading.setObjectName("SettingsSectionHeading")
            # (through the theme's stylesheet hook: the size scales with the text size)
            theme.set_note_role(self.heading, "head", theme.type_qss("body_strong") + " padding-top: 6px;")
            self._lay.addWidget(self.heading)

    def row(self, label: str, field, extra: str = "", key: str = "") -> _Row:
        r = _Row(label, field, indent=12, extra=extra + "\n" + self.title, key=key)
        self._lay.addWidget(r)
        self.rows.append(r)
        return r

    def check(self, widget, extra: str = "", key: str = "") -> _Row:
        """A check box (or any widget) across the row."""
        return self.row("", widget, extra, key)

    def sub(self, widget, extra: str = "", key: str = "", label: str = "") -> _Row:
        """A widget indented under the row before it (an option that needs that one), with
        its ``label`` before it when it has one."""
        r = _Row(label, widget, indent=34, extra=extra + "\n" + self.title, key=key)
        self._lay.addWidget(r)
        self.rows.append(r)
        return r

    def note(self, text: str) -> QtWidgets.QLabel:
        from ..style import theme

        lab = theme.note_label(text)
        lab.setContentsMargins(_sp(3), 0, 0, _sp(0))
        self._lay.addWidget(lab)
        return lab


class Page(QtWidgets.QWidget):
    """One page of settings: its title, then its sections (a part of the one long scroll)."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.title = title
        self._lay = QtWidgets.QVBoxLayout(self)
        self._lay.setContentsMargins(_sp(4), _sp(2), _sp(4), _sp(2))
        self._lay.setSpacing(_sp(1))
        from ..style import theme

        self.heading = QtWidgets.QLabel(title)
        self.heading.setObjectName("SettingsPageTitle")
        # (the type scale's title: theme.TYPE)
        theme.set_note_role(self.heading, "", theme.type_qss("title"))
        self._lay.addWidget(self.heading)
        self.sections: List[Section] = []

    def section(self, title: str = "") -> Section:
        s = Section(title)
        self._lay.addWidget(s)
        self.sections.append(s)
        return s

    def note(self, text: str) -> QtWidgets.QLabel:
        from ..style import theme

        lab = theme.note_label(text)
        self._lay.addWidget(lab)
        return lab

    def insert_widget(self, w: QtWidgets.QWidget) -> None:
        """A widget right under the page title (a preview)."""
        self._lay.insertWidget(1, w)

    def rows(self) -> List[_Row]:
        return [r for s in self.sections for r in s.rows]


class SettingsPages(QtWidgets.QWidget):
    """The page list, the search box and the pages (see the module)."""

    currentChanged = QtCore.Signal(int)

    def __init__(self, parent=None, search_placeholder: str = "Search settings"):
        super().__init__(parent)
        from ..style import theme

        self.pages: List[Page] = []
        self._syncing = False
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(_sp(1))
        top = QtWidgets.QHBoxLayout()
        top.addStretch(1)
        self.search = theme.SearchField(search_placeholder, regex_key="settings")
        self.search.setMinimumWidth(260)
        self.search.setToolTip(
            "Shows the settings whose name, choices or description "
            "contain the text, on every page. @modified shows the ones "
            "you changed"
        )
        self.search.searchChanged.connect(self._filter)
        top.addWidget(self.search)
        # the funnel: Show Modified Settings Only
        self.funnel = QtWidgets.QPushButton("Filter")
        self.funnel.setAutoDefault(False)
        self.funnel.setToolTip("Filter the settings")
        self.funnel.setAccessibleName("Filter the settings")
        fm = QtWidgets.QMenu(self.funnel)
        self.act_modified_only = fm.addAction("Show Modified Settings Only")
        self.act_modified_only.setCheckable(True)
        self.act_modified_only.toggled.connect(lambda _on: self._filter())
        self.funnel.setMenu(fm)
        top.addWidget(self.funnel)
        outer.addLayout(top)
        body = QtWidgets.QHBoxLayout()
        body.setSpacing(0)
        self.list = QtWidgets.QListWidget()
        self.list.setAccessibleName("Settings pages")
        self.list.setObjectName("SettingsPageList")
        self.list.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.list.setSpacing(1)
        self.list.setUniformItemSizes(True)
        self.list.currentRowChanged.connect(self._row_changed)
        frame = QtWidgets.QFrame()
        frame.setFrameShape(QtWidgets.QFrame.StyledPanel)
        fl = QtWidgets.QVBoxLayout(frame)
        fl.setContentsMargins(0, 0, 0, 0)
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        # (a window narrower than the widest row scrolls sideways instead of cutting it)
        self.scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self._column = QtWidgets.QWidget()
        self._col_lay = QtWidgets.QVBoxLayout(self._column)
        self._col_lay.setContentsMargins(0, 0, 0, 0)
        self._col_lay.setSpacing(0)
        # (room after the last page: its title can scroll to the top edge)
        self._tail = QtWidgets.QWidget()
        self._col_lay.addWidget(self._tail)
        self.scroll.setWidget(self._column)
        self.scroll.verticalScrollBar().valueChanged.connect(self._scrolled)
        self.scroll.viewport().installEventFilter(self)
        fl.addWidget(self.scroll)
        self.empty = QtWidgets.QLabel("No setting matches the search.")
        self.empty.setAlignment(QtCore.Qt.AlignCenter)
        self.empty.hide()
        fl.addWidget(self.empty)
        body.addWidget(self.list)
        body.addSpacing(8)
        body.addWidget(frame, 1)
        outer.addLayout(body, 1)
        self.search.set_list_view(self.list)

    # -- building ---------------------------------------------------------------------------
    def add_page(self, title: str, help_topic: str = "") -> Page:
        p = Page(title)
        if help_topic:
            from .. import help as _help

            _help.set_help(p, help_topic)
        self.pages.append(p)
        self._col_lay.insertWidget(self._col_lay.count() - 1, p)
        item = QtWidgets.QListWidgetItem(title)
        self.list.addItem(item)
        if self.list.currentRow() < 0:
            self._syncing = True
            self.list.setCurrentRow(0)
            self._syncing = False
        self._fit_list()
        return p

    def _fit_list(self) -> None:
        fm = self.list.fontMetrics()
        # (room for a count during a search: 'Pages and libraries (12)')
        w = max([fm.horizontalAdvance(p.title + " (99)") for p in self.pages] + [80]) + 36
        self.list.setFixedWidth(w)

    def rows(self) -> List[_Row]:
        return [r for p in self.pages for r in p.rows()]

    # -- the tab widget's answers -------------------------------------------------------------
    def count(self) -> int:
        return len(self.pages)

    def currentIndex(self) -> int:  # noqa: N802 -- Qt naming
        return self.list.currentRow()

    def setCurrentIndex(self, k: int) -> None:  # noqa: N802 -- Qt naming
        if 0 <= k < len(self.pages):
            if self.list.currentRow() == k:
                self._scroll_to(k)
            else:
                self.list.setCurrentRow(k)

    def currentWidget(self):  # noqa: N802 -- Qt naming
        k = self.currentIndex()
        return self.pages[k] if 0 <= k < len(self.pages) else None

    def widget(self, k: int):
        return self.pages[k] if 0 <= k < len(self.pages) else None

    def tabText(self, k: int) -> str:  # noqa: N802 -- Qt naming
        return self.pages[k].title if 0 <= k < len(self.pages) else ""

    def indexOf(self, title: str) -> int:  # noqa: N802 -- Qt naming
        for k, p in enumerate(self.pages):
            if p.title.lower() == (title or "").lower():
                return k
        return -1

    def show_page(self, title: str) -> bool:
        k = self.indexOf(title)
        if k >= 0:
            self.setCurrentIndex(k)
        return k >= 0

    def _row_changed(self, k: int) -> None:
        if not 0 <= k < len(self.pages):
            return
        if not self._syncing:
            self._scroll_to(k)
        self.currentChanged.emit(k)

    def _scroll_to(self, k: int) -> None:
        p = self.pages[k]
        self._column.layout().activate()
        self._syncing = True
        try:
            self.scroll.verticalScrollBar().setValue(p.y())
        finally:
            self._syncing = False

    def _scrolled(self, value: int) -> None:
        """The page under the top edge becomes the current one."""
        if self._syncing:
            return
        k_top = -1
        for k, p in enumerate(self.pages):
            if p.isHidden():
                continue
            if p.y() <= value + 24:
                k_top = k
            else:
                break
        if k_top < 0:
            k_top = next((k for k, p in enumerate(self.pages) if not p.isHidden()), -1)
        if k_top >= 0 and k_top != self.list.currentRow():
            self._syncing = True
            try:
                self.list.setCurrentRow(k_top)
            finally:
                self._syncing = False

    def _fit_tail(self) -> None:
        shown = [p for p in self.pages if not p.isHidden()]
        vh = self.scroll.viewport().height()
        last = shown[-1].sizeHint().height() if shown else 0
        self._tail.setFixedHeight(max(0, vh - last - 8))

    def eventFilter(self, obj, e) -> bool:  # noqa: N802 -- Qt naming
        if obj is self.scroll.viewport() and e.type() == QtCore.QEvent.Resize:
            self._fit_tail()
        return False

    # -- search ------------------------------------------------------------------------------
    def query(self):
        """(the search text without '@modified', modified only)."""
        text = self.search.text()
        token = MODIFIED_TOKEN in text.lower()
        if token:
            text = re.sub(re.escape(MODIFIED_TOKEN), "", text, flags=re.IGNORECASE)
        return text.strip(), token or self.act_modified_only.isChecked()

    def _filter(self, _text: str = "") -> None:
        text, modified_only = self.query()
        m = self.search.matcher(text)
        searching = not m.empty or modified_only
        pattern = _pattern(text, self.search.regex) if not m.empty else None
        any_page = False
        first = -1
        for k, p in enumerate(self.pages):
            hits = 0
            for s in p.sections:
                shown = 0
                for r in s.rows:
                    ok = (m.empty or m(r.search_text, p.title)) and (not modified_only or r.modified)
                    r.setVisible(bool(ok))
                    r.highlight(pattern if ok else None)
                    shown += bool(ok)
                s.setVisible(shown > 0 or not searching)
                hits += shown
            visible = hits > 0 or not searching
            p.setVisible(visible)
            item = self.list.item(k)
            item.setHidden(not visible)
            item.setText("%s (%d)" % (p.title, hits) if searching else p.title)
            if visible:
                any_page = True
                if first < 0:
                    first = k
        cur = self.currentIndex()
        self.scroll.setVisible(any_page)
        self.empty.setVisible(not any_page)
        self._fit_tail()
        # (the page the reader is on stays while it still has a match: a refined search
        # does not jump back to the first page; it scrolls to it again, as the pages above
        # it may have changed height)
        if any_page and (cur < 0 or self.list.item(cur).isHidden()):
            self.setCurrentIndex(first)
        elif any_page and searching:
            self.setCurrentIndex(cur)

    def refilter(self) -> None:
        """Again (a row's modified state changed while only those are shown)."""
        if self.query()[1]:
            self._filter()

    def keyPressEvent(self, ev: QtGui.QKeyEvent) -> None:  # noqa: N802 -- Qt naming
        if ev.matches(QtGui.QKeySequence.Find):
            self.search.setFocus()
            self.search.selectAll()
            return
        super().keyPressEvent(ev)
